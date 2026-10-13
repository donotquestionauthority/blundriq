import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { DEFAULT_DEPTH, useStockfish } from "../engine/useStockfish";
import { addLine, duplicateOf, refusalOf } from "../repertoire";
import type { GapCard, NewLineBody, NewLineResponse, Refusal } from "../repertoire";
import { played, seedFor, START, turnAfter } from "./lineSeed";
import { MoveBuilder } from "./MoveBuilder";
import type { MoveBuilderState } from "./MoveBuilder";

const input = "mt-1 w-full rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
const CATCH_ALL = "catch-all";

function defaultName(card: GapCard): string {
  const family = card.family ?? "Unnamed opening";
  if (card.context) return `${family} — after ${card.context.played}`;
  if (card.arrived_by) return `${family} — 1.${card.arrived_by}`;
  return family;
}

function refusalText(r: Refusal): string {
  const holders = r.rivals.map((x) => `${x.book_title} › ${x.chapter_title} › ${x.line_name} (${x.move})`).join("; ");
  if (r.reason === "dirty") return `Your repertoire already disagrees with itself at the board where this line plays ${r.move}: resolve that on the Conflicts page first.`;
  return `An active line prescribes a different move where this line plays ${r.move}: ${holders || "another line"}. Switch it off first, or change this line there.`;
}

/**
 * Add a line to the repertoire from a gap card: the move builder from the start position, seeded
 * with the game's stem and the engine's continuation, the cursor on the gap board; a destination
 * (a start-rooted chapter of the colour, or the colour's catch-all book under the opening family);
 * a name. Save is enabled only while the line ends on the player's move and has at least one own
 * move past the gap; the server's rule is the same rule. Mount it only while open, keyed per card.
 */
export function AddLineModal({ card, prefillPlies, maxPlies, onClose, onAdded }: { card: GapCard; prefillPlies: number; maxPlies: number; onClose: () => void; onAdded: (made: NewLineResponse) => void }) {
  const colour = card.colour === "white" ? "w" : "b";
  const stemLength = useMemo(() => played(card.stem ?? []).length, [card.stem]);
  const seed = useMemo(() => seedFor(card, prefillPlies, maxPlies), [card, prefillPlies, maxPlies]);
  const [line, setLine] = useState<MoveBuilderState>({ solutionLine: seed, isValid: seed.length > 0, endTurn: turnAfter(seed) });
  // The context chapter when it is a destination (start-rooted and on); otherwise the catch-all —
  // never some other chapter of the colour, which would file the line under an unrelated book.
  const [destination, setDestination] = useState<string>(() => {
    const ctx = card.context?.chapter_id;
    return ctx !== undefined && card.chapters.some((ch) => ch.id === ctx) ? String(ctx) : CATCH_ALL;
  });
  const [name, setName] = useState(() => defaultName(card));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The browser engine for Extend, at the saved Explore depth (read once; the hook's default until
  // it lands, and a late answer only sets the depth of the next search).
  const engine = useStockfish({ enabled: true, depth: undefined });
  const [depth, setDepth] = useState(DEFAULT_DEPTH);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    api
      .get<{ explore_engine_depth?: unknown }>("/settings")
      .then((s) => {
        const v = s.explore_engine_depth;
        if (alive.current && typeof v === "number" && Number.isFinite(v)) setDepth(v);
      })
      .catch(() => {
        /* the hook's own default stands */
      });
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submitting, onClose]);

  const pastGap = line.solutionLine.length > stemLength;
  const endsOnMine = line.endTurn !== colour;
  const canSave = pastGap && endsOnMine && name.trim().length > 0 && !submitting;
  const hint = !pastGap ? "Add at least one of your moves past the gap." : !endsOnMine ? "End on your move — Extend adds it." : null;

  async function save() {
    if (!canSave) return;
    setSubmitting(true);
    setError(null);
    const body: NewLineBody = { moves: line.solutionLine, line_name: name.trim() };
    if (destination === CATCH_ALL) body.catch_all = { colour: card.colour, family: card.family };
    else body.chapter_id = Number(destination);
    try {
      const made = await addLine(body);
      onAdded(made);
      onClose();
    } catch (e) {
      const dup = duplicateOf(e);
      const refusal = refusalOf(e);
      if (dup) setError(dup.kind === "identical" ? `This line is already in the chapter as “${dup.line_name}”${dup.active ? "" : " (switched off)"}.` : `“${dup.line_name}” already teaches this and goes further.`);
      else if (refusal) setError(refusalText(refusal));
      else setError(e instanceof ApiError ? e.message : "Could not add the line.");
      setSubmitting(false);
    }
  }

  return (
    <div role="dialog" aria-label="Add to repertoire" className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/70 px-4 py-6">
      <div className="my-auto w-full max-w-2xl rounded-xl border border-zinc-200 bg-white shadow-2xl lg:max-w-5xl dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <div>
            <h2 className="text-lg font-semibold">Add to repertoire</h2>
            <p className="mt-0.5 text-xs text-zinc-500">The line from move 1: the game to the gap, then the engine's continuation. Step through it, play a different move anywhere to replace what follows, Extend to add more. It is matched against your games and drilled like any line.</p>
          </div>
          <button type="button" aria-label="Close" disabled={submitting} onClick={onClose} className="text-zinc-500 hover:text-zinc-900 disabled:opacity-50 dark:hover:text-zinc-100">
            ✕
          </button>
        </div>
        <div className="grid gap-5 p-5 lg:grid-cols-2">
          <div className="lg:order-2">
            <MoveBuilder fen={START} color={colour} onChange={setLine} initialMoves={seed} initialShown={stemLength} extend={{ line: [...played(card.stem ?? []), ...(card.engine?.line ?? [])], engine, depth }} disabled={submitting} maxPlies={maxPlies} />
          </div>
          <div className="space-y-3 lg:order-1">
            <label className="block text-xs uppercase tracking-wide text-zinc-500">
              Book › Chapter
              <select aria-label="Book and chapter" value={destination} disabled={submitting} onChange={(e) => setDestination(e.target.value)} className={`${input} normal-case tracking-normal text-zinc-900 dark:text-zinc-100`}>
                {card.chapters.map((ch) => (
                  <option key={ch.id} value={String(ch.id)}>
                    {ch.book} › {ch.title}
                  </option>
                ))}
                <option value={CATCH_ALL}>
                  {card.catch_all.book} › {card.catch_all.chapter} (catch-all)
                </option>
              </select>
            </label>
            <label className="block text-xs uppercase tracking-wide text-zinc-500">
              Line name
              <input type="text" aria-label="Line name" value={name} maxLength={120} disabled={submitting} onChange={(e) => setName(e.target.value)} className={`${input} normal-case tracking-normal text-zinc-900 dark:text-zinc-100`} />
            </label>
            <p className="min-h-[20px] text-xs text-zinc-500">{hint}</p>
            <p role="alert" className="min-h-[20px] text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
            <div className="flex justify-end gap-2 border-t border-zinc-200 pt-3 dark:border-zinc-800">
              <button type="button" disabled={submitting} onClick={onClose} className="rounded px-3 py-1.5 text-sm text-zinc-600 hover:text-zinc-900 disabled:opacity-50 dark:text-zinc-300">
                Cancel
              </button>
              <button type="button" disabled={!canSave} onClick={save} className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900">
                {submitting ? "Saving…" : "Save line"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
