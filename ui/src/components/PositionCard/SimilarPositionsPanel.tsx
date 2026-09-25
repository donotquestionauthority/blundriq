/**
 * Similar positions in the repertoire: the near neighbourhood of the card's board, as a
 * collapsible panel. It fetches only when expanded — the count is an output of the search, so the
 * header shows one only once a search has answered, and keeps it when the panel is collapsed
 * again. The fetch, its cache and its abort rules are `useSimilarPositions`,
 * shared with the solver's modal; the panel's expanded state is what enables it, so collapsing
 * aborts. A change of identity collapses the panel (derived in the same render, so the fetcher is
 * never enabled for a board the user has not asked about) and closes the compare view.
 *
 * The response is server-final: one entry per board with all of its groups. The only chess done
 * here is turning a server SAN into arrow squares.
 */
import { useEffect, useId, useState } from "react";
import type { CSSProperties } from "react";
import { Chessboard } from "react-chessboard";
import type { PrepGroup, SimilarNeighbour } from "../../repertoire";
import { distanceLabel, groupKey, neighbourArrows, prepLabel } from "../../compare";
import { similarRequestKey, useSimilarPositions } from "../../hooks/useSimilarPositions";
import { HIGHLIGHT, SQUARES } from "../../utils/board";
import type { BoardArrow } from "../../utils/chess";
import { SimilarCompareView } from "./SimilarCompareView";

/** One neighbour board: differing squares highlighted, its arrows drawn. Every board on the page
 *  has its own id (react-chessboard resolves touch taps by element id). */
export function NeighbourBoard({ n, orientation }: { n: SimilarNeighbour; orientation: "white" | "black" }) {
  const boardId = "similar" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const squareStyles: Record<string, CSSProperties> = {};
  for (const d of n.diff_squares) squareStyles[d.square] = { backgroundColor: HIGHLIGHT.selected };
  return (
    <div className="aspect-square w-full max-w-[16rem]" data-testid="neighbour-board">
      <Chessboard options={{ id: boardId, position: n.fen, allowDragging: false, boardStyle: { borderRadius: "6px" }, ...SQUARES, squareStyles, boardOrientation: orientation, arrows: neighbourArrows(n) }} />
    </div>
  );
}

export function GroupRow({ g }: { g: PrepGroup }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 text-xs">
      <span className={g.prep_status === "move" ? "font-mono font-medium text-amber-700 dark:text-amber-400" : "italic text-zinc-500"}>{prepLabel(g)}</span>
      {g.is_queried_move && <span className="rounded bg-sky-100 px-1 text-[10px] text-sky-800 dark:bg-sky-900/40 dark:text-sky-300">your move</span>}
      <span className="text-zinc-500">
        after <span className="font-mono">{g.arriving.san}</span>
      </span>
      <span className="min-w-0 break-words text-zinc-600 dark:text-zinc-400">
        {g.book_title}
        {g.chapter_title && <> › {g.chapter_title}</>}
        {g.line_name && <> › {g.line_name}</>}
        {g.is_alternative && <span className="text-zinc-500"> (alt)</span>}
      </span>
      {g.carried_by_line_count > 1 && <span className="text-zinc-500">×{g.carried_by_line_count} lines</span>}
    </div>
  );
}

export function DivergentTag() {
  return <span className="rounded bg-orange-100 px-1 text-[10px] text-orange-800 dark:bg-orange-900/40 dark:text-orange-300">two book moves</span>;
}

