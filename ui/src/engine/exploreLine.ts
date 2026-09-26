/**
 * The explored line: a chess.js instance seeded at one position, moved on by the player, undone
 * and redone, and analysed by the engine after every change. Standard chess only; the host has
 * already refused a FEN chess.js cannot parse.
 *
 * Every transition ends in one `settle()`: it publishes the board from the live instance and then
 * either asks the engine for the position (ongoing) or resets it (terminal — checkmate, stalemate,
 * or a draw by fifty moves, insufficient material or threefold repetition, counted over the
 * instance's own history so a repetition made inside Explore is seen). A terminal board therefore
 * never has a search alive behind it, and `reanalyse()` is the only way a host asks for a search:
 * on a finished board it is a reset, so a depth change or a late settings response keeps quiet.
 */
import { useCallback, useRef, useState } from "react";
import { Chess } from "chess.js";
import type { Move, Square } from "chess.js";

/** The engine surface this drives (a structural subset of `StockfishApi`). */
export interface ExploreEngine {
  analyze: (fen: string, depth?: number) => void;
  reset: () => void;
}

export type Terminal = null | "checkmate" | "stalemate" | "draw";

export interface ExploreLine {
  fen: string;
  terminal: Terminal;
  selected: Square | null;
  last: [Square, Square] | null;
  san: string[];
  canBack: boolean;
  canForward: boolean;
  /** The selected piece's legal destinations, for the highlight dots. */
  targets: { to: Square; capture: boolean }[];
  move: (from: Square, to: Square) => boolean;
  squareClick: (square: Square) => void;
  back: () => void;
  forward: () => void;
  reset: () => void;
  reanalyse: (depth?: number) => void;
}

function terminalOf(c: Chess): Terminal {
  if (c.isCheckmate()) return "checkmate";
  if (c.isStalemate()) return "stalemate";
  if (c.isDraw()) return "draw";
  return null;
}

function lastOf(c: Chess): [Square, Square] | null {
  const h = c.history({ verbose: true });
  const m = h.length ? h[h.length - 1] : null;
  return m ? [m.from as Square, m.to as Square] : null;
}

export function useExploreLine({ engine, seed }: { engine: ExploreEngine; seed: string }): ExploreLine {
  const ref = useRef<Chess>(new Chess(seed));
  const [fen, setFen] = useState(() => new Chess(seed).fen());
  const [terminal, setTerminal] = useState<Terminal>(() => terminalOf(new Chess(seed)));
  const [selected, setSelected] = useState<Square | null>(null);
  const [targets, setTargets] = useState<{ to: Square; capture: boolean }[]>([]);
  const [last, setLast] = useState<[Square, Square] | null>(null);
  const [san, setSan] = useState<string[]>([]);
  const [redo, setRedo] = useState<Move[]>([]);
  const { analyze, reset: resetEngine } = engine;

  const settle = useCallback(
    (depth?: number) => {
      const c = ref.current;
      const f = c.fen();
      const t = terminalOf(c);
      setFen(f);
      setLast(lastOf(c));
      setSan(c.history());
      setTerminal(t);
      setSelected(null);
      setTargets([]);
      if (t === null) analyze(f, depth);
      else resetEngine();
    },
    [analyze, resetEngine],
  );

  // The seeding path, and the only one: the host calls it from a plain effect keyed on `reset`
  // (which changes only with `seed`), so it runs once in production and re-runs under StrictMode
  // after the worker effect has torn down and recreated the engine.
  const reset = useCallback(() => {
    ref.current = new Chess(seed);
    setRedo([]);
    settle();
  }, [seed, settle]);

  const move = useCallback(
    (from: Square, to: Square): boolean => {
      const c = ref.current;
      let res: Move | null;
      try {
        res = c.move({ from, to, promotion: "q" });
      } catch {
        return false;
      }
      if (!res) return false;
      setRedo([]); // a fresh move forks the line
      settle();
      return true;
    },
    [settle],
  );

  const squareClick = useCallback(
    (square: Square) => {
      const c = ref.current;
      const own = (sq: Square) => {
        const p = c.get(sq);
        return !!p && p.color === c.turn();
      };
      const select = (sq: Square | null) => {
        setSelected(sq);
        setTargets(sq ? c.moves({ square: sq, verbose: true }).map((m) => ({ to: m.to as Square, capture: !!c.get(m.to as Square) })) : []);
      };
      if (selected) {
        if (!move(selected, square)) select(own(square) ? square : null);
      } else if (own(square)) {
        select(square);
      }
    },
    [selected, move],
  );

  const back = useCallback(() => {
    const undone = ref.current.undo();
    if (!undone) return;
    setRedo((prev) => [...prev, undone]);
    settle();
  }, [settle]);

  const forward = useCallback(() => {
    if (redo.length === 0) return;
    const mv = redo[redo.length - 1];
    let res: Move | null;
    try {
      res = ref.current.move({ from: mv.from, to: mv.to, promotion: mv.promotion });
    } catch {
      return;
    }
    if (!res) return;
    setRedo((prev) => prev.slice(0, -1));
    settle();
  }, [redo, settle]);

  const reanalyse = useCallback((depth?: number) => settle(depth), [settle]);

  return { fen, terminal, selected, last, san, canBack: san.length > 0, canForward: redo.length > 0, targets, move, squareClick, back, forward, reset, reanalyse };
}
