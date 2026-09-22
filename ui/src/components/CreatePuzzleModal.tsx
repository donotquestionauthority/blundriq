import { useEffect, useState } from "react";
import { ApiError } from "../api";
import { createPuzzle } from "../blunders";
import { buildPgn } from "../utils/chess";
import { MoveBuilder } from "./MoveBuilder";
import type { MoveBuilderState } from "./MoveBuilder";

/** The position a puzzle is being made from, and what the page knows about it. */
export interface CreatePuzzleSource {
  source: "blunder" | "deviation" | "scout";
  fen: string;
  color: "w" | "b";
  movePlayed?: string | null;
  bestMove?: string | null;
  bestLine?: string | null;
  lineNames?: string[] | null;
  moves?: string[] | null;
  ply?: number | null;
}

function Context({ ctx }: { ctx: CreatePuzzleSource }) {
  const pgn = ctx.moves?.length && typeof ctx.ply === "number" ? buildPgn(ctx.moves, ctx.ply) : "";
  if (!ctx.movePlayed && !ctx.bestMove && !ctx.bestLine && !pgn && !ctx.lineNames?.length) return null;
  return (
    <div className="space-y-1.5 rounded border border-zinc-200 px-3 py-2 text-xs dark:border-zinc-800">
      <p className="uppercase tracking-wide text-zinc-500">Context</p>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {ctx.movePlayed && (
          <span>
            <span className="text-zinc-500">You played </span>
            <span className="font-mono">{ctx.movePlayed}</span>
          </span>
        )}
        {ctx.bestMove && (
          <span>
            <span className="text-zinc-500">Best </span>
            <span className="font-mono text-emerald-600 dark:text-emerald-400">{ctx.bestMove}</span>
          </span>
        )}
      </div>
      {ctx.bestLine && (
        <p>
          <span className="text-zinc-500">Best line </span>
          <span className="break-words font-mono">{ctx.bestLine}</span>
        </p>
      )}
      {ctx.lineNames?.length ? (
        <p>
          <span className="text-zinc-500">Line </span>
          {ctx.lineNames.slice(0, 3).join(" · ")}
        </p>
      ) : null}
      {pgn && (
        <p>
          <span className="text-zinc-500">PGN to position </span>
          <span className="break-words font-mono">{pgn}</span>
        </p>
      )}
    </div>
  );
}

const input = "mt-1 w-full rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";

/**
 * Make a puzzle from a position: play the solution out on the board, optionally name it.
 * Mount it only while it is open, with a `key` per source, so every opening starts empty.
 * Escape and Cancel close it unless a save is in flight.
 */
export function CreatePuzzleModal({ source, onClose, onCreated }: { source: CreatePuzzleSource; onClose: () => void; onCreated: (result: { id: number; visible: boolean }) => void }) {
  const [color, setColor] = useState<"w" | "b">(source.color);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [line, setLine] = useState<MoveBuilderState>({ solutionLine: [], isValid: false });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submitting, onClose]);

  async function save() {
    if (!line.isValid || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const made = await createPuzzle({ fen: source.fen, solution_line: line.solutionLine, color, source_types: [source.source], title: title.trim() || undefined, description: description.trim() || undefined });
      onCreated(made);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create the puzzle.");
      setSubmitting(false);
    }
  }

  return (
    <div role="dialog" aria-label="Create puzzle" className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/70 px-4 py-6">
      <div className="my-auto w-full max-w-2xl rounded-xl border border-zinc-200 bg-white shadow-2xl lg:max-w-5xl dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <div>
            <h2 className="text-lg font-semibold">Create puzzle</h2>
            <p className="mt-0.5 text-xs text-zinc-500">Play the solution on the board, both sides. It is served in Practice under Custom.</p>
          </div>
          <button type="button" aria-label="Close" disabled={submitting} onClick={onClose} className="text-zinc-500 hover:text-zinc-900 disabled:opacity-50 dark:hover:text-zinc-100">
            ✕
          </button>
        </div>
        <div className="grid gap-5 p-5 lg:grid-cols-2">
          <div className="lg:order-2">
            <MoveBuilder fen={source.fen} color={color} onChange={setLine} />
          </div>
          <div className="space-y-3 lg:order-1">
            <Context ctx={source} />
            <fieldset className="text-sm">
              <legend className="text-xs uppercase tracking-wide text-zinc-500">You play</legend>
              {(["w", "b"] as const).map((c) => (
                <label key={c} className="mr-4 inline-flex items-center gap-1.5">
                  <input type="radio" name="puzzle-color" checked={color === c} disabled={submitting} onChange={() => setColor(c)} />
                  {c === "w" ? "White" : "Black"}
                </label>
              ))}
            </fieldset>
            <label className="block text-xs uppercase tracking-wide text-zinc-500">
              Title (optional)
              <input type="text" value={title} maxLength={200} disabled={submitting} onChange={(e) => setTitle(e.target.value)} className={`${input} normal-case tracking-normal text-zinc-900 dark:text-zinc-100`} />
            </label>
            <label className="block text-xs uppercase tracking-wide text-zinc-500">
              Notes (optional)
              <textarea value={description} maxLength={2000} rows={3} disabled={submitting} onChange={(e) => setDescription(e.target.value)} className={`${input} normal-case tracking-normal text-zinc-900 dark:text-zinc-100`} />
            </label>
            <p role="alert" className="min-h-[20px] text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
            <div className="flex justify-end gap-2 border-t border-zinc-200 pt-3 dark:border-zinc-800">
              <button type="button" disabled={submitting} onClick={onClose} className="rounded px-3 py-1.5 text-sm text-zinc-600 hover:text-zinc-900 disabled:opacity-50 dark:text-zinc-300">
                Cancel
              </button>
              <button type="button" disabled={!line.isValid || submitting} onClick={save} className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900">
                {submitting ? "Creating…" : "Create puzzle"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
