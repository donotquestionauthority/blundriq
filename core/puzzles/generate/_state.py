"""The rule that decides who owns a board.

At most one active non-repertoire puzzle exists per board (the partial unique index
`ix_puzzles_player_standard_fen`). Two generators and the player all want to put a
puzzle there, so a board has an owner class and the classes have a precedence:

    custom > own_mate > blunder_auto > cc0

**A puzzle the player made by hand outranks everything**, including a missed mate: it is
there because he decided that position was worth practising, and displacing it would
throw away its progress along with his intent. Between the generators, a missed mate is
the stronger lesson than the blunder that led to it, and a corpus puzzle that happens to
sit on a board he actually blundered gives way to the real thing.

Both generators read the same `active_puzzles` CTE, so the classes cannot drift apart.
The generator's worklist is a FULL OUTER JOIN of what qualifies now against what is
active: without the outer arm a puzzle whose evidence has aged out is never deactivated
and the board stays stale forever.
"""

from __future__ import annotations

from core.constants import PLAYER_ID

NONE = "none"
OWN_MATE = "own_mate"
CUSTOM = "custom"
BLUNDER_AUTO = "blunder_auto"
CC0 = "cc0"

# Ordered strongest first. A generator displaces only classes weaker than its own, so
# `custom` at the head is what makes a hand-made puzzle untouchable by either generator.
PRECEDENCE = (CUSTOM, OWN_MATE, BLUNDER_AUTO, CC0)

# 'custom' is tested before 'blunder': a puzzle the player created from a blunder
# position carries both, and the player's intent wins.
ACTIVE_PUZZLES_CTE = f"""active_puzzles AS (
    SELECT pz.id AS active_puzzle_id, pz.canonical_fen, pz.fen,
           CASE
               WHEN pz.source_types @> ARRAY['own_mate']    THEN '{OWN_MATE}'
               WHEN pz.source_types @> ARRAY['lichess_cc0'] THEN '{CC0}'
               WHEN pz.source_types @> ARRAY['custom']      THEN '{CUSTOM}'
               WHEN pz.source_types @> ARRAY['blunder']     THEN '{BLUNDER_AUTO}'
               ELSE '{CUSTOM}'
           END AS active_class
    FROM puzzles pz
    WHERE pz.player_id = {PLAYER_ID} AND pz.is_repertoire = FALSE AND pz.active = TRUE
)"""


def displaceable(by: str, active_class: str) -> bool:
    """True if a generator of class `by` may deactivate a puzzle of `active_class`."""
    if active_class == NONE:
        return False
    return PRECEDENCE.index(active_class) > PRECEDENCE.index(by)
