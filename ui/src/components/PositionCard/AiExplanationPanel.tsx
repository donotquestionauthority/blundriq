import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import { explain, explainDryRun, formatDryRun, getPrompts } from "../../blunders";
import type { PromptLabel } from "../../blunders";

/** `**bold**` and blank-line paragraphs: all the markup the prompts ask a model for. */
export function MinimalMarkdown({ text }: { text: string }) {
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {text.split(/\n{2,}/g).map((block, bi) => (
        <p key={bi} className="whitespace-pre-line">
          {block.split(/(\*{2,3}[^*]+\*{2,3})/g).map((part, i) => {
            const bold = part.match(/^\*{2,3}([^*]+)\*{2,3}$/);
            return bold ? <strong key={i}>{bold[1].trim()}</strong> : <span key={i}>{part}</span>;
          })}
        </p>
      ))}
    </div>
  );
}

/** A refusal's text. The cap's refusal is an object with a message; everything else is a string. */
function messageOf(err: unknown): string {
  if (!(err instanceof ApiError)) return "AI call failed";
  try {
    const detail: unknown = JSON.parse(err.message);
    if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  } catch {
    /* a plain string */
  }
  return err.message || "AI call failed";
}

const button = "rounded border px-3 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50";
const idle = "border-zinc-300 text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-300";
const active = "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900";

/**
 * One button per configured prompt (the default first) and a row that copies exactly what
 * would be sent, for tuning prompts on Preferences. The request names the blunder by game and
 * ply; the server reads everything else itself. Mount it with a `key` per position so a card
 * change starts clean.
 */
export function AiExplanationPanel({ chessGameId, ply }: { chessGameId: number; ply: number }) {
  const [prompts, setPrompts] = useState<PromptLabel[] | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<{ text: string; cached: boolean; model: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getPrompts()
      .then((r) => live && setPrompts(r.prompts))
      .catch(() => live && setPrompts([]));
    return () => {
      live = false;
    };
  }, []);

  if (!prompts?.length) return null;

  async function ask(key: string) {
    setActiveKey(key);
    setAnswer(null);
    setError(null);
    setLoading(true);
    try {
      const r = await explain(chessGameId, ply, key);
      setAnswer({ text: r.explanation, cached: r.cached, model: r.model });
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

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-zinc-500">Explain</span>
        {prompts.map((p) => (
          <button key={p.key} type="button" disabled={loading} onClick={() => ask(p.key)} title={p.model} className={`${button} ${activeKey === p.key ? active : idle}`}>
            {p.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-zinc-500">Copy prompt</span>
        {prompts.map((p) => (
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
  );
}
