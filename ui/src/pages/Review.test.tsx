import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Review from "./Review";
import ReviewPosition from "./ReviewPosition";
import { ApiError } from "../api";
import { returnTarget } from "../utils/returnTo";
import { lineText, whyThisGame } from "../review";
import type { HabitGame, PositionPage, ReviewHabit, ReviewPage, ReviewPosition as Position } from "../review";

// The network calls are mocked; the labels and helpers are the real ones.
const getReviewPage = vi.fn();
const getHabitGames = vi.fn();
const getPositionPage = vi.fn();
vi.mock("../review", async () => {
  const actual = await vi.importActual<typeof import("../review")>("../review");
  return {
    ...actual,
    getReviewPage: (...args: unknown[]) => getReviewPage(...args),
    getHabitGames: (...args: unknown[]) => getHabitGames(...args),
    getPositionPage: (...args: unknown[]) => getPositionPage(...args),
  };
});
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string; boardOrientation?: string } }) => <div data-testid={`board-${options.id}`} data-fen={options.position} data-orientation={options.boardOrientation} />,
}));

// A key past 2^53: JavaScript numbers would round it.
const BIG = "-9007199254740993123";

const position = (over: Partial<Position> = {}): Position => ({
  colour: "black",
  key: "4242",
  line_san: ["d4", "d5"],
  fen: "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2",
  last_move: "d7d5",
  n: 181,
  score: 0.42,
  expected: 0.5,
  current_score: 0.44,
  current_expected: 0.5,
  long_deficit: 0.085,
  current_deficit: 0.06,
  leak_per_month: 3.28,
  recent_games: 70,
  status: "still_leaking",
  es_at_node: 47,
  trend: [8, null, -3, 12],
  parent_key: null,
  ...over,
});

const habit = (over: Partial<ReviewHabit> = {}): ReviewHabit => ({
  id: "missed:fork",
  label: "Missed a fork",
  events: 12,
  games: 11,
  rate_per_100: 10.4,
  current_rate_per_100: 14,
  points_per_month: 3.4,
  trend: "worse",
  practice_theme: "fork",
  ...over,
});

