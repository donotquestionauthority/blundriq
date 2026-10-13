import { useCallback, useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Chessboard } from "react-chessboard";
import { TIME_CLASS_LABELS } from "../blunders";
import type { TimeClass } from "../blunders";
import { AddLineModal } from "../components/AddLineModal";
import { fmtCp } from "../engine/eval";
import { useApi } from "../hooks/useApi";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { getReportGaps, getReportLines } from "../repertoire";
import type { GapCard, GapsResponse, PreparedRow, ReportTally } from "../repertoire";
import { filterRows, flatten, medianDepth, SORT_KEYS, sortRows, VIEWS } from "./reportRows";
import type { Dir, Filters, Row, SortKey, View } from "./reportRows";
import { SQUARES } from "../utils/board";
import { numberedLine } from "../utils/chess";

const select = "max-w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const tab = (active: boolean) => `rounded border px-2.5 py-1 text-xs font-medium ${active ? "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-300" : "border-zinc-300 text-zinc-500 hover:text-zinc-800 dark:border-zinc-700 dark:hover:text-zinc-200"}`;
const chip = "rounded border border-zinc-300 px-1 text-[10px] uppercase text-zinc-500 dark:border-zinc-700";

const pct = (n: number, of: number) => (of === 0 ? "–" : `${Math.round((100 * n) / of)}%`);
const wdl = (t: { wins: number; draws: number; losses: number }) => `${t.wins}–${t.draws}–${t.losses}`;
const scoreText = (s: number | null) => (s === null ? "–" : `${Math.round(100 * s)}%`);

/** "opponent left 9 · you left 2 · ran out 1 · ended 1", the nonzero parts only. */
function whoLeft(t: ReportTally): string {
  const parts: string[] = [];
  if (t.left_by_opponent) parts.push(`opponent left ${t.left_by_opponent}`);
  if (t.left_by_me) parts.push(`you left ${t.left_by_me}`);
  if (t.ran_out) parts.push(`ran out ${t.ran_out}`);
  if (t.ended) parts.push(`ended in book ${t.ended}`);
  return parts.join(" · ");
}

function slipText(t: ReportTally): string | null {
  if (!t.slip) return null;
  const s = t.slip;
  return `You left at your move ${s.own_move} in ${s.count} of ${t.left_by_me}: book ${s.expected}, you played ${s.played}.`;
}

/** Rows listed before "Show more": the whole report is thousands of lines, and a phone paints
 *  them one card at a time. The filters, Select all shown and the drill see every shown row. */
export const PAGE = 200;

/** `views`: where the column is; `sortViews`: where its sort is offered (a line row carries its
 *  book and chapter under the title rather than in columns of their own, but still sorts by them). */
const COLUMNS: { key: SortKey; label: string; views: View[]; sortViews?: View[]; numeric?: boolean; title?: string }[] = [
  { key: "colour", label: "Colour", views: VIEWS },
  { key: "book", label: "Book", views: ["chapters", "books"], sortViews: VIEWS },
  { key: "chapter", label: "Chapter", views: ["chapters"], sortViews: ["lines", "chapters"] },
  { key: "line", label: "Line", views: ["lines"] },
  { key: "games", label: "Games", views: VIEWS, numeric: true, title: "Games through this line, then wins–draws–losses" },
  { key: "score", label: "Score", views: VIEWS, numeric: true, title: "Wins and half the draws, over the decided games" },
  { key: "faithful", label: "Faithful", views: VIEWS, numeric: true, title: "Games where you never left the line" },
  { key: "depth", label: "Depth", views: ["lines"], numeric: true, title: "How many of your moves in the line the games played, median" },
  { key: "loads", label: "Loads", views: VIEWS, numeric: true, title: "Times the line's puzzle was loaded in the solver" },
  { key: "first_right", label: "First try", views: VIEWS, numeric: true, title: "Loads whose first attempt was right ✓ and wrong ✗; sorts by the right ones" },
  { key: "last_played", label: "Last played", views: VIEWS },
];

