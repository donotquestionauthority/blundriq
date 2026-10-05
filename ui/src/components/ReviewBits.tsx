import { useId } from "react";
import type { CSSProperties } from "react";
import { Link } from "react-router";
import { Chessboard } from "react-chessboard";
import { ARROWS, HIGHLIGHT, SQUARES } from "../utils/board";
import { BAD, GOOD, MISTAKE_STATUS_LABELS, STATUS_LABELS, STATUS_TONE, belowExpectation, givenAway, lineText, moveLabel, pct } from "../review";
import type { MistakeCard as Mistake, MistakeMove, MistakeStatus, PositionStatus, ReviewPosition, VisitState } from "../review";
import type { From } from "../utils/returnTo";

// A chip's label may wrap: "Improving, too early to call" is wider than a 320 px card's column.
const chip = "inline-block max-w-full rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide";

/** A status as a small outlined chip in its tone. */
export function StatusChip({ status }: { status: PositionStatus | null }) {
  if (!status) return null;
  const tone = STATUS_TONE[status];
  return (
    <span className={chip} style={{ borderColor: tone, color: tone }} data-status={status}>
      {STATUS_LABELS[status]}
    </span>
  );
}

const MISTAKE_TONE: Record<MistakeStatus, string> = { still_costing: BAD, not_yet_checked: ARROWS.opponent, fixed: GOOD, not_reached_lately: ARROWS.opponent };
const MISTAKE_HINT: Partial<Record<MistakeStatus, string>> = {
  not_yet_checked: "Your newest game here is waiting for the engine; the numbers count the moves it has checked.",
  fixed: "Your last visits here were fine after costly ones.",
  not_reached_lately: "Not reached in the last 30 days.",
};

/** An opening-mistakes status chip. */
export function MistakeChip({ status }: { status: MistakeStatus | null }) {
  if (!status) return null;
  const tone = MISTAKE_TONE[status];
  return (
    <span className={chip} style={{ borderColor: tone, color: tone }} data-status={status} title={MISTAKE_HINT[status]}>
      {MISTAKE_STATUS_LABELS[status]}
    </span>
  );
}

const VISIT_WORDS: Record<VisitState, string> = { costly: "costly", fine: "fine", unknown: "not checked yet" };

/** The last visits, oldest first: a red dot for a costly move, green for a fine one, grey for one
 *  the engine has not checked. */
