import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";
import type { Square } from "chess.js";
import { Chessboard } from "react-chessboard";
import { uciPvToSan } from "../engine/eval";
import type { StockfishApi } from "../engine/useStockfish";
import { HIGHLIGHT, SQUARES } from "../utils/board";
import { legalMove, numberedLine } from "../utils/chess";

export interface MoveBuilderState {
  solutionLine: string[];
  isValid: boolean;
  /** Whose move it is at the end of the line. */
  endTurn: "w" | "b";
}

/** The server's limit on a custom puzzle's line (core/puzzles/custom.py MAX_PLIES); a parent
 *  building something else passes its own `maxPlies`. */
export const MAX_PLIES = 30;

/** `moves` played from `fen`, or as many of them as play. */
function replay(fen: string, moves: string[]): { game: Chess; played: string[] } {
  const game = new Chess(fen);
  const played: string[] = [];
  for (const m of moves) {
    let mv;
    try {
      mv = legalMove(game, m);
    } catch {
      mv = null;
    }
    if (!mv) break;
    played.push(mv.san);
  }
  return { game, played };
}

/** Up to `n` plies of `line` played on `fen`, stopping at the first that does not play. */
function playable(fen: string, line: string[], n: number): string[] {
  return replay(fen, line.slice(0, n)).played;
}

/**
 * A board that records the moves made on it: the solution line of a hand-made puzzle. Both sides
 * are moved by hand, only legal moves are accepted, and promotion is to a queen (as in the solver).
 * The parent is told the line after every change.
 *
 * `moves` is the whole line, what is saved; `shown` is a cursor into it — the ply on the board —
 * so a seeded line (`initialMoves`) opens at its starting board and is stepped through with ◀ ▶
 * before it is approved. A move made at an earlier position replaces the suffix. Undo and Clear
 * act on the line and clamp the cursor; the cursor never reaches the server.
 *
 * With `extend`, **Extend** appends the next moves without the player supplying them, always
 * bringing the line to end on the player's (`color`) move: from a line that ends on the opponent's
 * move it adds the player's move alone, one ply; from a line that ends on the player's it adds the
 * opponent's reply and the player's next, two plies. A continuation that cannot reach the player's
 * move (a terminal board, an engine with one ply to give and it the opponent's) adds nothing and
 * says so. The plies come from the rest of a stored engine line while `moves` is still its prefix,
 * else from one search of the browser engine at the end of the line. A search is owned by the line
 * revision it was asked for: any change to the line drops it and stops the engine, and a finished
 * search is applied once, only to its own request (the revision, the end board and a fresh
 * dispatch all match).
 */
