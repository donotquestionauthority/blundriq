import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Chessboard } from "react-chessboard";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { askChapter, followUpChapter, getChapterConversation, getChapterIdea } from "../repertoire";
import type { ChapterConversation, ChapterIdeaAnswer, ChapterIdeaRead, ChapterMoveLink, ChapterSegment, ChapterTurn } from "../repertoire";
import { SQUARES } from "../utils/board";
import { CHAPTER_MESSAGES, refusalOf, sideLineBoards, when } from "../utils/chapterIdea";
import { ExploreLayer } from "./ExploreLayer";
import { QUESTION_MAX } from "./PositionCard/LineAskPanel";
import { LineWalkthrough } from "./PositionCard/LineReaderPanel";

/**
 * "Ask about this chapter": a full-screen dialog over the Repertoire page. It opens on the chapter's
 * newest stored conversation, or on an empty state with a question box; every question and answer
 * is read back from the server with no AI call, and only Ask and Ask a follow-up call one.
 *
 * Every move an answer names is a link, resolved by the server when the answer was stored. A move
 * of a line opens that line's walk-through at that ply (keyed by line, ply and click, so a second
 * move in the same line jumps there); a move no line plays opens the side-line viewer, which steps
 * the branch from the line's board (every replay through `legalMove`) and offers the walk-through
 * at the anchor and Explore at the board shown. A link to a line deleted since opens the viewer on
 * the stored boards. On a laptop the board sits beside the conversation; on a phone it is a sheet
 * over it, and Back returns to the text where it was (the conversation stays mounted beneath).
 *
 * One request at a time; a response is applied only if it is still the latest the dialog made, and
 * nothing is applied after it closes.
 */

const btn = "rounded border border-zinc-300 px-2.5 py-1 text-xs font-medium hover:border-zinc-500 disabled:opacity-40 dark:border-zinc-700";
const btnOn = "rounded border border-zinc-900 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40 dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900";
const input = "min-w-0 flex-1 rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const notice = "rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200";

// --- the answer ----------------------------------------------------------------------------------

function bold(text: string, key: string): ReactNode[] {
  return text.split(/(\*{2,3}[^*]+\*{2,3})/g).map((part, i) => {
    const b = part.match(/^\*{2,3}([^*]+)\*{2,3}$/);
    return b ? <strong key={`${key}-${i}`}>{b[1].trim()}</strong> : <span key={`${key}-${i}`}>{part}</span>;
  });
}

/** `MinimalMarkdown` with move links: paragraphs on blank lines, `**bold**`, and each link a button. */
export function SegmentText({ segments, selected, onMove }: { segments: ChapterSegment[]; selected: ChapterMoveLink | null; onMove: (m: ChapterMoveLink) => void }) {
  const paragraphs: ReactNode[][] = [[]];
  segments.forEach((seg, si) => {
    if ("move" in seg) {
      const m = seg.move;
      const on = selected === m;
      paragraphs[paragraphs.length - 1].push(
        <button key={`m${si}`} type="button" data-testid="move-link" aria-pressed={on} aria-label={`Show ${m.label} on the board`} onClick={() => onMove(m)} className={`rounded px-0.5 font-mono font-semibold ${on ? "bg-sky-700 text-white dark:bg-sky-300 dark:text-sky-950" : "text-sky-700 hover:underline dark:text-sky-400"}`}>
          {m.label}
        </button>,
      );
      return;
    }
    seg.text.split(/\n{2,}/g).forEach((block, bi) => {
      if (bi > 0) paragraphs.push([]);
      paragraphs[paragraphs.length - 1].push(...bold(block, `t${si}-${bi}`));
    });
  });
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {paragraphs.map((nodes, i) => (
        <p key={i} className="whitespace-pre-line">
          {nodes}
        </p>
      ))}
    </div>
  );
}

// --- the board side --------------------------------------------------------------------------------

