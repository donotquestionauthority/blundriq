import { Chess } from "chess.js";
import { useEffect, useId, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Chessboard } from "react-chessboard";
import { ApiError } from "../../api";
import { deleteAnnotation, getAnnotation, getLineAnnotated, putAnnotation } from "../../repertoire";
import type { Annotation, LineNote, LineReaderLine, LineReaderPosition } from "../../repertoire";
import { HIGHLIGHT, SQUARES } from "../../utils/board";
import { formatAnnotationText } from "./annotationText";
import { editTargetAt, fullMoveCount, moveListEntries, moveNotation, nextNotePly, noteAnchorPlies, parseNoteSegments, prevNotePly, stickyNoteAt } from "./lineReader";

/**
 * Notes on positions. Without a line: the note on this one board (the editor on every card).
 * With a line: "Read the whole line", a walk-through of the line with the author's note at
 * each move and an editor that writes to the position the board is showing.
 */

const btn = "rounded border border-zinc-300 px-2.5 py-1 text-xs font-medium hover:border-zinc-500 disabled:opacity-40 dark:border-zinc-700";
const btnOn = "rounded border border-zinc-900 bg-zinc-900 px-2.5 py-1 text-xs font-medium text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900";
const link = "text-xs text-zinc-500 hover:text-zinc-900 disabled:opacity-50 dark:hover:text-zinc-100";

function detailOf(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.message ? e.message : fallback;
}

export function Attribution({ note }: { note: LineNote | Annotation }) {
  if (note.source === "manual") return <p className="text-xs text-zinc-500">Your note</p>;
  if (!note.author && !note.book_title) return null;
  return (
    <p className="text-xs text-zinc-500">
      —{note.author ? ` ${note.author}` : ""}
      {note.author && note.book_title ? ", " : note.book_title ? " " : ""}
      {note.book_title && <em>{note.book_title}</em>}
    </p>
  );
}

/** Add, edit, replace or delete the note at `fen`, attached to `lineId` or to the bare position.
 *  A course note is not edited in place: the editor opens blank and saving replaces it. */
