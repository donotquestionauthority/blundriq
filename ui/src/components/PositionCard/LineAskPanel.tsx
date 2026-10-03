import { useEffect, useRef, useState } from "react";
import { explainLineMove } from "../../repertoire";
import { MinimalMarkdown } from "./AiExplanationPanel";
import { messageOf } from "./aiMessage";

/** The most a question may carry, after trimming (the server refuses more). */
export const QUESTION_MAX = 500;

type Answer = { question: string; text: string; cached: boolean; model: string } | { question: string; error: string };

const btn = "rounded border border-zinc-300 px-2.5 py-1 text-xs font-medium hover:border-zinc-500 disabled:opacity-40 dark:border-zinc-700";

/**
 * "Why does this move matter?" for the move the walk-through's board is showing. The server
 * reads the line and its notes itself; the request names the line, the arrival ply and the
 * optional question.
 *
 * An answer belongs to the ply it was asked at and says which question it answered. A ply
 * has at most one request in flight (its button waits), and a response is kept only if it was
 * asked under the current `notesGeneration`: the parent bumps that after a note is saved or
 * deleted, which clears every answer (their context no longer matches the line) and drops any
 * request still in flight when it lands. Mount it keyed by line: answers are kept by ply.
 */
export function LineAskPanel({ lineId, ply, notesGeneration }: { lineId: number; ply: number; notesGeneration: number }) {
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<Record<number, Answer>>({});
  const [pending, setPending] = useState<Record<number, number>>({});
  const [generation, setGeneration] = useState(notesGeneration);
  const liveGeneration = useRef(notesGeneration);

  // A note changed: everything asked so far was asked about another line. Derived during
  // render so no stale answer is ever painted after the change.
  if (generation !== notesGeneration) {
    setGeneration(notesGeneration);
    setAnswers({});
    setPending({});
  }
  useEffect(() => {
    liveGeneration.current = notesGeneration;
  }, [notesGeneration]);

  async function ask() {
    const asked = question.trim();
    const at = ply;
    const startedUnder = notesGeneration;
    setPending((p) => ({ ...p, [at]: startedUnder }));
    let result: Answer;
    try {
      const r = await explainLineMove(lineId, at, asked);
      result = { question: asked, text: r.explanation, cached: r.cached, model: r.model };
    } catch (e) {
      result = { question: asked, error: messageOf(e) };
    }
    if (liveGeneration.current !== startedUnder) return;
    setAnswers((a) => ({ ...a, [at]: result }));
    setPending((p) => {
      const next = { ...p };
      delete next[at];
      return next;
    });
  }

  const busy = pending[ply] !== undefined;
  const answer = answers[ply];
  const tooLong = [...question.trim()].length > QUESTION_MAX; // characters, as the server counts them
  return (
    <div className="space-y-2 rounded border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btn} disabled={ply === 0 || busy || tooLong} onClick={() => void ask()}>
          Ask Opus why this move matters
        </button>
        <input aria-label="Your question" type="text" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What don't you get? (optional)" className="min-w-0 flex-1 rounded border border-zinc-300 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900" />
      </div>
      {tooLong && <p className="text-xs text-red-600 dark:text-red-400">At most {QUESTION_MAX} characters.</p>}
      {busy && <p className="text-xs text-zinc-500">Thinking… this can take a minute or two.</p>}
      {!busy && answer && (
        <div className="space-y-2" data-testid="line-answer">
          <p className="text-xs text-zinc-500">Answer to: {answer.question ? <em>{answer.question}</em> : "why this move matters"}</p>
          {"error" in answer ? (
            <p role="alert" className="text-xs text-red-600 dark:text-red-400">
              {answer.error}
            </p>
          ) : (
            <>
              <MinimalMarkdown text={answer.text} />
              <p className="text-xs text-zinc-400">
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
