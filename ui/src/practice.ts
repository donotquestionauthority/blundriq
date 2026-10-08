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

export interface AttemptBody {
  solved: boolean;
  moves_played?: string;
  attempt_id: string;
  session_id?: string;
  /** The segment the solver displayed, exactly as the puzzle payload gave it (null for a
   *  standard puzzle). The server grades against it: the queue, Browse, a deep link and a
   *  solver that stays mounted across a change in the truncation can all show different
   *  segments of one repertoire puzzle, and only the solver knows which one it showed. */
  presentation_ply: number | null;
}

/**
 * `attempt_id` is a fresh UUID per submission (the server's idempotency key: a replay returns
 * the original verdict without re-applying SRS); `session_id` is stable across the retries of
 * one play-through, and only the first attempt of a session scores.
 */
export async function recordAttempt(puzzleId: number, body: AttemptBody): Promise<AttemptResponse> {
  const response = await api.post<AttemptResponse>(`/practice/puzzles/${puzzleId}/attempt`, body);
  window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT));
  return response;
}

/**
 * Fired on `window` after the server acknowledged an attempt, never after a failure. Every save
 * path (the solver, blocking mode and its Retry, the held-attempt banner, the durable queue)
 * goes through `recordAttempt`, so this is the one place that knows an attempt landed.
 */
export const ATTEMPT_SAVED_EVENT = "blundriq:attempt-saved";

/** Today's puzzles, the day taken in the `timezone` setting (GET /practice/today). */
export interface PracticeToday {
  /** The calendar day, YYYY-MM-DD. */
  date: string;
  /** Distinct puzzles with a correct attempt today — the same number as Home's. */
  solved: number;
  /** Distinct puzzles with any attempt today, right or wrong. */
  tried: number;
  /** `daily_puzzle_target`; applies to `solved`. */
  target: number;
  /** When the next day starts in that zone (an instant, ISO 8601). */
  next_day_at: string;
  /** The server's clock when it answered: the rollover is timed from this, not the browser's. */
  now: string;
}

export const getPracticeToday = () => api.get<PracticeToday>("/practice/today");

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

/** One node of the repertoire filter: `count` is how many positions that scope serves (the rows
 *  its browse list shows). */
export interface RepertoireScopeLine {
  id: number;
  title: string;
  count: number;
}
export interface RepertoireScopeChapter {
  id: number;
  title: string;
  count: number;
  lines: RepertoireScopeLine[];
}
export interface RepertoireScopeBook {
  id: number;
  title: string;
  color: "white" | "black";
  count: number;
  chapters: RepertoireScopeChapter[];
}

export async function getRepertoireScopes(): Promise<RepertoireScopeBook[]> {
  const data = await api.get<{ books: RepertoireScopeBook[] }>("/practice/repertoire-scopes");
  return data.books;
}

/** A repertoire SubType: `book:<id>`, `chapter:<id>`, `line:<id>`, or a bare `<id>` (a line, the
 *  older spelling). Anything else is no filter here; the server refuses it. */
export function parseRepertoireSubtype(subtype: string | null): { kind: "book" | "chapter" | "line"; id: number } | null {
  if (!subtype) return null;
  const m = /^(?:(book|chapter|line):)?([1-9][0-9]{0,9})$/.exec(subtype);
  if (!m) return null;
  return { kind: (m[1] as "book" | "chapter" | "line" | undefined) ?? "line", id: Number(m[2]) };
}

/** Where a SubType sits in the tree: the book, chapter and line it selects (null above it). */
export function locateScope(books: RepertoireScopeBook[], subtype: string | null): { book: number | null; chapter: number | null; line: number | null } {
  const none = { book: null, chapter: null, line: null };
  const parsed = parseRepertoireSubtype(subtype);
  if (!parsed) return none;
  for (const b of books) {
    if (parsed.kind === "book" && b.id === parsed.id) return { book: b.id, chapter: null, line: null };
    for (const c of b.chapters) {
      if (parsed.kind === "chapter" && c.id === parsed.id) return { book: b.id, chapter: c.id, line: null };
      for (const l of c.lines) if (parsed.kind === "line" && l.id === parsed.id) return { book: b.id, chapter: c.id, line: l.id };
    }
  }
  return none;
}
