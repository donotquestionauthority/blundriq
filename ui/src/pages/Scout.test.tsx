import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import Scout from "./Scout";
import { recommended } from "../components/PositionCard/types";
import { decisionNodeToCard, scoutToCard } from "../scout";
import type { OpponentProfile, ScoutDecisionNode, ScoutPosition, ScoutPositionsResponse, ScoutReport } from "../scout";
import { ARROWS } from "../utils/board";
import { buildArrows, decisionNodeArrows } from "../utils/chess";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; arrows?: unknown[]; onSquareClick?: (a: { square: string }) => void } }) => <div data-testid={`board-${options.id}`} data-arrows={JSON.stringify(options.arrows ?? [])} onClick={() => options.onSquareClick?.({ square: "e4" })} />,
}));

const P6 = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"; // after 3...Bc5
const P8 = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2P2N2/PP1P1PPP/RNBQK2R w KQkq - 1 5";
const NODE = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R b KQkq - 5 4"; // Black to move
const NODE_PRE = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";
const ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d4"];

const profile = (over: Partial<OpponentProfile> = {}): OpponentProfile => ({ id: 1, name: "Giri", is_initialized: true, game_count: 12, chesscom_username: "giri", lichess_username: null, chesscom_last_fetched: null, lichess_last_fetched: null, ...over });

const position = (over: Partial<ScoutPosition> = {}): ScoutPosition => ({
  fen: P6,
  tier: 3,
  my_frequency: 3,
  opp_frequency: 7,
  max_depth: 6,
  blunder_score: null,
  blunder_count: null,
  top_classification: null,
  move_played: null,
  best_move: "d3",
  best_move_date: "2026-09-12",
  line_id: null,
  line_name: null,
  chapter_title: null,
  book_title: null,
  book_color: null,
  moves: ITALIAN,
  starting_fen: null,
  ply: 6,
  my_games: [{ date: "2026-09-12", opponent: "x", color: "white", class: "", url: "https://example.test/1" }],
  opp_games: [{ date: "2026-09-10", opening: "Italian Game", as: "black", url: "https://www.chess.com/game/live/9" }],
  rep_lines: [],
  rep_expected_move: null,
  ...over,
});

const node = (over: Partial<ScoutDecisionNode> = {}): ScoutDecisionNode => ({ fen: NODE, node_freq: 6, distinct_replies: 2, my_frequency: 2, opp_replies: [{ move: "Bb4", cnt: 4 }, { move: "Bc5", cnt: 2 }], replies_more: 1, lead_in: "Nc3", lead_pre_fen: NODE_PRE, line_name: null, book_title: null, chapter_title: null, my_color: "white", ...over });

const report: ScoutReport = {
  activity: { last_1: 2, last_7: 9, last_30: 40, total: 300 },
  as_white: [{ family: "Italian Game", cnt: 20, pct: 60, games: [] }],
  as_black: [],
  most_played: [{ family: "Italian Game", cnt: 20, pct: 60 }],
  best_lines: [{ eco: "C50", family: "Italian Game", variation: "Italian Game: Giuoco", games: 10, wins: 8, draws: 1, losses: 1, win_pct: 80, shrunk_rate: 0.7 }],
  worst_lines: [{ eco: "B01", family: "Scandinavian", variation: "Scandinavian: Mieses", games: 4, wins: 0, draws: 1, losses: 3, win_pct: 0, shrunk_rate: 0.2 }],
};

const positionsPage = (positions: ScoutPosition[]): ScoutPositionsResponse => ({ positions, total: positions.length, page: 0, page_size: 20, total_pages: 1, tier_counts: { "3": positions.length } });

