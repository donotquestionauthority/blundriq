import { useEffect, useState } from "react";
import { Chessboard } from "react-chessboard";
import { daysAgo } from "../../blunders";
import { SQUARES } from "../../utils/board";
import { buildArrows, buildPgn } from "../../utils/chess";
import { AiExplanationPanel } from "./AiExplanationPanel";
import { ClassBadge } from "./CardInner";
import { GamesTable } from "./GamesTable";
import { lineNamesSummary } from "./types";
import type { PositionCardData } from "./types";

function CopyBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-zinc-500">{label}</span>
        <button
          type="button"
          className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
          onClick={async () => {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? "✓ copied" : "copy"}
        </button>
      </div>
      <p className="break-all font-mono text-xs text-zinc-600 dark:text-zinc-400">{text}</p>
    </div>
  );
}

/**
 * The full-screen view of one card, with ‹ › through the list: arrows, Escape, and swipes.
 *
 * While `suspended` (the page has a dialog open above it) the overlay listens to nothing, so
 * a key or a swipe meant for the dialog cannot also move the list underneath. Detaching is
 * the point: a flag checked inside a handler would still record the start of a swipe that
 * the dialog owns.
 */
export function Overlay({ items, initialIndex, onClose, onIndexChange, actions, suspended = false }: { items: PositionCardData[]; initialIndex: number; onClose: () => void; onIndexChange?: (i: number) => void; actions?: React.ReactNode; suspended?: boolean }) {
  const [index, setIndex] = useState(initialIndex);
  // The list can shrink underneath an open overlay (a dismissal refetches it).
  const at = Math.min(index, items.length - 1);
  const d = items[at];
  const hasPrev = at > 0;
  const hasNext = at < items.length - 1;

  useEffect(() => {
    if (items.length === 0) onClose();
  }, [items.length, onClose]);

  // The page underneath must not scroll while this covers it.
  useEffect(() => {
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = before;
    };
  }, []);

  useEffect(() => {
    if (suspended) return;
    const go = (i: number) => {
      setIndex(i);
      onIndexChange?.(i);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" && hasNext) go(at + 1);
      if (e.key === "ArrowLeft" && hasPrev) go(at - 1);
      if (e.key === "Escape") onClose();
    };
    // Only a swipe whose start this overlay saw counts. The overlay mounts inside the very
    // touchend that opened it, and that touchend would otherwise read as a swipe from x = 0.
    let startX: number | null = null;
    const onTouchStart = (e: TouchEvent) => {
      startX = e.touches[0].clientX;
    };
    const onTouchEnd = (e: TouchEvent) => {
      if (startX === null) return;
      const dx = e.changedTouches[0].clientX - startX;
      startX = null;
      if (Math.abs(dx) < 50) return;
      if (dx < 0 && hasNext) go(at + 1);
      if (dx > 0 && hasPrev) go(at - 1);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("touchstart", onTouchStart);
    window.addEventListener("touchend", onTouchEnd);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("touchstart", onTouchStart);
      window.removeEventListener("touchend", onTouchEnd);
    };
  }, [at, hasPrev, hasNext, onClose, onIndexChange, suspended]);

  if (!d) return null;
  const step = (i: number) => {
    setIndex(i);
    onIndexChange?.(i);
  };
  const pgn = d.moves && d.ply ? buildPgn(d.moves, d.ply) : "";
  const nav = "flex h-8 w-8 items-center justify-center rounded border border-zinc-300 disabled:opacity-30 dark:border-zinc-700";

  return (
    <div role="dialog" aria-label="Position" className="fixed inset-0 z-40 flex flex-col overflow-y-auto bg-zinc-50 dark:bg-zinc-950">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <button type="button" onClick={onClose} className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ← Back
        </button>
        <span className="text-xs text-zinc-500">
          {at + 1} / {items.length}
        </span>
        <div className="flex gap-2">
          <button type="button" aria-label="Previous position" disabled={!hasPrev} onClick={() => step(at - 1)} className={nav}>
            ‹
          </button>
          <button type="button" aria-label="Next position" disabled={!hasNext} onClick={() => step(at + 1)} className={nav}>
            ›
          </button>
        </div>
      </div>

      <div className="mx-auto w-full max-w-2xl flex-1 space-y-4 p-4">
        <div className="flex flex-wrap items-center gap-3">
          {d.score !== undefined && <span className="text-4xl font-bold">{d.score}</span>}
          <span className="text-4xl font-bold text-zinc-400">{d.times}×</span>
          <div className="space-y-1">
            <div className="flex flex-wrap gap-1">
              {Object.entries(d.classifications ?? {}).map(([cls, n]) => (
                <span key={cls} className="flex items-center gap-1">
                  <ClassBadge cls={cls} />
                  <span className="text-xs text-zinc-500">{n}</span>
                </span>
              ))}
            </div>
            <p className="text-xs text-zinc-500">
              as {d.color}
              {d.lastSeen ? ` · last ${daysAgo(d.lastSeen)}` : ""}
            </p>
          </div>
        </div>

        {d.book ? (
          <div className="rounded border border-zinc-200 px-3 py-2 dark:border-zinc-800">
            <p className="text-xs uppercase tracking-wide text-zinc-500">Repertoire</p>
            <p className="text-sm">
              {d.book}
              {d.chapter && <span className="text-zinc-500"> › {d.chapter}</span>}
            </p>
            {d.lineNames?.length ? <p className="text-xs italic text-zinc-500">{lineNamesSummary(d.lineNames)}</p> : null}
          </div>
        ) : (
          d.context && <p className="text-sm text-zinc-500">{d.context}</p>
        )}

        <div className="mx-auto aspect-square w-full max-w-sm">
          <Chessboard
            options={{
              id: "position-overlay-board",
              position: d.fen,
              allowDragging: false,
              boardStyle: { borderRadius: "6px" },
              ...SQUARES,
              boardOrientation: d.color,
              arrows: buildArrows({ fen: d.fen, moves: d.moves, ply: d.ply, movePlayed: d.movePlayed, bestMove: d.bestMove }),
            }}
          />
        </div>

        {(d.movePlayed || d.bestMove) && (
          <div className="space-y-1 rounded border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            {d.movePlayed && (
              <div className="flex items-center gap-3">
                <span className="w-16 text-xs text-zinc-500">Played</span>
                <span className="font-mono text-red-600 dark:text-red-400">{d.movePlayed}</span>
                {d.cpLoss ? <span className="ml-auto text-xs text-zinc-500">−{d.cpLoss}cp</span> : null}
              </div>
            )}
            {d.bestMove && (
              <div className="flex items-center gap-3">
                <span className="w-16 text-xs text-zinc-500">Best</span>
                <span className="font-mono text-emerald-600 dark:text-emerald-400">{d.bestMove}</span>
              </div>
            )}
            {d.bestLine && <p className="break-words pt-1 font-mono text-xs text-zinc-500">{d.bestLine}</p>}
          </div>
        )}

        {d.chessGameId != null && d.ply != null && <AiExplanationPanel key={`${d.chessGameId}:${d.ply}`} chessGameId={d.chessGameId} ply={d.ply} />}

        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}

        {pgn && <CopyBlock label="PGN to position" text={pgn} />}
        <CopyBlock label="FEN" text={d.fen} />

        {d.games.length > 0 ? <GamesTable games={d.games} bestLabel="Best" /> : <p className="py-2 text-center text-xs text-zinc-500">No game history for this position.</p>}

        <p className="pb-4 text-center text-xs text-zinc-500">Swipe or use ‹ › to move through the list</p>
      </div>
    </div>
  );
}
