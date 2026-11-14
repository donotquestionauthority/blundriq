import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Chess } from "chess.js";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExplorerEntry } from "../components/ExplorerEntry";
import { Overlay } from "../components/PositionCard/Overlay";
import type { PositionCardData } from "../components/PositionCard/types";
import type { EngineEval } from "../engine/useStockfish";
import type { ExplorerPosition, RepertoireMove } from "../explorer";
import { ARROWS, HIGHLIGHT } from "../utils/board";
import OpeningExplorer from "./OpeningExplorer";

// The board: its options are kept so a test can drop a piece the way react-chessboard does.
type DropArgs = { piece: unknown; sourceSquare: string; targetSquare: string | null };
let board: { position?: string; arrows?: unknown[]; onPieceDrop?: (a: DropArgs) => boolean; onSquareClick?: (a: { square: string }) => void; squareStyles?: Record<string, { background?: string; backgroundColor?: string }>; boardOrientation?: string } = {};
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: typeof board }) => {
    board = options;
    return <div data-testid="board" data-position={options.position} data-orientation={options.boardOrientation} data-arrows={JSON.stringify(options.arrows ?? [])} />;
  },
}));

vi.mock("../components/PositionCard/LineReaderPanel", () => ({
  LineWalkthrough: ({ lineId, initialPly }: { lineId: number; initialPly: number }) => <div data-testid="walk">{`${lineId}:${initialPly}`}</div>,
  WholeLineToggle: () => null,
  LineReaderPanel: () => null,
}));

const analyze = vi.fn();
const stop = vi.fn();
type EngineState = { ready: boolean; evalState: EngineEval | null };
const instances: { set: (s: EngineState) => void; enabled: boolean }[] = [];
vi.mock("../engine/useStockfish", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../engine/useStockfish")>();
  return {
    ...actual,
    useStockfish: ({ enabled }: { enabled: boolean }) => {
      const [s, setS] = useState<EngineState>({ ready: false, evalState: null });
      if (!instances.some((i) => i.set === setS)) instances.push({ set: setS, enabled });
      return { ...s, analyze, stop, reset: vi.fn() };
    },
  };
});
const engine = (s: EngineState) => act(() => instances.filter((i) => i.enabled).at(-1)!.set(s));
const ev = (fen: string, over: Partial<EngineEval> = {}): EngineEval => ({ fen, evalCp: 20, bestMoveUci: "g1f3", pvUci: ["g1f3"], depth: 16, thinking: false, ...over });

const askExplore = vi.fn();
vi.mock("../ask", () => ({
  askExplore: (...a: unknown[]) => askExplore(...a),
  askExploreDryRun: vi.fn(),
  getAskHistory: () => Promise.resolve({ items: [] }),
}));

const fenOf = (moves: string[]) => {
  const g = new Chess();
  for (const m of moves) g.move(m);
  return g.fen();
};
const rm = (san: string, over: Partial<RepertoireMove> = {}): RepertoireMove => ({ san, book: "White book", chapter: "Open", line_name: `L-${san}`, line_id: 7, line_ply: 0, lines: 1, ...over });
const numbers = { n: 0, w: 0, d: 0, l: 0, score: null, n_rated: 0, vs_expectation: null };

/** The default answer for a path: the repertoire plays 1.e4 2.Nf3 as White, prepares 1…e5 as
 *  Black; after a move of the player's that is not the book's, `arrival` says off-book. */
function answerFor(colour: "white" | "black", moves: string[]): ExplorerPosition {
  const book: Record<string, string> = colour === "white" ? { "": "e4", "e4,e5": "Nf3" } : { e4: "e5" };
  const at = moves.join(",");
  const playerTurn = (moves.length % 2 === 0) === (colour === "white");
  const prescribed = book[at];
  const before = moves.slice(0, -1).join(",");
  const lastWasMine = moves.length > 0 && !playerTurn;
  const arrival = lastWasMine && book[before] ? { off_book: book[before] !== moves.at(-1), book_moves: [book[before]] } : lastWasMine ? { off_book: false, book_moves: [] } : null;
  return {
    colour,
    fen: fenOf(moves),
    ply: moves.length,
    player_to_move: playerTurn,
    repertoire: { status: prescribed && playerTurn ? "match" : "none", moves: prescribed && playerTurn ? [rm(prescribed, { line_ply: moves.length })] : [] },
    node: { ...numbers, n: 4, w: 2, d: 1, l: 1, score: 0.625, n_rated: 4, vs_expectation: 0.05, continued: 4 },
    moves: [{ san: prescribed ?? "d4", ...numbers, n: 3, w: 2, d: 1, l: 0, score: 0.83, n_rated: 3, vs_expectation: 0.1, in_repertoire: !!prescribed }],
    arrival,
    lines_through: prescribed ? 1 : 0,
    months: 12,
    time_class: "focus",
    beyond_prefix: false,
  };
}

