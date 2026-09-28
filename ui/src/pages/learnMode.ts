/**
 * Learn mode: the state machine behind one rep on the game review page — which plies prompt,
 * the fixed question sequence, move entry with promotion, the timer, and the commit. The page
 * owns the ply, the board and the panels; this module owns the rep. Nothing here reveals an
 * answer before the commit: it exposes `revealed`, and every surface the page blanks at a
 * prompted ply keys off that one flag. No engine work happens here — the in-check branch needs
 * only chess.js's `isCheck()`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";
import type { Square } from "chess.js";
import { ApiError } from "../api";
import type { PromotionPiece } from "../components/PromotionChooser";
import { learnCommit } from "../games";
import type { LearnCommitBody } from "../games";
import { legalMove } from "../utils/chess";

/** The standard branch: four questions, asked in this order at every rep. They ask; they never
 *  instruct and never say how many answers exist. */
export const LEARN_PROMPTS_STANDARD: readonly string[] = ["What is your opponent threatening?", "What are your checks?", "What are your captures?", "What can you attack?"];

/** The in-check branch replaces the four. Nothing announces the check: the questions changing is
 *  the signal. The third's second sentence matters — in double check only king moves are legal,
 *  and a king move can itself capture a checker. */
export const LEARN_PROMPTS_IN_CHECK: readonly string[] = ["Can you take the checking piece with something other than your king?", "Can you block the check?", "Where can your king go? It may be able to capture."];

/** The commit step is an action, not a question. */
export const LEARN_COMMIT_PROMPT = "Play the move you would actually make.";

/** Every user-visible string of the Learn and repertoire surfaces. The app informs; it never
 *  adjudicates the chess. */
export const LEARN_COPY = {
  modeLabel: "Mode",
  modeLearn: "Learn from my mistakes",
  modeReview: "Review mode",
  alwaysStartHere: "Always start here",
  alreadyDefault: "This is already your default.",
  justShowMe: "Just show me",
  routineComplete: "Routine complete.",
  timeMyReps: "Time my reps",
  prepPlays: "Your prep plays",
  lineEndsHere: "Your line ends here.",
  conflictHeadline: "Your prep has different moves here.",
  prepUnreadable: "Part of your prep here couldn't be read.",
  notInRepertoire: "Not in your repertoire.",
  transposedNote: "This position also occurs in this line — the game reached it by a different move order.",
  nothingToWorkThrough: "There's nothing to work through in this game.",
  commitNetwork: "Not saved — check your connection.",
  commitConflict: "This rep was already recorded. Starting a fresh one.",
  commitGone: "This game needs re-fetching before we can save.",
  commitInvalid: "We couldn't accept that move. Try again.",
  tryAgain: "Try again",
  loading: "Loading…",
  saving: "Saving…",
  noEngineData: "No engine data for this position.",
  gameFailed: "This game couldn't be loaded.",
  a11yElapsed: "Elapsed time",
  nextQuestion: "Next question",
} as const;

/** The side to move of a FEN, or null for an unusable one. Turn parity comes from the FEN, never
 *  from ply parity: a game can start from a position with Black to move, and the server derives
 *  it the same way, so the client never offers a rep the route would refuse. */
export function fenActiveColor(fen: string | null | undefined): "white" | "black" | null {
  if (typeof fen !== "string") return null;
  const field = fen.split(" ")[1];
  return field === "w" ? "white" : field === "b" ? "black" : null;
}

/**
 * A ply is eligible iff it is the player's turn and a move was played from it (`ply <
 * moves.length`). The second clause is one comparison on purpose: the spine holds one more
 * position than moves, so exactly the last position has no played move, whatever ended the
 * game; a position that was claimable as a draw at ply 40 and played on from is still a
 * decision. Length drift between the two arrays makes no ply eligible.
 */
