"""Review: the player decisions that lost the expected score, priced and routed.

`detect` is the pure detector over one game's stored analysis; `window` reads the games and
their context; `write` publishes the tagged games' events in one statement; `run` is the hourly step that ties them
together under two locks. Every position is priced from the stored analysis alone (docs/decisions/001).
"""
