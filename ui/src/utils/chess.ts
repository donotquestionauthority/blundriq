import { Chess } from "chess.js";
import { ARROWS } from "./board";

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

/** `[from, to]` of a SAN move in a position, or null if it is not legal there. */
export function sanToSquares(fen: string, san: string): [string, string] | null {
  try {
    const move = new Chess(fen).move(san);
    return move ? [move.from, move.to] : null;
  } catch {
    return null;
  }
}

/** `[from, to]` of the move that produced the position after `ply` moves (the opponent's last
 *  move, when the player is to move there). Null when the moves are not stored. */
export function lastMoveSquares(moves: string[] | null | undefined, ply: number | null | undefined): [string, string] | null {
  if (!moves || !ply || ply < 1 || ply > moves.length) return null;
  try {
    const game = new Chess();
    for (let i = 0; i < ply - 1; i++) game.move(moves[i]);
    const move = game.move(moves[ply - 1]);
    return move ? [move.from, move.to] : null;
  } catch {
    return null;
  }
}

export interface BoardArrow {
  startSquare: string;
  endSquare: string;
  color: string;
}

/** The three arrows of a mistake: how the position arose, what was played, what was best.
 *  When played and best share a shaft the best move is drawn last and covers it. */
export function buildArrows(p: { fen: string; moves?: string[] | null; ply?: number | null; movePlayed?: string | null; bestMove?: string | null }): BoardArrow[] {
  const arrows: BoardArrow[] = [];
  const add = (sq: [string, string] | null, color: string) => {
    if (sq) arrows.push({ startSquare: sq[0], endSquare: sq[1], color });
  };
  add(lastMoveSquares(p.moves, p.ply), ARROWS.opponent);
  if (p.movePlayed) add(sanToSquares(p.fen, p.movePlayed), ARROWS.played);
  if (p.bestMove && p.bestMove !== p.movePlayed) add(sanToSquares(p.fen, p.bestMove), ARROWS.engine);
  return arrows;
}

/**
 * The (fen, preFen) pair the branch compare launches from: the latest player-decision node on the
 * solver's walked path (the start FEN plus `line[0..moveIndex-1]`) and its parent. Walking back
 * from the current node finds the last position with `color` to move that has a parent; null when
 * there is none, or the line is malformed. The server validates the pair again.
 */
export function branchCompareTarget(startFen: string, line: string[], moveIndex: number, color: "w" | "b"): { fen: string; preFen: string } | null {
  try {
    const g = new Chess(startFen);
    const replay = [g.fen()];
    for (let i = 0; i < moveIndex && i < line.length; i++) {
      g.move(line[i]);
      replay.push(g.fen());
    }
    for (let j = replay.length - 1; j >= 1; j--) {
      if (replay[j].split(" ")[1] === color) return { fen: replay[j], preFen: replay[j - 1] };
    }
  } catch {
    /* malformed line */
  }
  return null;
}

/** `1. e4 e5 2. Nf3` for the first `ply` moves (all of them when `ply` is omitted). */
export function buildPgn(moves: string[] | null | undefined, ply?: number | null): string {
  if (!moves?.length) return "";
  const slice = typeof ply === "number" && ply >= 0 ? moves.slice(0, ply) : moves;
  return slice.map((m, i) => (i % 2 === 0 ? `${i / 2 + 1}. ${m}` : m)).join(" ");
}

/** Lichess analysis board at the position after `ply` moves, from the player's side. */
export function lichessAnalyzeUrl(moves: string[] | null | undefined, ply: number | null | undefined, color: string): string {
  if (!moves || !ply) return "https://lichess.org/analysis";
  const pgn = buildPgn(moves, ply).replace(/ /g, "_");
  return `https://lichess.org/analysis/pgn/${pgn}${color === "black" ? "?color=black" : ""}`;
}

/** `12... Nc6 13. Bb5`, numbered from the starting position's own move number. */
export function numberedLine(startingFen: string, moves: string[]): string[] {
  const parts = startingFen.split(" ");
  let white = (parts[1] || "w") === "w";
  let number = parseInt(parts[5] || "1", 10) || 1;
  return moves.map((san, i) => {
    const token = white ? `${number}. ${san}` : i === 0 ? `${number}... ${san}` : san;
    if (!white) number++;
    white = !white;
    return token;
  });
}

/** "1.e4 e5 2.Nf3" from a move list, or "—" for none. */
export function numbered(moves: string[]): string {
  return moves.map((m, i) => (i % 2 === 0 ? `${i / 2 + 1}.${m}` : m)).join(" ") || "—";
}

/** One colour per distinct move, in a fixed order, so the same board reads the same twice. */
const MOVE_COLOURS = [ARROWS.book, ARROWS.engine, ARROWS.played, ARROWS.opponent];

/** Arrows for the moves prescribed at `fen`, in the order given, each in its own colour unless
 *  one is named; a move that is not in play is drawn faded. A move that is not legal on the
 *  board draws nothing. */
export function moveArrows(fen: string, moves: { move: string; inPlay: boolean; color?: string }[]): BoardArrow[] {
  const out: BoardArrow[] = [];
  moves.forEach((m, i) => {
    const sq = sanToSquares(fen, m.move);
    if (sq) out.push({ startSquare: sq[0], endSquare: sq[1], color: `${m.color ?? MOVE_COLOURS[i % MOVE_COLOURS.length]}${m.inPlay ? "" : "66"}` });
  });
  return out;
}
