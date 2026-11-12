import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Chess } from "chess.js";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EngineEval } from "../engine/useStockfish";
import type { LatestReviewResponse, RepertoireEntry } from "../games";
import { ARROWS } from "../utils/board";
import LatestReview from "./LatestReview";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position?: string; arrows?: unknown[] } }) => <div data-testid="board" data-position={options.position} data-arrows={JSON.stringify(options.arrows ?? [])} />,
}));

// The walk-through is not under test here (029 owns it): the stub shows which line is open.
vi.mock("../components/PositionCard/LineReaderPanel", () => ({
  LineWalkthrough: ({ lineId, initialPly }: { lineId: number; initialPly: number }) => <div data-testid="walk">{`${lineId}:${initialPly}`}</div>,
  WholeLineToggle: ({ open, onToggle }: { open: boolean; onToggle: () => void }) => (
    <button type="button" onClick={onToggle}>
      {open ? "Hide the line" : "Read the whole line"}
    </button>
  ),
}));

// Engine instances in mount order, each with the options it was created with. The body's engine
// is the one created with `enabled: true`; the Ask panel's own (for a named alternative) is not.
const analyze = vi.fn();
const stop = vi.fn();
const reset = vi.fn();
type State = { ready: boolean; evalState: EngineEval | null };
const instances: { set: (s: State) => void; depth: number | undefined; enabled: boolean }[] = [];
vi.mock("../engine/useStockfish", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../engine/useStockfish")>();
  return {
    ...actual,
    useStockfish: ({ enabled, depth }: { enabled: boolean; depth?: number }) => {
      const [s, setS] = useState<State>({ ready: false, evalState: null });
      if (!instances.some((i) => i.set === setS)) instances.push({ set: setS, depth, enabled });
      return { ...s, analyze, stop, reset };
    },
  };
});
const body = () => instances.filter((i) => i.enabled).at(-1)!;
const push = (s: State) => act(() => body().set(s));

const askExplore = vi.fn();
vi.mock("../ask", () => ({
  askExplore: (...a: unknown[]) => askExplore(...a),
  askExploreDryRun: vi.fn(),
  getAskHistory: () => Promise.resolve({ items: [] }),
}));

const MOVES = ["e4", "e5", "Nf3", "Nc6", "Bb5"];
const FENS = (() => {
  const c = new Chess();
  const out = [c.fen()];
  for (const m of MOVES) {
    c.move(m);
    out.push(c.fen());
  }
  return out;
})();

const entry = (over: Partial<RepertoireEntry>): RepertoireEntry => ({ status: "match", book_move: null, book: "White", chapter: "Spanish", line_name: "Main", line_id: 1, line_ply: 0, plan: [], more_lines: 0, transposed: false, conflict: null, ...over });
const turn = entry({ status: "not_your_turn", book: null, chapter: null, line_name: null, line_id: null, line_ply: null });
const conflictAt4 = (groups: number[]) =>
  entry({
    status: "conflict",
    book: null,
    chapter: null,
    line_name: null,
    line_id: null,
    line_ply: null,
    conflict: groups.map((id) => ({ move: id === 1 ? "Bb5" : "Bc4", book: "White", chapter: id === 1 ? "Spanish" : "Italian", line_name: `L${id}`, line_id: id, line_ply: 4, more_lines: 0 })),
  });
const response = (at4: RepertoireEntry = entry({ book_move: "Bb5", line_ply: 4 })): LatestReviewResponse => ({
  game: {
    source: "chesscom",
    url: "https://example.test/g/1",
    played_at: "2026-11-12T15:00:00Z",
    time_control: "600",
    time_class: "rapid",
    opening_name: null,
    opening_eco: null,
    termination: "resignation",
    player_color: "white",
    opponent_username: "opp",
    opponent_rating: 1500,
    player_rating: 1520,
    result: "win",
    moves: MOVES,
    fen_sequence: FENS,
  },
  repertoire: {
    by_ply: {
      "0": entry({ book_move: "e4", line_ply: 0 }),
      "1": turn,
      "2": entry({ book_move: "Nf3", line_ply: 2 }),
      "3": turn,
      "4": at4,
      "5": turn,
    },
  },
});

