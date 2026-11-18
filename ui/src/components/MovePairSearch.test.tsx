import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import Repertoire from "../pages/Repertoire";
import { formatPair, parsePair } from "../movePair";
import type { LineReaderLine, MovePairResponse, MovePairRow } from "../repertoire";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id: string; position: string; arrows?: { color: string; startSquare: string; endSquare: string }[] } }) => (
    <div data-testid="board" data-id={options.id} data-position={options.position} data-arrows={(options.arrows ?? []).map((a) => `${a.startSquare}${a.endSquare}:${a.color}`).join(",")} />
  ),
}));

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const BEFORE_H6 = "rnbq1rk1/ppp1bppp/4pn2/3p2B1/2PP4/2N1P3/PP1Q1PPP/R3KBNR b KQ - 2 6";

const row = (over: Partial<MovePairRow> = {}): MovePairRow => ({
  lineId: 7,
  book: "Black book",
  chapter: "QGD",
  line: "h6 line",
  ply: 10,
  label: "6.Qd2 h6",
  san1: "Qd2",
  san2: "h6",
  piece2: "P",
  square2: "h6",
  move2Uci: "h7h6",
  fenBefore2: BEFORE_H6,
  fenAfter: BEFORE_H6,
  rootFen: START,
  movesToPair: ["d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "O-O", "Qd2", "h6"],
  inPractice: false,
  ...over,
});
const answer = (rows: MovePairRow[], over: Partial<MovePairResponse> = {}): MovePairResponse => ({ rows, replies: null, total: rows.length, truncated: false, ...over });

type Reply = { status: number; body: unknown };
type Handler = (query: URLSearchParams) => Reply | Promise<Reply>;
function stubFetch(routes: Record<string, Handler>, { honourAbort = true } = {}) {
  const calls: Array<{ path: string; query: URLSearchParams }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [rawPath, rawQuery] = url.replace(/^.*\/api/, "").split("?");
      const query = new URLSearchParams(rawQuery ?? "");
      calls.push({ path: rawPath, query });
      const h = routes[rawPath];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(query);
      if (honourAbort && init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}
const page = (pair: Handler, extra: Record<string, Handler> = {}, opts: { honourAbort?: boolean } = {}) =>
  stubFetch(
    {
      "/repertoire": () => ({ status: 200, body: { books: [] } }),
      "/repertoire/conflicts": () => ({ status: 200, body: { contested: 0, positions: [], duplicates: [] } }),
      "/repertoire/move-pair": pair,
      ...extra,
    },
    opts,
  );
function mount(entry = "/repertoire") {
  const router = createMemoryRouter([{ path: "/repertoire", element: <Repertoire /> }], { initialEntries: [entry] });
  render(<RouterProvider router={router} />);
  return router;
}
const pairCalls = (calls: Array<{ path: string; query: URLSearchParams }>) => calls.filter((c) => c.path === "/repertoire/move-pair").map((c) => c.query.toString());

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("movePair helpers", () => {
  it("round-trips a search and refuses anything else", () => {
    const q = { colour: "black" as const, first: "opponent" as const, p1: "Q" as const, s1: "d2", move2: null };
    expect(formatPair(q)).toBe("black.opponent.Qd2.any");
    expect(parsePair("black.opponent.Qd2.any")).toEqual(q);
    expect(parsePair("white.me.Pe4.Nf3")).toEqual({ colour: "white", first: "me", p1: "P", s1: "e4", move2: { piece: "N", square: "f3" } });
    for (const bad of [null, "", "red.me.Pe4.any", "white.both.Pe4.any", "white.me.Xe4.any", "white.me.Pe9.any", "white.me.Pe4.N", "white.me.Pe4", "white.me.pe4.any"]) expect(parsePair(bad)).toBeNull();
  });
});

describe("Find a move pair", () => {
  it("follows move 1's player, searches on submit only, and shows the rows in their sections", async () => {
    const calls = page(() => ({ status: 200, body: answer([row({ inPractice: true, lineId: 3, line: "Practised", ply: 4, label: "3.Qd2 Nbd7" }), row()]) }));
    const router = mount();
    fireEvent.click(screen.getByRole("button", { name: /Find a move pair/ }));
    expect(screen.getByTestId("move2-player")).toHaveTextContent("Me");
    fireEvent.click(screen.getByRole("radio", { name: "Me" }));
    expect(screen.getByTestId("move2-player")).toHaveTextContent("Opponent");
    fireEvent.click(screen.getByRole("radio", { name: "Opponent" }));
    fireEvent.click(screen.getByRole("radio", { name: "Black" }));
    fireEvent.change(screen.getByLabelText("Move 1 square"), { target: { value: "D2" } });
    expect(pairCalls(calls)).toEqual([]); // typing is not a search
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByTestId("move-pair-results");
    expect(router.state.location.search).toBe("?pair=black.opponent.Qd2.any");
    expect(pairCalls(calls)).toEqual(["colour=black&first=opponent&p1=Q&s1=d2"]);
    const practice = screen.getByRole("region", { name: "In practice" });
    const rest = screen.getByRole("region", { name: "Rest of the repertoire" });
    expect(within(practice).getAllByTestId("move-pair-row").map((r) => r.textContent)).toEqual([expect.stringContaining("3.Qd2 Nbd7")]);
    expect(within(rest).getAllByTestId("move-pair-row")[0]).toHaveTextContent("6.Qd2 h6Black book › QGD › h6 line");
    expect(screen.getByText("2 matches.")).toBeInTheDocument();
  });

  it("refuses a square that is not one, and needs move 2's square once a piece is chosen", () => {
    page(() => ({ status: 200, body: answer([]) }));
    mount();
    fireEvent.click(screen.getByRole("button", { name: /Find a move pair/ }));
    const go = screen.getByRole("button", { name: "Search" });
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Move 1 square"), { target: { value: "i9" } });
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Move 1 square"), { target: { value: "d2" } });
    expect(go).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Move 2 piece"), { target: { value: "B" } });
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Move 2 square"), { target: { value: "b4" } });
    expect(go).toBeEnabled();
  });

  it("says plainly when nothing matches", async () => {
    page(() => ({ status: 200, body: answer([]) }));
    mount("/repertoire?pair=black.opponent.Qe2.Bb4");
    expect(await screen.findByText("No line in your Black repertoire has the opponent's queen to e2 followed by your bishop to b4.")).toBeInTheDocument();
  });

  it("a reply chip fills move 2 and searches; Back and a reload show the search the URL names", async () => {
    const calls = page((q) =>
      q.get("p2")
        ? { status: 200, body: answer([row()]) }
        : { status: 200, body: answer([row(), row({ lineId: 8, san2: "Nbd7", piece2: "N", square2: "d7", label: "6.Qd2 Nbd7", line: "Nbd7 line" })], { replies: [{ piece: "N", square: "d7", sans: ["Nbd7", "Nfd7"], count: 2, inPractice: 1 }, { piece: "P", square: "h6", sans: ["h6"], count: 1, inPractice: 0 }] }) },
    );
    const router = mount("/repertoire?pair=black.opponent.Qd2.any");
    const chips = await screen.findByLabelText("Replies");
    expect(within(chips).getAllByRole("button").map((b) => b.textContent)).toEqual(["Nbd7/Nfd7 · 21 in practice", "h6 · 1"]);
    fireEvent.click(within(chips).getByRole("button", { name: /^h6/ }));
    await waitFor(() => expect(router.state.location.search).toBe("?pair=black.opponent.Qd2.Ph6"));
    await waitFor(() => expect(screen.queryByLabelText("Replies")).toBeNull());
    expect((screen.getByLabelText("Move 2 piece") as HTMLSelectElement).value).toBe("P");
    expect((screen.getByLabelText("Move 2 square") as HTMLInputElement).value).toBe("h6");
    expect(pairCalls(calls)).toEqual(["colour=black&first=opponent&p1=Q&s1=d2", "colour=black&first=opponent&p1=Q&s1=d2&p2=P&s2=h6"]);
    await act(() => router.navigate(-1));
    expect(router.state.location.search).toBe("?pair=black.opponent.Qd2.any");
    await screen.findByLabelText("Replies");
    expect((screen.getByLabelText("Move 2 piece") as HTMLSelectElement).value).toBe("any");
    expect(pairCalls(calls)).toHaveLength(3); // the landing asks again for the URL's search
    await act(() => router.navigate(1));
    await waitFor(() => expect(screen.queryByLabelText("Replies")).toBeNull());
    expect((screen.getByLabelText("Move 2 square") as HTMLInputElement).value).toBe("h6");
  });

  it("drops a response for a search the URL no longer names, even one that arrives after the abort", async () => {
    let release: (r: Reply) => void = () => {};
    page((q) => (q.get("s1") === "d2" ? new Promise<Reply>((res) => (release = res)) : { status: 200, body: answer([row({ label: "6.Qe2 h6", san1: "Qe2" })]) }), {}, { honourAbort: false });
    const router = mount("/repertoire?pair=black.opponent.Qd2.any");
    await act(() => router.navigate("/repertoire?pair=black.opponent.Qe2.any"));
    await screen.findByText(/6\.Qe2 h6/);
    await act(async () => release({ status: 200, body: answer([row({ label: "6.Qd2 h6" })]) }));
    expect(screen.queryByText(/6\.Qd2 h6/)).toBeNull();
    expect(screen.getByText(/6\.Qe2 h6/)).toBeInTheDocument();
  });

  it("Search asks again after a failure, and a failure is not shown for a later visit", async () => {
    let fail = true;
    const calls = page(() => (fail ? { status: 500, body: { detail: "boom" } } : { status: 200, body: answer([row()]) }));
    const router = mount("/repertoire?pair=black.opponent.Qd2.any");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not search the repertoire.");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText(/6\.Qd2 h6/);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(pairCalls(calls)).toHaveLength(2);
    // Fail once more, leave, come back: the old failure is not shown while the new request runs.
    fail = true;
    await act(() => router.navigate("/repertoire?pair=black.opponent.Qe2.any"));
    await screen.findByRole("alert");
    let hold: (r: Reply) => void = () => {};
    page(() => new Promise<Reply>((res) => (hold = res)));
    await act(() => router.navigate("/repertoire?pair=black.opponent.Qd2.any"));
    await act(() => router.navigate("/repertoire?pair=black.opponent.Qe2.any"));
    expect(screen.getByText("Searching…")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => hold({ status: 200, body: answer([]) }));
  });

  it("Search with unchanged inputs asks again and shows the new answer, not the old one", async () => {
    let rows = [row()];
    let hold: ((r: Reply) => void) | null = null;
    const calls = page(() => (hold === null ? { status: 200, body: answer(rows) } : new Promise<Reply>((res) => (hold = res))));
    mount("/repertoire?pair=black.opponent.Qd2.any");
    await screen.findByText(/6\.Qd2 h6/);
    rows = [];
    hold = () => {};
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(pairCalls(calls)).toHaveLength(2));
    expect(screen.getByText("Searching…")).toBeInTheDocument(); // the earlier answer is not shown as this one
    expect(screen.queryByText(/6\.Qd2 h6/)).toBeNull();
    await act(async () => hold?.({ status: 200, body: answer(rows) }));
    expect(await screen.findByText(/No line in your Black repertoire/)).toBeInTheDocument();
  });

  it("a book switched off on the page asks the search on show again", async () => {
    let active = true;
    const calls = page(() => ({ status: 200, body: answer(active ? [row()] : []) }), {
      "/repertoire": () => ({ status: 200, body: { books: [{ book_id: 1, title: "Black book", color: "black", active: true, source_url: null, source_author: null, source_title: null, total_lines: 1, active_lines: 1 }] } }),
      "/repertoire/books/1": () => {
        active = false;
        return { status: 200, body: { active: false, held_back: [], rematched: 0 } };
      },
    });
    mount("/repertoire?pair=black.opponent.Qd2.any");
    await screen.findByText(/6\.Qd2 h6/);
    fireEvent.click(await screen.findByRole("switch", { name: "Black book active" }));
    // No Search pressed: the toggle's success alone re-asks.
    expect(await screen.findByText(/No line in your Black repertoire/)).toBeInTheDocument();
    expect(screen.queryByText(/6\.Qd2 h6/)).toBeNull();
    expect(pairCalls(calls)).toHaveLength(2);
  });

  it("shows nothing of the previous search while the next one is in flight", async () => {
    page((q) => (q.get("s1") === "e2" ? new Promise<Reply>(() => {}) : { status: 200, body: answer([row()]) }));
    const router = mount("/repertoire?pair=black.opponent.Qd2.any");
    await screen.findByText(/6\.Qd2 h6/);
    await act(() => router.navigate("/repertoire?pair=black.opponent.Qe2.any"));
    expect(screen.getByText("Searching…")).toBeInTheDocument();
    expect(screen.queryByText(/6\.Qd2 h6/)).toBeNull();
  });

  it("shows the board before move 2 with the book's reply as an arrow, and the moves to it", async () => {
    page(() => ({ status: 200, body: answer([row()]) }));
    mount("/repertoire?pair=black.opponent.Qd2.any");
    fireEvent.click(await screen.findByRole("button", { name: "Board" }));
    const board = within(screen.getByTestId("move-pair-board")).getByTestId("board");
    expect(board).toHaveAttribute("data-position", BEFORE_H6);
    expect(board).toHaveAttribute("data-arrows", "h7h6:#7c3aed");
    expect(screen.getByTestId("move-pair-moves")).toHaveTextContent(/^1\. d4 d5 2\. c4 e6 .* 6\. Qd2 h6$/);
  });

  it("pages the rows fifty at a time and says when the answer was capped", async () => {
    const many = Array.from({ length: 120 }, (_, i) => row({ lineId: i + 1, line: `L${i}` }));
    page(() => ({ status: 200, body: answer(many, { total: 400, truncated: true }) }));
    mount("/repertoire?pair=black.opponent.Qd2.any");
    await screen.findByTestId("move-pair-results");
    expect(screen.getAllByTestId("move-pair-row")).toHaveLength(50);
    expect(screen.getByText("120 of 400 matches loaded — narrow the search to see the rest.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getAllByTestId("move-pair-row")).toHaveLength(120);
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  });

  it("Walk through opens the line at the board after move 1", async () => {
    const positions = Array.from({ length: 13 }, (_, ply) => ({ ply, fen: `fen-${ply}`, move: ply < 12 ? "x" : null, annotation: null }));
    const annotated: LineReaderLine = { line_id: 7, line_name: "h6 line", color: "black", book_title: "Black book", chapter_title: "QGD", positions };
    const calls = page(() => ({ status: 200, body: answer([row()]) }), { "/repertoire/lines/7/annotated": () => ({ status: 200, body: annotated }) });
    mount("/repertoire?pair=black.opponent.Qd2.any");
    fireEvent.click(await screen.findByRole("button", { name: "Walk through" }));
    const dialog = await screen.findByRole("dialog", { name: "Line" });
    await waitFor(() => expect(within(dialog).getByTestId("board")).toHaveAttribute("data-position", "fen-11"));
    expect(calls.some((c) => c.path === "/repertoire/lines/7/annotated")).toBe(true);
  });
});
