/** Types, options and calls for the Deviations page. */
import { api } from "./api";
import type { TimeClass } from "./blunders";
import type { PositionCardData } from "./components/PositionCard/types";
import type { RepLine } from "./repertoire";

export interface DeviationGame {
  chess_game_id: number;
  game_url: string | null;
  played_at: string | null;
  result: "win" | "loss" | "draw" | null;
  played_move: string | null;
  expected_move: string;
  ply: number;
}

/** A pattern: a book, a chapter, the ply and the move the line expected there. */
export interface DeviationPattern {
  book_id: number;
  chapter_id: number;
  ply: number;
  expected_move: string;
  book: string;
  chapter: string;
  color: "white" | "black";
  count: number;
  wins: number;
  losses: number;
  draws: number;
  win_pct: number;
  is_new: boolean;
  last_seen: string | null;
  most_common_played: string | null;
  deviation_fen: string | null;
  chess_game_id: number | null;
  moves: string[] | null;
  line_names: string[] | null;
  rep_lines: RepLine[];
  /** The one move the repertoire agrees on at the board, or null when its lines disagree. */
  rep_expected_move: string | null;
  games: DeviationGame[];
}

/** A pattern key as the server names it: [book_id, chapter_id, ply, expected_move]. */
export type PatternKey = [number, number, number, string];

export interface DeviationsResponse {
  positions: DeviationPattern[];
  total: number;
  new_count: number;
  to_acknowledge: PatternKey[];
  page: number;
  page_size: number;
  total_pages: number;
}

export interface DeviationFilters {
  since_days: number | null;
  last_n_games: number; // > 0 wins over since_days
  min_occurrences: number;
  time_class: TimeClass;
  color: "white" | "black" | null;
}

export function buildQuery(f: DeviationFilters, page: number): string {
  const q = new URLSearchParams();
  if (f.last_n_games > 0) q.set("last_n_games", String(f.last_n_games));
  else if (f.since_days) q.set("since_days", String(f.since_days));
  q.set("min_occurrences", String(f.min_occurrences));
  q.set("time_class", f.time_class);
  if (f.color) q.set("color", f.color);
  q.set("page", String(page));
  return q.toString();
}

/** The page's opening filters, from the settings row (core/deviations.py `default_filters` is
 *  the same recipe server-side, so Home's count is over the list this page opens on). */
export function defaultFilters(s: Record<string, unknown>): DeviationFilters {
  const num = (k: string, d: number) => (typeof s[k] === "number" ? (s[k] as number) : d);
  const byGames = s.deviations_default_filter_mode === "games";
  return {
    since_days: byGames ? null : num("deviations_default_window_days", 20),
    last_n_games: byGames ? num("deviations_default_last_n_games", 500) : 0,
    min_occurrences: num("deviations_default_min_occurrences", 2),
    time_class: "focus",
    color: null,
  };
}

/** A pattern as the shared position card. The expected move is the pipeline's matched move
 *  (slot 2 of the arrow precedence); the repertoire's own reading of the board is slot 3. No
 *  explanation panel: a deviation is not a blunder row. */
export function toCard(p: DeviationPattern): PositionCardData {
  return {
    fen: p.deviation_fen ?? "start",
    color: p.color,
    times: p.count,
    book: p.book,
    chapter: p.chapter,
    lineNames: p.line_names,
    movePlayed: null,
    mostCommonPlayed: p.most_common_played,
    expectedMove: p.expected_move,
    repExpectedMove: p.rep_expected_move,
    repLines: p.rep_lines.length ? p.rep_lines : null,
    moves: p.moves,
    ply: p.ply,
    lastSeen: p.last_seen,
    isNew: p.is_new,
    record: { wins: p.wins, losses: p.losses, draws: p.draws, win_pct: p.win_pct },
    games: p.games.map((g) => ({ game_url: g.game_url, played_at: g.played_at, result: g.result, move_played: g.played_move, best_move: g.expected_move })),
  };
}

export const getDeviations = (f: DeviationFilters, page: number) => api.get<DeviationsResponse>(`/deviations?${buildQuery(f, page)}`);
/** The page has shown a response (its `to_acknowledge`, possibly empty). */
export const markSeen = (patterns: PatternKey[]) => api.post<{ seen_at: string }>("/deviations/seen", { patterns });
