/** Types and calls for the Review worklist. Shapes follow core/review/read.py. */
import { api, ApiError } from "./api";

export type ReviewTimeClass = "focus" | "all";
export const REVIEW_TIME_CLASS_LABELS: Record<ReviewTimeClass, string> = { focus: "My focus", all: "All" };

export const GROUP_BY_MODES = ["variation", "repertoire", "position"] as const;
export type GroupByMode = (typeof GROUP_BY_MODES)[number];
export const GROUP_BY_LABELS: Record<GroupByMode, string> = { variation: "By variation", repertoire: "By repertoire line", position: "By position" };

/** To review (default) / All: the client's own scope. The server returns both counts at every node. */
export type ReviewedScope = "to_review" | "all";

export const OPENING_ALL = "__all__";
export const OPENING_UNCLASSIFIED = "__unclassified__";

/** The worklist's four settings, kept in its URL so a return from a game, a reload and Back all
 *  find them again. */
export interface ReviewSettings {
  timeClass: ReviewTimeClass;
  scope: ReviewedScope;
  opening: string;
  groupBy: GroupByMode;
}
export const REVIEW_DEFAULTS: ReviewSettings = { timeClass: "focus", scope: "to_review", opening: OPENING_ALL, groupBy: "variation" };

/** Settings from the query string: a missing or unrecognised value is the default. The opening is
 *  the server's to judge (a stale one is a 422 the page recovers from). */
export function readReviewSettings(params: URLSearchParams): ReviewSettings {
  const tc = params.get("tc");
  const scope = params.get("scope");
  const group = params.get("group");
  const opening = params.get("opening");
  return {
    timeClass: tc != null && Object.hasOwn(REVIEW_TIME_CLASS_LABELS, tc) ? (tc as ReviewTimeClass) : REVIEW_DEFAULTS.timeClass,
    scope: scope === "all" || scope === "to_review" ? scope : REVIEW_DEFAULTS.scope,
    opening: opening ? opening : REVIEW_DEFAULTS.opening,
    groupBy: (GROUP_BY_MODES as readonly string[]).includes(group ?? "") ? (group as GroupByMode) : REVIEW_DEFAULTS.groupBy,
  };
}

