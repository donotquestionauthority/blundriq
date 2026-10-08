/**
 * Today's puzzle count, shared by every place that shows it (the Practice toolbar and the
 * puzzle dialog), so they always agree and one read serves them all.
 *
 * The store reads while anything is subscribed: on the first subscriber, after every
 * acknowledged attempt, when the tab becomes visible again, and once at the start of the next
 * day (the server says when that is, in the `timezone` setting). It never polls. The newest
 * read wins: an older answer arriving late is dropped. A failed read shows nothing rather than
 * a stale number, and never gets in the way of practising; the next trigger reads again. When
 * the last subscriber leaves, the timer and listeners go, and a read still in flight is
 * disowned.
 */
import { useSyncExternalStore } from "react";
import { ATTEMPT_SAVED_EVENT, getPracticeToday } from "./practice";
import type { PracticeToday } from "./practice";

let snapshot: PracticeToday | null = null;
let latest = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

/** setTimeout's ceiling; a longer wait is clamped and the timer re-armed by the read it fires. */
const MAX_DELAY_MS = 2 ** 31 - 1;
/** Read a moment after the boundary, so the server is already in the new day. */
const PAST_BOUNDARY_MS = 1000;

function notify() {
  for (const l of listeners) l();
}

function clearTimer() {
  if (timer !== null) clearTimeout(timer);
  timer = null;
}

/** Arms the rollover read from the server's own clock, so a browser clock that runs ahead or
 *  behind neither reads early (and again, and again) nor late. */
function arm(today: PracticeToday) {
  clearTimer();
  const until = Date.parse(today.next_day_at) - Date.parse(today.now);
  if (Number.isNaN(until)) return;
  timer = setTimeout(
    () => {
      timer = null;
      void refreshPracticeToday();
    },
    Math.min(Math.max(until, 0) + PAST_BOUNDARY_MS, MAX_DELAY_MS),
  );
}

/** Reads the count now, if anything is showing it. */
export async function refreshPracticeToday(): Promise<void> {
  if (listeners.size === 0) return;
  const mine = ++latest;
  try {
    const today = await getPracticeToday();
    if (mine !== latest) return;
    snapshot = today;
    arm(today);
  } catch (e) {
    if (mine !== latest) return;
    snapshot = null;
    clearTimer();
    console.warn(`Today's puzzle count could not be read (${e instanceof Error ? e.name : typeof e})`);
  }
  notify();
}

function onSaved() {
  void refreshPracticeToday();
}

function onVisibility() {
  if (document.visibilityState === "visible") void refreshPracticeToday();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) {
    window.addEventListener(ATTEMPT_SAVED_EVENT, onSaved);
    document.addEventListener("visibilitychange", onVisibility);
    void refreshPracticeToday();
  }
  return () => {
    if (!listeners.delete(listener) || listeners.size > 0) return;
    window.removeEventListener(ATTEMPT_SAVED_EVENT, onSaved);
    document.removeEventListener("visibilitychange", onVisibility);
    clearTimer();
    latest++; // a read still in flight answers no one
    snapshot = null;
  };
}

function getSnapshot(): PracticeToday | null {
  return snapshot;
}

/** Today's count, or null while it is unknown (not read yet, or the read failed). */
export function usePracticeToday(): PracticeToday | null {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function _resetPracticeTodayForTests(): void {
  listeners.clear();
  window.removeEventListener(ATTEMPT_SAVED_EVENT, onSaved);
  document.removeEventListener("visibilitychange", onVisibility);
  clearTimer();
  latest++;
  snapshot = null;
}
