import { useCallback, useEffect, useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Chessboard } from "react-chessboard";
import { TIME_CLASS_LABELS } from "../blunders";
import type { TimeClass } from "../blunders";
import { AddLineModal } from "../components/AddLineModal";
import { LineWalkthrough } from "../components/PositionCard/LineReaderPanel";
import { fmtCp } from "../engine/eval";
import { sameDeps, useApi } from "../hooks/useApi";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { getReport, getReportDecisions, hasEvidence } from "../repertoire";
import type { ContestedBoard, DecisionRow, GapCard, GapsResponse, LineDecision, LineEvidenceOrCount, PreparedRow, RecentTry, ReportLine, ReportTally } from "../repertoire";
import { DECISION_SORT_KEYS, decisionKey, decisionLineCount, decisionLines as linesOf, filterDecisions, filterRows, flatten, medianDepth, SORT_KEYS, sortDecisions, sortRows, TABLE_VIEWS, VIEWS } from "./reportRows";
import type { DecisionSortKey, Dir, Filters, Row, SortKey, TableView, View } from "./reportRows";
import { SQUARES } from "../utils/board";
import { numberedLine } from "../utils/chess";
import { clearQueue, setQueued, useDrillQueue } from "../utils/drillQueue";

const select = "max-w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const tab = (active: boolean) => `rounded border px-2.5 py-1 text-xs font-medium ${active ? "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-300" : "border-zinc-300 text-zinc-500 hover:text-zinc-800 dark:border-zinc-700 dark:hover:text-zinc-200"}`;
const chip = "rounded border border-zinc-300 px-1 text-[10px] uppercase text-zinc-500 dark:border-zinc-700";
const smallButton = "rounded border border-zinc-300 px-2 py-0.5 text-xs font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800";

const pct = (n: number, of: number) => (of === 0 ? "–" : `${Math.round((100 * n) / of)}%`);
const wdl = (t: { wins: number; draws: number; losses: number }) => `${t.wins}–${t.draws}–${t.losses}`;
const scoreText = (s: number | null) => (s === null ? "–" : `${Math.round(100 * s)}%`);
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "–");

/** "opponent left 9 · you left 2 · ran out 1 · ended 1", the nonzero parts only. */
function whoLeft(t: ReportTally): string {
  const parts: string[] = [];
  if (t.left_by_opponent) parts.push(`opponent left ${t.left_by_opponent}`);
  if (t.left_by_me) parts.push(`you left ${t.left_by_me}`);
  if (t.ran_out) parts.push(`ran out ${t.ran_out}`);
  if (t.ended) parts.push(`ended in book ${t.ended}`);
  return parts.join(" · ");
}

/** How the second read stands: its cells show `…` until it lands and `–` if it failed. */
type Evidence = "pending" | "ready" | "failed";
const PENDING = "…";

/** "Move 7 · missed 6 of 8": the line's decision with the most slips (its own population: every
 *  in-scope game that reached the board, whichever line the matcher attached it to). */
const troubleText = (e: LineEvidenceOrCount | undefined, state: Evidence) => (state === "pending" ? PENDING : hasEvidence(e) && e.trouble_spot ? `Move ${e.trouble_spot.own_move} ${e.trouble_spot.move} · missed ${e.trouble_spot.slips} of ${e.trouble_spot.opportunities}` : null);
/** "5 of 9 · correct through move 3": decisions with any opportunity, and the continuously tested
 *  successful prefix — the one number the word "through" is used for. A line sent as its count
 *  alone had no decision reached: "0 of n · all untested". */
function testedText(e: LineEvidenceOrCount | undefined, state: Evidence): string {
  if (state === "pending") return PENDING;
  if (e === undefined) return "–";
  if (!hasEvidence(e)) return e.decisions_total === 0 ? "–" : `0 of ${e.decisions_total} · all untested`;
  const through = e.correct_through === null ? null : `correct through move ${e.correct_through}`;
  return through ? `${e.tested} of ${e.decisions_total} · ${through}` : `${e.tested} of ${e.decisions_total}`;
}
const tryMark = ([verdict]: RecentTry) => (verdict === "ok" ? "✓" : verdict === "fail" ? "✗" : "?");
/** "✓ ✗ ✓ · newest: right up to move 9" — the newest five loads' first tries, newest first, and what
 *  the newest one tested. The segment the solver showed plays no part: the trace is the evidence,
 *  and whether it was the whole line is the trace's own fact (`complete`), from the first read. */
function recentText(l: ReportLine): string {
  if (l.recent.length === 0) return "no practice yet";
  const [verdict, upTo, failedAt, complete] = l.recent[0];
  const extent = verdict === "ok" ? (complete ? "whole line" : `right up to move ${upTo ?? "?"}`) : verdict === "fail" ? `failed at move ${failedAt ?? "?"}` : "unreadable";
  return `${l.recent.map(tryMark).join(" ")} · newest: ${extent}`;
}

/** Rows listed before "Show more": the whole report is thousands of lines, and a phone paints
 *  them one card at a time. The filters, Select all shown and the drill see every shown row. */
export const PAGE = 200;

type ColKey = SortKey | "recent";
/** `views`: where the column is; `sortViews`: where its sort is offered, when that is wider. A
 *  column without `sortable: false` sorts; `study` is a preset in the Sort select, not a column. */
