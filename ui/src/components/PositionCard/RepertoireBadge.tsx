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

export function RepertoireBadge({ fen, move }: { fen: string; move: string | null }) {
  const cov = useCoverage(fen, move);
  if (cov.status !== "known") return null;
  const c = cov.data;
  if (c.status === "none") return <p className="text-xs text-zinc-500">Not in your repertoire</p>;
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
