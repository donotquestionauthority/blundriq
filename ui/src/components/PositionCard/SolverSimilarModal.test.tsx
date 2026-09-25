import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PrepGroup, SimilarNeighbour, SimilarPositionsResponse } from "../../repertoire";
import { ARROWS } from "../../utils/board";
import { PuzzleEngine } from "../PuzzleEngine";
import { SolverSimilarModal } from "./SolverSimilarModal";

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
const AFTER_C3_NF6 = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2P2N2/PP1P1PPP/RNBQK2R w KQkq - 1 5";

const group = (over: Partial<PrepGroup> = {}): PrepGroup => ({
  prep_move: "c3",
  prep_status: "move",
  prep_raw_token: null,
  is_queried_move: true,
  arriving: { san: "Bc5", from: "f8", to: "c5", promotion: null, is_castling: false, is_en_passant: false },
  book_title: "Italian",
  chapter_title: "Giuoco",
  line_name: "Main",
  line_id: 1,
  line_ply: 6,
  is_alternative: false,
  carried_by_line_count: 1,
  ...over,
});
const neighbour = (over: Partial<SimilarNeighbour> = {}): SimilarNeighbour => ({ fen: FEN, distance: 0, same_material: true, castling_delta: [], is_castle_shape: false, diff_squares: [], board_prep_divergent: false, groups: [group()], ...over });
const response = (neighbours: SimilarNeighbour[], move: string | null = "c3", over: Partial<SimilarPositionsResponse> = {}): SimilarPositionsResponse => ({ query: { fen: FEN, move, max_distance: 6, max_positions: 12 }, truncated: false, positions_omitted: 0, neighbours, ...over });

type Call = { query: URLSearchParams; signal: AbortSignal | undefined };
function stubFetch(handler: () => Promise<{ status: number; body: unknown }> | { status: number; body: unknown }) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [path, query] = url.replace(/^.*\/api/, "").split("?");
      if (path !== "/repertoire/similar") return { ok: true, status: 200, statusText: "OK", json: async () => null };
      calls.push({ query: new URLSearchParams(query ?? ""), signal: init?.signal ?? undefined });
      const r = await handler();
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const launch = () => screen.getByTestId("similar-launch");
const view = () => screen.queryByTestId("similar-compare-view");

describe("the Similar-positions launcher in PuzzleEngine", () => {
  it("has a target at the first decision of a player-first puzzle where Compare has none, and asks about that board and move", async () => {
    const calls = stubFetch(() => ({ status: 200, body: response([neighbour()]) }));
    render(<PuzzleEngine fen={FEN} solutionLine={["c3"]} color="w" />);
    expect(screen.getByTestId("branch-compare-launch")).toBeDisabled(); // no parent
    expect(launch()).toBeEnabled();
    fireEvent.click(launch());
    await screen.findByTestId("similar-compare-view");
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].query.get("fen")).toBe(FEN);
    expect(calls[0].query.get("move")).toBe("c3");
    await screen.findByText("1 similar position");
    // The pinned board is the target board with the line's move in the book's colour.
    const pinned = within(screen.getByTestId("pinned-board")).getByTestId("board");
    expect(pinned).toHaveAttribute("data-position", FEN);
    const arrow = pinned.querySelector("[data-arrow]");
    expect(arrow).toHaveAttribute("data-arrow", ARROWS.book);
    expect(arrow).toHaveAttribute("data-from", "c2");
    expect(arrow).toHaveAttribute("data-to", "c3");
  });

  it("an opponent-first puzzle has no target until the reply has auto-played; a solved one-move puzzle keeps its target", async () => {
    stubFetch(() => ({ status: 200, body: response([]) }));
    render(<PuzzleEngine fen={PRE} solutionLine={["Bc5", "c3"]} color="w" />);
    expect(launch()).toBeDisabled();
    await waitFor(() => expect(launch()).toBeEnabled());
    fireEvent.click(launch());
    await screen.findByText(/No similar positions within 6 squares/);
  });

  it("is closed by a puzzle change and is disabled in map mode", async () => {
    stubFetch(() => ({ status: 200, body: response([]) }));
    const { rerender } = render(<PuzzleEngine fen={FEN} solutionLine={["c3", "Nf6", "d4"]} color="w" />);
    fireEvent.click(launch());
    await screen.findByTestId("similar-compare-view");
    rerender(<PuzzleEngine fen={PRE} solutionLine={["Bc5", "c3"]} color="w" />);
    expect(view()).toBeNull();
    rerender(<PuzzleEngine fen={FEN} solutionLine={["c3"]} color="w" acceptanceMap={{ v: 1, n: 1, p: {}, d: {} }} />);
    expect(launch()).toBeDisabled();
  });

  it("Escape closes the modal and is consumed before the page's bubble listeners; the launcher's Retry refetches after a failure", async () => {
    let fail = true;
    const calls = stubFetch(() => (fail ? { status: 500, body: { detail: "boom" } } : { status: 200, body: response([neighbour()]) }));
    const seen = vi.fn();
    document.addEventListener("keydown", seen);
    try {
      render(<PuzzleEngine fen={FEN} solutionLine={["c3"]} color="w" />);
      fireEvent.click(launch());
      await screen.findByRole("alert");
      fail = false;
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      await screen.findByText("1 similar position");
      expect(calls).toHaveLength(2);
      fireEvent.keyDown(document.body, { key: "Escape" });
      expect(view()).toBeNull();
      expect(seen).not.toHaveBeenCalled();
      fireEvent.keyDown(document.body, { key: "Escape" });
      expect(seen).toHaveBeenCalledTimes(1); // with the modal closed the page hears it again
    } finally {
      document.removeEventListener("keydown", seen);
    }
  });
});

describe("SolverSimilarModal on its own", () => {
  it("a target change while open aborts the outstanding request and a late old answer never lands", async () => {
    let resolveFirst: (v: { status: number; body: unknown }) => void = () => {};
    let n = 0;
    const calls = stubFetch(() => {
      n += 1;
      if (n === 1) return new Promise((resolve) => (resolveFirst = resolve));
      return { status: 200, body: response([neighbour(), neighbour({ fen: PRE })], "d4") };
    });
    const { rerender } = render(<SolverSimilarModal fen={FEN} move="c3" orientation="white" onClose={() => {}} />);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(screen.getByText(/Searching your repertoire/)).toBeInTheDocument();
    rerender(<SolverSimilarModal fen={AFTER_C3_NF6} move="d4" orientation="white" onClose={() => {}} />);
    expect(calls[0].signal?.aborted).toBe(true);
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].query.get("fen")).toBe(AFTER_C3_NF6);
    expect(calls[1].query.get("move")).toBe("d4");
    await screen.findByText("2 similar positions");
    await act(async () => {
      resolveFirst({ status: 200, body: response([neighbour()]) });
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.getByText("2 similar positions")).toBeInTheDocument();
  });

  it("unmounting aborts the request in flight", async () => {
    const calls = stubFetch(() => new Promise(() => {}));
    const { unmount } = render(<SolverSimilarModal fen={FEN} move="c3" orientation="white" onClose={() => {}} />);
    await waitFor(() => expect(calls).toHaveLength(1));
    unmount();
    expect(calls[0].signal?.aborted).toBe(true);
  });
});
