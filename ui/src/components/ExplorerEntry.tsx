import { Link } from "react-router";
import { explorerEntry } from "../utils/explorer";
import type { Colour } from "../utils/explorer";

/** "Open in Opening Explorer" at the board on show: a link that pushes (browser Back returns
 *  here), disabled with its reason past the explorer's bound, and absent when the path does not
 *  reach the board. */
export function ExplorerEntry({ moves, ply, fen, colour, className }: { moves: string[] | null | undefined; ply: number | null | undefined; fen: string; colour: Colour; className: string }) {
  const entry = explorerEntry(moves, ply, fen, colour);
  if ("to" in entry)
    return (
      <Link to={entry.to} className={className} data-testid="explorer-entry">
        Open in Opening Explorer
      </Link>
    );
  if (entry.unavailable == null) return null;
  return (
    <div className="space-y-1">
      <button type="button" disabled className={className} data-testid="explorer-entry">
        Open in Opening Explorer
      </button>
      <p className="text-center text-xs text-zinc-500">{entry.unavailable}</p>
    </div>
  );
}
