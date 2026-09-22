/** Types and calls for the Home page. */
import { api } from "./api";

export interface HomePage {
  /** When the Blunders list was last looked at — the boundary of "new"; null before the first look. */
  since: string | null;
  today: string;
  timezone: string;
  /** Boards that crossed the Blunders threshold since then; they wait here until the list is looked at. */
  new_blunders: number;
  puzzles: { due: number; solved_today: number; target: number; streak: number };
  games: { today: number; week: number; target: number; streak: number };
  pipeline: {
    /** When the hourly chain last ran through to its last step. */
    last_ok_at: string | null;
    /** Hourly steps whose most recent run failed, in chain order. */
    failed: Array<{ step: string; started_at: string | null; error: string | null }>;
  };
}

export const getHome = () => api.get<HomePage>("/home");

/** "3 minutes ago", "2 hours ago", "4 days ago" — for the pipeline line. */
export function ago(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.floor(h / 24);
  return `${d} days ago`;
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