export function SimilarPositionsPanel({
  fen,
  queriedMove,
  orientation,
  mainArrows,
  compareOpen = false,
  onCompareOpenChange,
}: {
  fen: string;
  /** The card's questioned move (played / most common); server-verified, it only flags groups. */
  queriedMove?: string | null;
  orientation: "white" | "black";
  /** The card board's own arrows, passed through to the compare view untouched. */
  mainArrows?: BoardArrow[];
  /** The compare view's open state is the Overlay's: its own listeners detach while it is open. */
  compareOpen?: boolean;
  onCompareOpenChange?: (open: boolean) => void;
}) {
  const requestKey = similarRequestKey(fen, queriedMove);
  // Expanded FOR an identity, so a new one is collapsed in the render that brings it.
  const [expandedFor, setExpandedFor] = useState<string | null>(null);
  const expanded = expandedFor === requestKey;
  const [openRow, setOpenRow] = useState<string | null>(null);
  const { status, data, retry } = useSimilarPositions(fen, queriedMove, expanded);

  useEffect(() => {
    setExpandedFor(null);
    setOpenRow(null);
    onCompareOpenChange?.(false);
    // The setter is stable; the effect keys on the request identity alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);

  const neighbours = data?.neighbours ?? [];
  return (
    <div className="rounded border border-zinc-200 dark:border-zinc-800" data-testid="similar-panel">
      <button type="button" onClick={() => setExpandedFor(expanded ? null : requestKey)} aria-expanded={expanded} className="flex w-full items-center justify-between px-3 py-2.5 text-left">
        <span className="text-xs uppercase tracking-wide text-zinc-500">
          Similar positions in your repertoire
          {status === "loaded" && data && (
            <span className="ml-2 normal-case tracking-normal text-zinc-600 dark:text-zinc-400">
              {neighbours.length} position{neighbours.length === 1 ? "" : "s"}
            </span>
          )}
        </span>
        <span className="flex items-center gap-2 text-xs text-zinc-500">
          {status === "loading" && <span className="h-3 w-3 animate-spin rounded-full border border-zinc-400 border-t-transparent" />}
          {expanded ? "▾" : "▸"}
        </span>
      </button>

      {expanded && (
        <div className="space-y-3 border-t border-zinc-200 p-3 dark:border-zinc-800">
          {status === "loading" && <p className="text-xs text-zinc-500">Searching your repertoire…</p>}
          {status === "error" && (
            <div className="flex items-center justify-between text-sm">
              <span role="alert" className="text-red-600 dark:text-red-400">
                Couldn't load similar positions.
              </span>
              <button type="button" onClick={retry} className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700">
                Retry
              </button>
            </div>
          )}
          {status === "loaded" && data && neighbours.length === 0 && (
            <p className="text-xs text-zinc-500">No similar positions within {data.query.max_distance} squares of your active repertoire.</p>
          )}
          {status === "loaded" && data && neighbours.length > 0 && (
            <>
              {onCompareOpenChange && (
                <button type="button" onClick={() => onCompareOpenChange(true)} className="w-full rounded border border-sky-300 bg-sky-50 px-3 py-2 text-xs font-medium text-sky-800 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-300">
                  Compare side by side
                </button>
              )}
              <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {neighbours.map((n) => (
                  <li key={n.fen} className="py-2">
                    <button type="button" className="flex w-full items-start justify-between gap-2 text-left" aria-expanded={openRow === n.fen} onClick={() => setOpenRow(openRow === n.fen ? null : n.fen)}>
                      <span className="text-sm">
                        {distanceLabel(n)} {n.board_prep_divergent && <DivergentTag />}
                      </span>
                      <span className="text-xs text-zinc-500">{openRow === n.fen ? "▾" : "▸"}</span>
                    </button>
                    <div className="mt-1 space-y-1">
                      {n.groups.map((g) => (
                        <GroupRow key={groupKey(g)} g={g} />
                      ))}
                    </div>
                    {openRow === n.fen && (
                      <div className="mt-2 space-y-2">
                        <NeighbourBoard n={n} orientation={orientation} />
                        <p className="text-xs text-zinc-500">
                          Highlighted squares differ from the position above; blue is how the line got here{n.groups.some((g) => g.prep_status === "move") && ", orange your book move"}.{n.castling_delta.length > 0 && <> Castling rights differ: {n.castling_delta.join(", ")}.</>}
                        </p>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              {data.truncated && (
                <p className="text-xs text-zinc-500">
                  {data.positions_omitted} more position{data.positions_omitted === 1 ? "" : "s"} beyond the cap (Preferences › Similar positions).
                </p>
              )}
            </>
          )}
        </div>
      )}

      {compareOpen && status === "loaded" && data && neighbours.length > 0 && <SimilarCompareView fen={fen} mainArrows={mainArrows ?? []} neighbours={neighbours} orientation={orientation} onClose={() => onCompareOpenChange?.(false)} />}
    </div>
  );
}
