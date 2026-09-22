import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import Blunders from "./Blunders";
import { buildQuery, defaultFilters, formatDryRun, toCard, topClass } from "../blunders";
import type { BlunderPosition, BlundersResponse } from "../blunders";
import { buildArrows, buildPgn, lichessAnalyzeUrl, numberedLine } from "../utils/chess";
import { ARROWS } from "../utils/board";

// The board is not under test. The mock shows its arrows, and a button that plays a move on it.
let nextDrop = { from: "f3", to: "e5" };
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; arrows?: unknown[]; onPieceDrop?: (a: { sourceSquare: string; targetSquare: string }) => boolean; onSquareClick?: (a: { square: string }) => void } }) => (
    <div data-testid={`board-${options.id}`} data-arrows={JSON.stringify(options.arrows ?? [])} onClick={() => options.onSquareClick?.({ square: "e4" })}>
      {options.onPieceDrop && (
        <button type="button" onClick={() => options.onPieceDrop?.({ sourceSquare: nextDrop.from, targetSquare: nextDrop.to })}>
          play
        </button>
      )}
    </div>
  ),
}));

const FORK = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";
const MOVES = ["e4", "e5", "Nf3", "Nc6", "Bc4", "h6", "d3"];

const position = (over: Partial<BlunderPosition> = {}): BlunderPosition => ({
  fen: FORK,
  count: 3,
  score: 10,
  classifications: { blunder: 2, mistake: 1 },
  color: "white",
  dismissed: false,
  last_seen: new Date().toISOString(),
  context: "Italian Game",
  book: null,
  chapter: null,
  line_names: null,
  move_played: "d3",
  best_move: "Nxe5",
  best_line: "Nxe5 Nxe5 d4",
  post_blunder_line: "Nf6",
  cp_loss: 250,
  ply: 6,
  chess_game_id: 77,
  moves: MOVES,
  games: [{ chess_game_id: 77, game_url: "https://example.test/77", played_at: new Date().toISOString(), result: "loss", classification: "blunder", cp_loss: 250, move_played: "d3", best_move: "Nxe5", in_repertoire: false, deviated_by_me: false }],
  ...over,
});

const OTHER = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";
const page = (positions: BlunderPosition[], over: Partial<BlundersResponse> = {}): BlundersResponse => ({ positions, active_count: positions.length, dismissed_count: 1, page: 0, page_size: 50, total_pages: 1, ...over });

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

const SETTINGS = { blunders_default_filter_mode: "days", blunders_default_window_days: 20, blunders_default_last_n_games: 500, blunders_default_min_occurrences: 2, blunders_default_classifications: ["blunder", "miss", "mistake"] };
const PROMPTS = { prompts: [{ key: "a", label: "Explain", model: "claude-x" }, { key: "c", label: "Other", model: "gpt-x" }] };

const renderPage = () =>
  render(
    <MemoryRouter>
      <Blunders />
    </MemoryRouter>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  nextDrop = { from: "f3", to: "e5" };
});

