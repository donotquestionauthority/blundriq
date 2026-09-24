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
move and the deviation would recur. A toggle carries no gate; when two active lines
disagree at a position, the read side reports the conflict instead of guessing.