export function isEligiblePly(p: { ply: number; fenSequence: string[] | null | undefined; moves: string[] | null | undefined; playerColor: "white" | "black" }): boolean {
  const { ply, fenSequence, moves, playerColor } = p;
  if (!Array.isArray(fenSequence) || !Array.isArray(moves)) return false;
  if (fenSequence.length !== moves.length + 1) return false;
  if (!(Number.isInteger(ply) && ply >= 0 && ply < moves.length)) return false;
  return fenActiveColor(fenSequence[ply]) === playerColor;
}

/**
 * The plies Learn prompts at, ascending: with "inaccuracies+ only" checked, the eligible plies
 * carrying a stored classification; unchecked, every eligible ply. Checked with no classified
 * plies is the empty set — never a fall-through to every ply, which would run reps at
 * unclassified plies while the filter reads as on. Eligibility is applied in both branches.
 */
export function learnPromptPlies(p: { fenSequence: string[] | null | undefined; moves: string[] | null | undefined; playerColor: "white" | "black"; inaccOnly: boolean; blunderPlies: number[] }): number[] {
  const { fenSequence, moves, playerColor, inaccOnly, blunderPlies } = p;
  if (!Array.isArray(fenSequence) || !Array.isArray(moves)) return [];
  if (fenSequence.length !== moves.length + 1) return [];
  const eligible = (ply: number) => isEligiblePly({ ply, fenSequence, moves, playerColor });
  if (inaccOnly) return [...blunderPlies].sort((a, b) => a - b).filter(eligible);
  const out: number[] = [];
  for (let ply = 0; ply < moves.length; ply++) if (eligible(ply)) out.push(ply);
  return out;
}

/** The Learn stepper: the next or previous prompt ply, or `from` when there is none. */
export function learnStepPly(from: number, dir: 1 | -1, promptPlies: number[]): number {
  if (dir === 1) {
    const next = promptPlies.find((x) => x > from);
    return next != null ? next : from;
  }
  let prev: number | null = null;
  for (const x of promptPlies) {
    if (x < from) prev = x;
    else break;
  }
  return prev != null ? prev : from;
}

/** `0:42` — elapsed only, no target. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** `conflict` is a client reusing an attempt id, never the player changing their move. */
export type LearnCommitFailure = "network" | "conflict" | "gone" | "invalid";
export type LearnCommitState = "idle" | "saving" | "saved" | "failed";

function commitFailureKind(e: unknown): LearnCommitFailure {
  const status = e instanceof ApiError ? e.status : undefined;
  if (status === 409) return "conflict";
  if (status === 410) return "gone";
  if (status === 422 || status === 404) return "invalid";
  return "network";
}

