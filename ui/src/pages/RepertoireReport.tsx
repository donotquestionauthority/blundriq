import { useCallback, useId, useState } from "react";
import { Link } from "react-router";
import { Chessboard } from "react-chessboard";
import { TIME_CLASS_LABELS } from "../blunders";
import type { TimeClass } from "../blunders";
import { CreatePuzzleModal } from "../components/CreatePuzzleModal";
import type { CreatePuzzleSource } from "../components/CreatePuzzleModal";
import { fmtCp } from "../engine/eval";
import { useApi } from "../hooks/useApi";
import { getReportGaps, getReportLines } from "../repertoire";
import type { GapCard, GapsResponse, PreparedRow, ReportBook, ReportChapter, ReportLine, ReportTally } from "../repertoire";
import { SQUARES } from "../utils/board";
import { numberedLine } from "../utils/chess";

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const linkClass = "text-zinc-600 underline hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100";
const tab = (active: boolean) => `rounded border px-2.5 py-1 text-xs font-medium ${active ? "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-300" : "border-zinc-300 text-zinc-500 hover:text-zinc-800 dark:border-zinc-700 dark:hover:text-zinc-200"}`;

const pct = (n: number, of: number) => (of === 0 ? "–" : `${Math.round((100 * n) / of)}%`);
const wdl = (t: { wins: number; draws: number; losses: number }) => `${t.wins}–${t.draws}–${t.losses}`;
const practiceScope = (kind: "book" | "chapter" | "line", id: number) => `/practice?type=repertoire&subtype=${kind}:${id}`;

/** The depth histogram's median own move, from {own moves: games}. */
function medianDepth(reached: Record<string, number>): number {
  const entries = Object.entries(reached)
    .map(([k, v]) => [Number(k), v] as const)
    .sort((a, b) => a[0] - b[0]);
  const total = entries.reduce((n, [, v]) => n + v, 0);
  let seen = 0;
  for (const [k, v] of entries) {
    seen += v;
    if (seen * 2 >= total) return k;
  }
  return 0;
}

/** "opponent left 9 · you left 2 · ran out 1 · ended 1", the nonzero parts only. */
function whoLeft(t: ReportTally): string {
  const parts: string[] = [];
  if (t.left_by_opponent) parts.push(`opponent left ${t.left_by_opponent}`);
  if (t.left_by_me) parts.push(`you left ${t.left_by_me}`);
  if (t.ran_out) parts.push(`ran out ${t.ran_out}`);
  if (t.ended) parts.push(`ended in book ${t.ended}`);
  return parts.join(" · ");
}

function WhoLeft({ t }: { t: ReportTally }) {
  const text = whoLeft(t);
  return text ? <p className="text-xs text-zinc-500">{text}</p> : null;
}

function Slip({ t }: { t: ReportTally }) {
  if (!t.slip) return null;
  const s = t.slip;
  return (
    <p className="text-xs text-zinc-500">
      You left at your move {s.own_move} in {s.count} of {t.left_by_me}: book <span className="font-mono">{s.expected}</span>, you played <span className="font-mono">{s.played}</span>.
    </p>
  );
}

function Drill({ kind, id, puzzles }: { kind: "book" | "chapter" | "line"; id: number; puzzles: number }) {
  if (puzzles === 0) return <span className="text-xs text-zinc-400">no puzzles yet</span>;
  return (
    <Link to={practiceScope(kind, id)} className={`text-xs ${linkClass}`}>
      Drill ({puzzles})
    </Link>
  );
}

function Numbers({ t }: { t: ReportTally }) {
  return (
    <span className="text-xs text-zinc-600 dark:text-zinc-300">
      {t.games} {t.games === 1 ? "game" : "games"} · {wdl(t)} · score {t.score === null ? "–" : `${Math.round(100 * t.score)}%`} · faithful {pct(t.faithful, t.games)}
    </span>
  );
}

