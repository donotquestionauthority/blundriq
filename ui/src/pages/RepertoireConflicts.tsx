import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import { ArrowBoard, RefusalDialog, Toggle } from "../components/RepertoireBits";
import { moveArrows, numbered } from "../utils/chess";
import { getConflicts, refusalOf, setActive } from "../repertoire";
import type { ConflictLine, ConflictPosition, ConflictsResponse, DuplicateGroup, Refusal } from "../repertoire";

/**
 * Where the repertoire disagrees with itself. Positions where two lines prescribe different
 * moves (contested when both are in play), and identical lines in different chapters; each
 * line with the same switch as the Repertoire page. Every successful flip refetches the
 * whole page (one line off can resolve several positions) under a latest-request guard;
 * a refused flip opens the refusal dialog. `?fen=` reveals one position — flipping the
 * filter to "All" when it is not contested, since a refusal's position usually is not — and
 * `?filter=all` opens on All. Expanded positions stay expanded across a refetch.
 */

type Filter = "contested" | "all";

function offNote(l: ConflictLine): string | null {
  if (l.effective || !l.line_active) return null;
  return !l.book_active ? "book off" : !l.chapter_active ? "chapter off" : null;
}

function LineRow({ line, busy, onFlip }: { line: ConflictLine; busy: boolean; onFlip: (line: ConflictLine, on: boolean, el: HTMLButtonElement) => void }) {
  const note = offNote(line);
  return (
    <li className={`flex items-center gap-2 text-xs ${line.effective ? "" : "text-zinc-400"}`}>
      <span className="min-w-0 flex-1 truncate">
        <span className="italic">{line.line_name}</span> <span className="text-zinc-500">— {line.book_title} / {line.chapter_title}</span>
        {note && <span className="ml-1 text-zinc-400">({note})</span>}
      </span>
      <Toggle on={line.line_active} label={`${line.line_name} active`} busy={busy} onChange={(on, el) => onFlip(line, on, el)} />
    </li>
  );
}

