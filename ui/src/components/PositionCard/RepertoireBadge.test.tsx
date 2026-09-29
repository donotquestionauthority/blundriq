import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LineReaderLine, RepertoireCoverage } from "../../repertoire";
import { RepertoireBadge } from "./RepertoireBadge";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string } }) => <div data-testid={`board-${options.id}`} data-position={options.position} />,
}));

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
const AFTER_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";
const AFTER_NF3 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2";

const coverage = (over: Partial<RepertoireCoverage> = {}): RepertoireCoverage => ({ status: "match", transposed: false, book_move: "Nf3", played_is_book: false, book: "Italian", chapter: "Giuoco", line_name: "Main", line_id: 1, line_ply: 2, more_lines: 0, ...over });
const line = (): LineReaderLine => ({
  line_id: 1,
  line_name: "Main",
  color: "white",
  book_title: "Italian",
  chapter_title: "Giuoco",
  positions: [
    { ply: 0, fen: START, move: "e4", annotation: null },
    { ply: 1, fen: AFTER_E4, move: "e5", annotation: null },
    { ply: 2, fen: AFTER_E5, move: "Nf3", annotation: null },
    { ply: 3, fen: AFTER_NF3, move: null, annotation: null },
  ],
});

type Reply = { status: number; body: unknown };
type Call = { path: string; query: URLSearchParams; signal: AbortSignal | undefined };
function stubFetch(routes: Record<string, (query: URLSearchParams) => Reply | Promise<Reply>>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [path, rawQuery] = url.replace(/^.*\/api/, "").split("?");
      const query = new URLSearchParams(rawQuery ?? "");
      calls.push({ path, query, signal: init?.signal ?? undefined });
      const h = routes[path];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(query);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("RepertoireBadge", () => {
  it("asks about the board and the move, and says what the book plays instead", async () => {
    const calls = stubFetch({ "/repertoire/coverage": () => ({ status: 200, body: coverage() }) });
    render(<RepertoireBadge fen={AFTER_E5} move="Nc3" />);
    const badge = await screen.findByTestId("repertoire-coverage");
    expect(badge).toHaveTextContent("In your repertoire · Italian › Giuoco › Main — your repertoire plays Nf3 here");
    expect(badge).not.toHaveTextContent("transposition");
    expect(calls[0].query.get("fen")).toBe(AFTER_E5);
    expect(calls[0].query.get("move")).toBe("Nc3");
    expect(screen.getByRole("button", { name: "📖 Read the whole line" })).toBeInTheDocument();
  });

  it("wordings: the book move played, a transposition, the end of a line, disagreeing lines, more lines", async () => {
    let served = coverage({ played_is_book: true, transposed: true, more_lines: 2 });
    stubFetch({ "/repertoire/coverage": () => ({ status: 200, body: served }) });
    const { unmount } = render(<RepertoireBadge fen={AFTER_E5} move="Nf3" />);
    expect(await screen.findByTestId("repertoire-coverage")).toHaveTextContent("In your repertoire (by transposition) · Italian › Giuoco › Main (+2 more) — you played the book move");
    unmount();

    served = coverage({ status: "end_of_line", book_move: null, played_is_book: null });
    render(<RepertoireBadge fen={AFTER_E5} move="Nf3" />);
    expect(await screen.findByTestId("repertoire-coverage")).toHaveTextContent("— the line ends here");
  });

  it("names the book move even with no move to judge, and the disagreement when the lines conflict", async () => {
    let served = coverage({ played_is_book: null });
    stubFetch({ "/repertoire/coverage": () => ({ status: 200, body: served }) });
    const first = render(<RepertoireBadge fen={AFTER_E5} move={null} />);
    expect(await screen.findByTestId("repertoire-coverage")).toHaveTextContent("— your repertoire plays Nf3 here");
    first.unmount();

    served = coverage({ status: "conflict", book_move: null, played_is_book: null, transposed: null, more_lines: 1 });
    render(<RepertoireBadge fen={AFTER_E5} move="Nc3" />);
    expect(await screen.findByTestId("repertoire-coverage")).toHaveTextContent("In your repertoire · Italian › Giuoco › Main (+1 more) — your lines disagree here");
    expect(screen.getByRole("button", { name: "📖 Read the whole line" })).toBeInTheDocument();
  });

  it("says quietly when the board is off book, and nothing at all when the request fails", async () => {
    stubFetch({ "/repertoire/coverage": () => ({ status: 200, body: coverage({ status: "none", book_move: null, played_is_book: null, book: null, chapter: null, line_name: null, line_id: null, line_ply: null, transposed: null }) }) });
    const first = render(<RepertoireBadge fen={AFTER_E5} move="Nc3" />);
    expect(await screen.findByText("Not in your repertoire")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Read the whole line/ })).toBeNull();
    first.unmount();

    const calls = stubFetch({});
    render(<RepertoireBadge fen={AFTER_E5} move="Nc3" />);
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => {});
    expect(screen.queryByTestId("repertoire-coverage")).toBeNull();
    expect(screen.queryByText("Not in your repertoire")).toBeNull();
  });

  it("aborts the old request when the board changes and never shows its late answer", async () => {
    const pending: Array<(r: Reply) => void> = [];
    const calls = stubFetch({ "/repertoire/coverage": () => new Promise<Reply>((resolve) => pending.push(resolve)) });
    const { rerender } = render(<RepertoireBadge fen={AFTER_E5} move="Nc3" />);
    await waitFor(() => expect(calls).toHaveLength(1));
    rerender(<RepertoireBadge fen={AFTER_NF3} move={null} />);
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0].signal?.aborted).toBe(true);
    expect(calls[1].signal?.aborted).toBe(false);
    // The first answer arrives late: a match for the old board.
    await act(async () => pending[0]({ status: 200, body: coverage() }));
    expect(screen.queryByTestId("repertoire-coverage")).toBeNull();
    await act(async () => pending[1]({ status: 200, body: coverage({ status: "none", book: null, line_id: null, book_move: null }) }));
    expect(await screen.findByText("Not in your repertoire")).toBeInTheDocument();
  });

  it("opens the walk-through on the card's board, clamped to the line", async () => {
    let served = coverage({ line_ply: 2 });
    stubFetch({
      "/repertoire/coverage": () => ({ status: 200, body: served }),
      "/repertoire/lines/1/annotated": () => ({ status: 200, body: line() }),
      "/repertoire/annotation": () => ({ status: 404, body: { detail: "No note for this position" } }),
    });
    const first = render(<RepertoireBadge fen={AFTER_E5} move="Nc3" />);
    fireEvent.click(await screen.findByRole("button", { name: "📖 Read the whole line" }));
    expect(await screen.findByTestId("walkthrough-position")).toHaveTextContent("1…e5 · move 1 of 2");
    expect(screen.getByTestId(/^board-lw/)).toHaveAttribute("data-position", AFTER_E5);
    fireEvent.click(screen.getByRole("button", { name: "‹ Prev" }));
    expect(screen.getByTestId("walkthrough-position")).toHaveTextContent("1.e4 · move 1 of 2");
    first.unmount();

    served = coverage({ line_ply: 40 });
    render(<RepertoireBadge fen={AFTER_E5} move="Nc3" />);
    fireEvent.click(await screen.findByRole("button", { name: "📖 Read the whole line" }));
    expect(await screen.findByTestId("walkthrough-position")).toHaveTextContent("2.Nf3 · move 2 of 2");
    expect(screen.getByTestId(/^board-lw/)).toHaveAttribute("data-position", AFTER_NF3);
  });
});
