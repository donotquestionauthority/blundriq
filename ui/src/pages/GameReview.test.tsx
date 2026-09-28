/**
 * The per-game review against the real page: what Learn mode keeps off the screen before a
 * commit and restores after it, promotion, the fail-closed rows, the commit outcomes, the timer,
 * exactly one prompt surface at every width, the prep arrow and panel, the conflict list, Explore,
 * and the exits. The boundaries (the board, the eval bar, the Explore layer, the AI panel, the
 * API) are mocked; everything between the route and the DOM is the shipped code.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Chess } from "chess.js";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import GameReview from "./GameReview";
import { ApiError } from "../api";
import type { GameReviewResponse, PlyAnalysisEntry, RepertoireEntry } from "../games";
import { ARROWS } from "../utils/board";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let boardOptions: any = null;
vi.mock("react-chessboard", () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  Chessboard: (props: any) => {
    boardOptions = props.options;
    return <div data-testid="board" />;
  },
}));
// The eval bar captures its prop: an evaluation reaching it is the condition, not pixels.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let evalBarProps: any = null;
vi.mock("../components/EvalBar", () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  EvalBar: (props: any) => {
    evalBarProps = props;
    return null;
  },
}));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let exploreProps: any = null;
vi.mock("../components/ExploreLayer", () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ExploreLayer: (props: any) => {
    exploreProps = props;
    return <div data-testid="explore-layer" />;
  },
}));
vi.mock("../components/PositionCard/AiExplanationPanel", () => ({ AiExplanationPanel: () => <div data-testid="ai-panel" /> }));

const getGameReview = vi.fn();
const learnCommit = vi.fn();
const getReviewPrefs = vi.fn();
const setReviewPref = vi.fn();
const markGameReviewed = vi.fn();
vi.mock("../games", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../games")>();
  return {
    ...actual,
    getGameReview: (...a: unknown[]) => getGameReview(...a),
    learnCommit: (...a: unknown[]) => learnCommit(...a),
    getReviewPrefs: (...a: unknown[]) => getReviewPrefs(...a),
    setReviewPref: (...a: unknown[]) => setReviewPref(...a),
    markGameReviewed: (...a: unknown[]) => markGameReviewed(...a),
  };
});

// 1.e4 e5 2.Nf3 Nc6: ply 2 is White's decision, carries a stored classification, and a move was
// played from it — eligible, and a prompt ply under the Learn default filter.
function buildSpine(sans: string[]) {
  const c = new Chess();
  const fen_sequence = [c.fen()];
  const moves: string[] = [];
  for (const s of sans) {
    moves.push(c.move(s).san);
    fen_sequence.push(c.fen());
  }
  return { fen_sequence, moves };
}
const { fen_sequence, moves } = buildSpine(["e4", "e5", "Nf3", "Nc6"]);
const BOOK_MOVE = "Bc4";
const STORED_BEST = "Bb5";
const STORED_BEST_LINE = "Bb5 a6 Ba4";
const CP_LOSS = 137;

const entry = (over: Partial<RepertoireEntry>): RepertoireEntry => ({ status: "none", book_move: null, book: null, chapter: null, line_name: null, line_ply: null, plan: [], more_lines: 0, transposed: null, conflict: null, ...over });

function reviewPayload(): GameReviewResponse {
  return {
    game: {
      id: 1,
      url: "https://example.test/1",
      source: "chesscom",
      played_at: null,
      time_control: null,
      time_class: "rapid",
      opening_name: "Italian",
      opening_eco: null,
      termination: null,
      variant: "standard",
      starting_fen: null,
      moves,
      fen_sequence,
      analysis_status: "completed",
      analysis_depth: 18,
      ply_analysis: fen_sequence.map((_, i): PlyAnalysisEntry => ({ ply: i, eval: 40 + i, best_move: i === 2 ? STORED_BEST : "d4", best_line: STORED_BEST_LINE })),
      ply_analysis_depth: 18,
      analyzed: true,
      player_color: "white",
      opponent_username: "op",
      opponent_rating: 1500,
      player_rating: null,
      result: "loss",
      reviewed_at: null,
      out_of_window: false,
    },
    blunders: [{ ply: 2, fen: fen_sequence[2], move_played: "Nf3", best_move: STORED_BEST, best_line: STORED_BEST_LINE, post_blunder_line: null, cp_loss: CP_LOSS, classification: "blunder", phase: "opening", fen_occurrence_count: 3 }],
    repertoire: {
      by_ply: Object.fromEntries(
        fen_sequence.map((_, i) => [
          String(i),
          i === 2
            ? entry({ status: "match", book_move: BOOK_MOVE, book: "Italian", chapter: "Main line", line_name: "Giuoco Piano", line_ply: 4, plan: [BOOK_MOVE, "Bc5"], transposed: false })
            : i % 2 === 1
              ? entry({ status: "not_your_turn" })
              : entry({}),
        ]),
      ),
    },
  };
}

function Probe() {
  const location = useLocation();
  return <pre data-testid="probe">{`${location.pathname}${location.search}|${JSON.stringify(location.state)}`}</pre>;
}

async function renderReview(ply = 2, state?: unknown) {
  render(
    <MemoryRouter initialEntries={[{ pathname: "/review/1", search: `?ply=${ply}`, state }]}>
      <Routes>
        <Route path="/review/:gameId" element={<GameReview />} />
        <Route path="/review" element={<Probe />} />
        <Route path="/games" element={<Probe />} />
      </Routes>
    </MemoryRouter>,
  );
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function reachCommitStep() {
  for (let i = 0; i < 6; i++) {
    const btn = screen.queryAllByText("Next question").map((b) => b.closest("button") as HTMLButtonElement).find((b) => !b.disabled);
    if (!btn) break;
    await act(async () => btn.click());
  }
}
const drop = async (from: string, to: string) => {
  let result: boolean | undefined;
  await act(async () => {
    result = boardOptions.onPieceDrop({ sourceSquare: from, targetSquare: to });
  });
  return result;
};
const flush = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const arrowColors = (): string[] => (boardOptions?.arrows ?? []).map((a: any) => a.color);
const bodyText = () => document.body.textContent ?? "";
const countText = (t: string) => screen.queryAllByText(t).length;
const setViewport = (w: number) => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
  window.dispatchEvent(new Event("resize"));
};
const PHONE = 390;
const DESKTOP = 1440;

beforeEach(() => {
  boardOptions = null;
  evalBarProps = null;
  exploreProps = null;
  setViewport(DESKTOP);
  getGameReview.mockResolvedValue(reviewPayload());
  getReviewPrefs.mockResolvedValue({ review_default_mode: "learn", review_show_timer: true });
  setReviewPref.mockResolvedValue({});
  markGameReviewed.mockResolvedValue({});
  learnCommit.mockResolvedValue({ id: 7, created: true });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("before a commit nothing that answers the question is on screen", () => {
  it("the eval bar receives no evaluation, and the value it would have shown exists", async () => {
    await renderReview();
    expect(evalBarProps).not.toBeNull();
    expect(evalBarProps.evalCp).toBeNull();
    expect(reviewPayload().game.ply_analysis?.[2].eval).not.toBeNull();
  });
  it("the only arrow is the opponent's last move", async () => {
    await renderReview();
    expect(arrowColors()).toEqual([ARROWS.opponent]);
  });
  it("the classification, the cp figure, the best move, the line and the repertoire panel are absent; the move list stops before the decision", async () => {
    await renderReview();
    const text = bodyText();
    for (const s of ["Blunder", String(CP_LOSS), STORED_BEST, STORED_BEST_LINE, BOOK_MOVE, "Your prep plays", "Giuoco Piano", "2. Nf3"]) expect(text).not.toContain(s);
    expect(text).toContain("1. e4 e5");
    expect(screen.queryByTestId("blunder-card")).toBeNull();
    expect(screen.queryByTestId("repertoire-panel")).toBeNull();
  });
  it("the reveal restores them, so the gate is not passing by rendering nothing", async () => {
    await renderReview();
    await reachCommitStep();
    expect(await drop("f1", "c4")).toBe(true);
    const text = bodyText();
    expect(text).toContain("Routine complete.");
    expect(text).toContain(BOOK_MOVE);
    expect(text).toContain("Your prep plays");
    expect(evalBarProps.evalCp).toBe(42);
    expect(arrowColors().length).toBeGreaterThan(1);
    // The committed move is the book move: one arrow, one legend row naming both.
    expect(text).toContain("You played · Your prep plays");
    expect(learnCommit).toHaveBeenCalledTimes(1);
    expect(learnCommit.mock.calls[0][0]).toBe(1);
    expect(learnCommit.mock.calls[0][1]).toMatchObject({ ply: 2, committed_move: "Bc4" });
    expect(typeof learnCommit.mock.calls[0][1].attempt_id).toBe("string");
  });
  it("the board takes no move before the commit step", async () => {
    await renderReview();
    expect(boardOptions.allowDragging).toBe(false);
    expect(boardOptions.onPieceDrop).toBeUndefined();
    await reachCommitStep();
    expect(boardOptions.allowDragging).toBe(true);
  });
});

describe("promotion: all four pieces survive, by drag and by tap", () => {
  const PROMO_START = "8/1P6/8/k7/8/8/8/7K w - - 0 1";
  function promoPayload(): GameReviewResponse {
    const c = new Chess(PROMO_START);
    const seq = [c.fen()];
    const mv = c.move("b8=Q");
    seq.push(c.fen());
    const p = reviewPayload();
    p.game.moves = [mv.san];
    p.game.fen_sequence = seq;
    p.game.ply_analysis = seq.map((_, i) => ({ ply: i, eval: 0, best_move: null, best_line: null }));
    p.blunders = [{ ...p.blunders[0], ply: 0, fen: seq[0], move_played: "b8=Q" }];
    p.repertoire = { by_ply: Object.fromEntries(seq.map((_, i) => [String(i), entry({})])) };
    return p;
  }
  beforeEach(() => getGameReview.mockResolvedValue(promoPayload()));

  it("a pawn-to-last-rank drop snaps back and opens the chooser", async () => {
    await renderReview(0);
    await reachCommitStep();
    expect(await drop("b7", "b8")).toBe(false);
    expect(countText("Promote to:")).toBe(1);
    expect(learnCommit).not.toHaveBeenCalled();
  });
  it.each([
    ["knight", "b8=N"],
    ["rook", "b8=R"],
    ["bishop", "b8=B"],
    ["queen", "b8=Q"],
  ])("a promotion to %s commits as %s", async (piece, san) => {
    await renderReview(0);
    await reachCommitStep();
    await drop("b7", "b8");
    await act(async () => screen.getByLabelText(`Promote to ${piece}`).click());
    expect(learnCommit).toHaveBeenCalledTimes(1);
    expect(learnCommit.mock.calls[0][1].committed_move).toBe(san);
    expect(bodyText()).toContain(san);
  });
  it("a second tap on a promotion target opens the same chooser", async () => {
    await renderReview(0);
    await reachCommitStep();
    await act(async () => boardOptions.onSquareClick({ square: "b7" }));
    await act(async () => boardOptions.onSquareClick({ square: "b8" }));
    expect(countText("Promote to:")).toBe(1);
    await act(async () => screen.getByLabelText("Promote to rook").click());
    expect(learnCommit.mock.calls[0][1].committed_move).toBe("b8=R");
  });
});

describe("fail closed", () => {
  it("a preference read failure renders Learn", async () => {
    getReviewPrefs.mockRejectedValue(new Error("network"));
    await renderReview();
    expect(evalBarProps.evalCp).toBeNull();
    expect(bodyText()).not.toContain(STORED_BEST);
    expect(bodyText()).toContain("What is your opponent threatening?");
  });
  it("a stored Review preference reaches Review mode, with the answer on screen", async () => {
    getReviewPrefs.mockResolvedValue({ review_default_mode: "review", review_show_timer: true });
    await renderReview();
    expect(bodyText()).not.toContain("What is your opponent threatening?");
    expect(bodyText()).toContain(STORED_BEST);
    expect(evalBarProps.evalCp).toBe(42);
    // One ply on, the blunder just played is the card, with the AI panel.
    await act(async () => fireEvent.keyDown(window, { key: "ArrowRight" }));
    expect(screen.getByTestId("blunder-card")).toBeInTheDocument();
    expect(screen.getByTestId("ai-panel")).toBeInTheDocument();
    expect(bodyText()).toContain(`−${CP_LOSS}cp`);
    expect(bodyText()).toContain("3× across your games");
  });
  it("length drift makes no ply eligible", async () => {
    const p = reviewPayload();
    p.game.fen_sequence = p.game.fen_sequence!.slice(0, -1);
    getGameReview.mockResolvedValue(p);
    await renderReview();
    expect(bodyText()).toContain("There's nothing to work through in this game.");
    expect(bodyText()).not.toContain("What is your opponent threatening?");
  });
  it("zero classified plies is the nothing-to-work-through state, not every-ply prompting", async () => {
    const p = reviewPayload();
    p.blunders = [];
    getGameReview.mockResolvedValue(p);
    await renderReview();
    expect(bodyText()).toContain("There's nothing to work through in this game.");
    expect(bodyText()).not.toContain("What is your opponent threatening?");
  });
  it("an opponent-turn ply gets the ordinary surface, not a stuck rep", async () => {
    await renderReview(1);
    expect(bodyText()).not.toContain("What is your opponent threatening?");
    expect(bodyText()).not.toContain("Just show me");
  });
  it("repertoire: null renders no panel and the rep still runs", async () => {
    const p = reviewPayload();
    p.repertoire = null;
    getGameReview.mockResolvedValue(p);
    await renderReview();
    await reachCommitStep();
    await drop("f1", "c4");
    expect(bodyText()).toContain("Routine complete.");
    expect(bodyText()).not.toContain("Not in your repertoire.");
    expect(screen.queryByTestId("repertoire-panel")).toBeNull();
  });
  it("a Chess960 answer (422) is the history-only note, and a failed load offers Try again", async () => {
    getGameReview.mockRejectedValue(new ApiError(422, "not_analysable"));
    await renderReview();
    expect(bodyText()).toContain("Chess960 games are kept as history and are not reviewed.");
    expect(screen.queryByTestId("board")).toBeNull();
    cleanup();
    getGameReview.mockRejectedValue(new ApiError(500, "boom"));
    await renderReview();
    expect(bodyText()).toContain("This game couldn't be loaded.");
    expect(screen.getByText("Try again")).toBeInTheDocument();
  });
  it("a game without its moves shows the window card and no board; one without analysis shows board and moves only", async () => {
    const p = reviewPayload();
    p.game.moves = null;
    p.game.fen_sequence = null;
    p.game.ply_analysis = null;
    p.game.out_of_window = true;
    getGameReview.mockResolvedValue(p);
    await renderReview();
    expect(bodyText()).toContain("Outside your analysis window");
    expect(bodyText()).toContain("analysis_game_limit");
    expect(screen.queryByTestId("board")).toBeNull();
    cleanup();
    const q = reviewPayload();
    q.game.analyzed = false;
    q.game.ply_analysis = null;
    q.blunders = [];
    getReviewPrefs.mockResolvedValue({ review_default_mode: "review", review_show_timer: true });
    getGameReview.mockResolvedValue(q);
    await renderReview(2);
    expect(screen.getByTestId("board")).toBeInTheDocument();
    expect(evalBarProps).toBeNull();
    expect(arrowColors()).toEqual([ARROWS.book]);
    expect(bodyText()).toContain("has not been analysed yet");
    expect(bodyText()).toContain("1. e4 e5");
  });
});

describe("commit outcomes", () => {
  it.each([
    [409, "This rep was already recorded. Starting a fresh one."],
    [410, "This game needs re-fetching before we can save."],
    [422, "We couldn't accept that move. Try again."],
  ])("a %i surfaces its copy and the reveal still happened", async (status, copy) => {
    learnCommit.mockRejectedValue(new ApiError(status, "x"));
    await renderReview();
    await reachCommitStep();
    await drop("f1", "c4");
    await flush();
    expect(bodyText()).toContain(copy);
    expect(bodyText()).toContain("Routine complete.");
    expect(bodyText()).toContain(BOOK_MOVE);
    expect(learnCommit).toHaveBeenCalledTimes(1);
    expect(bodyText().toLowerCase()).not.toContain("changed your move");
  });
  it("a network failure retries once with the same attempt id and bytes, then offers Try again", async () => {
    learnCommit.mockRejectedValue(new TypeError("offline"));
    await renderReview();
    await reachCommitStep();
    await drop("f1", "c4");
    await flush();
    expect(learnCommit).toHaveBeenCalledTimes(2);
    expect(learnCommit.mock.calls[0][1]).toEqual(learnCommit.mock.calls[1][1]);
    expect(bodyText()).toContain("Not saved — check your connection.");
    learnCommit.mockResolvedValue({ id: 9, created: false });
    await act(async () => screen.getByText("Try again").click());
    await flush();
    expect(learnCommit).toHaveBeenCalledTimes(3);
    expect(learnCommit.mock.calls[2][1]).toEqual(learnCommit.mock.calls[0][1]);
    expect(bodyText()).not.toContain("Not saved");
  });
  it("Just show me reveals and writes no row", async () => {
    await renderReview();
    await act(async () => screen.getByText("Just show me").click());
    expect(bodyText()).toContain("Routine complete.");
    expect(bodyText()).toContain(BOOK_MOVE);
    expect(learnCommit).not.toHaveBeenCalled();
  });
});

describe("the timer", () => {
  it("off means untimed: nothing displayed, elapsed_ms omitted", async () => {
    getReviewPrefs.mockResolvedValue({ review_default_mode: "learn", review_show_timer: false });
    await renderReview();
    await reachCommitStep();
    await drop("f1", "c4");
    expect(screen.queryAllByLabelText("Elapsed time")).toHaveLength(0);
    expect("elapsed_ms" in learnCommit.mock.calls[0][1]).toBe(false);
  });
  it("on: displayed and sent", async () => {
    await renderReview();
    expect(screen.getAllByLabelText("Elapsed time").length).toBeGreaterThan(0);
    await reachCommitStep();
    await drop("f1", "c4");
    expect(typeof learnCommit.mock.calls[0][1].elapsed_ms).toBe("number");
  });
  it("Time my reps and Always start here write the settings row", async () => {
    getReviewPrefs.mockResolvedValue({ review_default_mode: "review", review_show_timer: true });
    await renderReview();
    const always = screen.getByLabelText("Always start here") as HTMLInputElement;
    expect(always.checked).toBe(true);
    expect(always.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Mode"), { target: { value: "learn" } });
    expect(always.checked).toBe(false);
    await act(async () => fireEvent.click(always));
    expect(setReviewPref).toHaveBeenCalledWith({ review_default_mode: "learn" });
    await act(async () => fireEvent.click(screen.getByLabelText("Time my reps")));
    expect(setReviewPref).toHaveBeenCalledWith({ review_show_timer: false });
    expect(screen.queryAllByLabelText("Elapsed time")).toHaveLength(0);
  });
});

describe("exactly one prompt surface at any width", () => {
  it("below lg the fixed sheet is the one; at lg and above the in-flow panel is", async () => {
    setViewport(PHONE);
    await renderReview();
    expect(countText("Just show me")).toBe(1);
    expect(countText("Next question")).toBe(1);
    expect(screen.getByTestId("learn-sheet")).toContainElement(screen.getByText("Just show me"));
    expect(screen.queryByTestId("learn-panel")).toBeNull();
    cleanup();
    setViewport(DESKTOP);
    await renderReview();
    expect(countText("Just show me")).toBe(1);
    expect(countText("Next question")).toBe(1);
    expect(screen.queryByTestId("learn-sheet")).toBeNull();
    expect(screen.getByTestId("learn-panel")).toContainElement(screen.getByText("Just show me"));
  });
  it("a resize moves the prompt between the two without duplicating it", async () => {
    setViewport(DESKTOP);
    await renderReview();
    await act(async () => setViewport(PHONE));
    expect(countText("Just show me")).toBe(1);
    expect(screen.getByTestId("learn-sheet")).toContainElement(screen.getByText("Just show me"));
  });
  it("after the reveal the legend renders in flow at every width", async () => {
    for (const width of [PHONE, DESKTOP]) {
      cleanup();
      setViewport(width);
      await renderReview();
      await act(async () => screen.getByText("Just show me").click());
      expect(bodyText()).toContain("Routine complete.");
      expect(countText("Just show me")).toBe(0);
    }
  });
});

describe("the prep arrow and panel in Review mode", () => {
  beforeEach(() => getReviewPrefs.mockResolvedValue({ review_default_mode: "review", review_show_timer: true }));
  it("a ply with a book move draws the book arrow and the swatch beside the line", async () => {
    await renderReview(2);
    expect(arrowColors()).toContain(ARROWS.book);
    expect(bodyText()).toContain("Your prep plays");
    const swatches = Array.from(screen.getByTestId("repertoire-panel").querySelectorAll("span[aria-hidden]")).filter((el) => (el as HTMLElement).style.backgroundColor !== "");
    expect(swatches).toHaveLength(1);
  });
  it("no swatch when the book arrow merged into an arrow already drawn", async () => {
    const p = reviewPayload();
    p.repertoire!.by_ply["2"] = entry({ status: "match", book_move: STORED_BEST, book: "Spanish", chapter: "Main", line_name: "Closed", transposed: false });
    getGameReview.mockResolvedValue(p);
    await renderReview(2);
    expect(arrowColors()).not.toContain(ARROWS.book);
    expect(bodyText()).toContain("Your prep plays");
    const swatches = Array.from(screen.getByTestId("repertoire-panel").querySelectorAll("span[aria-hidden]"));
    expect(swatches).toHaveLength(0);
  });
  it("an opponent-turn ply shows no panel, no arrow and no copy; an uncovered player ply says Not in your repertoire", async () => {
    await renderReview(1);
    expect(arrowColors()).not.toContain(ARROWS.book);
    expect(bodyText()).not.toContain("Your prep plays");
    expect(bodyText()).not.toContain("Not in your repertoire");
    expect(screen.queryByTestId("repertoire-panel")).toBeNull();
    cleanup();
    await renderReview(0);
    expect(bodyText()).toContain("Not in your repertoire.");
    expect(screen.getByTestId("repertoire-panel")).toBeInTheDocument();
  });
  it("a conflict lists every distinct move in the order received, as a list", async () => {
    const p = reviewPayload();
    const groups = ["Bc4", "Bb5", "d4", "c3", "Nc3"].map((move, i) => ({ move, book: "Book", chapter: `Ch${i}`, line_name: `L${i}`, more_lines: i === 1 ? 2 : 0 }));
    p.repertoire!.by_ply["2"] = entry({ status: "conflict", conflict: groups });
    getGameReview.mockResolvedValue(p);
    await renderReview(2);
    expect(bodyText()).toContain("Your prep has different moves here.");
    const items = Array.from(screen.getByTestId("repertoire-panel").querySelectorAll("ul > li")).map((li) => (li.textContent ?? "").trim().split(" ")[0]);
    expect(items).toEqual(["Bc4", "Bb5", "d4", "c3", "Nc3"]);
    expect(bodyText()).toContain("+2 more");
    expect(arrowColors()).not.toContain(ARROWS.book);
  });
});

describe("stepping, Explore and the exits", () => {
  beforeEach(() => getReviewPrefs.mockResolvedValue({ review_default_mode: "review", review_show_timer: true }));
  it("stepping past the decision reviews the move just played, inaccuracies+ sits on the decision, and the URL carries the ply", async () => {
    await renderReview(3);
    expect(screen.getByTestId("blunder-card")).toBeInTheDocument(); // the blunder at ply 2, now on the board
    expect(arrowColors()).toContain(ARROWS.played);
    await act(async () => fireEvent.keyDown(window, { key: "ArrowLeft" }));
    expect(bodyText()).toContain("move 2 / 4");
    expect(screen.queryByTestId("blunder-card")).toBeNull(); // with the skip off, ply 2 reviews ply 1's move: none
    expect(arrowColors()).toContain(ARROWS.bestHint);
    await act(async () => fireEvent.keyDown(window, { key: "ArrowLeft" }));
    expect(bodyText()).toContain("move 1 / 4");
    fireEvent.click(screen.getByLabelText("Step only through inaccuracies+"));
    await act(async () => fireEvent.keyDown(window, { key: "ArrowRight" }));
    expect(bodyText()).toContain("move 2 / 4");
    expect(screen.getByTestId("blunder-card")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next →" })).toBeDisabled();
  });
  it("Explore opens with the current board and the player's orientation, and closes on a ply change", async () => {
    const p = reviewPayload();
    p.game.player_color = "black";
    getGameReview.mockResolvedValue(p);
    await renderReview(2);
    fireEvent.click(screen.getByText("🔍 Explore from here"));
    expect(screen.getByTestId("explore-layer")).toBeInTheDocument();
    expect(exploreProps.fen).toBe(fen_sequence[2]);
    expect(exploreProps.orientation).toBe("black");
    await act(async () => fireEvent.keyDown(window, { key: "ArrowRight" })); // ignored while exploring
    expect(bodyText()).toContain("move 2 / 4");
    await act(async () => exploreProps.onClose());
    expect(screen.queryByTestId("explore-layer")).toBeNull();
    fireEvent.click(screen.getByText("🔍 Explore from here"));
    await act(async () => fireEvent.keyDown(window, { key: "Escape" })); // the layer owns Escape while open
    expect(screen.getByTestId("explore-layer")).toBeInTheDocument();
    await act(async () => exploreProps.onClose());
    fireEvent.click(screen.getByText("🔍 Explore from here"));
    fireEvent.click(screen.getByRole("button", { name: "Next →" }));
    expect(screen.queryByTestId("explore-layer")).toBeNull();
  });
  it("landing stamps the game reviewed once, and Escape returns to where the review was opened from", async () => {
    await renderReview(2, { from: { pathname: "/games" }, games: { page: 3 } });
    expect(markGameReviewed).toHaveBeenCalledTimes(1);
    expect(markGameReviewed).toHaveBeenCalledWith(1);
    await act(async () => fireEvent.keyDown(window, { key: "ArrowRight" }));
    expect(markGameReviewed).toHaveBeenCalledTimes(1);
    await act(async () => fireEvent.keyDown(window, { key: "Escape" }));
    expect(screen.getByTestId("probe").textContent).toBe('/games|{"from":{"pathname":"/games"},"games":{"page":3}}');
  });
  it("Close with no origin returns to the worklist", async () => {
    await renderReview(2);
    fireEvent.click(screen.getByLabelText("Close review"));
    expect(screen.getByTestId("probe").textContent).toBe("/review|null");
  });
});