function Position({ p, expanded, highlighted, busy, onToggleExpand, onFlip }: { p: ConflictPosition; expanded: boolean; highlighted: boolean; busy: Set<number>; onToggleExpand: () => void; onFlip: (line: ConflictLine, on: boolean, el: HTMLButtonElement) => void }) {
  const boardId = useId().replace(/[^a-zA-Z0-9-]/g, ""); // the id ends up in url(#…) and a selector
  const arrows = moveArrows(
    p.fen,
    p.moves.map((g) => ({ move: g.move, inPlay: g.lines.some((l) => l.effective) })),
  );
  const total = p.moves.reduce((n, g) => n + g.lines.length, 0);
  const on = p.moves.reduce((n, g) => n + g.lines.filter((l) => l.effective).length, 0);
  return (
    <div data-fen={p.fen} className={`rounded border transition-colors ${p.contested ? "border-amber-400/60" : "border-zinc-200 dark:border-zinc-800"} ${highlighted ? "bg-amber-50 dark:bg-amber-950/30" : ""}`}>
      <div role="button" tabIndex={0} aria-expanded={expanded} className="flex cursor-pointer items-start gap-3 px-3 py-2" onClick={onToggleExpand} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onToggleExpand())}>
        <span className="w-4 pt-1 text-zinc-400">{expanded ? "▾" : "▸"}</span>
        <ArrowBoard id={boardId} fen={p.fen} orientation={p.color} arrows={arrows} />
        <div className="min-w-0 flex-1 text-sm">
          <p>
            {p.moves.length} moves · {on} of {total} lines on
            {p.contested && <span className="ml-2 rounded border border-amber-500 px-1.5 py-0.5 text-xs text-amber-700 dark:text-amber-400">Contested</span>}
          </p>
          <p className="font-mono text-xs text-zinc-500">{p.moves.map((g) => g.move).join(" · ")}</p>
        </div>
      </div>
      {expanded && (
        <div className="space-y-2 border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          {p.moves.map((g) => (
            <div key={g.move}>
              <p className="text-sm">
                <span className="font-mono font-medium">{g.move}</span> <span className="text-xs text-zinc-500">{g.lines.filter((l) => l.effective).length} of {g.lines.length} on</span>
              </p>
              <ul className="ml-4 mt-1 space-y-0.5">
                {g.lines.map((l) => (
                  <LineRow key={l.line_id} line={l} busy={busy.has(l.line_id)} onFlip={onFlip} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Duplicate({ g, busy, onFlip }: { g: DuplicateGroup; busy: Set<number>; onFlip: (line: ConflictLine, on: boolean, el: HTMLButtonElement) => void }) {
  return (
    <div className="rounded border border-zinc-200 px-3 py-2 dark:border-zinc-800">
      <p className="text-sm">
        <span className="text-xs text-zinc-500">{g.color}</span> <span className="font-mono">{numbered(g.moves)}</span>
      </p>
      <ul className="ml-4 mt-1 space-y-0.5">
        {g.lines.map((l) => (
          <LineRow key={l.line_id} line={l} busy={busy.has(l.line_id)} onFlip={onFlip} />
        ))}
      </ul>
    </div>
  );
}

export default function RepertoireConflicts() {
  const [data, setData] = useState<ConflictsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("contested");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [busy, setBusy] = useState<Set<number>>(() => new Set());
  const [refusal, setRefusal] = useState<{ refusal: Refusal; orientation: "white" | "black"; from: HTMLButtonElement } | null>(null);
  const latest = useRef(0);
  const [searchParams] = useSearchParams();
  const { key: navigation } = useLocation(); // a new key for every navigation, the same URL included

  const refetch = useCallback(() => {
    const req = ++latest.current;
    getConflicts().then(
      (r) => {
        if (req !== latest.current) return;
        setData(r);
        setError(null);
      },
      (e: unknown) => {
        if (req !== latest.current) return;
        setError(e instanceof Error ? e.message : String(e));
      },
    );
  }, []);

  useEffect(() => refetch(), [refetch]);

  // `?filter=all` opens on All; `?fen=` reveals its position once the data is in — expanded,
  // scrolled to, highlighted for a moment, and on the All filter when it is not contested.
  // Handled once per navigation (a navigation within the mounted page, the dialog's link,
  // counts, even to the URL already shown), never again on a refetch: state adjusted during
  // render, keyed on the location key. An unknown FEN is ignored.
  const [revealed, setRevealed] = useState<string | null>(null);
  if (data && revealed !== navigation) {
    setRevealed(navigation);
    if (searchParams.get("filter") === "all") setFilter("all");
    const target = searchParams.get("fen");
    const p = target ? data.positions.find((x) => x.fen === target) : undefined;
    if (p) {
      if (!p.contested) setFilter("all");
      setExpanded((s) => new Set(s).add(p.fen));
      setHighlighted(p.fen);
    }
  }
  useEffect(() => {
    if (!highlighted) return;
    const t = window.setTimeout(() => setHighlighted((h) => (h === highlighted ? null : h)), 2500);
    return () => window.clearTimeout(t);
  }, [highlighted]);
  useEffect(() => {
    if (!highlighted) return;
    document.querySelector(`[data-fen="${CSS.escape(highlighted)}"]`)?.scrollIntoView?.({ block: "center" });
  }, [highlighted, filter]);

  async function flip(line: ConflictLine, on: boolean, el: HTMLButtonElement) {
    setBusy((b) => new Set(b).add(line.line_id));
    setError(null);
    try {
      await setActive("lines", line.line_id, on);
      refetch();
    } catch (e) {
      const r = refusalOf(e);
      if (r) setRefusal({ refusal: r, orientation: r.fen.split(" ")[1] === "w" ? "white" : "black", from: el });
      else setError(e instanceof Error ? e.message : "Could not change that.");
    } finally {
      setBusy((b) => {
        const next = new Set(b);
        next.delete(line.line_id);
        return next;
      });
    }
  }

  const closeRefusal = useCallback(() => {
    setRefusal((r) => {
      r?.from.focus();
      return null;
    });
  }, []);

  const contested = data?.positions.filter((p) => p.contested) ?? [];
  const shown = filter === "contested" ? contested : (data?.positions ?? []);
  const filterButton = (f: Filter, label: string) => (
    <button type="button" aria-pressed={filter === f} onClick={() => setFilter(f)} className={`rounded border px-2 py-0.5 text-xs ${filter === f ? "border-zinc-900 text-zinc-900 dark:border-zinc-100 dark:text-zinc-100" : "border-zinc-300 text-zinc-500 dark:border-zinc-700"}`}>
      {label}
    </button>
  );

  return (
    <div>
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-xl font-semibold tracking-tight">Repertoire conflicts</h1>
        <Link to="/repertoire" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ← Repertoire
        </Link>
      </div>
      <p className="mb-4 mt-1 text-sm text-zinc-500">Positions where two of your lines say different things. At a contested position the cards and puzzles show no repertoire move; switch lines off until one move is left.</p>
      {error && (
        <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      {!data && !error && <p className="text-sm text-zinc-500">Loading…</p>}
      {data && (
        <>
          <section aria-labelledby="positions-heading">
            <div className="mb-2 flex items-center gap-2">
              <h2 id="positions-heading" className="text-sm font-medium">
                Positions
              </h2>
              {filterButton("contested", `Contested (${contested.length})`)}
              {filterButton("all", `All (${data.positions.length})`)}
            </div>
            {shown.length === 0 && <p className="py-6 text-center text-sm text-zinc-500">{filter === "contested" ? "No contested positions." : "No conflicts. Every position in your repertoire has at most one move."}</p>}
            <div className="space-y-2">
              {shown.map((p) => (
                <Position
                  key={p.fen}
                  p={p}
                  expanded={expanded.has(p.fen)}
                  highlighted={highlighted === p.fen}
                  busy={busy}
                  onToggleExpand={() =>
                    setExpanded((s) => {
                      const next = new Set(s);
                      if (next.has(p.fen)) next.delete(p.fen);
                      else next.add(p.fen);
                      return next;
                    })
                  }
                  onFlip={flip}
                />
              ))}
            </div>
          </section>
          <section aria-labelledby="duplicates-heading" className="mt-6">
            <h2 id="duplicates-heading" className="mb-2 text-sm font-medium">
              Duplicate lines
            </h2>
            {data.duplicates.length === 0 && <p className="py-4 text-center text-sm text-zinc-500">No duplicate lines.</p>}
            <div className="space-y-2">
              {data.duplicates.map((g) => (
                <Duplicate key={`${g.color}:${g.root}:${g.moves.join(" ")}`} g={g} busy={busy} onFlip={flip} />
              ))}
            </div>
          </section>
        </>
      )}
      {refusal && <RefusalDialog refusal={refusal.refusal} orientation={refusal.orientation} onClose={closeRefusal} />}
    </div>
  );
}
