import { useCallback, useEffect, useRef, useState } from "react";
import { CardInner } from "./CardInner";
import { Overlay } from "./Overlay";
import type { AddToRepertoire } from "./RepertoireBadge";
import type { BoardSize, PositionCardData } from "./types";

const SIZES: BoardSize[] = ["S", "M", "L"];

/**
 * A list of position cards, and the overlay that opens on one and steps through the rest.
 * The page supplies what differs: the accent of a card, what sits in its corner, and the
 * overlay's action buttons for whichever card is showing, and — for a page that lets a board be
 * added to the repertoire — the offer and the repertoire revision the overlay's readers follow.
 * `onShowingChange` tells the page which card the overlay shows — on opening, stepping, a list
 * that shrank under it (the overlay slides to the previous board) — and null once it closes.
 */
export function PositionList({ items, accent, headerRight, overlayActions, overlaySuspended, addToRepertoire, repertoireRevision, onShowingChange }: { items: PositionCardData[]; accent?: (item: PositionCardData) => string; headerRight?: (item: PositionCardData) => React.ReactNode; overlayActions?: (item: PositionCardData) => React.ReactNode; overlaySuspended?: boolean; addToRepertoire?: (item: PositionCardData) => AddToRepertoire | undefined; repertoireRevision?: number; onShowingChange?: (item: PositionCardData | null) => void }) {
  const [open, setOpen] = useState<number | null>(null);
  const [showing, setShowing] = useState(0);
  const [boardSize, setBoardSize] = useState<BoardSize>("M");
  const close = useCallback(() => setOpen(null), []);
  const openAt = (i: number) => {
    setOpen(i);
    setShowing(i);
  };
  const current = items[Math.min(showing, items.length - 1)];
  // The shown card's identity, as the overlay clamps it: reported whenever it changes, from
  // whatever cause, and as null when the overlay closes.
  const shownKey = open === null || !current ? null : `${current.chessGameId}:${current.ply}:${current.key ?? current.fen}`;
  const shown = open === null ? null : (current ?? null);
  const shownRef = useRef(shown);
  useEffect(() => {
    shownRef.current = shown;
  });
  useEffect(() => {
    onShowingChange?.(shownRef.current);
  }, [shownKey, onShowingChange]);

  return (
    <>
      <div className="mb-2 flex justify-end gap-1" role="group" aria-label="Board size">
        {SIZES.map((s) => (
          <button key={s} type="button" aria-pressed={boardSize === s} onClick={() => setBoardSize(s)} className={`rounded border px-2 py-0.5 text-xs ${boardSize === s ? "border-zinc-900 dark:border-zinc-100" : "border-zinc-300 text-zinc-500 dark:border-zinc-700"}`}>
            {s}
          </button>
        ))}
      </div>
      <div className="space-y-2">
        {items.map((item, i) => (
          <div key={item.key ?? item.fen} data-testid="position-card" className={`cursor-pointer overflow-hidden rounded-lg border border-l-4 border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900 ${accent?.(item) ?? ""}`} onClick={() => openAt(i)}>
            <CardInner d={item} headerRight={headerRight?.(item)} boardSize={boardSize} onBoardClick={() => openAt(i)} />
          </div>
        ))}
      </div>
      {open !== null && current && <Overlay items={items} initialIndex={open} onClose={close} onIndexChange={setShowing} actions={overlayActions?.(current)} suspended={overlaySuspended} addToRepertoire={addToRepertoire} repertoireRevision={repertoireRevision} />}
    </>
  );
}
