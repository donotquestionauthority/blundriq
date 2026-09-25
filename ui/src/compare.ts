/** Pure helpers for the two compare surfaces: arrows from wire squares, keys and labels. */
import type { CompareBranch, PrepGroup, SimilarNeighbour } from "./repertoire";
import { ARROWS } from "./utils/board";
import { sanToSquares } from "./utils/chess";
import type { BoardArrow } from "./utils/chess";

/** A neighbour's arrows: one blue per distinct arriving (from, to), one orange per book move. */
export function neighbourArrows(n: SimilarNeighbour): BoardArrow[] {
  const arrows: BoardArrow[] = [];
  const seen = new Set<string>();
  for (const g of n.groups) {
    const key = g.arriving.from + g.arriving.to;
    if (seen.has(key)) continue;
    seen.add(key);
    arrows.push({ startSquare: g.arriving.from, endSquare: g.arriving.to, color: ARROWS.opponent });
  }
  for (const g of n.groups) {
    if (g.prep_status !== "move" || !g.prep_move) continue;
    const sq = sanToSquares(n.fen, g.prep_move);
    if (sq) arrows.push({ startSquare: sq[0], endSquare: sq[1], color: ARROWS.book });
  }
  return arrows;
}

/** A branch's arrows: blue for the opponent's move always; orange for the repertoire's reply (the
 *  repertoire is the only source of orange); for a blunder-only branch the move played in vermilion
 *  and the engine's best in green; a scout best move in green when it is the only source. */
export function branchArrows(b: CompareBranch): BoardArrow[] {
  const arrows: BoardArrow[] = [{ startSquare: b.opponent_move.from, endSquare: b.opponent_move.to, color: ARROWS.opponent }];
  const rep = b.sources.repertoire;
  if (rep?.reply_squares) arrows.push({ startSquare: rep.reply_squares.from, endSquare: rep.reply_squares.to, color: ARROWS.book });
  const blu = b.sources.blunders; // null whenever rep is set: never beside orange
  if (blu) {
    arrows.push({ startSquare: blu.worst.move_played_squares.from, endSquare: blu.worst.move_played_squares.to, color: ARROWS.played });
    if (blu.worst.best_move_squares) arrows.push({ startSquare: blu.worst.best_move_squares.from, endSquare: blu.worst.best_move_squares.to, color: ARROWS.engine });
  }
  const sco = b.sources.scout;
  if (sco?.best_move_squares && !rep && !blu) arrows.push({ startSquare: sco.best_move_squares.from, endSquare: sco.best_move_squares.to, color: ARROWS.engine });
  return arrows;
}

/** The one React key for a group; every render site agrees. */
export function groupKey(g: PrepGroup): string {
  return `${g.prep_status}:${g.prep_move ?? g.prep_raw_token ?? ""}:${g.line_id}:${g.line_ply}`;
}

export function prepLabel(g: PrepGroup): string {
  if (g.prep_status === "move") return g.prep_move ?? "";
  if (g.prep_status === "end_of_line") return "line ends here";
  return `unreadable (${g.prep_raw_token ?? "?"})`;
}

export function distanceLabel(n: SimilarNeighbour): string {
  if (n.distance === 0) return "this position";
  if (n.is_castle_shape) return `${n.distance} squares differ · castling`;
  const pieces = n.distance / 2;
  return Number.isInteger(pieces) && pieces <= 3 ? `${n.distance} squares (~${pieces === 1 ? "one piece" : `${pieces} pieces`})` : `${n.distance} squares differ`;
}
