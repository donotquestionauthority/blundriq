/** The line report's drill queue: the lines the player has chosen to drill, in the order chosen,
 *  for this tab. It survives every filter, view, sort, time-class and scope change and a round trip
 *  to a walk-through, and is gone with the tab (sessionStorage; page state alone when storage is
 *  absent or throws). The Practice page clears it when a drill reaches Done, not when it is served,
 *  so a Stop or a serve that skipped every line keeps it. */
import { useSyncExternalStore } from "react";

const KEY = "report-drill-queue";

let queue: number[] = load();
const listeners = new Set<() => void>();

function load(): number[] {
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is number => typeof x === "number" && Number.isInteger(x)) : [];
  } catch {
    return [];
  }
}

function write(next: number[]) {
  queue = next;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage absent or full: the queue lives in this module for the visit.
  }
  for (const l of listeners) l();
}

export const readQueue = () => queue;

/** Add `ids` (those not yet queued, in the given order) or remove them all. */
export function setQueued(ids: number[], on: boolean) {
  const have = new Set(queue);
  if (on) {
    const added = ids.filter((id) => !have.has(id));
    if (added.length > 0) write([...queue, ...added]);
  } else {
    const drop = new Set(ids);
    if (queue.some((id) => drop.has(id))) write(queue.filter((id) => !drop.has(id)));
  }
}

export const clearQueue = () => {
  if (queue.length > 0) write([]);
};

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/** The queue as React state: re-renders on every change from any component. */
export const useDrillQueue = () => useSyncExternalStore(subscribe, readQueue, readQueue);

/** Tests: start from a given queue without touching storage semantics. */
export const resetQueueForTests = (ids: number[] = []) => write(ids);