const COLUMNS: { key: ColKey; label: string; views: TableView[]; sortViews?: TableView[]; numeric?: boolean; title?: string; sortable?: false }[] = [
  { key: "colour", label: "Colour", views: TABLE_VIEWS },
  { key: "book", label: "Book", views: TABLE_VIEWS },
  { key: "chapter", label: "Chapter", views: ["lines", "chapters"] },
  { key: "line", label: "Line", views: ["lines"] },
  { key: "games", label: "Games", views: TABLE_VIEWS, numeric: true, title: "Games the matcher attached to it, then wins–draws–losses" },
  { key: "slips", label: "Slips", views: TABLE_VIEWS, numeric: true, title: "Games where you left the line while it still prescribed a move — a departure from the repertoire, which need not have been a bad move" },
  { key: "last_slip", label: "Last slip", views: TABLE_VIEWS, title: "When you last left it" },
  { key: "trouble", label: "Trouble spot", views: ["lines"], numeric: true, title: "The line's decision you miss most: missed k of n games that reached that board (any line, matched or not)" },
  { key: "tested", label: "Tested", views: ["lines"], numeric: true, title: "Decisions of the line any game reached, and how far every decision was reached and played right" },
  { key: "score", label: "Score", views: TABLE_VIEWS, numeric: true, title: "Wins and half the draws, over the decided games" },
  { key: "faithful", label: "Faithful", views: TABLE_VIEWS, numeric: true, title: "Games where you never left the line" },
  { key: "depth", label: "Depth", views: ["lines"], numeric: true, title: "How many of your moves in the line the games played, median" },
  { key: "loads", label: "Loads", views: TABLE_VIEWS, numeric: true, title: "Times the line's puzzle was loaded in the solver" },
  { key: "first_right", label: "First try", views: TABLE_VIEWS, numeric: true, title: "Loads whose first attempt was right ✓ and wrong ✗; sorts by the right ones" },
  { key: "recent", label: "Recent first tries", views: ["lines"], sortable: false, title: "The newest five loads' first tries, newest first, and what the newest one tested; practice is not scoped to the games" },
  { key: "last_played", label: "Last played", views: TABLE_VIEWS },
];
const SORT_LABELS: Record<SortKey, string> = { study: "Study order", colour: "Colour", book: "Book", chapter: "Chapter", line: "Line", games: "Games", slips: "Slips", last_slip: "Last slip", trouble: "Trouble spot", tested: "Tested", score: "Score", faithful: "Faithful", depth: "Depth", loads: "Loads", first_right: "First try", last_played: "Last played" };
const DECISION_SORT_LABELS: Record<DecisionSortKey, string> = { study: "Study order", colour: "Colour", move: "Move", missed: "Missed", opportunities: "Opportunities", most_played: "Most played", last_slip: "Last slip", lines: "Lines", practice: "Practice", last_played: "Last played" };

const isSortKey = (k: ColKey): k is SortKey => k !== "recent";
const colTitle = (key: ColKey) => COLUMNS.find((c) => c.key === key)?.title;
/** Whether a table view offers sorting by `key` (Study order everywhere). */
const sortsBy = (view: TableView, key: SortKey) => key === "study" || COLUMNS.some((c) => c.key === key && c.sortable !== false && (c.sortViews ?? c.views).includes(view));
const firstTry = (p: { first_right: number; first_wrong: number }) => `${p.first_right} ✓ · ${p.first_wrong} ✗`;
const depthText = (r: Row) => (r.line ? (r.games === 0 ? "–" : `${medianDepth(r.depth_reached)} of ${r.line.depth_total}`) : null);
const TEXT_KEYS: SortKey[] = ["book", "chapter", "line", "colour"];

/** The row's name as the table's first text column shows it: the line, the chapter or the book. */
function Title({ r }: { r: Row }) {
  if (r.kind === "lines") {
    const l = r.line;
    if (!l) return null;
    return (
      <>
        <span className={l.is_alternative ? "italic" : ""}>{l.title}</span>
        {l.origin === "manual" && <span className={`ml-1 ${chip}`}>custom</span>}
        {l.shared && (
          <span className={`ml-1 ${chip}`} title="Every game under this line also sits under another line of the chapter — one weakness can show on many rows; the Decisions view counts it once">
            shared
          </span>
        )}
      </>
    );
  }
  if (r.kind === "chapters") {
    return (
      <>
        {r.chapter}
        <span className="ml-1 text-xs text-zinc-500">({r.count} lines)</span>
      </>
    );
  }
  return (
    <>
      {r.book}
      <span className="ml-1 text-xs text-zinc-500">({r.count} ch.)</span>
    </>
  );
}

/** The book and chapter a row sits in, the book left out when the Book filter names it already. */
const where = (r: Row, bookFiltered: boolean) => (bookFiltered ? r.chapter : `${r.book} › ${r.chapter}`);

const stateText = ([, , , state, opportunities, slips]: LineDecision) => (state === "untested" ? "untested" : state === "contested" ? "contested — not counted" : state === "clean" ? `✓ ${opportunities} of ${opportunities}` : `✗ missed ${slips} of ${opportunities}`);

/** A line's decisions in ply order, so a hole in the evidence is visible rather than implied; a
 *  line sent as its count alone says so. */
function Decisions({ e }: { e: LineEvidenceOrCount }) {
  if (!hasEvidence(e)) return <p className="text-xs text-zinc-500" data-testid="line-decisions">{e.decisions_total === 0 ? "This line has no decision of yours." : `None of its ${e.decisions_total} decisions was reached in these games.`}</p>;
  const tail = e.decisions_total - e.decisions.length;
  return (
    <ol className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs" data-testid="line-decisions">
      {e.decisions.map((d) => (
        <li key={d[0]} className={d[3] === "slipped" ? "text-red-700 dark:text-red-400" : d[3] === "clean" ? "" : "text-zinc-500"}>
          <span className="font-mono">
            {d[1]}. {d[2]}
          </span>{" "}
          {stateText(d)}
        </li>
      ))}
      {tail > 0 && <li className="text-zinc-500">then {tail} untested</li>}
    </ol>
  );
}

function Cell({ r, col, expanded, onExpand, evidence }: { r: Row; col: ColKey; expanded: boolean; onExpand: () => void; evidence: Evidence }) {
  switch (col) {
    case "colour":
      return <>{r.colour === "white" ? "White" : "Black"}</>;
    case "book":
      // A line's or chapter's book, and a line's chapter, wrap in a column of their own (the
      // player's choice over folding them under the title); the table scrolls sideways when the
      // titles are long.
      return r.kind === "books" ? <Title r={r} /> : <div className="min-w-[8rem] max-w-[12rem] text-xs text-zinc-600 dark:text-zinc-300">{r.book}</div>;
    case "chapter":
      return r.kind === "chapters" ? <Title r={r} /> : <div className="min-w-[8rem] max-w-[12rem] text-xs text-zinc-600 dark:text-zinc-300">{r.chapter}</div>;
    case "line": {
      if (!r.line) return null;
      return (
        <div className="min-w-[12rem]">
          <Title r={r} />
          <p className="text-xs text-zinc-500">
            {whoLeft(r)}
            {r.evidence !== undefined && r.evidence.decisions_total > 0 && (
              <>
                {whoLeft(r) ? " · " : ""}
                <button type="button" aria-expanded={expanded} onClick={onExpand} className="underline">
                  {expanded ? "hide decisions" : "decisions"}
                </button>
              </>
            )}
          </p>
        </div>
      );
    }
    case "games":
      return (
        <>
          {r.games}
          {r.games > 0 && <span className="block text-xs text-zinc-500">{wdl(r)}</span>}
        </>
      );
    case "slips":
      return <>{r.left_by_me}</>;
    case "last_slip":
      return <>{day(r.last_slip)}</>;
    case "trouble":
      return <span className="whitespace-nowrap">{r.line ? (troubleText(r.evidence, evidence) ?? "–") : null}</span>;
    case "tested":
      return <span className="whitespace-nowrap">{r.line ? testedText(r.evidence, evidence) : null}</span>;
    case "score":
      return <>{scoreText(r.score)}</>;
    case "faithful":
      return <>{pct(r.faithful, r.games)}</>;
    case "depth":
      return <>{depthText(r)}</>;
    case "loads":
      return <>{r.practice.loads}</>;
    case "first_right":
      return <>{firstTry(r.practice)}</>;
    case "recent":
      return <span className="whitespace-nowrap">{r.line ? recentText(r.line) : null}</span>;
    case "last_played":
      return <>{day(r.last_played)}</>;
  }
}

