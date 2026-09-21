/**
 * The one attempt that exists nowhere else.
 *
 * In queue mode an attempt is committed to the durable queue before the page moves on. In
 * blocking mode (no localStorage or no Web Locks) a failed save has no other copy, so it is
 * kept here, outside any component: leaving the page — the header links, the browser's
 * back button, a tab close — must not discard it. The header disables its links while an
 * attempt is held, the Practice page offers Retry for it however it is reached, and the
 * browser asks before unloading.
 */

export interface UnsavedAttempt {
  puzzle_id: number;
  attempt_id: string;
  session_id: string;
  solved: boolean;
  moves_played: string;
}

let current: UnsavedAttempt | null = null;
const listeners = new Set<() => void>();

function notify() {
  for (const l of listeners) l();
}

function onBeforeUnload(e: BeforeUnloadEvent) {
  e.preventDefault();
}

/** Holds one attempt. A different one cannot displace it: the first is the one with no
 *  other copy, and the page does not let a second be made while it is held. */
export function holdUnsavedAttempt(attempt: UnsavedAttempt): void {
  if (current !== null && current.attempt_id !== attempt.attempt_id) {
    console.error("An unsaved attempt is already held; refusing to replace it");
    return;
  }
  const wasHeld = current !== null;
  current = attempt;
  if (!wasHeld) window.addEventListener("beforeunload", onBeforeUnload);
  notify();
}

export function releaseUnsavedAttempt(attemptId?: string): void {
  if (current === null || (attemptId !== undefined && current.attempt_id !== attemptId)) return;
  current = null;
  window.removeEventListener("beforeunload", onBeforeUnload);
  notify();
}

export function getUnsavedAttempt(): UnsavedAttempt | null {
  return current;
}

export function subscribeUnsavedAttempt(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tests only. */
export function _resetUnsavedAttemptForTests(): void {
  current = null;
  window.removeEventListener("beforeunload", onBeforeUnload);
  listeners.clear();
}
