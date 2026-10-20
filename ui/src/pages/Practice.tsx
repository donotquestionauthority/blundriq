import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Link, useSearchParams } from "react-router";
import { ApiError } from "../api";
import { removePuzzle } from "../blunders";
import { beginRemoval, confirmGone, getUnplayable, isGone, removalFailed, subscribeRemovals } from "../utils/puzzleRemoval";
import { useApi } from "../hooks/useApi";
import { disownUnsavedAttempt, getUnsavedAttempt, holdUnsavedAttempt, isUnsavedAttemptOwned, releaseUnsavedAttempt, subscribeUnsavedAttempt, type UnsavedAttempt } from "../utils/unsavedAttempt";
import { PuzzleEngine } from "../components/PuzzleEngine";
import { getDrill, getPuzzleById, getPuzzles, getRepertoireScopes, isRotationPuzzle, LAST_N_OPTIONS, recordAttempt, skipPuzzle } from "../practice";
import { reportPath } from "../repertoire";
import { clearQueue } from "../utils/drillQueue";
import type { PlayablePuzzlePayload, PracticeType, Puzzle, RepertoireScopeBook, PuzzleGameLink, PuzzleSrs, SrsFilter, SrsLevel } from "../practice";
import { enqueue as enqueueAttempt, hasPendingAttempt, hasPendingAttemptForPuzzle, initQueueTriggers, isQueueModeAvailable, markCompleted as markAttemptCompleted, type PendingAttempt } from "../utils/attemptQueue";
import { RepertoireScopeFilter } from "../components/RepertoireScopeFilter";
import TodayCount from "../components/TodayCount";
import { errorLabel } from "../utils/errorLabel";

/**
 * Practice: the puzzle queue. `srs=due` is the play queue (a streaming batch consumer);
 * `srs=all` and `srs=retired` are lists, each row opening the solver in an overlay.
 * `?puzzle=<id>` deep-links straight into the overlay. `?drill=<ids>` is a drill instead of
 * the queue; `from=list` says it came from a list here, and the list's own params ride along.
 *
 * Entry query params are adopted into state whenever the URL carries them and no drill is
 * running, then stripped from the URL — on first render, and again when a drill's Back link
 * lands here with the list's params (the route is one component, so nothing else re-reads them).
 * While a drill runs they stay, so a reload serves the same drill and comes back to the same list:
 *   type = all | motif | repertoire | blunder | custom · subtype · srs = due | all | retired ·
 *   last_n_games · puzzle
 */

const noop = () => {};
const ENTRY_KEYS = ["type", "subtype", "srs", "last_n_games", "puzzle", "from"];
const PLAY_TYPES: PracticeType[] = ["all", "motif", "repertoire", "blunder", "custom"];
const TYPE_LABEL: Record<PracticeType, string> = { all: "All", motif: "Motif", repertoire: "Repertoire", blunder: "Blunder", custom: "Custom" };
// A custom puzzle's subtype is the page it was made from (core/puzzles/custom.py CONTEXT_TAGS).
const CUSTOM_SUBTYPES = ["blunder", "deviation", "scout"] as const;
const CUSTOM_SUBTYPE_LABEL: Record<(typeof CUSTOM_SUBTYPES)[number], string> = { blunder: "From Blunders", deviation: "From Deviations", scout: "From Scout" };

function isPracticeType(v: unknown): v is PracticeType {
  return PLAY_TYPES.includes(v as PracticeType);
}
function isSrsFilter(v: unknown): v is SrsFilter {
  return v === "due" || v === "all" || v === "retired";
}

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const chip = (active: boolean) =>
  `rounded border px-2.5 py-1 text-xs font-medium ${
    active ? "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-300" : "border-zinc-300 text-zinc-500 hover:text-zinc-800 dark:border-zinc-700 dark:hover:text-zinc-200"
  }`;

// ─── Badges ────────────────────────────────────────────────────────────────

