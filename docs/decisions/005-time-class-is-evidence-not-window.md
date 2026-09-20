# 005 — Time class filters evidence, Chess960 filters the window

Two rules that look alike and are applied at different points.

A **Chess960 game is never training material**, so it never takes a slot in the
recent-N window: the window is the most recent N *standard* games
(`core.chess.eligibility.window_cte`).

A **blitz game is still one of the player's recent games**. It just is not what he is
studying, so it occupies its slot and is then excluded as evidence
(`core.chess.eligibility.evidence_sql`, driven by the `time_class_focus` setting,
"rapid_plus" by default).

Filtering time class before the window would quietly change "your last 500 games" into
"your last 500 rapid games" and reach months further back than intended. Ranking first
and filtering after keeps the window meaning what it says.

The old system applied both rules this way in the blunder and missed-mate puzzle
worklists — but not in the repertoire one, which had neither filter. Here every
worklist goes through `core/chess/eligibility.py` (CLAUDE.md rule 7), so the
repertoire window gains the Chess960 exclusion the other two already had. A line whose
deviations were all in Chess960 games no longer generates a puzzle, which is correct:
there is no repertoire to deviate from in Chess960.
