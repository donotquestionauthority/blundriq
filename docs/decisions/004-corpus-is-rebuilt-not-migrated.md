# 004 — The Lichess corpus is rebuilt, not carried across

`lichess_puzzles` is a capped sample of Lichess's public CC0 puzzle database: about
six million rows filtered to the most popular few hundred per (theme, rating bucket).
It was the single largest table in the old database at 149 MB of 405 MB, and the
rebuild plan assumed it would be migrated with a prune.

It is not migrated. `pipeline import-corpus --csv <file>` rebuilds it from the
published CSV instead.

Nothing depends on the corpus once a puzzle exists. Serving copies a corpus row into
a `puzzles` row carrying its own position and solution, and whether a position is
fresh is decided from attempts, skips and dismissals — never from the corpus. So a
rebuilt sample cannot orphan a puzzle or lose progress; at worst a candidate the
player has not seen is replaced by another candidate they have not seen.

What this buys: about 149 MB against the 500 MB the database is allowed, no
single-use prune script, and the import path gets exercised now rather than the first
time Rob refreshes the corpus a year from now.

What it costs: one download of the CSV (~300 MB compressed) on the Dell before the
migration, and a few minutes of streaming.

Decided by Rob, 2026-09-20.
