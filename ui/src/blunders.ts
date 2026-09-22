/** Types, options and calls for the Blunders page. */
import { api } from "./api";
import type { PositionCardData } from "./components/PositionCard/types";

export type BlunderClass = "miss" | "blunder" | "mistake" | "inaccuracy";
export const BLUNDER_CLASSES: BlunderClass[] = ["miss", "blunder", "mistake", "inaccuracy"];

export type TimeClass = "focus" | "all" | "bullet" | "blitz" | "rapid" | "classical";
export const TIME_CLASS_LABELS: Record<TimeClass, string> = {
  focus: "My focus",
  all: "All",
  bullet: "Bullet",
  blitz: "Blitz",
  rapid: "Rapid",
  classical: "Classical + daily",
};

export const DAY_OPTIONS = [7, 10, 20, 30, 60, 90, 180, 365];
export const LAST_N_OPTIONS = [100, 200, 300, 500, 1000];
export const MIN_OCCURRENCE_OPTIONS = [1, 2, 3, 4, 5];

export interface BlunderGame {
  chess_game_id: number;
  game_url: string | null;
  played_at: string | null;
  result: "win" | "loss" | "draw" | null;
  classification: BlunderClass;
  cp_loss: number | null;
  move_played: string | null;
  best_move: string | null;
  in_repertoire: boolean;
  deviated_by_me: boolean;
}

export interface BlunderPosition {
  fen: string;
  count: number;
  score: number;
  classifications: Partial<Record<BlunderClass, number>>;
  color: "white" | "black";
  dismissed: boolean;
  /** Crossed the list's threshold after the request's `new_since` (Home's visit boundary). */
  is_new: boolean;
  last_seen: string | null;
  context: string;
  book: string | null;
  chapter: string | null;
  line_names: string[] | null;
  move_played: string | null;
  best_move: string | null;
  best_line: string | null;
  post_blunder_line: string | null;
  cp_loss: number | null;
  ply: number | null;
  chess_game_id: number | null;
  moves: string[] | null;
  games: BlunderGame[];
}

