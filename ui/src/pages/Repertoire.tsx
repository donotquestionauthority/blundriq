import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api";
import { LineWalkthrough } from "../components/PositionCard/LineReaderPanel";
import { RefusalDialog, Toggle } from "../components/RepertoireBits";
import { useApi } from "../hooks/useApi";
import { conflictsPath, getBooks, getConflicts, getSections, provenanceLabel, refusalOf, setActive } from "../repertoire";
import type { Refusal, RepertoireBook, RepertoireLine, RepertoireSection } from "../repertoire";
import { numbered } from "../utils/chess";

/**
 * The repertoire as stored: books, their chapters, their lines. Lines come in through
 * `pipeline import-repertoire`; here each book, chapter and line can be switched on or off
 * (off is the one remedy for a deviation that keeps counting against a line that should not
 * be in play), and a line opens as a walk-through with the author's notes. A toggle is
 * applied optimistically and reverted, with the error shown, when the server refuses it.
 *
 * Switching on is gated by the server. A refused line reverts and opens the refusal dialog
 * (which position, which lines disagree, a link to the Conflicts page). A chapter or book
 * that comes on with lines `held_back` shows the server's state instead of the optimistic
 * one — the container on, those lines off — with a notice naming them; the held-back ids
 * are kept until the sections are on screen (a book toggled before its sections loaded
 * applies them when they land), and a sections response that was in flight when the toggle
 * answered is dropped and fetched again, so it cannot restore the old flags. The contested
 * count under the title is fetched again after every successful toggle.
 */

type Opened = { refusal: Refusal; orientation: "white" | "black"; from: HTMLButtonElement };

