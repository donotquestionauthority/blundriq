import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "react-router";
import { ApiError } from "../api";
import { removePuzzle } from "../blunders";
import { beginRemoval, confirmGone, getUnplayable, isGone, removalFailed, subscribeRemovals } from "../utils/puzzleRemoval";
import { useApi } from "../hooks/useApi";
import { disownUnsavedAttempt, getUnsavedAttempt, holdUnsavedAttempt, isUnsavedAttemptOwned, releaseUnsavedAttempt, subscribeUnsavedAttempt, type UnsavedAttempt } from "../utils/unsavedAttempt";
import { PuzzleEngine } from "../components/PuzzleEngine";
import { getPuzzleById, getPuzzles, isRotationPuzzle, LAST_N_OPTIONS, recordAttempt, skipPuzzle } from "../practice";
import type { PlayablePuzzlePayload, PracticeType, Puzzle, PuzzleGameLink, PuzzleSrs, SrsFilter, SrsLevel } from "../practice";
import { enqueue as enqueueAttempt, hasPendingAttempt, hasPendingAttemptForPuzzle, initQueueTriggers, isQueueModeAvailable, markCompleted as markAttemptCompleted, type PendingAttempt } from "../utils/attemptQueue";

/**
 * Practice: the puzzle queue. `srs=due` is the play queue (a streaming batch consumer);
 * `srs=all` and `srs=retired` are lists, each row opening the solver in an overlay.
 * `?puzzle=<id>` deep-links straight into the overlay.
 *
 * Deep-link query params are read on first render and then stripped from the URL:
 *   type = all | motif | repertoire | blunder | custom · subtype · srs = due | all | retired ·
 *   last_n_games · puzzle
 */

const noop = () => {};
const PLAY_TYPES: PracticeType[] = ["all", "motif", "repertoire", "blunder", "custom"];
const TYPE_LABEL: Record<PracticeType, string> = { all: "All", motif: "Motif", repertoire: "Repertoire", blunder: "Blunder", custom: "Custom" };

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
 */
