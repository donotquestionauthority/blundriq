/** Types and pure helpers for the Games page (kept out of the component file for fast refresh). */
import { api } from "./api";

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


// --- the per-game review ------------------------------------------------------------------------

export type ReviewMode = "learn" | "review";

/** One position's stored analysis; `eval` is White-POV centipawns, mate ±10000. */
export interface PlyAnalysisEntry {
  ply: number;
  eval: number | null;
  best_move: string | null;
  best_line: string | null;
}

export interface ReviewBlunder {
  ply: number;
  fen: string;
  move_played: string | null;
  best_move: string | null;
  best_line: string | null;
  post_blunder_line: string | null;
  cp_loss: number | null;
  classification: string | null;
  phase: string | null;
  /** Distinct analysable games at this board, this one included. */
  fen_occurrence_count: number;
}

export type RepertoireStatus = "match" | "agree" | "end_of_line" | "conflict" | "unreadable" | "none" | "not_your_turn";

export interface RepertoireConflictGroup {
  move: string;
  book: string | null;
  chapter: string | null;
  line_name: string | null;
  more_lines: number;
}

export interface RepertoireEntry {
  status: RepertoireStatus;
  /** Non-null exactly when the status is `match` or `agree`. */
  book_move: string | null;
  book: string | null;
  chapter: string | null;
  line_name: string | null;
  line_ply: number | null;
  plan: string[];
  more_lines: number;
  transposed: boolean | null;
  conflict: RepertoireConflictGroup[] | null;
}

/** `null` means not computed (no active repertoire of this colour, or a failure); a populated map
 *  has an entry at every ply, so coverage is read from `status`, never from a key's presence. */
export interface RepertoireProjection {
  by_ply: Record<string, RepertoireEntry>;
}

export interface ReviewGame {
  id: number;
  url: string | null;
  source: "chesscom" | "lichess";
  played_at: string | null;
  time_control: string | null;
  time_class: string | null;
  opening_name: string | null;
  opening_eco: string | null;
  termination: string | null;
  variant: "standard" | "chess960";
  starting_fen: string | null;
  moves: string[] | null;
  fen_sequence: string[] | null;
  analysis_status: string;
  analysis_depth: number | null;
  ply_analysis: PlyAnalysisEntry[] | null;
  ply_analysis_depth: number | null;
  analyzed: boolean;
  player_color: "white" | "black";
  opponent_username: string | null;
  opponent_rating: number | null;
  player_rating: number | null;
  result: "win" | "loss" | "draw" | null;
  reviewed_at: string | null;
  /** The moves are no longer stored (housekeeping): the page shows a card, not a board. */
  out_of_window: boolean;
}

export interface GameReviewResponse {
  game: ReviewGame;
  blunders: ReviewBlunder[];
  repertoire: RepertoireProjection | null;
}

export const getGameReview = (gameId: number) => api.get<GameReviewResponse>(`/games/${gameId}/review`);
export const markGameReviewed = (gameId: number) => api.post<unknown>(`/games/${gameId}/reviewed`);

export interface LearnCommitBody {
  attempt_id: string;
  ply: number;
  committed_move: string;
  /** Present exactly when the rep was timed. */
  elapsed_ms?: number;
}
export const learnCommit = (gameId: number, body: LearnCommitBody) => api.post<{ id: number; created: boolean }>(`/games/${gameId}/learn-commit`, body);

export interface ReviewPrefs {
  review_default_mode: ReviewMode;
  review_show_timer: boolean;
}

const asMode = (v: unknown): ReviewMode => (v === "review" ? "review" : "learn");

/** The two Review fields of the settings row; a malformed value reads as the non-spoiling default. */
export const getReviewPrefs = () => api.get<Record<string, unknown>>("/settings").then((s) => ({ review_default_mode: asMode(s.review_default_mode), review_show_timer: s.review_show_timer !== false }));

/** Settings are one row: read it, change the field, write it back. */
export const setReviewPref = (patch: Partial<ReviewPrefs>) => api.get<Record<string, unknown>>("/settings").then((s) => api.put<Record<string, unknown>>("/settings", { ...s, ...patch }));
