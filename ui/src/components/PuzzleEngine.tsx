/**
 * PuzzleEngine: the interactive solver. Given a FEN and a solution line (SAN), the player plays
 * their moves on the board and the opponent's replies auto-play. A wrong move shows feedback
 * and can be retried from the position it was played in; a solve can be replayed from the start.
 *
 * Map mode: an own_mate puzzle carries an acceptance map, and then any map-optimal move is
 * accepted at each node (not just the stored line) and the opponent replies with the map's
 * canonical defence. Solve = mate delivered.
 *
 * Promotion is auto-queen. Underpromotion cannot be entered; a known limitation.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";
import type { Move, Square } from "chess.js";
import { Chessboard } from "react-chessboard";
import { BranchCompareView } from "./BranchCompareView";
import { ExploreLayer } from "./ExploreLayer";
import { LineReaderPanel } from "./PositionCard/LineReaderPanel";
import { SolverSimilarModal } from "./PositionCard/SolverSimilarModal";
import { useSimilarPositions } from "../hooks/useSimilarPositions";
import { HIGHLIGHT, SQUARES } from "../utils/board";
import { branchCompareTarget, mapKey, moveUci, parsesAsFen, sanResolvesToMove, similarTarget, uciToMove } from "../utils/chess";
import type { AcceptanceMap } from "../practice";

type PuzzleState = "playing" | "wrong" | "solved";

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

  const boardOrientation = color === "w" ? "white" : "black";
  const activeFen = finishLineMode ? finishLineFen : fen;
  const fenActiveColor = activeFen.split(" ")[1] as "w" | "b";
  const userMovesFirst = fenActiveColor === color;
  const isUserTurn = userMovesFirst ? moveIndex % 2 === 0 : moveIndex % 2 === 1;

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
    setStatusMessage("Your turn — find the best move");
    setSolutionShown(false);
    completeCalled.current = false;
    checkpointFen.current = startFen;
    checkpointMoveIndex.current = startIndex;
    checkpointLastMove.current = null;
  }, []);

  // Reset when the puzzle changes.
  useEffect(() => {
    setFinishLineMode(false);
    setFinishLineFen(fen);
    setFinishLineRemainingMoves([]);
    resetToFen(fen, 0);
  }, [fen, solutionLine, color, resetToFen]);

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
            setPuzzleState("solved");
            setStatusMessage(finishLineMode ? "Line complete!" : "Puzzle complete!");
            setRightSquares({ [result.to]: { backgroundColor: HIGHLIGHT.correct } });
            if (!completeCalled.current && !finishLineMode) {
              completeCalled.current = true;
              onComplete?.(true, movesPlayed);
            }
          }
        }
      } catch {
        console.error("Invalid opponent move in solution:", opponentMove);
      }
    }, 400);

    return () => clearTimeout(timer);
  }, [moveIndex, puzzleState, isUserTurn, game, activeSolutionLine, movesPlayed, onComplete, finishLineMode, mapMode, acceptanceMap]);

  const handleDrop = useCallback(
    (sourceSquare: Square, targetSquare: Square): boolean => {
      if (puzzleState !== "playing" || submissionLocked) return false;
      if (!isUserTurn) return false;
      if (moveIndex >= activeSolutionLine.length) return false;

      const expectedMove = activeSolutionLine[moveIndex];
      const tempGame = new Chess(game.fen());
      let result: Move;
      try {
        result = tempGame.move({ from: sourceSquare, to: targetSquare, promotion: "q" });
      } catch {
        return false;
      }

      const acceptCorrect = (finalMessage: string | null) => {
        setGame(tempGame);
        setLastMove([sourceSquare, targetSquare]);
        setMovesPlayed((prev) => [...prev, result.san]);
        setMoveIndex((prev) => prev + 1);
        setWrongSquares({});
        setSelectedSquare(null);
        setRightSquares({ [targetSquare]: { backgroundColor: HIGHLIGHT.correct } });
        if (finalMessage !== null) {
          setPuzzleState("solved");
          setStatusMessage(finalMessage);
          if (!completeCalled.current && !finishLineMode) {
            completeCalled.current = true;
            onComplete?.(true, [...movesPlayed, result.san]);
          }
        } else {
          setStatusMessage("Correct!");
        }
      };
      const rejectWrong = (message: string) => {
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
    [game, moveIndex, activeSolutionLine, puzzleState, isUserTurn, movesPlayed, onComplete, finishLineMode, lastMove, mateInfo, mapMode, acceptanceMap, submissionLocked],
  );

  // Click-to-move: first click selects, second attempts the move with the same validation as a drop.
  const handleSquareClick = useCallback(
    (square: Square) => {
      if (puzzleState !== "playing" || !isUserTurn || submissionLocked) return;
      const piece = game.get(square);
      const own = piece && piece.color === color;
      if (selectedSquare) {
        if (!handleDrop(selectedSquare, square)) setSelectedSquare(own ? square : null);
      } else if (own) {
        setSelectedSquare(square);
      }
    },
    [selectedSquare, puzzleState, isUserTurn, game, color, handleDrop, submissionLocked],
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
    if (puzzleState !== "wrong" || solutionShown) return;
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
  }, [puzzleState, solutionShown, game, moveIndex, activeSolutionLine, mapMode, acceptanceMap]);

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
      console.error("Failed to compute finish-line start FEN:", e);
    }
  }, [fen, solutionLine, solutionLineProp, resetToFen]);

  const squareStyles: Record<string, React.CSSProperties> = { ...wrongSquares, ...rightSquares };
  if (selectedSquare && puzzleState === "playing") {
    squareStyles[selectedSquare] = { ...squareStyles[selectedSquare], backgroundColor: HIGHLIGHT.selected };
    for (const m of game.moves({ square: selectedSquare, verbose: true })) {
      squareStyles[m.to] = { ...squareStyles[m.to], background: game.get(m.to as Square) ? HIGHLIGHT.legalRing : HIGHLIGHT.legalDot };
    }
  }
  if (lastMove) {
    squareStyles[lastMove[0]] = { ...squareStyles[lastMove[0]], backgroundColor: HIGHLIGHT.lastMove };
    squareStyles[lastMove[1]] = { ...squareStyles[lastMove[1]], backgroundColor: HIGHLIGHT.lastMove };
  }

  const totalUserMoves = userMovesFirst ? Math.ceil(activeSolutionLine.length / 2) : Math.floor(activeSolutionLine.length / 2);
  const completedUserMoves = Math.min(userMovesFirst ? Math.ceil(moveIndex / 2) : Math.floor(moveIndex / 2), totalUserMoves);

  const isPendingRetry = attemptStatus === "pending_retry" && puzzleState !== "playing";
  const isDowngradedSolve = puzzleState === "solved" && serverDowngraded;
  const tone: PuzzleState = isPendingRetry || isDowngradedSolve ? "wrong" : puzzleState;
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

      {(onPrev != null || onNext != null) && (
        <div className="flex w-full items-center justify-between gap-2">
          <button type="button" onClick={onPrev ?? undefined} disabled={prevDisabled || onPrev == null} className={btn}>
            ← Previous
          </button>
          <button
            type="button"
            onClick={onNext ?? undefined}
            disabled={nextDisabled || onNext == null}
            className={showNextButton && nextHighlighted ? btnAccent : btn}
          >
            {nextLabel}
          </button>
        </div>
      )}

      <div className="flex items-center gap-1.5" aria-label={`${completedUserMoves} of ${totalUserMoves} moves`}>
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
            allowDragging: puzzleState === "playing" && isUserTurn && !submissionLocked,
          }}
        />
      </div>

      {/* Always rendered at a fixed height so the board never shifts between states. */}
      <div className="flex min-h-[40px] w-full flex-wrap items-center justify-center gap-3">
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
