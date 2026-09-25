import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import RepertoireConflicts from "./RepertoireConflicts";
import type { ConflictLine, ConflictPosition, ConflictsResponse, DuplicateGroup, Refusal } from "../repertoire";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string; arrows?: { color: string }[] } }) => <div data-testid={`board-${options.id}`} data-position={options.position} data-arrows={(options.arrows ?? []).map((a) => a.color).join(",")} />,
}));

const AFTER_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";
const AFTER_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3";

const line = (over: Partial<ConflictLine> = {}): ConflictLine => ({ line_id: 1, line_name: "Main", chapter_id: 1, chapter_title: "Giuoco", book_id: 1, book_title: "Italian", line_active: true, chapter_active: true, book_active: true, effective: true, ...over });
const resolved = (): ConflictPosition => ({
  fen: AFTER_NC6,
  color: "white",
  contested: false,
  active_moves: 1,
  moves: [
    { move: "Bc4", lines: [{ ...line(), move: "Bc4" }] },
    { move: "Bb5", lines: [{ ...line({ line_id: 2, line_name: "Spanish", chapter_id: 2, chapter_title: "Intro", line_active: false, effective: false }), move: "Bb5" }] },
  ],
});
const contested = (): ConflictPosition => ({
  fen: AFTER_E5,
  color: "white",
  contested: true,
  active_moves: 2,
  moves: [
    { move: "Nf3", lines: [{ ...line({ line_id: 3, line_name: "Knight" }), move: "Nf3" }] },
    { move: "Bc4", lines: [{ ...line({ line_id: 4, line_name: "Bishop", chapter_id: 3, chapter_title: "Off", chapter_active: false, effective: false }), move: "Bc4" }, { ...line({ line_id: 5, line_name: "Bishop too" }), move: "Bc4" }] },
  ],
});
const duplicate = (): DuplicateGroup => ({ color: "white", moves: ["e4", "e5", "Nf3"], lines: [line({ line_id: 6, line_name: "Intro copy", chapter_id: 2, chapter_title: "Intro" }), line({ line_id: 7, line_name: "Chapter copy" })] });
const refusal = (): Refusal => ({ line_id: 2, line_name: "Spanish", chapter_title: "Intro", fen: AFTER_NC6, move: "Bb5", reason: "anchor", rivals: [{ line_id: 1, line_name: "Main", chapter_id: 1, chapter_title: "Giuoco", book_id: 1, book_title: "Italian", move: "Bc4" }] });

