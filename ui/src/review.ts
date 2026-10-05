/** Types and calls for the Review page and a position's page. Shapes follow core/review/read.py,
 *  core/review/mistakes.py, core/review/position.py and core/review/habits.py. Position keys are 64-bit integers and travel
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

// --- opening mistakes ------------------------------------------------------------------------

export type MistakeStatus = "still_costing" | "not_yet_checked" | "fixed" | "not_reached_lately";
/** One of Rob's visits to a board: his move there was fine, costly, or is not evaluated yet. */
export type VisitState = "fine" | "costly" | "unknown";

export interface MistakeMove {
  san: string;
  n: number;
  /** How many of the `n` are evaluated; `mean_loss` is over those (expected-score points, 0-100). */
  evaluated: number;
  mean_loss: number | null;
  costly: number;
  /** The move gives checkmate. */
  mates: boolean;
  last_played: string | null;
}

/** A board Rob moved from, with what his moves there gave away by the engine's account. */
export interface MistakeNumbers {
  colour: "white" | "black";
  key: string;
  line_san: string[];
  fen: string | null;
  last_move: string | null;
  /** Distinct games, and of those how many had a costly move here. */
  games: number;
  costly_games: number;
  /** Visits (one per game, board and move), how many are evaluated, how many were costly. */
  decisions: number;
  evaluated: number;
  costly: number;
  /** Expected points (a game is 1) given away a month at the current rate, and over the history. */
  per_month: number;
  per_month_12: number;
  per_visit: number;
  status: MistakeStatus | null;
  fixed: boolean;
  /** The last visits, oldest first. */
  strip: VisitState[];
  moves: MistakeMove[];
  best_move: string | null;
  terminal: "checkmate" | "draw" | null;
  last_costly: string | null;
}

export interface MistakeCard extends MistakeNumbers {
  parent_key: string | null;
}

export interface MistakeCoverage {
  decisions: number;
  /** On boards Rob moved from in at least `eval_min_games` games: the ones the engine checks. */
  covered: number;
  evaluated: number;
  eval_min_games: number;
}

export interface MoveGame {
  chess_game_id: number;
  ply: number;
  san: string;
  state: VisitState;
  /** Expected-score points the move gave away; null until the engine has checked it. */
  loss: number | null;
  played_at: string | null;
  opponent_username: string | null;
  opponent_rating: number | null;
  result: string | null;
  /** Its moves are still stored, so it opens in the game review. */
  has_moves: boolean;
}

export interface MistakeDetail extends MistakeNumbers {
  ranked: boolean;
}

/** Rob's games from a board, newest first: every game with `move`, or with none the costly ones. */
export interface MoveGames extends Paged<MoveGame> {
  move: string | null;
}

export const MISTAKE_STATUS_LABELS: Record<MistakeStatus, string> = {
  still_costing: "Still costing you",
  not_yet_checked: "Not yet checked",
  fixed: "Fixed?",
  not_reached_lately: "Not reached lately",
};

/** "4…Nf6" for the move played from a board reached after `ply` half-moves. */
export function moveLabel(ply: number, san: string): string {
  const n = Math.floor(ply / 2) + 1;
  return ply % 2 === 0 ? `${n}.${san}` : `${n}…${san}`;
}

/** "≈0.27 points a month" given away: small numbers keep two decimals. */
export const givenAway = (x: number) => `≈${x >= 1 ? x.toFixed(1) : x.toFixed(2)} point${Math.abs(x - 1) < 0.005 ? "" : "s"} given away a month`;

// --- results below rating expectation --------------------------------------------------------

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
  mistakes: { ranked: MistakeCard[]; fixed: MistakeCard[]; coverage: MistakeCoverage };
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
  /** The results numbers; null only for the starting position, which no game first reaches later. */
  node: (ReviewPosition & { rob_to_move: boolean; ply: number }) | null;
  children: PositionChild[];
  games: Paged<PositionGame>;
  older_games: number;
  /** Rob's moves from the board; null when he is never to move there. */
  mistake: MistakeDetail | null;
}

const q = (params: Record<string, string | number>) => new URLSearchParams(Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))).toString();

export const getReviewPage = (timeClass: ReviewTimeClass, opening: string) => api.get<ReviewPage>(`/review?${q({ time_class: timeClass, opening })}`);

export const getHabitGames = (habitId: string, timeClass: ReviewTimeClass, opening: string, page: number) => api.get<Paged<HabitGame>>(`/review/habits/${encodeURIComponent(habitId)}?${q({ time_class: timeClass, opening, page })}`);

export const getPositionPage = (colour: string, key: string, timeClass: ReviewTimeClass, opening: string, page: number) => api.get<PositionPage>(`/review/positions/${encodeURIComponent(colour)}/${encodeURIComponent(key)}?${q({ time_class: timeClass, opening, page })}`);

export const getPositionMoveGames = (colour: string, key: string, timeClass: ReviewTimeClass, opening: string, move: string | null, page: number) =>
  api.get<MoveGames>(`/review/positions/${encodeURIComponent(colour)}/${encodeURIComponent(key)}/games?${q({ time_class: timeClass, opening, page, ...(move ? { move } : {}) })}`);

const pageOf = (raw: string | null) => {
  const n = raw && /^[0-9]{1,5}$/.test(raw) ? Number(raw) : 1;
  return n >= 1 ? n : 1;
};

/** The page of a position's games from its query string: a positive integer, else 1. */
export function readPositionPage(params: URLSearchParams): number {
  return pageOf(params.get("page"));
}

/** Which of Rob's games from the board are listed (`move`: one move's, null: the costly ones) and
 *  their page, from the query string (`move`, `mpage`). */
export interface MovesView {
  move: string | null;
  page: number;
}
export function readMovesView(params: URLSearchParams): MovesView {
  const move = params.get("move");
  return { move: move && /^[A-Za-z0-9+#=-]{2,10}$/.test(move) ? move : null, page: pageOf(params.get("mpage")) };
}

/** A position page's query string: the settings, then the games' page and the moves view when
 *  they are not the defaults. */
export function positionSearch(s: ReviewSettings, page: number, moves: MovesView = { move: null, page: 1 }): string {
  const q = new URLSearchParams(reviewSettingsSearch(s));
  if (page > 1) q.set("page", String(page));
  if (moves.move) q.set("move", moves.move);
  if (moves.page > 1) q.set("mpage", String(moves.page));
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

export const pct = (x: number) => `${Math.round(x * 100)}%`;

/** "1.e4 d5 2.exd5 Qxd5" — a line from the start position, with move numbers. */
export function lineText(moves: string[]): string {
  return moves.map((m, i) => (i % 2 === 0 ? `${i / 2 + 1}.${m}` : m)).join(" ");
}

/** "≈1.7 below expectation a month": how far results trail the rating expectation, in game points. */
export const belowExpectation = (x: number) => `≈${x >= 10 ? x.toFixed(0) : x.toFixed(1)} below expectation a month`;

/** "≈1.7 points a month": a habit's cost in game points. */
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
