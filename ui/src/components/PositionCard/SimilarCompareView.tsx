/**
 * Every similar position on one screen, beside the asker's own board (with its own arrows, built
 * once by the host). Renders what the host's `useSimilarPositions` holds: no request of its own.
 * The Overlay's panel opens it over a loaded answer; the solver opens it at once and passes the
 * search's status through, so the searching, failed and empty states are drawn here too.
 *
 * Two mechanisms keep a key or a swipe from reaching the Overlay underneath. The Overlay detaches
 * its listeners while `compareOpen` (the state gate: it also covers a gesture whose start this
 * view never saw), and this view consumes Escape, the arrow keys and a ≥50 px swipe in the capture
 * phase — only a swipe whose touchstart it observed, since it can mount mid-gesture. Nothing is
 * `preventDefault`ed: taps and scrolling inside keep their browser behaviour.
 */
import { useEffect, useId, useRef } from "react";
import { Chessboard } from "react-chessboard";
import type { SimilarNeighbour } from "../../repertoire";
import { SQUARES } from "../../utils/board";
import type { BoardArrow } from "../../utils/chess";
import { distanceLabel, groupKey } from "../../compare";
import { DivergentTag, GroupRow, NeighbourBoard } from "./SimilarPositionsPanel";

export function SimilarCompareView({
  fen,
  mainArrows,
  neighbours,
  orientation,
  onClose,
  status = "loaded",
  onRetry,
  maxDistance = null,
}: {
  fen: string;
  mainArrows: BoardArrow[];
  neighbours: SimilarNeighbour[];
  orientation: "white" | "black";
  onClose: () => void;
  /** The host's search state; `neighbours` is read only when loaded. */
  status?: "loading" | "loaded" | "error";
  onRetry?: () => void;
  /** For the empty state's message; the answer's own `query.max_distance`. */
  maxDistance?: number | null;
}) {
  const boardId = "similarpinned" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const touchStartX = useRef<number | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.stopPropagation();
      }
    };
    const onTouchStart = (e: TouchEvent) => {
      touchStartX.current = e.touches[0].clientX;
    };
    const onTouchEnd = (e: TouchEvent) => {
      if (touchStartX.current === null) return;
      const dx = e.changedTouches[0].clientX - touchStartX.current;
      touchStartX.current = null;
      if (Math.abs(dx) >= 50) e.stopPropagation();
    };
    const onTouchCancel = () => {
      touchStartX.current = null;
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("touchstart", onTouchStart, true);
    window.addEventListener("touchend", onTouchEnd, true);
    window.addEventListener("touchcancel", onTouchCancel, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("touchstart", onTouchStart, true);
      window.removeEventListener("touchend", onTouchEnd, true);
      window.removeEventListener("touchcancel", onTouchCancel, true);
    };
  }, [onClose]);

  return (
    <div role="dialog" aria-label="Similar positions" className="fixed inset-0 z-[60] overflow-y-auto overscroll-contain bg-zinc-50 dark:bg-zinc-950" data-testid="similar-compare-view">
      <div className="mx-auto w-full max-w-6xl p-3 lg:flex lg:items-start lg:gap-6 lg:p-6">
        <div className="sticky top-0 z-10 -mx-3 border-b border-zinc-200 bg-zinc-50 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950 lg:top-6 lg:mx-0 lg:w-72 lg:shrink-0 lg:self-start lg:rounded lg:border lg:p-3">
          <div className="flex items-center justify-between pb-2">
            <button type="button" onClick={onClose} className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
              ← Back
            </button>
            {status === "loaded" && (
              <span className="text-xs text-zinc-500">
                {neighbours.length} similar position{neighbours.length === 1 ? "" : "s"}
              </span>
            )}
          </div>
          <div className="flex items-start gap-3 lg:block">
            <div className="w-44 shrink-0 lg:w-full" data-testid="pinned-board">
              <Chessboard options={{ id: boardId, position: fen, allowDragging: false, boardStyle: { borderRadius: "6px" }, ...SQUARES, boardOrientation: orientation, arrows: mainArrows }} />
            </div>
            <p className="text-xs text-zinc-500 lg:mt-2">Your position. Each similar position highlights the squares that differ, shows how its line got there in blue, and your book move there in orange.</p>
          </div>
        </div>

        <div className="mt-3 flex-1 lg:mt-0">
          {status === "loading" && (
            <div className="flex items-center gap-2 p-4 text-sm text-zinc-500">
              <span className="h-4 w-4 animate-spin rounded-full border border-zinc-400 border-t-transparent" />
              Searching your repertoire…
            </div>
          )}
          {status === "error" && (
            <div className="flex items-center justify-between rounded border border-zinc-200 p-4 text-sm dark:border-zinc-800">
              <span role="alert" className="text-red-600 dark:text-red-400">
                Couldn't load similar positions.
              </span>
              {onRetry && (
                <button type="button" onClick={onRetry} className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700">
                  Retry
                </button>
              )}
            </div>
          )}
          {status === "loaded" && neighbours.length === 0 && <p className="p-4 text-xs text-zinc-500">No similar positions within {maxDistance ?? "the configured"} squares of your active repertoire.</p>}
          {status === "loaded" && neighbours.length > 0 && (
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-3">
              {neighbours.map((n) => (
                <div key={n.fen} className="space-y-1.5 rounded border border-zinc-200 p-2 dark:border-zinc-800">
                  <NeighbourBoard n={n} orientation={orientation} />
                  <p className="text-xs">
                    {distanceLabel(n)} {n.board_prep_divergent && <DivergentTag />}
                  </p>
                  {n.castling_delta.length > 0 && <p className="text-xs text-zinc-500">Castling rights differ: {n.castling_delta.join(", ")}.</p>}
                  <div className="space-y-1">
                    {n.groups.map((g) => (
                      <GroupRow key={groupKey(g)} g={g} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
