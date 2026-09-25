/** The pieces the Repertoire page and the Conflicts page share: the on/off switch, a static
 *  board with one arrow per move, and the dialog for a line the server would not switch on. */
import { useEffect, useId, useRef } from "react";
import { Chessboard } from "react-chessboard";
import { Link } from "react-router";
import { conflictsPath } from "../repertoire";
import type { Refusal } from "../repertoire";
import { ARROWS, SQUARES } from "../utils/board";
import type { BoardArrow } from "../utils/chess";
import { moveArrows } from "../utils/chess";

const toggle = (on: boolean) => `shrink-0 rounded border px-2 py-0.5 text-xs font-medium ${on ? "border-emerald-600 text-emerald-700 dark:border-emerald-500 dark:text-emerald-400" : "border-zinc-300 text-zinc-400 dark:border-zinc-700"}`;

/** `onChange` gets the switch itself, so a dialog the flip opens can hand focus back to it. */
export function Toggle({ on, label, busy, onChange }: { on: boolean; label: string; busy: boolean; onChange: (on: boolean, el: HTMLButtonElement) => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={busy} className={`${toggle(on)} disabled:opacity-50`} onClick={(e) => (e.stopPropagation(), onChange(!on, e.currentTarget))}>
      {on ? "On" : "Off"}
    </button>
  );
}

export function ArrowBoard({ id, fen, orientation, arrows, size = 160 }: { id: string; fen: string; orientation: "white" | "black"; arrows: BoardArrow[]; size?: number }) {
  return (
    <div style={{ width: size }} className="shrink-0 self-start">
      <Chessboard options={{ id, position: fen, allowDragging: false, boardStyle: { borderRadius: "6px" }, ...SQUARES, boardOrientation: orientation, arrows }} />
    </div>
  );
}

/** Why a line could not be switched on, and where to resolve it. Escape closes; the caller
 *  returns focus to the toggle that opened it. */
export function RefusalDialog({ refusal, orientation, onClose }: { refusal: Refusal; orientation: "white" | "black"; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const boardId = useId().replace(/[^a-zA-Z0-9-]/g, "");
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  const rivalMoves = [...new Set(refusal.rivals.map((r) => r.move))];
  const arrows = moveArrows(refusal.fen, [...rivalMoves.map((m) => ({ move: m, inPlay: true, color: ARROWS.book })), { move: refusal.move, inPlay: true, color: ARROWS.played }]);
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="refusal-title" className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded border border-zinc-200 bg-zinc-50 p-4 shadow-lg dark:border-zinc-800 dark:bg-zinc-950" onClick={(e) => e.stopPropagation()}>
        <h2 id="refusal-title" className="text-base font-semibold">
          Can't switch <span className="italic">{refusal.line_name}</span> on
        </h2>
        <div className="mt-3 flex flex-col gap-3 sm:flex-row">
          <ArrowBoard id={boardId} fen={refusal.fen} orientation={orientation} arrows={arrows} />
          <div className="space-y-1 text-sm">
            <p>
              <span className="italic">{refusal.line_name}</span> plays <span className="font-mono font-medium">{refusal.move}</span> here
              {refusal.rivals.length === 0 ? "." : ";"}
            </p>
            {refusal.rivals.map((r) => (
              <p key={r.line_id}>
                <span className="italic">{r.line_name}</span> <span className="text-zinc-500">({r.book_title} / {r.chapter_title})</span> plays <span className="font-mono font-medium">{r.move}</span>.
              </p>
            ))}
            {refusal.reason === "dirty" && <p className="text-zinc-500">Those lines already disagree here; settle them first.</p>}
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Link to={conflictsPath({ fen: refusal.fen })} onClick={onClose} className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900">
            Open in Conflicts
          </Link>
          <button ref={closeRef} type="button" onClick={onClose} className="rounded bg-zinc-900 px-3 py-1 text-sm text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300">
            Keep off
          </button>
        </div>
      </div>
    </div>
  );
}