type Pending = { resolve: () => void };
const held = new Map<string, Pending>();
let holdNext: string[] = [];
let failNext: string[] = [];
let override: ((colour: "white" | "black", moves: string[]) => ExplorerPosition | null) | null = null;
let linesBody: unknown = { total: 0, rows: [], offset: 0, page_size: 50 };
const requests: string[] = [];

beforeEach(() => {
  instances.length = 0;
  analyze.mockClear();
  askExplore.mockReset().mockResolvedValue({ explanation: "Because.", cached: false, model: "m" });
  held.clear();
  holdNext = [];
  failNext = [];
  override = null;
  requests.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = url.replace(/^.*\/api/, "");
      requests.push(path);
      if (path === "/settings") return { ok: true, status: 200, statusText: "OK", json: async () => ({ explore_engine_depth: 16 }) };
      const u = new URL(path, "http://x");
      if (u.pathname === "/explorer/position") {
        const colour = u.searchParams.get("colour") as "white" | "black";
        const moves = u.searchParams.get("moves") ? u.searchParams.get("moves")!.split(",") : [];
        const k = moves.join(",");
        if (holdNext.includes(k)) {
          holdNext = holdNext.filter((x) => x !== k);
          await new Promise<void>((resolve) => held.set(k, { resolve }));
        }
        if (failNext.includes(k)) {
          failNext = failNext.filter((x) => x !== k);
          return { ok: false, status: 500, statusText: "boom", json: async () => ({}) };
        }
        const body = override?.(colour, moves) ?? answerFor(colour, moves);
        return { ok: true, status: 200, statusText: "OK", json: async () => body };
      }
      if (u.pathname === "/explorer/lines") return { ok: true, status: 200, statusText: "OK", json: async () => linesBody };
      return { ok: false, status: 404, statusText: "Not Found", json: async () => ({}) };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const settle = async () => {
  for (let i = 0; i < 4; i++) await act(async () => {});
};

function Probe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div>
      <span data-testid="where">{location.pathname + location.search}</span>
      <button type="button" onClick={() => navigate(-1)}>
        browser back
      </button>
      <button type="button" onClick={() => navigate("/explorer?colour=white&moves=d4&time_class=focus")}>
        nav link
      </button>
    </div>
  );
}

function Cards() {
  const moves = ["e4", "e5", "Nf3"];
  return <ExplorerEntry moves={moves} ply={2} fen={fenOf(moves.slice(0, 2))} colour="white" className="x" />;
}

function mount(entries: string[]) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
      <Probe />
      <Routes>
        <Route path="/explorer" element={<OpeningExplorer />} />
        <Route path="/cards" element={<Cards />} />
      </Routes>
    </MemoryRouter>,
  );
}
async function open(search: string, before: string[] = ["/cards"]) {
  const view = mount([...before, `/explorer${search}`]);
  await settle();
  return view;
}
const where = () => screen.getByTestId("where").textContent ?? "";
const movesInUrl = () => new URLSearchParams(where().split("?")[1] ?? "").get("moves") ?? "";
const positionRequests = () => requests.filter((r) => r.startsWith("/explorer/position")).map((r) => new URL(r, "http://x").searchParams.get("moves") ?? "");
const arrows = () => JSON.parse(screen.getByTestId("board").getAttribute("data-arrows") ?? "[]") as { startSquare: string; endSquare: string; color: string }[];
const drop = async (from: string, to: string) => {
  let accepted = false;
  act(() => {
    accepted = board.onPieceDrop!({ piece: null, sourceSquare: from, targetSquare: to });
  });
  await settle();
  return accepted;
};
const playRow = async (san: string) => {
  fireEvent.click(within(screen.getByTestId("explorer-games")).getByRole("button", { name: san }));
  await settle();
};
const backBtn = () => screen.getByRole("button", { name: "← Back" });
const forwardBtn = () => screen.getByRole("button", { name: "Forward →" });
const forwardMoves = () => Array.from(document.querySelectorAll('[data-forward="true"]')).map((el) => el.textContent);
const notice = () => screen.queryByTestId("off-book");