const page = (over: Partial<ReviewPage> = {}): ReviewPage => ({
  positions: {
    ranked: [position(), position({ key: BIG, line_san: ["d4", "d5", "c4", "c6"], parent_key: "4242", leak_per_month: 2.07, status: "new_leak", es_at_node: 35 })],
    fixed: [position({ key: "77", colour: "white", line_san: ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3"], status: "looks_fixed", leak_per_month: -0.1, current_score: 0.6 })],
  },
  habits: [habit(), habit({ id: "lost:middlegame", label: "Lost material in the middlegame", practice_theme: null, trend: null })],
  lost_wins: {
    games: Array.from({ length: 53 }, (_, i) => ({ chess_game_id: 300 + i, played_at: "2026-09-01T12:00:00Z", opponent_username: `opp${i}`, opponent_rating: 1400, result: "loss", time_class: "rapid", url: null, reviewed: false, peak_es: 81, anchor_ply: 33, anchor_move: "17…Qb6", cost: 22 })),
    total: 53,
  },
  filter: {
    time_class: "focus",
    opening: "__all__",
    openings: [
      { key: "black:Scandinavian Defense", label: "Scandinavian Defense · Black", games: 2003 },
      { key: "white:Italian Game", label: "Italian Game · White", games: 1057 },
    ],
  },
  meta: { as_of: "2026-10-04T00:00:00Z", history_months: 12, games_counted: 6000, games_without_prefix: 0, games_without_ratings: 3, window_games: 1000 },
  ...over,
});

const habitGames = (): { rows: HabitGame[]; total: number; page: number; page_size: number; total_pages: number } => ({
  rows: [{ chess_game_id: 901, played_at: "2026-09-30T12:00:00Z", opponent_username: "forky", opponent_rating: 1500, result: "loss", anchor_ply: 33, anchor_move: "17…Qb6", cost: 31, reviewed: false }],
  total: 1,
  page: 1,
  page_size: 50,
  total_pages: 1,
});

const positionPage = (over: Partial<PositionPage> = {}): PositionPage => ({
  node: { ...position({ key: BIG }), rob_to_move: false, ply: 2 },
  children: [
    { san: "c4", key: "-12", n: 60, score: 0.4, expected: 0.5 },
    { san: "Nf3", key: "13", n: 30, score: 0.55, expected: 0.5 },
  ],
  games: {
    rows: [
      { chess_game_id: 501, ply: 2, played_at: "2026-09-30T12:00:00Z", time_class: "rapid", opponent_username: "dfour", opponent_rating: 1500, result: "loss", reviewed: false, es_on_arrival: 47, turning_state: "found", turning_ply: 33, turning_move: "17…Qb6", turning_cost: 22 },
      { chess_game_id: 502, ply: 2, played_at: "2026-09-29T12:00:00Z", time_class: "rapid", opponent_username: "quiet", opponent_rating: 1500, result: "draw", reviewed: true, es_on_arrival: 50, turning_state: "none", turning_ply: null, turning_move: null, turning_cost: null },
    ],
    total: 2,
    page: 1,
    page_size: 50,
    total_pages: 1,
  },
  older_games: 212,
  ...over,
});

/** A game review stand-in with the review's two ways back. */
function GameStub() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div>
      <span data-testid="game-at">{location.pathname + location.search}</span>
      <button type="button" onClick={() => navigate(-1)}>
        browser back
      </button>
      <button
        type="button"
        onClick={() => {
          const back = returnTarget(location.state);
          navigate(back.to, back.state != null ? { state: back.state } : undefined);
        }}
      >
        close
      </button>
      <Link to="/review">top bar Review</Link>
    </div>
  );
}

function Where() {
  const location = useLocation();
  return <span data-testid="where">{location.pathname + location.search}</span>;
}

function Chrome() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(-1)}>
        page back
      </button>
      <Link to="/review">page top bar</Link>
    </>
  );
}

type Entry = string | { pathname: string; search?: string; state?: unknown };
const renderApp = (entry: Entry | Entry[] = "/review") => {
  const entries = Array.isArray(entry) ? entry : [entry];
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
      <Routes>
        <Route
          path="/review"
          element={
            <>
              <Review />
              <Where />
              <Chrome />
            </>
          }
        />
        <Route
          path="/review/positions/:colour/:key"
          element={
            <>
              <ReviewPosition />
              <Where />
            </>
          }
        />
        <Route path="/review/:gameId" element={<GameStub />} />
      </Routes>
    </MemoryRouter>,
  );
};
const where = () => screen.getByTestId("where").textContent;
const combo = (name: string) => screen.getByRole("combobox", { name }) as HTMLSelectElement;
const lastPageCall = () => getReviewPage.mock.calls.at(-1);

beforeEach(() => {
  getReviewPage.mockReset().mockResolvedValue(page());
  getHabitGames.mockReset().mockResolvedValue(habitGames());
  getPositionPage.mockReset().mockResolvedValue(positionPage());
});
afterEach(cleanup);

