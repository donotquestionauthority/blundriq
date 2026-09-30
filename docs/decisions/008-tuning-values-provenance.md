# 008 — Where the tuning values came from

The defaults in `core/settings.py` are the values in use on 2026-09-19. Before the
old system's checkouts were archived, one pass over its code, design memos and
handover notes recovered which of those values were argued for and which were not.
The reasons themselves sit above the fields they belong to; this note is the
inventory, so nobody re-derives a number that was never derived.

## Argued for, with evidence

- `miss_contested_gate`: a loss from a decided position is noise; drop the move.
- `motif_min_material_gain` (the old SEE > 0 rule), `motif_found_material_tolerance`
  (one piece, from six misclassified positions), and the eval-tolerance "found" test
  that replaced `move == best`, all ratified by Rob.
- The puzzle mix's hybrid supply rule (rotation buckets fill, SRS buckets serve only
  what is due), each batch targeting its percentages independently — the old system's
  trailing-window catch-up was removed because repaying a shortfall flooded the queue —
  and the shift of first-class share down / remaining up (five themes at 30 % made every
  batch look the same).
- Weak-motif weighting by raw miss count (Rob, over the design's severity weighting) in
  the old system's capped pool; this serve keeps the frequency principle as an ordering
  only (see the divergences below).
- Corpus: the −325 default offset (Rob's paired accounts), the 1050–2700 import range,
  the 500-per-cell cap (database size), the top-K candidate pool (150 distinct puzzles
  ever served under `LIMIT 1`), and the theme classes (first class = what the tagger
  detects; bare `mate` excluded on purpose).
- Review: candidates measured at a settled endpoint, CONF 15 (validated on ~1,000
  games) and 20 at depth ≤ 12, pool floors 5/8 (Rob: 2-event pools are noise), cost
  charged once per decision, shape metrics never used for severity — though the old
  code labelled every review knob provisional regardless (next section).
- Blunder, deviation and repertoire recurrence counts are distinct games, after an
  endgame reached repeatedly in one game was flagged as recurring (the weak-motif gate
  counts miss events).

## Labelled provisional by their own design

All of the `review_*` knobs: "provisional, unfrozen pending per-band calibration" (a
second rating band and a depth-12-vs-18 sensitivity) that was never run. Of them,
`review_early_k_plies`, `review_early_ply_cap`, `review_missed_win_shed`,
`review_faded_peak_es`, `review_quiesce_max_plies`, `review_recency_half_life_games`
and the n/(n+3) severity shrink have no validation behind them at all.

## Never explained anywhere

The 50/100/200/300 centipawn ladder; `missed_mate_max_moves` = 3; every SRS number
(intervals, retry, 2-of-3 demotion, 2 hits / 300 games); batch 12 and window 50; the
weak-motif target 20 and theme cap 40 %; the coverage thresholds; the tier widths
beyond `normal`; the per-time-class rating offsets; bucket width 100; K = 40 rather
than another K; `scout_bayesian_prior_strength`.

## What the export had already moved, and the divergences to know about

Two seeded defaults differed from the live values at export (admin edits; the rebuild
keeps the live values): `srs_advance_threshold` 2 → 1 (May 2026, the month Rob recorded
that promotions felt too slow; the observation itself stayed open), and the mix
25/30/15/10/20 → 25/20/35/10/10.

The old puzzle path centred the corpus window with the single `default` offset only;
the per-time-class offsets were read by Scout's opponent comparison and were never
calibrated against the corpus. `core/puzzles/serve.py` applies the time-class value
when the latest game has one. At Rob's ratings the difference is inside the tier band
(rapid −250 vs default −325); it is recorded here rather than changed.

The old system kept a materialised pool of weak-motif puzzles, `weak_motif_target_count`
deep, each theme's share capped at `weak_motif_theme_cap_pct` and weighted by its miss
count. This serve has no pool: `_weak_theme_order` ranks the first-class themes by
misses (at or above `weak_motif_min_occurrences`) and the first-class bucket
round-robins one candidate per theme in that order, so one weak theme can supply the
whole bucket. The two knobs, and `puzzle_mix_window`, are retained in the settings row
with no consumer, as are the six `coverage_*` fields of the unported Stats page. All nine
descriptions say "Not used by this implementation". Removing them is a migration of the settings row; restoring the
weighting is a serving change. Neither is this note's decision.

## Not a home for future values

A new setting gets its reason in the comment above its field when it is added, or an
honest "chosen, not derived". This note is not appended to.