export function MoveBuilder({
  fen,
  color,
  onChange,
  initialMoves,
  initialShown = 0,
  extend,
  disabled = false,
  maxPlies = MAX_PLIES,
}: {
  fen: string;
  color: "w" | "b";
  onChange: (state: MoveBuilderState) => void;
  initialMoves?: string[];
  /** The cursor's starting ply into `initialMoves` (a seed that opens on a later board). */
  initialShown?: number;
  extend?: { line: string[]; engine: StockfishApi; depth?: number };
  /** While the line is being saved: every control is off and a pending search is dropped. */
  disabled?: boolean;
  maxPlies?: number;
}) {
  const [moves, setMoves] = useState<string[]>(() => (initialMoves?.length ? playable(fen, initialMoves, maxPlies) : []));
  const [shown, setShown] = useState(() => Math.max(0, Math.min(initialShown, initialMoves?.length ? playable(fen, initialMoves, maxPlies).length : 0)));
  const [extendNote, setExtendNote] = useState<string | null>(null);
  const [selected, setSelected] = useState<Square | null>(null);
  const [lastMove, setLastMove] = useState<[string, string] | null>(null);
  // The line's revision: every change to `moves` bumps it and invalidates a pending search.
  const revisionRef = useRef(0);
  const pendingRef = useRef<{ revision: number; endFen: string; armed: boolean } | null>(null);
  const [searching, setSearching] = useState(false);

  const game = useMemo(() => replay(fen, moves.slice(0, shown)).game, [fen, moves, shown]);
  const end = useMemo(() => replay(fen, moves).game, [fen, moves]);

  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  useEffect(() => {
    onChangeRef.current({ solutionLine: moves, isValid: moves.length >= 1, endTurn: end.turn() });
  }, [moves, end]);

  // The hook hands out a new object every render; a ref keeps `cancelPending` stable so the
  // unmount cleanup below runs once, at unmount, and never on a re-render.
  const engine = extend?.engine;
  const engineRef = useRef(engine);
  useEffect(() => {
    engineRef.current = engine;
  }, [engine]);
  const cancelPending = useCallback(() => {
    if (pendingRef.current) {
      pendingRef.current = null;
      setSearching(false);
      engineRef.current?.stop();
    }
  }, []);

  /** Every change to the line goes through here: the revision moves on and a search is dropped. */
  const setLine = useCallback(
    (next: string[], cursor: number) => {
      revisionRef.current += 1;
      cancelPending();
      setExtendNote(null);
      setMoves(next);
      setShown(Math.max(0, Math.min(cursor, next.length)));
    },
    [cancelPending],
  );

  function tryMove(from: string, to: string): boolean {
    if (disabled) return false;
    let san: string;
    try {
      const mv = legalMove(new Chess(game.fen()), { from, to, promotion: "q" });
      if (!mv) return false;
      san = mv.san;
    } catch {
      return false;
    }
    if (shown >= maxPlies) return false;
    const next = [...moves.slice(0, shown), san];
    setLine(next, next.length);
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
    setLine(next, Math.min(shown, next.length));
    setLastMove(null);
    setSelected(null);
  }

  function step(delta: number) {
    setShown((s) => Math.max(0, Math.min(moves.length, s + delta)));
    setLastMove(null);
    setSelected(null);
  }

  // The extend control. How many plies it wants is whose turn it is at the end of the line: the
  // player's move alone when it is theirs, the opponent's reply and then the player's when not.
  // The stored line is reused while `moves` is its prefix with moves to spare; otherwise one
  // search at the end board, owned by this revision and this board.
  const terminal = end.isGameOver();
  const want = end.turn() === color ? 1 : 2;
  const room = maxPlies - moves.length;
  const atCap = room < want;
  const stored = extend?.line ?? [];
  const storedRest = stored.length > moves.length && moves.every((m, i) => m === stored[i]) ? stored.slice(moves.length) : [];
  const canExtend = !!extend && !terminal && !atCap && !searching && !disabled;
  const NO_MORE = "The engine has no further move here.";

  /** Appends exactly the plies wanted, when `plies` supplies them from the end board; false otherwise. */
  function appendPlies(plies: string[]): boolean {
    const got = playable(end.fen(), plies, want);
    if (got.length < want) return false;
    setLine([...moves, ...got], moves.length + got.length);
    setLastMove(null);
    setSelected(null);
    return true;
  }

  function extendLine() {
    if (!extend || !canExtend) return;
    // The stored line is used while it has the plies to give; a token that does not play (a seed
    // that stopped early) hands over to the engine instead of leaving the button inert.
    if (storedRest.length >= want && appendPlies(storedRest)) return;
    const endFen = end.fen();
    pendingRef.current = { revision: revisionRef.current, endFen, armed: false };
    setSearching(true);
    extend.engine.reset(); // an old finished search for the same board must not be mistaken for this one
    extend.engine.analyze(endFen, extend.depth);
  }

  // A finished search is applied once, only while it is still the pending request: the revision
  // unchanged, the evaluated board the one asked for, and a fresh dispatch seen (`thinking` went
  // true for this board after the click) so an old evaluation the hook still holds is ignored.
  const ev = engine?.evalState ?? null;
  useEffect(() => {
    const pending = pendingRef.current;
    if (!pending || !ev || ev.fen !== pending.endFen) return;
    if (ev.thinking) {
      pending.armed = true;
      return;
    }
    if (!pending.armed || pending.revision !== revisionRef.current) return;
    pendingRef.current = null;
    setSearching(false);
    const pv = ev.pvUci.length ? ev.pvUci : ev.bestMoveUci ? [ev.bestMoveUci] : [];
    const san = uciPvToSan(pending.endFen, pv, want);
    const got = san ? san.split(" ") : [];
    if (got.length < want) {
      setExtendNote(NO_MORE);
      return;
    }
    revisionRef.current += 1;
    setMoves([...moves, ...got]);
    setShown(moves.length + got.length);
    setLastMove(null);
    setSelected(null);
  }, [ev, moves, want]);

  // Saving drops a pending request (the line sent is the line at the click); so does unmounting.
  useEffect(() => {
    if (disabled) cancelPending();
  }, [disabled, cancelPending]);
  useEffect(() => () => cancelPending(), [cancelPending]);

  // ← → step the cursor, as the walk-through does; a text field keeps its own arrows.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

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
      <div data-testid="solution-line" data-shown={shown} className="max-h-[120px] min-h-[64px] w-full overflow-y-auto rounded border border-zinc-200 px-3 py-2 font-mono text-sm dark:border-zinc-800">
        {tokens.length === 0 ? (
          <p className="text-center text-xs italic leading-[48px] text-zinc-500">Click or drag pieces to build the solution line</p>
        ) : (
          <p className="break-words">
            {tokens.map((t, i) => (
              <span key={i} className={i < shown ? "" : "text-zinc-400 dark:text-zinc-500"}>
                {i > 0 ? " " : ""}
                {t}
              </span>
            ))}
          </p>
        )}
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        <button type="button" aria-label="Previous move" disabled={shown === 0} onClick={() => step(-1)} className={tool}>
          ◀
        </button>
        <button type="button" aria-label="Next move" disabled={shown >= moves.length} onClick={() => step(1)} className={tool}>
          ▶
        </button>
        <button type="button" disabled={moves.length === 0 || disabled} onClick={() => rewind(moves.slice(0, -1))} className={tool}>
          ↶ Undo
        </button>
        <button type="button" disabled={moves.length === 0 || disabled} onClick={() => rewind([])} className={tool}>
          ✕ Clear
        </button>
        {extend && (
          <button type="button" disabled={!canExtend} onClick={extendLine} className={tool} title={atCap ? `A line holds at most ${maxPlies} plies` : terminal ? "The game is over at the end of the line" : want === 1 ? "Add your move from the engine" : "Add the reply and your next move from the engine"}>
            {searching ? "searching…" : want === 1 ? "Extend +1" : "Extend +2"}
          </button>
        )}
      </div>
      {extendNote && (
        <p role="status" className="text-xs text-zinc-500">
          {extendNote}
        </p>
      )}
    </div>
  );
}
