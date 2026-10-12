/**
 * The open line walk-through on the per-game review belongs to the line, not the ply. It is page
 * state (the repertoire panel it is opened from unmounts on every opponent-turn ply), so the page
 * decides at every ply whether the selection still stands; the rule here is that decision.
 */
import type { RepertoireEntry } from "../games";

/** What is open: the line, and the ply in it the walk-through opened at (read once, by the
 *  walk-through, with the line). */
export interface ReviewWalk {
  lineId: number;
  openAt: number;
}

/** Whether the walk-through of `lineId` survives the review arriving at a ply whose repertoire
 *  entry is `entry` (null: no projection at all). An opponent-turn ply says nothing about the line
 *  and leaves it alone; a player-turn ply keeps it exactly when it still offers the same line —
 *  as its selection, or as one of its conflict groups; `none` and `unreadable` offer no line. */
export function walkSurvives(entry: RepertoireEntry | null, lineId: number): boolean {
  if (!entry) return false;
  switch (entry.status) {
    case "not_your_turn":
      return true;
    case "match":
    case "agree":
    case "end_of_line":
      return entry.line_id === lineId;
    case "conflict":
      return (entry.conflict ?? []).some((g) => g.line_id === lineId);
    default:
      return false;
  }
}

/** Where a walk-through opened from the panel starts: on the prescribed move — the board after it
 *  (`linePly + 1`), so the walk-through's note and its ask are about the move the panel named —
 *  or, at the end of a line where there is no move to explain, on the board itself. */
export function walkOpenAt(status: RepertoireEntry["status"], linePly: number): number {
  return status === "end_of_line" ? linePly : linePly + 1;
}