describe("Blunders helpers", () => {
  it("opens on the settings row's window, by days or by games", () => {
    expect(defaultFilters(SETTINGS)).toEqual({ since_days: 20, last_n_games: 0, min_occurrences: 2, time_class: "focus", classifications: ["blunder", "miss", "mistake"], show_dismissed: false });
    expect(defaultFilters({ ...SETTINGS, blunders_default_filter_mode: "games" })).toMatchObject({ since_days: null, last_n_games: 500 });
    expect(defaultFilters({ blunders_default_classifications: ["nonsense"] }).classifications).toEqual(["miss", "blunder", "mistake"]);
  });
  it("builds the query; a game window wins over a day window", () => {
    const f = defaultFilters(SETTINGS);
    expect(buildQuery(f, 0)).toBe("since_days=20&min_occurrences=2&time_class=focus&classifications=blunder&classifications=miss&classifications=mistake&page=0");
    expect(buildQuery({ ...f, last_n_games: 200, show_dismissed: true, time_class: "blitz" }, 2)).toContain("last_n_games=200&min_occurrences=2&time_class=blitz");
    expect(buildQuery({ ...f, last_n_games: 200 }, 0)).not.toContain("since_days");
  });
  it("names the most severe class present and maps a row onto the card", () => {
    expect(topClass({ classifications: { mistake: 3, miss: 1 } })).toBe("miss");
    expect(topClass({ classifications: {} })).toBeNull();
    expect(toCard(position())).toMatchObject({ times: 3, score: 10, topClassification: "blunder", movePlayed: "d3", chessGameId: 77, ply: 6 });
  });
  it("draws how the position arose, what was played and what was best, each in its own colour", () => {
    expect(buildArrows({ fen: FORK, moves: MOVES, ply: 6, movePlayed: "d3", bestMove: "Nxe5" })).toEqual([
      { startSquare: "h7", endSquare: "h6", color: ARROWS.opponent },
      { startSquare: "d2", endSquare: "d3", color: ARROWS.played },
      { startSquare: "f3", endSquare: "e5", color: ARROWS.engine },
    ]);
    expect(buildArrows({ fen: FORK, movePlayed: "d3", bestMove: "d3" })).toHaveLength(1); // played the best move: one arrow
    expect(buildArrows({ fen: FORK, movePlayed: "Qh5", bestMove: null, moves: MOVES, ply: 99 })).toEqual([]); // illegal or out of range: nothing, no throw
  });
  it("writes PGN, the Lichess link and a numbered line", () => {
    expect(buildPgn(MOVES, 3)).toBe("1. e4 e5 2. Nf3");
    expect(buildPgn(null)).toBe("");
    expect(lichessAnalyzeUrl(MOVES, 3, "black")).toBe("https://lichess.org/analysis/pgn/1._e4_e5_2._Nf3?color=black");
    expect(lichessAnalyzeUrl(null, 3, "white")).toBe("https://lichess.org/analysis");
    expect(numberedLine("8/8/8/8/8/8/8/K6k b - - 0 12", ["Nc6", "Bb5", "a6"])).toEqual(["12... Nc6", "13. Bb5", "a6"]);
  });
  it("formats a dry run as what would be sent", () => {
    const text = formatDryRun({ dry_run: true, model: "m", provider: "anthropic", rendered_prompt: "P", system_prompt: "S", temperature: null, max_tokens: 512, prefill: "", thinking: { type: "disabled" } });
    expect(text).toBe("MODEL: m\nPROVIDER: anthropic\nTEMPERATURE: default\nTHINKING: disabled\nMAX_TOKENS: 512\nPREFILL: (none)\n\n=== SYSTEM PROMPT ===\nS\n\n=== USER PROMPT ===\nP");
  });
});