function Book({ book, onError, onOpenLine, onRefusal, onToggled }: { book: RepertoireBook; onError: (e: string | null) => void; onOpenLine: (line: RepertoireLine) => void; onRefusal: (o: Opened) => void; onToggled: () => void }) {
  const [open, setOpen] = useState(false);
  const [active, setActiveState] = useState(book.active);
  const [sections, setSections] = useState<RepertoireSection[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Set<string>>(() => new Set());
  const [held, setHeld] = useState<Refusal[]>([]);
  const [reload, setReload] = useState(0);
  const latest = useRef(0);

  // Sections load on first expansion, and again when a toggle asked for it (`reload`); a
  // stale response never renders under a newer request. Lines the server held back since
  // are applied to whatever arrives.
  useEffect(() => {
    if (!open || sections) return;
    const req = ++latest.current;
    getSections(book.book_id).then(
      (r) => {
        if (req === latest.current) {
          setSections(r.sections);
          setLoadError(null);
        }
      },
      (e: unknown) => {
        if (req === latest.current) setLoadError(e instanceof Error ? e.message : String(e));
      },
    );
  }, [open, sections, book.book_id, reload]);

  const heldIds = held.map((r) => r.line_id);
  const shown = sections?.map((c) => ({ ...c, lines: c.lines.map((l) => (heldIds.includes(l.id) ? { ...l, active: false } : l)) })) ?? null;

  async function flip(kind: "books" | "chapters" | "lines", id: number, on: boolean, apply: (on: boolean) => void, el: HTMLButtonElement) {
    const key = `${kind}:${id}`;
    setBusy((b) => new Set(b).add(key));
    onError(null);
    setHeld([]);
    apply(on);
    try {
      const r = await setActive(kind, id, on);
      const heldBack = r.held_back ?? [];
      if (heldBack.length > 0) {
        // The server's state, not the optimistic one: the held-back lines are off. An older
        // sections response must not bring them back, so it is dropped and fetched again.
        setHeld(heldBack);
        latest.current++;
        setReload((n) => n + 1);
        setSections((s) => s?.map((c) => ({ ...c, lines: c.lines.map((l) => (heldBack.some((h) => h.line_id === l.id) ? { ...l, active: false } : l)) })) ?? s);
      }
      onToggled();
    } catch (e) {
      apply(!on);
      const refusal = refusalOf(e);
      if (refusal) onRefusal({ refusal, orientation: book.color, from: el });
      else onError(e instanceof ApiError ? e.message : "Could not change that.");
    } finally {
      setBusy((b) => {
        const next = new Set(b);
        next.delete(key);
        return next;
      });
    }
  }

  const patchChapter = (id: number, f: (c: RepertoireSection) => RepertoireSection) => setSections((s) => s?.map((c) => (c.chapter_id === id ? f(c) : c)) ?? s);
  const activeLines = shown ? shown.reduce((n, c) => n + c.lines.filter((l) => l.active).length, 0) : book.active_lines;
  const provenance = provenanceLabel(book);

  return (
    <div className={`rounded border border-zinc-200 dark:border-zinc-800 ${active ? "" : "opacity-70"}`}>
      <div role="button" tabIndex={0} aria-expanded={open} className="flex cursor-pointer items-center gap-3 px-3 py-2" onClick={() => (setLoadError(null), setOpen((o) => !o))} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (setLoadError(null), setOpen((o) => !o))}>
        <span className="w-4 text-zinc-400">{open ? "▾" : "▸"}</span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{book.title}</p>
          <p className="text-xs text-zinc-500">
            {book.color} · {activeLines} / {book.total_lines} lines on{provenance ? ` · ${provenance}` : ""}
          </p>
        </div>
        <Toggle on={active} label={`${book.title} active`} busy={busy.has(`books:${book.book_id}`)} onChange={(on, el) => void flip("books", book.book_id, on, setActiveState, el)} />
      </div>
      {held.length > 0 && (
        <p role="status" className="border-t border-zinc-200 px-3 py-2 text-xs text-amber-700 dark:border-zinc-800 dark:text-amber-400">
          Switched on; {held.length} {held.length === 1 ? "line" : "lines"} held back because another active line disagrees with {held.length === 1 ? "it" : "them"}:{" "}
          {held.map((h, i) => (
            <span key={h.line_id}>
              {i > 0 && ", "}
              <Link to={conflictsPath({ fen: h.fen })} className="italic underline">
                {h.line_name}
              </Link>
            </span>
          ))}
          .{" "}
          <Link to={conflictsPath({ filter: "all" })} className="underline">
            View all →
          </Link>
        </p>
      )}
      {open && (
        <div className="border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          {loadError && (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {loadError}
            </p>
          )}
          {!shown && !loadError && <p className="text-sm text-zinc-500">Loading…</p>}
          {shown?.map((c) => (
            <div key={c.chapter_id} className={`py-1.5 ${c.active ? "" : "opacity-60"}`}>
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{c.title}</p>
                  <p className="text-xs text-zinc-500">
                    {c.lines.filter((l) => l.active).length} / {c.lines.length} on{c.root_fen ? " · from a set position" : ""}
                  </p>
                </div>
                <Toggle on={c.active} label={`${c.title} active`} busy={busy.has(`chapters:${c.chapter_id}`)} onChange={(on, el) => void flip("chapters", c.chapter_id, on, (v) => patchChapter(c.chapter_id, (x) => ({ ...x, active: v })), el)} />
              </div>
              <ul className="ml-4 mt-1 space-y-0.5">
                {c.lines.map((l) => (
                  <li key={l.id} className={`flex items-center gap-2 text-xs ${l.active ? "" : "text-zinc-400"}`}>
                    <button type="button" className="min-w-0 flex-1 truncate text-left hover:underline" title="Read the line" onClick={() => onOpenLine(l)}>
                      <span className="italic">{l.name}</span>
                      {l.is_alternative ? " (alt)" : ""} <span className="font-mono text-zinc-500">{numbered(l.moves)}</span>
                    </button>
                    <Toggle on={l.active} label={`${l.name} active`} busy={busy.has(`lines:${l.id}`)} onChange={(on, el) => void flip("lines", l.id, on, (v) => patchChapter(c.chapter_id, (x) => ({ ...x, lines: x.lines.map((y) => (y.id === l.id ? { ...y, active: v } : y)) })), el)} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function LineDialog({ line, onClose }: { line: RepertoireLine; onClose: () => void }) {
  useEffect(() => {
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = before;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);
  return (
    <div role="dialog" aria-label="Line" className="fixed inset-0 z-40 flex flex-col overflow-y-auto bg-zinc-50 dark:bg-zinc-950">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <button type="button" onClick={onClose} className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ← Back
        </button>
        <span className="truncate text-xs text-zinc-500">{line.name}</span>
      </div>
      <div className="mx-auto w-full max-w-2xl flex-1 p-4">
        <LineWalkthrough lineId={line.id} />
      </div>
    </div>
  );
}

export default function Repertoire() {
  const { data, error, isLoading } = useApi(getBooks);
  const [actionError, setActionError] = useState<string | null>(null);
  const [openLine, setOpenLine] = useState<RepertoireLine | null>(null);
  const [refusal, setRefusal] = useState<Opened | null>(null);
  const [contested, setContested] = useState<number | null>(null);
  const countReq = useRef(0);

  // The contested count, with the books and again after every successful toggle (one line
  // off can resolve several positions); a response older than the newest request never renders.
  const refetchCount = useCallback(() => {
    const req = ++countReq.current;
    getConflicts().then(
      (r) => {
        if (req === countReq.current) setContested(r.contested);
      },
      () => {
        /* the count is a convenience; the page stands without it */
      },
    );
  }, []);
  useEffect(() => refetchCount(), [refetchCount]);

  const closeRefusal = useCallback(() => {
    setRefusal((r) => {
      r?.from.focus();
      return null;
    });
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">Repertoire</h1>
      <p className="mt-1 text-sm text-zinc-500">The lines your games are matched against. Switch a book, chapter or line off to take it out of play; open a line to read it with its notes.</p>
      <p className="mb-4 mt-1 text-sm">
        <Link to={conflictsPath()} className="text-zinc-600 underline hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100">
          Conflicts: {contested === null ? "…" : contested === 0 ? "no contested positions" : `${contested} contested`} →
        </Link>
      </p>
      {(error || actionError) && (
        <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-400">
          {error ?? actionError}
        </p>
      )}
      {isLoading && !data && <p className="text-sm text-zinc-500">Loading…</p>}
      {data && data.books.length === 0 && <p className="py-8 text-center text-sm text-zinc-500">No repertoire yet. Load one with `pipeline import-repertoire`.</p>}
      <div className="space-y-2">{data?.books.map((b) => <Book key={b.book_id} book={b} onError={setActionError} onOpenLine={setOpenLine} onRefusal={setRefusal} onToggled={refetchCount} />)}</div>
      {openLine && <LineDialog line={openLine} onClose={() => setOpenLine(null)} />}
      {refusal && <RefusalDialog refusal={refusal.refusal} orientation={refusal.orientation} onClose={closeRefusal} />}
    </div>
  );
}
