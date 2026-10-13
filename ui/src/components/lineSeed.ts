/** The seed a gap card's line opens with (components/AddLineModal.tsx): the game's stem to the
 *  gap board, then the engine's line, trimmed to end on the player's move. Pure, out of the
 *  component file so that file exports only a component. */
import { Chess } from "chess.js";
import type { GapCard } from "../repertoire";
import { legalMove } from "../utils/chess";

export const START = new Chess().fen();

/** `moves` played from the start, as many as play. */
export function played(moves: string[]): string[] {
  const game = new Chess(START);
  const out: string[] = [];
  for (const m of moves) {
    let mv;
    try {
      mv = legalMove(game, m);
    } catch {
      mv = null;
    }
    if (!mv) break;
    out.push(mv.san);
  }
  return out;
}

/** Whose move it is after `moves` from the start. */
export function turnAfter(moves: string[]): "w" | "b" {
  const game = new Chess(START);
  for (const m of moves) game.move(m);
  return game.turn();
}

/**
 * The seed a gap card opens with: the game's stem to the gap board, then the engine's line up to
 * `prefillPlies`, trimmed so the line ends on the player's move (the gap board is the player's
 * to move, so an even number of engine plies would end on the opponent's). The stem alone when
 * the card has no engine line.
 */
export function seedFor(card: GapCard, prefillPlies: number, maxPlies: number): string[] {
  const stem = played(card.stem ?? []);
  const colour = card.colour === "white" ? "w" : "b";
  const seed = played([...stem, ...(card.engine?.line ?? []).slice(0, prefillPlies)]).slice(0, maxPlies);
  while (seed.length > stem.length && turnAfter(seed) === colour) seed.pop();
  return seed;
}

