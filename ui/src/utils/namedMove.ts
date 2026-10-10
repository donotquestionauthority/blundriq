/**
 * The move a question names: the first token that is a legal move on the board and not the
 * engine's own move, with the board after it — or the outcome, when the move ends the game and
 * there is nothing to search. A casual mention of a move counts; the panel's chip lets the player
 * say otherwise.
 */
import { Chess } from "chess.js";
import { legalMove, normalizeSan, sansEquivalent } from "./chess";

/** A SAN-shaped token: piece, disambiguation, capture, square, promotion, check — or a castle. */
const SAN_SHAPE = /^(?:[NBRQK]?[a-h]?[1-8]?x?[a-h][1-8](?:=[NBRQ])?[+#]?|O-O(?:-O)?[+#]?|0-0(?:-0)?[+#]?)$/;
const TRIM = /^[("'[]+|[)."'\],;:!?]+$/g;

export type AltOutcome = "checkmate" | "stalemate" | "draw";

export interface NamedMove {
  san: string;
  /** The board after it, or null when the move ends the game. */
  fenAfter: string | null;
  outcome: AltOutcome | null;
}

/** The first token of `question` that is a legal move on `fen` and not `bestMoveSan`. */
export function namedMove(question: string, fen: string, bestMoveSan: string | null): NamedMove | null {
  let seed: Chess;
  try {
    seed = new Chess(fen);
  } catch {
    return null;
  }
  for (const raw of question.split(/\s+/)) {
    const token = raw.replace(TRIM, "");
    if (!token || !SAN_SHAPE.test(token)) continue;
    const g = new Chess(seed.fen());
    let played;
    try {
      played = legalMove(g, normalizeSan(token));
    } catch {
      continue;
    }
    if (!played) continue;
    if (bestMoveSan && sansEquivalent(played.san, bestMoveSan)) continue;
    const outcome: AltOutcome | null = g.isCheckmate() ? "checkmate" : g.isStalemate() ? "stalemate" : g.isInsufficientMaterial() ? "draw" : null;
    return { san: played.san, fenAfter: outcome ? null : g.fen(), outcome };
  }
  return null;
}