describe("Review page", () => {
  it("leads with the positions costing points, each a card with its line, numbers, status, engine word, trend and breadcrumb", async () => {
    renderApp();
    const cards = await screen.findAllByTestId("position-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("1.d4 d5");
    expect(cards[0]).toHaveTextContent("181 games · 42% (expected 50%) · ≈3.3 points a month");
    expect(cards[0]).toHaveTextContent("Still leaking");
    expect(cards[0]).toHaveTextContent("Fine when you get here");
    expect(within(cards[0]).getAllByTestId("trend")[0].querySelectorAll("[data-bar]")).toHaveLength(4);
    expect(within(cards[0]).getAllByTestId("trend")[0].querySelectorAll('[data-bar="empty"]')).toHaveLength(1);
    expect(cards[1]).toHaveTextContent("Inside 1.d4 d5");
    expect(cards[1]).toHaveTextContent("Already worse when you get here");
    // Keys stay strings all the way into the URL.
    expect(cards[1].getAttribute("href")).toBe(`/review/positions/black/${BIG}`);
    expect(screen.getByText(/Your last 12 months prove a leak/)).toBeInTheDocument();
    // Fixed? and Lost wins start collapsed; the habits start open.
    expect(screen.getByRole("button", { name: /Fixed\?/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: /Lost wins/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("From your last 1,000 analysed games.")).toBeInTheDocument();
  });

  it("Fixed? shows the history beside now", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /Fixed\?/ }));
    const card = screen.getAllByTestId("position-card").find((c) => c.getAttribute("data-key") === "77");
    expect(card).toHaveTextContent("12 months: 42% (expected 50%) · now: 60% (expected 50%)");
    expect(card).toHaveTextContent("Looks fixed");
  });

  it("a habit row shows its rate, trend and cost, links Practice only for a served theme, and expands to its games", async () => {
    renderApp();
    const rows = await screen.findAllByTestId("habit");
    expect(rows[0]).toHaveTextContent("Missed a fork");
    expect(rows[0]).toHaveTextContent("10.4 per 100 games");
    expect(rows[0]).toHaveTextContent("Getting worse");
    expect(within(rows[0]).getByRole("link", { name: "Drill this" })).toHaveAttribute("href", "/practice?type=motif&subtype=fork");
    expect(within(rows[1]).queryByRole("link", { name: "Drill this" })).toBeNull();
    fireEvent.click(within(rows[0]).getByRole("button", { name: /Missed a fork/ }));
    expect(await within(rows[0]).findByText("forky")).toBeInTheDocument();
    expect(getHabitGames).toHaveBeenCalledWith("missed:fork", "focus", "__all__", 1);
    expect(within(rows[0]).getByRole("link", { name: "Review →" })).toHaveAttribute("href", "/review/901?ply=33");
  });

  it("Lost wins shows fifty at a time, each with why it was lost", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /Lost wins/ }));
    expect(screen.getAllByText("Winning (ES 81), turned at 17…Qb6 (−22), lost.")).toHaveLength(50);
    fireEvent.click(screen.getByRole("button", { name: "Show 3 more (3 remaining)" }));
    expect(screen.getAllByText("Winning (ES 81), turned at 17…Qb6 (−22), lost.")).toHaveLength(53);
  });

  it("explains an empty page by the games still waiting for their opening moves", async () => {
    getReviewPage.mockResolvedValue(page({ positions: { ranked: [], fixed: [] }, meta: { ...page().meta, games_counted: 0, games_without_prefix: 812 } }));
    renderApp();
    expect(await screen.findByText(/812 games still need their opening moves fetched/)).toBeInTheDocument();
  });

  it("the opening filter lists colour-labelled openings and refetches; the time class too", async () => {
    renderApp();
    await screen.findAllByTestId("position-card");
    expect([...combo("Opening").options].map((o) => o.textContent)).toEqual(["All openings", "Scandinavian Defense · Black (2003)", "Italian Game · White (1057)"]);
    fireEvent.change(combo("Opening"), { target: { value: "black:Scandinavian Defense" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["focus", "black:Scandinavian Defense"]));
    expect(where()).toBe("/review?opening=black%3AScandinavian+Defense");
    fireEvent.change(combo("Time class"), { target: { value: "all" } });
    await waitFor(() => expect(lastPageCall()).toEqual(["all", "black:Scandinavian Defense"]));
  });

  it("recovers a stale-opening 422 by dropping it from the URL with a one-line notice", async () => {
    getReviewPage.mockImplementation((_tc: string, opening: string) => (opening === "__all__" ? Promise.resolve(page()) : Promise.reject(new ApiError(422, "unknown opening key: 'white:Gone'"))));
    renderApp("/review?tc=all&opening=white%3AGone");
    expect(await screen.findByRole("status")).toHaveTextContent("That opening no longer has enough games");
    await waitFor(() => expect(where()).toBe("/review?tc=all"));
  });

  it("does not launder any other error into a stale-opening reset", async () => {
    getReviewPage.mockRejectedValue(new ApiError(500, "boom"));
    renderApp("/review?opening=white%3AItalian+Game");
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(where()).toBe("/review?opening=white%3AItalian+Game");
  });

  it("a notice from a recovery does not outlive a top-bar visit with the same settings", async () => {
    getReviewPage.mockImplementation((_tc: string, opening: string) => (opening === "__all__" ? Promise.resolve(page()) : Promise.reject(new ApiError(422, "unknown opening key: 'x'"))));
    renderApp("/review?opening=white%3AGone");
    await screen.findByRole("status");
    fireEvent.click(screen.getByRole("link", { name: "page top bar" }));
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });
});

