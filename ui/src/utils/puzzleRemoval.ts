/**
 * Puzzles that must not be played: a removal is in flight, or the server has confirmed the
 * puzzle is gone.
 *
 * Kept outside any component. The overlay that asks for a removal can be closed, the row
 * reopened, the page left and re-entered, before the answer arrives — and other copies of the
 * puzzle may be mounted at the same time (the due queue under a deep link, an earlier list
 * row). Every solver and every list consults this store, so:
 *
 *   * while the request is **pending**, every copy is locked and the page waits;
 *   * once the server says the puzzle is **gone** (200, or a 404 saying it already was), it
 *     stays unplayable for the rest of the page load and every list and queue drops it, so
 *     no copy can accept a move the server would refuse;
 *   * a **failed** request clears the lock and play resumes.
 */

const pending = new Set<number>();
const gone = new Set<number>();
const listeners = new Set<() => void>();
let snapshot: ReadonlySet<number> = new Set();

function notify() {
  snapshot = new Set([...pending, ...gone]);
  for (const l of listeners) l();
}

export function beginRemoval(puzzleId: number): void {
  pending.add(puzzleId);
  notify();
}

/** The server refused or could not be reached: the puzzle is still there and playable. */
export function removalFailed(puzzleId: number): void {
  pending.delete(puzzleId);
  notify();
}

/** The server confirmed the puzzle no longer exists (for this page load, permanently). */
export function confirmGone(puzzleId: number): void {
  pending.delete(puzzleId);
  gone.add(puzzleId);
  notify();
}

/** Ids no solver may play: pending and gone together. For useSyncExternalStore: a fresh set
 *  after every change, the same one between changes. */
export function getUnplayable(): ReadonlySet<number> {
  return snapshot;
}

export function isGone(puzzleId: number): boolean {
  return gone.has(puzzleId);
}

export function subscribeRemovals(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function _resetRemovalsForTests(): void {
  pending.clear();
  gone.clear();
  notify();
}
