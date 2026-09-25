/** Types, calls and card mappings for the Scout page. */
import { api } from "./api";
import type { BlunderClass } from "./blunders";
import type { PositionCardData } from "./components/PositionCard/types";
import type { RepLine } from "./repertoire";

export type ScoutColor = "both" | "white" | "black";
export const COLOR_LABELS: Record<ScoutColor, string> = { both: "Both colors", white: "As White", black: "As Black" };

export interface OpponentProfile {
  id: number;
  name: string;
  is_initialized: boolean;
  game_count: number;
  chesscom_username: string | null;
  lichess_username: string | null;
  chesscom_last_fetched: string | null;
  lichess_last_fetched: string | null;
}

export interface Activity {
  last_1: number;
  last_7: number;
  last_30: number;
  total: number;
  [k: string]: number;
}

export interface FamilyRow {
  family: string;
  cnt: number;
  pct: number;
  games?: Array<{ date: string; url: string }>;
}

export interface LineRow {
  eco: string;
  family: string;
  variation: string;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  win_pct: number;
  shrunk_rate: number;
}

export interface ScoutReport {
  activity: Activity;
  as_white: FamilyRow[];
  as_black: FamilyRow[];
  most_played: FamilyRow[];
  best_lines: LineRow[];
  worst_lines: LineRow[];
}

export interface LineGame {
  date: string;
  result: string;
  color: string;
  opening: string;
  url: string;
}

export interface ScoutMyGame {
  date: string;
  opponent: string;
  color: string;
  class: string;
  url: string;
}

export interface ScoutOppGame {
  date: string;
  opening: string;
  as: string;
  url: string;
}

export interface ScoutPosition {
  fen: string;
  tier: 1 | 2 | 3;
  my_frequency: number;
  opp_frequency: number;
  max_depth: number;
  blunder_score: number | null;
  blunder_count: number | null;
  top_classification: BlunderClass | null;
  move_played: string | null;
  /** The engine's move: the blunder row's on tier 1, else the analysed replay game's. */
  best_move: string | null;
  best_move_date: string | null;
  line_id: number | null;
  line_name: string | null;
  chapter_title: string | null;
  book_title: string | null;
  book_color: string | null;
  moves: string[] | null;
  starting_fen: string | null;
  ply: number | null;
  my_games: ScoutMyGame[];
  opp_games: ScoutOppGame[];
  rep_lines: RepLine[];
  /** The one move the repertoire's lines agree on here, or null. */
  rep_expected_move: string | null;
}

