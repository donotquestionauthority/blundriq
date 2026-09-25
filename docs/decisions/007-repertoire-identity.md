# 007 — Repertoire rows are identified by their source, never rewritten, and switched off rather than deleted

A book is `(player, source_book_id)`. A chapter is `(book, source_chapter_id)` when the id is
known, else `(book, title, root_fen)`; a title-only match adopts a row that has no source id
yet, and a title that already belongs to a chapter with a different id stops the import
rather than fork the learning history. A line is `(chapter, moves)` — the move tokens exactly
as the file spells them, compared raw: every existing line was keyed that way, and
normalising the key would make the whole repertoire "new" on the first run and switch the
originals off as vanished. A note is `(line, fen_norm)`, the first four FEN fields.

An import never updates a line and never deletes one. A line whose moves changed at the
source is a new row; the old row is switched off (only when the file says the book was
extracted completely — an extraction that dropped a note must not read as the author
removing it). Deleting a line would cascade its puzzle away with the spaced-repetition
state on it; rebuilding it in place would reset that state on the next generator run. Kept
immutable, the puzzle survives every re-import.

Everything that reads the repertoire to publish results, or changes it — the hourly
matcher, a rematch, a toggle on the Repertoire page, an import, the puzzle generators —
holds one advisory lock from the read to the commit, so a matcher that read the lines
before a change cannot publish results computed from them after it.

The toggles on the Repertoire page are the one remedy for a deviation that should not
count: there is no dismissal for deviations, because the line would still prescribe the
move and the deviation would recur. Switching off is never gated. Switching on runs the
import's gate: a line, or the lines a chapter or book would bring into play, must agree with
every effectively-active line at each position where the book's side is to move (and, where
no active line covers a position, a container's own lines resolve their disagreement by
plurality, as an import does). A refused line stays off and the page says which position
and which lines refused it; a chapter or book still comes on, with the lines the gate
refused switched off in the same transaction and reported, so a container never brings a
contested position with it. A target that is already on is a no-op: a line that is on is an
anchor, never a candidate, so re-gating it could only switch its fellows off and leave the
results citing them stale. A flip under a container that is off is not gated; the gate runs
when the container comes on. The Conflicts page lists every position where two lines in any
state disagree, marks the ones where two active lines do (the positions the read side fails
closed on), and offers the same toggles to resolve them.