describe("Review keeps its settings in the URL and its expansion in the entry", () => {
  it("a setting change replaces the entry, defaults left out; Reset filters returns to them", async () => {
    renderApp(["/elsewhere", "/review"]);
    await screen.findAllByTestId("position-card");
    fireEvent.change(combo("Time class"), { target: { value: "all" } });
    await waitFor(() => expect(where()).toBe("/review?tc=all"));
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    await waitFor(() => expect(where()).toBe("/review"));
    expect(screen.queryByRole("button", { name: "Reset filters" })).toBeNull();
  });

  it("an unknown time class is the default", async () => {
    renderApp("/review?tc=constructor");
    await screen.findAllByTestId("position-card");
    expect(lastPageCall()).toEqual(["focus", "__all__"]);
  });

  it.each(["close", "browser back"])("%s from a game returns to the same settings, the same habit open and its games reloaded", async (way) => {
    renderApp("/review?tc=all");
    const rows = await screen.findAllByTestId("habit");
    fireEvent.click(screen.getByRole("button", { name: /Lost wins/ }));
    fireEvent.click(within(rows[0]).getByRole("button", { name: /Missed a fork/ }));
    fireEvent.click(await within(rows[0]).findByRole("link", { name: "Review →" }));
    expect(screen.getByTestId("game-at")).toHaveTextContent("/review/901?ply=33");
    fireEvent.click(screen.getByRole("button", { name: way }));
    const back = await screen.findAllByTestId("habit");
    expect(where()).toBe("/review?tc=all");
    expect(within(back[0]).getByRole("button", { name: /Missed a fork/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Lost wins/ })).toHaveAttribute("aria-expanded", "true");
    expect(await within(back[0]).findByText("forky")).toBeInTheDocument();
  });

  it("a snapshot taken under other settings is ignored", async () => {
    renderApp({ pathname: "/review", search: "?tc=all", state: { open: { sections: ["lost"], habits: [], key: "focus|__all__" } } });
    await screen.findAllByTestId("position-card");
    expect(screen.getByRole("button", { name: /Lost wins/ })).toHaveAttribute("aria-expanded", "false");
  });
});

