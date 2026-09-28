import { Chess } from "chess.js";
import type { Move } from "chess.js";
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

/**
 * Play `move` — a SAN token or a `{from, to, promotion?}` object — on `g` and return the move, or
 * null when it is not a legal move there. chess.js accepts the null move `--` (a "move" from a
 * square to itself that passes the turn) and returns it as if legal; python-chess does the same,
 * and the server refuses it — so nothing here may take `g.move()`'s truthiness as legality. An
 * unparseable token throws in chess.js; the caller's own try/catch decides what that means for it.
 */
export function legalMove(g: Chess, move: string | { from: string; to: string; promotion?: string }): Move | null {
  const played = g.move(move);
  if (!played || played.san === "--" || played.from === played.to) return null;
  return played;
}

/** True when chess.js can seed a board from `fen`: the launchers' only gate on Explore. */
export function parsesAsFen(fen: string): boolean {
  try {
    new Chess(fen);
    return true;
  } catch {
    return false;
  }
}

/** `[from, to]` of a SAN move in a position, or null if it is not legal there. */
export function sanToSquares(fen: string, san: string): [string, string] | null {
  try {
    const move = legalMove(new Chess(fen), san);
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
    for (let i = 0; i < ply - 1; i++) if (!legalMove(game, moves[i])) return null;
    const move = legalMove(game, moves[ply - 1]);
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

/** A decision node's arrows: one per opponent reply, its opacity scaled by how often they chose
 *  it, under a blue arrow for the move that led here when the coverage agrees on one. A reply that
 *  is not legal on the board draws nothing. */
export function decisionNodeArrows(p: { fen: string; replies: { move: string; cnt: number }[] | null | undefined; leadIn?: string | null; leadPreFen?: string | null }): BoardArrow[] {
  const arrows: BoardArrow[] = [];
  if (p.leadIn && p.leadPreFen) {
    const sq = sanToSquares(p.leadPreFen, p.leadIn);
    if (sq) arrows.push({ startSquare: sq[0], endSquare: sq[1], color: ARROWS.opponent });
  }
  const replies = p.replies ?? [];
  const maxCnt = Math.max(...replies.map((r) => r.cnt), 1);
  for (const r of replies) {
    const sq = sanToSquares(p.fen, r.move);
    if (!sq) continue;
    const alpha = Math.round(255 * (0.35 + 0.65 * (r.cnt / maxCnt)));
    arrows.push({ startSquare: sq[0], endSquare: sq[1], color: `${ARROWS.book}${alpha.toString(16).padStart(2, "0")}` });
  }
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
      if (!legalMove(g, line[i])) return null;
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

/**
 * The (fen, move) pair the solver's Similar-positions search asks about: the board at the latest
 * player decision on the walked path and the line's move there. Unlike `branchCompareTarget` it
 * needs no parent, so the first decision (index 0) and a one-move player-first puzzle both have a
 * target. Let `i` be the largest player-ply index `<= min(moveIndex, line.length - 1)` — player
 * plies are the even indexes when `color` is to move on `activeFen`, the odd ones otherwise; the
 * target is the board after `line[0..i)` from `activeFen` and `move = line[i]`. Null for an empty
 * line, when no player ply is within the bound yet (an opponent-first puzzle at index 0, before the
 * reply has auto-played), and — failing closed — when a move of `line[0..i]` is not legal on the
 * board it is applied to (the move at `i` included, so the server is never asked about a token
 * the board cannot play; a null move `--` counts as not legal). Map mode is the caller's to exclude.
 */
export function similarTarget(activeFen: string, line: string[], moveIndex: number, color: "w" | "b"): { fen: string; move: string } | null {
  if (line.length === 0) return null;
  try {
    const g = new Chess(activeFen);
    const playerFirst = g.turn() === color;
    const bound = Math.min(moveIndex, line.length - 1);
    let i = -1;
    for (let k = 0; k <= bound; k++) if ((k % 2 === 0) === playerFirst) i = k;
    if (i < 0) return null;
    for (let k = 0; k < i; k++) if (!legalMove(g, line[k])) return null;
    const fen = g.fen();
    if (!legalMove(g, line[i])) return null;
    return { fen, move: line[i] };
  } catch {
    return null;
  }
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

// --- the game review's arrows ------------------------------------------------------------------

/** True when two SAN spellings name the same move up to suffixes and castle notation. */
export function sansEquivalent(a: string, b: string): boolean {
  return normalizeSan(a) === normalizeSan(b);
}

/** `[from, to]` of the move that produced `fenSequence[ply]`: `moves[ply - 1]` played from
 *  `fenSequence[ply - 1]`. Derived from the stored spine rather than a replay from the standard
 *  start, so a game from any starting position reads the same. */
export function spineLastMove(fenSequence: string[] | null | undefined, moves: string[] | null | undefined, ply: number): [string, string] | null {
  if (!fenSequence || !moves || ply < 1) return null;
  const before = fenSequence[ply - 1];
  const san = moves[ply - 1];
  return before && san ? sanToSquares(before, san) : null;
}

export interface ReviewArrows {
  arrows: BoardArrow[];
  /** True iff a `bestMissed` arrow was drawn — the played-move squares then take the frame, not
   *  the yellow fill. Returned by the builder rather than inferred from arrow colours. */
  bestMissed: boolean;
}

/**
 * The game review's arrows on an ordinary ply, from the stored per-position analysis: a sky-blue
 * `bestHint` for the engine's move in the position shown, and one ply after a decision that was
 * not the engine's, a yellow `bestMissed` re-drawing the missed move from the prior position's
 * squares. `plyAnalysis` is one entry per position, so it indexes by ply.
 */
export function reviewArrowsFromPlyAnalysis(p: { fenSequence: string[]; ply: number; plyAnalysis: { best_move?: string | null }[]; moves: string[] | null }): ReviewArrows {
  const arrows: BoardArrow[] = [];
  let bestMissed = false;
  const here = p.plyAnalysis[p.ply];
  if (here?.best_move && p.fenSequence[p.ply]) {
    const sq = sanToSquares(p.fenSequence[p.ply], here.best_move);
    if (sq) arrows.push({ startSquare: sq[0], endSquare: sq[1], color: ARROWS.bestHint });
  }
  if (p.ply >= 1 && p.fenSequence[p.ply - 1]) {
    const prev = p.plyAnalysis[p.ply - 1];
    const played = p.moves ? p.moves[p.ply - 1] : null;
    if (prev?.best_move && (!played || !sansEquivalent(played, prev.best_move))) {
      const sq = sanToSquares(p.fenSequence[p.ply - 1], prev.best_move);
      if (sq) {
        arrows.push({ startSquare: sq[0], endSquare: sq[1], color: ARROWS.bestMissed });
        bestMissed = true;
      }
    }
  }
  return { arrows, bestMissed };
}

/** The same two arrows from blunder rows alone, for a game with no stored per-position series. */
export function reviewArrowsForPly(p: { fenSequence: string[]; ply: number; blunderAtPly?: { best_move?: string | null } | null; blunderAtPrevPly?: { best_move?: string | null; move_played?: string | null } | null }): ReviewArrows {
  const arrows: BoardArrow[] = [];
  let bestMissed = false;
  if (p.blunderAtPly?.best_move && p.fenSequence[p.ply]) {
    const sq = sanToSquares(p.fenSequence[p.ply], p.blunderAtPly.best_move);
    if (sq) arrows.push({ startSquare: sq[0], endSquare: sq[1], color: ARROWS.bestHint });
  }
  const prev = p.blunderAtPrevPly;
  if (prev?.best_move && p.ply >= 1 && p.fenSequence[p.ply - 1] && (!prev.move_played || !sansEquivalent(prev.move_played, prev.best_move))) {
    const sq = sanToSquares(p.fenSequence[p.ply - 1], prev.best_move);
    if (sq) {
      arrows.push({ startSquare: sq[0], endSquare: sq[1], color: ARROWS.bestMissed });
      bestMissed = true;
    }
  }
  return { arrows, bestMissed };
}

/** The five meanings of a Learn reveal, in row order — which is also the merge precedence. */
export type RevealSlot = "committed" | "engine" | "book" | "played" | "opponent";

export const REVEAL_SLOT_LABEL: Record<RevealSlot, string> = {
  committed: "You played",
  engine: "Stockfish plays",
  book: "Your prep plays",
  played: "You played in the game",
  opponent: "Opponent's last move",
};

const REVEAL_SLOT_COLOR: Record<RevealSlot, string> = { committed: ARROWS.committed, engine: ARROWS.engine, book: ARROWS.book, played: ARROWS.played, opponent: ARROWS.opponent };
const REVEAL_SLOT_ORDER: RevealSlot[] = ["committed", "engine", "book", "played", "opponent"];

export interface RevealRow {
  slots: RevealSlot[];
  /** The slot labels joined by ` · ` in precedence order. */
  label: string;
  /** The SAN as the panel names it, promotion piece included: the only place it is on screen. */
  move: string;
  from: string;
  to: string;
  color: string;
}

export interface RevealArrows {
  arrows: BoardArrow[];
  rows: RevealRow[];
}

/**
 * The Learn reveal: at most one arrow per from/to pair, and a legend row per arrow naming every
 * meaning it carries. Collisions are the common case (the committed move is the game's, or the
 * engine's, or the book's), and react-chessboard keys arrows by their squares, so two slots on
 * one pair would render as one arrow in whichever colour won: the first slot in row order keeps
 * the colour and leads the label. The opponent's move resolves against `prevFen`, every other
 * slot against `fen`. A slot with no SAN, or one that is not legal in its position, is skipped.
 */
export function buildLearnRevealArrows(p: { fen: string; prevFen?: string | null; committedMove?: string | null; engineMove?: string | null; bookMove?: string | null; gameMove?: string | null; opponentMove?: string | null }): RevealArrows {
  const san: Record<RevealSlot, string | null | undefined> = { committed: p.committedMove, engine: p.engineMove, book: p.bookMove, played: p.gameMove, opponent: p.opponentMove };
  const arrows: BoardArrow[] = [];
  const rows: RevealRow[] = [];
  const byPair = new Map<string, RevealRow>();
  for (const slot of REVEAL_SLOT_ORDER) {
    const move = san[slot];
    if (!move) continue;
    const source = slot === "opponent" ? p.prevFen : p.fen;
    if (!source) continue;
    const sq = sanToSquares(source, move);
    if (!sq) continue;
    const key = `${sq[0]}-${sq[1]}`;
    const existing = byPair.get(key);
    if (existing) {
      existing.slots.push(slot);
      existing.label = existing.slots.map((s) => REVEAL_SLOT_LABEL[s]).join(" · ");
      continue;
    }
    const row: RevealRow = { slots: [slot], label: REVEAL_SLOT_LABEL[slot], move, from: sq[0], to: sq[1], color: REVEAL_SLOT_COLOR[slot] };
    byPair.set(key, row);
    rows.push(row);
    arrows.push({ startSquare: sq[0], endSquare: sq[1], color: row.color });
  }
  return { arrows, rows };
}

/** The book arrow on an ordinary review ply, unless its pair is already drawn (one arrow per pair:
 *  a second on the same squares would silently replace the first). `drew` is what the panel's
 *  swatch keys on — a swatch beside an arrow that merged away would claim a colour not on the board. */
export function appendBookArrow(arrows: BoardArrow[], fen: string, bookMove: string | null | undefined): { arrows: BoardArrow[]; drew: boolean } {
  if (!bookMove) return { arrows, drew: false };
  const sq = sanToSquares(fen, bookMove);
  if (!sq) return { arrows, drew: false };
  if (arrows.some((a) => a.startSquare === sq[0] && a.endSquare === sq[1])) return { arrows, drew: false };
  return { arrows: [...arrows, { startSquare: sq[0], endSquare: sq[1], color: ARROWS.book }], drew: true };
}
