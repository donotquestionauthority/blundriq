import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import Repertoire from "./Repertoire";
import { provenanceLabel } from "../repertoire";
import type { LineReaderLine, RepertoireBook, RepertoireSection } from "../repertoire";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string; squareStyles?: Record<string, unknown> } }) => <div data-testid={`board-${options.id}`} data-position={options.position} data-highlights={Object.keys(options.squareStyles ?? {}).join(",")} />,
}));

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
const AFTER_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";
const AFTER_NF3 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2";

const book = (over: Partial<RepertoireBook> = {}): RepertoireBook => ({ book_id: 1, title: "Course: The Italian", color: "white", active: true, source_url: null, source_author: null, source_title: null, total_lines: 2, active_lines: 2, ...over });
const sections = (): RepertoireSection[] => [
  { chapter_id: 1, title: "1) Giuoco", active: true, root_fen: null, lines: [{ id: 1, name: "Main", moves: ["e4", "e5", "Nf3"], active: true, is_alternative: false }] },
  { chapter_id: 2, title: "2) Sidelines", active: true, root_fen: AFTER_E5, lines: [{ id: 2, name: "Alt", moves: ["Bc4"], active: false, is_alternative: true }] },
];
const line = (): LineReaderLine => ({
  line_id: 1,
  line_name: "Main",
  color: "white",
  book_title: "Course: The Italian",
  chapter_title: "1) Giuoco",
  positions: [
    { ply: 0, fen: START, move: "e4", annotation: { text: "Start with @@SANStart@@e4@@SANEnd@@.", source: "course", author: "GM Example", book_title: "Course: The Italian", from_chapter: null } },
    { ply: 1, fen: AFTER_E4, move: "e5", annotation: null },
    { ply: 2, fen: AFTER_E5, move: "Nf3", annotation: { text: "Now Nf3.", source: "manual", author: null, book_title: null, from_chapter: null } },
    { ply: 3, fen: AFTER_NF3, move: null, annotation: null },
  ],
});

type Reply = { status: number; body: unknown };
function stubFetch(routes: Record<string, (method: string, body: Record<string, unknown> | null, query: URLSearchParams) => Reply | Promise<Reply>>) {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | null; query: URLSearchParams }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [rawPath, rawQuery] = url.replace(/^.*\/api/, "").split("?");
      const call = { path: rawPath, method: init?.method ?? "GET", body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null, query: new URLSearchParams(rawQuery ?? "") };
      calls.push(call);
      const h = routes[rawPath];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(call.method, call.body, call.query);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

