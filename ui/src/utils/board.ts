/** One look for every board: square colours, move highlights, arrow colours. */

export const SQUARES = { darkSquareStyle: { backgroundColor: "#779952" }, lightSquareStyle: { backgroundColor: "#edeed1" } } as const;

export const HIGHLIGHT = {
  selected: "rgba(255, 255, 0, 0.5)",
  lastMove: "rgba(255, 255, 0, 0.3)",
  correct: "rgba(34, 197, 94, 0.4)",
  wrong: "rgba(239, 68, 68, 0.5)",
  legalDot: "radial-gradient(rgba(0,0,0,0.25) 22%, transparent 22%)",
  legalRing: "radial-gradient(transparent 51%, rgba(0,0,0,0.3) 51%)",
} as const;

/** Arrow colours by meaning. A board never picks a colour inline: the same meaning is the same
 *  colour on every page. Context is quiet, right is green, the game's mistake is red. */
export const ARROWS = {
  opponent: "#52525b", // the opponent's last move: how this position arose (context)
  played: "#e11d48", // the move actually played in the game
  engine: "#16a34a", // the engine's best move, in the position shown or the one before a mistake
  book: "#7c3aed", // the repertoire's move
  committed: "#2563eb", // a Learn answer that matches no other move on the board: not graded
} as const;

/** A colour at reduced strength (an 8-digit hex): the same meaning, said more quietly. */
export function faded(color: string, alpha = 0x66): string {
  return `${color}${alpha.toString(16).padStart(2, "0")}`;
}