describe("Opening Explorer: the URL", () => {
  it("replays the path, drops an illegal tail with replace, and reads only what it kept", async () => {
    await open("?colour=white&moves=e4,--,e5&time_class=focus");
    expect(movesInUrl()).toBe("e4");
    expect(positionRequests()).toEqual(["e4"]);
    expect(screen.getByTestId("board").getAttribute("data-position")).toBe(fenOf(["e4"]));
    fireEvent.click(screen.getByRole("button", { name: "browser back" }));
    expect(where()).toBe("/cards"); // the bad URL was replaced, not kept behind the good one
  });

  it("cuts a hand-edited path past move 30 to its first sixty plies before any read", async () => {
    const shuffle = ["Nf3", "Nf6", "Ng1", "Ng8"];
    const sixty = Array.from({ length: 15 }, () => shuffle).flat();
    await open(`?colour=white&moves=${[...sixty, "Nf3"].join(",")}&time_class=focus`);
    expect(movesInUrl()).toBe(sixty.join(","));
    expect(positionRequests()).toEqual([sixty.join(",")]);
    expect(screen.getByText("The Opening Explorer stops at move 30.")).toBeInTheDocument();
    expect(await drop("g1", "f3")).toBe(false);
    expect(movesInUrl()).toBe(sixty.join(","));
    fireEvent.click(backBtn());
    await settle();
    expect(movesInUrl()).toBe(sixty.slice(0, -1).join(","));
  });

  it("means white and focus when the URL does not say", async () => {
    await open("");
    expect(where()).toBe("/explorer?colour=white&time_class=focus");
    expect(screen.getByTestId("board").getAttribute("data-orientation")).toBe("white");
  });
});

