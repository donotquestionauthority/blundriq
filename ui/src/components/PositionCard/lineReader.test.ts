
import { describe, it, expect } from 'vitest'
import {
  stickyNoteAt,
  noteAnchorPlies,
  nextNotePly,
  prevNotePly,
  editTargetAt,
  resolveSanJumpPly,
  parseNoteSegments,
  moveListEntries,
  moveNotation,
  fullMoveCount,
} from './lineReader'
import type { LineReaderPosition, LineNote } from '../../repertoire'

function note(text: string, source: LineNote['source'] = 'course'): LineNote {
  return { text, source, author: null, book_title: null, from_chapter: null }
}

const FEN_R = 'rep w'
function mk(): LineReaderPosition[] {
  return [
    { ply: 0, fen: FEN_R, move: 'e4', annotation: note('Anchor at 0') },
    { ply: 1, fen: 'p1',  move: 'e5', annotation: null },
    { ply: 2, fen: 'p2',  move: 'Nf3', annotation: note('Anchor at 2') },
    { ply: 3, fen: FEN_R, move: 'Nc6', annotation: note('Anchor at 3 (repeat)') },
    { ply: 4, fen: 'p4',  move: 'Bb5', annotation: null },
    { ply: 5, fen: 'p5',  move: 'a6', annotation: null },
    { ply: 6, fen: 'p6',  move: null, annotation: null },
  ]
}

describe('the sticky note: post-move fill over the raw array', () => {
  it('shows the note of the most recent PLAYED annotated move (not the upcoming one)', () => {
    const P = mk()
    const s1 = stickyNoteAt(P, 1)!
    expect(s1.note.text).toBe('Anchor at 0')
    expect(s1.anchorPly).toBe(0)
    expect(stickyNoteAt(P, 2)?.note.text).toBe('Anchor at 0')
    expect(stickyNoteAt(P, 3)?.note.text).toBe('Anchor at 2')
    expect(stickyNoteAt(P, 4)?.note.text).toBe('Anchor at 3 (repeat)')
  })

  it('back-fills the lead-in from the FIRST note before any annotated move is played', () => {
    const P: LineReaderPosition[] = [
      { ply: 0, fen: 'a', move: 'e4', annotation: null },
      { ply: 1, fen: 'b', move: 'e5', annotation: null },
      { ply: 2, fen: 'c', move: 'Nf3', annotation: note('first anchor at 2') },
      { ply: 3, fen: 'd', move: null, annotation: null },
    ]
    expect(stickyNoteAt(P, 0)?.anchorPly).toBe(2)
    expect(stickyNoteAt(P, 1)?.anchorPly).toBe(2)
    expect(stickyNoteAt(P, 2)?.anchorPly).toBe(2)
    expect(stickyNoteAt(P, 3)?.anchorPly).toBe(2)
  })

  it('keeps the LAST note visible past its arrival (carried forward, no special case)', () => {
    const P = mk()
    expect(stickyNoteAt(P, 4)?.anchorPly).toBe(3)
    for (const ply of [5, 6]) {
      const s = stickyNoteAt(P, ply)!
      expect(s.note.text).toBe('Anchor at 3 (repeat)')
      expect(s.anchorPly).toBe(3)
      expect(s.anchorPly).toBeLessThan(ply - 1)
    }
  })

  it('returns null only when the line has NO notes at all', () => {
    const P: LineReaderPosition[] = [
      { ply: 0, fen: 'a', move: 'e4', annotation: null },
      { ply: 1, fen: 'b', move: null, annotation: null },
    ]
    expect(stickyNoteAt(P, 0)).toBeNull()
    expect(stickyNoteAt(P, 1)).toBeNull()
  })

  it('does NOT dedupe repeated-position anchors — each occurrence counts', () => {
    const P = mk()
    expect(noteAnchorPlies(P)).toEqual([0, 2, 3])
  })
})

describe('move-number helpers (a stepper once counted plies as moves)', () => {
  it('moveNotation renders White as "m.san" and Black as "m…san"', () => {
    const P = mk()
    expect(moveNotation(P, 1)).toBe('1.e4')
    expect(moveNotation(P, 2)).toBe('1…e5')
    expect(moveNotation(P, 3)).toBe('2.Nf3')
    expect(moveNotation(P, 4)).toBe('2…Nc6')
    expect(moveNotation(P, 5)).toBe('3.Bb5')
    expect(moveNotation(P, 6)).toBe('3…a6')
    expect(moveNotation(P, 0)).toBeNull()
    expect(moveNotation(P, 99)).toBeNull()
  })

  it('fullMoveCount counts full moves, a trailing White half-move included', () => {
    const P = mk()                       // 6 plies played
    expect(fullMoveCount(P)).toBe(3)
    expect(fullMoveCount(P.slice(0, 6))).toBe(3)   // 5 plies → 3 (trailing White)
    expect(fullMoveCount(P.slice(0, 1))).toBe(0)   // start only
  })
})