function DepthBar({ line }: { line: ReportLine }) {
  const median = medianDepth(line.depth_reached);
  const total = Math.max(line.depth_total, 1);
  return (
    <span className="inline-flex items-center gap-2 text-xs text-zinc-500" title="How many of your moves in the line the games played, median">
      <span className="inline-block h-1.5 w-20 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800" aria-hidden>
        <span className="block h-full bg-sky-500" style={{ width: `${Math.min(100, Math.round((100 * median) / total))}%` }} />
      </span>
      {median} of {line.depth_total} of your moves
    </span>
  );
}

function LineRow({ line }: { line: ReportLine }) {
  return (
    <li className="border-t border-zinc-100 py-2 pl-6 dark:border-zinc-800/60" data-testid="report-line">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`text-sm ${line.is_alternative ? "italic" : ""}`}>{line.title}</span>
        {line.shared && <span className="rounded border border-zinc-300 px-1 text-[10px] uppercase text-zinc-500 dark:border-zinc-700">shared</span>}
        <Numbers t={line} />
        <DepthBar line={line} />
        <Drill kind="line" id={line.id} puzzles={line.puzzles} />
      </div>
      <WhoLeft t={line} />
      <Slip t={line} />
    </li>
  );
}

function ChapterRow({ chapter }: { chapter: ReportChapter }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-t border-zinc-200 py-2 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="text-sm font-medium">
          {open ? "▾" : "▸"} {chapter.title}
        </button>
        <Numbers t={chapter} />
        <Drill kind="chapter" id={chapter.id} puzzles={chapter.puzzles} />
      </div>
      <div className="pl-5">
        <WhoLeft t={chapter} />
        <Slip t={chapter} />
      </div>
      {open && (
        <ul className="mt-1">
          {chapter.lines.map((ln) => (
            <LineRow key={ln.id} line={ln} />
          ))}
        </ul>
      )}
    </li>
  );
}

