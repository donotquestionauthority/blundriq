import { useCallback, useState } from "react";
import { CardInner } from "./CardInner";
import { Overlay } from "./Overlay";
import type { BoardSize, PositionCardData } from "./types";

const SIZES: BoardSize[] = ["S", "M", "L"];

/**
 * A list of position cards, and the overlay that opens on one and steps through the rest.
 * The page supplies what differs: the accent of a card, what sits in its corner, and the
 * overlay's action buttons for whichever card is showing.
 */
export function PositionList({ items, accent, headerRight, overlayActions, overlaySuspended }: { items: PositionCardData[]; accent?: (item: PositionCardData) => string; headerRight?: (item: PositionCardData) => React.ReactNode; overlayActions?: (item: PositionCardData) => React.ReactNode; overlaySuspended?: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  const [showing, setShowing] = useState(0);
  const [boardSize, setBoardSize] = useState<BoardSize>("M");
  const close = useCallback(() => setOpen(null), []);
  const openAt = (i: number) => {
    setOpen(i);
    setShowing(i);
  };
  const current = items[Math.min(showing, items.length - 1)];

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
          <div key={item.fen} data-testid="position-card" className={`cursor-pointer overflow-hidden rounded-lg border border-l-4 border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900 ${accent?.(item) ?? ""}`} onClick={() => openAt(i)}>
            <CardInner d={item} headerRight={headerRight?.(item)} boardSize={boardSize} onBoardClick={() => openAt(i)} />
          </div>
        ))}
      </div>
      {open !== null && current && <Overlay items={items} initialIndex={open} onClose={close} onIndexChange={setShowing} actions={overlayActions?.(current)} suspended={overlaySuspended} />}
    </>
  );
}