/** A row on a phone: the name, where it sits, who left, and the numbers in one wrapping strip. A
 *  tap on the + queues the line for a drill (and takes it out again). */
function RowCard({ r, queued, bookFiltered, onToggle, evidence }: { r: Row; queued: boolean; bookFiltered: boolean; onToggle: () => void; evidence: Evidence }) {
  const [open, setOpen] = useState(false);
  // The book is named unless the Book filter already names it on every card.
  const place = r.kind === "lines" ? where(r, bookFiltered) : r.kind === "chapters" && !bookFiltered ? r.book : null;
  const stat = (label: string, value: ReactNode, title?: string) => (
    <span title={title} className="whitespace-nowrap">
      <span className="font-medium tabular-nums text-zinc-800 dark:text-zinc-100">{value}</span> {label}
    </span>
  );
  const name = r.line?.title ?? r.chapter ?? r.book;
  return (
    <li className="border-t border-zinc-100 first:border-t-0 dark:border-zinc-800/60" data-testid={`report-row-${r.kind}`}>
      <div className="flex gap-2.5 px-3 py-2.5">
        <button type="button" aria-label={`${queued ? "Remove from the drill queue" : "Add to the drill queue"}: ${name}`} aria-pressed={queued} onClick={onToggle} className={`mt-0.5 h-7 w-7 shrink-0 self-start rounded-full border text-sm font-semibold ${queued ? "border-sky-400 bg-sky-50 text-sky-800 dark:border-sky-700 dark:bg-sky-900/40 dark:text-sky-300" : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300"}`}>
          {queued ? "✓" : "+"}
        </button>
        <div className="min-w-0 flex-1 text-sm">
          <p>
            <Title r={r} />
          </p>
          <p className="text-xs text-zinc-500">
            {r.colour === "white" ? "White" : "Black"}
            {place ? ` · ${place}` : ""}
          </p>
          {r.kind === "lines" && whoLeft(r) && <p className="text-xs text-zinc-500">{whoLeft(r)}</p>}
          <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-zinc-500">
            {stat(r.games === 1 ? "game" : "games", r.games > 0 ? `${r.games} (${wdl(r)})` : "0")}
            {stat("slips", r.left_by_me, colTitle("slips"))}
            {stat("last slip", day(r.last_slip))}
            {stat("score", scoreText(r.score), colTitle("score"))}
            {stat("faithful", pct(r.faithful, r.games), colTitle("faithful"))}
            {r.kind === "lines" && stat("depth", depthText(r), colTitle("depth"))}
            {stat("loads", r.practice.loads, colTitle("loads"))}
            {stat("first try", firstTry(r.practice), colTitle("first_right"))}
            {stat("last", day(r.last_played))}
          </p>
          {r.line && (
            <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-zinc-500">
              {troubleText(r.evidence, evidence) && stat("trouble spot", troubleText(r.evidence, evidence), colTitle("trouble"))}
              {stat("tested", testedText(r.evidence, evidence), colTitle("tested"))}
              {stat("practice", recentText(r.line), colTitle("recent"))}
              {r.evidence !== undefined && r.evidence.decisions_total > 0 && (
                <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="underline">
                  {open ? "hide decisions" : "decisions"}
                </button>
              )}
            </p>
          )}
          {open && r.evidence !== undefined && (
            <div className="mt-1">
              <Decisions e={r.evidence} />
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

function Board({ fen, colour }: { fen: string; colour: "white" | "black" }) {
  const id = "gap" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  return (
    <div className="w-full max-w-[200px] self-start">
      <Chessboard options={{ id, position: fen, allowDragging: false, boardOrientation: colour, boardStyle: { borderRadius: "4px" }, ...SQUARES }} />
    </div>
  );
}

// --- the decisions view ------------------------------------------------------------------------

const missedText = (d: DecisionRow) => `${d.slips} of ${d.opportunities}`;
const practiceText = (d: DecisionRow) => (d.practice.of === 0 ? "not yet reached in practice" : `failed here in ${d.practice.failed} of the last ${d.practice.of} first tries that reached it`);

/** What a decision's expansion needs about each line through it: the line, and the plies at which
 *  it reaches the board (more than one when the line reaches it twice). */
interface LineInfo {
  row: Row;
  plies: number[];
}

/** A walk-through is of one occurrence: a line at one of its plies. */
type Walk = { id: number; ply: number };

function DecisionLines({ d, lines, queue, onToggle }: { d: DecisionRow; lines: LineInfo[]; queue: Set<number>; onToggle: (id: number) => void }) {
  const [walk, setWalk] = useState<Walk | null>(null);
  const open = (id: number, ply: number) => walk !== null && walk.id === id && walk.ply === ply;
  // The own move number of a ply, for the label: the line's decision at that ply carries it.
  const moveAt = (row: Row, ply: number) => (hasEvidence(row.evidence) ? row.evidence.decisions.find((dec) => dec[0] === ply)?.[1] : undefined);
  return (
    <div className="space-y-2 text-xs" data-testid="decision-lines">
      <p className="text-zinc-500">
        One decision · {lines.length} {lines.length === 1 ? "line" : "lines"}. {practiceText(d)}.
      </p>
      <ul className="space-y-0.5">
        {lines.map(({ row, plies }) => (
          <li key={row.id} className="flex flex-wrap items-center gap-x-2">
            <input type="checkbox" aria-label={`Queue ${row.line?.title ?? ""}`} checked={queue.has(row.id)} onChange={() => onToggle(row.id)} />
            <span>
              <Title r={row} /> <span className="text-zinc-500">· {where(row, false)}</span>
            </span>
            <span className="text-zinc-500">
              {row.games} {row.games === 1 ? "game" : "games"} · {row.left_by_me} {row.left_by_me === 1 ? "slip" : "slips"}
            </span>
            {d.pick === row.id && <span className={chip}>drill pick</span>}
            {plies.map((ply) => (
              <button key={ply} type="button" onClick={() => setWalk(open(row.id, ply) ? null : { id: row.id, ply })} className="underline">
                {open(row.id, ply) ? "close" : plies.length > 1 ? `walk through at move ${moveAt(row, ply) ?? "?"}` : "walk through"}
              </button>
            ))}
          </li>
        ))}
      </ul>
      {walk && (
        <div className="rounded border border-zinc-200 p-3 dark:border-zinc-800">
          <LineWalkthrough key={`${walk.id}:${walk.ply}`} lineId={walk.id} initialPly={walk.ply + 1} />
        </div>
      )}
    </div>
  );
}

function Contested({ boards, titles }: { boards: ContestedBoard[]; titles: Map<number, string> }) {
  const [open, setOpen] = useState(false);
  if (boards.length === 0) return null;
  const n = boards.reduce((s, b) => s + b.alternatives.length, 0);
  return (
    <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-900/20" data-testid="contested-notice">
      <p>
        The repertoire prescribes {n} continuations at {boards.length} {boards.length === 1 ? "board" : "boards"} reached by transposition — {boards.length === 1 ? "that board is" : "these boards are"} not counted.{" "}
        <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="underline">
          {open ? "Hide" : "Show"}
        </button>
      </p>
      {open && (
        <ul className="mt-2 space-y-1 text-xs">
          {boards.map((b) => (
            <li key={`${b.colour}:${b.fen}`}>
              {b.colour === "white" ? "White" : "Black"}, move {b.own_move}:{" "}
              {b.alternatives.map((a, i) => (
                <span key={a.move}>
                  {i > 0 ? "; " : ""}
                  <span className="font-mono">{a.move}</span> in {a.line_ids.map((id) => titles.get(id) ?? `line ${id}`).join(", ")}
                </span>
              ))}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- the gaps ----------------------------------------------------------------------------------

function Card({ card, added, onAdd }: { card: GapCard; added: boolean; onAdd: (card: GapCard) => void }) {
  const ctx = card.context;
  return (
    <article className="flex flex-col gap-3 rounded-lg border border-zinc-200 p-3 sm:flex-row dark:border-zinc-800" data-testid="gap-card">
      <Board fen={card.fen} colour={card.colour} />
      <div className="min-w-0 flex-1 space-y-1 text-sm">
        <p className="font-medium">{card.family ?? "(unnamed opening)"}</p>
        {ctx ? (
          <p className="text-xs text-zinc-500">
            Left <em>
              {ctx.book} › {ctx.chapter}
            </em>{" "}
            here: book <span className="font-mono">{ctx.expected}</span>, played <span className="font-mono">{ctx.played}</span> — {ctx.count} of {card.games} games
          </p>
        ) : (
          <p className="text-xs text-zinc-500">No book reaches this board.</p>
        )}
        <p className="text-xs text-zinc-600 dark:text-zinc-300">
          {card.games} {card.games === 1 ? "game" : "games"} · {wdl(card)} · score {scoreText(card.score)}
        </p>
        {card.continuations.length > 0 && (
          <ul className="text-xs text-zinc-600 dark:text-zinc-300">
            {card.continuations.map((c) => (
              <li key={c.moves.join(" ")}>
                <span className="font-mono">{c.moves.join(" ")}</span> · {c.games} · {scoreText(c.score)}
              </li>
            ))}
          </ul>
        )}
        {card.engine ? (
          <p className="text-xs text-zinc-500">
            Engine {fmtCp(card.engine.mate_in != null ? (card.engine.mate_in > 0 ? 10000 - card.engine.mate_in : -10000 - card.engine.mate_in) : card.engine.eval)}: <span className="font-mono">{numberedLine(card.fen, card.engine.line).join(" ")}</span>
          </p>
        ) : (
          <p className="text-xs text-zinc-400">No stored engine line yet for this board.</p>
        )}
        <div className="pt-1">
          {added ? (
            <p className="text-xs text-zinc-500">Added to your repertoire. It is in the lines above.</p>
          ) : card.stem ? (
            <button type="button" onClick={() => onAdd(card)} className="rounded border border-zinc-300 px-2.5 py-1 text-xs font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
              Add to repertoire
            </button>
          ) : (
            <p className="text-xs text-zinc-500">No replayable prefix to this board: a line cannot be built from it.</p>
          )}
        </div>
      </div>
    </article>
  );
}

function Prepared({ rows }: { rows: PreparedRow[] }) {
  if (rows.length === 0) return null;
  return (
    <section className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <h3 className="text-sm font-semibold">Prepared but not played</h3>
      <p className="text-xs text-zinc-500">Games where your book had a move at the first decision and you played another.</p>
      <ul className="mt-1 space-y-1">
        {rows.map((r) => (
          <li key={`${r.fen}|${r.book_move}|${r.played}`} className="flex flex-wrap items-center gap-x-3 text-xs" data-testid="prepared-row">
            <span>
              Your book plays <span className="font-mono">{r.book_move}</span>; you played <span className="font-mono">{r.played}</span> in {r.games} {r.games === 1 ? "game" : "games"} ({wdl(r)})
              {r.book ? (
                <>
                  {" "}
                  — <em>{r.book}{r.chapter ? ` › ${r.chapter}` : ""}</em>
                </>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const TIME_CLASSES = Object.keys(TIME_CLASS_LABELS) as TimeClass[];

/** The page's state is the URL: view, sort, direction, the filters, the time class and the scope. */
function readParams(p: URLSearchParams): { view: View; sort: SortKey; decisionSort: DecisionSortKey; dir: Dir; filters: Filters; timeClass: TimeClass; gapColour: "white" | "black" | null; games: number | null } {
  const view = (VIEWS as string[]).includes(p.get("view") ?? "") ? (p.get("view") as View) : "lines";
  const raw = p.get("sort") ?? "";
  const wanted = (SORT_KEYS as string[]).includes(raw) ? (raw as SortKey) : "games";
  const sort = view !== "decisions" && sortsBy(view, wanted) ? wanted : "games";
  const decisionSort = (DECISION_SORT_KEYS as string[]).includes(raw) ? (raw as DecisionSortKey) : "study";
  const dir = p.get("dir") === "asc" ? "asc" : "desc";
  const num = (k: string) => (p.get(k) && /^\d+$/.test(p.get(k)!) ? Number(p.get(k)) : null);
  const colour = p.get("colour") === "white" || p.get("colour") === "black" ? (p.get("colour") as "white" | "black") : null;
  const origin = p.get("origin") === "course" || p.get("origin") === "manual" ? (p.get("origin") as "course" | "manual") : null;
  const tc = p.get("time_class");
  const gc = p.get("gaps");
  return {
    view,
    sort,
    decisionSort,
    dir,
    filters: { colour, book: num("book"), chapter: num("chapter"), origin },
    timeClass: tc && (TIME_CLASSES as string[]).includes(tc) ? (tc as TimeClass) : "focus",
    gapColour: gc === "white" || gc === "black" ? gc : null,
    games: num("games"),
  };
}

export default function RepertoireReport() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { view, sort, decisionSort, dir, filters, timeClass, gapColour, games } = useMemo(() => readParams(params), [params]);
  const [listed, setListed] = useState(PAGE);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  // A phone gets the rows as cards; the table needs the width of a laptop (the breakpoint is lg).
  const wide = useMediaQuery("(min-width: 1024px)", true);
  // Every control writes the URL here; a change to the rows starts the listing over, a change
  // that leaves them alone (the Gaps tabs) keeps the reader's place.
  const update = useCallback(
    (changes: Record<string, string | null>, { keepListed = false } = {}) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(changes)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      setParams(next, { replace: true });
      if (!keepListed) setListed(PAGE);
    },
    [params, setParams],
  );

  // Two reads for the page, issued together over the scope the URL names (the server clamps it to
  // the analysis window and says what it used): the lines table and both colours' gaps, then the
  // decision evidence. Both carry the generation, which a confirmed save advances so both are read
  // again and both old answers are stale at once.
  const [generation, setGeneration] = useState(0);
  const report = useApi(() => getReport(timeClass, games), [timeClass, games, generation]);
  const decisionsRead = useApi(() => getReportDecisions(timeClass, games), [timeClass, games, generation]);
  // The evidence is joined to the rows only when both answers describe the same request: while a
  // new scope is read the old pair stays together (dimmed) until either new answer lands, and from
  // then the cells show `…` until both are current, whichever landed first; `–` once the current
  // rows are in and their decisions failed. An answer for another request is never joined.
  const paired = report.data != null && decisionsRead.data != null && sameDeps(report.dataDeps, decisionsRead.dataDeps);
  const decisionsData = paired ? decisionsRead.data : null;
  const evidenceState: Evidence = paired ? "ready" : decisionsRead.error && !decisionsRead.isLoading && !report.isStale ? "failed" : "pending";
  const lines = report.data?.lines ?? null;
  const scope = report.data?.scope ?? null;
  // A stale scope in the URL (the window shrank, or a hand-typed number) is rewritten to what the
  // server used, as a sort key a view does not offer is dropped.
  useEffect(() => {
    if (scope && !report.isStale && games !== null && games !== scope.games) {
      const next = new URLSearchParams(params);
      next.set("games", String(scope.games));
      setParams(next, { replace: true });
    }
  }, [scope, report.isStale, games, params, setParams]);
  const [source, setSource] = useState<GapCard | null>(null);
  const [added, setAdded] = useState<Record<string, true>>({});
  const queueIds = useDrillQueue();
  const queue = useMemo(() => new Set(queueIds), [queueIds]);

  const flat = useMemo(() => (lines ? flatten(lines.books, decisionsData?.lines) : null), [lines, decisionsData]);
  const drillMax = lines?.drill_max_lines ?? 50;
  const tableView: TableView = view === "decisions" ? "lines" : view;
  // The lines the filters leave, in the table's order: what "shown" means for every view. A
  // chapter or book row stands for its shown lines only, so Select all shown and a container's
  // checkbox never reach a line the filters hide.
  const shownLines = useMemo(() => (flat ? sortRows(filterRows(flat.lines, filters), sort, dir) : []), [flat, filters, sort, dir]);
  const shownLineSet = useMemo(() => new Set(shownLines.map((r) => r.id)), [shownLines]);
  const shownRows = useMemo(
    () => (flat ? sortRows(filterRows(flat[tableView], filters), sort, dir).map((r) => (r.kind === "lines" ? r : { ...r, lineIds: r.lineIds.filter((id) => shownLineSet.has(id)) })) : []),
    [flat, tableView, filters, sort, dir, shownLineSet],
  );
  const books = useMemo(() => (flat ? Array.from(new Map(flat.books.map((b) => [b.book_id, b])).values()) : []), [flat]);
  const chapters = useMemo(() => (flat ? flat.chapters.filter((c) => filters.book === null || c.book_id === filters.book) : []), [flat, filters.book]);
  const lineById = useMemo(() => new Map((flat?.lines ?? []).map((r) => [r.id, r])), [flat]);
  const titles = useMemo(() => new Map((flat?.lines ?? []).map((r) => [r.id, r.line?.title ?? ""])), [flat]);

  // The decisions view: the boards, filtered through their lines, in Study order by default.
  const decisionRows = useMemo(() => (decisionsData ? sortDecisions(filterDecisions(decisionsData.rows, shownLineSet), decisionSort, dir) : []), [decisionsData, shownLineSet, decisionSort, dir]);

  const shownLineIds = useMemo(() => shownLines.map((r) => r.id), [shownLines]);
  const allShownQueued = shownLineIds.length > 0 && shownLineIds.every((id) => queue.has(id));
  // The queue is the one selection: a row's control adds its lines (in the table's order) or takes
  // them all out; it keeps what a filter hides (the player assembles the drill; the table only shows).
  const toggleRow = (r: Row) => setQueued(r.lineIds, !r.lineIds.every((id) => queue.has(id)));
  const toggleAllShown = () => setQueued(shownLineIds, !allShownQueued);
  const overCap = queueIds.length > drillMax;
  const drill = () => navigate(`/practice?drill=${queueIds.join(",")}`);

  // A new key starts ascending for text, descending for numbers; the same key again flips.
  const sortBy = (key: string) => update({ sort: key, dir: (TEXT_KEYS as string[]).includes(key) || key === "move" ? "asc" : "desc" });
  const onHeader = (key: string) => {
    const current = view === "decisions" ? decisionSort : sort;
    if (key === current) update({ dir: dir === "desc" ? "asc" : "desc" });
    else sortBy(key);
  };
  const changeView = (v: View) => {
    const keep = v === "decisions" ? (DECISION_SORT_KEYS as string[]).includes(params.get("sort") ?? "") : sortsBy(v, sort) && (params.get("sort") ?? "games") === sort;
    update({ view: v === "lines" ? null : v, ...(keep ? {} : { sort: null, dir: null }) });
  };

  const both = report.data?.gaps ?? null;
  const shown: GapsResponse | null = both ? (gapColour ?? (both.black.gaps.length > both.white.gaps.length ? "black" : "white")) === "white" ? both.white : both.black : null;
  const activeColour = shown?.colour ?? gapColour ?? "white";
  // A confirmed save marks its card and advances the generation, so the lines, the gaps and the
  // decision evidence are all read again (the new line's row and its evidence arrive as a pair);
  // closing only clears the modal, so a cancelled modal re-reads nothing. The modal calls onAdded
  // and onClose in the same turn, so nothing here may depend on state set by the other.
  const onAdded = useCallback(() => {
    if (source) setAdded((a) => ({ ...a, [source.fen]: true }));
    setGeneration((g) => g + 1);
  }, [source]);
  const closeModal = useCallback(() => setSource(null), []);

  const columns = COLUMNS.filter((c) => c.views.includes(tableView));
  const sortable: { key: string; label: string }[] =
    view === "decisions"
      ? DECISION_SORT_KEYS.map((k) => ({ key: k, label: DECISION_SORT_LABELS[k] }))
      : [{ key: "study", label: SORT_LABELS.study }, ...COLUMNS.filter((c) => isSortKey(c.key) && c.sortable !== false && (c.sortViews ?? c.views).includes(tableView)).map((c) => ({ key: c.key, label: SORT_LABELS[c.key as SortKey] }))];
  const currentSort = view === "decisions" ? decisionSort : sort;
  const th = "px-1.5 py-1.5 text-left text-xs font-medium text-zinc-500 whitespace-nowrap select-none";
  const listedRows = shownRows.length > listed ? shownRows.slice(0, listed) : shownRows;
  const listedDecisions = decisionRows.length > listed ? decisionRows.slice(0, listed) : decisionRows;
  const isQueued = (r: Row) => r.lineIds.length > 0 && r.lineIds.every((id) => queue.has(id));
  const unit = view === "lines" ? "lines" : view === "chapters" ? "chapters" : view === "books" ? "books" : "decisions";
  const shownCount = view === "decisions" ? decisionRows.length : shownRows.length;
  const listedCount = view === "decisions" ? listedDecisions.length : listedRows.length;
  const toggleExpanded = (key: string) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const decisionLines = (d: DecisionRow): LineInfo[] => linesOf(d).flatMap(({ line_id, plies }) => (lineById.has(line_id) ? [{ row: lineById.get(line_id)!, plies }] : []));
  const header = (key: string, label: string, title?: string, numeric = false) => (
    <th key={key} className={th} aria-sort={currentSort === key ? (dir === "asc" ? "ascending" : "descending") : "none"} title={title}>
      <button type="button" onClick={() => onHeader(key)} className={`inline-flex items-center gap-1 hover:text-zinc-900 dark:hover:text-zinc-100 ${numeric ? "w-full justify-end" : ""}`}>
        {label}
        {currentSort === key && <span aria-hidden>{dir === "asc" ? "▲" : "▼"}</span>}
      </button>
    </th>
  );

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Line report</h1>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs text-zinc-500">
            Games{" "}
            <select aria-label="Games" className={select} value={scope ? String(scope.games) : ""} disabled={!scope} onChange={(e) => update({ games: e.target.value })}>
              {scope ? (
                // A hand-typed number the server honoured (60, say) is listed as its own option, so
                // the control shows what the page describes.
                (scope.options.includes(scope.games) ? scope.options : [...scope.options, scope.games].sort((a, b) => a - b)).map((o) => (
                  <option key={o} value={o}>
                    {o === scope.limit ? `All (${o})` : `Last ${o}`}
                  </option>
                ))
              ) : (
                <option value="">…</option>
              )}
            </select>
          </label>
          <label className="text-xs text-zinc-500">
            Time class{" "}
            <select aria-label="Time class" className={select} value={timeClass} onChange={(e) => update({ time_class: e.target.value === "focus" ? null : e.target.value })}>
              {TIME_CLASSES.map((t) => (
                <option key={t} value={t}>
                  {TIME_CLASS_LABELS[t]}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <p className="mt-1 text-sm text-zinc-500">
        Which lines your games go through, how far, with what result, whether you played the book move, and how the line's puzzle has gone; the decisions you keep missing; then the boards where you were on your own.
        {scope ? ` Your last ${scope.games} analysable games${scope.games === scope.limit ? " (the whole analysis window)" : ""}; practice is not scoped.` : ""}
      </p>

      <div className="mb-2 mt-5 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Lines</h2>
        <div role="tablist" aria-label="View" className="flex gap-2">
          {VIEWS.map((v) => (
            <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => changeView(v)} className={tab(view === v)}>
              {v === "lines" ? "By line" : v === "chapters" ? "By chapter" : v === "books" ? "By book" : "By decision"}
            </button>
          ))}
        </div>
        <div className="flex max-w-full flex-wrap items-center gap-2 text-xs text-zinc-500">
          <select aria-label="Colour" className={select} value={filters.colour ?? ""} onChange={(e) => update({ colour: e.target.value || null })}>
            <option value="">Both colours</option>
            <option value="white">White</option>
            <option value="black">Black</option>
          </select>
          <select aria-label="Book" className={select} value={filters.book ?? ""} onChange={(e) => update({ book: e.target.value || null, chapter: null })}>
            <option value="">All books</option>
            {books.map((b) => (
              <option key={b.book_id} value={b.book_id}>
                {b.book}
              </option>
            ))}
          </select>
          {view !== "books" && (
            <select aria-label="Chapter" className={select} value={filters.chapter ?? ""} onChange={(e) => update({ chapter: e.target.value || null })}>
              <option value="">All chapters</option>
              {chapters.map((c) => (
                <option key={c.chapter_id} value={c.chapter_id!}>
                  {filters.book === null ? `${c.book} › ` : ""}
                  {c.chapter}
                </option>
              ))}
            </select>
          )}
          {(view === "lines" || view === "decisions") && (
            <select aria-label="Origin" className={select} value={filters.origin ?? ""} onChange={(e) => update({ origin: e.target.value || null })}>
              <option value="">Course and custom</option>
              <option value="course">Course</option>
              <option value="manual">Custom</option>
            </select>
          )}
          <span className="inline-flex items-center gap-1">
            <select aria-label="Sort" className={select} value={currentSort} onChange={(e) => sortBy(e.target.value)}>
              {sortable.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
            <button type="button" aria-label={dir === "asc" ? "Ascending; sort descending" : "Descending; sort ascending"} onClick={() => update({ dir: dir === "desc" ? "asc" : "desc" })} className={select}>
              <span aria-hidden>{dir === "asc" ? "▲" : "▼"}</span>
            </button>
          </span>
        </div>
      </div>
      {report.error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {report.error}
        </p>
      )}
      {report.isLoading && !report.data && <p className="text-sm text-zinc-500">Loading…</p>}
      {flat && flat.lines.length === 0 && <p className="text-sm text-zinc-500">No active line in your repertoire.</p>}
      {flat && flat.lines.length > 0 && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-3 text-sm" data-testid="queue-bar">
            {view !== "decisions" && (
              <label className="inline-flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-300">
                <input type="checkbox" aria-label="Select all shown" checked={allShownQueued} disabled={shownLineIds.length === 0} onChange={toggleAllShown} />
                Select all shown
              </label>
            )}
            <span className="text-xs text-zinc-600 dark:text-zinc-300" data-testid="queued-count">
              {queueIds.length} queued
            </span>
            <button type="button" disabled={queueIds.length === 0 || overCap} onClick={drill} className="rounded bg-zinc-900 px-3 py-1 text-xs font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900">
              Drill queue
            </button>
            {overCap && <span className="text-xs text-red-600 dark:text-red-400">{drillMax} at most</span>}
            {queueIds.length > 0 && (
              <button type="button" onClick={clearQueue} className="text-xs text-zinc-500 underline">
                Clear
              </button>
            )}
            <span className="text-xs text-zinc-500">
              {shownCount} {unit} shown
              {listedCount < shownCount ? `, first ${listedCount} listed` : ""}
            </span>
          </div>
          {evidenceState === "failed" && view !== "decisions" && (
            <p role="alert" className="mb-2 text-xs text-red-600 dark:text-red-400">
              The decisions could not be read ({decisionsRead.error}); the Trouble spot and Tested columns are empty.
            </p>
          )}
          {view === "decisions" && decisionsData && (
            <div className="mb-2 space-y-2">
              <p className="text-xs text-zinc-500">
                A decision is a board where your repertoire prescribes a move; its games are every game in scope that reached the board — matched to the line, to another, or to none — so these are not the Games column's games. One board is one row, however many lines run through it.
                {decisionsData.unknown_loads > 0 ? ` ${decisionsData.unknown_loads} practice ${decisionsData.unknown_loads === 1 ? "load" : "loads"} could not be read and count nowhere.` : ""}
              </p>
              <Contested boards={decisionsData.contested} titles={titles} />
            </div>
          )}
          {view === "decisions" ? (
            evidenceState === "pending" ? (
              <p className="text-sm text-zinc-500" data-testid="decisions-loading">Loading the decisions…</p>
            ) : evidenceState === "failed" ? (
              <p role="alert" className="text-sm text-red-600 dark:text-red-400">
                The decisions could not be read: {decisionsRead.error}
              </p>
            ) : decisionRows.length === 0 ? (
              <p className="text-sm text-zinc-500">No decision of yours was reached in these games.</p>
            ) : wide ? (
              <div className={`overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800 ${report.isStale ? "opacity-60" : ""}`}>
                <table className="w-full text-sm" data-testid="report-table" data-view="decisions">
                  <thead className="border-b border-zinc-200 dark:border-zinc-800">
                    <tr>
                      <th className={th} />
                      {header("colour", "Colour")}
                      {header("move", "Move", "Your move number and the move the repertoire prescribes")}
                      {header("missed", "Missed", "Games that reached this board and left the repertoire there, of all that reached it", true)}
                      {header("most_played", "Most played", "What you played instead, most often")}
                      {header("last_slip", "Last slip")}
                      {header("lines", "Lines", "Lines through this board: one decision, not one per line", true)}
                      {header("practice", "Practice", "First tries of the newest five loads whose trace reached this board, over every line through it", true)}
                      {header("last_played", "Last played")}
                    </tr>
                  </thead>
                  <tbody>
                    {listedDecisions.map((d) => {
                      const open = expanded.has(decisionKey(d));
                      const pick = d.pick !== null ? lineById.get(d.pick) : undefined;
                      return [
                        <tr key={decisionKey(d)} className="border-t border-zinc-100 align-top dark:border-zinc-800/60" data-testid="report-row-decisions">
                          <td className="px-1.5 py-1.5">
                            {pick && (
                              <button type="button" aria-label={`${queue.has(pick.id) ? "Remove from the drill queue" : "Add to the drill queue"}: ${pick.line?.title ?? ""}`} aria-pressed={queue.has(pick.id)} title={`Drills ${pick.book} › ${pick.chapter} › ${pick.line?.title ?? ""}`} onClick={() => setQueued([pick.id], !queue.has(pick.id))} className={smallButton}>
                                {queue.has(pick.id) ? "✓" : "+"}
                              </button>
                            )}
                          </td>
                          <td className="px-1.5 py-1.5 whitespace-nowrap">{d.colour === "white" ? "White" : "Black"}</td>
                          <td className="px-1.5 py-1.5 whitespace-nowrap">
                            <span className="font-mono">
                              {d.own_move}. {d.move}
                            </span>
                            {d.unreadable && <span className={`ml-1 ${chip}`}>unreadable</span>}
                          </td>
                          <td className="px-1.5 py-1.5 text-right tabular-nums whitespace-nowrap">
                            {missedText(d)} <span className="text-xs text-zinc-500">{pct(d.slips, d.opportunities)}</span>
                          </td>
                          <td className="px-1.5 py-1.5 font-mono whitespace-nowrap">{d.most_played ?? "–"}</td>
                          <td className="px-1.5 py-1.5 whitespace-nowrap">{day(d.last_slip)}</td>
                          <td className="px-1.5 py-1.5 text-right tabular-nums whitespace-nowrap">
                            <button type="button" aria-expanded={open} onClick={() => toggleExpanded(decisionKey(d))} className="underline">
                              {decisionLineCount(d)} {decisionLineCount(d) === 1 ? "line" : "lines"}
                            </button>
                          </td>
                          <td className="px-1.5 py-1.5 text-right tabular-nums whitespace-nowrap">{d.practice.of === 0 ? <span className="text-zinc-500">not yet reached</span> : `${d.practice.failed} ✗ of ${d.practice.of}`}</td>
                          <td className="px-1.5 py-1.5 whitespace-nowrap">{day(d.last_played)}</td>
                        </tr>,
                        open ? (
                          <tr key={`${decisionKey(d)}:lines`} className="bg-zinc-50 dark:bg-zinc-900/40">
                            <td />
                            <td colSpan={8} className="px-1.5 py-2">
                              <DecisionLines d={d} lines={decisionLines(d)} queue={queue} onToggle={(id) => setQueued([id], !queue.has(id))} />
                            </td>
                          </tr>
                        ) : null,
                      ];
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <ul className={`rounded-lg border border-zinc-200 dark:border-zinc-800 ${report.isStale ? "opacity-60" : ""}`} data-testid="report-cards" data-view="decisions">
                {listedDecisions.map((d) => {
                  const open = expanded.has(decisionKey(d));
                  const pick = d.pick !== null ? lineById.get(d.pick) : undefined;
                  return (
                    <li key={decisionKey(d)} className="border-t border-zinc-100 px-3 py-2.5 text-sm first:border-t-0 dark:border-zinc-800/60" data-testid="report-row-decisions">
                      <div className="flex gap-2.5">
                        {pick && (
                          <button type="button" aria-label={`${queue.has(pick.id) ? "Remove from the drill queue" : "Add to the drill queue"}: ${pick.line?.title ?? ""}`} aria-pressed={queue.has(pick.id)} onClick={() => setQueued([pick.id], !queue.has(pick.id))} className={`mt-0.5 h-7 w-7 shrink-0 self-start rounded-full border text-sm font-semibold ${queue.has(pick.id) ? "border-sky-400 bg-sky-50 text-sky-800 dark:border-sky-700 dark:bg-sky-900/40 dark:text-sky-300" : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300"}`}>
                            {queue.has(pick.id) ? "✓" : "+"}
                          </button>
                        )}
                        <div className="min-w-0 flex-1">
                          <p>
                            <span className="font-mono">
                              {d.own_move}. {d.move}
                            </span>{" "}
                            <span className="text-xs text-zinc-500">{d.colour === "white" ? "White" : "Black"}</span>
                          </p>
                          <p className="flex flex-wrap gap-x-3 text-xs text-zinc-500">
                            <span>
                              <span className="font-medium text-zinc-800 dark:text-zinc-100">missed {missedText(d)}</span> {pct(d.slips, d.opportunities)}
                            </span>
                            {d.most_played && <span>most played {d.most_played}</span>}
                            <span>last slip {day(d.last_slip)}</span>
                            <span>{d.practice.of === 0 ? "not yet reached in practice" : `${d.practice.failed} ✗ of ${d.practice.of} first tries`}</span>
                            <button type="button" aria-expanded={open} onClick={() => toggleExpanded(decisionKey(d))} className="underline">
                              {decisionLineCount(d)} {decisionLineCount(d) === 1 ? "line" : "lines"}
                            </button>
                          </p>
                          {open && (
                            <div className="mt-1">
                              <DecisionLines d={d} lines={decisionLines(d)} queue={queue} onToggle={(id) => setQueued([id], !queue.has(id))} />
                            </div>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )
          ) : wide ? (
            <div className={`overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800 ${report.isStale ? "opacity-60" : ""}`}>
              <table className="w-full text-sm" data-testid="report-table" data-view={view}>
                <thead className="border-b border-zinc-200 dark:border-zinc-800">
                  <tr>
                    <th className={th} />
                    {columns.map((c) =>
                      c.sortable === false ? (
                        <th key={c.key} className={th} title={c.title}>
                          {c.label}
                        </th>
                      ) : (
                        header(c.key, c.label, c.title, c.numeric)
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {listedRows.map((r) => {
                    const open = expanded.has(r.key);
                    return [
                      <tr key={r.key} className="border-t border-zinc-100 align-top dark:border-zinc-800/60" data-testid={`report-row-${r.kind}`}>
                        <td className="px-1.5 py-1.5">
                          <input type="checkbox" aria-label={`Select ${r.line?.title ?? r.chapter ?? r.book}`} checked={isQueued(r)} onChange={() => toggleRow(r)} />
                        </td>
                        {columns.map((c) => (
                          <td key={c.key} className={`px-1.5 py-1.5 ${c.numeric ? "text-right tabular-nums" : ""} ${c.key === "line" || c.key === "book" || c.key === "chapter" ? "" : "whitespace-nowrap"}`}>
                            <Cell r={r} col={c.key} expanded={open} onExpand={() => toggleExpanded(r.key)} evidence={evidenceState} />
                          </td>
                        ))}
                      </tr>,
                      open && r.evidence !== undefined ? (
                        <tr key={`${r.key}:decisions`} className="bg-zinc-50 dark:bg-zinc-900/40">
                          <td />
                          <td colSpan={columns.length} className="px-1.5 py-2">
                            <Decisions e={r.evidence} />
                          </td>
                        </tr>
                      ) : null,
                    ];
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <ul className={`rounded-lg border border-zinc-200 dark:border-zinc-800 ${report.isStale ? "opacity-60" : ""}`} data-testid="report-cards" data-view={view}>
              {listedRows.map((r) => (
                <RowCard key={r.key} r={r} queued={isQueued(r)} bookFiltered={filters.book !== null} onToggle={() => toggleRow(r)} evidence={evidenceState} />
              ))}
            </ul>
          )}
          {listedCount < shownCount && (
            <button type="button" onClick={() => setListed((n) => n + PAGE)} className="mt-2 rounded border border-zinc-300 px-2.5 py-1 text-xs font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
              Show {Math.min(PAGE, shownCount - listedCount)} more ({shownCount - listedCount} left)
            </button>
          )}
        </>
      )}

      <div className="mb-2 mt-6 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Gaps</h2>
        <div role="tablist" className="flex gap-2">
          {(["white", "black"] as const).map((c) => (
            <button key={c} type="button" role="tab" aria-selected={activeColour === c} onClick={() => update({ gaps: c }, { keepListed: true })} className={tab(activeColour === c)}>
              {c === "white" ? "White" : "Black"}
            </button>
          ))}
        </div>
      </div>
      {report.isLoading && !both && <p className="text-sm text-zinc-500">Loading…</p>}
      {shown && (
        <div className={`space-y-3 ${report.isStale ? "opacity-60" : ""}`}>
          {shown.no_book ? (
            <p className="text-sm text-zinc-500">No active White line: every White game opens outside your repertoire.</p>
          ) : (
            <>
              <Prepared rows={shown.prepared} />
              {shown.gaps.length === 0 && <p className="text-sm text-zinc-500">No gaps as {shown.colour} in the window.</p>}
              <div className="grid gap-3 md:grid-cols-2">
                {shown.gaps.map((card) => (
                  <Card key={card.fen} card={card} added={card.fen in added} onAdd={setSource} />
                ))}
              </div>
            </>
          )}
        </div>
      )}
      {source && shown && <AddLineModal key={source.fen} card={source} destinations={shown.destinations} prefillPlies={shown.prefill_plies} maxPlies={shown.max_plies} onClose={closeModal} onAdded={onAdded} />}
    </div>
  );
}