const renderPage = () =>
  render(
    <MemoryRouter>
      <Repertoire />
    </MemoryRouter>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Repertoire page", () => {
  it("labels provenance from whatever the book carries", () => {
    expect(provenanceLabel(book())).toBeNull();
    expect(provenanceLabel(book({ source_title: "T", source_author: "A" }))).toBe("T by A");
    expect(provenanceLabel(book({ source_url: "https://example.test/x" }))).toBe("https://example.test/x");
  });

  it("lists books, loads a book's sections once on expansion, and toggles optimistically", async () => {
    const calls = stubFetch({
      "/repertoire": () => ({ status: 200, body: { books: [book()] } }),
      "/repertoire/1/sections": () => ({ status: 200, body: { sections: sections() } }),
      "/repertoire/lines/2": () => ({ status: 200, body: { detail: "updated", rematch: {} } }),
      "/repertoire/books/1": () => ({ status: 200, body: { detail: "updated", rematch: {} } }),
    });
    renderPage();
    expect(await screen.findByText("Course: The Italian")).toBeInTheDocument();
    expect(screen.getByText("white · 2 / 2 lines on")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Course: The Italian/ }));
    expect(await screen.findByText("1) Giuoco")).toBeInTheDocument();
    expect(screen.getByText("1 / 1 on")).toBeInTheDocument();
    expect(screen.getByText("0 / 1 on · from a set position")).toBeInTheDocument();
    expect(screen.getByText("(alt)")).toBeInTheDocument();
    expect(screen.getByText("1.e4 e5 2.Nf3")).toBeInTheDocument();
    const alt = screen.getByRole("switch", { name: "Alt active" });
    expect(alt).toHaveAttribute("aria-checked", "false");
    fireEvent.click(alt);
    expect(alt).toHaveAttribute("aria-checked", "true"); // at once
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/repertoire/lines/2" && c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.path === "/repertoire/lines/2")?.body).toEqual({ active: true });
    expect(screen.getByText("white · 2 / 2 lines on")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Course: The Italian active" }));
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/repertoire/books/1")).toBe(true));
    expect(calls.filter((c) => c.path === "/repertoire/1/sections")).toHaveLength(1);
  });

  it("reverts a toggle the server refuses and shows why", async () => {
    stubFetch({
      "/repertoire": () => ({ status: 200, body: { books: [book()] } }),
      "/repertoire/1/sections": () => ({ status: 200, body: { sections: sections() } }),
      "/repertoire/lines/1": () => ({ status: 404, body: { detail: "Not found" } }),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Course: The Italian/ }));
    const main = await screen.findByRole("switch", { name: "Main active" });
    fireEvent.click(main);
    expect(main).toHaveAttribute("aria-checked", "false");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found");
    expect(main).toHaveAttribute("aria-checked", "true");
  });

  it("opens a line as a walk-through: steps, shows the sticky note and its provenance, jumps on a move in the note, saves a note at the current ply and keeps its place", async () => {
    let served = line();
    const calls = stubFetch({
      "/repertoire": () => ({ status: 200, body: { books: [book()] } }),
      "/repertoire/1/sections": () => ({ status: 200, body: { sections: sections() } }),
      "/repertoire/lines/1/annotated": () => ({ status: 200, body: served }),
      "/repertoire/annotation": (method) => (method === "PUT" ? { status: 200, body: { detail: "Note saved", line_id: 1 } } : { status: 404, body: { detail: "No note for this position" } }),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Course: The Italian/ }));
    fireEvent.click((await screen.findAllByTitle("Read the line"))[0]);
    const dialog = await screen.findByRole("dialog", { name: "Line" });
    expect(await within(dialog).findByTestId("walkthrough-position")).toHaveTextContent("Start · 2 moves");
    expect(within(dialog).getByTestId(/^board-lw/)).toHaveAttribute("data-position", START);
    expect(within(dialog).getByText("Start with")).toBeInTheDocument(); // the lead-in note
    expect(within(dialog).getByText("— GM Example,")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Next ›" }));
    expect(within(dialog).getByTestId("walkthrough-position")).toHaveTextContent("1.e4 · move 1 of 2");
    expect(within(dialog).getByTestId(/^board-lw/)).toHaveAttribute("data-highlights", "e2,e4");
    expect(within(dialog).getByText("· on 1.e4")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Next ›" }));
    expect(within(dialog).getByText("· on 1.e4 (carried forward)")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Next note ›" })).toBeDisabled(); // the last anchor is this ply's own
    fireEvent.click(within(dialog).getByRole("button", { name: "Next ›" }));
    expect(within(dialog).getByText("Now Nf3.")).toBeInTheDocument(); // shown once its move has been played
    expect(within(dialog).getByText("Your note")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "‹ Prev note" }));
    expect(within(dialog).getByTestId("walkthrough-position")).toHaveTextContent("1…e5 · move 1 of 2");
    // A move named in the note jumps to the position after it.
    fireEvent.click(within(dialog).getByRole("button", { name: "Go to start" }));
    expect(within(dialog).getByRole("button", { name: "Go to 2.Nf3 (has a note)" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "e4" }));
    expect(within(dialog).getByTestId("walkthrough-position")).toHaveTextContent("1.e4 · move 1 of 2");
    // Add a note at this ply (no note anchored here): it is written to the current FEN, attached to the line.
    fireEvent.click(within(dialog).getByRole("button", { name: "＋ Add note here" }));
    fireEvent.change(within(dialog).getByLabelText("Note"), { target: { value: "Black replies e5" } });
    served = { ...line(), positions: line().positions.map((p) => (p.ply === 1 ? { ...p, annotation: { text: "Black replies e5", source: "manual", author: null, book_title: null, from_chapter: null } } : p)) };
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/repertoire/lines/1/annotated")).toHaveLength(2));
    const put = calls.find((c) => c.path === "/repertoire/annotation" && c.method === "PUT");
    expect(put?.body).toEqual({ fen: AFTER_E4, text: "Black replies e5", line_id: 1 });
    expect(within(dialog).getByTestId("walkthrough-position")).toHaveTextContent("1.e4 · move 1 of 2"); // still here
    fireEvent.click(within(dialog).getByRole("button", { name: "Next ›" }));
    expect(await within(dialog).findByText("Black replies e5")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Line" })).not.toBeInTheDocument();
  });
});
