/** Types and calls for the Home page. */
import { api } from "./api";

export interface HomePage {
  /** When the Blunders list was last looked at — the boundary of "new"; null before the first look. */
  since: string | null;
  today: string;
  timezone: string;
  /** Boards that crossed the Blunders threshold since then; they wait here until the list is looked at. */
  new_blunders: number;
  /** When the Deviations list was last looked at; null before the first look. */
  deviations_since: string | null;
  /** Deviation patterns the Deviations list has never shown. */
  new_deviations: number;
  puzzles: { due: number; solved_today: number; target: number; streak: number };
  games: { today: number; week: number; target: number; streak: number };
  /** Games played in the last 24 h / 7 d / 30 d / ever, every variant. */
  activity: { last_1: number; last_7: number; last_30: number; total: number };
  pipeline: {
    /** The most recent run of the chain: when its import started and what became of it. `running`
     *  is "no result yet and young enough to still be going"; `incomplete` is the same past the
     *  job's timeout — the job died. Null before the first run. */
    last_run: { started_at: string; status: RunStatus; failed_step: string | null } | null;
    /** When the hourly chain last ran through to its last step. */
    last_ok_at: string | null;
    /** Hourly steps whose most recent run failed, in chain order. */
    failed: Array<{ step: string; started_at: string | null; error: string | null }>;
  };
}

export type RunStatus = "ok" | "failed" | "running" | "incomplete";

export const getHome = () => api.get<HomePage>("/home");

/** Ask GitHub to run the hourly workflow now; the time of the accepted request. */
export const runPipeline = () => api.post<{ requested_at: string }>("/home/pipeline/run");

/** How often Home re-reads while a run is in progress, and how long after a request it waits
 *  for the run's first row before saying nothing started (the job's setup takes 1–2 minutes). */
export const POLL_MS = 15_000;
export const START_WAIT_MS = 5 * 60_000;

/** The status line's wording for a run. */
export function runLabel(run: NonNullable<HomePage["pipeline"]["last_run"]>): string {
  switch (run.status) {
    case "ok":
      return "ok";
    case "failed":
      return run.failed_step ? `failed (${run.failed_step})` : "failed";
    case "running":
      return "running";
    case "incomplete":
      return "did not finish";
  }
}

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
