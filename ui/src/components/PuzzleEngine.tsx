/**
 * PuzzleEngine: the interactive solver. Given a FEN and a solution line (SAN), the player plays
 * their moves on the board and the opponent's replies auto-play. A wrong move shows feedback
 * and can be retried from the position it was played in; a solve can be replayed from the start.
 *
 * Map mode: an own_mate puzzle carries an acceptance map, and then any map-optimal move is
 * accepted at each node (not just the stored line) and the opponent replies with the map's
 * canonical defence. Solve = mate delivered.
 *
 * Review: a pass (from the start, or from Replay) that reaches its last ply with boards whose
 * first move in the pass was wrong goes back to those boards, in line order and cycling, until
 * each has been answered right `REVIEW_STREAK` times in a row; a wrong answer resets that board
 * alone. The completing `onComplete` has already fired by then, so the review sends nothing and
 * leaving mid-review loses nothing the server was owed. Play On is never reviewed, nor a segment
 * with fewer than two player moves.
 *
 * Navigation: `onNavState` reports, synchronously from the handler that changes it, whether the
 * host may offer a way past this puzzle — `fresh` before any verdict, `owed` from a pass's first
 * miss until its review ends, `free` once a verdict is in and nothing is owed.
 *
 * Promotion: a pawn move to the last rank is not played on the drop. The board snaps it back,
 * the squares are held, and the chooser under the board applies the chosen piece as the real
 * move; from/to squares cannot tell a queen from a knight, and the line is graded on the piece.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";
import type { Move, Square } from "chess.js";
import { Chessboard } from "react-chessboard";
import { BranchCompareView } from "./BranchCompareView";
import { ExploreLayer } from "./ExploreLayer";
import { PromotionChooser } from "./PromotionChooser";
import type { PromotionPiece } from "./PromotionChooser";
import { LineReaderPanel } from "./PositionCard/LineReaderPanel";
import { SolverSimilarModal } from "./PositionCard/SolverSimilarModal";
import { useSimilarPositions } from "../hooks/useSimilarPositions";
import { HIGHLIGHT, SQUARES } from "../utils/board";
import { errorLabel } from "../utils/errorLabel";
import { branchCompareTarget, legalMove, mapKey, moveUci, parsesAsFen, sanResolvesToMove, similarTarget, uciToMove } from "../utils/chess";
import type { AcceptanceMap } from "../practice";

type PuzzleState = "playing" | "wrong" | "solved" | "reviewing";

/** Consecutive right answers a missed board needs in review. Fixed, not a setting. */
export const REVIEW_STREAK = 3;

export type NavState = "fresh" | "owed" | "free";

/** A board whose first move in the pass was wrong: where it sits in the line and how it was reached. */
interface MissedBoard {
  fen: string;
  index: number;
  lastMove: [Square, Square] | null;
}

interface Review {
  boards: MissedBoard[];
  streaks: number[];
  at: number;
  verdict: "right" | "wrong" | null;
}

export interface PuzzleEngineProps {
  fen: string;
  /** Alternates player/opponent from the FEN's side to move vs `color`; entry 0 may be the opponent's. */
  solutionLine: string[];
  color: "w" | "b";
  /** Fires once per attempt with the cleaned player-move sequence (a retried wrong move is sliced off). */
  onComplete?: (solved: boolean, movesPlayed: string[]) => void;
  /** Repertoire puzzles: truncate the line to this many plies; the rest is offered as Play On. */
  presentationPly?: number | null;
  acceptanceMap?: AcceptanceMap | null;
  onPrev?: (() => void) | null;
  onNext?: (() => void) | null;
  prevDisabled?: boolean;
  nextDisabled?: boolean;
  nextLabel?: string;
  nextHighlighted?: boolean;
  /** Post-attempt Next; when false the same slot shows Skip. */
  showNextButton?: boolean;
  isRepertoire?: boolean;
  /** Set by the parent when the server recorded a claimed solve as wrong. Visual only. */
  serverDowngraded?: boolean;
  /** 'pending_retry' shows a save-failed banner; 'in_flight' is deliberately not surfaced. */
  attemptStatus?: "in_flight" | "pending_retry" | null;
  /** One attempt at a time: while the parent still owes the server one, no control that
   *  could produce another (Try Again, Replay, Play On, the board) is available. */
  submissionLocked?: boolean;
  /** The repertoire line a deviation puzzle came from: offers its walk-through under the board. */
  repertoireLineId?: number | null;
  /** Keep the navigation row's space while neither handler is set (invisible), so the board
   *  does not move when one arrives. */
  reserveNavRow?: boolean;
  /** Render the right-hand navigation button invisible and out of the accessibility tree,
   *  keeping its space. */
  nextHidden?: boolean;
  /** Whether the host may offer a way past this puzzle; called synchronously on every change. */
  onNavState?: (state: NavState) => void;
}

