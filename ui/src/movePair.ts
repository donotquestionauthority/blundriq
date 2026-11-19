import type { MovePairPiece, MovePairQuery } from "./repertoire";

/** The move-pair search's URL value and its parts (`components/MovePairSearch`). */

export const PIECES: { value: MovePairPiece; label: string }[] = [
  { value: "K", label: "King" },
  { value: "Q", label: "Queen" },
  { value: "R", label: "Rook" },
  { value: "B", label: "Bishop" },
  { value: "N", label: "Knight" },
  { value: "P", label: "Pawn" },
];
const PIECE_SET = new Set(PIECES.map((p) => p.value));
export const SQUARE = /^[a-h][1-8]$/;

function move(token: string): { piece: MovePairPiece; square: string } | null {
  const piece = token.slice(0, 1) as MovePairPiece;
  const square = token.slice(1);
  return token.length === 3 && PIECE_SET.has(piece) && SQUARE.test(square) ? { piece, square } : null;
}

/** The search a `pair` value names, or null for anything that is not one. */
export function parsePair(value: string | null): MovePairQuery | null {
  const parts = (value ?? "").split(".");
  if (parts.length !== 4) return null;
  const [colour, first, m1, m2] = parts;
  if ((colour !== "white" && colour !== "black") || (first !== "me" && first !== "opponent")) return null;
  const one = move(m1);
  const two = m2 === "any" ? null : move(m2);
  if (!one || (m2 !== "any" && !two)) return null;
  return { colour, first, p1: one.piece, s1: one.square, move2: two };
}

export function formatPair(q: MovePairQuery): string {
  return [q.colour, q.first, `${q.p1}${q.s1}`, q.move2 ? `${q.move2.piece}${q.move2.square}` : "any"].join(".");
}

export const pieceName = (p: MovePairPiece) => PIECES.find((x) => x.value === p)?.label.toLowerCase() ?? p;
const whose = (mine: boolean) => (mine ? "your" : "the opponent's");

export function describe(q: MovePairQuery): string {
  const mine = q.first === "me";
  const two = q.move2 ? `${whose(!mine)} ${pieceName(q.move2.piece)} to ${q.move2.square}` : "any reply";
  return `${whose(mine)} ${pieceName(q.p1)} to ${q.s1} followed by ${two}`;
}
