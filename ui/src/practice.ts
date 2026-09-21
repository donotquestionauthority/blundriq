/** Types and API calls for the Practice page (kept out of the component files for fast refresh). */
import { api, ApiError } from "./api";

export type SrsLevel = "pawn" | "knight" | "bishop" | "rook" | "queen" | "king";
export type SrsFilter = "due" | "all" | "retired";
export type PracticeType = "all" | "motif" | "repertoire" | "blunder" | "custom";

export interface PuzzleGameLink {
  url: string;
  date: string;
  source_type: "blunder" | "deviation" | string;
  classification: string;
  opponent: string;
}

export interface PuzzleCorrectGameLink {
  url: string;
  date: string;
  opponent: string;
}

export interface AttemptSummary {
  total: number;
  solved: number;
  last_attempt_at: string | null;
  /** Signed: positive = consecutive correct, negative = consecutive wrong, 0 = never attempted. */
  streak: number;
}

export interface PuzzleSrs {
  level: SrsLevel;
  correct_at_level: number;
  last_3_attempts: boolean[];
  last_correct_date: string | null;
  next_show_at: string;
  updated_at: string;
}

/**
 * Forced-mate acceptance map (own_mate puzzles only; null elsewhere). Keys are EPDs as
 * python-chess writes them (see PuzzleEngine's mapKey). `p` maps a player-to-move node to
 * every accepted (optimal) move in UCI; `d` maps an opponent-to-move node to its single
 * canonical defence. `n` is the number of player moves to mate.
 */
export interface AcceptanceMap {
  v: number;
  n: number;
  p: Record<string, string[]>;
  d: Record<string, string>;
}

export interface Puzzle {
  id: number;
  fen: string;
  solution_line: string[];
  acceptance_map: AcceptanceMap | null;
  /** Provenance markers ('blunder', 'lichess_cc0', 'custom', ...), not motif identity. */
  source_types: string[];
  /** Tactical-theme tags: the motif SubType axis. */
  themes: string[];
  color: "w" | "b";
  title: string | null;
  description: string | null;
  created_at: string;
  occurrence_count: number;
  source_breakdown: Record<string, number>;
  game_links: PuzzleGameLink[];
  correct_game_links: PuzzleCorrectGameLink[];
  attempt_count: number;
  attempt_summary: AttemptSummary;
  is_repertoire: boolean;
  presentation_ply: number | null;
  presentation_fen: string | null;
  repertoire_line_id: number | null;
  /** null until first attempted, and always null for rotation puzzles (never on the ladder). */
  srs: PuzzleSrs | null;
  /** The pending batch this served row belongs to; null on non-queue serves. */
  play_batch_id?: number | null;
}

export const CC0_SOURCE_TYPE = "lichess_cc0";
/** Corpus puzzles rotate rather than climb the SRS ladder: `srs` stays null even after attempts. */
export function isRotationPuzzle(p: Pick<Puzzle, "source_types">): boolean {
  return (p.source_types ?? []).includes(CC0_SOURCE_TYPE);
}

export interface PuzzlesResponse {
  puzzles: Puzzle[];
  total: number;
  mastered_count: number;
  advance_threshold: number;
  /** Newest pending batch id (the prefetch cursor); null unless srs=due. */
  batch_id: number | null;
  scope: string | null;
  mint_ahead_threshold?: number | null;
  /** Corpus themes the motif SubType filter may offer. */
  served_themes: string[];
}

export type SrsOutcome = "promoted" | "demoted" | "advanced" | "unchanged";

export interface SrsTransition {
  outcome: SrsOutcome;
  prev_level: SrsLevel;
  new_level: SrsLevel;
  prev_correct_at_level: number;
  new_correct_at_level: number;
  advance_threshold: number;
}

export interface AttemptResponseSrs {
  level: SrsLevel;
  correct_at_level: number;
  advance_threshold: number;
  transition: SrsTransition | null;
}