export function NoteEditor({ fen, existingNote, onMutated, lineId = null }: { fen: string; existingNote: LineNote | Annotation | null; onMutated: () => void; lineId?: number | null }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const text = draft.trim();
    if (!text) return;
    setSaving(true);
    setError(null);
    try {
      await putAnnotation(fen, text, lineId);
      setEditing(false);
      onMutated();
    } catch (e) {
      setError(detailOf(e, "Could not save the note."));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setSaving(true);
    setError(null);
    try {
      await deleteAnnotation(fen, lineId);
      setEditing(false);
      onMutated();
    } catch (e) {
      setError(detailOf(e, "Could not delete the note."));
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-3 pt-1">
        <button
          type="button"
          className={link}
          onClick={() => {
            setDraft(existingNote?.source === "manual" ? existingNote.text : "");
            setError(null);
            setEditing(true);
          }}
        >
          {existingNote ? (existingNote.source === "manual" ? "✎ Edit note" : "✎ Replace with your note") : "＋ Add note here"}
        </button>
        {existingNote && (
          <button type="button" className={link} disabled={saving} onClick={() => void remove()}>
            Delete
          </button>
        )}
        {error && (
          <span role="alert" className="text-xs text-red-600 dark:text-red-400">
            {error}
          </span>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-2 pt-1">
      <textarea aria-label="Note" value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} placeholder={existingNote && existingNote.source !== "manual" ? "Saving replaces the course note with yours…" : "A note about this position…"} className="w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
      {error && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btnOn} disabled={saving || !draft.trim()} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
        {existingNote && (
          <button type="button" className={btn} disabled={saving} onClick={() => void remove()}>
            Delete
          </button>
        )}
        <button type="button" className={btn} disabled={saving} onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function SinglePosition({ fen }: { fen: string }) {
  const [note, setNote] = useState<Annotation | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);

  const refetch = () => getAnnotation(fen).then(setNote, () => setNote(null));

  useEffect(() => {
    let cancelled = false;
    getAnnotation(fen)
      .then((n) => {
        if (!cancelled) setNote(n);
      })
      .catch(() => {
        if (!cancelled) setNote(null);
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [fen]);

  if (!loaded) return null;
  return (
    <div className="space-y-2">
      <button type="button" aria-expanded={open} className={open ? btnOn : btn} onClick={() => setOpen((o) => !o)}>
        {note ? "📝 Note" : "＋ Add note"}
      </button>
      {open && (
        <div className="space-y-2 rounded border border-zinc-200 p-3 dark:border-zinc-800">
          {note && (
            <>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{formatAnnotationText(note.text)}</p>
              <Attribution note={note} />
            </>
          )}
          <NoteEditor key={fen} fen={fen} existingNote={note} onMutated={() => void refetch()} />
        </div>
      )}
    </div>
  );
}

function lastMoveSquares(positions: LineReaderPosition[], curPly: number): [string, string] | null {
  if (curPly <= 0) return null;
  const prev = positions[curPly - 1];
  if (!prev?.move) return null;
  try {
    const mv = new Chess(prev.fen).move(prev.move);
    return mv ? [mv.from, mv.to] : null;
  } catch {
    return null;
  }
}

function StickyNote({ line, curPly, onJump }: { line: LineReaderLine; curPly: number; onJump: (ply: number) => void }) {
  const sticky = stickyNoteAt(line.positions, curPly);
  const moves = useMemo(() => line.positions.map((p) => p.move), [line.positions]);
  const segments = useMemo(() => (sticky ? parseNoteSegments(sticky.note.text, moves, sticky.anchorPly) : []), [sticky, moves]);
  if (!sticky) return <p className="text-sm text-zinc-500">No note covers this move yet.</p>;
  const about = moveNotation(line.positions, sticky.anchorPly + 1);
  const carried = sticky.anchorPly < curPly - 1;
  return (
    <div className="space-y-1">
      <p className="text-sm leading-relaxed">
        {segments.map((seg, i) =>
          seg.kind === "text" ? (
            <span key={i} className="whitespace-pre-wrap">
              {seg.value}
            </span>
          ) : seg.jumpPly != null ? (
            <button key={i} type="button" className="font-mono font-semibold text-sky-700 hover:underline dark:text-sky-400" onClick={() => onJump(seg.jumpPly!)}>
              {seg.san}
            </button>
          ) : (
            <span key={i} className="font-mono font-semibold">
              {seg.san}
            </span>
          ),
        )}
      </p>
      <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <Attribution note={sticky.note} />
        {about && (
          <span>
            · on {about}
            {carried ? " (carried forward)" : ""}
          </span>
        )}
        {sticky.note.from_chapter && (
          <span className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] dark:border-zinc-700">
            from <em>{sticky.note.from_chapter}</em>
          </span>
        )}
      </div>
    </div>
  );
}

/** The whole line, move by move, with the author's notes and an editor for the current ply. */
export function LineWalkthrough({ lineId }: { lineId: number }) {
  const [line, setLine] = useState<LineReaderLine | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [curPly, setCurPly] = useState(0);
  const boardId = "lw" + useId().replace(/[^a-zA-Z0-9-]/g, "");

  useEffect(() => {
    let cancelled = false;
    getLineAnnotated(lineId)
      .then((res) => {
        if (!cancelled) setLine(res);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(detailOf(e, "Could not load the line."));
      });
    return () => {
      cancelled = true;
    };
  }, [lineId]);

  // After a note is saved or deleted the line is read again; the board stays where it was.
  const reload = () =>
    getLineAnnotated(lineId).then(
      (res) => {
        setLine(res);
        setCurPly((p) => Math.min(p, Math.max(0, res.positions.length - 1)));
      },
      (e: unknown) => setError(detailOf(e, "Could not load the line.")),
    );

  const last = useMemo(() => (line ? lastMoveSquares(line.positions, curPly) : null), [line, curPly]);
  const squareStyles = useMemo(() => {
    const s: Record<string, CSSProperties> = {};
    if (last) {
      s[last[0]] = { backgroundColor: HIGHLIGHT.lastMove };
      s[last[1]] = { backgroundColor: HIGHLIGHT.lastMove };
    }
    return s;
  }, [last]);

  if (error)
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        {error}
      </p>
    );
  if (!line) return <p className="text-sm text-zinc-500">Loading line…</p>;

  const positions = line.positions;
  const total = positions.length;
  const cur = positions[curPly];
  const anchors = noteAnchorPlies(positions);
  const prevNote = prevNotePly(positions, curPly);
  const nextNote = nextNotePly(positions, curPly);
  const target = editTargetAt(positions, curPly);

  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-500">
        {line.book_title} · {line.chapter_title} · <span className="text-zinc-700 dark:text-zinc-300">{line.line_name}</span>
      </p>
      <div className="mx-auto aspect-square w-full max-w-[360px]">
        <Chessboard options={{ id: boardId, position: cur.fen, boardOrientation: line.color, allowDragging: false, squareStyles, animationDurationInMs: 150, boardStyle: { borderRadius: "6px" }, ...SQUARES }} />
      </div>
      <div className="flex items-center justify-between gap-2">
        <button type="button" className={btn} disabled={curPly === 0} onClick={() => setCurPly((p) => Math.max(0, p - 1))}>
          ‹ Prev
        </button>
        <span className="text-xs text-zinc-500" data-testid="walkthrough-position">
          {curPly === 0 ? (
            <>
              Start · <span className="font-mono">{fullMoveCount(positions)}</span> moves
            </>
          ) : (
            <>
              <span className="font-mono text-zinc-900 dark:text-zinc-100">{moveNotation(positions, curPly) ?? "·"}</span> · move <span className="font-mono">{Math.floor((curPly - 1) / 2) + 1}</span> of <span className="font-mono">{fullMoveCount(positions)}</span>
            </>
          )}
        </span>
        <button type="button" className={btn} disabled={curPly >= total - 1} onClick={() => setCurPly((p) => Math.min(total - 1, p + 1))}>
          Next ›
        </button>
      </div>
      {anchors.length > 0 && (
        <div className="flex items-center justify-center gap-2">
          <button type="button" className={btn} disabled={prevNote == null} onClick={() => prevNote != null && setCurPly(prevNote)}>
            ‹ Prev note
          </button>
          <button type="button" className={btn} disabled={nextNote == null} onClick={() => nextNote != null && setCurPly(nextNote)}>
            Next note ›
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-1 text-xs" aria-label="Moves">
        <button type="button" aria-label="Go to start" onClick={() => setCurPly(0)} className={`rounded px-1.5 py-0.5 font-mono ${curPly === 0 ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"}`}>
          Start
        </button>
        {moveListEntries(positions).map((e) => (
          <button key={e.ply} type="button" aria-label={`Go to ${moveNotation(positions, e.ply) ?? e.ply}${e.hasNote ? " (has a note)" : ""}`} onClick={() => setCurPly(e.ply)} className={`rounded px-1.5 py-0.5 font-mono ${e.ply === curPly ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : e.hasNote ? "bg-zinc-100 text-sky-700 dark:bg-zinc-800 dark:text-sky-400" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"}`}>
            {e.san ?? "·"}
            {e.hasNote ? " •" : ""}
          </button>
        ))}
      </div>
      <div className="space-y-2 rounded border border-zinc-200 p-3 dark:border-zinc-800">
        <StickyNote line={line} curPly={curPly} onJump={setCurPly} />
        {target && <NoteEditor key={curPly} fen={target.fen} existingNote={target.existingNote} onMutated={() => void reload()} lineId={lineId} />}
      </div>
    </div>
  );
}

/** Keyed by what it shows, so a new position or line starts from a fresh, closed panel. */
export function LineReaderPanel({ fen, repertoireLineId = null }: { fen: string; repertoireLineId?: number | null }) {
  const [open, setOpen] = useState(false);
  if (repertoireLineId == null) return <SinglePosition key={fen} fen={fen} />;
  return (
    <div className="space-y-2">
      <button type="button" aria-expanded={open} className={open ? btnOn : btn} onClick={() => setOpen((o) => !o)}>
        📖 Read the whole line
      </button>
      {open && (
        <div className="rounded border border-zinc-200 p-3 dark:border-zinc-800">
          <LineWalkthrough key={repertoireLineId} lineId={repertoireLineId} />
        </div>
      )}
    </div>
  );
}