const btn = "rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900 disabled:opacity-40 disabled:pointer-events-none";
const launcher = "flex-1 rounded border px-3 py-2 text-xs font-medium";
const launcherOn = "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-300";
const launcherOff = "pointer-events-none border-transparent text-transparent";
const btnAccent = "rounded border border-sky-300 bg-sky-50 px-3 py-1.5 text-sm text-sky-800 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-300 disabled:opacity-40 disabled:pointer-events-none";

export function PuzzleEngine({
  fen,
  solutionLine: solutionLineProp,
  color,
  onComplete,
  presentationPly = null,
  acceptanceMap = null,
  onPrev,
  onNext,
  prevDisabled = false,
  nextDisabled = false,
  nextLabel = "Next Puzzle →",
  nextHighlighted = false,
  showNextButton = false,
  isRepertoire = false,
  serverDowngraded = false,
  attemptStatus = null,
  submissionLocked = false,
  repertoireLineId = null,
  reserveNavRow = false,
  nextHidden = false,
  onNavState,
}: PuzzleEngineProps) {
  const solutionLine = useMemo(
    () => (presentationPly != null && presentationPly > 0 ? solutionLineProp.slice(0, presentationPly + 1) : solutionLineProp),
    // solutionLineProp is a fresh array each render; key on its content.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [solutionLineProp.join(","), presentationPly],
  );

  // Play On: after solving the truncated line, play the remainder from where it ended.
  const [finishLineMode, setFinishLineMode] = useState(false);
  const [finishLineRemainingMoves, setFinishLineRemainingMoves] = useState<string[]>([]);
  const [finishLineFen, setFinishLineFen] = useState(fen);
  const activeSolutionLine = finishLineMode ? finishLineRemainingMoves : solutionLine;

  // Every optimal line forces mate in n moves = solutionLine.length plies, so index bookkeeping is shared.
  const mapMode = !finishLineMode && acceptanceMap != null && typeof acceptanceMap.n === "number" && acceptanceMap.n >= 1 && acceptanceMap.p != null && acceptanceMap.d != null;

  const [game, setGame] = useState(() => new Chess(fen));
  const [moveIndex, setMoveIndex] = useState(0);
  const [puzzleState, setPuzzleState] = useState<PuzzleState>("playing");
  const [movesPlayed, setMovesPlayed] = useState<string[]>([]);
  const [lastMove, setLastMove] = useState<[Square, Square] | null>(null);
  const [wrongSquares, setWrongSquares] = useState<Record<string, React.CSSProperties>>({});
  const [rightSquares, setRightSquares] = useState<Record<string, React.CSSProperties>>({});
  const [statusMessage, setStatusMessage] = useState("Your turn — find the best move");
  const [selectedSquare, setSelectedSquare] = useState<Square | null>(null);
  // A pawn move to the last rank waiting for its piece; the board shows the position before it.
  const [promotionPending, setPromotionPending] = useState<{ from: Square; to: Square } | null>(null);
  const [solutionShown, setSolutionShown] = useState(false);
  const completeCalled = useRef(false);
  // Compare similar positions: what else the opponent could have played before the latest decision
  // node. Not in map mode (the player may leave the stored line, so the replay no longer describes
  // the board), and not gated on attempt state: opening it before solving spoils one's own puzzle,
  // like Show me, and touches no scoring. Closed on every puzzle change.
  const [branchCompareOpen, setBranchCompareOpen] = useState(false);
  useEffect(() => setBranchCompareOpen(false), [fen]);
  const compareTarget = useMemo(() => (mapMode ? null : branchCompareTarget(finishLineMode ? finishLineFen : fen, activeSolutionLine, moveIndex, color)), [mapMode, finishLineMode, finishLineFen, fen, activeSolutionLine, moveIndex, color]);
  // Similar positions in the repertoire: the board at the latest player decision and the line's move
  // there. Its own target, not Compare's — no parent is needed, so the first decision and a one-move
  // puzzle have one. Ungated like Compare; the view's neighbours name the book move, so opening it
  // before answering spoils one's own puzzle in the same way. Closed on every puzzle change. The
  // search is owned here, enabled while the view is open: reopening on the same board shows the
  // remembered answer, and a target change while open (the reply auto-playing) asks about the new one.
  const [similarOpen, setSimilarOpen] = useState(false);
  useEffect(() => setSimilarOpen(false), [fen]);
  const similarAt = useMemo(() => (mapMode ? null : similarTarget(finishLineMode ? finishLineFen : fen, activeSolutionLine, moveIndex, color)), [mapMode, finishLineMode, finishLineFen, fen, activeSolutionLine, moveIndex, color]);
  const similarSearch = useSimilarPositions(similarAt?.fen ?? "", similarAt?.move ?? null, similarOpen && similarAt !== null);
  // A view whose target has gone (Play On into a reply, a line change) is closed, not left open to
  // reappear on its own when the next target arrives.
  useEffect(() => {
    if (!compareTarget) setBranchCompareOpen(false);
    if (!similarAt) setSimilarOpen(false);
  }, [compareTarget, similarAt]);
  // Explore from here: the board the solver is showing at the click — after the player's moves and
  // any auto-played reply — captured into state so a reply landing while the layer is open does
  // not re-seed it. Ungated like the other two (opening it before answering spoils one's own puzzle
  // and touches no scoring), closed on every puzzle change. Map mode is not excluded: the layer
  // needs only a FEN. The solver stays mounted underneath and owes nothing.
  const [exploreSeed, setExploreSeed] = useState<string | null>(null);
  useEffect(() => setExploreSeed(null), [fen]);
  const closeExplore = useCallback(() => setExploreSeed(null), []);
  const canExplore = parsesAsFen(game.fen()); // always, on a board chess.js itself produced
  // Where the wrong move was played from; Try Again restores here, not the puzzle start.
  const checkpointFen = useRef(fen);
  const checkpointMoveIndex = useRef(0);
  const checkpointLastMove = useRef<[Square, Square] | null>(null);
  // The current pass: boards missed (by board key, first visit only) and boards answered right.
  const passMissed = useRef<Map<string, MissedBoard>>(new Map());
  const passAnswered = useRef<Set<string>>(new Set());
  const [review, setReview] = useState<Review | null>(null);
  // The pass's end as it was shown — board, place in the line, last move, message — shown again
  // when its review finishes.
  const completedBoard = useRef<{ fen: string; index: number; lastMove: [Square, Square] | null; message: string }>({ fen, index: 0, lastMove: null, message: "" });
  const navState = useRef<NavState>("fresh");
  const onNavStateRef = useRef(onNavState);
  useEffect(() => {
    onNavStateRef.current = onNavState;
  }, [onNavState]);
  const reportNav = useCallback((state: NavState) => {
    if (navState.current === state) return;
    navState.current = state;
    onNavStateRef.current?.(state);
  }, []);

  const boardOrientation = color === "w" ? "white" : "black";
  const activeFen = finishLineMode ? finishLineFen : fen;
  const fenActiveColor = activeFen.split(" ")[1] as "w" | "b";
  const userMovesFirst = fenActiveColor === color;
  const isUserTurn = userMovesFirst ? moveIndex % 2 === 0 : moveIndex % 2 === 1;
  const totalUserMoves = userMovesFirst ? Math.ceil(activeSolutionLine.length / 2) : Math.floor(activeSolutionLine.length / 2);
  // Whether a move may be entered on the board: the pass's turn, or a review board awaiting its answer.
  const acceptingMoves = (puzzleState === "playing" || (puzzleState === "reviewing" && review?.verdict === null)) && isUserTurn && !submissionLocked;

  // Final-ply alternate mate: when the stored line ends in checkmate, any move that itself mates
  // on the last player ply is accepted (the server agrees). Earlier plies are not relaxed.
  const mateInfo = useMemo(() => {
    try {
      const probe = new Chess(fen);
      let lastPlayerIdx = -1;
      for (let i = 0; i < solutionLine.length; i++) {
        if ((probe.turn() === "w") === (color === "w")) lastPlayerIdx = i;
        probe.move(solutionLine[i]);
      }
      return { isMate: probe.isCheckmate(), lastPlayerIdx };
    } catch {
      return { isMate: false, lastPlayerIdx: -1 };
    }
  }, [fen, solutionLine, color]);

  const canFinishLine = isRepertoire && puzzleState === "solved" && !serverDowngraded && !finishLineMode && solutionLineProp.length > solutionLine.length;

  const resetToFen = useCallback((startFen: string, startIndex: number) => {
    setGame(new Chess(startFen));
    setMoveIndex(startIndex);
    setPuzzleState("playing");
    setMovesPlayed([]);
    setLastMove(null);
    setWrongSquares({});
    setRightSquares({});
    setSelectedSquare(null);
    setPromotionPending(null);
    setStatusMessage("Your turn — find the best move");
    setSolutionShown(false);
    setReview(null);
    passMissed.current = new Map();
    passAnswered.current = new Set();
    completeCalled.current = false;
    checkpointFen.current = startFen;
    checkpointMoveIndex.current = startIndex;
    checkpointLastMove.current = null;
  }, []);

  // Reset when the puzzle changes, and only then. The state above already starts as a reset
  // would leave it, so the mount run is skipped: a move played before effects had run (a fast
  // first drop) would otherwise be wiped. Keyed on what the puzzle is, so StrictMode's second
  // effect run, which changes nothing, does not reset either.
  const appliedPuzzle = useRef(`${fen}\n${solutionLine.join(",")}\n${color}`);
  useEffect(() => {
    const puzzle = `${fen}\n${solutionLine.join(",")}\n${color}`;
    if (appliedPuzzle.current === puzzle) return;
    appliedPuzzle.current = puzzle;
    navState.current = "fresh";
    setFinishLineMode(false);
    setFinishLineFen(fen);
    setFinishLineRemainingMoves([]);
    resetToFen(fen, 0);
  }, [fen, solutionLine, color, resetToFen]);

  const showReviewBoard = useCallback((board: MissedBoard) => {
    setGame(new Chess(board.fen));
    setMoveIndex(board.index);
    setLastMove(board.lastMove);
    setWrongSquares({});
    setRightSquares({});
    setSelectedSquare(null);
    setPromotionPending(null);
    setSolutionShown(false);
    setStatusMessage("Find the move");
  }, []);

  // The end of a pass. The completing attempt is sent first, as it always was; then the pass's
  // misses, if any are reviewable, become the review, and otherwise the puzzle is solved.
  const completePass = useCallback(
    (final: Chess, finalMove: [Square, Square], message: string, moves: string[]) => {
      setRightSquares({ [finalMove[1]]: { backgroundColor: HIGHLIGHT.correct } });
      if (!completeCalled.current && !finishLineMode) {
        completeCalled.current = true;
        onComplete?.(true, moves);
      }
      const boards = [...passMissed.current.values()].sort((a, b) => a.index - b.index);
      if (!finishLineMode && boards.length > 0 && totalUserMoves >= 2) {
        completedBoard.current = { fen: final.fen(), index: activeSolutionLine.length, lastMove: finalMove, message };
        setReview({ boards, streaks: boards.map(() => 0), at: 0, verdict: null });
        setPuzzleState("reviewing");
        showReviewBoard(boards[0]);
        return;
      }
      setPuzzleState("solved");
      setStatusMessage(message);
      if (!finishLineMode) reportNav("free");
    },
    [finishLineMode, onComplete, totalUserMoves, activeSolutionLine.length, showReviewBoard, reportNav],
  );

  // The next review board after the one on show: line order, wrapping, among those still short of
  // the streak; when none is, the review is over and the pass's last board comes back.
  const advanceReview = useCallback(() => {
    if (!review) return;
    const n = review.boards.length;
    let next = -1;
    for (let step = 1; step <= n; step++) {
      const i = (review.at + step) % n;
      if (review.streaks[i] < REVIEW_STREAK) {
        next = i;
        break;
      }
    }
    if (next < 0) {
      const end = completedBoard.current;
      setReview(null);
      setGame(new Chess(end.fen));
      setMoveIndex(end.index);
      setLastMove(end.lastMove);
      setWrongSquares({});
      setRightSquares(end.lastMove ? { [end.lastMove[1]]: { backgroundColor: HIGHLIGHT.correct } } : {});
      setSelectedSquare(null);
      setSolutionShown(false);
      setPuzzleState("solved");
      setStatusMessage(end.message);
      reportNav("free");
      return;
    }
    setReview({ ...review, at: next, verdict: null });
    showReviewBoard(review.boards[next]);
  }, [review, showReviewBoard, reportNav]);

  // A right review answer stays on the board briefly, then the next board comes up.
  useEffect(() => {
    if (review?.verdict !== "right") return;
    const timer = setTimeout(advanceReview, 600);
    return () => clearTimeout(timer);
  }, [review, advanceReview]);

  // Auto-play the opponent's reply.
  useEffect(() => {
    if (puzzleState !== "playing") return;
    if (moveIndex >= activeSolutionLine.length) return;
    if (isUserTurn) return;

    const timer = setTimeout(() => {
      const g = new Chess(game.fen());
      let opponentMove: string | { from: string; to: string; promotion?: string };
      if (mapMode) {
        const defUci = acceptanceMap!.d[mapKey(g)];
        if (!defUci) {
          // No canonical defence at this node: should not happen on an optimal line. Leave it
          // the player's turn rather than crashing.
          console.error("No canonical defence for position:", g.fen());
          return;
        }
        opponentMove = uciToMove(defUci);
      } else {
        opponentMove = activeSolutionLine[moveIndex];
      }
      try {
        const result = g.move(opponentMove);
        if (result) {
          setGame(g);
          setLastMove([result.from as Square, result.to as Square]);
          setMoveIndex((prev) => prev + 1);
          setStatusMessage("Your turn");
          if (moveIndex + 1 >= activeSolutionLine.length) {
            completePass(g, [result.from as Square, result.to as Square], finishLineMode ? "Line complete!" : "Puzzle complete!", movesPlayed);
          }
        }
      } catch {
        console.error("Invalid opponent move in solution:", opponentMove);
      }
    }, 400);

    return () => clearTimeout(timer);
  }, [moveIndex, puzzleState, isUserTurn, game, activeSolutionLine, movesPlayed, finishLineMode, mapMode, acceptanceMap, completePass]);

  // The one path every player move takes, whichever way it was entered: play it on a copy of
  // the board, then grade it. Returns whether the board should show the move.
  const playMove = useCallback(
    (sourceSquare: Square, targetSquare: Square, promotion: PromotionPiece): boolean => {
      if (!acceptingMoves) return false;
      if (moveIndex >= activeSolutionLine.length) return false;

      const expectedMove = activeSolutionLine[moveIndex];
      const tempGame = new Chess(game.fen());
      let result: Move | null;
      try {
        result = legalMove(tempGame, { from: sourceSquare, to: targetSquare, promotion });
      } catch {
        return false;
      }
      if (!result) return false;
      // A move that was actually made supersedes a held promotion; a miss (an illegal drop) does not.
      setPromotionPending(null);
      const boardKey = mapKey(game);

      // A review board: graded as the pass grades this ply, and nothing is sent.
      if (puzzleState === "reviewing" && review) {
        const right = mapMode
          ? (acceptanceMap!.p[boardKey] ?? []).includes(moveUci(result))
          : sanResolvesToMove(game.fen(), expectedMove, result) || (mateInfo.isMate && moveIndex === mateInfo.lastPlayerIdx && tempGame.isCheckmate());
        setSelectedSquare(null);
        if (right) {
          const streak = review.streaks[review.at] + 1;
          setGame(tempGame);
          setLastMove([sourceSquare, targetSquare]);
          setWrongSquares({});
          setRightSquares({ [targetSquare]: { backgroundColor: HIGHLIGHT.correct } });
          setStatusMessage(`Right — ${streak} in a row`);
          setReview({ ...review, streaks: review.streaks.map((v, i) => (i === review.at ? streak : v)), verdict: "right" });
          return true;
        }
        setWrongSquares({ [targetSquare]: { backgroundColor: HIGHLIGHT.wrong } });
        setStatusMessage("Not quite — this board starts again");
        setReview({ ...review, streaks: review.streaks.map((v, i) => (i === review.at ? 0 : v)), verdict: "wrong" });
        return false;
      }

      const acceptCorrect = (finalMessage: string | null) => {
        if (!finishLineMode && !passMissed.current.has(boardKey)) passAnswered.current.add(boardKey);
        setGame(tempGame);
        setLastMove([sourceSquare, targetSquare]);
        setMovesPlayed((prev) => [...prev, result.san]);
        setMoveIndex((prev) => prev + 1);
        setWrongSquares({});
        setSelectedSquare(null);
        setRightSquares({ [targetSquare]: { backgroundColor: HIGHLIGHT.correct } });
        if (finalMessage !== null) {
          completePass(tempGame, [sourceSquare, targetSquare], finalMessage, [...movesPlayed, result.san]);
        } else {
          setStatusMessage("Correct!");
        }
      };
      const rejectWrong = (message: string) => {
        if (!finishLineMode) {
          if (!passMissed.current.has(boardKey) && !passAnswered.current.has(boardKey)) {
            passMissed.current.set(boardKey, { fen: game.fen(), index: moveIndex, lastMove });
          }
          reportNav("owed");
        }
        checkpointFen.current = game.fen();
        checkpointMoveIndex.current = moveIndex;
        checkpointLastMove.current = lastMove;
        setWrongSquares({ [targetSquare]: { backgroundColor: HIGHLIGHT.wrong } });
        setSelectedSquare(null);
        setMovesPlayed((prev) => [...prev, result.san]);
        setPuzzleState("wrong");
        setStatusMessage(message);
        if (!completeCalled.current && !finishLineMode) {
          completeCalled.current = true;
          onComplete?.(false, [...movesPlayed, result.san]);
        }
      };

      if (mapMode) {
        const accepted = acceptanceMap!.p[mapKey(game)];
        if (accepted && accepted.includes(moveUci(result))) {
          // An accepted move can only mate at the final node.
          acceptCorrect(tempGame.isCheckmate() ? "Checkmate!" : null);
          return true;
        }
        rejectWrong("There’s a faster mate — find the quickest one");
        return false;
      }

      // Compare resolved moves, never SAN strings: the stored token may be spelled differently.
      if (sanResolvesToMove(game.fen(), expectedMove, result)) {
        const last = moveIndex + 1 >= activeSolutionLine.length;
        acceptCorrect(last ? (finishLineMode ? "Line complete!" : "Puzzle complete!") : null);
        return true;
      }
      if (!finishLineMode && mateInfo.isMate && moveIndex === mateInfo.lastPlayerIdx && tempGame.isCheckmate()) {
        acceptCorrect("Puzzle complete!");
        return true;
      }
      rejectWrong("Not quite — that's not the best move");
      return false;
    },
    [game, moveIndex, activeSolutionLine, puzzleState, movesPlayed, onComplete, finishLineMode, lastMove, mateInfo, mapMode, acceptanceMap, acceptingMoves, review, completePass, reportNav],
  );

  // A drop (or a click pair). A pawn reaching the last rank is held for the chooser instead of
  // being played: the board snaps it back and nothing is graded until a piece is chosen.
  const handleDrop = useCallback(
    (sourceSquare: Square, targetSquare: Square): boolean => {
      if (!acceptingMoves) return false;
      const promotes = game.moves({ square: sourceSquare, verbose: true }).some((m) => m.to === targetSquare && m.promotion);
      if (promotes) {
        setPromotionPending({ from: sourceSquare, to: targetSquare });
        setSelectedSquare(null);
        return false;
      }
      return playMove(sourceSquare, targetSquare, "q");
    },
    [game, acceptingMoves, playMove],
  );

  const choosePromotion = useCallback(
    (piece: PromotionPiece) => {
      if (!promotionPending) return;
      const { from, to } = promotionPending;
      setPromotionPending(null); // whatever the outcome: the choice was made
      playMove(from, to, piece);
    },
    [promotionPending, playMove],
  );
  // The chooser is only meaningful on the board it was opened on, with the move still the
  // player's to make; it is dropped with the reset that changes either.
  const promotionOpen = promotionPending !== null && acceptingMoves;

  // Click-to-move: first click selects, second attempts the move with the same validation as a drop.
  const handleSquareClick = useCallback(
    (square: Square) => {
      if (!acceptingMoves) return;
      const piece = game.get(square);
      const own = piece && piece.color === color;
      if (selectedSquare) {
        if (!handleDrop(selectedSquare, square)) setSelectedSquare(own ? square : null);
      } else if (own) {
        setSelectedSquare(square);
      }
    },
    [selectedSquare, acceptingMoves, game, color, handleDrop],
  );

  // Wrong-state retry only: restore the checkpoint and slice the wrong move off movesPlayed (it
  // was appended before the state flipped to 'wrong'), so the eventual onComplete carries a
  // clean sequence. Replay is NOT this; see handleReplay.
  const handleRetry = useCallback(() => {
    setGame(new Chess(checkpointFen.current));
    setMoveIndex(checkpointMoveIndex.current);
    setPuzzleState("playing");
    setWrongSquares({});
    setRightSquares({});
    setSelectedSquare(null);
    setPromotionPending(null);
    setStatusMessage("Your turn — find the best move");
    setSolutionShown(false);
    completeCalled.current = false;
    setMovesPlayed((prev) => prev.slice(0, -1));
    setLastMove(checkpointLastMove.current);
  }, []);

  // Solved-state replay: a full reset, including movesPlayed. Routing this through handleRetry
  // would leave stale moves polluting the next submission.
  const handleReplay = useCallback(() => {
    if (finishLineMode) resetToFen(finishLineFen, 0);
    else resetToFen(fen, 0);
  }, [fen, finishLineMode, finishLineFen, resetToFen]);

  // Show me: animate the next correct move after a wrong attempt. A peek only: moveIndex and
  // puzzleState are untouched and the attempt stays recorded as wrong.
  const handleShowMe = useCallback(() => {
    const reviewWrong = puzzleState === "reviewing" && review?.verdict === "wrong";
    if ((puzzleState !== "wrong" && !reviewWrong) || solutionShown) return;
    if (!mapMode && moveIndex >= activeSolutionLine.length) return;
    try {
      const g = new Chess(game.fen());
      let correctMove: string | { from: string; to: string; promotion?: string };
      if (mapMode) {
        const accepted = acceptanceMap!.p[mapKey(g)];
        if (!accepted || accepted.length === 0) return;
        correctMove = uciToMove(accepted[0]);
      } else {
        correctMove = activeSolutionLine[moveIndex];
      }
      const result = g.move(correctMove);
      if (result) {
        setGame(g);
        setLastMove([result.from as Square, result.to as Square]);
        setRightSquares({ [result.to]: { backgroundColor: HIGHLIGHT.correct } });
        setWrongSquares({});
        setSolutionShown(true);
      }
    } catch {
      console.error("Invalid solution move in handleShowMe");
    }
  }, [puzzleState, review, solutionShown, game, moveIndex, activeSolutionLine, mapMode, acceptanceMap]);

  const handleFinishLine = useCallback(() => {
    try {
      const g = new Chess(fen);
      for (const m of solutionLine) g.move(m);
      const continueFromFen = g.fen();
      setFinishLineFen(continueFromFen);
      setFinishLineRemainingMoves(solutionLineProp.slice(solutionLine.length));
      setFinishLineMode(true);
      resetToFen(continueFromFen, 0);
    } catch (e) {
      console.error("Failed to compute finish-line start FEN:", errorLabel(e));
    }
  }, [fen, solutionLine, solutionLineProp, resetToFen]);

  const squareStyles: Record<string, React.CSSProperties> = { ...wrongSquares, ...rightSquares };
  if (selectedSquare && acceptingMoves) {
    squareStyles[selectedSquare] = { ...squareStyles[selectedSquare], backgroundColor: HIGHLIGHT.selected };
    for (const m of game.moves({ square: selectedSquare, verbose: true })) {
      squareStyles[m.to] = { ...squareStyles[m.to], background: game.get(m.to as Square) ? HIGHLIGHT.legalRing : HIGHLIGHT.legalDot };
    }
  }
  if (lastMove) {
    squareStyles[lastMove[0]] = { ...squareStyles[lastMove[0]], backgroundColor: HIGHLIGHT.lastMove };
    squareStyles[lastMove[1]] = { ...squareStyles[lastMove[1]], backgroundColor: HIGHLIGHT.lastMove };
  }

  const completedUserMoves = Math.min(userMovesFirst ? Math.ceil(moveIndex / 2) : Math.floor(moveIndex / 2), totalUserMoves);

  const navReserved = reserveNavRow && onPrev == null && onNext == null;
  const isPendingRetry = attemptStatus === "pending_retry" && puzzleState !== "playing";
  const isDowngradedSolve = (puzzleState === "solved" || puzzleState === "reviewing") && serverDowngraded;
  const tone: "playing" | "wrong" | "solved" =
    isPendingRetry || isDowngradedSolve ? "wrong" : puzzleState === "reviewing" ? (review?.verdict === "right" ? "solved" : review?.verdict === "wrong" ? "wrong" : "playing") : puzzleState;
  const message = isPendingRetry ? "Save failed — will retry automatically" : isDowngradedSolve ? "Server recorded this as a wrong attempt" : statusMessage;
  const toneClass =
    tone === "solved"
      ? "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300"
      : tone === "wrong"
        ? "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-900/30 dark:text-rose-300"
        : "border-zinc-200 bg-zinc-50 text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300";

  return (
    <div className="flex w-full flex-col items-center gap-2">
      <div role="status" className={`w-full rounded border px-3 py-1.5 text-center text-sm ${toneClass}`}>
        {message}
      </div>

      {(onPrev != null || onNext != null || navReserved) && (
        <div data-testid="puzzle-nav" aria-hidden={navReserved || undefined} className={`flex w-full items-center justify-between gap-2${navReserved ? " invisible" : ""}`}>
          <button type="button" onClick={onPrev ?? undefined} disabled={prevDisabled || onPrev == null} className={btn}>
            ← Previous
          </button>
          <button
            type="button"
            onClick={onNext ?? undefined}
            disabled={nextDisabled || onNext == null || nextHidden}
            aria-hidden={nextHidden || undefined}
            className={`${showNextButton && nextHighlighted ? btnAccent : btn}${nextHidden ? " invisible" : ""}`}
          >
            {nextLabel}
          </button>
        </div>
      )}

      {/* One row for the move dots or the review's progress, at the dots' height whatever it holds (the
          text overflows into the gaps around it), so the board never moves between them. */}
      {review ? (
        <div className="flex h-2 items-center gap-1.5 text-xs leading-none text-zinc-600 dark:text-zinc-400" data-testid="review-progress">
          <span>
            Review: {review.streaks.filter((v) => v >= REVIEW_STREAK).length} of {review.boards.length} {review.boards.length === 1 ? "board" : "boards"} fixed
          </span>
          <span className="ml-1 flex items-center gap-1" aria-label={`${review.streaks[review.at]} of ${REVIEW_STREAK} in a row`}>
            {Array.from({ length: REVIEW_STREAK }).map((_, i) => (
              <span key={i} className={`h-2 w-2 rounded-full ${i < review.streaks[review.at] ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-700"}`} />
            ))}
          </span>
        </div>
      ) : (
      <div className="flex h-2 items-center gap-1.5 leading-none" aria-label={`${completedUserMoves} of ${totalUserMoves} moves`}>
        {Array.from({ length: totalUserMoves }).map((_, i) => (
          <span
            key={i}
            className={`h-2 w-2 rounded-full ${
              i < completedUserMoves ? "bg-emerald-500" : i === completedUserMoves && puzzleState === "wrong" ? "bg-rose-500" : "bg-zinc-300 dark:bg-zinc-700"
            }`}
          />
        ))}
        {finishLineMode && <span className="ml-1 text-xs text-sky-600 dark:text-sky-400">· finish line</span>}
      </div>
      )}

      <div className="w-full max-w-[560px]">
        <Chessboard
          options={{
            id: "puzzle-engine-board",
            position: game.fen(),
            onPieceDrop: ({ sourceSquare, targetSquare }) => (targetSquare ? handleDrop(sourceSquare as Square, targetSquare as Square) : false),
            onSquareClick: ({ square }) => handleSquareClick(square as Square),
            boardOrientation,
            squareStyles,
            boardStyle: { borderRadius: "4px" },
            ...SQUARES,
            animationDurationInMs: 200,
            allowDragging: acceptingMoves,
          }}
        />
      </div>

      {/* Always rendered at a fixed height so the board never shifts between states. */}
      <div className="flex min-h-[40px] w-full flex-wrap items-center justify-center gap-3">
        {promotionOpen && <PromotionChooser onChoose={choosePromotion} />}
        {puzzleState === "wrong" && (
          <>
            <button type="button" onClick={handleRetry} disabled={submissionLocked} className={btnAccent}>
              Try Again
            </button>
            <button type="button" onClick={handleShowMe} disabled={solutionShown} className="text-xs text-zinc-500 hover:text-zinc-800 disabled:opacity-40 dark:hover:text-zinc-200">
              {solutionShown ? "✓ Shown" : "Show me"}
            </button>
          </>
        )}
        {puzzleState === "reviewing" && review?.verdict === "wrong" && (
          <>
            <button type="button" onClick={advanceReview} className={btnAccent}>
              Continue
            </button>
            <button type="button" onClick={handleShowMe} disabled={solutionShown} className="text-xs text-zinc-500 hover:text-zinc-800 disabled:opacity-40 dark:hover:text-zinc-200">
              {solutionShown ? "✓ Shown" : "Show me"}
            </button>
          </>
        )}
        {puzzleState === "solved" && (
          <>
            {canFinishLine && (
              <button type="button" onClick={handleFinishLine} disabled={submissionLocked} className={btnAccent}>
                Play On →
              </button>
            )}
            <button type="button" onClick={handleReplay} disabled={submissionLocked} className={btn}>
              Replay
            </button>
          </>
        )}
      </div>
      {/* The launchers are always rendered, invisible without a target, so nothing below the board shifts. */}
      <div className="flex w-full flex-col gap-2 sm:flex-row">
        <button type="button" data-testid="branch-compare-launch" onClick={() => compareTarget && setBranchCompareOpen(true)} disabled={!compareTarget} className={`${launcher} ${compareTarget ? launcherOn : launcherOff}`}>
          What if {compareTarget?.preFen.split(" ")[1] === "w" ? "White" : "Black"} had played differently?
        </button>
        <button type="button" data-testid="similar-launch" onClick={() => similarAt && setSimilarOpen(true)} disabled={!similarAt} className={`${launcher} ${similarAt ? launcherOn : launcherOff}`}>
          Similar positions in your repertoire
        </button>
        <button type="button" data-testid="explore-launch" onClick={() => canExplore && setExploreSeed(game.fen())} disabled={!canExplore} title={canExplore ? undefined : "This position cannot be explored"} className={`${launcher} ${launcherOn}`}>
          Explore from here
        </button>
      </div>
      {/* The note on the position the board is showing, and the whole line for a repertoire puzzle. */}
      <div className="mt-3 w-full">
        <LineReaderPanel fen={game.fen()} repertoireLineId={repertoireLineId} />
      </div>
      {branchCompareOpen && compareTarget && <BranchCompareView fen={compareTarget.fen} preFen={compareTarget.preFen} orientation={boardOrientation} onClose={() => setBranchCompareOpen(false)} />}
      {similarOpen && similarAt && <SolverSimilarModal fen={similarAt.fen} move={similarAt.move} search={similarSearch} orientation={boardOrientation} onClose={() => setSimilarOpen(false)} />}
      {exploreSeed && <ExploreLayer fen={exploreSeed} orientation={boardOrientation} onClose={closeExplore} />}
    </div>
  );
}
