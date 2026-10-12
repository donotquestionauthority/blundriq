import { useEffect, useState } from "react";
import { explain, explainDryRun, formatDryRun, getPrompts } from "../../blunders";
import type { PromptLabel } from "../../blunders";
import { AskHistory } from "../AskHistory";
import { MinimalMarkdown } from "../MinimalMarkdown";
import { messageOf } from "./aiMessage";

const button = "rounded border px-3 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50";
const idle = "border-zinc-300 text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-300";
const active = "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900";

/**
 * One button per configured prompt (the default first) and a row that copies exactly what
 * would be sent, for tuning prompts on Preferences. The request names the blunder by game and
 * ply; the server reads everything else itself. Mount it with a `key` per position so a card
 * change starts clean.
 *
 * `fen` is the blunder's board, which every answer here is cached under: what was asked at it
 * before, by any panel, is listed first (`AskHistory`, read-only: there is no box to refill),
 * with no usable prompt too, and read again after each Explain that lands.
 */
export function AiExplanationPanel({ chessGameId, ply, fen }: { chessGameId: number; ply: number; fen: string }) {
  const [prompts, setPrompts] = useState<PromptLabel[] | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<{ text: string; cached: boolean; model: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [answerRevision, setAnswerRevision] = useState(0);

  useEffect(() => {
    let live = true;
    getPrompts()
      .then((r) => live && setPrompts(r.prompts))
      .catch(() => live && setPrompts([]));
    return () => {
      live = false;
    };
  }, []);

  async function ask(key: string) {
    setActiveKey(key);
    setAnswer(null);
    setError(null);
    setLoading(true);
    try {
      const r = await explain(chessGameId, ply, key);
      setAnswer({ text: r.explanation, cached: r.cached, model: r.model });
      setAnswerRevision((n) => n + 1);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setLoading(false);
    }
  }

  async function copy(key: string) {
    try {
      await navigator.clipboard.writeText(formatDryRun(await explainDryRun(chessGameId, ply, key)));
      setCopied(key);
      setTimeout(() => setCopied(null), 2000);
    } catch (e) {
      setActiveKey(key);
      setAnswer(null);
      setError(messageOf(e));
    }
  }

  // The history keeps its place in the tree whether or not a prompt is usable, so the prompts
  // arriving does not mount it again (and read the board again); with neither, nothing renders.
  const usable = prompts ?? [];
  return (
    <>
      <AskHistory fen={fen} answerRevision={answerRevision} />
      {usable.length > 0 && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs uppercase tracking-wide text-zinc-500">Explain</span>
            {usable.map((p) => (
              <button key={p.key} type="button" disabled={loading} onClick={() => ask(p.key)} title={p.model} className={`${button} ${activeKey === p.key ? active : idle}`}>
                {p.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs uppercase tracking-wide text-zinc-500">Copy prompt</span>
            {usable.map((p) => (
              <button key={p.key} type="button" aria-label={`Copy prompt: ${p.label}`} disabled={loading} onClick={() => copy(p.key)} className={`${button} ${idle}`}>
                {copied === p.key ? "✓ copied" : p.label}
              </button>
            ))}
          </div>
          {activeKey !== null && (
            <div className="min-h-[4rem] rounded border border-zinc-200 p-3 dark:border-zinc-800">
              {loading && <p className="text-xs text-zinc-500">Thinking…</p>}
              {!loading && error && (
                <p role="alert" className="text-xs text-red-600 dark:text-red-400">
                  {error}
                </p>
              )}
              {!loading && answer && (
                <>
                  <MinimalMarkdown text={answer.text} />
                  <p className="mt-2 text-xs text-zinc-400">
                    {answer.model}
                    {answer.cached ? " · cached" : ""}
                  </p>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );
}
