/**
 * Puzzles whose removal has been requested and not yet answered.
 *
 * Kept outside any component: a removal is a request in flight against the server, and the
 * overlay that started it can be closed, the row reopened, the page re-entered, before the
 * answer comes back. Every solver checks here and locks its board while its puzzle is on the
 * list, so no attempt can be made on a puzzle that may already be gone (an attempt that lands
 * after the removal is refused by the server and could never be credited).
 */

const removing = new Set<number>();
const listeners = new Set<() => void>();
let snapshot: ReadonlySet<number> = new Set();

function notify() {
  snapshot = new Set(removing);
  for (const l of listeners) l();
}

export function beginRemoval(puzzleId: number): void {
  removing.add(puzzleId);
  notify();
}

export function endRemoval(puzzleId: number): void {
  removing.delete(puzzleId);
  notify();
}

/** For useSyncExternalStore: a fresh set after every change, the same one between changes. */
export function getRemovals(): ReadonlySet<number> {
  return snapshot;
}

export function subscribeRemovals(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function _resetRemovalsForTests(): void {
  removing.clear();
  notify();
}