/** The query string for settings: defaults are left out, so the default view is a bare /review. */
export function reviewSettingsSearch(s: ReviewSettings): string {
  const q = new URLSearchParams();
  if (s.timeClass !== REVIEW_DEFAULTS.timeClass) q.set("tc", s.timeClass);
  if (s.scope !== REVIEW_DEFAULTS.scope) q.set("scope", s.scope);
  if (s.opening !== REVIEW_DEFAULTS.opening) q.set("opening", s.opening);
  if (s.groupBy !== REVIEW_DEFAULTS.groupBy) q.set("group", s.groupBy);
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

/** What was expanded on the worklist, and under which settings: kept in the worklist's own
 *  history entry (and carried into a game and back), restored only when the settings agree. */
export interface ReviewOpenSnapshot {
  cats: string[];
  nodes: string[];
  key: string;
}
export const reviewSettingsKey = (s: ReviewSettings) => `${s.timeClass}|${s.scope}|${s.opening}|${s.groupBy}`;

export function readOpenSnapshot(state: unknown, settings: ReviewSettings): ReviewOpenSnapshot | null {
  const open = (state as { open?: unknown } | null)?.open as Partial<ReviewOpenSnapshot> | undefined;
  if (!open || open.key !== reviewSettingsKey(settings) || !Array.isArray(open.cats) || !Array.isArray(open.nodes)) return null;
  return { cats: open.cats.filter((c): c is string => typeof c === "string"), nodes: open.nodes.filter((n): n is string => typeof n === "string"), key: open.key };
}

export type ReviewBaseRoute = "endgame_technique" | "lapse_defense" | "lapse_offense" | "faded";
export type ReviewDisplayedRoute = "opening" | ReviewBaseRoute;

/** One game row: the game's representative anchor with the context the row renders. */
export interface ReviewGameRow {
  chess_game_id: number;
  anchor_ply: number;
  cost: number;
  phase: string | null;
  piece_label: string | null;
  book_relation: string | null;
  evidence: Record<string, unknown>;
  base_route: ReviewBaseRoute;
  displayed_route: ReviewDisplayedRoute | null;
  pool_key: string | null;
  board_key: number | null;
  played_at: string | null;
  opponent_username: string | null;
  result: string | null;
  url: string | null;
  time_class: string | null;
  canonical_family: string | null;
  canonical_variation: string | null;
  /** Per game (player_games.reviewed_at): hidden under "To review", marked under "All". */
  reviewed: boolean;
  /** Drill-down rows only: the game's other anchors in this node ("+N more"). */
  extra_in_game?: number;
  /** Opening nodes only: the correct move at the anchor, and the book move when there is one. */
  best_move?: string | null;
  best_line?: string | null;
  expected_move?: string | null;
  deviated_at_ply?: number | null;
}

export interface ReviewPool {
  pool_id: string;
  pool_key: string | null;
  label: string;
  severity: number;
  raw_severity: number;
  event_count: number;
  confidence: "high" | "low";
  book_relation_verdict: string | null;
  total_games: number;
  to_review_games: number;
  representative_game: ReviewGameRow;
}

export interface OpeningSubgroup {
  subgroup_id: string;
  label: string;
  kind: GroupByMode;
  severity: number;
  raw_severity: number;
  event_count: number;
  confidence: "high" | "low";
  book_relation_verdict: string | null;
  total_games: number;
  to_review_games: number;
  representative_game: ReviewGameRow;
}

export interface OpeningFamily {
  family_id: string;
  family_key: string;
  label: string;
  severity: number;
  raw_severity: number;
  event_count: number;
  confidence: "high" | "low";
  total_games: number;
  to_review_games: number;
  subgroups: OpeningSubgroup[];
  representative_game: ReviewGameRow;
}

interface CategoryCounts {
  total_games: number;
  to_review_games: number;
}
export interface OpeningCategory extends CategoryCounts {
  families: OpeningFamily[];
}
export interface PoolCategory extends CategoryCounts {
  pools: ReviewPool[];
}
export interface OversightsCategory extends CategoryCounts {
  defense: PoolCategory;
  offense: PoolCategory;
}
export interface LostWinsCategory extends CategoryCounts {
  games: ReviewGameRow[];
}

export interface ReviewCategories {
  opening: OpeningCategory;
  oversights: OversightsCategory;
  endgame: PoolCategory;
  faded: PoolCategory;
  lost_wins: LostWinsCategory;
}

export interface OpeningOption {
  key: string;
  label: string;
  to_review_games: number;
}

export interface ReviewPage {
  categories: ReviewCategories;
  page: { total_games: number; to_review_games: number };
  filter: { time_class: ReviewTimeClass; opening: string; openings: OpeningOption[]; group_by: GroupByMode };
}

export interface ReviewPoolEventsResponse {
  events: ReviewGameRow[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

const q = (params: Record<string, string | number>) => new URLSearchParams(Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))).toString();

export const getReviewPage = (timeClass: ReviewTimeClass, opening: string, groupBy: GroupByMode) => api.get<ReviewPage>(`/review?${q({ time_class: timeClass, opening, group_by: groupBy })}`);

/** The page size is the server's (50); `page` is the only paging input. */
export const getPoolEvents = (poolId: string, timeClass: ReviewTimeClass, opening: string, reviewedScope: ReviewedScope, page: number) =>
  api.get<ReviewPoolEventsResponse>(`/review/pools/${encodeURIComponent(poolId)}/events?${q({ time_class: timeClass, opening, reviewed_scope: reviewedScope, page })}`);

/** Records that a node was shown, for representative rotation. Fire-and-forget: the caller ignores failures. */
export const touchPoolShown = (poolId: string, timeClass: ReviewTimeClass, opening: string) => api.post<unknown>(`/review/pools/${encodeURIComponent(poolId)}/shown?${q({ time_class: timeClass, opening })}`);

/** The one error the page recovers from on its own: the focused opening no longer has review games. */
export const isStaleOpeningError = (err: unknown): boolean => err instanceof ApiError && err.status === 422 && err.message.includes("unknown opening key");

/** Machine tokens (piece labels, themes, verdicts) as words: `hangingPiece`, `forced_loss` and
 *  `mateIn2` read "Hanging piece", "Forced loss" and "Mate in 2". */
export function prettyToken(s: string): string {
  const t = s
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([a-zA-Z])([0-9])/g, "$1 $2")
    .toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** The pipeline's `forced-loss` piece label reads as a sentence; the stored token is unchanged. */
export const pieceLabelDisplay = (token: string): string => (token === "forced-loss" ? "Forced material loss" : prettyToken(token));

/** The book-relation vocabulary the detector writes, as remediation copy. Opening nodes only. */
const BOOK_RELATION_LABELS: Record<string, string> = {
  deviation_before: "You left book first — drill the line",
  opponent_left: "Opponent left your prep — extend prep here",
  inside_line: "Inside a followed line",
  post_book: "After book ended — post-book plans",
  no_repertoire: "No repertoire coverage",
};
export const bookRelationLabel = (verdict: string): string => BOOK_RELATION_LABELS[verdict] ?? prettyToken(verdict);

/** The piece / theme column: the piece label when there is one, else the offense motif theme. */
export function pieceOrTheme(e: ReviewGameRow): string {
  if (e.piece_label) return pieceLabelDisplay(e.piece_label);
  const theme = e.evidence?.theme;
  return typeof theme === "string" && theme ? prettyToken(theme) : "—";
}

/** "N to review" under the To review scope; "N games · M to review" under All. */
export function countLabel(scope: ReviewedScope, total: number, toReview: number): string {
  if (scope === "to_review") return `${toReview} to review`;
  return `${total} game${total !== 1 ? "s" : ""} · ${toReview} to review`;
}