function useAttemptSubmit(puzzleId: number, onRecorded: () => void) {
  const [lastResult, setLastResult] = useState<"solved" | "wrong" | null>(null);
  const [serverDowngraded, setServerDowngraded] = useState(false);
  const [attemptStatus, setAttemptStatus] = useState<"in_flight" | "pending_retry" | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The failed attempt keeps the puzzle it was made on: a retry must go there, whatever the
  // hook is showing by the time it is clicked.
  const [blockingError, setBlockingError] = useState<{ attemptId: string; solved: boolean; movesPlayed: string[]; puzzleId: number; sessionId: string } | null>(null);
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
  const pendingRetryUuidRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string>(crypto.randomUUID());

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
  }, []);

  // The attempt names its puzzle and its session: a retry, or a slow response, never posts
  // to whatever the hook is showing by then, nor under a session started since.
  const post = useCallback(
    (attemptPuzzleId: number, sessionId: string, attemptId: string, solved: boolean, movesPlayed: string[]) =>
      recordAttempt(attemptPuzzleId, { solved, moves_played: movesPlayed.join(","), attempt_id: attemptId, session_id: sessionId }),
    [],
  );

  const runBlockingMode = useCallback(
    async (attemptId: string, solved: boolean, movesPlayed: string[], attemptPuzzleId: number, sessionId: string) => {
      outstandingRef.current = attemptId;
      // Held from before the request goes out, not from its failure: the whole app treats the
      // attempt as unsaved until its id is acknowledged, so leaving and returning mid-request
      // cannot open a second one.
      const payload = { puzzle_id: attemptPuzzleId, attempt_id: attemptId, session_id: sessionId, solved, moves_played: movesPlayed.join(",") };
      holdUnsavedAttempt(payload, ownerRef.current);
      setSubmitting(true);
      setBlockingError(null);
      setServerDowngraded(false);
      try {
        const response = await post(attemptPuzzleId, sessionId, attemptId, solved, movesPlayed);
        releaseUnsavedAttempt(attemptId);
        outstandingRef.current = null;
        if (activePuzzleIdRef.current === attemptPuzzleId) {
          setLastResult(response.solved ? "solved" : "wrong");
          if (solved && !response.solved) setServerDowngraded(true);
        }
        onRecorded();
      } catch (err) {
        console.error("Blocking-mode recordAttempt failed:", err);
        // Still held (since before the request) unless a retry from elsewhere — the recovery
        // banner, after this solver was left — landed it meanwhile.
        if (getUnsavedAttempt()?.attempt_id === attemptId) {
          if (activePuzzleIdRef.current === attemptPuzzleId) setBlockingError({ attemptId, solved, movesPlayed, puzzleId: attemptPuzzleId, sessionId });
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

      if (!isQueueModeAvailable()) {
        await runBlockingMode(attemptId, solved, movesPlayed, attemptPuzzleId, sessionId);
        return;
      }
      try {
        await enqueueAttempt({ attempt_id: attemptId, puzzle_id: attemptPuzzleId, solved, moves_played: movesPlayed.join(","), session_id: sessionId });
      } catch (err) {
        console.warn("Queue enqueue failed, falling back to blocking mode:", err);
        inFlightRef.current.delete(attemptId);
        await runBlockingMode(attemptId, solved, movesPlayed, attemptPuzzleId, sessionId);
        return;
      }

      // Committed to storage: the UI may move on now.
      setLastResult(solved ? "solved" : "wrong");
      setServerDowngraded(false);
      setAttemptStatus("in_flight");
      try {
        const response = await post(attemptPuzzleId, sessionId, attemptId, solved, movesPlayed);
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
        console.error("Foreground recordAttempt failed; queued for retry:", err);
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
    const { attemptId, solved, movesPlayed, puzzleId: attemptPuzzleId, sessionId } = blockingError;
    inFlightRef.current.delete(attemptId);
    void runBlockingMode(attemptId, solved, movesPlayed, attemptPuzzleId, sessionId);
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
      await recordAttempt(attempt.puzzle_id, { solved: attempt.solved, moves_played: attempt.moves_played, attempt_id: attempt.attempt_id, session_id: attempt.session_id });
      releaseUnsavedAttempt(attempt.attempt_id);
      onSaved();
    } catch (err) {
      console.error("Retry of a held attempt failed:", err);
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
  const attempt = useAttemptSubmit(puzzle?.id ?? -1, recorded);
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
  useEffect(() => {
    if (queued.length === 0) return;
    if (liveAhead + 1 > prefetchThreshold) return;
    if (allCaughtUp) return;
    const key = `${batchId ?? "null"}|${cursor}`;
    if (prefetchedForRef.current === key) return;
    prefetchedForRef.current = key;
    onNeedRefetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor, liveAhead, batchId, prefetchThreshold, allCaughtUp]);

  if (!puzzle) {
    if (allCaughtUp) {
      return (
        <div className="rounded border border-zinc-200 px-6 py-12 text-center dark:border-zinc-800">
          <p className="text-lg text-zinc-500">You're all caught up</p>
          <p className="mt-2 text-sm text-zinc-500">No more puzzles to serve right now.</p>
        </div>
      );
    }
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
      console.error("skipPuzzle failed:", err);
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
        serverDowngraded={attempt.serverDowngraded}
        attemptStatus={attempt.attemptStatus}
        submissionLocked={attempt.navigationBlocked || beingRemoved}
      />
      <BlockingBanner error={attempt.blockingError} submitting={attempt.submitting} onRetry={attempt.retry} />
      {((showNext && isLastQueued) || awaitingNext) && !allCaughtUp && <p className="mt-4 text-center text-xs text-zinc-500">Loading more puzzles…</p>}
      {exhausted && <p className="mt-4 text-center text-sm text-zinc-500">You're all caught up</p>}
      <GameLinks puzzle={puzzle} />
    </div>
  );
}

// ─── Overlay: one puzzle in a modal (deep link, list rows) ──────────────────

function PuzzleOverlay({ puzzle, onClose, onAttemptRecorded, onNavigationLock }: { puzzle: Puzzle; onClose: () => void; onAttemptRecorded: () => void; onNavigationLock: (locked: boolean) => void }) {
  const attempt = useAttemptSubmit(puzzle.id, onAttemptRecorded);
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
        <div className="flex items-start justify-between gap-3">
          <PuzzleHeader puzzle={puzzle} />
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

function PuzzleList({ puzzles, onOpen }: { puzzles: Puzzle[]; onOpen: (p: Puzzle) => void }) {
  if (puzzles.length === 0) return <p className="px-2 py-6 text-center text-sm text-zinc-500">No puzzles match.</p>;
  const th = (label: string) => (
    <th key={label} className="px-2 py-1.5 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
      {label}
    </th>
  );
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-zinc-200 dark:border-zinc-800">{["Puzzle", "Level", "Source", "Themes", "Seen", "Attempts", "Streak", "Next due"].map(th)}</tr>
        </thead>
        <tbody>
          {puzzles.map((p) => (
            <tr key={p.id} onClick={() => onOpen(p)} className="cursor-pointer border-b border-zinc-100 hover:bg-zinc-100 dark:border-zinc-900 dark:hover:bg-zinc-900">
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
  );
}

// ─── Filters ────────────────────────────────────────────────────────────────

function FiltersPopover({
  disabled = false,
  subtype,
  onSubtypeChange,
  subtypeOptions,
  srsFilter,
  onSrsChange,
  lastNGames,
  onPeriodChange,
}: {
  disabled?: boolean;
  subtype: string | null;
  onSubtypeChange: (v: string | null) => void;
  subtypeOptions: { value: string; label: string }[];
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

  return (
    <div ref={rootRef} className="relative">
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
        <div role="dialog" aria-label="Practice filters" className="absolute right-0 z-30 mt-2 flex w-72 flex-col gap-3 rounded border border-zinc-200 bg-white p-3 shadow-lg dark:border-zinc-800 dark:bg-zinc-950">
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
  const [openPuzzle, setOpenPuzzle] = useState<Puzzle | null>(null);
  const [inPageLock, setInPageLock] = useState(false);
  // An attempt held from an earlier visit (the page was left, or reloaded into blocking
  // mode) is offered for retry here, above whatever the page serves, until it is saved.
  const held = useSyncExternalStore(subscribeUnsavedAttempt, getUnsavedAttempt, getUnsavedAttempt);
  const heldOwnedHere = useSyncExternalStore(subscribeUnsavedAttempt, isUnsavedAttemptOwned, isUnsavedAttemptOwned);
  // Ownership, not the effect-driven in-page lock, decides whether a solver stays mounted:
  // it is known in the same render in which the hold appears.
  const orphaned = held !== null && !heldOwnedHere;
  const navLocked = inPageLock || held !== null;

  // Strip the entry params after first render; the state above already holds them.
  const urlStripRef = useRef(false);
  useEffect(() => {
    if (urlStripRef.current) return;
    urlStripRef.current = true;
    const keys = ["type", "subtype", "srs", "last_n_games", "puzzle"];
    if (keys.some((k) => searchParams.has(k))) {
      const next = new URLSearchParams(searchParams);
      keys.forEach((k) => next.delete(k));
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Every dep is a primitive, so `isStale` compares exactly.
  const { data, isLoading, error, refetch, isStale } = useApi(() => getPuzzles({ srs: srsFilter, ptype: type, subtype, last_n_games: lastNGames }), [type, subtype, lastNGames, srsFilter]);

  const { data: deepLinkData, isLoading: deepLinkLoading, error: deepLinkError } = useApi(() => (deepLinkId != null ? getPuzzleById(deepLinkId) : Promise.resolve(null)), [deepLinkId]);
  useEffect(() => {
    // A missing or no-longer-visible puzzle falls back to the queue.
    if (deepLinkError && deepLinkId != null) setDeepLinkId(null);
  }, [deepLinkError, deepLinkId]);
  const deepLinkPuzzle = deepLinkData && deepLinkData.id === deepLinkId ? hydrateDeepLinkPuzzle(deepLinkData) : null;

  // Background processor for the durable attempt queue.
  useEffect(() => {
    const handler = async (record: PendingAttempt) => {
      await recordAttempt(record.puzzle_id, {
        solved: record.solved,
        moves_played: record.moves_played,
        attempt_id: record.attempt_id,
        ...(record.session_id != null ? { session_id: record.session_id } : {}),
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
  const closeOpenPuzzle = useCallback(() => setOpenPuzzle(null), []);
  const subtypeOptions = (() => {
    if (type === "motif") return (data?.served_themes ?? []).map((t) => ({ value: t, label: t }));
    if (type === "blunder") return [...new Set(puzzles.flatMap((p) => p.themes ?? []))].sort().map((t) => ({ value: t, label: t }));
    if (type === "repertoire") {
      const lines = new Map<number, string>();
      for (const p of puzzles) if (p.repertoire_line_id != null && !lines.has(p.repertoire_line_id)) lines.set(p.repertoire_line_id, p.title ?? `line ${p.repertoire_line_id}`);
      return [...lines].map(([id, label]) => ({ value: String(id), label }));
    }
    return [];
  })();

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">Practice</h1>
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
        </div>
        <div className="flex items-center gap-3">
          {data && srsFilter === "due" && <span className="text-xs text-zinc-500">{data.mastered_count} mastered</span>}
          <FiltersPopover disabled={navLocked} subtype={subtype} onSubtypeChange={setSubtype} subtypeOptions={subtypeOptions} srsFilter={srsFilter} onSrsChange={setSrsFilter} lastNGames={lastNGames} onPeriodChange={setLastNGames} />
        </div>
      </div>

      {/* An attempt held from an earlier visit is the only thing on the page until it is
          saved: no board, no Skip, no list, no overlay — a second attempt must not be possible. */}
      {orphaned && held && <HeldAttemptBanner attempt={held} onSaved={refetch} />}
      {!orphaned && (
      <div className="mt-4">
        {/* `isStale` is checked as well as `isLoading`: on the first render after a filter change the
            effect has not run yet, so isLoading still reads false from the previous fetch. */}
        {(isStale || (isLoading && data == null)) && <p className="text-sm text-zinc-500">…</p>}
        {error && data == null && !isStale && <p className="text-sm text-rose-600">{error}</p>}
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
            prefetchThreshold={data.mint_ahead_threshold ?? 4}
            onAttemptRecorded={noop}
            onNeedRefetch={refetch}
            onNavigationLock={setInPageLock}
          />
        )}
        {data != null && !isStale && srsFilter !== "due" && (
          <>
            <p className="mb-2 text-sm text-zinc-500">
              {data.total} {srsFilter === "retired" ? "mastered" : ""} puzzles
            </p>
            <PuzzleList puzzles={puzzles} onOpen={setOpenPuzzle} />
          </>
        )}
      </div>
      )}

      {deepLinkId != null && deepLinkLoading && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 text-sm text-zinc-200">…</div>}
      {!orphaned && deepLinkPuzzle && <PuzzleOverlay key={deepLinkPuzzle.id} puzzle={deepLinkPuzzle} onClose={closeDeepLink} onAttemptRecorded={refetch} onNavigationLock={setInPageLock} />}
      {!orphaned && openPuzzle && !deepLinkPuzzle && <PuzzleOverlay key={openPuzzle.id} puzzle={openPuzzle} onClose={closeOpenPuzzle} onAttemptRecorded={refetch} onNavigationLock={setInPageLock} />}
    </div>
  );
}