export function VisitStrip({ strip }: { strip: VisitState[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1" role="img" aria-label={`Last ${strip.length} visits, oldest first: ${strip.map((s) => VISIT_WORDS[s]).join(", ")}`} data-testid="strip">
      {strip.map((s, i) => (
        <span key={i} title={VISIT_WORDS[s]} data-visit={s} className={`h-2.5 w-2.5 rounded-full ${s === "unknown" ? "bg-zinc-300 dark:bg-zinc-600" : ""}`} style={s === "unknown" ? undefined : { backgroundColor: s === "costly" ? BAD : GOOD }} />
      ))}
    </div>
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

/** A static board: oriented to the player's side, the move that reached it highlighted. Every board on a
 *  page needs its own id (react-chessboard looks its squares up by element id). A small board
 *  leaves the coordinates out: at card size they sit on the pieces. */
export function PositionBoard({ fen, colour, lastMove, small = false }: { fen: string; colour: "white" | "black"; lastMove: string | null; small?: boolean }) {
  const id = "rv" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const squareStyles: Record<string, CSSProperties> = {};
  if (lastMove) {
    squareStyles[lastMove.slice(0, 2)] = { backgroundColor: HIGHLIGHT.lastMove };
    squareStyles[lastMove.slice(2, 4)] = { backgroundColor: HIGHLIGHT.lastMove };
  }
  return <Chessboard options={{ id, position: fen, allowDragging: false, boardOrientation: colour, squareStyles, boardStyle: { borderRadius: "4px" }, showNotation: !small, ...SQUARES }} />;
}

/** "79 games · 42% (expected 50%) · ≈1.7 below expectation a month" */
export function NumbersLine({ p, fixed = false, months = 12 }: { p: ReviewPosition; fixed?: boolean; months?: number }) {
  if (fixed)
    return (
      <p className="text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
        {p.n} games · {months} months: {pct(p.score)} (expected {pct(p.expected)}) · now: {pct(p.current_score)} (expected {pct(p.current_expected)})
      </p>
    );
  return (
    <p className="text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
      {p.n} games · {pct(p.score)} (expected {pct(p.expected)}) · {belowExpectation(p.leak_per_month)}
    </p>
  );
}

const card = "flex gap-3 rounded border border-zinc-200 p-3 hover:border-zinc-400 dark:border-zinc-800 dark:hover:border-zinc-600";
const thumb = "aspect-square w-[96px] shrink-0 self-start sm:w-[160px]";

/** The card's line, its side, and the line of the card it sits inside. Every part wraps. */
function LineHeader({ colour, line, parentLine }: { colour: "white" | "black"; line: string[]; parentLine: string | null }) {
  return (
    <>
      {parentLine && <p className="break-words text-[11px] text-zinc-500">Inside {parentLine}</p>}
      <p className="break-words font-mono text-sm">
        <span className="mr-1.5 font-sans text-xs text-zinc-500">{colour === "white" ? "White" : "Black"}</span>
        {line.length ? lineText(line) : "Starting position"}
      </p>
    </>
  );
}

/** A results position. The whole card opens the position's page, carrying the way back. */
export function PositionCard({ p, to, from, parentLine, fixed = false, months = 12 }: { p: ReviewPosition; to: string; from: From; parentLine: string | null; fixed?: boolean; months?: number }) {
  return (
    <Link to={to} state={{ from }} className={card} data-testid="position-card" data-key={p.key}>
      <div className={thumb}>{p.fen ? <PositionBoard fen={p.fen} colour={p.colour} lastMove={p.last_move} small /> : null}</div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <LineHeader colour={p.colour} line={p.line_san} parentLine={parentLine} />
        <NumbersLine p={p} fixed={fixed} months={months} />
        <StatusChip status={p.status} />
        <TrendBars trend={p.trend} />
      </div>
    </Link>
  );
}

const loss = (x: number | null) => (x == null ? "not checked" : x < 0.5 ? "no loss" : `−${x.toFixed(0)}`);

/** "…Nf6 ×12 (−15)": a move the player played from the board, how often, and what it gave away on average. */
function moveSummary(ply: number, mv: MistakeMove): string {
  return `${moveLabel(ply, mv.san)} ×${mv.n} (${mv.mates ? "mate" : loss(mv.mean_loss)})`;
}

/** An opening mistake: where the player moves, what they played there, how often it cost them, how much a
 *  month now (in Fixed?: over the history, what it is ranked by), its status and its last visits.
 *  The whole card opens the position's page. */
export function MistakeCard({ m, to, from, parentLine, fixed = false, months = 12 }: { m: Mistake; to: string; from: From; parentLine: string | null; fixed?: boolean; months?: number }) {
  const ply = m.line_san.length;
  const unchecked = m.decisions - m.evaluated;
  return (
    <Link to={to} state={{ from }} className={card} data-testid="mistake-card" data-key={m.key}>
      <div className={thumb}>{m.fen ? <PositionBoard fen={m.fen} colour={m.colour} lastMove={m.last_move} small /> : null}</div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <LineHeader colour={m.colour} line={m.line_san} parentLine={parentLine} />
        <p className="break-words text-xs text-zinc-700 dark:text-zinc-300">
          You played {m.moves.slice(0, 3).map((mv) => moveSummary(ply, mv)).join(", ")}
          {m.best_move ? ` · engine: ${moveLabel(ply, m.best_move)}` : ""}
        </p>
        <p className="text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
          Costly in {m.costly_games} of {m.games} games · {fixed ? `≈${(m.per_month_12 * months).toFixed(1)} points given away over ${months} months` : givenAway(m.per_month)}
          {unchecked > 0 ? ` · ${m.evaluated} of ${m.decisions} visits checked` : ""}
        </p>
        <MistakeChip status={m.status} />
        <VisitStrip strip={m.strip} />
      </div>
    </Link>
  );
}
