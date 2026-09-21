/**
 * The one attempt that exists nowhere else.
 *
 * In queue mode an attempt is committed to the durable queue before the page moves on. In
 * blocking mode (no localStorage or no Web Locks) a failed save has no other copy, so it is
 * kept here, outside any component: leaving the page — the header links, the browser's
 * back button, a tab close — must not discard it. The header disables its links while an
 * attempt is held, the Practice page offers Retry for it however it is reached, and the
 * browser asks before unloading.
 *
 * The attempt is held from the moment its request goes out, not from its failure, so a
 * page change mid-request cannot open a second one. While the solver that made it is
 * mounted it is the attempt's owner and shows its own state; once that solver unmounts the
 * attempt is ownerless and the Practice page shows only the recovery banner for it.
 */

export interface UnsavedAttempt {
  puzzle_id: number;
  attempt_id: string;
  session_id: string;
  solved: boolean;
  moves_played: string;
}

let current: UnsavedAttempt | null = null;
let owner: object | null = null;
const listeners = new Set<() => void>();

function notify() {
  for (const l of listeners) l();
}

function onBeforeUnload(e: BeforeUnloadEvent) {
  e.preventDefault();
}

/** Holds one attempt. A different one cannot displace it: the first is the one with no
 *  other copy, and the page does not let a second be made while it is held. */
export function holdUnsavedAttempt(attempt: UnsavedAttempt, by: object | null = null): void {
  if (current !== null && current.attempt_id !== attempt.attempt_id) {
    console.error("An unsaved attempt is already held; refusing to replace it");
    return;
  }
  const wasHeld = current !== null;
  current = attempt;
  owner = by;
  if (!wasHeld) window.addEventListener("beforeunload", onBeforeUnload);
  notify();
}

/** The owning solver is going away (unmount); the attempt stays held, now ownerless. */
export function disownUnsavedAttempt(by: object): void {
  if (owner !== by) return;
  owner = null;
  notify();
}

export function releaseUnsavedAttempt(attemptId?: string): void {
  if (current === null || (attemptId !== undefined && current.attempt_id !== attemptId)) return;
  current = null;
  owner = null;
  window.removeEventListener("beforeunload", onBeforeUnload);
  notify();
}

export function getUnsavedAttempt(): UnsavedAttempt | null {
  return current;
}

/** Is the held attempt's solver still mounted? False when nothing is held. */
export function isUnsavedAttemptOwned(): boolean {
  return current !== null && owner !== null;
}

export function subscribeUnsavedAttempt(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tests only. */
export function _resetUnsavedAttemptForTests(): void {
  current = null;
  owner = null;
  window.removeEventListener("beforeunload", onBeforeUnload);
  listeners.clear();
}