/** A v4 UUID; the fallback keeps the route's uuid parse satisfied where `randomUUID` is missing. */
function newAttemptId(): string {
  const c = globalThis.crypto as (Crypto & { randomUUID?: () => string }) | undefined;
  if (c?.randomUUID) return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (c?.getRandomValues) c.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export interface LearnRep {
  prompts: readonly string[];
  /** Index into `prompts`; equals `prompts.length` at the commit step. */
  promptIndex: number;
  atCommitStep: boolean;
  advance: () => void;
  /** The one leak gate: everything the page blanks at a prompted ply keys off this. */
  revealed: boolean;
  /** The committed SAN, or null when the rep was skipped (nothing stored). */
  committedMove: string | null;
  skipped: boolean;
  selected: Square | null;
  legalTargets: { to: Square; capture: boolean }[];
  promotionPending: boolean;
  move: (from: Square, to: Square) => boolean;
  squareClick: (square: Square) => void;
  choosePromotion: (p: PromotionPiece) => void;
  skip: () => void;
  /** True iff this rep is timed; untimed means nothing is measured, not merely hidden. */
  timed: boolean;
  elapsedMs: number;
  commitState: LearnCommitState;
  commitFailure: LearnCommitFailure | null;
  retryCommit: () => void;
}

/**
 * The rep at one ply. `enabled` is the page's composition of mode, availability and whether
 * this ply prompts; when false the hook holds no rep and every control is inert. Any change of
 * game, ply or enablement discards the rep — its prompts, timer and attempt id — so a new rep
 * is never recorded against a previous rep's identity.
 */
export function useLearnMode({ enabled, gameId, ply, fen, showTimer }: { enabled: boolean; gameId: number; ply: number; fen: string | null; showTimer: boolean }): LearnRep {
  const inCheck = useMemo(() => {
    if (!enabled || !fen) return false;
    try {
      return new Chess(fen).isCheck();
    } catch {
      return false;
    }
  }, [enabled, fen]);
  const prompts = inCheck ? LEARN_PROMPTS_IN_CHECK : LEARN_PROMPTS_STANDARD;

  const [promptIndex, setPromptIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [skipped, setSkipped] = useState(false);
  const [committedMove, setCommittedMove] = useState<string | null>(null);
  const [selected, setSelected] = useState<Square | null>(null);
  const [promo, setPromo] = useState<{ from: Square; to: Square } | null>(null);
  const [commitState, setCommitState] = useState<LearnCommitState>("idle");
  const [commitFailure, setCommitFailure] = useState<LearnCommitFailure | null>(null);
  // The request body is built once at the commit and kept: every retry, silent or manual, sends
  // exactly it. The timer preference changing afterwards is for the next rep, not this record.
  const [commitBody, setCommitBody] = useState<LearnCommitBody | null>(null);

  // One attempt id per rep: a retry reuses it (that is the idempotency key's job), a fresh rep
  // gets a new one. `epoch` counts rep boundaries: the timer's reset and the guard that drops a
  // late commit response from a rep the player has already left.
  const [attemptId, setAttemptId] = useState(newAttemptId);
  const [epoch, setEpoch] = useState(0);

  // The timer: accumulated + running-since, so pause and resume carry no per-tick error; `tick`
  // only re-renders the display.
  const [timed, setTimed] = useState(showTimer);
  const accumRef = useRef(0);
  const sinceRef = useRef<number | null>(null);
  const stoppedRef = useRef(false);
  /** The displayed value, refreshed by the tick and fixed at the stop. */
  const [elapsedMs, setElapsedMs] = useState(0);

  const readElapsed = useCallback(() => accumRef.current + (sinceRef.current != null ? Date.now() - sinceRef.current : 0), []);
  const pauseTimer = useCallback(() => {
    if (sinceRef.current == null) return;
    accumRef.current += Date.now() - sinceRef.current;
    sinceRef.current = null;
  }, []);
  /** Stop freezes the value; it is the player's commit that stops it, never the network. */
  const stopTimer = useCallback(() => {
    pauseTimer();
    stoppedRef.current = true;
  }, [pauseTimer]);
  const boundary = `${gameId}:${ply}:${enabled ? "1" : "0"}`;
  const [trackedBoundary, setTrackedBoundary] = useState(boundary);
  const startRep = useCallback(
    (nextTimed: boolean) => {
      setAttemptId(newAttemptId());
      setPromptIndex(0);
      setRevealed(false);
      setSkipped(false);
      setCommittedMove(null);
      setSelected(null);
      setPromo(null);
      setCommitState("idle");
      setCommitFailure(null);
      setCommitBody(null);
      setElapsedMs(0);
      setTimed(nextTimed);
      setEpoch((e) => e + 1);
    },
    [],
  );
  if (trackedBoundary !== boundary) {
    // Adjusted during render so the new ply never paints with the previous rep's reveal.
    setTrackedBoundary(boundary);
    startRep(showTimer);
  }
  // The timer's refs are reset for the new rep before the start effect below runs (effects run
  // in declaration order).
  useEffect(() => {
    accumRef.current = 0;
    sinceRef.current = null;
    stoppedRef.current = false;
  }, [epoch]);

  // The rep starts when its first prompt is painted (an effect, after paint: loading time is
  // never counted), pauses while the tab is hidden, and never restarts once stopped.
  useEffect(() => {
    if (!enabled || !timed || revealed || stoppedRef.current) return;
    if (sinceRef.current == null && document.visibilityState !== "hidden") sinceRef.current = Date.now();
  }, [enabled, timed, revealed, epoch]);
  useEffect(() => {
    if (!enabled || !timed || revealed) return;
    const id = window.setInterval(() => setElapsedMs(readElapsed()), 250);
    return () => window.clearInterval(id);
  }, [enabled, timed, revealed, epoch, readElapsed]);
  useEffect(() => {
    if (!enabled || !timed) return;
    const onVisibility = () => {
      if (document.visibilityState === "hidden") pauseTimer();
      else if (!stoppedRef.current && !revealed && sinceRef.current == null) sinceRef.current = Date.now();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [enabled, timed, revealed, pauseTimer]);

  // The preference toggled mid-rep: off stops and discards (this rep commits untimed); on waits
  // for the next rep. `timed` is only ever forced down mid-rep, never up.
  const prevShowTimer = useRef(showTimer);
  useEffect(() => {
    if (prevShowTimer.current && !showTimer) {
      pauseTimer();
      accumRef.current = 0;
      stoppedRef.current = true;
      setElapsedMs(0);
      setTimed(false);
    }
    prevShowTimer.current = showTimer;
  }, [showTimer, pauseTimer]);

  const atCommitStep = promptIndex >= prompts.length;
  const advance = useCallback(() => {
    if (revealed) return;
    setPromptIndex((i) => (i < prompts.length ? i + 1 : i));
  }, [revealed, prompts.length]);

  // The commit. The reveal happens first and is never conditional on the write: a failure of
  // any kind leaves the reveal on screen and reports beside it.
  const epochRef = useRef(epoch);
  useEffect(() => {
    epochRef.current = epoch;
  }, [epoch]);

  const postCommit = useCallback(
    async (body: LearnCommitBody, myEpoch: number) => {
      setCommitState("saving");
      setCommitFailure(null);
      try {
        await learnCommit(gameId, body);
        if (myEpoch === epochRef.current) setCommitState("saved");
      } catch (e) {
        const kind = commitFailureKind(e);
        // A network failure is retried once with the same attempt id and bytes — the lost-response
        // case the key exists for. An HTTP status is a decided answer and is never retried.
        if (kind === "network") {
          try {
            await learnCommit(gameId, body);
            if (myEpoch === epochRef.current) setCommitState("saved");
            return;
          } catch (e2) {
            if (myEpoch !== epochRef.current) return;
            setCommitState("failed");
            setCommitFailure(commitFailureKind(e2));
            return;
          }
        }
        if (myEpoch !== epochRef.current) return;
        // A 409 means the id collided: rotate it so the next commit cannot collide again. The
        // reveal stays — the rep is the point, the record is secondary.
        if (kind === "conflict") setAttemptId(newAttemptId());
        setCommitState("failed");
        setCommitFailure(kind);
      }
    },
    [gameId],
  );

  const commit = useCallback(
    (san: string) => {
      if (revealed) return;
      stopTimer();
      const elapsed = timed ? Math.round(readElapsed()) : null;
      if (elapsed != null) setElapsedMs(elapsed);
      // `elapsed_ms` is omitted, never null, when untimed: NULL in the column has one meaning.
      const body: LearnCommitBody = elapsed == null ? { attempt_id: attemptId, ply, committed_move: san } : { attempt_id: attemptId, ply, committed_move: san, elapsed_ms: elapsed };
      setCommitBody(body);
      setCommittedMove(san);
      setSkipped(false);
      setRevealed(true);
      setSelected(null);
      setPromo(null);
      void postCommit(body, epochRef.current);
    },
    [revealed, stopTimer, timed, readElapsed, postCommit, attemptId, ply],
  );

  const retryCommit = useCallback(() => {
    if (!commitBody || commitState === "saving") return;
    // The same attempt id and the same bytes: that is what makes the retry decidable forever.
    void postCommit(commitBody, epochRef.current);
  }, [commitBody, commitState, postCommit]);

  // Move entry. A pawn reaching the last rank snaps back and opens the chooser (by drag or by a
  // second tap), so the committed move carries the piece the player chose, never an auto-queen.
  const boardAt = useCallback((): Chess | null => {
    if (!fen) return null;
    try {
      return new Chess(fen);
    } catch {
      return null;
    }
  }, [fen]);

  const apply = useCallback(
    (from: Square, to: Square, promotion: PromotionPiece): boolean => {
      const board = boardAt();
      if (!board) return false;
      let mv;
      try {
        mv = legalMove(board, { from, to, promotion });
      } catch {
        return false;
      }
      if (!mv) return false;
      commit(mv.san);
      return true;
    },
    [boardAt, commit],
  );

  const move = useCallback(
    (from: Square, to: Square): boolean => {
      if (!enabled || revealed || !atCommitStep) return false;
      const board = boardAt();
      if (!board) return false;
      const promoMove = board.moves({ square: from, verbose: true }).find((m) => m.to === to && m.promotion);
      if (promoMove) {
        setPromo({ from, to });
        setSelected(null);
        return false;
      }
      return apply(from, to, "q");
    },
    [enabled, revealed, atCommitStep, boardAt, apply],
  );

  const squareClick = useCallback(
    (square: Square) => {
      if (!enabled || revealed || !atCommitStep) return;
      const board = boardAt();
      if (!board) return;
      if (selected) {
        if (square === selected) {
          setSelected(null);
          return;
        }
        const mv = board.moves({ square: selected, verbose: true }).find((m) => m.to === square);
        if (mv) {
          if (mv.promotion) {
            setPromo({ from: selected, to: square });
            setSelected(null);
          } else {
            apply(selected, square, "q");
          }
          return;
        }
        const piece = board.get(square);
        setSelected(piece && piece.color === board.turn() ? square : null);
        return;
      }
      const piece = board.get(square);
      if (piece && piece.color === board.turn() && board.moves({ square, verbose: true }).length > 0) setSelected(square);
    },
    [enabled, revealed, atCommitStep, boardAt, selected, apply],
  );

  const choosePromotion = useCallback(
    (p: PromotionPiece) => {
      if (!promo) return;
      const { from, to } = promo;
      setPromo(null);
      apply(from, to, p);
    },
    [promo, apply],
  );

  /** "Just show me": reveals, writes no row, discards the timer. */
  const skip = useCallback(() => {
    if (revealed) return;
    pauseTimer();
    accumRef.current = 0;
    stoppedRef.current = true;
    setElapsedMs(0);
    setSkipped(true);
    setCommittedMove(null);
    setRevealed(true);
    setSelected(null);
    setPromo(null);
    setCommitState("idle");
    setCommitFailure(null);
  }, [revealed, pauseTimer]);

  const legalTargets = useMemo(() => {
    if (!enabled || revealed || !selected) return [];
    const board = boardAt();
    if (!board) return [];
    return board.moves({ square: selected, verbose: true }).map((m) => ({ to: m.to as Square, capture: !!m.captured }));
  }, [enabled, revealed, selected, boardAt]);

  return {
    prompts,
    promptIndex,
    atCommitStep,
    advance,
    revealed,
    committedMove,
    skipped,
    selected,
    legalTargets,
    promotionPending: promo != null,
    move,
    squareClick,
    choosePromotion,
    skip,
    timed,
    elapsedMs: timed ? elapsedMs : 0,
    commitState,
    commitFailure,
    retryCommit,
  };
}
