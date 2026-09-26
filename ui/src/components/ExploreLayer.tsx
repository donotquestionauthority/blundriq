/**
 * Explore: a full-screen layer over a position card or the solver, with an in-browser Stockfish
 * (one worker per open layer, created on mount and terminated on close) evaluating whatever line
 * the player plays from the seed. Not a page — no route, no history entry; the host mounts it with
 * a FEN and an orientation and unmounts it to close. Escape closes; ← / → undo and redo the line
 * (a key with a modifier held is left to the browser — Cmd+← is Back on a Mac).
 *
 * The layer never asks the engine for a search itself: every search goes through the explored
 * line's `reanalyse()`, so a depth change or the settings response on a finished board stays
 * quiet. Exactly one search is asked for at mount: the settings response re-analyses only when
 * the fetched depth differs from the one in use.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { Square } from "chess.js";
import { Chessboard } from "react-chessboard";
import { api } from "../api";
import { fmtCp, uciPvToSan } from "../engine/eval";
import { useExploreLine } from "../engine/exploreLine";
import { useStockfish } from "../engine/useStockfish";
import { ARROWS, HIGHLIGHT, SQUARES } from "../utils/board";
import type { BoardArrow } from "../utils/chess";
import { EvalBar } from "./EvalBar";

const DEFAULT_DEPTH = 16;
/** The selector's steps; the settings row's value joins them when it is not one. */
const DEPTH_STEPS = [12, 16, 18, 20, 24];
const btn = "rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900 disabled:opacity-40 disabled:pointer-events-none";
const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm tabular-nums dark:border-zinc-700 dark:bg-zinc-900";

