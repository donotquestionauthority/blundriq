"""Review: where the player loses points, and the decisions that lost them.

`detect` is the pure detector over one game's stored analysis; `window` reads the games and
their context; `write` publishes the tagged games' events in one statement; `run` is the hourly step that ties them
together under two locks. The page is read at request time: `positions` ranks the boards of every
game's opening prefix against the Elo expectation, `position` is one board's page, `habits` groups
the stored events by kind of mistake, and `read` assembles them under the `filters`. Every
position is priced from stored analysis alone (docs/decisions/001).
"""
