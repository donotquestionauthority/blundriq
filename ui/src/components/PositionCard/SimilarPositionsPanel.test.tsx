import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PrepGroup, SimilarNeighbour, SimilarPositionsResponse } from "../../repertoire";
import { ARROWS } from "../../utils/board";
import { Overlay } from "./Overlay";
import { SimilarPositionsPanel } from "./SimilarPositionsPanel";
import type { PositionCardData } from "./types";

// The board renders its id, position and arrows as data so tests can scope arrows to a board.
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id: string; position: string; arrows?: { color: string; startSquare: string; endSquare: string }[] } }) => (
    <div data-testid="board" data-board-id={options.id} data-position={options.position}>
      {(options.arrows ?? []).map((a, i) => (
        <span key={i} data-arrow={a.color} data-from={a.startSquare} data-to={a.endSquare} />
      ))}
    </div>
  ),
}));

const FEN = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";
const OTHER = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";

const group = (over: Partial<PrepGroup> = {}): PrepGroup => ({
  prep_move: "c3",
  prep_status: "move",
  prep_raw_token: null,
  is_queried_move: false,
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
const response = (neighbours: SimilarNeighbour[], move: string | null = null, over: Partial<SimilarPositionsResponse> = {}): SimilarPositionsResponse => ({ query: { fen: FEN, move, max_distance: 4, max_positions: 12 }, truncated: false, positions_omitted: 0, neighbours, ...over });

type Call = { query: URLSearchParams; signal: AbortSignal | undefined };
function stubFetch(handler: (q: URLSearchParams) => Promise<{ status: number; body: unknown }> | { status: number; body: unknown }) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [path, query] = url.replace(/^.*\/api/, "").split("?");
      const q = new URLSearchParams(query ?? "");
      if (path !== "/repertoire/similar") return { ok: true, status: 200, statusText: "OK", json: async () => null };
      calls.push({ query: q, signal: init?.signal ?? undefined });
      const r = await handler(q);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const header = () => screen.getByRole("button", { name: /Similar positions in your repertoire/ });

describe("SimilarPositionsPanel", () => {
  it("issues no request on mount, one on expansion, and none on re-expansion of the same identity", async () => {
    const calls = stubFetch(() => ({ status: 200, body: response([neighbour()]) }));
    render(<SimilarPositionsPanel fen={FEN} queriedMove="c3" orientation="white" />);
    expect(calls).toHaveLength(0);
    expect(header()).not.toHaveTextContent(/\d/);
    fireEvent.click(header());
    await screen.findByText("this position");
    expect(calls).toHaveLength(1);
    expect(calls[0].query.get("fen")).toBe(FEN);
    expect(calls[0].query.get("move")).toBe("c3");
    expect(header()).toHaveTextContent("1 position");
    fireEvent.click(header());
    expect(screen.queryByText("this position")).toBeNull();
    expect(header()).toHaveTextContent("1 position"); // a known count stays on the collapsed header
    fireEvent.click(header());
    await screen.findByText("this position");
    expect(calls).toHaveLength(1);
  });

  it("stepping to another board and back leaves the panel collapsed, with nothing asked for the board in between", async () => {
    const calls = stubFetch(() => ({ status: 200, body: response([neighbour()]) }));
    const { rerender } = render(<SimilarPositionsPanel fen={FEN} orientation="white" />);
    fireEvent.click(header());
    await screen.findByText("this position");
    rerender(<SimilarPositionsPanel fen={OTHER} orientation="white" />);
    expect(header()).toHaveAttribute("aria-expanded", "false");
    rerender(<SimilarPositionsPanel fen={FEN} orientation="white" />);
    expect(header()).toHaveAttribute("aria-expanded", "false");
    expect(header()).toHaveTextContent("1 position");
    expect(calls).toHaveLength(1);
  });

  it("re-expanding after a failure searches afresh: no stale alert, no spinner on the collapsed header", async () => {
    let n = 0;
    const calls = stubFetch(() => {
      n += 1;
      if (n === 1) return { status: 500, body: { detail: "boom" } };
      return new Promise(() => {});
    });
    render(<SimilarPositionsPanel fen={FEN} orientation="white" />);
    fireEvent.click(header());
    await screen.findByRole("alert");
    fireEvent.click(header());
    expect(header().querySelector(".animate-spin")).toBeNull();
    fireEvent.click(header());
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/Searching your repertoire/)).toBeInTheDocument();
  });

  it("a different queried move at the same board is a different request; a new board collapses the panel", async () => {
    const calls = stubFetch((q) => ({ status: 200, body: response([neighbour({ groups: [group({ is_queried_move: q.get("move") === "c3" })] })], q.get("move")) }));
    const { rerender } = render(<SimilarPositionsPanel fen={FEN} queriedMove="c3" orientation="white" />);
    fireEvent.click(header());
    await screen.findByText("your move");
    rerender(<SimilarPositionsPanel fen={FEN} queriedMove="d3" orientation="white" />);
    expect(screen.queryByText("this position")).toBeNull(); // collapsed by the identity change
    fireEvent.click(header());
    await screen.findByText("this position");
    expect(screen.queryByText("your move")).toBeNull();
    expect(calls.map((c) => c.query.get("move"))).toEqual(["c3", "d3"]);
    rerender(<SimilarPositionsPanel fen={OTHER} queriedMove="d3" orientation="white" />);
    expect(screen.queryByText("this position")).toBeNull();
    expect(calls).toHaveLength(2);
  });

  it("an in-flight answer for the old identity is aborted and never shown for the new one", async () => {
    let resolveFirst: (v: { status: number; body: unknown }) => void = () => {};
    let n = 0;
    const calls = stubFetch(() => {
      n += 1;
      if (n === 1) return new Promise((resolve) => (resolveFirst = resolve));
      return { status: 200, body: response([neighbour({ fen: OTHER, distance: 4 })]) };
    });
    const { rerender } = render(<SimilarPositionsPanel fen={FEN} orientation="white" />);
    fireEvent.click(header());
    await waitFor(() => expect(calls).toHaveLength(1));
    rerender(<SimilarPositionsPanel fen={OTHER} orientation="white" />);
    expect(calls[0].signal?.aborted).toBe(true);
    fireEvent.click(header());
    await screen.findByText("4 squares (~2 pieces)");
    await act(async () => resolveFirst({ status: 200, body: response([neighbour()]) }));
    expect(screen.queryByText("this position")).toBeNull();
    expect(header()).toHaveTextContent("1 position");
  });

  it("collapsing aborts a request in flight and unmounting does too", async () => {
    const calls = stubFetch(() => new Promise(() => {}));
    const { unmount } = render(<SimilarPositionsPanel fen={FEN} orientation="white" />);
    fireEvent.click(header());
    await waitFor(() => expect(calls).toHaveLength(1));
    fireEvent.click(header());
    expect(calls[0].signal?.aborted).toBe(true);
    fireEvent.click(header());
    await waitFor(() => expect(calls).toHaveLength(2));
    unmount();
    expect(calls[1].signal?.aborted).toBe(true);
  });

  it("surfaces a failure with Retry, and shows the empty and truncated states", async () => {
    let fail = true;
    stubFetch(() => (fail ? { status: 500, body: { detail: "boom" } } : { status: 200, body: response([], null, { truncated: true, positions_omitted: 3 }) }));
    render(<SimilarPositionsPanel fen={FEN} orientation="white" />);
    fireEvent.click(header());
    await screen.findByRole("alert");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(/No similar positions within 4 squares/);
    expect(screen.queryByText(/beyond the cap/)).toBeNull(); // no rows, so no compare button and no cap note either
  });

  it("draws the neighbour's arrows inside its own board and lists every group verbatim", async () => {
    const n = neighbour({
      fen: OTHER,
      distance: 4,
      diff_squares: [{ square: "c5", from: "b", to: null }],
      castling_delta: ["K"],
      board_prep_divergent: true,
      groups: [group({ prep_move: "d3", line_id: 2, arriving: { san: "Nf6", from: "g8", to: "f6", promotion: null, is_castling: false, is_en_passant: false } }), group({ prep_move: "c3", line_id: 3, carried_by_line_count: 2, is_alternative: true, arriving: { san: "Nf6", from: "g8", to: "f6", promotion: null, is_castling: false, is_en_passant: false } }), group({ prep_status: "end_of_line", prep_move: null, line_id: 4, line_name: "Short", arriving: { san: "Nf6", from: "g8", to: "f6", promotion: null, is_castling: false, is_en_passant: false } })],
    });
    stubFetch(() => ({ status: 200, body: response([n], null, { truncated: true, positions_omitted: 2 }) }));
    render(<SimilarPositionsPanel fen={FEN} orientation="white" />);
    fireEvent.click(header());
    await screen.findByText("4 squares (~2 pieces)");
    expect(screen.getByText("two book moves")).toBeInTheDocument();
    expect(screen.getByText("line ends here")).toBeInTheDocument();
    expect(screen.getByText("×2 lines")).toBeInTheDocument();
    expect(screen.getByText("(alt)")).toBeInTheDocument();
    expect(screen.queryByText("Compare side by side")).toBeNull(); // offered only when a handler is given
    expect(screen.getByText(/2 more positions beyond the cap/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /4 squares/ }));
    const board = within(screen.getByTestId("neighbour-board")).getByTestId("board");
    expect(board.getAttribute("data-position")).toBe(OTHER);
    const arrows = Array.from(board.querySelectorAll("[data-arrow]")).map((a) => [a.getAttribute("data-arrow"), a.getAttribute("data-from"), a.getAttribute("data-to")]);
    expect(arrows).toEqual([
      [ARROWS.opponent, "g8", "f6"],
      [ARROWS.book, "d2", "d3"],
      [ARROWS.book, "c2", "c3"],
    ]);
    expect(screen.getByText(/Castling rights differ: K/)).toBeInTheDocument();
  });

  it("two panels give their boards distinct ids", async () => {
    stubFetch(() => ({ status: 200, body: response([neighbour()]) }));
    render(
      <>
        <SimilarPositionsPanel fen={FEN} orientation="white" />
        <SimilarPositionsPanel fen={FEN} orientation="white" />
      </>,
    );
    for (const h of screen.getAllByRole("button", { name: /Similar positions in your repertoire/ })) fireEvent.click(h);
    await waitFor(() => expect(screen.getAllByText("this position")).toHaveLength(2));
    for (const row of screen.getAllByRole("button", { name: /this position/ })) fireEvent.click(row);
    const ids = screen.getAllByTestId("board").map((b) => b.getAttribute("data-board-id"));
    expect(new Set(ids).size).toBe(2);
  });
});