let latest: { status: number; body: unknown } = { status: 200, body: response() };
let settings: Record<string, unknown> = { explore_engine_depth: 16 };
let holdLatest = false;
let releaseLatest: () => void = () => {};
const requests: string[] = [];
beforeEach(() => {
  instances.length = 0;
  analyze.mockClear();
  stop.mockClear();
  askExplore.mockReset().mockResolvedValue({ explanation: "Because.", cached: false, model: "m" });
  latest = { status: 200, body: response() };
  settings = { explore_engine_depth: 16 };
  holdLatest = false;
  requests.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      requests.push(url.replace(/^.*\/api/, ""));
      if (url.endsWith("/settings")) return { ok: true, status: 200, statusText: "OK", json: async () => settings };
      if (url.includes("/review/latest")) {
        if (holdLatest) await new Promise<void>((r) => (releaseLatest = r));
        return { ok: latest.status === 200, status: latest.status, statusText: "x", json: async () => latest.body };
      }
      return { ok: false, status: 404, statusText: "Not Found", json: async () => ({}) };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const ev = (fen: string, over: Partial<EngineEval> = {}): EngineEval => ({ fen, evalCp: 30, bestMoveUci: "g1f3", pvUci: ["g1f3", "b8c6"], depth: 16, thinking: false, ...over });
const arrows = () => JSON.parse(screen.getByTestId("board").getAttribute("data-arrows") ?? "[]") as { startSquare: string; endSquare: string; color: string }[];
const engineArrows = () => arrows().filter((a) => a.color === ARROWS.engine);
const next = () => fireEvent.click(screen.getByRole("button", { name: "Next →" }));
const back = () => fireEvent.click(screen.getByRole("button", { name: "← Back" }));

async function open(path = "/review/latest?source=chesscom") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/review/latest" element={<LatestReview />} />
        <Route path="/" element={<p>home page</p>} />
      </Routes>
    </MemoryRouter>,
  );
  await act(async () => {});
  await act(async () => {});
}

