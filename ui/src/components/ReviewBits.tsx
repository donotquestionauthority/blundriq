import { useId } from "react";
import type { CSSProperties } from "react";
import { Link } from "react-router";
import { Chessboard } from "react-chessboard";
import { HIGHLIGHT, SQUARES } from "../utils/board";
import { BAD, GOOD, STATUS_LABELS, STATUS_TONE, engineLabel, lineText, pct, pointsAMonth } from "../review";
import type { PositionStatus, ReviewPosition } from "../review";
import type { From } from "../utils/returnTo";

/** A status as a small outlined chip in its tone. */
export function StatusChip({ status }: { status: PositionStatus | null }) {
  if (!status) return null;
  const tone = STATUS_TONE[status];
  return (
    <span className="whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide" style={{ borderColor: tone, color: tone }} data-status={status}>
      {STATUS_LABELS[status]}
    </span>
  );
}

/** One bar per 30 days, oldest first: up (bad) is points below expectation per 100 games, down
 *  (good) above it; an empty month is a dot. */
export function TrendBars({ trend, height = 28 }: { trend: (number | null)[]; height?: number }) {
  const cap = 30;
  const half = height / 2;
  return (
    <div className="flex items-center gap-[2px]" style={{ height }} role="img" aria-label="Points below expectation per 100 games, by month, oldest first" data-testid="trend">
      {trend.map((v, i) => {
        const age = trend.length - i;
        const title = v == null ? `${age * 30}–${(age - 1) * 30} days ago: too few games` : `${age * 30}–${(age - 1) * 30} days ago: ${v > 0 ? `${v.toFixed(0)} below` : `${(-v).toFixed(0)} above`} expectation per 100 games`;
        if (v == null)
          return (
            <span key={i} title={title} className="flex h-full w-[6px] items-center justify-center" data-bar="empty">
              <span className="h-[2px] w-[2px] rounded-full bg-zinc-400" />
            </span>
          );
        const size = Math.max(1, (Math.min(Math.abs(v), cap) / cap) * half);
        return (
          <span key={i} title={title} className="relative h-full w-[6px]" data-bar={v > 0 ? "bad" : "good"}>
            <span className="absolute left-0 w-full rounded-[1px]" style={{ backgroundColor: v > 0 ? BAD : GOOD, height: size, ...(v > 0 ? { bottom: half } : { top: half }) }} />
          </span>
        );
      })}
    </div>
  );
}

/** A static board: oriented to Rob's side, the move that reached it highlighted. Every board on a
 *  page needs its own id (react-chessboard looks its squares up by element id). */
export function PositionBoard({ fen, colour, lastMove }: { fen: string; colour: "white" | "black"; lastMove: string | null }) {
  const id = "rv" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const squareStyles: Record<string, CSSProperties> = {};
  if (lastMove) {
    squareStyles[lastMove.slice(0, 2)] = { backgroundColor: HIGHLIGHT.lastMove };
    squareStyles[lastMove.slice(2, 4)] = { backgroundColor: HIGHLIGHT.lastMove };
  }
  return <Chessboard options={{ id, position: fen, allowDragging: false, boardOrientation: colour, squareStyles, boardStyle: { borderRadius: "4px" }, ...SQUARES }} />;
}

/** "79 games · 42% (expected 50%) · ≈1.7 points a month" */
export function NumbersLine({ p, fixed = false, months = 12 }: { p: ReviewPosition; fixed?: boolean; months?: number }) {
  if (fixed)
    return (
      <p className="text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
        {p.n} games · {months} months: {pct(p.score)} (expected {pct(p.expected)}) · now: {pct(p.current_score)} (expected {pct(p.current_expected)})
      </p>
    );
  return (
    <p className="text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
      {p.n} games · {pct(p.score)} (expected {pct(p.expected)}) · {pointsAMonth(p.leak_per_month)}
    </p>
  );
}

/** A ranked position. The whole card opens the position's page, carrying the way back. */
export function PositionCard({ p, to, from, parentLine, fixed = false, months = 12 }: { p: ReviewPosition; to: string; from: From; parentLine: string | null; fixed?: boolean; months?: number }) {
  return (
    <Link to={to} state={{ from }} className="flex gap-3 rounded border border-zinc-200 p-3 hover:border-zinc-400 dark:border-zinc-800 dark:hover:border-zinc-600" data-testid="position-card" data-key={p.key}>
      <div className="aspect-square w-[120px] shrink-0 self-start sm:w-[160px]">{p.fen ? <PositionBoard fen={p.fen} colour={p.colour} lastMove={p.last_move} /> : null}</div>
      <div className="min-w-0 flex-1 space-y-1.5">
        {parentLine && <p className="truncate text-[11px] text-zinc-500">Inside {parentLine}</p>}
        <p className="break-words font-mono text-sm">
          <span className="mr-1.5 font-sans text-xs text-zinc-500">{p.colour === "white" ? "White" : "Black"}</span>
          {lineText(p.line_san)}
        </p>
        <NumbersLine p={p} fixed={fixed} months={months} />
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip status={p.status} />
          <span className="text-xs text-zinc-500">{engineLabel(p.es_at_node)}</span>
        </div>
        <TrendBars trend={p.trend} />
      </div>
    </Link>
  );
}