type Reply = { status: number; body: unknown };
function stubFetch(routes: Record<string, (method: string) => Reply>) {
  const calls: Array<{ path: string; method: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [rawPath] = url.replace(/^.*\/api/, "").split("?");
      const call = { path: rawPath, method: init?.method ?? "GET" };
      calls.push(call);
      const h = routes[rawPath];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = h(call.method);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

const renderPage = (initial = "/repertoire/conflicts") =>
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/repertoire/conflicts" element={<RepertoireConflicts />} />
        <Route path="/repertoire" element={<p>Repertoire page</p>} />
      </Routes>
    </MemoryRouter>,
  );

const response = (over: Partial<ConflictsResponse> = {}): ConflictsResponse => ({ positions: [contested(), resolved()], duplicates: [duplicate()], contested: 1, ...over });

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Repertoire conflicts page", () => {
  it("opens on contested positions, lists every position under All, and lists duplicate lines", async () => {
    stubFetch({ "/repertoire/conflicts": () => ({ status: 200, body: response() }) });
    renderPage();
    expect(await screen.findByRole("button", { name: "Contested (1)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("link", { name: "← Repertoire" })).toHaveAttribute("href", "/repertoire");
    const card = screen.getByTestId(`board-conflict-${AFTER_E5}`).closest("[data-fen]")!;
    expect(card).toHaveTextContent("2 moves · 2 of 3 lines on");
    expect(within(card as HTMLElement).getByText("Contested")).toBeInTheDocument();
    expect(screen.getByTestId(`board-conflict-${AFTER_E5}`)).toHaveAttribute("data-arrows", "#E69F00,#009E73"); // both moves in play: solid
    expect(screen.queryByTestId(`board-conflict-${AFTER_NC6}`)).not.toBeInTheDocument();
    // Expanding shows the move groups with a switch per line and why a line is not in play.
    fireEvent.click(within(card as HTMLElement).getByRole("button", { expanded: false }));
    expect(screen.getByText("1 of 2 on")).toBeInTheDocument();
    expect(screen.getByText("(chapter off)")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Bishop active" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("button", { name: "All (2)" }));
    expect(screen.getByTestId(`board-conflict-${AFTER_NC6}`)).toHaveAttribute("data-arrows", "#E69F00,#009E7366"); // the off line's move faded
    expect(screen.getByRole("switch", { name: "Bishop active" })).toBeInTheDocument(); // still expanded
    // Duplicate lines.
    expect(screen.getByText("1.e4 e5 2.Nf3")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Intro copy active" })).toBeInTheDocument();
  });

  it("shows the empty states", async () => {
    stubFetch({ "/repertoire/conflicts": () => ({ status: 200, body: response({ positions: [], duplicates: [], contested: 0 }) }) });
    renderPage();
    expect(await screen.findByText("No contested positions.")).toBeInTheDocument();
    expect(screen.getByText("No duplicate lines.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "All (0)" }));
    expect(screen.getByText("No conflicts. Every position in your repertoire has at most one move.")).toBeInTheDocument();
  });

  it("a deep link to a position that is not contested flips the filter to All and reveals it; a contested target keeps the default", async () => {
    stubFetch({ "/repertoire/conflicts": () => ({ status: 200, body: response({ positions: [resolved()], contested: 0 }) }) });
    const { unmount } = renderPage(`/repertoire/conflicts?fen=${encodeURIComponent(AFTER_NC6)}`);
    expect(await screen.findByRole("button", { name: "All (1)" })).toHaveAttribute("aria-pressed", "true");
    const card = screen.getByTestId(`board-conflict-${AFTER_NC6}`).closest("[data-fen]") as HTMLElement;
    expect(within(card).getByRole("button", { expanded: true })).toBeInTheDocument();
    expect(card.className).toContain("bg-amber-50");
    unmount();
    stubFetch({ "/repertoire/conflicts": () => ({ status: 200, body: response() }) });
    renderPage(`/repertoire/conflicts?fen=${encodeURIComponent(AFTER_E5)}`);
    expect(await screen.findByRole("button", { name: "Contested (1)" })).toHaveAttribute("aria-pressed", "true");
    expect(within(screen.getByTestId(`board-conflict-${AFTER_E5}`).closest("[data-fen]") as HTMLElement).getByRole("button", { expanded: true })).toBeInTheDocument();
  });

  it("an unknown FEN is ignored and ?filter=all opens on All", async () => {
    stubFetch({ "/repertoire/conflicts": () => ({ status: 200, body: response() }) });
    const { unmount } = renderPage("/repertoire/conflicts?fen=nope");
    expect(await screen.findByRole("button", { name: "Contested (1)" })).toHaveAttribute("aria-pressed", "true");
    unmount();
    renderPage("/repertoire/conflicts?filter=all");
    expect(await screen.findByRole("button", { name: "All (2)" })).toHaveAttribute("aria-pressed", "true");
  });

  it("refetches after a flip, and a refused flip opens the dialog whose link reveals the position in place", async () => {
    let served = response();
    const calls = stubFetch({
      "/repertoire/conflicts": () => ({ status: 200, body: served }),
      "/repertoire/lines/3": () => ((served = response({ positions: [resolved()], contested: 0 })), { status: 200, body: { detail: "updated", rematch: {}, held_back: [] } }),
      "/repertoire/lines/2": () => ({ status: 409, body: { detail: "activation_conflict", refusal: refusal() } }),
    });
    renderPage();
    const card = (await screen.findByTestId(`board-conflict-${AFTER_E5}`)).closest("[data-fen]") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { expanded: false }));
    fireEvent.click(screen.getByRole("switch", { name: "Knight active" }));
    expect(await screen.findByText("No contested positions.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/repertoire/conflicts")).toHaveLength(2);
    // The resolved position, under All; switching Spanish on is refused.
    fireEvent.click(screen.getByRole("button", { name: "All (1)" }));
    const resolvedCard = screen.getByTestId(`board-conflict-${AFTER_NC6}`).closest("[data-fen]") as HTMLElement;
    fireEvent.click(within(resolvedCard).getByRole("button", { expanded: false }));
    const spanish = screen.getByRole("switch", { name: "Spanish active" });
    fireEvent.click(spanish);
    const dialog = await screen.findByRole("dialog", { name: "Can't switch Spanish on" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(spanish).toHaveAttribute("aria-checked", "false");
    // Back to Contested, then the dialog's link: an in-page navigation that reveals the target.
    fireEvent.click(screen.getByRole("button", { name: "Contested (0)" }));
    fireEvent.click(within(dialog).getByRole("link", { name: "Open in Conflicts" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "All (1)" })).toHaveAttribute("aria-pressed", "true");
    expect((screen.getByTestId(`board-conflict-${AFTER_NC6}`).closest("[data-fen]") as HTMLElement).className).toContain("bg-amber-50");
  });
});
