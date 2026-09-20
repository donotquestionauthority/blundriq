# 003 — Repertoire match tie-break is by line id

When several active lines match a game equally (same number of positions in
order), the game's result row names the book/chapter and expected move of the
FIRST tied line, and every tied line is recorded in `game_result_lines`. The
old system took "first" from the database's physical row order, which changed
as rows were updated, so its choice was not reproducible. The rebuild orders
lines by id.

Verified against the old database (`tools/oracle/diff_matching.py`, 2026-09-20):
every in-window game, identical match/no-match decisions, identical deviation
ply, deviator, played move and tied-line sets; 66 games differ only in which
tied line supplied `expected_move` (58) or `chapter_id` (8).

Not changed: the matcher's semantics. It is called a subsequence match but every
line position must appear in the game in order, so a game that reaches a line's
position by a different move order does not match (0). Changing that is a
product decision for Rob, not a port detail.
