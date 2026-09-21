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

/** Arrow colours by meaning, from a colour-blind-safe palette (Okabe–Ito). A board never
 *  picks a colour inline: the same meaning is the same colour on every page. */
export const ARROWS = {
  opponent: "#0072B2", // the opponent's last move: how this position arose
  played: "#D55E00", // the move actually played
  engine: "#009E73", // the engine's best move
  book: "#E69F00", // the repertoire's move
} as const;