export interface BlundersResponse {
  positions: BlunderPosition[];
  active_count: number;
  dismissed_count: number;
  /** Active boards marked new under `new_since`; 0 without one. They are listed first. */
  new_count: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface BlunderFilters {
  since_days: number | null;
  last_n_games: number; // > 0 wins over since_days
  min_occurrences: number;
  time_class: TimeClass;
  classifications: BlunderClass[];
  show_dismissed: boolean;
  /** When the list was last looked at (`/blunders/seen`), read once as the page opens; boards that
   *  crossed the threshold after it are marked NEW for the whole stay, whatever the filters. */
  new_since: string | null;
}

export function buildQuery(f: BlunderFilters, page: number): string {
  const q = new URLSearchParams();
  if (f.last_n_games > 0) q.set("last_n_games", String(f.last_n_games));
  else if (f.since_days) q.set("since_days", String(f.since_days));
  q.set("min_occurrences", String(f.min_occurrences));
  q.set("time_class", f.time_class);
  for (const c of f.classifications) q.append("classifications", c);
  if (f.show_dismissed) q.set("show_dismissed", "true");
  if (f.new_since) q.set("new_since", f.new_since);
  q.set("page", String(page));
  return q.toString();
}

/** The page's opening filters, from the settings row (core/blunders.py `default_filters` is the
 *  same recipe server-side, so Home's count is over the list this page opens on), and the marker. */
export function defaultFilters(s: Record<string, unknown>, newSince: string | null = null): BlunderFilters {
  const num = (k: string, d: number) => (typeof s[k] === "number" ? (s[k] as number) : d);
  const byGames = s.blunders_default_filter_mode === "games";
  const classes = Array.isArray(s.blunders_default_classifications) ? s.blunders_default_classifications.filter((c): c is BlunderClass => BLUNDER_CLASSES.includes(c as BlunderClass)) : [];
  return {
    since_days: byGames ? null : num("blunders_default_window_days", 20),
    last_n_games: byGames ? num("blunders_default_last_n_games", 500) : 0,
    min_occurrences: num("blunders_default_min_occurrences", 2),
    time_class: "focus",
    classifications: classes.length ? classes : ["miss", "blunder", "mistake"],
    show_dismissed: false,
    new_since: newSince,
  };
}

export const topClass = (p: Pick<BlunderPosition, "classifications">): BlunderClass | null => BLUNDER_CLASSES.find((c) => (p.classifications[c] ?? 0) > 0) ?? null;

/** A blunder row as the shared position card. */
export function toCard(p: BlunderPosition): PositionCardData {
  return {
    fen: p.fen,
    color: p.color,
    times: p.count,
    score: p.score,
    classifications: p.classifications,
    topClassification: topClass(p),
    context: p.context,
    book: p.book,
    chapter: p.chapter,
    lineNames: p.line_names,
    movePlayed: p.move_played,
    bestMove: p.best_move,
    bestLine: p.best_line,
    cpLoss: p.cp_loss,
    moves: p.moves,
    ply: p.ply,
    chessGameId: p.chess_game_id,
    lastSeen: p.last_seen,
    dismissed: p.dismissed,
    isNew: p.is_new,
    games: p.games,
  };
}


export function daysAgo(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days}d ago`;
}

export const getBlunders = (f: BlunderFilters, page: number) => api.get<BlundersResponse>(`/blunders?${buildQuery(f, page)}`);
export const dismissBoard = (fen: string) => api.post<{ detail: string }>("/blunders/dismiss", { fen });
export const restoreBoard = (fen: string) => api.post<{ detail: string }>("/blunders/restore", { fen });
/** The marker: when the list was last looked at. The page reads it as it opens and moves it once
 *  the list is on screen; Home only reads it, so a new board waits until it has been seen. */
export const getSeen = () => api.get<{ seen_at: string | null }>("/blunders/seen");
export const markSeen = () => api.post<{ seen_at: string }>("/blunders/seen");

// --- explanations ---

export interface PromptLabel {
  key: string;
  label: string;
  model: string;
}
export interface Explanation {
  explanation: string;
  cached: boolean;
  model: string;
  prompt_label: string;
}
export interface DryRun {
  dry_run: true;
  model: string;
  provider: string;
  rendered_prompt: string;
  system_prompt: string;
  temperature: number | null;
  max_tokens: number;
  prefill?: string;
  thinking?: { type: string; budget_tokens?: number };
}

export const getPrompts = () => api.get<{ prompts: PromptLabel[] }>("/blunders/prompts");
export const explain = (chess_game_id: number, ply: number, prompt_key: string) => api.post<Explanation>("/blunders/explain", { chess_game_id, ply, prompt_key });
export const explainDryRun = (chess_game_id: number, ply: number, prompt_key: string) => api.post<DryRun>("/blunders/explain", { chess_game_id, ply, prompt_key, dry_run: true });

/** Exactly what would be sent, as text for the clipboard. */
export function formatDryRun(r: DryRun): string {
  const thinking = r.thinking == null ? "omitted" : r.thinking.type === "enabled" ? `enabled (budget ${r.thinking.budget_tokens ?? 0} tokens)` : r.thinking.type;
  const head = [`MODEL: ${r.model}`, `PROVIDER: ${r.provider}`, `TEMPERATURE: ${r.temperature ?? "default"}`, `THINKING: ${thinking}`, `MAX_TOKENS: ${r.max_tokens}`, `PREFILL: ${r.prefill || "(none)"}`];
  const system = r.system_prompt ? `\n\n=== SYSTEM PROMPT ===\n${r.system_prompt}` : "";
  return `${head.join("\n")}${system}\n\n=== USER PROMPT ===\n${r.rendered_prompt}`;
}

// --- hand-made puzzles ---

export interface NewPuzzle {
  fen: string;
  solution_line: string[];
  color: "w" | "b";
  source_types: Array<"blunder" | "deviation" | "scout">;
  title?: string;
  description?: string;
}
export const createPuzzle = (body: NewPuzzle) => api.post<{ id: number; visible: boolean }>("/puzzles", body);
export const removePuzzle = (id: number) => api.del<{ detail: string }>(`/puzzles/${id}`);
