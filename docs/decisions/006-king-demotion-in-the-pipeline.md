# 006 — King demotion runs in the hourly pipeline, not on page load

A mastered ("king") puzzle un-retires when its pattern recurs in enough distinct analysed
games played after mastery. The old system checked this as a prelude on every read that
touched king state — five GET paths, each needing its own call to stay in step.

Here it is a pipeline step, `pipeline srs-maintain`, run hourly after `analyze`. The rule
is unchanged. What changes is when it runs: a demotion surfaces on the next hourly run
instead of the next page load, and no GET handler writes to the ladder. The step depends
only on newly analysed games and is idempotent, which is what makes it a pipeline step
in the first place.

Ruled by Rob, 2026-09-20.