describe("Review latest game", () => {
  it("opens on the start position with the repertoire arrow, and searches it at the saved depth", async () => {
    settings = { explore_engine_depth: 18 };
    await open();
    expect(screen.getByTestId("board")).toHaveAttribute("data-position", FENS[0]);
    expect(screen.getByText(/move 0 \/ 5/)).toBeInTheDocument();
    expect(body().depth).toBe(18);
    expect(analyze).toHaveBeenLastCalledWith(FENS[0], 18);
    expect(arrows().some((a) => a.color === ARROWS.book && a.startSquare === "e2" && a.endSquare === "e4")).toBe(true);
    expect(screen.getByText("White · Spanish · Main")).toBeInTheDocument();
    expect(requests.filter((r) => r.startsWith("/review/latest"))).toEqual(["/review/latest?source=chesscom"]);
  });

  it("draws the engine arrow as it searches the board on show, with the depth reached", async () => {
    await open();
    push({ ready: true, evalState: ev(FENS[0], { bestMoveUci: "d2d4", thinking: true, depth: 9 }) });
    expect(engineArrows()).toEqual([{ startSquare: "d2", endSquare: "d4", color: ARROWS.engine }]);
    expect(screen.getByTestId("engine-status")).toHaveTextContent("depth 9/16");
    push({ ready: true, evalState: ev(FENS[0], { bestMoveUci: "e2e4" }) });
    expect(screen.getByTestId("engine-status")).toHaveTextContent("Stockfish 19 · depth 16");
    expect(screen.getByTestId("engine-panel")).toHaveTextContent("Best e4");
  });

  it("never draws a late result for a board already stepped past", async () => {
    await open();
    push({ ready: true, evalState: ev(FENS[0], { thinking: true }) });
    next();
    expect(analyze).toHaveBeenLastCalledWith(FENS[1], 16);
    // The search for the start position finishes after the step: not this board's.
    push({ ready: true, evalState: ev(FENS[0], { bestMoveUci: "d2d4" }) });
    expect(engineArrows()).toEqual([]);
    expect(screen.getByTestId("engine-status")).toHaveTextContent("depth 0/16");
  });

  it("serves a board stepped back to from the visit's cache, asking for nothing", async () => {
    await open();
    push({ ready: true, evalState: ev(FENS[0], { bestMoveUci: "d2d4" }) });
    next();
    push({ ready: true, evalState: ev(FENS[1], { bestMoveUci: "e7e5" }) });
    next();
    analyze.mockClear();
    stop.mockClear();
    back();
    back();
    expect(screen.getByTestId("board")).toHaveAttribute("data-position", FENS[0]);
    expect(analyze).not.toHaveBeenCalled();
    expect(stop).toHaveBeenCalled();
    expect(engineArrows()).toEqual([{ startSquare: "d2", endSquare: "d4", color: ARROWS.engine }]);
  });

  it("a depth change starts a fresh engine and searches the board on show again", async () => {
    await open();
    push({ ready: true, evalState: ev(FENS[0], { bestMoveUci: "d2d4" }) });
    expect(engineArrows()).toHaveLength(1);
    analyze.mockClear();
    fireEvent.change(screen.getByLabelText("Engine depth"), { target: { value: "20" } });
    expect(body().depth).toBe(20);
    expect(analyze).toHaveBeenLastCalledWith(FENS[0], 20);
    // The old depth's result is gone with its worker.
    expect(engineArrows()).toEqual([]);
  });

  it("does not search a board where the game is over", async () => {
    const mated = ["f3", "e5", "g4", "Qh4#"];
    const c = new Chess();
    const fens = [c.fen()];
    for (const m of mated) {
      c.move(m);
      fens.push(c.fen());
    }
    latest = { status: 200, body: { ...response(), game: { ...response().game, moves: mated, fen_sequence: fens }, repertoire: null } };
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Last move" }));
    expect(analyze).not.toHaveBeenLastCalledWith(fens[4], 16);
    expect(stop).toHaveBeenCalled();
    expect(screen.getByTestId("engine-status")).toHaveTextContent("The game is over on this board.");
  });

  it("Ask Opus sends the board on show, the game's colour, no stored game and the finished snapshot", async () => {
    await open();
    next();
    next();
    push({ ready: true, evalState: ev(FENS[2], { bestMoveUci: "g1f3", pvUci: ["g1f3", "b8c6"], depth: 16, evalCp: 25 }) });
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "why?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Opus/ }));
    expect(askExplore).toHaveBeenCalledTimes(1);
    expect(askExplore.mock.calls[0]).toEqual([{ seed_fen: FENS[2], orientation: "white", moves: [], engine: { depth: 16, eval_cp: 25, best_move: "g1f3", pv: ["g1f3", "b8c6"] }, game: null }, "why?", null]);
  });

  it("Ask Opus sends black as the orientation of a game played as Black", async () => {
    latest = { status: 200, body: { ...response(), game: { ...response().game, player_color: "black" }, repertoire: null } };
    await open();
    next();
    push({ ready: true, evalState: ev(FENS[1], { bestMoveUci: "e7e5", pvUci: ["e7e5"] }) });
    fireEvent.click(screen.getByRole("button", { name: /Ask Opus/ }));
    expect(askExplore.mock.calls[0][0]).toEqual({ seed_fen: FENS[1], orientation: "black", moves: [], engine: { depth: 16, eval_cp: 30, best_move: "e7e5", pv: ["e7e5"] }, game: null });
  });

  it("Ask Opus waits while the board's search is unfinished", async () => {
    await open();
    push({ ready: true, evalState: ev(FENS[0], { thinking: true }) });
    expect(screen.getByRole("button", { name: /Waiting/ })).toBeDisabled();
  });

  describe("Read the whole line follows 029's rule", () => {
    it("stays open through opponent plies and a conflict ply whose groups hold the open line", async () => {
      latest = { status: 200, body: response(conflictAt4([2, 1])) };
      await open();
      next();
      next(); // ply 2: Nf3 in line 1
      fireEvent.click(screen.getByRole("button", { name: "Read the whole line" }));
      expect(screen.getByTestId("walk")).toHaveTextContent("1:3");
      next(); // opponent's turn
      expect(screen.getByTestId("walk")).toHaveTextContent("1:3");
      next(); // the conflict: line 1 is one of its groups
      expect(screen.getByTestId("walk")).toHaveTextContent("1:3");
    });

    it("closes at a conflict ply whose groups do not hold it", async () => {
      latest = { status: 200, body: response(conflictAt4([2, 3])) };
      await open();
      next();
      next();
      fireEvent.click(screen.getByRole("button", { name: "Read the whole line" }));
      next();
      expect(screen.getByTestId("walk")).toBeInTheDocument();
      next();
      expect(screen.queryByTestId("walk")).toBeNull();
      back(); // coming back does not reopen it
      expect(screen.queryByTestId("walk")).toBeNull();
    });
  });

  it("shows a refusal's notice and a way back", async () => {
    latest = { status: 422, body: { detail: { code: "chess960", message: "Your latest Chess.com game is Chess960 — kept as history, not reviewed." } } };
    await open();
    expect(screen.getByTestId("latest-refused")).toHaveTextContent("Chess960 — kept as history");
    expect(screen.queryByTestId("board")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "← Back" }));
    expect(screen.getByText("home page")).toBeInTheDocument();
  });

  it("the site not answering (the route's 502) is a failure with its message and a retry, not a refusal", async () => {
    latest = { status: 502, body: { detail: { code: "fetch_failed", message: "The site did not return the game; try again shortly." } } };
    await open();
    expect(screen.queryByTestId("latest-refused")).toBeNull();
    expect(screen.getByText("The site did not return the game; try again shortly.")).toBeInTheDocument();
    latest = { status: 200, body: response() };
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    await act(async () => {});
    expect(screen.getByTestId("board")).toBeInTheDocument();
  });

  it("steps with the arrow keys, leaves on Escape, and ignores the keys while Explore is open", async () => {
    await open();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByTestId("board")).toHaveAttribute("data-position", FENS[2]);
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByTestId("board")).toHaveAttribute("data-position", FENS[1]);
    fireEvent.click(screen.getByRole("button", { name: /Explore from here/ }));
    expect(screen.getByTestId("explore-layer")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowRight", bubbles: true });
    // The review underneath did not move (the layer's own board shows FENS[1] too, so read the review's).
    expect(screen.getAllByTestId("board")[0]).toHaveAttribute("data-position", FENS[1]);
    fireEvent.click(screen.getByRole("button", { name: "← Close" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByText("home page")).toBeInTheDocument();
  });

  it("says the server may be waking after a few seconds, and offers a retry on failure", async () => {
    vi.useFakeTimers();
    holdLatest = true;
    latest = { status: 500, body: { detail: "boom" } };
    render(
      <MemoryRouter initialEntries={["/review/latest?source=lichess"]}>
        <Routes>
          <Route path="/review/latest" element={<LatestReview />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Fetching your latest Lichess game…")).toBeInTheDocument();
    expect(screen.queryByTestId("slow-note")).toBeNull();
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByTestId("slow-note")).toBeInTheDocument();
    await act(async () => releaseLatest());
    expect(screen.getByText("Could not fetch the game.")).toBeInTheDocument();
    holdLatest = false;
    latest = { status: 200, body: response() };
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    await act(async () => {});
    expect(screen.getByTestId("board")).toBeInTheDocument();
    expect(screen.queryByTestId("slow-note")).toBeNull();
  });
});
