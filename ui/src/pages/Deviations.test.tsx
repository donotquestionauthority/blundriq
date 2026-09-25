import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import Deviations from "./Deviations";
import { buildQuery, defaultFilters, toCard } from "../deviations";
import type { DeviationPattern, DeviationsResponse } from "../deviations";
import { recommended } from "../components/PositionCard/types";
import { ARROWS } from "../utils/board";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; arrows?: unknown[]; onSquareClick?: (a: { square: string }) => void } }) => <div data-testid={`board-${options.id}`} data-arrows={JSON.stringify(options.arrows ?? [])} onClick={() => options.onSquareClick?.({ square: "e4" })} />,
}));

const AFTER_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3";
const MOVES = ["e4", "e5", "Nf3", "Nc6", "d4", "exd4"];

const pattern = (over: Partial<DeviationPattern> = {}): DeviationPattern => ({
  book_id: 1,
  chapter_id: 1,
  ply: 4,
  expected_move: "Bc4",
  book: "Italian",
  chapter: "Giuoco",
  color: "white",
  count: 3,
  wins: 1,
  losses: 2,
  draws: 0,
  win_pct: 33,
  is_new: false,
  last_seen: new Date().toISOString(),
  most_common_played: "d4",
  deviation_fen: AFTER_NC6,
  chess_game_id: 9,
  moves: MOVES,
  line_names: ["Main", "Main too"],
  rep_lines: [{ book: "Italian", chapter: "Giuoco", line_name: "Main", line_id: 1, is_alternative: false, expected_move: "Bc4", occurrence_fen: AFTER_NC6, line_moves: ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], line_ply: 4, followed: 5, last_followed: null, deviated_by_me: 3, last_deviated: null, me_dev_expected: "Bc4", me_dev_played: "d4", me_dev_ply: 4, deviated_by_opp: 0, opp_dev_expected: null, opp_dev_played: null, opp_dev_ply: null }],
  rep_expected_move: "Bc4",
  games: [{ chess_game_id: 9, game_url: "https://example.test/9", played_at: new Date().toISOString(), result: "loss", played_move: "d4", expected_move: "Bc4", ply: 4 }],
  ...over,
});

