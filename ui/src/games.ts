/** Types and pure helpers for the Games page (kept out of the component file for fast refresh). */

export type Game = {
  id: number;
  url: string | null;
  source: "chesscom" | "lichess";
  played_at: string | null;
  variant: "standard" | "chess960";
  player_color: "white" | "black";
  opponent_username: string | null;
  opponent_rating: number | null;
  player_rating: number | null;
  result: "win" | "loss" | "draw";
  time_control: string | null;
  time_class: string | null;
  opening_name: string | null;
  opening_eco: string | null;
  termination: string | null;
  analyzed: boolean;
  moves: string[] | null;
  deviated_at_ply: number | null;
  deviation_by: "me" | "opponent" | "none" | null;
  expected_move: string | null;
  played_move: string | null;
  book_title: string | null;
  chapter_title: string | null;
  line_name: string | null;
  issue_count: number;
  miss_count: number;
  blunder_count: number;
  mistake_count: number;
  inaccuracy_count: number;
};
export type Summary = { total: number; wins: number; losses: number; draws: number; win_pct: number; pages: number };
export type Filters = {
  since_days: number | null;
  last_n_games: number;
  color: string;
  result: string;
  platform: string;
  variant: string;
  book: string;
  chapter: string;
  deviation: string;
  opponent: string;
};
export type FilterValues = { books: Array<{ id: number; title: string; color: string }>; chapters: Array<{ id: number; title: string; book_id: number }> };
export type SortKey = "played_at" | "result" | "opponent_rating" | "player_rating" | "issue_count" | "deviated_at_ply";

export const DAY_OPTIONS = [7, 10, 20, 30, 60, 90, 180, 365];
export const LAST_N_OPTIONS = [50, 100, 250, 500, 1000];
// Column keys as they appear in the games_columns setting; unknown keys are ignored.
export const ALL_COLUMNS = ["Date", "Opening", "Opponent", "Color", "Result", "Repertoire", "Section", "Deviation", "My Rating", "Opp Rating", "Issues", "Platform", "Variant", "Link"] as const;

export function buildQuery(f: Filters, page: number): string {
  const q = new URLSearchParams();
  if (f.last_n_games > 0) q.set("last_n_games", String(f.last_n_games));
  else if (f.since_days) q.set("since_days", String(f.since_days));
  for (const k of ["color", "result", "platform", "variant", "book", "chapter", "deviation", "opponent"] as const) {
    if (f[k]) q.set(k, f[k]);
  }
  q.set("page", String(page));
  return q.toString();
}

export function sortGames(games: Game[], key: SortKey, dir: "asc" | "desc"): Game[] {
  const order = { win: 0, draw: 1, loss: 2 } as const;
  const val = (g: Game): number | string => {
    if (key === "result") return order[g.result];
    if (key === "played_at") return g.played_at ?? "";
    return g[key] ?? -1;
  };
  return [...games].sort((a, b) => {
    const x = val(a);
    const y = val(b);
    const c = x < y ? -1 : x > y ? 1 : 0;
    return dir === "asc" ? c : -c;
  });
}

export function pgnOf(moves: string[] | null): string {
  if (!moves || moves.length === 0) return "";
  return moves.map((m, i) => (i % 2 === 0 ? `${i / 2 + 1}. ${m}` : m)).join(" ");
}

