import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BranchCompareResponse, CompareBranch, PrepGroup } from "../repertoire";
import { ARROWS } from "../utils/board";
import { BranchCompareView } from "./BranchCompareView";
import { PuzzleEngine } from "./PuzzleEngine";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id: string; position: string; arrows?: { color: string; startSquare: string; endSquare: string }[] } }) => (
    <div data-testid="board" data-board-id={options.id} data-position={options.position}>
      {(options.arrows ?? []).map((a, i) => (
        <span key={i} data-arrow={a.color} data-from={a.startSquare} data-to={a.endSquare} />
      ))}
    </div>
  ),
}));

const PRE = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3";
const FEN = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";
const NF6 = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";
const H6 = "r1bqkbnr/pppp1pp1/2n4p/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4";

const group = (over: Partial<PrepGroup> = {}): PrepGroup => ({ prep_move: "c3", prep_status: "move", prep_raw_token: null, is_queried_move: false, arriving: { san: "Bc5", from: "f8", to: "c5", promotion: null, is_castling: false, is_en_passant: false }, book_title: "Italian", chapter_title: "Giuoco", line_name: "Main", line_id: 1, line_ply: 6, is_alternative: false, carried_by_line_count: 1, ...over });
const mv = (san: string, from: string, to: string) => ({ san, from, to, promotion: null, is_castling: false, is_en_passant: false });
const repBranch = (fen: string, san: string, from: string, to: string, reply: string | null, replySq: { from: string; to: string } | null, over: Partial<NonNullable<CompareBranch["sources"]["repertoire"]>> = {}): CompareBranch => ({
  child_fen: fen,
  opponent_move: mv(san, from, to),
  sources: { repertoire: { reply_san: reply, reply_squares: replySq, end_of_line: false, line_count: 1, board_prep_divergent: false, groups: [group({ prep_move: reply })], ...over }, blunders: null, scout: null },
});
const bluBranch = (best: boolean): CompareBranch => ({
  child_fen: H6,
  opponent_move: mv("h6", "h7", "h6"),
  sources: { repertoire: null, blunders: { games: 2, worst: { move_played_san: "Nxe5", move_played_squares: { from: "f3", to: "e5" }, best_move_san: best ? "d3" : null, best_move_squares: best ? { from: "d2", to: "d3" } : null, centipawn_loss: 400 } }, scout: null },
});
const response = (branches: CompareBranch[], over: Partial<BranchCompareResponse> = {}): BranchCompareResponse => ({ query: { fen: FEN, pre_fen: PRE, book_color: "white", max_boards: 12 }, current: repBranch(FEN, "Bc5", "f8", "c5", "c3", { from: "c2", to: "c3" }), truncated: false, boards_omitted: 0, branches, ...over });

type Call = { query: URLSearchParams; signal: AbortSignal | undefined };
function stubFetch(handler: () => Promise<{ status: number; body: unknown }> | { status: number; body: unknown }) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [path, query] = url.replace(/^.*\/api/, "").split("?");
      if (path !== "/repertoire/branch-compare") return { ok: true, status: 200, statusText: "OK", json: async () => null };
      calls.push({ query: new URLSearchParams(query ?? ""), signal: init?.signal ?? undefined });
      const r = await handler();
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const arrowsOf = (wrapper: HTMLElement) => Array.from(within(wrapper).getByTestId("board").querySelectorAll("[data-arrow]")).map((a) => a.getAttribute("data-arrow"));