describe('note jumps skip to anchored plies, repeats independent', () => {
  it('next/prev land on raw anchor plies', () => {
    const P = mk()
    expect(nextNotePly(P, 0)).toBe(2)
    expect(nextNotePly(P, 2)).toBe(3)   // the repeat anchor, not skipped
    expect(nextNotePly(P, 3)).toBeNull()
    expect(prevNotePly(P, 4)).toBe(3)
    expect(prevNotePly(P, 3)).toBe(2)
    expect(prevNotePly(P, 0)).toBeNull()
  })
})

describe('the editor targets the current ply FEN, never the carried-forward note', () => {
  it('an un-annotated carried-forward ply edits the CURRENT FEN, not the sticky note', () => {
    const P = mk()
    expect(stickyNoteAt(P, 4)?.anchorPly).toBe(3)
    const t = editTargetAt(P, 4)!
    expect(t.fen).toBe('p4')
    expect(t.existingNote).toBeNull()   // "add" creates here, not an edit of anchor 3
  })

  it('an anchor ply edits its own note', () => {
    const P = mk()
    const t = editTargetAt(P, 2)!
    expect(t.fen).toBe('p2')
    expect(t.existingNote?.text).toBe('Anchor at 2')
  })

  it('repeated-position anchors each behave independently', () => {
    const P = mk()
    expect(editTargetAt(P, 0)!.existingNote?.text).toBe('Anchor at 0')
    expect(editTargetAt(P, 3)!.existingNote?.text).toBe('Anchor at 3 (repeat)')
    expect(editTargetAt(P, 0)!.fen).toBe(editTargetAt(P, 3)!.fen)  // same position…
    expect(editTargetAt(P, 0)!.existingNote).not.toBe(editTargetAt(P, 3)!.existingNote)  // …different anchor
  })
})

describe('the move list is keyed to the ARRIVING move', () => {
  it('entry c jumps to ply c with the arriving-move san and pre-move note-dot', () => {
    const P = mk()
    const entries = moveListEntries(P)
    expect(entries.map((e) => e.ply)).toEqual([1, 2, 3, 4, 5, 6])
    for (const e of entries) expect(e.ply).toBeGreaterThanOrEqual(1)
    expect(entries.map((e) => e.san)).toEqual(['e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6'])
    expect(entries.map((e) => e.hasNote)).toEqual([true, false, true, true, false, false])
    for (const e of entries) {
      expect(e.san).toBe(P[e.ply - 1].move)
      expect(e.hasNote).toBe(P[e.ply - 1].annotation != null)
    }
  })

  it('omits the trailing null move and produces no entry past the last position', () => {
    const P = mk()
    const entries = moveListEntries(P)
    expect(entries).toHaveLength(P.length - 1)
    expect(entries[entries.length - 1].ply).toBe(P.length - 1)
    expect(entries.every((e) => e.san !== null)).toBe(true)
  })
})

describe('deterministic SAN resolution in note text', () => {
  const moves: (string | null)[] = ['e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6', null]

  it('resolves first occurrence at or after the note ply, jumping to position after the move', () => {
    expect(resolveSanJumpPly(moves, 'Nf3', 0)).toBe(3)   // move idx 2 → ply 3
    expect(resolveSanJumpPly(moves, 'Bb5', 2)).toBe(5)   // move idx 4 → ply 5
  })

  it('falls back to the first occurrence overall when none at/after the note ply', () => {
    expect(resolveSanJumpPly(moves, 'e4', 4)).toBe(1)    // only at idx 0 → ply 1
  })

  it('is non-clickable (null) for a token that is not a line move', () => {
    expect(resolveSanJumpPly(moves, 'Qxd5', 0)).toBeNull()
  })

  it('parseNoteSegments splits prose + move tokens and marks clickability', () => {
    const segs = parseNoteSegments(
      'Play @@SANStart@@Nf3@@SANEnd@@ then watch the @@SANStart@@Qxd5@@SANEnd@@ idea',
      moves,
      0,
    )
    expect(segs).toEqual([
      { kind: 'text', value: 'Play ' },
      { kind: 'move', san: 'Nf3', jumpPly: 3 },
      { kind: 'text', value: ' then watch the ' },
      { kind: 'move', san: 'Qxd5', jumpPly: null },
      { kind: 'text', value: ' idea' },
    ])
  })
})

describe('bracketed asides', () => {
  it('render as parentheses in prose, in segments and in plain formatting', () => {
    const segs = parseNoteSegments('play @@StartBracket@@ or wait @@EndBracket@@ then @@SANStart@@e4@@SANEnd@@', ['e4'], 0)
    expect(segs[0]).toEqual({ kind: 'text', value: 'play (or wait) then ' })
    expect(segs[1]).toEqual({ kind: 'move', san: 'e4', jumpPly: 1 })
  })
})