describe("Opening Explorer: stepping", () => {
  it("Back keeps a Forward branch, a different move clears it, a jump goes anywhere on it", async () => {
    await open("?colour=white&time_class=focus");
    await playRow("e4");
    expect(await drop("e7", "e5")).toBe(true);
    await playRow("Nf3");
    expect(movesInUrl()).toBe("e4,e5,Nf3");
    fireEvent.click(backBtn());
    await settle();
    fireEvent.click(backBtn());
    await settle();
    expect(movesInUrl()).toBe("e4");
    expect(forwardMoves()).toEqual(["e5", "Nf3"]);
    fireEvent.click(forwardBtn());
    await settle();
    expect(movesInUrl()).toBe("e4,e5");
    expect(forwardMoves()).toEqual(["Nf3"]);
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await settle();
    expect(movesInUrl()).toBe("");
    expect(forwardMoves()).toEqual(["e4", "e5", "Nf3"]);
    fireEvent.click(within(screen.getByTestId("explorer-moves")).getByRole("button", { name: "Nf3" }));
    await settle();
    expect(movesInUrl()).toBe("e4,e5,Nf3");
    expect(forwardBtn()).toBeDisabled();
    fireEvent.click(backBtn());
    await settle();
    await drop("b1", "c3"); // the same board, another move
    expect(movesInUrl()).toBe("e4,e5,Nc3");
    expect(forwardMoves()).toEqual([]);
    // Playing the branch's own next move keeps the rest of it.
    fireEvent.click(backBtn());
    await settle();
    fireEvent.click(backBtn());
    await settle();
    await drop("e7", "e5");
    expect(forwardMoves()).toEqual(["Nc3"]);
  });

  it("a URL the page did not write (a link, the nav) starts without the branch", async () => {
    await open("?colour=white&moves=e4,e5&time_class=focus");
    fireEvent.click(backBtn());
    await settle();
    expect(forwardMoves()).toEqual(["e5"]);
    fireEvent.click(screen.getByRole("button", { name: "nav link" }));
    await settle();
    expect(movesInUrl()).toBe("d4");
    expect(forwardMoves()).toEqual([]);
    expect(forwardBtn()).toBeDisabled();
  });

  it("a colour change closes an open line", async () => {
    await open("?colour=white&moves=e4,e5&time_class=focus");
    fireEvent.click(within(screen.getByTestId("explorer-repertoire")).getByRole("button", { name: "Read the line" }));
    expect(screen.getByTestId("walk")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Black" }));
    await settle();
    expect(screen.queryByTestId("walk")).toBeNull();
  });

  it("a colour change keeps the path, reorients the board and clears the branch", async () => {
    await open("?colour=white&moves=e4,e5&time_class=focus");
    fireEvent.click(backBtn());
    await settle();
    expect(forwardMoves()).toEqual(["e5"]);
    fireEvent.click(screen.getByRole("button", { name: "Black" }));
    await settle();
    expect(where()).toBe("/explorer?colour=black&moves=e4&time_class=focus");
    expect(screen.getByTestId("board").getAttribute("data-orientation")).toBe("black");
    expect(forwardMoves()).toEqual([]);
    expect(forwardBtn()).toBeDisabled();
  });

  it("a reload after stepping back restores the board, the answer and the notice, but not the branch", async () => {
    const first = await open("?colour=white&moves=d4&time_class=focus");
    expect(notice()).toHaveTextContent("Off-book — your repertoire plays e4 here");
    await drop("d7", "d5");
    fireEvent.click(backBtn());
    await settle();
    expect(forwardMoves()).toEqual(["d5"]);
    const url = where();
    first.unmount();
    await open(url.replace("/explorer", ""));
    expect(where()).toBe(url);
    expect(forwardBtn()).toBeDisabled();
    expect(forwardMoves()).toEqual([]);
    expect(screen.getByTestId("board").getAttribute("data-position")).toBe(fenOf(["d4"]));
    expect(notice()).toHaveTextContent("Off-book — your repertoire plays e4 here");
    expect(screen.getByTestId("explorer-node")).toHaveTextContent("Reached in 4 games");
  });

  it("a board's answer is read once for the visit", async () => {
    await open("?colour=white&time_class=focus");
    await playRow("e4");
    fireEvent.click(backBtn());
    await settle();
    fireEvent.click(forwardBtn());
    await settle();
    expect(positionRequests()).toEqual(["", "e4"]);
  });
});

describe("Opening Explorer: moving by clicks", () => {
  const click = async (square: string) => {
    act(() => board.onSquareClick!({ square }));
    await settle();
  };
  const dots = () =>
    Object.entries(board.squareStyles ?? {})
      .filter(([, st]) => st.background === HIGHLIGHT.legalDot || st.background === HIGHLIGHT.legalRing)
      .map(([sq, st]) => `${sq}:${st.background === HIGHLIGHT.legalRing ? "ring" : "dot"}`)
      .sort();

  it("a clicked piece shows a dot on every square it can reach, and a ring on a capture", async () => {
    await open("?colour=white&moves=e4,d5&time_class=focus");
    await click("e4");
    expect(board.squareStyles?.e4?.backgroundColor).toBe(HIGHLIGHT.selected);
    expect(dots()).toEqual(["d5:ring", "e5:dot"]);
    await click("g1");
    expect(dots()).toEqual(["e2:dot", "f3:dot", "h3:dot"]);
    await click("f3");
    expect(movesInUrl()).toBe("e4,d5,Nf3");
    expect(dots()).toEqual([]);
  });

  it("an en passant capture is a ring, though its square is empty", async () => {
    await open("?colour=white&moves=e4,a6,e5,d5&time_class=focus");
    await click("e5");
    expect(dots()).toEqual(["d6:ring", "e6:dot"]);
  });

  it("the marks go with a step back and a colour change", async () => {
    await open("?colour=white&moves=e4,e5&time_class=focus");
    await click("g1");
    expect(dots()).not.toEqual([]);
    fireEvent.click(backBtn());
    await settle();
    expect(dots()).toEqual([]);
    await click("e7"); // after 1.e4, Black to move
    expect(dots()).not.toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Black" }));
    await settle();
    expect(dots()).toEqual([]);
  });

  it("no marks where no move is taken: a finished board and the 60-ply stop", async () => {
    await open("?colour=black&moves=e4,e5,Qh5,Nc6,Bc4,Nf6,Qxf7%23&time_class=focus");
    await click("e8");
    expect(dots()).toEqual([]);
    cleanup();
    const shuffle = ["Nf3", "Nf6", "Ng1", "Ng8"];
    await open(`?colour=white&moves=${Array.from({ length: 15 }, () => shuffle).flat().join(",")}&time_class=focus`);
    await click("g1");
    expect(dots()).toEqual([]);
  });

  it("an empty square or the opponent's piece shows nothing", async () => {
    await open("?colour=white&time_class=focus");
    await click("e5");
    expect(dots()).toEqual([]);
    await click("e7");
    expect(dots()).toEqual([]);
  });
});

describe("Opening Explorer: the repertoire on the board", () => {
  it("draws the prescribed move at the player's turn and the engine's as a hint, never the replies", async () => {
    await open("?colour=white&time_class=focus");
    expect(arrows()).toEqual([{ startSquare: "e2", endSquare: "e4", color: ARROWS.book }]);
    engine({ ready: true, evalState: ev(fenOf([]), { bestMoveUci: "d2d4" }) });
    expect(arrows()).toEqual([
      { startSquare: "e2", endSquare: "e4", color: ARROWS.book },
      { startSquare: "d2", endSquare: "d4", color: ARROWS.engine },
    ]);
    override = (colour, moves) => (moves.length === 1 ? { ...answerFor(colour, moves), repertoire: { status: "replies", moves: [rm("c5", { line_ply: 1 })] } } : null);
    await playRow("e4");
    expect(arrows().filter((a) => a.color === ARROWS.book)).toEqual([]);
    expect(screen.getByTestId("explorer-repertoire")).toHaveTextContent("Replies your repertoire prepares");
    expect(screen.getByTestId("explorer-repertoire")).toHaveTextContent("c5");
  });

  it("arrows every conflict group", async () => {
    override = (colour, moves) => ({ ...answerFor(colour, moves), repertoire: { status: "conflict", moves: [rm("e4"), rm("d4", { line_id: 8 }), rm("c4", { line_id: 9 })] } });
    await open("?colour=white&time_class=focus");
    expect(arrows().map((a) => a.endSquare)).toEqual(["e4", "d4", "c4"]);
    expect(screen.getAllByTestId("rep-move")).toHaveLength(3);
  });

  it("says an unreadable move is unreadable and leaves only the engine's arrow", async () => {
    override = (colour, moves) => ({ ...answerFor(colour, moves), repertoire: { status: "unreadable", moves: [] } });
    await open("?colour=white&time_class=focus");
    engine({ ready: true, evalState: ev(fenOf([])) });
    expect(screen.getByText("The repertoire's move here can't be read.")).toBeInTheDocument();
    expect(arrows()).toEqual([{ startSquare: "g1", endSquare: "f3", color: ARROWS.engine }]);
  });

  it("shows the off-book notice after a move that is not the book's at the player's turn, and none at the opponent's", async () => {
    await open("?colour=white&time_class=focus");
    await drop("d2", "d4");
    expect(notice()).toHaveTextContent("Off-book — your repertoire plays e4 here");
    await drop("g8", "f6"); // the opponent's move: never judged
    expect(notice()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await settle();
    await drop("e2", "e4");
    expect(notice()).toBeNull();
  });
});

describe("Opening Explorer: an answer belongs to its path", () => {
  it("a move played before A's answer lands needs nothing from A: B's own answer sets the notice", async () => {
    holdNext = [""];
    await open("?colour=white&time_class=focus");
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(arrows()).toEqual([]);
    await drop("d2", "d4");
    expect(notice()).toHaveTextContent("Off-book — your repertoire plays e4 here");
    await act(async () => held.get("")!.resolve());
    await settle();
    expect(notice()).toHaveTextContent("Off-book — your repertoire plays e4 here");
    expect(arrows().filter((a) => a.color === ARROWS.book)).toEqual([]); // A's e4 arrow is not B's
  });

  it("A's late answer, after another move, is never painted, and is used when A is shown again", async () => {
    holdNext = ["", "d4"];
    await open("?colour=white&time_class=focus");
    await drop("d2", "d4");
    await act(async () => held.get("")!.resolve());
    await settle();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByTestId("explorer-repertoire")).toBeNull();
    expect(notice()).toBeNull();
    expect(arrows()).toEqual([]);
    fireEvent.click(backBtn());
    await settle();
    expect(screen.getByTestId("explorer-repertoire")).toHaveTextContent("e4");
    expect(positionRequests()).toEqual(["", "d4"]);
  });

  it("shows no notice while pending or after a failure, and Try again restores it", async () => {
    holdNext = ["d4"];
    failNext = ["d4"];
    await open("?colour=white&time_class=focus");
    await drop("d2", "d4");
    expect(notice()).toBeNull();
    await act(async () => held.get("d4")!.resolve());
    await settle();
    expect(notice()).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load your games here");
    // The board stays playable with the engine's arrow.
    engine({ ready: true, evalState: ev(fenOf(["d4"]), { bestMoveUci: "d7d5" }) });
    expect(arrows()).toEqual([{ startSquare: "d7", endSquare: "d5", color: ARROWS.engine }]);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await settle();
    expect(notice()).toHaveTextContent("Off-book — your repertoire plays e4 here");
    expect(await drop("d7", "d5")).toBe(true);
  });

  it("a failed board read again on return says Loading…, not the old failure", async () => {
    failNext = ["d4"];
    await open("?colour=white&time_class=focus");
    await drop("d2", "d4");
    expect(screen.getByRole("alert")).toBeInTheDocument();
    fireEvent.click(backBtn());
    await settle();
    holdNext = ["d4"];
    fireEvent.click(forwardBtn());
    await settle();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    await act(async () => held.get("d4")!.resolve());
    await settle();
    expect(notice()).toHaveTextContent("Off-book — your repertoire plays e4 here");
  });

  it("a failed read leaves the board playable", async () => {
    failNext = [""];
    await open("?colour=white&time_class=focus");
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(await drop("e2", "e4")).toBe(true);
    expect(movesInUrl()).toBe("e4");
  });
});

describe("Opening Explorer: entries, lines and Ask Opus", () => {
  it("an entry pushes to exactly its board and browser Back returns", async () => {
    mount(["/cards"]);
    await settle();
    fireEvent.click(screen.getByRole("link", { name: "Open in Opening Explorer" }));
    await settle();
    expect(where()).toBe("/explorer?colour=white&moves=e4,e5&time_class=focus");
    expect(positionRequests()).toEqual(["e4,e5"]);
    await playRow("Nf3");
    fireEvent.click(screen.getByRole("button", { name: "browser back" }));
    await settle();
    expect(where()).toBe("/cards");
  });

  it("opens a one-line board's line directly, and a picked line at its occurrence", async () => {
    linesBody = { total: 1, rows: [{ line_id: 7, line_name: "Main", book: "White book", chapter: "Open", line_ply: 0 }], offset: 0, page_size: 50 };
    await open("?colour=white&time_class=focus");
    fireEvent.click(within(screen.getByTestId("lines-picker")).getByRole("button", { name: "Read the line" }));
    await settle();
    expect(screen.getByTestId("walk")).toHaveTextContent("7:0");
    override = (colour, moves) => ({ ...answerFor(colour, moves), lines_through: 120 });
    linesBody = { total: 120, rows: [{ line_id: 3, line_name: "A", book: "W", chapter: "C", line_ply: 4 }, { line_id: 5, line_name: "B", book: "W", chapter: "C", line_ply: 2 }], offset: 0, page_size: 50 };
    await playRow("e4");
    expect(screen.queryByTestId("walk")).toBeNull();
    fireEvent.click(within(screen.getByTestId("lines-picker")).getByRole("button", { name: "Pick a line" }));
    await settle();
    expect(screen.getByText("Step further to narrow it.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "W · C · B" }));
    expect(screen.getByTestId("walk")).toHaveTextContent("5:2");
    expect(requests.filter((r) => r.startsWith("/explorer/lines")).at(-1)).toBe("/explorer/lines?colour=white&moves=e4&offset=0");
  });

  it("a prescribed move's Read the line opens its representative at its ply", async () => {
    await open("?colour=white&moves=e4,e5&time_class=focus");
    fireEvent.click(within(screen.getByTestId("explorer-repertoire")).getByRole("button", { name: "Read the line" }));
    expect(screen.getByTestId("walk")).toHaveTextContent("7:2");
  });

  for (const colour of ["white", "black"] as const)
    it(`Ask Opus asks about the board alone, as ${colour}`, async () => {
      await open(`?colour=${colour}&moves=e4,e5&time_class=focus`);
      expect(screen.getByRole("button", { name: /Waiting for the engine/ })).toBeDisabled();
      engine({ ready: true, evalState: ev(fenOf(["e4", "e5"]), { bestMoveUci: "g1f3", pvUci: ["g1f3", "b8c6"], evalCp: 25 }) });
      fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "why?" } });
      fireEvent.click(screen.getByRole("button", { name: /Ask Opus/ }));
      expect(askExplore.mock.calls[0]).toEqual([{ seed_fen: fenOf(["e4", "e5"]), orientation: colour, moves: [], engine: { depth: 16, eval_cp: 25, best_move: "g1f3", pv: ["g1f3", "b8c6"] }, game: null }, "why?", null]);
    });

  it("Ask Opus is off on a finished board, which is not searched", async () => {
    await open("?colour=white&moves=e4,e5,Qh5,Nc6,Bc4,Nf6,Qxf7%23&time_class=focus");
    expect(screen.getByText("Finished board.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ask Opus/ })).toBeNull();
    expect(analyze).not.toHaveBeenCalled();
  });
});

describe("Opening Explorer: from a card's overlay", () => {
  const shuffle = ["Nf3", "Nf6", "Ng1", "Ng8"];
  const game = [...Array.from({ length: 15 }, () => shuffle).flat(), "Nf3", "Nf6"];
  const card = (ply: number, fen = fenOf(game.slice(0, ply))): PositionCardData => ({ fen, color: "black", times: 2, moves: game, ply, games: [] });
  function Cards({ item }: { item: PositionCardData }) {
    return <Overlay items={[item]} initialIndex={0} onClose={() => {}} />;
  }
  async function openCard(item: PositionCardData) {
    render(
      <MemoryRouter initialEntries={["/cards"]}>
        <Probe />
        <Routes>
          <Route path="/explorer" element={<OpeningExplorer />} />
          <Route path="/cards" element={<Cards item={item} />} />
        </Routes>
      </MemoryRouter>,
    );
    await settle();
  }

  it("at ply 60 opens exactly that board: the landing reads it, and browser Back returns to the card", async () => {
    await openCard(card(60));
    fireEvent.click(screen.getByRole("link", { name: "Open in Opening Explorer" }));
    await settle();
    expect(positionRequests()).toEqual([game.slice(0, 60).join(",")]);
    expect(screen.getByTestId("board").getAttribute("data-position")).toBe(fenOf(game.slice(0, 60)));
    expect(screen.getByTestId("board").getAttribute("data-orientation")).toBe("black");
    fireEvent.click(screen.getByRole("button", { name: "browser back" }));
    await settle();
    expect(where()).toBe("/cards");
  });

  it("at ply 61 is disabled with its reason and goes nowhere", async () => {
    await openCard(card(61));
    const entry = screen.getByTestId("explorer-entry");
    expect(entry.tagName).toBe("BUTTON");
    expect(entry).toBeDisabled();
    expect(screen.getByText("The Opening Explorer covers the first 30 moves.")).toBeInTheDocument();
    fireEvent.click(entry);
    await settle();
    expect(where()).toBe("/cards");
    expect(positionRequests()).toEqual([]);
  });

  it("is not offered on a card whose path does not reach its board", async () => {
    await openCard(card(2, fenOf(["e4", "e5"])));
    expect(screen.queryByTestId("explorer-entry")).toBeNull();
  });
});