export interface ScoutPositionsResponse {
  positions: ScoutPosition[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  tier_counts: Partial<Record<"1" | "2" | "3", number>>;
}

export interface ScoutDecisionNode {
  fen: string;
  node_freq: number;
  distinct_replies: number;
  my_frequency: number;
  opp_replies: Array<{ move: string; cnt: number }>;
  replies_more: number;
  lead_in: string | null;
  lead_pre_fen: string | null;
  line_name: string | null;
  book_title: string | null;
  chapter_title: string | null;
  my_color: "white" | "black";
}

export interface DismissedBoard {
  fen: string;
  dismissed_at: string;
}

export interface ScoutFilters {
  my_last_n: number; // 0 = all
  opp_last_n: number; // 0 = all
  color: ScoutColor;
  min_freq: number;
}

export function defaultFilters(s: Record<string, unknown>): ScoutFilters {
  const num = (k: string, d: number) => (typeof s[k] === "number" ? (s[k] as number) : d);
  return { my_last_n: num("scout_default_last_n_games", 500), opp_last_n: num("scout_default_last_n_games", 500), color: "both", min_freq: num("scout_default_min_occurrences", 2) };
}

const windowParams = (f: Pick<ScoutFilters, "my_last_n" | "opp_last_n">) => ({ my_last_n: String(f.my_last_n), opp_last_n: String(f.opp_last_n) });

export const getProfiles = () => api.get<{ profiles: OpponentProfile[] }>("/scout/profiles");
export const createProfile = (body: { name: string; chesscom_username?: string; lichess_username?: string }) => api.post<{ profile_id: number }>("/scout/profiles", body);
export const deleteProfile = (id: number) => api.del<{ detail: string }>(`/scout/profiles/${id}`);
export const getReport = (id: number, oppLastN: number) => api.get<ScoutReport>(`/scout/report/${id}?opp_last_n=${oppLastN}`);
export const getLineGames = (id: number, family: string, variation: string | null, oppLastN: number) => {
  const q = new URLSearchParams({ family, opp_last_n: String(oppLastN) });
  if (variation) q.set("variation", variation);
  return api.get<{ games: LineGame[] }>(`/scout/line-games/${id}?${q}`);
};
export const getPositions = (id: number, f: ScoutFilters, page: number) => api.get<ScoutPositionsResponse>(`/scout/positions/${id}?${new URLSearchParams({ ...windowParams(f), color: f.color, min_freq: String(f.min_freq), page: String(page) })}`);
export const getDecisionNodes = (id: number, f: ScoutFilters) => api.get<{ nodes: ScoutDecisionNode[]; total: number }>(`/scout/decision-nodes/${id}?${new URLSearchParams({ ...windowParams(f), min_freq: String(f.min_freq) })}`);
export const dismissBoard = (fen: string) => api.post<{ detail: string }>("/scout/dismiss", { fen });
export const restoreBoard = (fen: string) => api.del<{ detail: string }>("/scout/dismiss", { fen });
export const getDismissed = () => api.get<{ boards: DismissedBoard[] }>("/scout/dismissed");

export function fenActiveColor(fen: string): "white" | "black" {
  return fen.split(" ")[1] === "b" ? "black" : "white";
}

/**
 * A Scout position as the shared card. The arrow is the repertoire's move first: `expectedMove`
 * carries the move the lines agree on (labelled "Expected"), and the engine's `bestMove` is
 * given only when the repertoire has none, so `recommended()` never puts the engine ahead of
 * the book here. The blunder tier's played move keeps its vermilion arrow.
 */
export function scoutToCard(p: ScoutPosition): PositionCardData {
  return {
    fen: p.fen,
    color: fenActiveColor(p.fen),
    times: p.opp_frequency,
    topClassification: p.top_classification,
    context: `Me ${p.my_frequency}× · Them ${p.opp_frequency}×`,
    book: p.book_title,
    chapter: p.chapter_title,
    lineNames: p.line_name ? [p.line_name] : null,
    movePlayed: p.move_played,
    expectedMove: p.rep_expected_move,
    bestMove: p.rep_expected_move ? null : p.best_move,
    bestMoveDate: p.rep_expected_move ? null : p.best_move_date,
    repLines: p.rep_lines.length ? p.rep_lines : null,
    moves: p.moves,
    ply: p.ply,
    games: p.my_games.map((g) => ({ game_url: g.url || null, played_at: g.date || null, classification: (g.class || undefined) as BlunderClass | undefined })),
    oppGames: p.opp_games.map((g) => ({ game_url: g.url || null, played_at: g.date || null, opening: g.opening, as: g.as })),
  };
}

/** A decision node as a card: no played or best arrow, one arrow per reply, the board from
 *  the player's side (the side not to move). */
export function decisionNodeToCard(n: ScoutDecisionNode): PositionCardData {
  return {
    fen: n.fen,
    color: n.my_color,
    times: n.node_freq,
    context: `Them ${n.node_freq}× · Me ${n.my_frequency}×`,
    book: n.book_title,
    chapter: n.chapter_title,
    lineNames: n.line_name ? [n.line_name] : null,
    oppReplies: n.opp_replies,
    repliesMore: n.replies_more,
    leadIn: n.lead_in,
    leadPreFen: n.lead_pre_fen,
    nodeFreq: n.node_freq,
    games: [],
  };
}

export const TIER_ACCENT: Record<1 | 2 | 3, string> = { 1: "border-l-red-500", 2: "border-l-amber-500", 3: "border-l-blue-500" };
