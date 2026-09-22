import { useEffect, useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";
import type { Square } from "chess.js";
import { Chessboard } from "react-chessboard";
import { HIGHLIGHT, SQUARES } from "../utils/board";
import { numberedLine } from "../utils/chess";

export interface MoveBuilderState {
  solutionLine: string[];
  isValid: boolean;
}

/**
 * A board that records the moves made on it: the solution line of a hand-made puzzle. Both
 * sides are moved by hand, only legal moves are accepted, and promotion is to a queen (as in
 * the solver). The parent is told the line after every change.
 */
export function MoveBuilder({ fen, color, onChange }: { fen: string; color: "w" | "b"; onChange: (state: MoveBuilderState) => void }) {
  const [moves, setMoves] = useState<string[]>([]);
  const [selected, setSelected] = useState<Square | null>(null);
  const [lastMove, setLastMove] = useState<[string, string] | null>(null);

  const game = useMemo(() => {
    const g = new Chess(fen);
    for (const m of moves) g.move(m);
    return g;
  }, [fen, moves]);

  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  useEffect(() => {
    onChangeRef.current({ solutionLine: moves, isValid: moves.length >= 1 });
  }, [moves]);

  function tryMove(from: string, to: string): boolean {
    let san: string;
    try {
      san = new Chess(game.fen()).move({ from, to, promotion: "q" }).san;
    } catch {
      return false;
    }
    setMoves((prev) => [...prev, san]);
    setLastMove([from, to]);
    setSelected(null);
    return true;
  }

  function onSquareClick(square: Square) {
    const own = game.get(square)?.color === game.turn();
    if (selected && square !== selected && tryMove(selected, square)) return;
    setSelected(own && square !== selected ? square : null);
  }

  function rewind(next: string[]) {
    setMoves(next);
    setLastMove(null);
    setSelected(null);
  }

  const squareStyles: Record<string, React.CSSProperties> = {};
  if (lastMove) for (const sq of lastMove) squareStyles[sq] = { backgroundColor: HIGHLIGHT.lastMove };
  if (selected) {
    squareStyles[selected] = { backgroundColor: HIGHLIGHT.selected };
    for (const m of game.moves({ square: selected, verbose: true })) {
      squareStyles[m.to] = { ...squareStyles[m.to], background: game.get(m.to as Square) ? HIGHLIGHT.legalRing : HIGHLIGHT.legalDot };
    }
  }
  const tokens = numberedLine(fen, moves);
  const tool = "rounded border border-zinc-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700";

  return (
    <div className="flex w-full flex-col items-center gap-3">
      <span className="inline-flex min-w-[140px] justify-center rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">{game.turn() === "w" ? "White to move" : "Black to move"}</span>
      <div className="aspect-square w-full max-w-[480px]">
        <Chessboard
          options={{
            id: "move-builder-board",
            position: game.fen(),
            onPieceDrop: ({ sourceSquare, targetSquare }) => (targetSquare ? tryMove(sourceSquare, targetSquare) : false),
            onSquareClick: ({ square }) => onSquareClick(square as Square),
            boardOrientation: color === "w" ? "white" : "black",
            squareStyles,
            boardStyle: { borderRadius: "4px" },
            ...SQUARES,
            animationDurationInMs: 200,
            allowDragging: true,
          }}
        />
      </div>
      <div data-testid="solution-line" className="max-h-[120px] min-h-[64px] w-full overflow-y-auto rounded border border-zinc-200 px-3 py-2 font-mono text-sm dark:border-zinc-800">
        {tokens.length === 0 ? <p className="text-center text-xs italic leading-[48px] text-zinc-500">Click or drag pieces to build the solution line</p> : <p className="break-words">{tokens.join(" ")}</p>}
      </div>
      <div className="flex gap-2">
        <button type="button" disabled={moves.length === 0} onClick={() => rewind(moves.slice(0, -1))} className={tool}>
          ↶ Undo
        </button>
        <button type="button" disabled={moves.length === 0} onClick={() => rewind([])} className={tool}>
          ✕ Clear
        </button>
      </div>
    </div>
  );
}