export interface AttemptResponse {
  detail: string;
  /** The server's verdict; may downgrade a client-claimed solve. */
  solved: boolean;
  attempt_summary: { total: number; solved: number; streak: number };
  srs: AttemptResponseSrs;
}

export interface PlayablePuzzlePayload {
  id: number;
  fen: string;
  solution_line: string[];
  color: "w" | "b";
  acceptance_map: AcceptanceMap | null;
  source_types: string[];
  themes: string[];
  is_repertoire: boolean;
  presentation_ply: number | null;
}

export type SkipStatus = "DEFERRED" | "ALREADY_CONSUMED" | "STATE_MISS";
export interface SkipResult {
  status: SkipStatus;
}

export const LAST_N_OPTIONS = [
  { label: "Last 100 games", value: 100 },
  { label: "Last 200 games", value: 200 },
  { label: "Last 300 games", value: 300 },
  { label: "Last 500 games", value: 500 },
  { label: "All games", value: 0 },
] as const;

/** Migrated rows may carry null themes; the page always works with an array. */
function withThemes<T extends { themes: string[] | null }>(p: T): T & { themes: string[] } {
  return { ...p, themes: p.themes ?? [] };
}

export async function getPuzzles(params: { srs?: SrsFilter; ptype?: PracticeType; subtype?: string | null; last_n_games?: number } = {}): Promise<PuzzlesResponse> {
  const q = new URLSearchParams();
  if (params.srs) q.set("srs", params.srs);
  if (params.ptype && params.ptype !== "all") q.set("ptype", params.ptype);
  if (params.subtype) q.set("subtype", params.subtype);
  if (params.last_n_games) q.set("last_n_games", String(params.last_n_games));
  const qs = q.toString();
  const data = await api.get<PuzzlesResponse>(`/practice/puzzles${qs ? `?${qs}` : ""}`);
  return { ...data, puzzles: data.puzzles.map(withThemes) };
}

/**
 * `attempt_id` is a fresh UUID per submission (the server's idempotency key: a replay returns
 * the original verdict without re-applying SRS); `session_id` is stable across the retries of
 * one play-through, and only the first attempt of a session scores.
 */
export function recordAttempt(puzzleId: number, body: { solved: boolean; moves_played?: string; attempt_id: string; session_id?: string }): Promise<AttemptResponse> {
  return api.post<AttemptResponse>(`/practice/puzzles/${puzzleId}/attempt`, body);
}

export async function getPuzzleById(id: number): Promise<PlayablePuzzlePayload> {
  return withThemes(await api.get<PlayablePuzzlePayload>(`/practice/puzzles/${id}`));
}

function normalizeSkipStatus(raw: unknown): SkipStatus | null {
  const env = raw as { detail?: unknown; status?: unknown } | undefined;
  const data = (env?.detail ?? env) as { status?: unknown } | undefined;
  const s = data?.status;
  return s === "DEFERRED" || s === "ALREADY_CONSUMED" || s === "STATE_MISS" ? s : null;
}

/**
 * A skip is a server-recorded defer, not client navigation. 200 carries DEFERRED or
 * ALREADY_CONSUMED (both mean the item is consumed); a 409 carries STATE_MISS in its detail
 * envelope and resolves too, so the caller decides what to do with it. Anything else rethrows.
 */
export async function skipPuzzle(body: { ptype: string; subtype: string | null; batch_id: number; puzzle_id: number }): Promise<SkipResult> {
  try {
    const data = await api.post<unknown>("/practice/skip", body);
    return { status: normalizeSkipStatus(data) ?? "STATE_MISS" };
  } catch (err: unknown) {
    if (err instanceof ApiError && err.status === 409) {
      let detail: unknown = null;
      try {
        detail = JSON.parse(err.message);
      } catch {
        /* not JSON */
      }
      return { status: normalizeSkipStatus(detail) ?? "STATE_MISS" };
    }
    throw err;
  }
}