type Reply = { status: number; body: unknown };
type Call = { path: string; method: string; body: Record<string, unknown> | null; query: URLSearchParams };
function stubFetch(routes: Record<string, (call: Call) => Reply>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [rawPath, rawQuery] = url.replace(/^.*\/api/, "").split("?");
      const call: Call = { path: rawPath, method: init?.method ?? "GET", body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null, query: new URLSearchParams(rawQuery ?? "") };
      calls.push(call);
      const h = routes[rawPath];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = h(call);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

const SETTINGS = { scout_default_last_n_games: 500, scout_default_min_occurrences: 2 };

function routes(state: { profiles: OpponentProfile[]; positions: ScoutPosition[]; nodes: ScoutDecisionNode[]; dismissed: string[] }) {
  return {
    "/settings": () => ({ status: 200, body: SETTINGS }),
    "/scout/profiles": (c: Call) => {
      if (c.method === "POST") {
        if (c.body?.name === "Taken") return { status: 409, body: { detail: "name_taken" } };
        state.profiles = [...state.profiles, profile({ id: 9, name: String(c.body?.name), is_initialized: false, game_count: 0 })];
        return { status: 200, body: { profile_id: 9 } };
      }
      return { status: 200, body: { profiles: state.profiles } };
    },
    "/scout/profiles/1": () => {
      state.profiles = state.profiles.filter((p) => p.id !== 1);
      return { status: 200, body: { detail: "removed" } };
    },
    "/scout/report/1": () => ({ status: 200, body: report }),
    "/scout/line-games/1": () => ({ status: 200, body: { games: [{ date: "2026-09-01", result: "win", color: "white", opening: "Italian Game", url: "https://lichess.org/abc" }] } }),
    "/scout/positions/1": () => ({ status: 200, body: positionsPage(state.positions.filter((p) => !state.dismissed.includes(p.fen))) }),
    "/scout/decision-nodes/1": () => {
      const nodes = state.nodes.filter((n) => !state.dismissed.includes(n.fen));
      return { status: 200, body: { nodes, total: nodes.length } };
    },
    "/scout/dismiss": (c: Call) => {
      const fen = String(c.body?.fen);
      state.dismissed = c.method === "DELETE" ? state.dismissed.filter((f) => f !== fen) : [...state.dismissed, fen];
      return { status: 200, body: { detail: "ok" } };
    },
    "/scout/dismissed": () => ({ status: 200, body: { boards: state.dismissed.map((fen) => ({ fen, dismissed_at: "2026-09-20T10:00:00+00:00" })) } }),
  };
}

const renderPage = () =>
  render(
    <MemoryRouter>
      <Scout />
    </MemoryRouter>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Scout card mapping", () => {
  it("prefers the repertoire's move over the engine's, on every tier", () => {
    const both = scoutToCard(position({ rep_expected_move: "c3", best_move: "d3" }));
    expect(recommended(both)).toEqual({ move: "c3", label: "Expected" });
    expect(both.bestMove).toBeNull();
    const arrows = buildArrows({ fen: P6, moves: ITALIAN, ply: 6, movePlayed: both.movePlayed, bestMove: recommended(both).move });
    expect(arrows.map((a) => [a.color, a.endSquare])).toEqual([
      [ARROWS.opponent, "c5"],
      [ARROWS.engine, "c3"],
    ]); // the repertoire's move, never d3
    const engineOnly = scoutToCard(position({ rep_expected_move: null, best_move: "Nc3" }));
    expect(recommended(engineOnly)).toEqual({ move: "Nc3", label: "Best" });
    expect(engineOnly.bestMoveDate).toBe("2026-09-12");
    const blunderTier = scoutToCard(position({ tier: 1, move_played: "a3", best_move: "d4", rep_expected_move: "c3", top_classification: "blunder" }));
    expect(recommended(blunderTier)).toEqual({ move: "c3", label: "Expected" });
    expect(blunderTier.movePlayed).toBe("a3");
    expect(scoutToCard(position({ tier: 1, move_played: "a3", best_move: "d4" }))).toMatchObject({ bestMove: "d4", expectedMove: null });
  });
  it("maps the frequencies, the games and the repertoire lines", () => {
    const c = scoutToCard(position());
    expect(c).toMatchObject({ times: 7, context: "Me 3× · Them 7×", color: "white", ply: 6 });
    expect(c.games).toEqual([{ game_url: "https://example.test/1", played_at: "2026-09-12", classification: undefined }]);
    expect(c.oppGames).toEqual([{ game_url: "https://www.chess.com/game/live/9", played_at: "2026-09-10", opening: "Italian Game", as: "black" }]);
    expect(c.repLines).toBeNull();
  });
  it("a decision node draws one arrow per reply under the lead-in and no best arrow", () => {
    const c = decisionNodeToCard(node());
    expect(c).toMatchObject({ color: "white", times: 6, repliesMore: 1, leadIn: "Nc3" });
    expect(recommended(c).move).toBeNull();
    const arrows = decisionNodeArrows({ fen: c.fen, replies: c.oppReplies, leadIn: c.leadIn, leadPreFen: c.leadPreFen });
    expect(arrows).toHaveLength(3);
    expect(arrows[0]).toMatchObject({ startSquare: "b1", endSquare: "c3", color: ARROWS.opponent });
    expect(arrows[1]).toMatchObject({ startSquare: "f8", endSquare: "b4" });
    expect(arrows[2]).toMatchObject({ startSquare: "f8", endSquare: "c5" });
    expect(arrows[1].color.startsWith(ARROWS.book) && arrows[2].color.startsWith(ARROWS.book)).toBe(true);
    expect(arrows[1].color > arrows[2].color).toBe(true); // the more frequent reply is the more opaque
    expect(decisionNodeArrows({ fen: c.fen, replies: [{ move: "Qxh9", cnt: 1 }] })).toEqual([]);
  });
});

