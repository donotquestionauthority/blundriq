/**
 * "Asked here before": every cached AI answer at the board on show, whichever panel paid for
 * it (a review question, an Explore question, a walk-through's "why does this move matter", a
 * blunder Explain), newest first, each expanding to its answer. A read of what is already paid
 * for, so it is shown whether or not the host can ask anything right now.
 *
 * One fetch per (fen, answerRevision): every request takes the next generation number and its
 * result is applied only while it is still the latest, so a late list never replaces a newer
 * one. A board change clears the list at once (the rows belong to the board they were fetched
 * for); the host bumps `answerRevision` after a successful answer, which refetches the same board.
 * `onReuse` puts a past question in the host's box; without it the list is read-only.
 */
import { useEffect, useRef, useState } from "react";
import { getAskHistory } from "../ask";
import type { HistoryItem } from "../ask";
import { MinimalMarkdown } from "./MinimalMarkdown";

const reuseButton = "rounded border border-zinc-300 px-2 py-0.5 text-xs font-medium text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-300";

export function AskHistory({ fen, answerRevision, onReuse }: { fen: string; answerRevision: number; onReuse?: (question: string) => void }) {
  const [rows, setRows] = useState<{ fen: string; items: HistoryItem[] } | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    const mine = ++generation.current;
    getAskHistory(fen)
      .then((r) => {
        if (generation.current === mine) setRows({ fen, items: Array.isArray(r?.items) ? r.items : [] });
      })
      .catch(() => {
        if (generation.current === mine) setRows({ fen, items: [] });
      });
  }, [fen, answerRevision]);
  const items = rows?.fen === fen ? rows.items : [];
  if (items.length === 0) return null;
  return (
    <details className="rounded border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800" data-testid="ask-history">
      <summary className="cursor-pointer text-xs uppercase tracking-wide text-zinc-500">Asked here before ({items.length})</summary>
      <ul className="mt-2 space-y-1">
        {items.map((it, i) => (
          <li key={`${it.created_at}:${i}`}>
            <details className="rounded border border-zinc-200 px-2 py-1 dark:border-zinc-800">
              <summary className="cursor-pointer">
                <span className="text-sm">{it.question ?? it.prompt_label}</span>
                <span className="ml-2 text-xs text-zinc-500">
                  {it.question !== null ? `${it.prompt_label} · ` : ""}
                  {new Date(it.created_at).toLocaleDateString()}
                </span>
              </summary>
              <div className="mt-2 space-y-2">
                <MinimalMarkdown text={it.explanation} />
                <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
                  <span>{it.model}</span>
                  {onReuse && it.question !== null && (
                    <button type="button" className={reuseButton} onClick={() => onReuse(it.question!)}>
                      Ask again
                    </button>
                  )}
                </div>
              </div>
            </details>
          </li>
        ))}
      </ul>
    </details>
  );
}