function BookSection({ book }: { book: ReportBook }) {
  return (
    <section className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 className="text-base font-semibold">
          {book.title} <span className="text-xs font-normal text-zinc-500">({book.color})</span>
        </h3>
        <Numbers t={book} />
        <Drill kind="book" id={book.id} puzzles={book.puzzles} />
      </div>
      <WhoLeft t={book} />
      <Slip t={book} />
      <ul className="mt-2">
        {book.chapters.map((ch) => (
          <ChapterRow key={ch.id} chapter={ch} />
        ))}
      </ul>
    </section>
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

function hiddenNotice(saved: boolean): string {
  return saved
    ? "Saved. Practice will not serve it: the board is dismissed on Blunders or your repertoire prescribes a different move inside the line."
    : "A puzzle is on this board but Practice hides it: a Blunders dismissal, or your repertoire prescribes a different move inside it.";
}

function Card({ card, savedHere, onCreate }: { card: GapCard; savedHere: boolean; onCreate: (card: GapCard) => void }) {
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
          {card.games} {card.games === 1 ? "game" : "games"} · {wdl(card)} · score {card.score === null ? "–" : `${Math.round(100 * card.score)}%`}
        </p>
        {card.continuations.length > 0 && (
          <ul className="text-xs text-zinc-600 dark:text-zinc-300">
            {card.continuations.map((c) => (
              <li key={c.moves.join(" ")}>
                <span className="font-mono">{c.moves.join(" ")}</span> · {c.games} · {c.score === null ? "–" : `${Math.round(100 * c.score)}%`}
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
          {card.puzzle === null ? (
            <button type="button" onClick={() => onCreate(card)} className="rounded border border-zinc-300 px-2.5 py-1 text-xs font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
              Add as custom puzzle
            </button>
          ) : card.puzzle.playable ? (
            <Link to={`/practice?puzzle=${card.puzzle.id}`} className={`text-xs ${linkClass}`}>
              Drill
            </Link>
          ) : (
            <p className="text-xs text-zinc-500">{hiddenNotice(savedHere)}</p>
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
            {r.line_id !== null && <Drill kind="line" id={r.line_id} puzzles={r.puzzles} />}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function RepertoireReport() {
  const [timeClass, setTimeClass] = useState<TimeClass>("focus");
  const [colour, setColour] = useState<"white" | "black" | null>(null);
  const [source, setSource] = useState<{ card: GapCard; src: CreatePuzzleSource } | null>(null);
  const lines = useApi(() => getReportLines(timeClass), [timeClass]);
  const gapsRead = useApi(() => Promise.all([getReportGaps("white", timeClass), getReportGaps("black", timeClass)]), [timeClass]);
  // Cards saved on this page carry their new puzzle at once (the save's own answer); a later read
  // says the same thing, and the card still says "Saved." for the visit.
  const [local, setLocal] = useState<Record<string, GapCard["puzzle"]>>({});

  const both = gapsRead.data;
  const shown: GapsResponse | null = both ? (colour ?? (both[1].gaps.length > both[0].gaps.length ? "black" : "white")) === "white" ? both[0] : both[1] : null;
  const activeColour = shown?.colour ?? colour ?? "white";

  const prefillPlies = both ? both[0].prefill_plies : 0;
  const openCreate = useCallback((card: GapCard) => {
    const engineLine = card.engine?.line ?? [];
    const title = `${card.family ?? "(unnamed opening)"}: ${card.context ? `after ${card.context.played}` : card.arrived_by ? `1.${card.arrived_by}` : "no book here"}`;
    const description = card.context ? `The engine's line from the board where the opponent left ${card.context.book} › ${card.context.chapter} (${card.context.expected} expected, ${card.context.played} played).` : "The engine's line from the board where no book of yours reaches.";
    setSource({
      card,
      src: { source: "gap", fen: card.fen, color: card.colour === "white" ? "w" : "b", engineLine, prefillPlies, title, description },
    });
  }, [prefillPlies]);

  const refetchGaps = gapsRead.refetch;
  const onCreated = useCallback(
    (made: { id: number; visible: boolean }) => {
      if (!source) return;
      setLocal((l) => ({ ...l, [source.card.fen]: { id: made.id, playable: made.visible } }));
    },
    [source],
  );
  const closeModal = useCallback(() => {
    setSource(null);
    // A 409 means another tab or the pipeline took the board after this page loaded: re-read.
    refetchGaps();
  }, [refetchGaps]);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Line report</h1>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs text-zinc-500">
            Time class{" "}
            <select aria-label="Time class" className={select} value={timeClass} onChange={(e) => setTimeClass(e.target.value as TimeClass)}>
              {(Object.keys(TIME_CLASS_LABELS) as TimeClass[]).map((t) => (
                <option key={t} value={t}>
                  {TIME_CLASS_LABELS[t]}
                </option>
              ))}
            </select>
          </label>
          <Link to="/practice?type=custom&subtype=gap" className={`text-sm ${linkClass}`}>
            Drill the gaps →
          </Link>
        </div>
      </div>
      <p className="mt-1 text-sm text-zinc-500">Which lines your games go through, how far, with what result and whether you played the book move; then the boards where you were on your own.</p>

      <h2 className="mb-2 mt-5 text-lg font-semibold">Lines</h2>
      {lines.error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {lines.error}
        </p>
      )}
      {lines.isLoading && !lines.data && <p className="text-sm text-zinc-500">Loading…</p>}
      {lines.data && lines.data.books.length === 0 && <p className="text-sm text-zinc-500">No games in the window go through an active line.</p>}
      <div className={`space-y-3 ${lines.isStale ? "opacity-60" : ""}`}>{lines.data?.books.map((b) => <BookSection key={b.id} book={b} />)}</div>

      <div className="mb-2 mt-6 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Gaps</h2>
        <div role="tablist" className="flex gap-2">
          {(["white", "black"] as const).map((c) => (
            <button key={c} type="button" role="tab" aria-selected={activeColour === c} onClick={() => setColour(c)} className={tab(activeColour === c)}>
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
                {shown.gaps.map((card) => {
                  const savedHere = card.fen in local;
                  return <Card key={card.fen} card={savedHere ? { ...card, puzzle: local[card.fen] } : card} savedHere={savedHere} onCreate={openCreate} />;
                })}
              </div>
            </>
          )}
        </div>
      )}
      {source && <CreatePuzzleModal key={source.card.fen} source={source.src} onClose={closeModal} onCreated={onCreated} />}
    </div>
  );
}