export function SideLineViewer({ link, lineName, deleted, orientation, onOpenLine, onExplore }: { link: ChapterMoveLink; lineName: string; deleted: boolean; orientation: "white" | "black"; onOpenLine: () => void; onExplore: (fen: string) => void }) {
  const boards = useMemo(() => sideLineBoards(link), [link]);
  const [at, setAt] = useState(boards.length - 1);
  const boardId = "side" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const cur = boards[Math.min(at, boards.length - 1)];
  return (
    <div className="space-y-3" data-testid="side-line">
      <p className="text-xs text-zinc-500" data-testid="side-line-caption">
        {deleted ? (
          CHAPTER_MESSAGES.deleted
        ) : (
          <>
            Not in your repertoire — from <em>{lineName}</em> after <span className="font-mono">{link.anchor}</span>
          </>
        )}
      </p>
      <div className="mx-auto aspect-square w-full max-w-[360px] self-start">
        <Chessboard options={{ id: boardId, position: cur.fen, boardOrientation: orientation, allowDragging: false, animationDurationInMs: 150, boardStyle: { borderRadius: "6px" }, ...SQUARES }} />
      </div>
      {boards.length > 1 && (
        <div className="flex items-center justify-between gap-2">
          <button type="button" className={btn} disabled={at === 0} onClick={() => setAt((n) => Math.max(0, n - 1))}>
            ‹ Prev
          </button>
          <span className="font-mono text-xs" data-testid="side-line-position">
            {cur.label}
          </span>
          <button type="button" className={btn} disabled={at >= boards.length - 1} onClick={() => setAt((n) => Math.min(boards.length - 1, n + 1))}>
            Next ›
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {!deleted && (
          <button type="button" className={btn} onClick={onOpenLine}>
            Open the line here
          </button>
        )}
        <button type="button" className={btn} onClick={() => onExplore(cur.fen)}>
          Explore from here
        </button>
      </div>
    </div>
  );
}

type Target = { link: ChapterMoveLink; click: number; asLine: boolean };

function BoardPanel({ target, lines, orientation, onOpenLine, onExplore }: { target: Target; lines: ChapterConversation["lines"]; orientation: "white" | "black"; onOpenLine: () => void; onExplore: (fen: string) => void }) {
  const line = lines[target.link.line];
  const exists = line?.exists ?? true;
  if (target.asLine && exists) return <LineWalkthrough key={`${target.link.line_id}:${target.link.ply}:${target.click}`} lineId={target.link.line_id} initialPly={target.link.ply} />;
  return <SideLineViewer key={target.click} link={target.link} lineName={line?.name ?? target.link.line} deleted={!exists} orientation={orientation} onOpenLine={onOpenLine} onExplore={onExplore} />;
}

// --- the dialog ------------------------------------------------------------------------------------

function TurnView({ turn, first, selected, onMove }: { turn: ChapterTurn; first: boolean; selected: ChapterMoveLink | null; onMove: (m: ChapterMoveLink) => void }) {
  return (
    <div className="space-y-2" data-testid="chapter-turn">
      <p className="text-xs text-zinc-500">
        {first ? "Asked" : "Follow-up"}: <em className="text-zinc-700 dark:text-zinc-300">{turn.question}</em> · {when(turn.created_at)}
      </p>
      <SegmentText segments={turn.segments} selected={selected} onMove={onMove} />
    </div>
  );
}

export function ChapterIdeaDialog({ chapterId, title, onClose, onCount }: { chapterId: number; title: string; onClose: () => void; onCount?: (n: number) => void }) {
  const [data, setData] = useState<ChapterIdeaRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [unsaved, setUnsaved] = useState<{ turn: ChapterTurn; base: number | null; lines: ChapterConversation["lines"] } | null>(null);
  const [startingOver, setStartingOver] = useState(false);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<Target | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [exploreFen, setExploreFen] = useState<string | null>(null);
  const wide = useMediaQuery("(min-width: 1024px)", true);
  const latest = useRef(0);
  const alive = useRef(true);
  const clicks = useRef(0);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const apply = useCallback(
    (r: ChapterIdeaRead) => {
      setData(r);
      onCount?.(r.conversations.length);
    },
    [onCount],
  );

  useEffect(() => {
    const req = ++latest.current;
    getChapterIdea(chapterId).then(
      (r) => {
        if (alive.current && req === latest.current) apply(r);
      },
      (e: unknown) => {
        if (alive.current && req === latest.current) setLoadError(e instanceof Error ? e.message : String(e));
      },
    );
  }, [chapterId, apply]);

  // Escape closes the board sheet first on a phone, then the dialog. Explore takes its own Escape.
  useEffect(() => {
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (!wide && sheetOpen) setSheetOpen(false);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = before;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose, sheetOpen, wide]);

  const loaded = data?.conversation ?? null;
  const unsavedFirst = unsaved !== null && unsaved.base === null;
  // An unsaved first answer stands alone: the conversation on screen is not the loaded one.
  const conversation = unsavedFirst ? null : loaded;
  const chapter = data?.chapter ?? null;
  const orientation = chapter?.color ?? "white";
  const showingNew = startingOver || (conversation === null && !unsavedFirst);
  const turns: ChapterTurn[] = useMemo(() => {
    if (unsaved && unsaved.base === null) return [unsaved.turn];
    const stored = conversation?.turns ?? [];
    return unsaved && unsaved.base === conversation?.id ? [...stored, unsaved.turn] : stored;
  }, [conversation, unsaved]);

  function openMove(link: ChapterMoveLink) {
    clicks.current += 1;
    setTarget({ link, click: clicks.current, asLine: link.branch.length === 0 });
    setSheetOpen(true);
  }

  function openConversation(id: number) {
    const req = ++latest.current;
    setError(null);
    getChapterConversation(id).then(
      (r) => {
        if (!alive.current || req !== latest.current) return;
        apply(r);
        setUnsaved(null);
        setStartingOver(false);
        setTarget(null);
      },
      (e: unknown) => {
        if (alive.current && req === latest.current) setError(refusalOf(e).message);
      },
    );
  }

  // A question in flight owns the dialog: Earlier and Start over wait for it, so its answer always
  // lands on the conversation it was asked from, and nothing else moves `latest` meanwhile.
  async function send(kind: "new" | "followup") {
    const asked = question.trim();
    const req = ++latest.current;
    setPending(true);
    setError(null);
    let result: ChapterIdeaAnswer | null = null;
    let failure: { code: string | null; message: string } | null = null;
    try {
      result = kind === "new" ? await askChapter(chapterId, asked) : await followUpChapter(conversation!.id, asked);
    } catch (e) {
      failure = refusalOf(e);
    }
    if (!alive.current) return;
    setPending(false);
    if (req !== latest.current) return;
    if (failure) {
      setError(failure.message);
      // A follow-up the count refused is recorded on the conversation: read it again so the box greys.
      if (failure.code === "conversation_full" && conversation) openConversation(conversation.id);
      return;
    }
    setQuestion("");
    setStartingOver(false);
    if (result && result.saved) {
      apply(result);
      setUnsaved(null);
    } else if (result) {
      setUnsaved({ turn: result.unsaved_turn, base: kind === "new" ? null : conversation!.id, lines: result.lines });
    }
  }

  const tooLong = [...question.trim()].length > QUESTION_MAX;
  const noLines = chapter !== null && chapter.active_lines === 0;
  const others = (data?.conversations ?? []).filter((c) => c.id !== conversation?.id || showingNew);
  const boardLines = unsaved && (unsavedFirst || unsaved.base === loaded?.id) ? { ...(loaded?.lines ?? {}), ...unsaved.lines } : (loaded?.lines ?? {});
  const followBlocked = conversation?.blocked ? CHAPTER_MESSAGES[conversation.blocked] : conversation?.full ? CHAPTER_MESSAGES.full : null;
  const canFollow = !!conversation && !showingNew && !followBlocked && !unsaved && !pending;
  const board = target && (
    <BoardPanel
      target={target}
      lines={unsavedFirst ? (unsaved?.lines ?? {}) : boardLines}
      orientation={orientation}
      onOpenLine={() => {
        clicks.current += 1;
        setTarget({ link: target.link, click: clicks.current, asLine: true });
      }}
      onExplore={setExploreFen}
    />
  );

  return (
    <div role="dialog" aria-label="Chapter idea" className="fixed inset-0 z-40 flex flex-col overflow-y-auto bg-zinc-50 dark:bg-zinc-950" data-testid="chapter-dialog">
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <button type="button" onClick={onClose} className="shrink-0 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ← Back
        </button>
        <span className="min-w-0 truncate text-xs text-zinc-500">{chapter ? `${chapter.book_title} · ${chapter.title}` : title}</span>
      </div>
      <div className="mx-auto w-full max-w-6xl flex-1 p-4 lg:grid lg:grid-cols-[minmax(0,1fr)_400px] lg:items-start lg:gap-6">
        <div className="min-w-0 space-y-4" data-testid="chapter-conversation">
          {loadError && (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {loadError}
            </p>
          )}
          {!data && !loadError && <p className="text-sm text-zinc-500">Loading…</p>}
          {data && (
            <>
              {others.length > 0 && (
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-500" data-testid="chapter-earlier">
                  <span>{conversation && !showingNew && others.every((c) => c.created_at <= conversation.created_at) ? "Earlier:" : "Conversations:"}</span>
                  {others.map((c) => (
                    <button key={c.id} type="button" disabled={pending} className="underline hover:text-zinc-900 disabled:opacity-40 dark:hover:text-zinc-100" onClick={() => openConversation(c.id)}>
                      {when(c.created_at)}
                    </button>
                  ))}
                </p>
              )}
              {!showingNew && conversation?.stale && <p className={notice}>{conversation.stale_reason === "empty" ? CHAPTER_MESSAGES.emptied : CHAPTER_MESSAGES.stale}</p>}
              {!showingNew && conversation && !conversation.prompt_current && <p className="text-xs text-zinc-500">{CHAPTER_MESSAGES.oldPrompt}</p>}
              {!showingNew &&
                turns.map((t, i) => (
                  <div key={`${conversation?.id ?? "new"}:${t.seq}:${t.created_at}`} className="space-y-2 border-b border-zinc-200 pb-4 dark:border-zinc-800">
                    <TurnView turn={t} first={t.seq === 0} selected={target?.link ?? null} onMove={openMove} />
                    {unsaved && i === turns.length - 1 && (
                      <p role="alert" className={notice}>
                        {CHAPTER_MESSAGES.unsaved}
                      </p>
                    )}
                  </div>
                ))}
              {pending && <p className="text-xs text-zinc-500">{CHAPTER_MESSAGES.thinking}</p>}
              {error && (
                <p role="alert" className="text-xs text-red-600 dark:text-red-400">
                  {error}
                </p>
              )}
              {showingNew ? (
                <div className="space-y-2 rounded border border-zinc-200 p-3 dark:border-zinc-800" data-testid="chapter-ask">
                  <p className="text-sm">Ask Opus what this chapter is about</p>
                  <div className="flex flex-wrap items-center gap-2">
                    <input aria-label="Your question about the chapter" type="text" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Anything in particular? (optional)" className={input} disabled={noLines} />
                    <button type="button" className={btnOn} disabled={pending || tooLong || noLines} onClick={() => void send("new")}>
                      Ask
                    </button>
                    {startingOver && (
                      <button type="button" className={btn} disabled={pending} onClick={() => setStartingOver(false)}>
                        Cancel
                      </button>
                    )}
                  </div>
                  {noLines && <p className="text-xs text-zinc-500">{CHAPTER_MESSAGES.noLines}</p>}
                  {tooLong && <p className="text-xs text-red-600 dark:text-red-400">At most {QUESTION_MAX} characters.</p>}
                </div>
              ) : (
                <div className="space-y-2 rounded border border-zinc-200 p-3 dark:border-zinc-800" data-testid="chapter-followup">
                  <div className="flex flex-wrap items-center gap-2">
                    <input aria-label="Your follow-up question" type="text" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask a follow-up…" className={input} disabled={!canFollow} />
                    <button type="button" className={btnOn} disabled={!canFollow || tooLong || !question.trim()} onClick={() => void send("followup")}>
                      Ask a follow-up
                    </button>
                  </div>
                  {followBlocked && <p className="text-xs text-zinc-500">{followBlocked}</p>}
                  {tooLong && <p className="text-xs text-red-600 dark:text-red-400">At most {QUESTION_MAX} characters.</p>}
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <button type="button" className={btn} disabled={pending || noLines} onClick={() => (setStartingOver(true), setUnsaved(null), setQuestion(""), setError(null))}>
                      Start over
                    </button>
                    {noLines && <span className="text-xs text-zinc-500">{CHAPTER_MESSAGES.noLines}</span>}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
        {wide && (
          <aside className="hidden lg:sticky lg:top-16 lg:block" data-testid="chapter-board">
            {board ?? <p className="text-sm text-zinc-500">Click a move in the answer to see it on the board.</p>}
          </aside>
        )}
      </div>
      {!wide && target && sheetOpen && (
        <div role="dialog" aria-label="Board" className="fixed inset-0 z-50 flex flex-col overflow-y-auto overscroll-contain bg-zinc-50 dark:bg-zinc-950" data-testid="chapter-board-sheet">
          <div className="sticky top-0 z-10 flex items-center border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
            <button type="button" onClick={() => setSheetOpen(false)} className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
              ← Back to the text
            </button>
          </div>
          <div className="mx-auto w-full max-w-2xl flex-1 p-4">{board}</div>
        </div>
      )}
      {exploreFen && <ExploreLayer fen={exploreFen} orientation={orientation} onClose={() => setExploreFen(null)} />}
    </div>
  );
}
