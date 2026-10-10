/**
 * Ask Opus about the board on show: a question box, the answer, and "Copy prompt" (the dry run,
 * for tuning the prompt on Preferences). The host owns the board and builds the request; the
 * panel owns the question (kept across boards, so the same question can be asked at the next
 * ply) and the answer (cleared when `fen` changes — an answer belongs to the board it was asked
 * about, and a reply that arrives for a board no longer on show is dropped).
 *
 * A question that names a legal move — the first token that plays on the board and is not the
 * engine's own move — is asked about that move: the panel plays it on a chess.js board and, when
 * the result is not game over, runs its own engine on the board after it (one worker, created
 * for the search and terminated when the chip clears or the panel unmounts) so the engine's
 * reply goes with the question. A move that ends the game is sent with its outcome and no
 * search. The ✕ on the chip dismisses that move for as long as the board stays.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { AskAlternative, AskAnswer } from "../ask";
import { formatDryRun } from "../blunders";
import type { DryRun } from "../blunders";
import { engineSnapshot } from "../engine/eval";
import { clampDepth, useStockfish } from "../engine/useStockfish";
import { namedMove } from "../utils/namedMove";
import type { AltOutcome } from "../utils/namedMove";
import { MinimalMarkdown } from "./PositionCard/AiExplanationPanel";
import { messageOf } from "./PositionCard/aiMessage";

export const QUESTION_MAX_CHARS = 500;
const COUNTER_FROM = 400;
const NO_SKIP: readonly string[] = [];
const OUTCOME_COPY: Record<AltOutcome, string> = { checkmate: "checkmate", stalemate: "stalemate", draw: "a draw by insufficient material" };

const button = "rounded border px-3 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50";
const idle = "border-zinc-300 text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-300";
const active = "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900";

export interface AskOpusPanelProps {
  /** The board asked about. A change clears the answer. */
  fen: string;
  /** The engine's move for the board; a question naming it is not an alternative. */
  bestMoveSan: string | null;
  /** When set, the panel shows this line and no box: there is nothing to ask about. */
  disabledReason?: string | null;
  /** When set, the box stays and the button waits, saying why (the engine is still searching). */
  waiting?: string | null;
  ask: (question: string, alternative: AskAlternative | null) => Promise<AskAnswer>;
  dryRun: (question: string, alternative: AskAlternative | null) => Promise<DryRun>;
}

