import { useCallback, useEffect, useMemo, useState } from "react";
import { Chessboard } from "react-chessboard";
import { daysAgo } from "../../blunders";
import { SQUARES } from "../../utils/board";
import { buildArrows, buildPgn, decisionNodeArrows, parsesAsFen, sanToSquares } from "../../utils/chess";
import { ExploreLayer } from "../ExploreLayer";
import { AiExplanationPanel } from "./AiExplanationPanel";
import { ClassBadge, RepliesLine } from "./CardInner";
import { GamesTable } from "./GamesTable";
import { LineReaderPanel } from "./LineReaderPanel";
import { RepLinesPanel } from "./RepLinesPanel";
import { SimilarPositionsPanel } from "./SimilarPositionsPanel";
import { lineNamesSummary, recommended } from "./types";
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
 * While `suspended` (the page has a dialog open above it), the similar-positions compare view is
 * open, or Explore is, the overlay listens to nothing, so a key or a swipe meant for the dialog
 * cannot also move the list underneath. Detaching is the point: a flag checked inside a handler
 * would still record the start of a swipe that the dialog owns.
 *
 * Explore is seeded with the FEN and colour captured at the click, not `d`'s: the list can shrink
 * under an open overlay (a dismissal refetches it) and the clamped index would otherwise re-seed an
 * open layer with another card's board, or flip it.
 */
export function Overlay({ items, initialIndex, onClose, onIndexChange, actions, suspended = false }: { items: PositionCardData[]; initialIndex: number; onClose: () => void; onIndexChange?: (i: number) => void; actions?: React.ReactNode; suspended?: boolean }) {
  const [index, setIndex] = useState(initialIndex);
  const [compareOpen, setCompareOpen] = useState(false);
  const [exploreSeed, setExploreSeed] = useState<{ fen: string; orientation: "white" | "black" } | null>(null);
  const closeExplore = useCallback(() => setExploreSeed(null), []);
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
    if (suspended || compareOpen || exploreSeed) return;
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
  }, [at, hasPrev, hasNext, onClose, onIndexChange, suspended, compareOpen, exploreSeed]);

  // Expected always to parse: card lists go through `core.chess.eligibility`. Kept because a
  // disabled button is cheaper than a layer that mounts and throws.
  const fenHere = d?.fen ?? "";
  const canExplore = useMemo(() => parsesAsFen(fenHere), [fenHere]);

  if (!d) return null;
  const step = (i: number) => {
    setCompareOpen(false);
    setExploreSeed(null);
    setIndex(i);
    onIndexChange?.(i);
  };
  const pgn = d.moves && d.ply ? buildPgn(d.moves, d.ply) : "";
  const node = d.oppReplies != null;
  const played = node ? null : (d.movePlayed ?? d.mostCommonPlayed ?? null);
  // A deviation pattern spans boards: its most common played move is an aggregate over the pattern and
  // its board is the latest game's, so the move can be illegal here. The similar-positions search only
  // takes a move it can play on this board (the arrows already draw nothing for such a move).
  const queriedMove = played && sanToSquares(d.fen, played) ? played : null;
  const best = node ? { move: null, label: "Best" as const } : recommended(d);
  // One derivation for the board here and for anything that shows the same board beside it.
  const mainArrows = node ? decisionNodeArrows({ fen: d.fen, replies: d.oppReplies, leadIn: d.leadIn, leadPreFen: d.leadPreFen }) : buildArrows({ fen: d.fen, moves: d.moves, ply: d.ply, movePlayed: played, bestMove: best.move });
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
              arrows: mainArrows,
            }}
          />
        </div>

        {node && (
          <div className="rounded border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <RepliesLine d={d} />
          </div>
        )}
        {(played || best.move) && (
          <div className="space-y-1 rounded border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            {played && (
              <div className="flex items-center gap-3">
                <span className="w-16 text-xs text-zinc-500">{d.movePlayed ? "Played" : "Usually"}</span>
                <span className="font-mono text-red-600 dark:text-red-400">{played}</span>
                {d.cpLoss ? <span className="ml-auto text-xs text-zinc-500">−{d.cpLoss}cp</span> : null}
              </div>
            )}
            {best.move && (
              <div className="flex items-center gap-3">
                <span className="w-16 text-xs text-zinc-500">{best.label}</span>
                <span className="font-mono text-emerald-600 dark:text-emerald-400">{best.move}</span>
                {d.oppGames && (
                  <span className="ml-auto text-xs text-zinc-500">{best.label === "Expected" && d.expectedMove ? "your repertoire plays this" : d.bestMoveDate ? `from your game on ${d.bestMoveDate}` : ""}</span>
                )}
              </div>
            )}
            {d.bestLine && <p className="break-words pt-1 font-mono text-xs text-zinc-500">{d.bestLine}</p>}
          </div>
        )}

        <button type="button" data-testid="explore-launch" onClick={() => canExplore && setExploreSeed({ fen: d.fen, orientation: d.color })} disabled={!canExplore} title={canExplore ? undefined : "This position cannot be explored"} className="w-full rounded border border-sky-300 bg-sky-50 px-3 py-2 text-xs font-medium text-sky-800 disabled:opacity-40 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-300">
          Explore from here
        </button>

        {d.record && (
          <p className="text-sm text-zinc-500">
            <span className="text-emerald-600 dark:text-emerald-400">{d.record.wins}W</span> · <span className="text-red-600 dark:text-red-400">{d.record.losses}L</span> · {d.record.draws}D · {d.record.win_pct}% won
          </p>
        )}

        {d.chessGameId != null && d.ply != null && <AiExplanationPanel key={`${d.chessGameId}:${d.ply}`} chessGameId={d.chessGameId} ply={d.ply} />}

        <LineReaderPanel fen={d.fen} repertoireLineId={null} />

        <SimilarPositionsPanel fen={d.fen} queriedMove={queriedMove} orientation={d.color} mainArrows={mainArrows} compareOpen={compareOpen} onCompareOpenChange={setCompareOpen} />

        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}

        {pgn && <CopyBlock label="PGN to position" text={pgn} />}
        <CopyBlock label="FEN" text={d.fen} />

        {d.repLines?.length ? <RepLinesPanel key={d.fen} lines={d.repLines} /> : null}

        {d.games.length > 0 ? <GamesTable games={d.games} bestLabel={best.label} title={d.oppGames ? "My games" : "Games"} /> : !node && <p className="py-2 text-center text-xs text-zinc-500">No game history for this position.</p>}
        {d.oppGames && (d.oppGames.length > 0 ? <GamesTable games={d.oppGames} bestLabel={best.label} title="Their games" /> : <p className="py-2 text-center text-xs text-zinc-500">None of their games reach this position.</p>)}

        <p className="pb-4 text-center text-xs text-zinc-500">Swipe or use ‹ › to move through the list</p>
      </div>
      {exploreSeed && <ExploreLayer fen={exploreSeed.fen} orientation={exploreSeed.orientation} onClose={closeExplore} />}
    </div>
  );
}
