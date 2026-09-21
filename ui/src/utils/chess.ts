import { Chess } from "chess.js";

/**
 * Normalize a SAN token for equivalence comparison. Mirrors the Python normalize_san; the two
 * must agree or a move the client accepts can be rejected by the server (or vice versa).
 *
 *   - trailing check/mate marks (+, #) and annotation glyphs (!, ?, !!, ??, !?, ?!) are
 *     stripped, looping until stable so any ordering of suffixes normalizes
 *   - digit-zero castles (0-0, 0-0-0) become letter-O castles, full-token match only
 *   - surrounding whitespace is trimmed
 */
export function normalizeSan(s: string): string {
  let out = s.trim();
  let prev: string;
  do {
    prev = out;
    out = out.replace(/[+#]$/, "");
    out = out.replace(/(\?\?|!!|\?!|!\?|\?|!)$/, "");
  } while (out !== prev);
  if (out === "0-0-0") out = "O-O-O";
  else if (out === "0-0") out = "O-O";
  return out;
}

/**
 * True iff `storedSan` resolves, on `fen`, to the same move as `played`. SAN is not canonical
 * (disambiguation, castle spelling, suffixes), so acceptance compares the resolved
 * from/to/promotion, never the strings. Parse failure returns false, never throws.
 */
export function sanResolvesToMove(fen: string, storedSan: string, played: { from: string; to: string; promotion?: string }): boolean {
  try {
    const c = new Chess(fen);
    const expected = c.move(normalizeSan(storedSan));
    if (!expected) return false;
    return expected.from === played.from && expected.to === played.to && (expected.promotion ?? "") === (played.promotion ?? "");
  } catch {
    return false;
  }
}

/**
 * EPD key for a chess.js game as python-chess writes board.epd(): the first four FEN fields, but
 * with the en-passant square present only when a LEGAL en-passant capture exists. chess.js
 * versions differ on whether the square is written after any double pawn push; the guard makes
 * the key independent of that. A key with a spurious square misses the acceptance map and a
 * correct move reads as wrong.
 */
export function mapKey(g: Chess): string {
  const parts = g.fen().split(" ");
  if (parts[3] !== "-") {
    const hasLegalEp = g.moves({ verbose: true }).some((m) => m.flags.includes("e"));
    if (!hasLegalEp) parts[3] = "-";
  }
  return parts.slice(0, 4).join(" ");
}

/** UCI for a chess.js move result, matching the map's tokens (python-chess Move.uci()). */
export function moveUci(result: { from: string; to: string; promotion?: string }): string {
  return result.from + result.to + (result.promotion ?? "");
}

/** Parse a UCI token into a chess.js move object. */
export function uciToMove(uci: string): { from: string; to: string; promotion?: string } {
  return { from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4) || undefined };
}