describe("Scout page", () => {
  it("renders the four sections from fixtures and keys the positions by fen", async () => {
    const state = { profiles: [profile()], positions: [position(), position({ fen: P8, opp_frequency: 2 })], nodes: [node()], dismissed: [] as string[] };
    stubFetch(routes(state));
    renderPage();
    const activity = await screen.findByLabelText("Activity");
    expect(activity.textContent).toContain("Giri — activity");
    expect(activity.textContent).toContain("300");
    expect(screen.getByText("Italian Game: Giuoco")).toBeInTheDocument();
    expect(screen.getByText("Scandinavian: Mieses")).toBeInTheDocument();
    expect(screen.getByText("2 positions · 0 blunder · 0 repertoire · 2 shared")).toBeInTheDocument();
    const cards = screen.getAllByTestId("position-card");
    expect(cards).toHaveLength(3); // one node card, two positions
    expect(within(cards[0]).getByTestId("replies-line").textContent).toBe("after your Nc3, they play: Bb4 ×4 · Bc5 ×2 · +1 more");
    const nodeArrows = JSON.parse(within(cards[0]).getByTestId(/^board-pc/).getAttribute("data-arrows") ?? "[]") as Array<{ color: string }>;
    expect(nodeArrows).toHaveLength(3);
    expect(nodeArrows.some((a) => a.color === ARROWS.engine || a.color === ARROWS.played)).toBe(false);
    expect(cards[1].textContent).toContain("Me 3× · Them 7×");
    expect(screen.queryByRole("combobox", { name: "Opponent" })).not.toBeInTheDocument(); // one profile: no select
    // the drill-down behind a line
    fireEvent.click(screen.getByText("Italian Game: Giuoco"));
    const panel = await screen.findByTestId("line-games");
    expect(panel.textContent).toContain("Italian Game");
    fireEvent.click(within(panel).getByText("Close"));
    expect(screen.queryByTestId("line-games")).not.toBeInTheDocument();
  });

  it("adds an opponent through the form and refetches; a taken name shows the reason", async () => {
    const state = { profiles: [profile()], positions: [], nodes: [], dismissed: [] as string[] };
    const calls = stubFetch(routes(state));
    renderPage();
    await screen.findByLabelText("Activity");
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    const form = screen.getByRole("form", { name: "Add opponent" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Taken" } });
    fireEvent.change(within(form).getByLabelText("Lichess handle"), { target: { value: "x" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already have an opponent");
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Caruana" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Opponent" })).toHaveValue("9"));
    const post = calls.find((c) => c.path === "/scout/profiles" && c.method === "POST" && c.body?.name === "Caruana");
    expect(post?.body).toEqual({ name: "Caruana", lichess_username: "x" });
    expect(screen.getByText(/importing on the next run/)).toBeInTheDocument();
    expect(screen.getByText(/imported on the next hourly run/)).toBeInTheDocument();
  });

  it("removing confirms first, then refetches the profiles", async () => {
    const state = { profiles: [profile(), profile({ id: 2, name: "Nepo" })], positions: [], nodes: [], dismissed: [] as string[] };
    const calls = stubFetch(routes(state));
    renderPage();
    await screen.findByLabelText("Activity");
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    expect(screen.getByText("Remove Giri and their 12 games from Scout?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove" }));
    await waitFor(() => expect(screen.queryByText("Remove Giri and their 12 games from Scout?")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.method === "DELETE" && c.path === "/scout/profiles/1")).toHaveLength(1);
    await waitFor(() => expect(screen.queryByRole("combobox", { name: "Opponent" })).not.toBeInTheDocument());
  });

  it("dismisses a board from the list and restores it from the Dismissed panel", async () => {
    const state = { profiles: [profile()], positions: [position(), position({ fen: P8 })], nodes: [], dismissed: [] as string[] };
    const calls = stubFetch(routes(state));
    renderPage();
    await screen.findByLabelText("Activity");
    expect(await screen.findAllByTestId("position-card")).toHaveLength(2);
    fireEvent.click(within(screen.getAllByTestId("position-card")[1]).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.getAllByTestId("position-card")).toHaveLength(1));
    expect(calls.find((c) => c.path === "/scout/dismiss")?.body).toEqual({ fen: P8 });
    const panel = screen.getByTestId("dismissed-panel");
    expect(panel.textContent).toContain("Dismissed boards (1)");
    fireEvent.click(within(panel).getByRole("button", { name: /Dismissed boards/ }));
    fireEvent.click(within(panel).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(screen.getAllByTestId("position-card")).toHaveLength(2));
    expect(calls.filter((c) => c.path === "/scout/dismiss" && c.method === "DELETE")).toHaveLength(1);
    expect(panel.textContent).toContain("Dismissed boards (0)");
  });

  it("with no opponents the add form is open", async () => {
    const state = { profiles: [], positions: [], nodes: [], dismissed: [] as string[] };
    stubFetch(routes(state));
    renderPage();
    expect(await screen.findByText("No opponents yet — add one to scout.")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Add opponent" })).toBeInTheDocument();
  });
});
