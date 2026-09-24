/**
 * The walk-through's pure logic: which note the board shows at a ply, where the note jumps
 * go, how clickable moves in a note resolve, and what the editor writes to. The server sends
 * one entry per position with the note anchored there or null; everything here is a
 * projection over that array, which stays the single source of truth.
 *
 * A note is anchored to the position *before* its move, and is about that move. So while the
 * board shows ply N the note displayed is the one on the most recently played annotated move
 * (largest anchor below N); before the first note the first one is shown as a lead-in; after
 * the last one it simply stays. The editor, by contrast, always writes to the current ply's own
 * position: adding a note on a carried-forward ply creates a new note there.
 */
import type { LineReaderPosition } from "../../repertoire";

export interface StickyNote {
  note: NonNullable<LineReaderPosition["annotation"]>;
  /** The ply the note is anchored to; its move arrives at anchorPly + 1. */
  anchorPly: number;
}

export function stickyNoteAt(positions: LineReaderPosition[], curPly: number): StickyNote | null {
  let anchorPly = -1;
  for (let k = Math.min(curPly, positions.length) - 1; k >= 0; k--) {
    if (positions[k].annotation != null) {
      anchorPly = k;
      break;
    }
  }
  if (anchorPly < 0) {
    for (let k = curPly; k < positions.length; k++) {
      if (positions[k].annotation != null) {
        anchorPly = k;
        break;
      }
    }
  }
  if (anchorPly < 0) return null;
  return { note: positions[anchorPly].annotation!, anchorPly };
}

/** Notation for the move arriving at `ply` (positions[ply-1].move): "3.Nf3" or "3…Nc6". */
export function moveNotation(positions: LineReaderPosition[], ply: number): string | null {
  if (ply < 1 || ply >= positions.length) return null;
  const san = positions[ply - 1].move;
  if (!san) return null;
  const m = ply - 1;
  const num = Math.floor(m / 2) + 1;
  return m % 2 === 0 ? `${num}.${san}` : `${num}…${san}`;
}

/** Full moves in the line (a trailing White-only half-move counts). */
export function fullMoveCount(positions: LineReaderPosition[]): number {
  return Math.ceil(Math.max(0, positions.length - 1) / 2);
}

/** Every ply carrying a note, ascending; a repeated position is listed each time. */
export function noteAnchorPlies(positions: LineReaderPosition[]): number[] {
  const out: number[] = [];
  for (let k = 0; k < positions.length; k++) if (positions[k].annotation != null) out.push(k);
  return out;
}

export function nextNotePly(positions: LineReaderPosition[], curPly: number): number | null {
  for (let k = curPly + 1; k < positions.length; k++) if (positions[k].annotation != null) return k;
  return null;
}

export function prevNotePly(positions: LineReaderPosition[], curPly: number): number | null {
  for (let k = Math.min(curPly, positions.length) - 1; k >= 0; k--) if (positions[k].annotation != null) return k;
  return null;
}

export interface EditTarget {
  /** Always the current ply's FEN. */
  fen: string;
  /** The note anchored at this ply, if any — null means "add" creates one here. */
  existingNote: LineReaderPosition["annotation"];
}

export function editTargetAt(positions: LineReaderPosition[], curPly: number): EditTarget | null {
  const p = positions[curPly];
  if (!p) return null;
  return { fen: p.fen, existingNote: p.annotation };
}

export interface MoveListEntry {
  /** The ply this entry jumps to: the position after the arriving move. */
  ply: number;
  san: string | null;
  /** Whether the arriving move carries a note (at its pre-move position). */
  hasNote: boolean;
}

/** One entry per played move, keyed by the move arriving at each ply. */
export function moveListEntries(positions: LineReaderPosition[]): MoveListEntry[] {
  const out: MoveListEntry[] = [];
  for (let c = 1; c < positions.length; c++) out.push({ ply: c, san: positions[c - 1].move, hasNote: positions[c - 1].annotation != null });
  return out;
}

/** A course's bracketed aside is stored as `@@StartBracket@@…@@EndBracket@@`; show parentheses. */
export function unwrapBrackets(s: string): string {
  return s.replace(/@@StartBracket@@\s*/g, "(").replace(/\s*@@EndBracket@@/g, ")");
}

export type NoteSegment = { kind: "text"; value: string } | { kind: "move"; san: string; jumpPly: number | null };

const SAN_TOKEN_RE = /@@SANStart@@(.*?)@@SANEnd@@/g;

/** The ply to jump to for a move named in a note: after its first occurrence at or after the
 *  note's anchor, else its first occurrence anywhere, else null (rendered as plain text). */
export function resolveSanJumpPly(moves: (string | null)[], san: string, fromPly: number): number | null {
  let firstOverall = -1;
  let firstAtOrAfter = -1;
  for (let i = 0; i < moves.length; i++) {
    if (moves[i] === san) {
      if (firstOverall < 0) firstOverall = i;
      if (i >= fromPly && firstAtOrAfter < 0) firstAtOrAfter = i;
    }
  }
  const idx = firstAtOrAfter >= 0 ? firstAtOrAfter : firstOverall;
  return idx < 0 ? null : idx + 1;
}

/** A note's text as prose and move segments. Orphan delimiters are dropped from the prose. */
export function parseNoteSegments(rawText: string, moves: (string | null)[], anchorPly: number): NoteSegment[] {
  const segments: NoteSegment[] = [];
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  SAN_TOKEN_RE.lastIndex = 0;
  const stripOrphans = (s: string) => unwrapBrackets(s.replace(/@@SAN(?:Start|End)@@/g, ""));
  while ((m = SAN_TOKEN_RE.exec(rawText)) !== null) {
    if (m.index > lastIndex) {
      const text = stripOrphans(rawText.slice(lastIndex, m.index));
      if (text) segments.push({ kind: "text", value: text });
    }
    segments.push({ kind: "move", san: m[1], jumpPly: resolveSanJumpPly(moves, m[1], anchorPly) });
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < rawText.length) {
    const text = stripOrphans(rawText.slice(lastIndex));
    if (text) segments.push({ kind: "text", value: text });
  }
  return segments;
}
