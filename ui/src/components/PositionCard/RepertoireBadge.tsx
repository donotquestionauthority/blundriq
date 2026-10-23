import { useCoverage } from "../../hooks/useCoverage";
import type { RepertoireCoverage } from "../../repertoire";
import { LineReaderPanel } from "./LineReaderPanel";

/** Whether a card's board is in the repertoire (`hooks/useCoverage`), and the line to read from there. */

/** Book › chapter › line, and how many other lines hold the board. */
function whereInBook(c: RepertoireCoverage): string | null {
  if (!c.book) return null;
  const more = c.more_lines > 0 ? ` (+${c.more_lines} more)` : "";
  return [c.book, c.chapter, c.line_name].filter(Boolean).join(" › ") + more;
}

/** What the book says about the card's move here. With no move to judge, the book move alone. */
function Verdict({ c, move }: { c: RepertoireCoverage; move: string | null }) {
  switch (c.status) {
    case "match":
    case "agree":
      if (move && c.played_is_book) return <>you played the book move</>;
      if (c.book_move)
        return (
          <>
            your repertoire plays <span className="font-mono font-semibold">{c.book_move}</span> here
          </>
        );
      return null;
    case "end_of_line":
      return <>the line ends here</>;
    case "conflict":
      return <>your lines disagree here</>;
    default:
      return null;
  }
}

/** What a page offers on a board the repertoire does not cover: the click, whether its read is
 *  in flight, and the reason the last one was refused (shown under the line, for this card). */
export interface AddToRepertoire {
  onAdd: () => void;
  pending: boolean;
  error: string | null;
}

export function RepertoireBadge({ fen, move, add }: { fen: string; move: string | null; add?: AddToRepertoire }) {
  const cov = useCoverage(fen, move);
  // Nothing while loading: the walk-through below unmounts with it, so stepping to another card
  // covered by the same line starts it closed, at the new card's ply.
  if (cov.status !== "known") return null;
  const c = cov.data;
  if (c.status === "none")
    return (
      <div className="space-y-1" data-testid="repertoire-none">
        <p className="text-xs text-zinc-500">
          <span>Not in your repertoire</span>
          {add && (
            <>
              {" · "}
              <button type="button" disabled={add.pending} onClick={add.onAdd} className="rounded border border-zinc-300 px-2 py-0.5 text-xs font-medium text-zinc-700 hover:border-zinc-500 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300">
                {add.pending ? "Opening…" : "Add to repertoire"}
              </button>
            </>
          )}
        </p>
        {add?.error && (
          <p role="alert" className="text-xs text-red-600 dark:text-red-400">
            {add.error}
          </p>
        )}
      </div>
    );
  const where = whereInBook(c);
  const said = Verdict({ c, move });
  return (
    <div className="space-y-2" data-testid="repertoire-coverage">
      <p className="text-sm">
        <span className="font-medium text-amber-800 dark:text-amber-300">📖 In your repertoire</span>
        {c.transposed ? <span className="text-xs text-zinc-500"> (by transposition)</span> : null}
        {where && <span className="text-xs text-zinc-500"> · {where}</span>}
        {said && <span className="text-zinc-700 dark:text-zinc-300"> — {said}</span>}
      </p>
      {c.line_id != null && <LineReaderPanel fen={fen} repertoireLineId={c.line_id} initialPly={c.line_ply ?? 0} />}
    </div>
  );
}
