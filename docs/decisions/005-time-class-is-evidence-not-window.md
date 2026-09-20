# 005 — Time class filters evidence, Chess960 filters the window

Two rules that look alike and are applied at different points.

A **Chess960 game is never training material**, so it never takes a slot in the
recent-N window: the window is the most recent N *standard* games
(`core.chess.eligibility.window_cte`). This applies to every worklist, including the
repertoire one, which in the old system had no variant filter at all. A line whose
deviations were all in Chess960 games no longer generates a puzzle, which is right:
there is no repertoire to deviate from in Chess960.

A **blitz game is still one of the player's recent games**. It just is not what he is
studying, so it occupies its slot and is then excluded as evidence
(`core.chess.eligibility.evidence_sql`, driven by the `time_class_focus` setting,
"rapid_plus" by default).

Filtering time class before the window would quietly turn "your last 500 games" into
"your last 500 rapid games" and reach months further back than intended. Ranking first
and filtering after keeps the window meaning what it says.

**Repertoire puzzles are the exception: they ignore time class.** A deviation is about
the opening, not the clock — leaving a prepared line in a blitz game is the same mistake
as leaving it in a rapid one, and the line is the thing being practised either way. This
matches the old system, which applied the time-class filter to the blunder and
missed-mate worklists only. It is a deliberate asymmetry rather than an oversight, and
whether Rob wants it changed is a product question, not a port one.

The rule that every worklist resolves eligibility through `core/chess/eligibility.py`,
rather than writing its own variant literal, is what keeps these three behaviours
describable in one place instead of drifting apart across three queries.