describe("A position's page", () => {
  it("opens from a card with the page's settings, and its Back returns to the page as it was", async () => {
    renderApp("/review?tc=all");
    fireEvent.click(await screen.findByRole("button", { name: /Lost wins/ }));
    fireEvent.click(screen.getAllByTestId("position-card")[1]);
    await screen.findByText("What happens next");
    expect(where()).toBe(`/review/positions/black/${BIG}?tc=all`);
    expect(getPositionPage).toHaveBeenCalledWith("black", BIG, "all", "__all__", 1);
    fireEvent.click(screen.getByRole("button", { name: "← Back" }));
    await screen.findAllByTestId("position-card");
    expect(where()).toBe("/review?tc=all");
    expect(screen.getByRole("button", { name: /Lost wins/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("shows the explorer, each game with why it is worth opening, the older games, and links that carry the way back", async () => {
    renderApp({ pathname: `/review/positions/black/${BIG}`, state: { from: { pathname: "/review", search: "?tc=all" } } });
    await screen.findByText("What happens next");
    expect(screen.getByText("Their replies from here.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "c4" })).toHaveAttribute("href", "/review/positions/black/-12");
    expect(screen.getByText("Fine when you got here (ES 47), turned at 17…Qb6 (−22), lost.")).toBeInTheDocument();
    expect(screen.getByText("Fine when you got here (ES 50), no single turning point, drew.")).toBeInTheDocument();
    expect(screen.getByText("Plus 212 older games in the numbers above.")).toBeInTheDocument();
    const games = screen.getAllByTestId("position-game");
    expect(within(games[0]).getByRole("link", { name: "Review →" })).toHaveAttribute("href", "/review/501?ply=33");
    expect(within(games[0]).getByRole("link", { name: "From here" })).toHaveAttribute("href", "/review/501?ply=2");
    expect(within(games[1]).getByRole("link", { name: "Review →" })).toHaveAttribute("href", "/review/502?ply=2");
    // A game's Close comes back here; this page's Back still goes where it was opened from.
    fireEvent.click(within(games[0]).getByRole("link", { name: "Review →" }));
    fireEvent.click(screen.getByRole("button", { name: "close" }));
    await screen.findByText("What happens next");
    expect(where()).toBe(`/review/positions/black/${BIG}`);
    fireEvent.click(screen.getByRole("button", { name: "← Back" }));
    await screen.findAllByTestId("position-card");
    expect(where()).toBe("/review?tc=all");
  });

  it("a stale focused opening drops to all openings with a notice", async () => {
    getPositionPage.mockImplementation((_c: string, _k: string, _tc: string, opening: string) => (opening === "__all__" ? Promise.resolve(positionPage()) : Promise.reject(new ApiError(422, "unknown opening key: 'white:Gone'"))));
    renderApp(`/review/positions/black/${BIG}?tc=all&opening=white%3AGone`);
    expect(await screen.findByRole("status")).toHaveTextContent("That opening no longer has enough games");
    await waitFor(() => expect(where()).toBe(`/review/positions/black/${BIG}?tc=all`));
    expect(await screen.findByText("What happens next")).toBeInTheDocument();
  });

  it("a stale-opening answer that lands after Back leaves the page Rob went back to alone", async () => {
    let reject: (e: unknown) => void = () => {};
    getPositionPage.mockImplementation(() => new Promise((_resolve, rej) => (reject = rej)));
    renderApp(["/review?tc=all", { pathname: `/review/positions/black/${BIG}`, search: "?tc=all&opening=white%3AGone", state: { from: { pathname: "/review", search: "?tc=all" } } }]);
    await waitFor(() => expect(getPositionPage).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "← Back" }));
    await screen.findAllByTestId("position-card");
    reject(new ApiError(422, "unknown opening key: 'white:Gone'"));
    await new Promise((r) => setTimeout(r, 20));
    expect(where()).toBe("/review?tc=all");
  });

  it("a position no game reaches says so", async () => {
    getPositionPage.mockRejectedValue(new ApiError(404, "no counted game reaches this position"));
    renderApp(`/review/positions/white/${BIG}`);
    expect(await screen.findByRole("alert")).toHaveTextContent("None of your games under this filter reach this position.");
  });
});

describe("Review helpers", () => {
  it("number a line and say why a game matters", () => {
    expect(lineText(["e4", "d5", "exd5"])).toBe("1.e4 d5 2.exd5");
    expect(whyThisGame({ ...positionPage().games.rows[0], es_on_arrival: null, turning_state: "not_analysed" })).toBe("Not analysed yet, lost.");
    expect(whyThisGame({ ...positionPage().games.rows[0], turning_move: null })).toBe("Fine when you got here (ES 47), turned (−22), lost.");
    expect(whyThisGame({ ...positionPage().games.rows[0], es_on_arrival: 31 })).toBe("Already worse when you got here (ES 31), turned at 17…Qb6 (−22), lost.");
  });
});