const page = (positions: DeviationPattern[], over: Partial<DeviationsResponse> = {}): DeviationsResponse => ({
  positions,
  total: positions.length,
  new_count: positions.filter((p) => p.is_new).length,
  to_acknowledge: positions.filter((p) => p.is_new).map((p) => [p.book_id, p.chapter_id, p.ply, p.expected_move]),
  page: 0,
  page_size: 50,
  total_pages: 1,
  ...over,
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
      const h = routes[rawPath] ?? (rawPath === "/deviations/seen" ? () => ({ status: 200, body: { seen_at: "2026-09-24T10:00:00+00:00" } }) : rawPath === "/repertoire/annotation" ? () => ({ status: 200, body: null }) : undefined);
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(call.method, call.body, call.query);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

const SETTINGS = { deviations_default_filter_mode: "games", deviations_default_window_days: 20, deviations_default_last_n_games: 500, deviations_default_min_occurrences: 2 };
const lastList = (calls: ReturnType<typeof stubFetch>) => calls.filter((c) => c.path === "/deviations").at(-1);
const acks = (calls: ReturnType<typeof stubFetch>) => calls.filter((c) => c.path === "/deviations/seen" && c.method === "POST");

const renderPage = () =>
  render(
    <MemoryRouter>
      <Deviations />
    </MemoryRouter>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Deviations helpers", () => {
  it("opens on the settings row's window and builds the query", () => {
    const f = defaultFilters(SETTINGS);
    expect(f).toEqual({ since_days: null, last_n_games: 500, min_occurrences: 2, time_class: "focus", color: null });
    expect(buildQuery(f, 0)).toBe("last_n_games=500&min_occurrences=2&time_class=focus&page=0");
    expect(buildQuery({ ...f, last_n_games: 0, since_days: 7, color: "black" }, 2)).toBe("since_days=7&min_occurrences=2&time_class=focus&color=black&page=2");
    expect(defaultFilters({}).since_days).toBe(20);
  });
  it("maps a pattern onto the card: the expected move is slot 2, the repertoire's reading slot 3, and no explanation", () => {
    const card = toCard(pattern());
    expect(card).toMatchObject({ fen: AFTER_NC6, times: 3, expectedMove: "Bc4", mostCommonPlayed: "d4", repExpectedMove: "Bc4", ply: 4, movePlayed: null });
    expect(card.chessGameId).toBeUndefined();
    expect(card.games[0]).toMatchObject({ move_played: "d4", best_move: "Bc4" });
    expect(toCard(pattern({ rep_lines: [] })).repLines).toBeNull();
  });
});

describe("the recommended move's three authorities", () => {
  it("prefers the engine, then the page's expected move, then the repertoire — and labels accordingly", () => {
    expect(recommended({ bestMove: "Nxe5", expectedMove: "Bc4", movePlayed: "d3", repExpectedMove: "Nf3" })).toEqual({ move: "Nxe5", label: "Best" });
    expect(recommended({ bestMove: null, expectedMove: "Bc4", movePlayed: null, repExpectedMove: "Nf3" })).toEqual({ move: "Bc4", label: "Expected" });
    expect(recommended({ bestMove: null, expectedMove: null, movePlayed: null, repExpectedMove: "Nf3" })).toEqual({ move: "Nf3", label: "Expected" });
  });
  it("never lets a pure repertoire move stand in once a move was actually played, and never calls it Best", () => {
    expect(recommended({ bestMove: null, expectedMove: null, movePlayed: "d3", repExpectedMove: "Nf3" })).toEqual({ move: null, label: "Expected" });
    expect(recommended({ bestMove: null, expectedMove: null, movePlayed: null, repExpectedMove: null }).move).toBeNull();
  });
});

describe("Deviations page", () => {
  it("lists patterns from the default filters, draws the expected move in book colour, and refetches when a filter changes", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/deviations": () => ({ status: 200, body: page([pattern()]) }),
    });
    renderPage();
    expect(await screen.findByText("3×")).toBeInTheDocument();
    expect(screen.getByText("1 deviation patterns")).toBeInTheDocument();
    expect(screen.getByText("📖 Italian")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/deviations")[0].query.toString()).toBe(buildQuery(defaultFilters(SETTINGS), 0));
    const arrows = JSON.parse(screen.getByTestId(/^board-pc/).getAttribute("data-arrows") ?? "[]") as Array<{ color: string; endSquare: string }>;
    expect(arrows.map((a) => [a.color, a.endSquare])).toEqual([
      [ARROWS.opponent, "c6"],
      [ARROWS.played, "d4"],
      [ARROWS.engine, "c4"],
    ]);
    fireEvent.change(screen.getByLabelText("Color"), { target: { value: "black" } });
    await vi.waitFor(() => expect(lastList(calls)?.query.get("color")).toBe("black"));
    fireEvent.change(screen.getByLabelText("Window"), { target: { value: "d30" } });
    await vi.waitFor(() => expect(lastList(calls)?.query.get("since_days")).toBe("30"));
    expect(lastList(calls)?.query.has("last_n_games")).toBe(false);
  });

  it("opens a card: the expected move is labelled Expected, the record and the repertoire lines show, and there is no explanation panel", async () => {
    stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/deviations": () => ({ status: 200, body: page([pattern()]) }),
    });
    renderPage();
    fireEvent.click(await screen.findByText("3×"));
    const dialog = await screen.findByRole("dialog", { name: "Position" });
    expect(within(dialog).getAllByText("Expected").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("Usually")).toBeInTheDocument();
    expect(within(dialog).getByText("1W")).toBeInTheDocument();
    expect(within(dialog).getByTestId("rep-lines")).toHaveTextContent("Repertoire lines (1)");
    expect(within(dialog).getByTestId("rep-lines")).toHaveTextContent("I deviated 3×");
    expect(within(dialog).queryByText(/Explain/)).not.toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Analyze on Lichess" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Create puzzle" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("columnheader", { name: "Expected" })).toBeInTheDocument();
  });

  it("similar positions asks about the pattern's most common move only when it is legal on the displayed board", async () => {
    // A pattern spans boards: its board is the latest game's, its most common played move an aggregate. Here the
    // aggregate `exd5` is not playable on the displayed board (after 1.e4 e5 2.Nf3 Nc6); the panel must still work.
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/deviations": () => ({ status: 200, body: page([pattern({ most_common_played: "exd5" })]) }),
      "/repertoire/similar": (_m, _b, q) => (q.has("move") ? { status: 400, body: { detail: "move is not legal in fen" } } : { status: 200, body: { query: { fen: q.get("fen"), move: null, max_distance: 4, max_positions: 12 }, truncated: false, positions_omitted: 0, neighbours: [] } }),
    });
    renderPage();
    fireEvent.click(await screen.findByText("3×"));
    const dialog = await screen.findByRole("dialog", { name: "Position" });
    fireEvent.click(within(dialog).getByRole("button", { name: /Similar positions in your repertoire/ }));
    expect(await within(dialog).findByText(/No similar positions within 4 squares/)).toBeInTheDocument();
    const similar = calls.filter((c) => c.path === "/repertoire/similar");
    expect(similar).toHaveLength(1);
    expect(similar[0].query.get("fen")).toBe(AFTER_NC6);
    expect(similar[0].query.has("move")).toBe(false);
  });

  it("similar positions asks about the most common move when it is legal on the displayed board", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/deviations": () => ({ status: 200, body: page([pattern()]) }),
      "/repertoire/similar": (_m, _b, q) => ({ status: 200, body: { query: { fen: q.get("fen"), move: q.get("move"), max_distance: 4, max_positions: 12 }, truncated: false, positions_omitted: 0, neighbours: [] } }),
    });
    renderPage();
    fireEvent.click(await screen.findByText("3×"));
    const dialog = await screen.findByRole("dialog", { name: "Position" });
    fireEvent.click(within(dialog).getByRole("button", { name: /Similar positions in your repertoire/ }));
    await within(dialog).findByText(/No similar positions within 4 squares/);
    expect(calls.filter((c) => c.path === "/repertoire/similar")[0].query.get("move")).toBe("d4");
  });

  it("marks the patterns the server flags and acknowledges exactly what each rendered response says", async () => {
    let flagged = true;
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/deviations": () => ({ status: 200, body: page([pattern({ is_new: flagged }), pattern({ chapter_id: 2, count: 2 })]) }),
    });
    renderPage();
    expect(await screen.findByText("3×")).toBeInTheDocument();
    expect(screen.getAllByText("NEW")).toHaveLength(1);
    expect(screen.getByText("2 deviation patterns · 1 new")).toBeInTheDocument();
    await vi.waitFor(() => expect(acks(calls)).toHaveLength(1));
    expect(acks(calls)[0].body).toEqual({ patterns: [[1, 1, 4, "Bc4"]] });
    flagged = false;
    fireEvent.change(screen.getByLabelText("Min seen"), { target: { value: "3" } });
    await vi.waitFor(() => expect(acks(calls)).toHaveLength(2));
    expect(acks(calls)[1].body).toEqual({ patterns: [] });
    expect(screen.queryByText("NEW")).not.toBeInTheDocument();
  });

  it("records an empty first look", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/deviations": () => ({ status: 200, body: page([]) }),
    });
    renderPage();
    expect(await screen.findByText(/No deviations for these filters/)).toBeInTheDocument();
    await vi.waitFor(() => expect(acks(calls)).toHaveLength(1));
    expect(acks(calls)[0].body).toEqual({ patterns: [] });
  });

  it("never acknowledges a response a newer request has superseded", async () => {
    const resolvers: Array<(r: Reply) => void> = [];
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/deviations": () => new Promise<Reply>((resolve) => resolvers.push(resolve)),
    });
    const { unmount } = renderPage();
    await vi.waitFor(() => expect(resolvers).toHaveLength(1));
    await screen.findByLabelText("Min seen");
    fireEvent.change(screen.getByLabelText("Min seen"), { target: { value: "3" } });
    await vi.waitFor(() => expect(resolvers).toHaveLength(2));
    resolvers[1]({ status: 200, body: page([]) });
    expect(await screen.findByText(/No deviations for these filters/)).toBeInTheDocument();
    resolvers[0]({ status: 200, body: page([pattern({ is_new: true })]) });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("NEW")).not.toBeInTheDocument();
    await vi.waitFor(() => expect(acks(calls)).toHaveLength(1));
    expect(acks(calls)[0].body).toEqual({ patterns: [] });
    unmount();
    expect(acks(calls)).toHaveLength(1);
  });

  it("never acknowledges a response that arrives after the page was left", async () => {
    const resolvers: Array<(r: Reply) => void> = [];
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: SETTINGS }),
      "/deviations": () => new Promise<Reply>((resolve) => resolvers.push(resolve)),
    });
    const { unmount } = renderPage();
    await vi.waitFor(() => expect(resolvers).toHaveLength(1));
    unmount();
    resolvers[0]({ status: 200, body: page([pattern({ is_new: true })]) });
    await new Promise((r) => setTimeout(r, 20));
    expect(acks(calls)).toHaveLength(0);
  });
});
