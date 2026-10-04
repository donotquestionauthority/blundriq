/** Types and calls for the Review page and a position's page. Shapes follow core/review/read.py,
 *  core/review/position.py and core/review/habits.py. Position keys are 64-bit integers and travel
 *  as decimal strings everywhere: in JSON, in these types and in the URL. */
import { api, ApiError } from "./api";
import { ARROWS } from "./utils/board";

export type ReviewTimeClass = "focus" | "all";
export const REVIEW_TIME_CLASS_LABELS: Record<ReviewTimeClass, string> = { focus: "My focus", all: "All" };

export const OPENING_ALL = "__all__";

/** The page's two settings, kept in its URL so a return from a game, a reload and Back all find
 *  them again. */
export interface ReviewSettings {
  timeClass: ReviewTimeClass;
  opening: string;
}
export const REVIEW_DEFAULTS: ReviewSettings = { timeClass: "focus", opening: OPENING_ALL };

/** Settings from the query string: a missing or unrecognised value is the default. The opening is
 *  the server's to judge (a stale one is a 422 the page recovers from). */
export function readReviewSettings(params: URLSearchParams): ReviewSettings {
  const tc = params.get("tc");
  const opening = params.get("opening");
  return {
    timeClass: tc != null && Object.hasOwn(REVIEW_TIME_CLASS_LABELS, tc) ? (tc as ReviewTimeClass) : REVIEW_DEFAULTS.timeClass,
    opening: opening ? opening : REVIEW_DEFAULTS.opening,
  };
}

/** The query string for settings: defaults are left out, so the default view is a bare /review. */
export function reviewSettingsSearch(s: ReviewSettings): string {
  const q = new URLSearchParams();
  if (s.timeClass !== REVIEW_DEFAULTS.timeClass) q.set("tc", s.timeClass);
  if (s.opening !== REVIEW_DEFAULTS.opening) q.set("opening", s.opening);
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

/** What was expanded on the page, and under which settings: kept in the page's own history entry
 *  (and carried into a game or a position and back), restored only when the settings agree. */
export interface ReviewOpenSnapshot {
  sections: string[];
  habits: string[];
  key: string;
}
export const reviewSettingsKey = (s: ReviewSettings) => `${s.timeClass}|${s.opening}`;

export function readOpenSnapshot(state: unknown, settings: ReviewSettings): ReviewOpenSnapshot | null {
  const open = (state as { open?: unknown } | null)?.open as Partial<ReviewOpenSnapshot> | undefined;
  if (!open || open.key !== reviewSettingsKey(settings) || !Array.isArray(open.sections) || !Array.isArray(open.habits)) return null;
  return { sections: open.sections.filter((c): c is string => typeof c === "string"), habits: open.habits.filter((n): n is string => typeof n === "string"), key: open.key };
}

// --- positions --------------------------------------------------------------------------------

export type PositionStatus = "still_leaking" | "new_leak" | "too_early" | "not_reached_lately" | "looks_fixed" | "improving";

export interface ReviewPosition {
  colour: "white" | "black";
  key: string;
  line_san: string[];
  fen: string | null;
  last_move: string | null;
  n: number;
  /** Over the whole history, unweighted: Rob's score and the Elo expectation, as fractions. */
  score: number;
  expected: number;
  /** The same, weighted toward recent games. */
  current_score: number;
  current_expected: number;
  long_deficit: number;
  current_deficit: number;
  leak_per_month: number;
  recent_games: number;
  status: PositionStatus | null;
  /** Rob's expected score (0-100) at the board by the engine; null until it is evaluated. */
  es_at_node: number | null;
  /** Points below expectation per 100 games, one entry per 30 days, oldest first; null: too few games. */
  trend: (number | null)[];
  parent_key: string | null;
}

export interface ReviewHabit {
  id: string;
  label: string;
  events: number;
  games: number;
  rate_per_100: number;
  current_rate_per_100: number;
  points_per_month: number;
  trend: "worse" | "improving" | "steady" | null;
  practice_theme: string | null;
}

export interface LostWin {
  chess_game_id: number;
  played_at: string | null;
  opponent_username: string | null;
  opponent_rating: number | null;
  result: string | null;
  time_class: string | null;
  url: string | null;
  reviewed: boolean;
  peak_es: number | null;
  anchor_ply: number;
  anchor_move: string | null;
  cost: number;
}

export interface OpeningOption {
  key: string;
  label: string;
  games: number;
}

export interface ReviewPage {
  positions: { ranked: ReviewPosition[]; fixed: ReviewPosition[] };
  habits: ReviewHabit[];
  lost_wins: { games: LostWin[]; total: number };
  filter: { time_class: ReviewTimeClass; opening: string; openings: OpeningOption[] };
  meta: { as_of: string | null; history_months: number; games_counted: number; games_without_prefix: number; games_without_ratings: number; window_games: number };
}

export interface HabitGame {
  chess_game_id: number;
  played_at: string | null;
  opponent_username: string | null;
  opponent_rating: number | null;
  result: string | null;
  anchor_ply: number;
  anchor_move: string | null;
  cost: number;
  reviewed: boolean;
}

export interface Paged<T> {
  rows: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export type TurningState = "found" | "none" | "not_analysed";

export interface PositionGame {
  chess_game_id: number;
  ply: number;
  played_at: string | null;
  time_class: string | null;
  opponent_username: string | null;
  opponent_rating: number | null;
  result: string | null;
  reviewed: boolean;
  es_on_arrival: number | null;
  turning_state: TurningState;
  turning_ply: number | null;
  turning_move: string | null;
  turning_cost: number | null;
}

export interface PositionChild {
  san: string;
  key: string;
  /** False when the move goes back to a board that is never a position of its own (the start). */
  linkable: boolean;
  n: number;
  score: number;
  expected: number;
}

export interface PositionPage {
  node: ReviewPosition & { rob_to_move: boolean; ply: number };
  children: PositionChild[];
  games: Paged<PositionGame>;
  older_games: number;
}

const q = (params: Record<string, string | number>) => new URLSearchParams(Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))).toString();