describe("BranchCompareView", () => {
  it("fetches the pair, pins current once, and the grid owns the alternatives with the contract's arrows", async () => {
    const calls = stubFetch(() => ({ status: 200, body: response([repBranch(NF6, "Nf6", "g8", "f6", "d3", { from: "d2", to: "d3" }), bluBranch(true), { ...bluBranch(false), child_fen: H6 + "x" }], { truncated: true, boards_omitted: 1 }) }));
    render(<BranchCompareView fen={FEN} preFen={PRE} orientation="white" onClose={() => {}} />);
    expect(screen.getAllByText(/Computing/).length).toBeGreaterThan(0);
    await screen.findByText("3 alternatives");
    expect(calls).toHaveLength(1);
    expect(calls[0].query.get("fen")).toBe(FEN);
    expect(calls[0].query.get("pre_fen")).toBe(PRE);
    const pinned = screen.getByTestId("branch-current-board");
    expect(within(pinned).getByTestId("board").getAttribute("data-position")).toBe(FEN);
    expect(arrowsOf(pinned)).toEqual([ARROWS.opponent, ARROWS.book]);
    expect(screen.getByText(/your opponent played/)).toHaveTextContent("Bc5");
    const boards = screen.getAllByTestId("branch-board");
    expect(boards).toHaveLength(3);
    expect(arrowsOf(boards[0])).toEqual([ARROWS.opponent, ARROWS.book]);
    expect(arrowsOf(boards[1])).toEqual([ARROWS.opponent, ARROWS.played, ARROWS.engine]);
    expect(arrowsOf(boards[2])).toEqual([ARROWS.opponent, ARROWS.played]); // no best move recorded: nothing invented
    expect(screen.getAllByText(/you've blundered here in 2 games/)).toHaveLength(2);
    expect(screen.getByText(/1 more alternative beyond the cap/)).toBeInTheDocument();
    const ids = screen.getAllByTestId("board").map((b) => b.getAttribute("data-board-id"));
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("a divergent or terminal current branch says so and draws no reply", async () => {
    stubFetch(() => ({ status: 200, body: response([], { current: repBranch(FEN, "Bc5", "f8", "c5", null, null, { board_prep_divergent: true, line_count: 2 }) }) }));
    render(<BranchCompareView fen={FEN} preFen={PRE} orientation="white" onClose={() => {}} />);
    await screen.findByText("0 alternatives");
    expect(screen.getByText("conflicting prep")).toBeInTheDocument();
    expect(screen.getByText(/In your repertoire/)).toHaveTextContent("×2 lines");
    expect(arrowsOf(screen.getByTestId("branch-current-board"))).toEqual([ARROWS.opponent]);
    expect(screen.getByText(/No alternatives from the previous position/)).toBeInTheDocument();
    expect(screen.queryAllByTestId("branch-board")).toHaveLength(0);
  });

  it("a failure is surfaced with Retry, and Retry refetches", async () => {
    let fail = true;
    const calls = stubFetch(() => (fail ? { status: 500, body: { detail: "boom" } } : { status: 200, body: response([]) }));
    render(<BranchCompareView fen={FEN} preFen={PRE} orientation="white" onClose={() => {}} />);
    await screen.findByRole("alert");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("0 alternatives");
    expect(calls).toHaveLength(2);
  });

  it("Escape closes and is consumed before a host's bubble listener; unmounting aborts the request", async () => {
    const calls = stubFetch(() => new Promise(() => {}));
    const onClose = vi.fn();
    const host = vi.fn();
    window.addEventListener("keydown", host);
    const { unmount } = render(<BranchCompareView fen={FEN} preFen={PRE} orientation="white" onClose={onClose} />);
    await waitFor(() => expect(calls).toHaveLength(1));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(host).not.toHaveBeenCalled();
    window.removeEventListener("keydown", host);
    unmount();
    expect(calls[0].signal?.aborted).toBe(true);
  });
});

describe("the Compare launcher in PuzzleEngine", () => {
  it("is invisible without a target, opens the view from the latest decision node, and closes on a puzzle change", async () => {
    const calls = stubFetch(() => ({ status: 200, body: response([]) }));
    // The puzzle starts with the opponent's Bc5: the first player node has a parent from the start.
    const { rerender } = render(<PuzzleEngine fen={PRE} solutionLine={["Bc5", "c3"]} color="w" />);
    const launch = screen.getByTestId("branch-compare-launch");
    expect(launch).toBeDisabled(); // before the auto-played reply there is no player node yet
    await waitFor(() => expect(launch).toBeEnabled());
    fireEvent.click(launch);
    await screen.findByTestId("branch-compare-view");
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].query.get("fen")).toBe(FEN);
    expect(calls[0].query.get("pre_fen")).toBe(PRE);
    rerender(<PuzzleEngine fen={FEN} solutionLine={["c3", "Nf6", "d4"]} color="w" />);
    expect(screen.queryByTestId("branch-compare-view")).toBeNull();
    expect(screen.getByTestId("branch-compare-launch")).toBeDisabled(); // starts on the player's move: no parent
  });

  it("is disabled in map mode", () => {
    stubFetch(() => ({ status: 200, body: response([]) }));
    render(<PuzzleEngine fen={PRE} solutionLine={["Bc5", "c3"]} color="w" acceptanceMap={{ v: 1, n: 1, p: {}, d: {} }} />);
    expect(screen.getByTestId("branch-compare-launch")).toBeDisabled();
  });
});

describe("what an independent read said the suite would not catch", () => {
  it("a pair change while mounted shows loading, aborts the old request, and a late old answer never lands", async () => {
    let resolveFirst: (v: { status: number; body: unknown }) => void = () => {};
    let n = 0;
    const calls = stubFetch(() => {
      n += 1;
      if (n === 1) return new Promise((resolve) => (resolveFirst = resolve));
      return { status: 200, body: response([bluBranch(true)]) };
    });
    const { rerender } = render(<BranchCompareView fen={FEN} preFen={PRE} orientation="white" onClose={() => {}} />);
    await waitFor(() => expect(calls).toHaveLength(1));
    rerender(<BranchCompareView fen={NF6} preFen={PRE} orientation="white" onClose={() => {}} />);
    expect(calls[0].signal?.aborted).toBe(true);
    expect(screen.getAllByText(/Computing/).length).toBeGreaterThan(0);
    await screen.findByText("1 alternative");
    resolveFirst({ status: 200, body: response([]) });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText("1 alternative")).toBeInTheDocument();
  });

  it("a new puzzle closes an open view even when the new puzzle has a target of its own", async () => {
    stubFetch(() => ({ status: 200, body: response([]) }));
    const { rerender } = render(<PuzzleEngine fen={PRE} solutionLine={["Bc5", "c3"]} color="w" />);
    await waitFor(() => expect(screen.getByTestId("branch-compare-launch")).toBeEnabled());
    fireEvent.click(screen.getByTestId("branch-compare-launch"));
    await screen.findByTestId("branch-compare-view");
    rerender(<PuzzleEngine fen={"r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"} solutionLine={["Bc4", "Bc5", "c3"]} color="b" />);
    await waitFor(() => expect(screen.getByTestId("branch-compare-launch")).toBeEnabled());
    expect(screen.queryByTestId("branch-compare-view")).toBeNull();
  });

  it("map mode has no target even once the opponent has replied", async () => {
    stubFetch(() => ({ status: 200, body: response([]) }));
    const acceptanceMap = { v: 1, n: 1, p: {}, d: { [PRE.split(" ").slice(0, 4).join(" ")]: "f8c5" } };
    render(<PuzzleEngine fen={PRE} solutionLine={["Bc5", "c3"]} color="w" acceptanceMap={acceptanceMap} />);
    await new Promise((r) => setTimeout(r, 600));
    expect(screen.getByTestId("branch-compare-launch")).toBeDisabled();
  });
});