// --- the compare view inside the Overlay ------------------------------------------------

const card = (fen: string, over: Partial<PositionCardData> = {}): PositionCardData => ({ fen, color: "white", times: 2, movePlayed: "Nxe5", moves: ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], ply: 6, games: [], ...over });

async function openCompare() {
  const calls = stubFetch(() => ({ status: 200, body: response([neighbour({ fen: OTHER, distance: 4 })]) }));
  const onClose = vi.fn();
  const onIndexChange = vi.fn();
  render(<Overlay items={[card(FEN), card(OTHER)]} initialIndex={0} onClose={onClose} onIndexChange={onIndexChange} />);
  fireEvent.click(header());
  await screen.findByText("Compare side by side");
  fireEvent.click(screen.getByText("Compare side by side"));
  await screen.findByTestId("similar-compare-view");
  return { calls, onClose, onIndexChange };
}

const swipe = (from: number, to: number) => {
  fireEvent.touchStart(window, { touches: [{ clientX: from }] });
  fireEvent.touchEnd(window, { changedTouches: [{ clientX: to }] });
};

describe("SimilarCompareView in the Overlay", () => {
  it("opens over the panel's data with no further request; the pinned board carries the card's arrows", async () => {
    const { calls } = await openCompare();
    expect(calls).toHaveLength(1);
    const pinned = within(screen.getByTestId("pinned-board")).getByTestId("board");
    expect(pinned.getAttribute("data-position")).toBe(FEN);
    expect(Array.from(pinned.querySelectorAll("[data-arrow]")).map((a) => a.getAttribute("data-arrow"))).toEqual([ARROWS.opponent, ARROWS.played]);
    const view = screen.getByTestId("similar-compare-view");
    expect(within(view).getAllByTestId("neighbour-board")).toHaveLength(1);
    const ids = screen.getAllByTestId("board").map((b) => b.getAttribute("data-board-id"));
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("Escape closes the compare view and not the card; arrows and swipes do not page the list", async () => {
    const { onClose, onIndexChange } = await openCompare();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    swipe(300, 100);
    expect(onIndexChange).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("similar-compare-view")).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    // Closed: the overlay pages and closes again.
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(onIndexChange).toHaveBeenCalledWith(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("a swipe that started before the view opened cannot page the list when it ends after (the state gate, not consumption)", async () => {
    const calls = stubFetch(() => ({ status: 200, body: response([neighbour({ fen: OTHER, distance: 4 })]) }));
    const onIndexChange = vi.fn();
    render(<Overlay items={[card(FEN), card(OTHER)]} initialIndex={0} onClose={() => {}} onIndexChange={onIndexChange} />);
    fireEvent.click(header());
    await screen.findByText("Compare side by side");
    fireEvent.touchStart(window, { touches: [{ clientX: 300 }] });
    fireEvent.click(screen.getByText("Compare side by side"));
    await screen.findByTestId("similar-compare-view");
    fireEvent.touchEnd(window, { changedTouches: [{ clientX: 100 }] });
    expect(onIndexChange).not.toHaveBeenCalled();
    expect(calls).toHaveLength(1);
  });

  it("stepping to another card closes the view and collapses the panel", async () => {
    await openCompare();
    fireEvent.click(screen.getByRole("button", { name: "Next position" }));
    expect(screen.queryByTestId("similar-compare-view")).toBeNull();
    expect(screen.queryByText("Compare side by side")).toBeNull();
    expect(header()).toHaveAttribute("aria-expanded", "false");
  });
});

// --- each event layer on its own; the cache, the identity and the queried move ------------------

describe("the two layers, each on its own", () => {
  it("the view consumes Escape, arrows and a qualifying swipe before any bubble listener, and passes a tap through", async () => {
    await openCompare();
    const bubble = vi.fn();
    window.addEventListener("keydown", bubble);
    window.addEventListener("touchend", bubble);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    swipe(300, 100);
    expect(bubble).not.toHaveBeenCalled();
    swipe(300, 290); // a tap: not consumed
    expect(bubble).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(bubble).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("similar-compare-view")).toBeNull();
    window.removeEventListener("keydown", bubble);
    window.removeEventListener("touchend", bubble);
  });

  it("the Overlay registers no listeners of its own while the view is open (the gate, apart from consumption)", async () => {
    stubFetch(() => ({ status: 200, body: response([neighbour({ fen: OTHER, distance: 4 })]) }));
    render(<Overlay items={[card(FEN), card(OTHER)]} initialIndex={0} onClose={() => {}} />);
    fireEvent.click(header());
    await screen.findByText("Compare side by side");
    const spy = vi.spyOn(window, "addEventListener");
    fireEvent.click(screen.getByText("Compare side by side"));
    await screen.findByTestId("similar-compare-view");
    const bubbleAdds = (calls: unknown[][]) => calls.filter(([type, , opts]) => (type === "keydown" || type === "touchstart" || type === "touchend") && opts !== true);
    expect(bubbleAdds(spy.mock.calls)).toHaveLength(0); // only the view's capture-phase listeners were added
    spy.mockClear();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(bubbleAdds(spy.mock.calls)).toHaveLength(3); // closed: the Overlay attaches its three again
    spy.mockRestore();
  });
});

describe("panel identity and cache", () => {
  it("A → B → A serves the cached answer without a request; the compare view closes when the identity changes", async () => {
    const calls = stubFetch((q) => ({ status: 200, body: response([neighbour({ fen: q.get("fen") === FEN ? OTHER : FEN, distance: 4 })]) }));
    const onCompare = vi.fn();
    const { rerender } = render(<SimilarPositionsPanel fen={FEN} orientation="white" onCompareOpenChange={onCompare} />);
    fireEvent.click(header());
    await screen.findByText("4 squares (~2 pieces)");
    rerender(<SimilarPositionsPanel fen={OTHER} orientation="white" onCompareOpenChange={onCompare} />);
    expect(onCompare).toHaveBeenLastCalledWith(false);
    fireEvent.click(header());
    await screen.findByText("4 squares (~2 pieces)");
    rerender(<SimilarPositionsPanel fen={FEN} orientation="white" onCompareOpenChange={onCompare} />);
    fireEvent.click(header());
    await screen.findByText("4 squares (~2 pieces)");
    expect(calls.map((c) => c.query.get("fen"))).toEqual([FEN, OTHER]);
  });

  it("the list shrinking under an open view closes it (no step happens; the panel's identity change does)", async () => {
    stubFetch(() => ({ status: 200, body: response([neighbour({ fen: OTHER, distance: 4 })]) }));
    const { rerender } = render(<Overlay items={[card(FEN), card(OTHER)]} initialIndex={1} onClose={() => {}} />);
    fireEvent.click(header());
    await screen.findByText("Compare side by side");
    fireEvent.click(screen.getByText("Compare side by side"));
    await screen.findByTestId("similar-compare-view");
    rerender(<Overlay items={[card(FEN)]} initialIndex={1} onClose={() => {}} />);
    expect(screen.queryByTestId("similar-compare-view")).toBeNull();
  });

  it("a deviation card asks about its most common played move", async () => {
    const calls = stubFetch(() => ({ status: 200, body: response([]) }));
    render(<Overlay items={[card(FEN, { movePlayed: null, mostCommonPlayed: "d3" })]} initialIndex={0} onClose={() => {}} />);
    fireEvent.click(header());
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].query.get("move")).toBe("d3");
  });
});