export const getReviewPage = (timeClass: ReviewTimeClass, opening: string) => api.get<ReviewPage>(`/review?${q({ time_class: timeClass, opening })}`);

export const getHabitGames = (habitId: string, timeClass: ReviewTimeClass, opening: string, page: number) => api.get<Paged<HabitGame>>(`/review/habits/${encodeURIComponent(habitId)}?${q({ time_class: timeClass, opening, page })}`);

export const getPositionPage = (colour: string, key: string, timeClass: ReviewTimeClass, opening: string, page: number) => api.get<PositionPage>(`/review/positions/${encodeURIComponent(colour)}/${encodeURIComponent(key)}?${q({ time_class: timeClass, opening, page })}`);

/** The page of a position's games from its query string: a positive integer, else 1. */
export function readPositionPage(params: URLSearchParams): number {
  const raw = params.get("page") ?? "";
  const n = /^[0-9]{1,5}$/.test(raw) ? Number(raw) : 1;
  return n >= 1 ? n : 1;
}

/** A position page's query string: the settings, then the page when it is not the first. */
export function positionSearch(s: ReviewSettings, page: number): string {
  const q = new URLSearchParams(reviewSettingsSearch(s));
  if (page > 1) q.set("page", String(page));
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

/** A position's page, under the same settings as the page that links to it. */
export const positionPath = (colour: string, key: string, s: ReviewSettings) => `/review/positions/${colour}/${key}${reviewSettingsSearch(s)}`;

/** The one error a page recovers from on its own: the focused opening no longer qualifies. */
export const isStaleOpeningError = (err: unknown): boolean => err instanceof ApiError && err.status === 422 && err.message.includes("unknown opening key");

// --- words and colours ------------------------------------------------------------------------

export const STATUS_LABELS: Record<PositionStatus, string> = {
  still_leaking: "Still leaking",
  new_leak: "New leak",
  too_early: "Too early to tell",
  not_reached_lately: "Not reached lately",
  looks_fixed: "Looks fixed",
  improving: "Improving, too early to call",
};

/** Good and bad take the board's own meanings: the engine's green, the played move's red. */
export const GOOD = ARROWS.engine;
export const BAD = ARROWS.played;
export const STATUS_TONE: Record<PositionStatus, string> = {
  still_leaking: BAD,
  new_leak: BAD,
  too_early: ARROWS.opponent,
  not_reached_lately: ARROWS.opponent,
  looks_fixed: GOOD,
  improving: GOOD,
};

/** Rob's expected score (0-100) below which a position is "already worse" (the server's
 *  REVIEW_PLAYABLE_ES, which also orders a position's games). */
export const PLAYABLE_ES = 40;

/** The engine's word on a position, from Rob's expected score there. */
export function engineLabel(es: number | null): string {
  if (es == null) return "Engine check pending";
  return es < PLAYABLE_ES ? "Already worse when you get here — prep it" : "Fine when you get here — results are the problem";
}

export const pct = (x: number) => `${Math.round(x * 100)}%`;

/** "1.e4 d5 2.exd5 Qxd5" — a line from the start position, with move numbers. */
export function lineText(moves: string[]): string {
  return moves.map((m, i) => (i % 2 === 0 ? `${i / 2 + 1}.${m}` : m)).join(" ");
}

/** "≈1.7 points a month": a leak in game points. */
export const pointsAMonth = (x: number) => `≈${x >= 10 ? x.toFixed(0) : x.toFixed(1)} point${Math.abs(x) >= 0.95 && Math.abs(x) < 1.05 ? "" : "s"} a month`;

/** The one sentence that says why a game is worth opening:
 *  "Fine when you got here (ES 47), turned at 17…Qb6 (−22), lost." */
export function whyThisGame(g: PositionGame): string {
  const parts: string[] = [];
  if (g.es_on_arrival != null) parts.push(g.es_on_arrival >= PLAYABLE_ES ? `Fine when you got here (ES ${Math.round(g.es_on_arrival)})` : `Already worse when you got here (ES ${Math.round(g.es_on_arrival)})`);
  const cost = `(−${Math.round(g.turning_cost ?? 0)})`;
  parts.push(g.turning_state === "found" ? (g.turning_move ? `turned at ${g.turning_move} ${cost}` : `turned ${cost}`) : g.turning_state === "none" ? "no single turning point" : "not analysed yet");
  parts.push(g.result === "win" ? "won" : g.result === "draw" ? "drew" : g.result === "loss" ? "lost" : "unfinished");
  const text = parts.join(", ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}