function SourceBadge({ type }: { type: string }) {
  const tones: Record<string, string> = {
    blunder: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
    deviation: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  };
  return <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${tones[type] ?? "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"}`}>{type}</span>;
}

const LEVEL_ICON: Record<SrsLevel, string> = { pawn: "♙", knight: "♘", bishop: "♗", rook: "♖", queen: "♕", king: "♔" };

function LevelBadge({ srs, isRotation }: { srs: PuzzleSrs | null; isRotation: boolean }) {
  // Rotation puzzles never join the ladder, so "new" would be permanently (and wrongly) shown.
  if (isRotation) return null;
  if (srs === null) return <span className="inline-block rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-sky-800 dark:bg-sky-900/40 dark:text-sky-300">new</span>;
  return (
    <span className="inline-flex items-center gap-1 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
      <span aria-hidden className="text-sm leading-none">{LEVEL_ICON[srs.level]}</span>
      {srs.level}
    </span>
  );
}

function GameLinks({ puzzle }: { puzzle: Puzzle }) {
  const row = (link: PuzzleGameLink | Puzzle["correct_game_links"][number], i: number) => (
    <div key={i} className="flex items-center gap-2 text-xs">
      {"source_type" in link && <SourceBadge type={link.source_type} />}
      {link.opponent && <span>vs {link.opponent}</span>}
      <span className="text-zinc-500">{link.date}</span>
      {link.url && (
        <a href={link.url} target="_blank" rel="noreferrer" className="ml-auto text-zinc-500 underline hover:text-zinc-900 dark:hover:text-zinc-100">
          view
        </a>
      )}
    </div>
  );
  return (
    <>
      {puzzle.game_links.length > 0 && (
        <details className="mt-4 border-t border-zinc-200 pt-3 dark:border-zinc-800">
          <summary className="cursor-pointer text-xs uppercase tracking-wide text-zinc-500">Your games with this position ({puzzle.game_links.length})</summary>
          <div className="mt-2 max-h-32 space-y-1 overflow-y-auto">{puzzle.game_links.slice(0, 10).map(row)}</div>
        </details>
      )}
      {puzzle.correct_game_links.length > 0 && (
        <div className="mt-4 border-t border-zinc-200 pt-3 dark:border-zinc-800">
          <div className="mb-2 text-xs uppercase tracking-wide text-emerald-600 dark:text-emerald-400">Games where you played this correctly ({puzzle.correct_game_links.length})</div>
          <div className="max-h-32 space-y-1 overflow-y-auto">{puzzle.correct_game_links.slice(0, 10).map(row)}</div>
        </div>
      )}
    </>
  );
}

function PuzzleHeader({ puzzle, note }: { puzzle: Puzzle; note?: string | null }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
      <span className="font-mono">#{puzzle.id}</span>
      <LevelBadge srs={puzzle.srs} isRotation={isRotationPuzzle(puzzle)} />
      {puzzle.source_types.map((s) => (
        <SourceBadge key={s} type={s} />
      ))}
      {(puzzle.themes ?? []).length > 0 && <span>{(puzzle.themes ?? []).join(", ")}</span>}
      {puzzle.title && <span className="text-zinc-700 dark:text-zinc-300">{puzzle.title}</span>}
      {puzzle.occurrence_count > 0 && <span>encountered {puzzle.occurrence_count}× in your games</span>}
      {note && <span className="text-emerald-600 dark:text-emerald-400">· {note}</span>}
    </div>
  );
}

// ─── Attempt submission ─────────────────────────────────────────────────────

/** Ids no solver may play right now: a removal in flight, or confirmed gone (utils/puzzleRemoval). */
function useUnplayable(): ReadonlySet<number> {
  return useSyncExternalStore(subscribeRemovals, getUnplayable, getUnplayable);
}


const ATTEMPT_RESOLVED_EVENT = "blundriq:attempt-resolved";

/**
 * Records one puzzle's attempts. Queue mode: the attempt is committed to the durable queue
 * BEFORE the UI moves on (enqueue-before-navigation), then POSTed in the foreground with the
 * queue retrying any failure. Blocking mode (no localStorage or no Web Locks): navigation is
 * disabled until the POST settles, and a failure shows an explicit error with Retry. Never
 * fire-and-forget.
 *
 * One session_id per play-through (the caller calls `reset()` when it moves to another
 * puzzle, or remounts), stable across wrong-answer retries; one attempt_id per submission.
 * Every attempt carries `presentationPly`, the segment the solver it was made on displays,
 * on every path (foreground, queued retry, blocking retry, the held-attempt banner), and
 * `attempt_seq`, its order within the play-through from 1, so the server knows which attempt
 * of a load was the first whatever order the requests arrive in. `onVerdict` hears every
 * verdict the solver produces with that sequence, before anything is sent.
 */
function useAttemptSubmit(puzzleId: number, onRecorded: () => void, presentationPly: number | null, onVerdict?: (seq: number, solved: boolean) => void) {
  const [lastResult, setLastResult] = useState<"solved" | "wrong" | null>(null);
  const [serverDowngraded, setServerDowngraded] = useState(false);
  const [attemptStatus, setAttemptStatus] = useState<"in_flight" | "pending_retry" | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The failed attempt keeps the puzzle it was made on: a retry must go there, whatever the
  // hook is showing by the time it is clicked.
  const [blockingError, setBlockingError] = useState<{ attemptId: string; solved: boolean; movesPlayed: string[]; puzzleId: number; sessionId: string; presentationPly: number | null; seq: number } | null>(null);
  const inFlightRef = useRef<Set<string>>(new Set());
  // The attempt the server is still owed in blocking mode: in flight, or failed and waiting
  // for Retry. Only one may exist; handleComplete refuses to open another while it stands.
  const outstandingRef = useRef<string | null>(null);
  // This hook instance, as the held attempt's owner while it is mounted.
  const ownerRef = useRef<object>({});
  useEffect(() => {
    const owner = ownerRef.current;
    return () => disownUnsavedAttempt(owner);
  }, []);
  const activePuzzleIdRef = useRef(puzzleId);
  useEffect(() => {
    activePuzzleIdRef.current = puzzleId;
  }, [puzzleId]);
  const presentationPlyRef = useRef(presentationPly);
  useEffect(() => {
    presentationPlyRef.current = presentationPly;
  }, [presentationPly]);
  const pendingRetryUuidRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string>(crypto.randomUUID());
  const seqRef = useRef(0);
  const onVerdictRef = useRef(onVerdict);
  useEffect(() => {
    onVerdictRef.current = onVerdict;
  }, [onVerdict]);

  useEffect(() => {
    const onResolved = (e: Event) => {
      const detail = (e as CustomEvent<{ attempt_id: string }>).detail;
      if (!detail || pendingRetryUuidRef.current !== detail.attempt_id) return;
      pendingRetryUuidRef.current = null;
      setAttemptStatus(null);
    };
    window.addEventListener(ATTEMPT_RESOLVED_EVENT, onResolved);
    return () => window.removeEventListener(ATTEMPT_RESOLVED_EVENT, onResolved);
  }, []);

  // A new play-through: clear the previous verdict and start a new session.
  const reset = useCallback(() => {
    setLastResult(null);
    setServerDowngraded(false);
    setAttemptStatus(null);
    setBlockingError(null);
    pendingRetryUuidRef.current = null;
    outstandingRef.current = null;
    sessionIdRef.current = crypto.randomUUID();
    seqRef.current = 0;
  }, []);

  // The attempt names its puzzle, its session and the segment it was made on: a retry, or a
  // slow response, never posts to whatever the hook is showing by then, nor under a session
  // started since, nor against a segment the solver was not displaying.
  const post = useCallback(
    (attemptPuzzleId: number, sessionId: string, attemptId: string, solved: boolean, movesPlayed: string[], shownPly: number | null, seq: number) =>
      recordAttempt(attemptPuzzleId, { solved, moves_played: movesPlayed.join(","), attempt_id: attemptId, session_id: sessionId, presentation_ply: shownPly, attempt_seq: seq }),
    [],
  );

  const runBlockingMode = useCallback(
    async (attemptId: string, solved: boolean, movesPlayed: string[], attemptPuzzleId: number, sessionId: string, shownPly: number | null, seq: number) => {
      outstandingRef.current = attemptId;
      // Held from before the request goes out, not from its failure: the whole app treats the
      // attempt as unsaved until its id is acknowledged, so leaving and returning mid-request
      // cannot open a second one.
      const payload = { puzzle_id: attemptPuzzleId, attempt_id: attemptId, session_id: sessionId, solved, moves_played: movesPlayed.join(","), presentation_ply: shownPly, attempt_seq: seq };
      holdUnsavedAttempt(payload, ownerRef.current);
      setSubmitting(true);
      setBlockingError(null);
      setServerDowngraded(false);
      try {
        const response = await post(attemptPuzzleId, sessionId, attemptId, solved, movesPlayed, shownPly, seq);
        releaseUnsavedAttempt(attemptId);
        outstandingRef.current = null;
        if (activePuzzleIdRef.current === attemptPuzzleId) {
          setLastResult(response.solved ? "solved" : "wrong");
          if (solved && !response.solved) setServerDowngraded(true);
        }
        onRecorded();
      } catch (err) {
        console.error("Blocking-mode recordAttempt failed:", errorLabel(err));
        // Still held (since before the request) unless a retry from elsewhere — the recovery
        // banner, after this solver was left — landed it meanwhile.
        if (getUnsavedAttempt()?.attempt_id === attemptId) {
          if (activePuzzleIdRef.current === attemptPuzzleId) setBlockingError({ attemptId, solved, movesPlayed, puzzleId: attemptPuzzleId, sessionId, presentationPly: shownPly, seq });
        } else {
          outstandingRef.current = null;
          onRecorded();
        }
      } finally {
        setSubmitting(false);
        inFlightRef.current.delete(attemptId);
      }
    },
    [post, onRecorded],
  );

  const handleComplete = useCallback(
    async (solved: boolean, movesPlayed: string[]) => {
      if (outstandingRef.current !== null || getUnsavedAttempt() !== null) {
        console.error("An attempt is still unsaved; refusing to open another");
        return;
      }
      const attemptId = crypto.randomUUID();
      if (inFlightRef.current.has(attemptId)) return;
      inFlightRef.current.add(attemptId);
      const attemptPuzzleId = puzzleId;
      const sessionId = sessionIdRef.current;
      const shownPly = presentationPlyRef.current;
      const seq = ++seqRef.current;
      onVerdictRef.current?.(seq, solved);

      if (!isQueueModeAvailable()) {
        await runBlockingMode(attemptId, solved, movesPlayed, attemptPuzzleId, sessionId, shownPly, seq);
        return;
      }
      try {
        await enqueueAttempt({ attempt_id: attemptId, puzzle_id: attemptPuzzleId, solved, moves_played: movesPlayed.join(","), session_id: sessionId, presentation_ply: shownPly, attempt_seq: seq });
      } catch (err) {
        console.warn("Queue enqueue failed, falling back to blocking mode:", errorLabel(err));
        inFlightRef.current.delete(attemptId);
        await runBlockingMode(attemptId, solved, movesPlayed, attemptPuzzleId, sessionId, shownPly, seq);
        return;
      }

      // Committed to storage: the UI may move on now.
      setLastResult(solved ? "solved" : "wrong");
      setServerDowngraded(false);
      setAttemptStatus("in_flight");
      try {
        const response = await post(attemptPuzzleId, sessionId, attemptId, solved, movesPlayed, shownPly, seq);
        await markAttemptCompleted(attemptId);
        if (activePuzzleIdRef.current === attemptPuzzleId) {
          setAttemptStatus(null);
          if (response.solved !== solved) {
            setLastResult(response.solved ? "solved" : "wrong");
            if (solved && !response.solved) setServerDowngraded(true);
          }
        }
        onRecorded();
      } catch (err) {
        console.error("Foreground recordAttempt failed; queued for retry:", errorLabel(err));
        if (activePuzzleIdRef.current === attemptPuzzleId) {
          // A background tick may already have won the race; only flag a still-queued attempt.
          if (hasPendingAttempt(attemptId)) {
            setAttemptStatus("pending_retry");
            pendingRetryUuidRef.current = attemptId;
          } else {
            setAttemptStatus(null);
          }
        }
      } finally {
        inFlightRef.current.delete(attemptId);
      }
    },
    [puzzleId, post, runBlockingMode, onRecorded],
  );

  const retry = useCallback(() => {
    if (!blockingError) return;
    const { attemptId, solved, movesPlayed, puzzleId: attemptPuzzleId, sessionId, presentationPly: shownPly, seq } = blockingError;
    inFlightRef.current.delete(attemptId);
    void runBlockingMode(attemptId, solved, movesPlayed, attemptPuzzleId, sessionId, shownPly, seq);
  }, [blockingError, runBlockingMode]);

  return { lastResult, serverDowngraded, attemptStatus, submitting, blockingError, handleComplete, retry, reset, navigationBlocked: submitting || blockingError !== null };
}

/** Retry for an attempt held across a page change; the same attempt id, so a save that
 *  did land server-side replays rather than scoring twice. */
function HeldAttemptBanner({ attempt, onSaved }: { attempt: UnsavedAttempt; onSaved: () => void }) {
  const [submitting, setSubmitting] = useState(false);
  const retry = async () => {
    setSubmitting(true);
    try {
      await recordAttempt(attempt.puzzle_id, { solved: attempt.solved, moves_played: attempt.moves_played, attempt_id: attempt.attempt_id, session_id: attempt.session_id, presentation_ply: attempt.presentation_ply, ...(attempt.attempt_seq != null ? { attempt_seq: attempt.attempt_seq } : {}) });
      releaseUnsavedAttempt(attempt.attempt_id);
      onSaved();
    } catch (err) {
      console.error("Retry of a held attempt failed:", errorLabel(err));
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <div className="mt-4 flex items-center justify-between gap-3 rounded border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-800 dark:bg-rose-900/30 dark:text-rose-300">
      <span>Your attempt on puzzle #{attempt.puzzle_id} has not been saved — please retry to continue.</span>
      <button type="button" onClick={() => void retry()} disabled={submitting} className="shrink-0 rounded border border-rose-300 px-2 py-0.5 text-xs font-medium disabled:opacity-50 dark:border-rose-800">
        {submitting ? "Retrying…" : "Retry"}
      </button>
    </div>
  );
}

function BlockingBanner({ error, submitting, onRetry }: { error: unknown; submitting: boolean; onRetry: () => void }) {
  if (error) {
    return (
      <div className="mt-3 flex items-center justify-between gap-3 rounded border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-800 dark:bg-rose-900/30 dark:text-rose-300">
        <span>Couldn't save your attempt — please retry to continue.</span>
        <button type="button" onClick={onRetry} disabled={submitting} className="shrink-0 rounded border border-rose-300 px-2 py-0.5 text-xs font-medium disabled:opacity-50 dark:border-rose-800">
          {submitting ? "Retrying…" : "Retry"}
        </button>
      </div>
    );
  }
  if (submitting) return <p className="mt-3 text-center text-xs text-zinc-500">Saving attempt…</p>;
  return null;
}

// ─── Play mode: the streaming queue ─────────────────────────────────────────

/** Where the queue would say "Loading…" after a re-read that failed: the way to ask again. */
function RetryLoad({ onRetry }: { onRetry: () => void }) {
  return (
    <p className="mt-4 text-center text-xs text-zinc-500">
      More puzzles could not be loaded.{" "}
      <button type="button" onClick={onRetry} className="underline">
        Try again
      </button>
    </p>
  );
}

/**
 * The parent passes the LATEST served batch; PlayMode keeps its own growing `items` queue,
 * appending each row whose `play_batch_id` has not been seen (never a global puzzle-id dedup,
 * and never resetting the cursor). A skip is a server-recorded defer. "All caught up" is
 * terminal only when the server returns an empty batch.
 */
function PlayMode({
  batch,
  batchId,
  ptype,
  subtype,
  allCaughtUp,
  refreshFailed,
  prefetchThreshold,
  onAttemptRecorded,
  onNeedRefetch,
  onNavigationLock,
}: {
  batch: Puzzle[];
  batchId: number | null;
  ptype: PracticeType;
  subtype: string | null;
  allCaughtUp: boolean;
  /** The last re-read of the queue failed (the rows shown are the ones before it). */
  refreshFailed: boolean;
  prefetchThreshold: number;
  onAttemptRecorded: () => void;
  onNeedRefetch: () => void;
  onNavigationLock: (locked: boolean) => void;
}) {
  // Every hook precedes the `if (!puzzle)` early return: on a batch boundary `puzzle` is briefly
  // undefined, and a hook declared after that return would be skipped on that render.
  const batchKeyOf = (p: Puzzle): number | "null" => p.play_batch_id ?? batchId ?? "null";
  // The queue only ever grows; an entry keeps its position for the life of the page. The cursor
  // is a POSITION in it, so a retirement earlier in the queue cannot move what is showing, nor
  // hand the showing solver's hook another puzzle. A retired entry is not shown: stepping over
  // it is how the cursor passes it, and if it is the one showing, the cursor steps off it.
  const [queued, setQueued] = useState<Puzzle[]>(() => batch);
  const unplayable = useUnplayable();
  const appendedBatchIdsRef = useRef<Set<number | "null">>(new Set(batch.map((p) => p.play_batch_id ?? batchId ?? "null")));
  const [cursor, setCursor] = useState(0);
  const [awaitingNext, setAwaitingNext] = useState(false);
  const [skipping, setSkipping] = useState(false);
  // At most one prefetch per (newest batch, cursor position): re-armed on every advance, so a
  // prefetch the server answered with the same batch (its pending count still above the mint
  // threshold while an ack was in flight) does not disarm prefetching for the rest of the batch.
  const prefetchedForRef = useRef<string | null>(null);

  useEffect(() => {
    if (batch.length === 0) return;
    const fresh = batch.filter((p) => !appendedBatchIdsRef.current.has(batchKeyOf(p)));
    if (fresh.length === 0) return;
    for (const p of fresh) appendedBatchIdsRef.current.add(batchKeyOf(p));
    setQueued((prev) => [...prev, ...fresh]);
    setAwaitingNext(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch]);

  const live = (i: number) => i < queued.length && !isGone(queued[i].id);
  const nextLive = (from: number) => {
    let i = from;
    while (i < queued.length && !live(i)) i++;
    return i; // queued.length when nothing live remains
  };
  const prevLive = (from: number) => {
    let i = from;
    while (i >= 0 && !live(i)) i--;
    return i; // -1 when nothing live precedes
  };
  const puzzle: Puzzle | undefined = live(cursor) ? queued[cursor] : undefined;
  const liveAhead = queued.slice(cursor + 1).filter((p) => !isGone(p.id)).length;
  // A save that lands while the cursor waits at the end of the queue is what lets the server
  // mint again: the boundary refetch may have run before the acknowledgement committed, and
  // nothing else would ask again.
  const awaitingNextRef = useRef(false);
  awaitingNextRef.current = awaitingNext;
  const recorded = useCallback(() => {
    onAttemptRecorded();
    if (awaitingNextRef.current) onNeedRefetch();
  }, [onAttemptRecorded, onNeedRefetch]);
  const attempt = useAttemptSubmit(puzzle?.id ?? -1, recorded, puzzle?.presentation_ply ?? null);
  const beingRemoved = puzzle !== undefined && unplayable.has(puzzle.id);
  const showNext = attempt.lastResult !== null;
  // The showing entry was retired (from a deep link over it, or another copy): step off it to
  // the next live entry, or to the end of the queue and ask for more. Nothing can be owed on
  // it — a removal is refused while any attempt on the puzzle is unsaved — so the hook resets.
  const attemptReset = attempt.reset;
  useEffect(() => {
    if (cursor >= queued.length || live(cursor)) return;
    attemptReset();
    const next = nextLive(cursor + 1);
    setCursor(next);
    if (next >= queued.length) {
      setAwaitingNext(true);
      onNeedRefetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor, queued, unplayable]);
  // While an attempt is unsaved (blocking mode), every way of leaving this puzzle is closed:
  // Previous, the type tabs and the filters would all discard the only copy of it.
  const navigationBlocked = attempt.navigationBlocked;
  useEffect(() => {
    onNavigationLock(navigationBlocked);
    return () => onNavigationLock(false);
  }, [navigationBlocked, onNavigationLock]);

  // Look-ahead prefetch. The server mints when `pending <= threshold`, and pending INCLUDES the
  // displayed un-acknowledged item, so fire at `remainingAhead + 1 <= threshold`; firing one
  // advance earlier is refused by the server every time.
  // A batch that has arrived but is not yet in the queue is not looked ahead over: the render
  // before its rows are appended sees the new batch id with the old count and would ask again.
  const batchQueued = batch.every((p) => queued.some((q) => q.id === p.id));
  useEffect(() => {
    if (queued.length === 0 || !batchQueued) return;
    if (liveAhead + 1 > prefetchThreshold) return;
    if (allCaughtUp) return;
    const key = `${batchId ?? "null"}|${cursor}`;
    if (prefetchedForRef.current === key) return;
    prefetchedForRef.current = key;
    onNeedRefetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor, liveAhead, batchId, batchQueued, prefetchThreshold, allCaughtUp]);

  if (!puzzle) {
    if (allCaughtUp) {
      return (
        <div className="rounded border border-zinc-200 px-6 py-12 text-center dark:border-zinc-800">
          <p className="text-lg text-zinc-500">You're all caught up</p>
          <p className="mt-2 text-sm text-zinc-500">No more puzzles to serve right now.</p>
        </div>
      );
    }
    if (refreshFailed) return <RetryLoad onRetry={onNeedRefetch} />;
    return <p className="px-6 py-12 text-center text-sm text-zinc-500">Loading next puzzle…</p>;
  }

  const isLastQueued = liveAhead === 0;

  // At the boundary the cursor steps to ONE PAST the consumed item (clamped, so repeated Next
  // while awaiting cannot skip an unseen appended item); when the next batch appends,
  // queued[cursor] resolves to its first row and the consumed item can never reappear.
  const advance = () => {
    attempt.reset();
    const next = nextLive(cursor + 1);
    setCursor(next);
    if (next >= queued.length) {
      setAwaitingNext(true);
      onNeedRefetch();
    }
  };

  // STATE_MISS means the item is not an actionable member of any pending batch, so re-showing it
  // is never right: advance past it and refetch. DEFERRED / ALREADY_CONSUMED are proven consumed.
  const handleSkip = async () => {
    if (skipping || beingRemoved) return;
    const itemBatchId = puzzle.play_batch_id ?? batchId;
    if (itemBatchId == null) {
      advance();
      return;
    }
    setSkipping(true);
    try {
      const { status } = await skipPuzzle({ ptype, subtype, batch_id: itemBatchId, puzzle_id: puzzle.id });
      advance();
      if (status === "STATE_MISS") onNeedRefetch();
    } catch (err) {
      console.error("skipPuzzle failed:", errorLabel(err));
    } finally {
      setSkipping(false);
    }
  };

  const handlePrev = () => {
    if (attempt.navigationBlocked) return;
    const prev = prevLive(cursor - 1);
    if (prev >= 0) {
      attempt.reset();
      setCursor(prev);
    }
  };

  const exhausted = showNext && isLastQueued && allCaughtUp;

  return (
    <div className="mx-auto max-w-xl">
      <PuzzleHeader puzzle={puzzle} note={puzzle.attempt_summary.solved > 0 && !attempt.lastResult ? "previously solved" : null} />
      <PuzzleEngine
        key={`${puzzle.id}|${cursor}`}
        fen={puzzle.fen}
        solutionLine={puzzle.solution_line}
        color={puzzle.color}
        onComplete={attempt.handleComplete}
        presentationPly={puzzle.presentation_ply}
        acceptanceMap={puzzle.acceptance_map}
        onPrev={handlePrev}
        onNext={exhausted ? null : showNext ? advance : () => void handleSkip()}
        prevDisabled={prevLive(cursor - 1) < 0 || attempt.navigationBlocked}
        nextDisabled={exhausted || attempt.navigationBlocked || skipping || beingRemoved}
        nextLabel={showNext ? "Next Puzzle →" : skipping ? "Skipping…" : "Skip →"}
        nextHighlighted={attempt.lastResult === "solved"}
        showNextButton={showNext && !exhausted}
        isRepertoire={puzzle.is_repertoire}
        repertoireLineId={puzzle.repertoire_line_id}
        serverDowngraded={attempt.serverDowngraded}
        attemptStatus={attempt.attemptStatus}
        submissionLocked={attempt.navigationBlocked || beingRemoved}
      />
      <BlockingBanner error={attempt.blockingError} submitting={attempt.submitting} onRetry={attempt.retry} />
      {((showNext && isLastQueued) || awaitingNext) && !allCaughtUp && (refreshFailed ? <RetryLoad onRetry={onNeedRefetch} /> : <p className="mt-4 text-center text-xs text-zinc-500">Loading more puzzles…</p>)}
      {exhausted && <p className="mt-4 text-center text-sm text-zinc-500">You're all caught up</p>}
      <GameLinks puzzle={puzzle} />
    </div>
  );
}

// ─── Drill: the chosen lines, whole, in order ───────────────────────────────

/**
 * A drill of the lines the report or a list sent (`/practice?drill=1,2,3`): one serve, each line
 * played whole from its first board, in the order given, until the list is done. Not the queue:
 * no batch, nothing due, no Skip, no Previous — Next is offered once a verdict is in, and every
 * position in the list is one load (the solver's key is the position and the round), so the
 * tally — the first verdict of each load, as the hook reported it — is the server's count too.
 * Stop and Done tell the server nothing (a drill has no server-side lifetime to end) and go
 * `back` to where the drill came from; Stop waits for a verdict still posting, as every way of
 * leaving a solver does.
 */
function DrillMode({ lineIds, back, onAttemptRecorded, onNavigationLock }: { lineIds: number[]; back: { to: string; label: string; clearsQueue: boolean }; onAttemptRecorded: () => void; onNavigationLock: (locked: boolean) => void }) {
  const key = lineIds.join(",");
  const { data, isLoading, error } = useApi(() => getDrill(lineIds), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const [cursor, setCursor] = useState(0);
  const [round, setRound] = useState(1);
  // The first verdict of each load this round, by position; Replay and Try Again add later
  // sequences that do not count.
  const [firsts, setFirsts] = useState<Record<number, boolean>>({});
  const puzzles = useMemo(() => data?.puzzles ?? [], [data]);
  const puzzle: Puzzle | undefined = puzzles[cursor];
  const onVerdict = useCallback(
    (seq: number, solved: boolean) => {
      if (seq === 1) setFirsts((f) => (cursor in f ? f : { ...f, [cursor]: solved }));
    },
    [cursor],
  );
  const attempt = useAttemptSubmit(puzzle?.id ?? -1, onAttemptRecorded, puzzle?.presentation_ply ?? null, onVerdict);
  const navigationBlocked = attempt.navigationBlocked;
  useEffect(() => {
    onNavigationLock(navigationBlocked);
    return () => onNavigationLock(false);
  }, [navigationBlocked, onNavigationLock]);

  const done = data !== null && data !== undefined && cursor >= puzzles.length;
  // Done is when the report's drill queue has been drilled: it clears here, not when the drill was
  // served, so a Stop before the end and a serve that skipped every line keep the queue. A drill
  // that came from a list did not drill the queue and leaves it alone.
  const clearsQueue = back.clearsQueue;
  useEffect(() => {
    if (done && puzzles.length > 0 && clearsQueue) clearQueue();
  }, [done, puzzles.length, clearsQueue]);
  const right = Object.values(firsts).filter(Boolean).length;
  const wrong = Object.values(firsts).filter((v) => !v).length;
  const stop = (
    <Link to={back.to} aria-disabled={navigationBlocked} onClick={(e) => navigationBlocked && e.preventDefault()} title={navigationBlocked ? "Saving your attempt first" : undefined} className={`rounded border border-zinc-300 px-2.5 py-1 text-xs font-medium dark:border-zinc-700 ${navigationBlocked ? "pointer-events-none opacity-40" : "hover:bg-zinc-50 dark:hover:bg-zinc-800"}`}>
      {done ? back.label : "Stop"}
    </Link>
  );

  if (isLoading && !data) return <p className="px-6 py-12 text-center text-sm text-zinc-500">Loading the drill…</p>;
  if (error && !data) return <p className="text-sm text-rose-600">{error}</p>;
  if (!data) return null;
  if (puzzles.length === 0) {
    return (
      <div className="rounded border border-zinc-200 px-6 py-12 text-center dark:border-zinc-800" data-testid="drill-empty">
        <p className="text-lg text-zinc-500">Nothing to drill</p>
        <p className="mt-2 text-sm text-zinc-500">{data.skipped.length > 0 ? "None of the chosen lines is active." : "No lines were named."}</p>
        <div className="mt-4">{stop}</div>
      </div>
    );
  }

  const advance = () => {
    attempt.reset();
    setCursor((c) => c + 1);
  };
  const again = () => {
    attempt.reset();
    setFirsts({});
    setRound((r) => r + 1);
    setCursor(0);
  };
  const header = (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm" data-testid="drill-header">
      <span>
        <span className="font-medium">Drill</span> · {Math.min(cursor + 1, puzzles.length)} of {puzzles.length} · {right} first-try
        {data.skipped.length > 0 && <span className="text-xs text-zinc-500"> · {data.skipped.length} skipped</span>}
      </span>
      {stop}
    </div>
  );

  if (!puzzle) {
    return (
      <div className="mx-auto max-w-xl">
        {header}
        <div className="rounded border border-zinc-200 px-6 py-12 text-center dark:border-zinc-800" data-testid="drill-done">
          <p className="text-lg">Done</p>
          <p className="mt-2 text-sm text-zinc-500">
            {puzzles.length} {puzzles.length === 1 ? "line" : "lines"} drilled · first try right {right} · wrong {wrong}
          </p>
          <div className="mt-4 flex justify-center gap-3">
            <button type="button" onClick={again} className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900">
              Drill again
            </button>
          </div>
        </div>
      </div>
    );
  }

  const showNext = attempt.lastResult !== null;
  return (
    <div className="mx-auto max-w-xl">
      {header}
      <PuzzleHeader puzzle={puzzle} note={puzzle.attempt_summary.solved > 0 && !attempt.lastResult ? "previously solved" : null} />
      <PuzzleEngine
        key={`drill|${data.batch_id}|${round}|${cursor}`}
        fen={puzzle.fen}
        solutionLine={puzzle.solution_line}
        color={puzzle.color}
        onComplete={attempt.handleComplete}
        presentationPly={puzzle.presentation_ply}
        acceptanceMap={puzzle.acceptance_map}
        onNext={showNext ? advance : null}
        prevDisabled
        nextDisabled={navigationBlocked}
        nextLabel={cursor + 1 < puzzles.length ? "Next line →" : "Finish →"}
        nextHighlighted={attempt.lastResult === "solved"}
        showNextButton={showNext}
        isRepertoire={puzzle.is_repertoire}
        repertoireLineId={puzzle.repertoire_line_id}
        serverDowngraded={attempt.serverDowngraded}
        attemptStatus={attempt.attemptStatus}
        submissionLocked={navigationBlocked}
      />
      <BlockingBanner error={attempt.blockingError} submitting={attempt.submitting} onRetry={attempt.retry} />
      <GameLinks puzzle={puzzle} />
    </div>
  );
}

// ─── Overlay: one puzzle in a modal (deep link, list rows) ──────────────────

function PuzzleOverlay({ puzzle, onClose, onAttemptRecorded, onAttempted, onNavigationLock }: { puzzle: Puzzle; onClose: () => void; onAttemptRecorded: () => void; onAttempted?: (puzzleId: number) => void; onNavigationLock: (locked: boolean) => void }) {
  // `onAttempted` hears the verdict itself, before the save — a save that fails and lands later
  // from the durable queue is still this visit's attempt.
  const verdict = useCallback(() => onAttempted?.(puzzle.id), [onAttempted, puzzle.id]);
  const attempt = useAttemptSubmit(puzzle.id, onAttemptRecorded, puzzle.presentation_ply, verdict);
  const locked = attempt.navigationBlocked;

  // A hand-made puzzle can be retired from here. Not while an attempt on it exists anywhere
  // unsaved — held in memory, in flight, or waiting in the durable queue from this or an
  // earlier page load — because an attempt that lands after the puzzle is gone is refused.
  // From the moment the request goes out until it is answered the puzzle is on the removal
  // list (utils/puzzleRemoval), which locks every solver showing it, this one included, and
  // this overlay's Close and the page's navigation wait with it.
  const removable = !puzzle.is_repertoire && puzzle.source_types.includes("custom");
  const held = useSyncExternalStore(subscribeUnsavedAttempt, getUnsavedAttempt, getUnsavedAttempt) !== null; // by any solver on the page
  const unplayable = useUnplayable();
  const removing = unplayable.has(puzzle.id) && !isGone(puzzle.id);
  const gone = isGone(puzzle.id);
  const [confirming, setConfirming] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const queued = confirming && hasPendingAttemptForPuzzle(puzzle.id);
  const removeBlocked = locked || held || queued || attempt.attemptStatus !== null || removing || gone;
  const closeBlocked = locked || removing;
  useEffect(() => {
    onNavigationLock(closeBlocked);
    return () => onNavigationLock(false);
  }, [closeBlocked, onNavigationLock]);
  // Any copy of a puzzle that is confirmed gone closes itself — this overlay whether or not it
  // asked — unless an attempt is still unsaved, in which case it stays, locked, until it is.
  useEffect(() => {
    if (gone && !locked) onClose();
  }, [gone, locked, onClose]);
  async function remove() {
    if (removeBlocked || hasPendingAttemptForPuzzle(puzzle.id)) return;
    beginRemoval(puzzle.id);
    setRemoveError(null);
    try {
      await removePuzzle(puzzle.id);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        removalFailed(puzzle.id);
        setRemoveError("Could not remove this puzzle. Try again.");
        setConfirming(false);
        return;
      }
    }
    confirmGone(puzzle.id); // every list drops it and every solver showing it locks, then closes
    onAttemptRecorded(); // refetch the list it was on
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4" role="dialog" aria-label={`Puzzle ${puzzle.id}`}>
      <div className="max-h-[90svh] w-full max-w-xl overflow-y-auto rounded border border-zinc-200 bg-white p-4 shadow-xl dark:border-zinc-800 dark:bg-zinc-950">
        {/* The count sits beside Close; on a phone it drops to its own line under the title,
            still above the board, where the dialog opens. */}
        <div className="flex flex-wrap items-start justify-between gap-x-3">
          <div className="min-w-0 flex-1">
            <PuzzleHeader puzzle={puzzle} />
          </div>
          <TodayCount variant="dialog" className="order-last mb-3 basis-full sm:order-none sm:basis-auto" />
          <button
            type="button"
            onClick={onClose}
            disabled={closeBlocked}
            title={locked ? "Saving — please wait or retry" : removing ? "Removing — please wait" : "Close"}
            aria-label="Close"
            className="text-zinc-500 hover:text-zinc-900 disabled:opacity-40 dark:hover:text-zinc-100"
          >
            ✕
          </button>
        </div>
        <PuzzleEngine
          fen={puzzle.fen}
          solutionLine={puzzle.solution_line}
          color={puzzle.color}
          onComplete={attempt.handleComplete}
          presentationPly={puzzle.presentation_ply}
          acceptanceMap={puzzle.acceptance_map}
          isRepertoire={puzzle.is_repertoire}
          repertoireLineId={puzzle.repertoire_line_id}
          serverDowngraded={attempt.serverDowngraded}
          attemptStatus={attempt.attemptStatus}
          submissionLocked={attempt.navigationBlocked || removing || gone}
        />
        <BlockingBanner error={attempt.blockingError} submitting={attempt.submitting} onRetry={attempt.retry} />
        <GameLinks puzzle={puzzle} />
        {removable && (
          <div className="mt-4 flex items-center justify-end gap-3 border-t border-zinc-200 pt-3 text-xs dark:border-zinc-800">
            {removeError && (
              <span role="alert" className="mr-auto text-red-600 dark:text-red-400">
                {removeError}
              </span>
            )}
            {confirming ? (
              <>
                <span className="text-zinc-500">Remove this puzzle? Its history is kept.</span>
                {queued && <span className="text-zinc-500">An attempt on it is still being saved.</span>}
                <button type="button" disabled={removeBlocked} onClick={remove} className="font-medium text-red-600 disabled:opacity-40 dark:text-red-400">
                  {removing ? "Removing…" : "Yes, remove"}
                </button>
                <button type="button" disabled={removing} onClick={() => setConfirming(false)} className="text-zinc-500 disabled:opacity-40">
                  Keep
                </button>
              </>
            ) : (
              <button type="button" disabled={removeBlocked} title={removeBlocked ? "Saving your attempt first" : undefined} onClick={() => setConfirming(true)} className="text-zinc-500 hover:text-zinc-900 disabled:opacity-40 dark:hover:text-zinc-100">
                Remove puzzle
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** The deep-link payload carries only the solver fields; the rest defaults to "never attempted". */
function hydrateDeepLinkPuzzle(p: PlayablePuzzlePayload): Puzzle {
  return {
    ...p,
    title: null,
    description: null,
    created_at: "",
    occurrence_count: 0,
    source_breakdown: {},
    game_links: [],
    correct_game_links: [],
    attempt_count: 0,
    attempt_summary: { total: 0, solved: 0, last_attempt_at: null, streak: 0 },
    presentation_fen: null,
    repertoire_line_id: null,
    srs: null,
  };
}

// ─── List views (srs=all, srs=retired) ──────────────────────────────────────

type SortKey = "id" | "level" | "seen" | "attempts" | "streak" | "due";
type Sort = { key: SortKey; dir: "asc" | "desc" };
const SORT_LABEL: Record<SortKey, string> = { id: "Puzzle", level: "Level", seen: "Seen", attempts: "Attempts", streak: "Streak", due: "Next due" };
// The direction a first click gives: the most actionable rows first.
const FIRST_DIR: Record<SortKey, "asc" | "desc"> = { id: "asc", level: "asc", seen: "desc", attempts: "asc", streak: "asc", due: "asc" };
const LEVEL_RANK: Record<SrsLevel, number> = { pawn: 0, knight: 1, bishop: 2, rook: 3, queen: 4, king: 5 };

/** A column's value for sorting; null is "no value" (Next due before a first attempt) and sorts
 *  last in both directions. A puzzle not yet attempted has no level: it sorts below pawn. */
function sortValue(p: Puzzle, key: SortKey): number | null {
  switch (key) {
    case "id":
      return p.id;
    case "level":
      return p.srs ? LEVEL_RANK[p.srs.level] : -1;
    case "seen":
      return p.occurrence_count;
    case "attempts":
      return p.attempt_summary.total;
    case "streak":
      return p.attempt_summary.streak;
    case "due":
      return p.srs ? new Date(p.srs.next_show_at).getTime() : null;
  }
}

/** The ids of `rows` under `sort`: stable, null values last either way, ties by id ascending. */
function sortedIds(rows: Puzzle[], sort: Sort): number[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return rows
    .map((p) => ({ id: p.id, v: sortValue(p, sort.key) }))
    .sort((a, b) => {
      if (a.v === null || b.v === null) return a.v === b.v ? a.id - b.id : a.v === null ? 1 : -1;
      return a.v === b.v ? a.id - b.id : (a.v - b.v) * sign;
    })
    .map((r) => r.id);
}

const sameIds = (a: number[], b: number[]) => a.length === b.length && a.every((id, i) => id === b[i]);

/** The list's rows in the visit's order: every id of `order` the response still carries, then
 *  any new id in response order. */
function mergeOrder(order: number[], rows: Puzzle[]): number[] {
  const present = new Set(rows.map((p) => p.id));
  const kept = order.filter((id) => present.has(id));
  const known = new Set(kept);
  return [...kept, ...rows.filter((p) => !known.has(p.id)).map((p) => p.id)];
}

const DEFAULT_CAPTION: Record<"all" | "retired", string> = {
  all: "Default order: due first, then your blunders before deviations, lower level first, most seen first. Frozen for this visit — a row you solve stays where it is.",
  retired: "Default order: most recently updated first.",
};

/** The list's own path, for a drill to come back to (the query the list is keyed on). */
function listPath(type: PracticeType, subtype: string | null, srs: SrsFilter, lastNGames: number): string {
  const q = new URLSearchParams({ type, srs, last_n_games: String(lastNGames) });
  if (subtype) q.set("subtype", subtype);
  return `/practice?${q}`;
}

/**
 * One visit to a list: the rows' order is fixed when the list mounts (the server's Default order
 * for the view), and a refetch after an attempt updates each row in place — appending a row that
 * became visible, dropping one that is gone — but moves nothing. A header click sorts the rows as
 * they are now and freezes again; a third click restores the mount's order. A row attempted this
 * visit is tinted, wherever a sort puts it. The parent keys this component on the query, so a
 * filter change is a new visit: a new order, no sort, no tint. The row overlay is this visit's
 * too, so an attempt in it is known here by id, at its verdict.
 *
 * `drill`: on the repertoire "All" view, the listed rows' distinct lines in the current order,
 * as a drill. `cap` null means the cap is not known (the scopes read pending or failed): disabled.
 */
function ListMode({
  puzzles,
  total,
  view,
  drill,
  onNavigationLock,
  onOverlayChange,
  onNeedRefetch,
}: {
  puzzles: Puzzle[];
  total: number;
  view: "all" | "retired";
  drill: { cap: number | null; href: (lineIds: number[]) => string; locked: boolean } | null;
  onNavigationLock: (locked: boolean) => void;
  onOverlayChange: (open: boolean) => void;
  onNeedRefetch: () => void;
}) {
  // The Default order: the mount's, then every row appended since in the order it arrived.
  const [defaultOrder, setDefaultOrder] = useState<number[]>(() => puzzles.map((p) => p.id));
  const [order, setOrder] = useState<number[]>(defaultOrder);
  const [sort, setSort] = useState<Sort | null>(null);
  const [attempted, setAttempted] = useState<ReadonlySet<number>>(() => new Set());
  const [openPuzzle, setOpenPuzzle] = useState<Puzzle | null>(null);
  const byId = useMemo(() => new Map(puzzles.map((p) => [p.id, p])), [puzzles]);
  // The order follows the rows in render: an appended or dropped row shows on the render that
  // brings it, and the stored order is what is showing, so the next merge starts from it.
  const merged = mergeOrder(order, puzzles);
  if (!sameIds(merged, order)) setOrder(merged);
  const arrived = mergeOrder(defaultOrder, puzzles);
  if (!sameIds(arrived, defaultOrder)) setDefaultOrder(arrived);
  const rows = merged.map((id) => byId.get(id)).filter((p): p is Puzzle => p !== undefined);

  const clickHeader = (key: SortKey) => {
    const next: Sort | null = sort?.key !== key ? { key, dir: FIRST_DIR[key] } : sort.dir === FIRST_DIR[key] ? { key, dir: sort.dir === "asc" ? "desc" : "asc" } : null;
    setSort(next);
    setOrder(next ? sortedIds(rows, next) : mergeOrder(defaultOrder, rows));
  };
  const attemptedNow = useCallback((id: number) => setAttempted((prev) => (prev.has(id) ? prev : new Set(prev).add(id))), []);
  const closeOpenPuzzle = useCallback(() => setOpenPuzzle(null), []);
  useEffect(() => {
    onOverlayChange(openPuzzle !== null);
    return () => onOverlayChange(false);
  }, [openPuzzle, onOverlayChange]);

  const lineIds = drill ? [...new Set(rows.map((p) => p.repertoire_line_id).filter((id): id is number => id !== null))] : [];
  const drillReason = !drill
    ? null
    : drill.locked
      ? "Save your attempt first"
      : lineIds.length === 0
        ? "Nothing listed to drill"
        : drill.cap === null
          ? "The drill limit is not known yet"
          : lineIds.length > drill.cap
            ? `${drill.cap} at most — narrow the filter`
            : null;
  const drillLabel = `Drill these (${lineIds.length} ${lineIds.length === 1 ? "line" : "lines"})`;
  const drillButton = "rounded bg-zinc-900 px-3 py-1 text-xs font-medium text-white dark:bg-zinc-100 dark:text-zinc-900";

  const caption = sort ? `Sorted by ${SORT_LABEL[sort.key]}, ${sort.dir === "asc" ? "ascending" : "descending"}.` : DEFAULT_CAPTION[view];
  const th = (label: string, key: SortKey | null) => {
    const active = key !== null && sort?.key === key;
    return (
      <th key={label} aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : key ? "none" : undefined} className="px-2 py-1.5 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
        {key ? (
          <button type="button" onClick={() => clickHeader(key)} className={`whitespace-nowrap uppercase tracking-wide ${active ? "text-zinc-900 dark:text-zinc-100" : "hover:text-zinc-800 dark:hover:text-zinc-200"}`}>
            {label}
            {active && <span aria-hidden="true"> {sort.dir === "asc" ? "▲" : "▼"}</span>}
          </button>
        ) : (
          label
        )}
      </th>
    );
  };
  return (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-zinc-500">
        <span>
          {total} {view === "retired" ? "mastered" : ""} puzzles
        </span>
        {drill &&
          (drillReason ? (
            <button type="button" disabled title={drillReason} className={`${drillButton} disabled:opacity-40`}>
              {drillLabel}
            </button>
          ) : (
            <Link to={drill.href(lineIds)} className={drillButton}>
              {drillLabel}
            </Link>
          ))}
        {drill && drillReason && lineIds.length > 0 && !drill.locked && <span className="text-xs text-red-600 dark:text-red-400">{drillReason}</span>}
      </div>
      {rows.length === 0 ? (
        <p className="px-2 py-6 text-center text-sm text-zinc-500">No puzzles match.</p>
      ) : (
        <div className="overflow-x-auto">
          <p className="mb-1 px-2 text-xs text-zinc-500" data-testid="list-caption">
            {caption}
          </p>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                {th("Puzzle", "id")}
                {th("Level", "level")}
                {th("Source", null)}
                {th("Themes", null)}
                {th("Seen", "seen")}
                {th("Attempts", "attempts")}
                {th("Streak", "streak")}
                {th("Next due", "due")}
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id} onClick={() => setOpenPuzzle(p)} data-attempted={attempted.has(p.id) ? "true" : undefined} className={`cursor-pointer border-b border-zinc-100 dark:border-zinc-900 ${attempted.has(p.id) ? "bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-900/20 dark:hover:bg-emerald-900/30" : "hover:bg-zinc-100 dark:hover:bg-zinc-900"}`}>
                  <td className="px-2 py-1.5 font-mono text-xs">
                    #{p.id}
                    {p.title && <span className="ml-2 font-sans text-zinc-700 dark:text-zinc-300">{p.title}</span>}
                  </td>
                  <td className="px-2 py-1.5">
                    <LevelBadge srs={p.srs} isRotation={isRotationPuzzle(p)} />
                  </td>
                  <td className="space-x-1 px-2 py-1.5">
                    {p.source_types.map((s) => (
                      <SourceBadge key={s} type={s} />
                    ))}
                  </td>
                  <td className="max-w-[14rem] truncate px-2 py-1.5 text-xs" title={(p.themes ?? []).join(", ")}>
                    {(p.themes ?? []).join(", ") || "—"}
                  </td>
                  <td className="px-2 py-1.5 font-mono text-xs">{p.occurrence_count || "—"}</td>
                  <td className="px-2 py-1.5 font-mono text-xs">
                    {p.attempt_summary.solved}/{p.attempt_summary.total}
                  </td>
                  <td className={`px-2 py-1.5 font-mono text-xs ${p.attempt_summary.streak > 0 ? "text-emerald-600 dark:text-emerald-400" : p.attempt_summary.streak < 0 ? "text-rose-600 dark:text-rose-400" : "text-zinc-400"}`}>
                    {p.attempt_summary.streak > 0 ? `+${p.attempt_summary.streak}` : p.attempt_summary.streak || "—"}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 font-mono text-xs">{p.srs ? new Date(p.srs.next_show_at).toLocaleDateString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {openPuzzle && <PuzzleOverlay key={openPuzzle.id} puzzle={openPuzzle} onClose={closeOpenPuzzle} onAttemptRecorded={onNeedRefetch} onAttempted={attemptedNow} onNavigationLock={onNavigationLock} />}
    </>
  );
}

// ─── Filters ────────────────────────────────────────────────────────────────

function FiltersPopover({
  disabled = false,
  subtype,
  onSubtypeChange,
  subtypeOptions,
  repertoireScopes,
  repertoireScopesError,
  srsFilter,
  onSrsChange,
  lastNGames,
  onPeriodChange,
}: {
  disabled?: boolean;
  subtype: string | null;
  onSubtypeChange: (v: string | null) => void;
  subtypeOptions: { value: string; label: string }[];
  /** Set (or loading, as null) only for the Repertoire type, which filters by book, chapter and line. */
  repertoireScopes?: RepertoireScopeBook[] | null;
  repertoireScopesError?: string | null;
  srsFilter: SrsFilter;
  onSrsChange: (v: SrsFilter) => void;
  lastNGames: number;
  onPeriodChange: (v: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const activeCount = (subtype ? 1 : 0) + (srsFilter !== "due" ? 1 : 0) + (lastNGames !== 0 ? 1 : 0);

  // The panel is positioned by the toolbar (the nearest `relative` ancestor), never by its button:
  // on a phone it spans the toolbar's edges, so it stays inside the viewport wherever the row has
  // wrapped the button to; from `sm` up it is 288 px wide, hung from the toolbar's right edge,
  // where the button sits.
  return (
    <div ref={rootRef}>
      <button
        type="button"
        disabled={disabled}
        title={disabled ? "Save your attempt first" : undefined}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={chip(open || activeCount > 0)}
      >
        Filters{activeCount > 0 ? ` (${activeCount})` : ""}
      </button>
      {open && (
        <div role="dialog" aria-label="Practice filters" className="absolute left-0 right-0 top-full z-30 mt-2 flex flex-col gap-3 rounded border border-zinc-200 bg-white p-3 shadow-lg sm:left-auto sm:w-72 dark:border-zinc-800 dark:bg-zinc-950">
          {repertoireScopes !== undefined && <RepertoireScopeFilter books={repertoireScopes} error={repertoireScopesError} subtype={subtype} onChange={onSubtypeChange} />}
          {subtypeOptions.length > 0 && (
            <label className="flex flex-col gap-1 text-xs text-zinc-500">
              Subtype
              <select className={select} value={subtype ?? ""} onChange={(e) => onSubtypeChange(e.target.value || null)}>
                <option value="">All</option>
                {subtypeOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex flex-col gap-1 text-xs text-zinc-500">
            View
            <div className="flex flex-wrap gap-1.5">
              {(
                [
                  { key: "due", label: "Your move" },
                  { key: "all", label: "All" },
                  { key: "retired", label: "Mastered" },
                ] as const
              ).map((o) => (
                <button key={o.key} type="button" onClick={() => onSrsChange(o.key)} className={chip(srsFilter === o.key)}>
                  {o.label}
                </button>
              ))}
            </div>
          </div>
          <label className="flex flex-col gap-1 text-xs text-zinc-500">
            Period
            <select className={select} value={lastNGames} onChange={(e) => onPeriodChange(Number(e.target.value))}>
              {LAST_N_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
    </div>
  );
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function Practice() {
  const [searchParams, setSearchParams] = useSearchParams();

  const [type, setType] = useState<PracticeType>(() => {
    const url = searchParams.get("type");
    return isPracticeType(url) ? url : "all";
  });
  const [subtype, setSubtype] = useState<string | null>(() => searchParams.get("subtype"));
  const [srsFilter, setSrsFilter] = useState<SrsFilter>(() => {
    const url = searchParams.get("srs");
    return isSrsFilter(url) ? url : "due";
  });
  const [lastNGames, setLastNGames] = useState<number>(() => {
    const parsed = Number(searchParams.get("last_n_games"));
    return LAST_N_OPTIONS.some((o) => o.value === parsed) ? parsed : 0;
  });
  const [deepLinkId, setDeepLinkId] = useState<number | null>(() => {
    const n = Number(searchParams.get("puzzle"));
    return Number.isInteger(n) && n > 0 ? n : null;
  });
  // The lines a drill was asked for, kept in the URL so a reload serves the drill again.
  const drillIds = useMemo(() => {
    const raw = searchParams.get("drill");
    if (!raw) return null;
    const ids = raw.split(",").map((x) => Number(x)).filter((n) => Number.isInteger(n) && n > 0);
    return ids.length > 0 ? ids : null; // the server holds the cap
  }, [searchParams]);
  const [listOverlayOpen, setListOverlayOpen] = useState(false);
  const [inPageLock, setInPageLock] = useState(false);
  // An attempt held from an earlier visit (the page was left, or reloaded into blocking
  // mode) is offered for retry here, above whatever the page serves, until it is saved.
  const held = useSyncExternalStore(subscribeUnsavedAttempt, getUnsavedAttempt, getUnsavedAttempt);
  const heldOwnedHere = useSyncExternalStore(subscribeUnsavedAttempt, isUnsavedAttemptOwned, isUnsavedAttemptOwned);
  // Ownership, not the effect-driven in-page lock, decides whether a solver stays mounted:
  // it is known in the same render in which the hold appears.
  const orphaned = held !== null && !heldOwnedHere;
  const navLocked = inPageLock || held !== null;

  const drilling = drillIds !== null;
  // A drill from a list carries the list's query in its own URL, and that URL alone says where
  // the drill came from and where it goes back to: the page's filter state may belong to a later
  // visit (browser Back restores a drill URL without restoring the filters). An omitted subtype
  // is no subtype. Anything else is a report drill.
  const listReturn = useMemo(() => {
    if (!drilling || searchParams.get("from") !== "list" || searchParams.get("type") !== "repertoire") return null;
    const v = searchParams.get("srs");
    const n = Number(searchParams.get("last_n_games"));
    return listPath("repertoire", searchParams.get("subtype") || null, isSrsFilter(v) ? v : "all", LAST_N_OPTIONS.some((o) => o.value === n) ? n : 0);
  }, [drilling, searchParams]);

  // Adopt the entry params whenever the URL carries any and no drill is running, then strip
  // them. Adoption is state set during render (the URL is a prop, not an external system), once
  // per search string; the first render's is a no-op, the initialisers read the same values.
  const search = searchParams.toString();
  const [adopted, setAdopted] = useState(search);
  const carriesEntry = ENTRY_KEYS.some((k) => searchParams.has(k));
  if (!carriesEntry && adopted !== search) setAdopted(search); // the strip landed: the next entry URL is new, even if identical
  if (!drilling && carriesEntry && adopted !== search) {
    setAdopted(search);
    const t = searchParams.get("type");
    if (isPracticeType(t)) setType(t);
    if (searchParams.has("subtype")) setSubtype(searchParams.get("subtype") || null);
    const v = searchParams.get("srs");
    if (isSrsFilter(v)) setSrsFilter(v);
    const n = Number(searchParams.get("last_n_games"));
    if (searchParams.has("last_n_games") && LAST_N_OPTIONS.some((o) => o.value === n)) setLastNGames(n);
    const id = Number(searchParams.get("puzzle"));
    if (Number.isInteger(id) && id > 0) setDeepLinkId(id);
  }
  useEffect(() => {
    if (drilling || !carriesEntry) return;
    const next = new URLSearchParams(searchParams);
    ENTRY_KEYS.forEach((k) => next.delete(k));
    setSearchParams(next, { replace: true });
  }, [searchParams, drilling, carriesEntry, setSearchParams]);

  // Every dep is a primitive, so `isStale` compares exactly. A failed re-read within one query
  // keeps the rows (`keepDataOnError`): the list's visit and the queue's cursor live on, and the
  // error shows beside them; a read for a new query that fails shows the error alone.
  const { data, isLoading, error, refetch, isStale } = useApi(() => (drilling ? Promise.resolve(null) : getPuzzles({ srs: srsFilter, ptype: type, subtype, last_n_games: lastNGames })), [type, subtype, lastNGames, srsFilter, drilling], { keepDataOnError: true });

  const { data: deepLinkData, isLoading: deepLinkLoading, error: deepLinkError } = useApi(() => (deepLinkId != null ? getPuzzleById(deepLinkId) : Promise.resolve(null)), [deepLinkId]);
  // A missing or no-longer-visible puzzle falls back to the queue.
  if (deepLinkError && deepLinkId != null) setDeepLinkId(null);
  const deepLinkPuzzle = deepLinkData && deepLinkData.id === deepLinkId ? hydrateDeepLinkPuzzle(deepLinkData) : null;

  // Background processor for the durable attempt queue.
  useEffect(() => {
    const handler = async (record: PendingAttempt) => {
      await recordAttempt(record.puzzle_id, {
        solved: record.solved,
        moves_played: record.moves_played,
        attempt_id: record.attempt_id,
        ...(record.session_id != null ? { session_id: record.session_id } : {}),
        presentation_ply: record.presentation_ply ?? null,
        ...(record.attempt_seq != null ? { attempt_seq: record.attempt_seq } : {}),
      });
      refetch();
    };
    const onSuccess = (record: PendingAttempt) => {
      window.dispatchEvent(new CustomEvent(ATTEMPT_RESOLVED_EVENT, { detail: { attempt_id: record.attempt_id, puzzle_id: record.puzzle_id } }));
    };
    return initQueueTriggers(handler, onSuccess);
  }, [refetch]);

  // A puzzle confirmed gone this page load never shows in a list or queue again, even from a
  // response that predates its removal.
  const unplayable = useUnplayable();
  const puzzles = useMemo(() => (data?.puzzles ?? []).filter((p) => !isGone(p.id)), [data, unplayable]); // eslint-disable-line react-hooks/exhaustive-deps
  const closeDeepLink = useCallback(() => setDeepLinkId(null), []);
  const subtypeOptions = (() => {
    if (type === "motif") return (data?.served_themes ?? []).map((t) => ({ value: t, label: t }));
    if (type === "blunder") return [...new Set(puzzles.flatMap((p) => p.themes ?? []))].sort().map((t) => ({ value: t, label: t }));
    if (type === "custom") return CUSTOM_SUBTYPES.map((t) => ({ value: t, label: CUSTOM_SUBTYPE_LABEL[t] }));
    return [];
  })();
  // The repertoire filter's tree comes from the server, so a line appears whether or not one of
  // its puzzles is on the page; fetched only while the Repertoire type is showing.
  const { data: scopeData, error: scopeError } = useApi(() => (type === "repertoire" ? getRepertoireScopes() : Promise.resolve(null)), [type]);
  const repertoireScopes = type === "repertoire" ? (scopeData?.books ?? null) : undefined;
  // The dialog covers the toolbar and carries its own copy of the count.
  const dialogOpen = !orphaned && (deepLinkPuzzle !== null || listOverlayOpen);
  // "Drill these" on the repertoire "All" list: the cap is the server's, known once the scopes
  // read answers; a drill from here carries the list's query so its Back link can return to it.
  const drill = type === "repertoire" && srsFilter === "all" ? { cap: scopeData?.drill_max_lines ?? null, locked: navLocked, href: (ids: number[]) => `/practice?drill=${ids.join(",")}&from=list&${listPath(type, subtype, srsFilter, lastNGames).split("?")[1]}` } : null;
  const back = listReturn ? { to: listReturn, label: "Back to the list", clearsQueue: false } : { to: reportPath(), label: "Back to the report", clearsQueue: true };

  return (
    <div>
      {/* `relative`: the Filters panel hangs from this whole toolbar, not from its button. */}
      <div className="relative flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">Practice</h1>
          {!drilling && (
          <div className="flex flex-wrap gap-1" role="tablist" aria-label="Practice type">
            {PLAY_TYPES.map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={type === t}
                disabled={navLocked}
                title={navLocked ? "Save your attempt first" : undefined}
                onClick={() => {
                  setType(t);
                  setSubtype(null);
                }}
                className={chip(type === t)}
              >
                {TYPE_LABEL[t]}
              </button>
            ))}
          </div>
          )}
        </div>
        <div className="flex items-center gap-3">
          <TodayCount variant="toolbar" quiet={dialogOpen} />
          {data && !isStale && srsFilter === "due" && <span className="text-xs text-zinc-500">{data.mastered_count} mastered</span>}
          {!drilling && <FiltersPopover disabled={navLocked} subtype={subtype} onSubtypeChange={setSubtype} subtypeOptions={subtypeOptions} repertoireScopes={repertoireScopes} repertoireScopesError={type === "repertoire" ? scopeError : null} srsFilter={srsFilter} onSrsChange={setSrsFilter} lastNGames={lastNGames} onPeriodChange={setLastNGames} />}
        </div>
      </div>

      {/* An attempt held from an earlier visit is the only thing on the page until it is
          saved: no board, no Skip, no list, no overlay — a second attempt must not be possible. */}
      {orphaned && held && <HeldAttemptBanner attempt={held} onSaved={refetch} />}
      {!orphaned && drilling && drillIds && (
        <div className="mt-4">
          <DrillMode key={drillIds.join(",")} lineIds={drillIds} back={back} onAttemptRecorded={noop} onNavigationLock={setInPageLock} />
        </div>
      )}
      {!orphaned && !drilling && (
      <div className="mt-4">
        {/* `isStale` is checked as well as `isLoading`: on the first render after a filter change the
            effect has not run yet, so isLoading still reads false from the previous fetch. */}
        {((isStale && !error) || (isLoading && data == null)) && <p className="text-sm text-zinc-500">…</p>}
        {error && (data == null || isStale) && <p className="text-sm text-rose-600">{error}</p>}
        {error && data != null && !isStale && <p className="mb-2 text-xs text-rose-600">Could not refresh: {error}</p>}
        {data != null && !isStale && srsFilter === "due" && (
          // Keyed on the serve QUERY, not puzzle ids: a filter change resets the queue, but an
          // in-query refetch appends and keeps the cursor.
          <PlayMode
            key={`play|${type}|${subtype ?? ""}|${lastNGames}`}
            batch={puzzles}
            batchId={data.batch_id ?? null}
            ptype={type}
            subtype={subtype}
            allCaughtUp={puzzles.length === 0}
            refreshFailed={error !== null}
            prefetchThreshold={data.mint_ahead_threshold ?? 4}
            onAttemptRecorded={noop}
            onNeedRefetch={refetch}
            onNavigationLock={setInPageLock}
          />
        )}
        {data != null && !isStale && srsFilter !== "due" && (
          // Keyed on the query: a filter change is a new visit (new order, no sort, no tint); an
          // in-query refetch updates the rows in place.
          <ListMode
            key={`list|${type}|${subtype ?? ""}|${lastNGames}|${srsFilter}`}
            puzzles={puzzles}
            total={data.total}
            view={srsFilter}
            drill={drill}
            onNavigationLock={setInPageLock}
            onOverlayChange={setListOverlayOpen}
            onNeedRefetch={refetch}
          />
        )}
      </div>
      )}

      {deepLinkId != null && deepLinkLoading && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 text-sm text-zinc-200">…</div>}
      {!orphaned && deepLinkPuzzle && <PuzzleOverlay key={deepLinkPuzzle.id} puzzle={deepLinkPuzzle} onClose={closeDeepLink} onAttemptRecorded={refetch} onNavigationLock={setInPageLock} />}
    </div>
  );
}