export function ExploreLayer({ fen, orientation, onClose }: { fen: string; orientation: "white" | "black"; onClose: () => void }) {
  const boardId = "explore" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const [depth, setDepth] = useState(DEFAULT_DEPTH);
  const engine = useStockfish({ enabled: true, depth });
  const line = useExploreLine({ engine, seed: fen });
  const { reset, reanalyse } = line;

  // The seed. A plain effect, keyed on `reset` (which changes only with the seed): under
  // StrictMode it re-runs after the worker effect has torn down and recreated the engine, which a
  // ran-once guard would leave with nothing to analyse.
  useEffect(() => reset(), [reset]);

  // The saved default, read once. It re-analyses only when it differs from the depth in use.
  const depthInUse = useRef(DEFAULT_DEPTH);
  useEffect(() => {
    let alive = true;
    api
      .get<{ explore_engine_depth?: unknown }>("/settings")
      .then((s) => {
        const v = s.explore_engine_depth;
        if (alive && typeof v === "number" && Number.isFinite(v)) setDepth(v);
      })
      .catch(() => {
        /* the hook's own default stands */
      });
    return () => {
      alive = false;
    };
  }, []);

  // A depth change — from the selector or the settings response — re-searches the current board
  // at the new depth, through the line (a finished board stays quiet).
  useEffect(() => {
    if (depthInUse.current === depth) return;
    depthInUse.current = depth;
    reanalyse(depth);
  }, [depth, reanalyse]);

  const { back, forward, move } = line;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.altKey || e.ctrlKey) return;
      if (e.target instanceof HTMLSelectElement) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "ArrowLeft") {
        e.stopPropagation();
        back();
      } else if (e.key === "ArrowRight") {
        e.stopPropagation();
        forward();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, back, forward]);

  const onDrop = useCallback(({ sourceSquare, targetSquare }: { sourceSquare: string; targetSquare: string | null }) => (targetSquare ? move(sourceSquare as Square, targetSquare as Square) : false), [move]);

  const squareStyles: Record<string, React.CSSProperties> = {};
  if (line.selected) {
    squareStyles[line.selected] = { backgroundColor: HIGHLIGHT.selected };
    for (const t of line.targets) squareStyles[t.to] = { ...squareStyles[t.to], background: t.capture ? HIGHLIGHT.legalRing : HIGHLIGHT.legalDot };
  }
  if (line.last) {
    squareStyles[line.last[0]] = { ...squareStyles[line.last[0]], backgroundColor: HIGHLIGHT.lastMove };
    squareStyles[line.last[1]] = { ...squareStyles[line.last[1]], backgroundColor: HIGHLIGHT.lastMove };
  }

  // Only a search for the board on show may draw on it: the state is the latest dispatched
  // search's, and a debounce window is long enough for the board to have moved on.
  const ev = engine.evalState && engine.evalState.fen === line.fen && line.terminal === null ? engine.evalState : null;
  const bm = ev?.bestMoveUci;
  const arrows: BoardArrow[] = bm ? [{ startSquare: bm.slice(0, 2), endSquare: bm.slice(2, 4), color: ARROWS.engine }] : [];
  const pvSan = ev && ev.pvUci.length ? uciPvToSan(line.fen, ev.pvUci) : "";
  const bestSan = pvSan ? pvSan.split(" ")[0] : bm ?? null;
  const steps = DEPTH_STEPS.includes(depth) ? DEPTH_STEPS : [...DEPTH_STEPS, depth].sort((a, b) => a - b);
  const n = line.san.length;
  const finished = line.terminal === "checkmate" ? "Checkmate" : line.terminal === "stalemate" ? "Stalemate" : line.terminal === "draw" ? "Draw" : null;

  return (
    <div role="dialog" aria-label="Explore" className="fixed inset-0 z-[60] flex flex-col overflow-y-auto overscroll-contain bg-zinc-50 dark:bg-zinc-950" data-testid="explore-layer">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <button type="button" onClick={onClose} className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ← Close
        </button>
        <span className="text-sm font-semibold">Explore</span>
        <span className="w-12" />
      </div>

      <div className="mx-auto w-full max-w-5xl flex-1 p-4">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div>
            <div className="flex items-stretch justify-center gap-2">
              <EvalBar evalCp={ev?.evalCp ?? null} flipped={orientation === "black"} />
              <div className="min-w-0 flex-1 max-w-[560px]">
                <Chessboard
                  options={{
                    id: boardId,
                    position: line.fen,
                    boardOrientation: orientation,
                    squareStyles,
                    arrows,
                    boardStyle: { borderRadius: "4px" },
                    ...SQUARES,
                    onPieceDrop: onDrop,
                    onSquareClick: ({ square }) => line.squareClick(square as Square),
                    allowDragging: true,
                    animationDurationInMs: 150,
                  }}
                />
              </div>
            </div>

            <div className="mt-4 flex min-h-[36px] flex-wrap items-center justify-center gap-3">
              <button type="button" onClick={back} disabled={!line.canBack} className={btn}>
                ← Undo
              </button>
              <span className="text-xs tabular-nums text-zinc-500">{n === 0 ? "Exploring — make a move" : `${n} ${n === 1 ? "move" : "moves"} in`}</span>
              <button type="button" onClick={forward} disabled={!line.canForward} className={btn}>
                Redo →
              </button>
              <button type="button" onClick={reset} disabled={!line.canBack && !line.canForward} className={btn}>
                ↺ Reset
              </button>
            </div>

            <p className="mt-3 text-center text-xs text-zinc-500">
              In-browser analysis by Stockfish 18 (GPL-3.0) ·{" "}
              <a href="/engine/Copying.txt" target="_blank" rel="noreferrer" className="underline hover:text-zinc-900 dark:hover:text-zinc-100">
                licence
              </a>{" "}
              ·{" "}
              <a href="/engine/NNUE-NOTICE.txt" target="_blank" rel="noreferrer" className="underline hover:text-zinc-900 dark:hover:text-zinc-100">
                notices
              </a>{" "}
              ·{" "}
              <a href="/engine/stockfish-source.tar.gz" target="_blank" rel="noreferrer" className="underline hover:text-zinc-900 dark:hover:text-zinc-100">
                source
              </a>
            </p>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3 rounded border border-zinc-200 p-3 dark:border-zinc-800">
              <label htmlFor={`${boardId}-depth`} className="text-sm font-semibold">
                Engine depth
              </label>
              <select id={`${boardId}-depth`} className={select} value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
                {steps.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-2 rounded border border-zinc-200 p-3 dark:border-zinc-800" data-testid="engine-panel">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold">Engine</span>
                <span className="font-mono text-xs text-zinc-500">{finished ?? fmtCp(ev?.evalCp ?? null)}</span>
              </div>
              {finished ? (
                <p className="text-sm text-zinc-500">{finished}</p>
              ) : !engine.ready ? (
                <p className="text-xs text-zinc-500">Loading engine…</p>
              ) : (
                <div className="space-y-1 text-sm">
                  {bestSan && (
                    <p>
                      <span className="text-zinc-500">Best:</span> <span className="font-mono text-emerald-600 dark:text-emerald-400">{bestSan}</span>
                      {ev?.thinking && <span className="text-zinc-500"> · thinking…</span>}
                    </p>
                  )}
                  {!bestSan && ev?.thinking && <p className="text-xs text-zinc-500">Thinking…</p>}
                  {pvSan && <p className="break-words font-mono text-xs leading-relaxed text-zinc-500">{pvSan}</p>}
                </div>
              )}
            </div>
            <p className="text-xs leading-relaxed text-zinc-500">Drag or click pieces to play any line; the engine re-evaluates each position. Undo and Redo walk the line (← / →), Reset returns to the starting position.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
