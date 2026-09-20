import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import Games from "./Games";
import { buildQuery, pgnOf, sortGames, type Game } from "../games";

const game = (over: Partial<Game>): Game => ({
  id: 1,
  url: "https://lichess.org/abc",
  source: "lichess",
  played_at: "2026-09-01T10:00:00+00:00",
  variant: "standard",
  player_color: "black",
  opponent_username: "opp",
  opponent_rating: 1500,
  player_rating: 1520,
  result: "win",
  time_control: "600+0",
  time_class: "rapid",
  opening_name: "Scandinavian Defense",
  opening_eco: "B01",
  termination: "resignation",
  analyzed: true,
  moves: ["e4", "d5", "exd5"],
  deviated_at_ply: 3,
  deviation_by: "me",
  expected_move: "Qxd5",
  played_move: "Nf6",
  book_title: "Scandi",
  chapter_title: "Main",
  line_name: "Qa5",
  issue_count: 2,
  miss_count: 1,
  blunder_count: 1,
  mistake_count: 0,
  inaccuracy_count: 0,
  ...over,
});

describe("Games helpers", () => {
  it("builds the query from filters", () => {
    const base = { since_days: 60, last_n_games: 0, color: "", result: "win", platform: "", variant: "", book: "", chapter: "", deviation: "", opponent: "" };
    expect(buildQuery(base, 2)).toBe("since_days=60&result=win&page=2");
    expect(buildQuery({ ...base, last_n_games: 100 }, 1)).toBe("last_n_games=100&result=win&page=1");
  });
  it("sorts by result order and by numeric fields", () => {
    const gs = [game({ id: 1, result: "loss", issue_count: 3 }), game({ id: 2, result: "win", issue_count: 0 }), game({ id: 3, result: "draw", issue_count: 1 })];
    expect(sortGames(gs, "result", "asc").map((g) => g.id)).toEqual([2, 3, 1]);
    expect(sortGames(gs, "issue_count", "desc").map((g) => g.id)).toEqual([1, 3, 2]);
  });
  it("renders PGN move numbers", () => {
    expect(pgnOf(["e4", "d5", "exd5"])).toBe("1. e4 d5 2. exd5");
    expect(pgnOf(null)).toBe("");
  });
});

describe("Games page", () => {
  it("ignores a slow earlier response that resolves after a newer request", async () => {
    const resolvers: Array<(v: unknown) => void> = [];
    const base = {
      "/settings": { games_columns: ["Opponent"], games_default_window_days: 30 },
      "/games/filters": { books: [], chapters: [] },
    } as Record<string, unknown>;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const path = url.replace(/^.*\/api/, "").split("?")[0];
        if (path !== "/games") return Promise.resolve({ ok: true, status: 200, json: async () => base[path] });
        return new Promise((resolve) => {
          resolvers.push((body) => resolve({ ok: true, status: 200, json: async () => body }));
        });
      }),
    );
    render(<Games />);
    await screen.findByLabelText("Opponent");
    fireEvent.change(screen.getByLabelText("Opponent"), { target: { value: "zz" } });
    await vi.waitFor(() => expect(resolvers).toHaveLength(2));
    const summary = { total: 1, wins: 1, losses: 0, draws: 0, win_pct: 100, pages: 1 };
    resolvers[1]({ games: [game({ opponent_username: "newest" })], summary });
    expect(await screen.findByText("newest")).toBeInTheDocument();
    resolvers[0]({ games: [game({ opponent_username: "stale" })], summary });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
    expect(screen.getByText("newest")).toBeInTheDocument();
  });

  it("shows the settings-driven columns and expands a row", async () => {
    const responses: Record<string, unknown> = {
      "/settings": { games_columns: ["Date", "Opponent", "Result", "Deviation", "Issues"], games_default_window_days: 30 },
      "/games/filters": { books: [{ id: 1, title: "Scandi", color: "black" }], chapters: [{ id: 1, title: "Main", book_id: 1 }] },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const path = url.replace(/^.*\/api/, "").split("?")[0];
        const body = path === "/games" ? { games: [game({})], summary: { total: 1, wins: 1, losses: 0, draws: 0, win_pct: 100, pages: 1 } } : responses[path];
        return { ok: true, status: 200, json: async () => body };
      }),
    );
    render(<Games />);
    expect(await screen.findByText("opp")).toBeInTheDocument();
    expect(screen.getByText("1 games · 1W 0L 0D · 100% wins")).toBeInTheDocument();
    expect(screen.getByText(/I deviated · ply 3/)).toBeInTheDocument();
    expect(screen.queryByText("Scandinavian Defense")).not.toBeInTheDocument(); // Opening column hidden by the setting
    fireEvent.click(screen.getByText("opp"));
    expect(screen.getByText("1. e4 d5 2. exd5")).toBeInTheDocument();
    expect(screen.getByText(/expected/)).toBeInTheDocument();
    const called = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(called.some((u) => u.includes("/games?since_days=30&page=1"))).toBe(true);
  });
});