export function AskOpusPanel({ fen, bestMoveSan, disabledReason = null, waiting = null, ask, dryRun }: AskOpusPanelProps) {
  const [question, setQuestion] = useState("");
  // A visit is one stay on one board: it ends when `fen` changes, and coming back to the same
  // board is a new visit. Every request belongs to the visit it was made in and carries a
  // sequence number; a reply is applied only when its visit is the current one and nothing
  // newer was asked since, so a late reply never lands on an answer the player asked for later,
  // not even after leaving a board and returning to it.
  const [visit, setVisit] = useState({ fen, n: 0 });
  if (visit.fen !== fen) setVisit({ fen, n: visit.n + 1 });
  const seqRef = useRef(0);
  const latest = useRef<{ visit: number; seq: number } | null>(null);
  const [result, setResult] = useState<{ visit: number; answer: AskAnswer | null; error: string | null } | null>(null);
  const [pending, setPending] = useState<{ visit: number; seq: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const [dismissed, setDismissed] = useState<{ visit: number; sans: string[] }>({ visit: 0, sans: [] });
  const [depth, setDepth] = useState<number | undefined>(undefined);
  const answer = result?.visit === visit.n ? result.answer : null;
  const error = result?.visit === visit.n ? result.error : null;
  const loading = pending?.visit === visit.n;
  const visitRef = useRef(visit.n);
  useEffect(() => {
    visitRef.current = visit.n;
  }, [visit.n]);
  /** Start a request in this visit; `owns()` says whether its reply may still be applied. */
  const begin = () => {
    const mine = { visit: visit.n, seq: ++seqRef.current };
    latest.current = mine;
    setPending(mine);
    const owns = () => visitRef.current === mine.visit && latest.current?.seq === mine.seq;
    const done = () => setPending((p) => (p?.seq === mine.seq ? null : p));
    return { mine, owns, done };
  };

  const skip = dismissed.visit === visit.n ? dismissed.sans : NO_SKIP;
  const named = useMemo(() => (disabledReason ? null : namedMove(question, fen, bestMoveSan, skip)), [disabledReason, question, fen, bestMoveSan, skip]);
  const altFen = named?.fenAfter ?? null;

  // The alternative's own engine: enabled only while a playable alternative is named, on the
  // board after it, at the saved Explore depth (read once, when first needed).
  const engine = useStockfish({ enabled: altFen !== null, depth });
  const { analyze } = engine;
  useEffect(() => {
    if (altFen === null || depth !== undefined) return;
    let alive = true;
    api
      .get<{ explore_engine_depth?: unknown }>("/settings")
      .then((s) => {
        const v = s.explore_engine_depth;
        if (alive) setDepth(typeof v === "number" && Number.isFinite(v) ? clampDepth(v) : clampDepth(Number.NaN));
      })
      .catch(() => {
        if (alive) setDepth(clampDepth(Number.NaN));
      });
    return () => {
      alive = false;
    };
  }, [altFen, depth]);
  useEffect(() => {
    if (altFen !== null && engine.ready) analyze(altFen, depth);
  }, [altFen, engine.ready, analyze, depth]);
  const altSnapshot = altFen !== null && engine.evalState?.fen === altFen ? engineSnapshot(engine.evalState) : null;
  const altPending = altFen !== null && altSnapshot === null;

  const alternative = useCallback((): AskAlternative | null => {
    if (!named) return null;
    if (named.outcome) return { move: named.san };
    return altSnapshot ? { move: named.san, engine: altSnapshot } : null;
  }, [named, altSnapshot]);

  async function send() {
    const { mine, owns, done } = begin();
    setResult(null);
    try {
      const r = await ask(question, alternative());
      if (owns()) setResult({ visit: mine.visit, answer: r, error: null });
    } catch (e) {
      if (owns()) setResult({ visit: mine.visit, answer: null, error: messageOf(e) });
    } finally {
      done();
    }
  }

  async function copy() {
    const { mine, owns, done } = begin();
    try {
      await navigator.clipboard.writeText(formatDryRun(await dryRun(question, alternative())));
      if (owns()) {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    } catch (e) {
      if (owns()) setResult({ visit: mine.visit, answer: null, error: messageOf(e) });
    } finally {
      done();
    }
  }

  const busy = loading || altPending || waiting !== null;
  const n = question.length;
  return (
    <div className="space-y-2 rounded border border-zinc-200 p-3 text-sm dark:border-zinc-800" data-testid="ask-opus">
      <span className="text-xs uppercase tracking-wide text-zinc-500">Ask Opus</span>
      {disabledReason ? (
        <p className="text-xs text-zinc-500">{disabledReason}</p>
      ) : (
        <>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value.slice(0, QUESTION_MAX_CHARS))}
            rows={2}
            maxLength={QUESTION_MAX_CHARS}
            placeholder="What don't you get? Name a move to ask about it — e.g. Why can't I play Nf3 here?"
            aria-label="Your question"
            className="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          />
          {named && (
            <p className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400" data-testid="ask-chip">
              <span>{named.outcome ? `${named.san} ends the game — ${OUTCOME_COPY[named.outcome]}` : altPending ? `Analysing ${named.san}…` : `Analysing ${named.san} as your alternative`}</span>
              <button type="button" aria-label={`Not asking about ${named.san}`} onClick={() => setDismissed((d) => ({ visit: visit.n, sans: [...(d.visit === visit.n ? d.sans : []), named.san] }))} className="text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100">
                ✕
              </button>
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" disabled={busy} onClick={send} className={`${button} ${loading ? active : idle}`}>
              {waiting ?? (altPending && named ? `Analysing ${named.san}…` : "Ask Opus")}
            </button>
            <button type="button" disabled={busy} onClick={copy} className={`${button} ${idle}`}>
              {copied ? "✓ copied" : "Copy prompt"}
            </button>
            {n > COUNTER_FROM && (
              <span className="text-xs tabular-nums text-zinc-500">
                {n}/{QUESTION_MAX_CHARS}
              </span>
            )}
          </div>
          {(loading || error || answer) && (
            <div className="min-h-[4rem] rounded border border-zinc-200 p-3 dark:border-zinc-800">
              {loading && <p className="text-xs text-zinc-500">Thinking…</p>}
              {!loading && error && (
                <p role="alert" className="text-xs text-red-600 dark:text-red-400">
                  {error}
                </p>
              )}
              {!loading && answer && (
                <>
                  <MinimalMarkdown text={answer.explanation} />
                  <p className="mt-2 text-xs text-zinc-400">
                    {answer.model}
                    {answer.cached ? " · cached" : ""}
                  </p>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