describe("Blunders page", () => {
  it("lists positions from the default filters and refetches from page 0 when a filter changes", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/blunders": () => ({ status: 200, body: page([position()], { total_pages: 3 }) }),
    });
    renderPage();
    expect(await screen.findByText("3×")).toBeInTheDocument();
    expect(screen.getByText("1 positions · 1 dismissed")).toBeInTheDocument();
    expect(screen.getByText("Italian Game")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/blunders")[0].query.toString()).toBe(buildQuery(defaultFilters(SETTINGS), 0));

    fireEvent.click(screen.getByText("Next →"));
    await vi.waitFor(() => expect(calls.at(-1)?.query.get("page")).toBe("1"));
    fireEvent.change(screen.getByLabelText("Window"), { target: { value: "n200" } });
    await vi.waitFor(() => expect(calls.at(-1)?.query.get("last_n_games")).toBe("200"));
    expect(calls.at(-1)?.query.get("page")).toBe("0");
    expect(calls.at(-1)?.query.has("since_days")).toBe(false);
    fireEvent.change(screen.getByLabelText("Time class"), { target: { value: "blitz" } });
    await vi.waitFor(() => expect(calls.at(-1)?.query.get("time_class")).toBe("blitz"));
    fireEvent.click(screen.getByRole("button", { name: "inaccuracy" }));
    await vi.waitFor(() => expect(calls.at(-1)?.query.getAll("classifications")).toContain("inaccuracy"));
  });

  it("never sends an empty classification set", async () => {
    stubFetch({ "/settings": () => ({ status: 200, body: { ...SETTINGS, blunders_default_classifications: ["blunder"] } }), "/blunders": () => ({ status: 200, body: page([]) }) });
    renderPage();
    expect(await screen.findByText("No recurring positions for these filters.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "blunder" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "miss" })).toBeEnabled();
  });

  it("dismisses from the card without opening it, and restores from the Dismissed view", async () => {
    let dismissed = false;
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/blunders": (_m, _b, q) => {
        const wantDismissed = q.get("show_dismissed") === "true";
        return { status: 200, body: page(wantDismissed === dismissed ? [position({ dismissed })] : [], { active_count: dismissed ? 0 : 1, dismissed_count: dismissed ? 1 : 0 }) };
      },
      "/blunders/dismiss": () => ((dismissed = true), { status: 200, body: { detail: "dismissed" } }),
      "/blunders/restore": () => ((dismissed = false), { status: 200, body: { detail: "restored" } }),
    });
    renderPage();
    fireEvent.click(await screen.findByLabelText("Dismiss"));
    expect(await screen.findByText("No recurring positions for these filters.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((c) => c.path === "/blunders/dismiss")?.body).toEqual({ fen: FORK });

    fireEvent.click(screen.getByText("Dismissed (1)"));
    fireEvent.click(await screen.findByLabelText("Restore"));
    expect(await screen.findByText("No dismissed positions.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/blunders/restore")?.body).toEqual({ fen: FORK });
  });

  it("says so when a dismissal fails, and keeps the card", async () => {
    stubFetch({ "/settings": () => ({ status: 200, body: SETTINGS }), "/blunders": () => ({ status: 200, body: page([position()]) }), "/blunders/dismiss": () => ({ status: 500, body: { detail: "database is down" } }) });
    renderPage();
    fireEvent.click(await screen.findByLabelText("Dismiss"));
    expect(await screen.findByRole("alert")).toHaveTextContent("database is down");
    expect(screen.getByText("3×")).toBeInTheDocument();
  });

  it("opens a card in the overlay, steps with the arrow keys and closes on Escape", async () => {
    stubFetch({ "/settings": () => ({ status: 200, body: SETTINGS }), "/blunders": () => ({ status: 200, body: page([position(), position({ fen: OTHER, count: 2, chess_game_id: null, context: "Open Game" })]) }), "/blunders/prompts": () => ({ status: 200, body: PROMPTS }) });
    renderPage();
    fireEvent.click((await screen.findAllByTestId("position-card"))[0]);
    const dialog = await screen.findByRole("dialog", { name: "Position" });
    expect(within(dialog).getByText("1 / 2")).toBeInTheDocument();
    expect(within(dialog).getByText("1. e4 e5 2. Nf3 Nc6 3. Bc4 h6")).toBeInTheDocument();
    expect(JSON.parse(within(dialog).getByTestId("board-position-overlay-board").dataset.arrows ?? "[]")).toHaveLength(3);
    expect(await within(dialog).findByRole("button", { name: "Explain" })).toBeInTheDocument();
    expect(within(dialog).getByText("Analyze on Lichess").closest("a")).toHaveAttribute("href", lichessAnalyzeUrl(MOVES, 6, "white"));

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(within(dialog).getByText("2 / 2")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Explain" })).not.toBeInTheDocument(); // no analysed game to explain
    fireEvent.keyDown(window, { key: "ArrowRight" }); // already at the end
    expect(within(dialog).getByText("2 / 2")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(within(dialog).getByText("1 / 2")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens from a tap on the board, and ignores the touchend that opened it", async () => {
    stubFetch({ "/settings": () => ({ status: 200, body: SETTINGS }), "/blunders": () => ({ status: 200, body: page([position(), position({ fen: OTHER })]) }), "/blunders/prompts": () => ({ status: 200, body: { prompts: [] } }) });
    renderPage();
    const cards = await screen.findAllByTestId("position-card");
    fireEvent.click(within(cards[1]).getByTestId(/^board-pc/));
    const dialog = await screen.findByRole("dialog", { name: "Position" });
    expect(within(dialog).getByText("2 / 2")).toBeInTheDocument();
    fireEvent.touchEnd(window, { changedTouches: [{ clientX: 300 }] }); // no touchstart seen: not a swipe
    expect(within(dialog).getByText("2 / 2")).toBeInTheDocument();
    fireEvent.touchStart(window, { touches: [{ clientX: 100 }] });
    fireEvent.touchEnd(window, { changedTouches: [{ clientX: 300 }] });
    expect(within(dialog).getByText("1 / 2")).toBeInTheDocument();
  });

  it("explains with the chosen prompt, shows a refusal's message, and copies the dry run", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    let n = 0;
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/blunders": () => ({ status: 200, body: page([position()]) }),
      "/blunders/prompts": () => ({ status: 200, body: PROMPTS }),
      "/blunders/explain": (_m, body) => {
        if (body?.dry_run) return { status: 200, body: { dry_run: true, model: "claude-x", provider: "anthropic", rendered_prompt: "RENDERED", system_prompt: "", temperature: null, max_tokens: 512 } };
        n += 1;
        return n === 1 ? { status: 200, body: { explanation: "**d3 was incorrect because** it is slow.\\n\\nNxe5 wins a pawn.", cached: true, model: "claude-x", prompt_label: "Explain" } } : { status: 429, body: { detail: { window: "hourly", message: "Limit reached: 20 AI calls per hour." } } };
      },
    });
    renderPage();
    fireEvent.click((await screen.findAllByTestId("position-card"))[0]);
    fireEvent.click(await screen.findByRole("button", { name: "Explain" }));
    expect(await screen.findByText("d3 was incorrect because")).toBeInTheDocument();
    expect(screen.getByText("claude-x · cached")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/blunders/explain")?.body).toEqual({ chess_game_id: 77, ply: 6, prompt_key: "a" }); // identifiers only

    fireEvent.click(screen.getByRole("button", { name: "Other" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Limit reached: 20 AI calls per hour.");
    expect(screen.queryByText("d3 was incorrect because")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy prompt: Explain" }));
    await vi.waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(String(writeText.mock.calls[0])).toContain("=== USER PROMPT ===\nRENDERED");
  });

  it("creates a puzzle from the overlay: the line is played on the board, and Escape closes only the dialog", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/blunders": () => ({ status: 200, body: page([position({ color: "white" })]) }),
      "/blunders/prompts": () => ({ status: 200, body: { prompts: [] } }),
      "/puzzles": () => ({ status: 200, body: { id: 5, visible: false } }),
    });
    renderPage();
    fireEvent.click((await screen.findAllByTestId("position-card"))[0]);
    fireEvent.click(await screen.findByText("Create puzzle"));
    const modal = await screen.findByRole("dialog", { name: "Create puzzle" });
    const save = within(modal).getByRole("button", { name: "Create puzzle" });
    expect(save).toBeDisabled(); // no line yet

    nextDrop = { from: "a1", to: "a8" };
    fireEvent.click(within(modal).getByText("play")); // not a legal move: nothing recorded
    expect(save).toBeDisabled();
    nextDrop = { from: "f3", to: "e5" };
    fireEvent.click(within(modal).getByText("play"));
    nextDrop = { from: "c6", to: "e5" };
    fireEvent.click(within(modal).getByText("play"));
    expect(within(modal).getByTestId("solution-line")).toHaveTextContent("4. Nxe5 Nxe5");
    fireEvent.click(within(modal).getByText("↶ Undo"));
    expect(within(modal).getByTestId("solution-line")).toHaveTextContent(/^4\. Nxe5$/);

    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Create puzzle" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Position" })).toBeInTheDocument(); // the overlay underneath stayed

    fireEvent.click(screen.getByText("Create puzzle"));
    const again = await screen.findByRole("dialog", { name: "Create puzzle" });
    expect(within(again).getByRole("button", { name: "Create puzzle" })).toBeDisabled(); // a fresh start every time
    nextDrop = { from: "f3", to: "e5" };
    fireEvent.click(within(again).getByText("play"));
    fireEvent.change(within(again).getByLabelText(/Title/), { target: { value: "  Fork trick " } });
    fireEvent.click(within(again).getByRole("button", { name: "Create puzzle" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/hidden for now/);
    expect(calls.find((c) => c.path === "/puzzles")?.body).toEqual({ fen: FORK, solution_line: ["Nxe5"], color: "w", source_types: ["blunder"], title: "Fork trick" });
  });

  it("keeps the dialog open with the server's reason when the board already has a puzzle", async () => {
    stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/blunders": () => ({ status: 200, body: page([position()]) }),
      "/blunders/prompts": () => ({ status: 200, body: { prompts: [] } }),
      "/puzzles": () => ({ status: 409, body: { detail: "You already have a puzzle for this position" } }),
    });
    renderPage();
    fireEvent.click((await screen.findAllByTestId("position-card"))[0]);
    fireEvent.click(await screen.findByText("Create puzzle"));
    const modal = await screen.findByRole("dialog", { name: "Create puzzle" });
    fireEvent.click(within(modal).getByText("play"));
    fireEvent.click(within(modal).getByRole("button", { name: "Create puzzle" }));
    expect(await within(modal).findByText("You already have a puzzle for this position")).toBeInTheDocument();
    expect(within(modal).getByRole("button", { name: "Create puzzle" })).toBeEnabled();
  });

  it("steps back a page when a dismissal empties the last one", async () => {
    let dismissed = 0;
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/blunders": (_m, _b, q) => {
        const p = Number(q.get("page"));
        const total = 51 - dismissed;
        const pages = Math.max(1, Math.ceil(total / 50));
        const rows = p < pages ? [position({ fen: p === 0 ? FORK : OTHER })] : [];
        return { status: 200, body: page(rows, { active_count: total, page: p, total_pages: pages }) };
      },
      "/blunders/dismiss": () => ((dismissed += 1), { status: 200, body: { detail: "dismissed" } }),
    });
    renderPage();
    fireEvent.click(await screen.findByText("Next →"));
    await vi.waitFor(() => expect(calls.at(-1)?.query.get("page")).toBe("1"));
    expect(await screen.findByText("2 / 2")).toBeInTheDocument(); // the second page has rendered
    fireEvent.click(screen.getByLabelText("Dismiss"));
    await vi.waitFor(() => expect(calls.at(-1)?.query.get("page")).toBe("0"));
    expect(await screen.findByText("50 positions · 1 dismissed")).toBeInTheDocument();
    expect(screen.getAllByTestId("position-card")).toHaveLength(1);
  });
});