const colTitle = (key: SortKey) => COLUMNS.find((c) => c.key === key)?.title;
const firstTry = (p: { first_right: number; first_wrong: number }) => `${p.first_right} ✓ · ${p.first_wrong} ✗`;
const depthText = (r: Row) => (r.line ? (r.games === 0 ? "–" : `${medianDepth(r.depth_reached)} of ${r.line.depth_total}`) : null);
const lastPlayed = (r: Row) => (r.last_played ? r.last_played.slice(0, 10) : "–");

/** The row's name as the table's first text column shows it: the line, the chapter or the book. */
function Title({ r }: { r: Row }) {
  if (r.kind === "lines" && r.line) {
    const l = r.line;
    return (
      <>
        <span className={l.is_alternative ? "italic" : ""}>{l.title}</span>
        {l.origin === "manual" && <span className={`ml-1 ${chip}`}>custom</span>}
        {l.shared && <span className={`ml-1 ${chip}`}>shared</span>}
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

function Cell({ r, col, bookFiltered }: { r: Row; col: SortKey; bookFiltered: boolean }) {
  switch (col) {
    case "colour":
      return <>{r.colour === "white" ? "White" : "Black"}</>;
    case "book":
      return r.kind === "books" ? <Title r={r} /> : <div className="min-w-[12rem] text-xs text-zinc-600 dark:text-zinc-300">{r.book}</div>;
    case "chapter":
      return <Title r={r} />;
    case "line": {
      if (!r.line) return null;
      const slip = slipText(r);
      return (
        <div className="min-w-[16rem]">
          <Title r={r} />
          <p className="text-xs text-zinc-500">{where(r, bookFiltered)}</p>
          <p className="text-xs text-zinc-500">{[whoLeft(r), slip].filter(Boolean).join(" · ")}</p>
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
    case "last_played":
      return <>{lastPlayed(r)}</>;
  }
}

/** A row on a phone: the name, where it sits, who left, and the numbers in one wrapping strip. */
function RowCard({ r, checked, bookFiltered, onToggle }: { r: Row; checked: boolean; bookFiltered: boolean; onToggle: () => void }) {
  const slip = r.kind === "lines" ? slipText(r) : null;
  // The book is named unless the Book filter already names it on every card.
  const place = r.kind === "lines" ? where(r, bookFiltered) : r.kind === "chapters" && !bookFiltered ? r.book : null;
  const stat = (label: string, value: ReactNode, title?: string) => (
    <span title={title} className="whitespace-nowrap">
      <span className="font-medium tabular-nums text-zinc-800 dark:text-zinc-100">{value}</span> {label}
    </span>
  );
  return (
    <li className="border-t border-zinc-100 first:border-t-0 dark:border-zinc-800/60" data-testid={`report-row-${r.kind}`}>
      <label className="flex gap-2.5 px-3 py-2.5">
        <input type="checkbox" className="mt-1 shrink-0 self-start" aria-label={`Select ${r.line?.title ?? r.chapter ?? r.book}`} checked={checked} onChange={onToggle} />
        <div className="min-w-0 flex-1 text-sm">
          <p>
            <Title r={r} />
          </p>
          <p className="text-xs text-zinc-500">
            {r.colour === "white" ? "White" : "Black"}
            {place ? ` · ${place}` : ""}
          </p>
          {r.kind === "lines" && (whoLeft(r) || slip) && <p className="text-xs text-zinc-500">{[whoLeft(r), slip].filter(Boolean).join(" · ")}</p>}
          <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-zinc-500">
            {stat(r.games === 1 ? "game" : "games", r.games > 0 ? `${r.games} (${wdl(r)})` : "0")}
            {stat("score", scoreText(r.score), colTitle("score"))}
            {stat("faithful", pct(r.faithful, r.games), colTitle("faithful"))}
            {r.kind === "lines" && stat("depth", depthText(r), colTitle("depth"))}
            {stat("loads", r.practice.loads, colTitle("loads"))}
            {stat("first try", firstTry(r.practice), colTitle("first_right"))}
            {stat("last", lastPlayed(r))}
          </p>
        </div>
      </label>
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

/** The page's state is the URL: view, sort, direction, the filters and the time class. */
function readParams(p: URLSearchParams): { view: View; sort: SortKey; dir: Dir; filters: Filters; timeClass: TimeClass; gapColour: "white" | "black" | null } {
  const view = (VIEWS as string[]).includes(p.get("view") ?? "") ? (p.get("view") as View) : "lines";
  const sort = (SORT_KEYS as string[]).includes(p.get("sort") ?? "") ? (p.get("sort") as SortKey) : "games";
  const dir = p.get("dir") === "asc" ? "asc" : "desc";
  const num = (k: string) => (p.get(k) && /^\d+$/.test(p.get(k)!) ? Number(p.get(k)) : null);
  const colour = p.get("colour") === "white" || p.get("colour") === "black" ? (p.get("colour") as "white" | "black") : null;
  const origin = p.get("origin") === "course" || p.get("origin") === "manual" ? (p.get("origin") as "course" | "manual") : null;
  const tc = p.get("time_class");
  const gc = p.get("gaps");
  return {
    view,
    sort,
    dir,
    filters: { colour, book: num("book"), chapter: num("chapter"), origin },
    timeClass: tc && (TIME_CLASSES as string[]).includes(tc) ? (tc as TimeClass) : "focus",
    gapColour: gc === "white" || gc === "black" ? gc : null,
  };
}

export default function RepertoireReport() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { view, sort, dir, filters, timeClass, gapColour } = useMemo(() => readParams(params), [params]);
  const [listed, setListed] = useState(PAGE);
  // A phone gets the rows as cards; the table needs the width of a laptop (the breakpoint is lg).
  const wide = useMediaQuery("(min-width: 1024px)", true);
  const update = useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(changes)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      setParams(next, { replace: true });
      setListed(PAGE);
    },
    [params, setParams],
  );

  const lines = useApi(() => getReportLines(timeClass), [timeClass]);
  const gapsRead = useApi(() => Promise.all([getReportGaps("white", timeClass), getReportGaps("black", timeClass)]), [timeClass]);
  const [source, setSource] = useState<GapCard | null>(null);
  const [added, setAdded] = useState<Record<string, true>>({});
  const [checkedRaw, setChecked] = useState<Set<number>>(() => new Set());

  const flat = useMemo(() => (lines.data ? flatten(lines.data.books) : null), [lines.data]);
  const drillMax = lines.data?.drill_max_lines ?? 50;
  // The lines the filters leave, in the table's order: what "shown" means for every view. A
  // chapter or book row stands for its shown lines only, so Select all shown and a container's
  // checkbox never reach a line the filters hide.
  const shownLines = useMemo(() => (flat ? sortRows(filterRows(flat.lines, filters), sort, dir) : []), [flat, filters, sort, dir]);
  const shownLineSet = useMemo(() => new Set(shownLines.map((r) => r.id)), [shownLines]);
  const shownRows = useMemo(
    () => (flat ? sortRows(filterRows(flat[view], filters), sort, dir).map((r) => (r.kind === "lines" ? r : { ...r, lineIds: r.lineIds.filter((id) => shownLineSet.has(id)) })) : []),
    [flat, view, filters, sort, dir, shownLineSet],
  );
  const books = useMemo(() => (flat ? Array.from(new Map(flat.books.map((b) => [b.book_id, b])).values()) : []), [flat]);
  const chapters = useMemo(() => (flat ? flat.chapters.filter((c) => filters.book === null || c.book_id === filters.book) : []), [flat, filters.book]);
  // The selection is the checked ids among the shown lines, derived, never pruned in an effect:
  // a line a filter hides, or that is no longer a row, is not selected and not drilled; it is
  // checked again the moment it is shown again.
  const checked = useMemo(() => new Set(Array.from(checkedRaw).filter((id) => shownLineSet.has(id))), [checkedRaw, shownLineSet]);

  const shownLineIds = useMemo(() => shownLines.map((r) => r.id), [shownLines]);
  const allShownChecked = shownLineIds.length > 0 && shownLineIds.every((id) => checked.has(id));
  const toggleRow = (r: Row) => {
    setChecked((c) => {
      const next = new Set(c);
      const on = r.lineIds.every((id) => next.has(id));
      for (const id of r.lineIds) {
        if (on) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  };
  const toggleAllShown = () => {
    setChecked((c) => {
      const next = new Set(c);
      for (const id of shownLineIds) {
        if (allShownChecked) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  };
  // The drill takes the checked lines in the order the table shows them.
  const drillIds = useMemo(() => shownLineIds.filter((id) => checked.has(id)), [shownLineIds, checked]);
  const overCap = drillIds.length > drillMax;

  // A new key starts ascending for text, descending for numbers; the same key again flips.
  const sortBy = (key: SortKey) => update({ sort: key, dir: key === "book" || key === "chapter" || key === "line" || key === "colour" ? "asc" : "desc" });
  const onHeader = (key: SortKey) => {
    if (key === sort) update({ dir: dir === "desc" ? "asc" : "desc" });
    else sortBy(key);
  };

  const both = gapsRead.data;
  const shown: GapsResponse | null = both ? (gapColour ?? (both[1].gaps.length > both[0].gaps.length ? "black" : "white")) === "white" ? both[0] : both[1] : null;
  const activeColour = shown?.colour ?? gapColour ?? "white";
  const refetchLines = lines.refetch;
  const refetchGaps = gapsRead.refetch;
  const onAdded = useCallback(() => {
    if (source) setAdded((a) => ({ ...a, [source.fen]: true }));
    refetchLines();
  }, [source, refetchLines]);
  const closeModal = useCallback(() => {
    setSource(null);
    refetchGaps();
  }, [refetchGaps]);

  const columns = COLUMNS.filter((c) => c.views.includes(view));
  const sortable = COLUMNS.filter((c) => (c.sortViews ?? c.views).includes(view));
  const th = "px-1.5 py-1.5 text-left text-xs font-medium text-zinc-500 whitespace-nowrap select-none";
  const listedRows = shownRows.length > listed ? shownRows.slice(0, listed) : shownRows;
  const isChecked = (r: Row) => r.lineIds.length > 0 && r.lineIds.every((id) => checked.has(id));
  const unit = view === "lines" ? "lines" : view === "chapters" ? "chapters" : "books";

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Line report</h1>
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
      <p className="mt-1 text-sm text-zinc-500">Which lines your games go through, how far, with what result, whether you played the book move, and how the line's puzzle has gone; then the boards where you were on your own.</p>

      <div className="mb-2 mt-5 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Lines</h2>
        <div role="tablist" aria-label="View" className="flex gap-2">
          {VIEWS.map((v) => (
            <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => update({ view: v === "lines" ? null : v })} className={tab(view === v)}>
              {v === "lines" ? "By line" : v === "chapters" ? "By chapter" : "By book"}
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
          {view === "lines" && (
            <select aria-label="Origin" className={select} value={filters.origin ?? ""} onChange={(e) => update({ origin: e.target.value || null })}>
              <option value="">Course and custom</option>
              <option value="course">Course</option>
              <option value="manual">Custom</option>
            </select>
          )}
          <span className="inline-flex items-center gap-1">
            <select aria-label="Sort" className={select} value={sort} onChange={(e) => sortBy(e.target.value as SortKey)}>
              {sortable.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
            <button type="button" aria-label={dir === "asc" ? "Ascending; sort descending" : "Descending; sort ascending"} onClick={() => update({ dir: dir === "desc" ? "asc" : "desc" })} className={`${select} px-2`}>
              <span aria-hidden>{dir === "asc" ? "▲" : "▼"}</span>
            </button>
          </span>
        </div>
      </div>
      {lines.error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {lines.error}
        </p>
      )}
      {lines.isLoading && !lines.data && <p className="text-sm text-zinc-500">Loading…</p>}
      {flat && flat.lines.length === 0 && <p className="text-sm text-zinc-500">No active line in your repertoire.</p>}
      {flat && flat.lines.length > 0 && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-3 text-sm">
            <label className="inline-flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-300">
              <input type="checkbox" aria-label="Select all shown" checked={allShownChecked} disabled={shownLineIds.length === 0} onChange={toggleAllShown} />
              Select all shown
            </label>
            <button type="button" disabled={drillIds.length === 0 || overCap} onClick={() => navigate(`/practice?drill=${drillIds.join(",")}`)} className="rounded bg-zinc-900 px-3 py-1 text-xs font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900">
              Drill {drillIds.length} {drillIds.length === 1 ? "line" : "lines"}
            </button>
            {overCap && <span className="text-xs text-red-600 dark:text-red-400">{drillMax} at most</span>}
            {checkedRaw.size > 0 && (
              <button type="button" onClick={() => setChecked(new Set())} className="text-xs text-zinc-500 underline">
                Clear
              </button>
            )}
            <span className="text-xs text-zinc-500">
              {shownRows.length} {unit} shown
              {listedRows.length < shownRows.length ? `, first ${listedRows.length} listed` : ""}
            </span>
          </div>
          {wide ? (
            <div className={`overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800 ${lines.isStale ? "opacity-60" : ""}`}>
              <table className="w-full text-sm" data-testid="report-table" data-view={view}>
                <thead className="border-b border-zinc-200 dark:border-zinc-800">
                  <tr>
                    <th className={th} />
                    {columns.map((c) => (
                      <th key={c.key} className={th} aria-sort={sort === c.key ? (dir === "asc" ? "ascending" : "descending") : "none"} title={c.title}>
                        <button type="button" onClick={() => onHeader(c.key)} className="inline-flex items-center gap-1 hover:text-zinc-900 dark:hover:text-zinc-100">
                          {c.label}
                          {sort === c.key && <span aria-hidden>{dir === "asc" ? "▲" : "▼"}</span>}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {listedRows.map((r) => (
                    <tr key={r.key} className="border-t border-zinc-100 align-top dark:border-zinc-800/60" data-testid={`report-row-${r.kind}`}>
                      <td className="px-1.5 py-1.5">
                        <input type="checkbox" aria-label={`Select ${r.line?.title ?? r.chapter ?? r.book}`} checked={isChecked(r)} onChange={() => toggleRow(r)} />
                      </td>
                      {columns.map((c) => (
                        <td key={c.key} className={`px-1.5 py-1.5 ${c.numeric ? "text-right tabular-nums" : ""} ${c.key === "line" || c.key === "book" || c.key === "chapter" ? "" : "whitespace-nowrap"}`}>
                          <Cell r={r} col={c.key} bookFiltered={filters.book !== null} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <ul className={`rounded-lg border border-zinc-200 dark:border-zinc-800 ${lines.isStale ? "opacity-60" : ""}`} data-testid="report-cards" data-view={view}>
              {listedRows.map((r) => (
                <RowCard key={r.key} r={r} checked={isChecked(r)} bookFiltered={filters.book !== null} onToggle={() => toggleRow(r)} />
              ))}
            </ul>
          )}
          {listedRows.length < shownRows.length && (
            <button type="button" onClick={() => setListed((n) => n + PAGE)} className="mt-2 rounded border border-zinc-300 px-2.5 py-1 text-xs font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
              Show {Math.min(PAGE, shownRows.length - listedRows.length)} more ({shownRows.length - listedRows.length} left)
            </button>
          )}
        </>
      )}

      <div className="mb-2 mt-6 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Gaps</h2>
        <div role="tablist" className="flex gap-2">
          {(["white", "black"] as const).map((c) => (
            <button key={c} type="button" role="tab" aria-selected={activeColour === c} onClick={() => update({ gaps: c })} className={tab(activeColour === c)}>
              {c === "white" ? "White" : "Black"}
            </button>
          ))}
        </div>
      </div>
      {gapsRead.error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {gapsRead.error}
        </p>
      )}
      {gapsRead.isLoading && !both && <p className="text-sm text-zinc-500">Loading…</p>}
      {shown && (
        <div className={`space-y-3 ${gapsRead.isStale ? "opacity-60" : ""}`}>
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
      {source && shown && <AddLineModal key={source.fen} card={source} prefillPlies={shown.prefill_plies} maxPlies={shown.max_plies} onClose={closeModal} onAdded={onAdded} />}
    </div>
  );
}
