"""Puzzles from repertoire lines the player keeps leaving.

These are keyed on the line, not on a board: one puzzle per active repertoire line the
player has deviated from in at least `deviation_puzzle_min_occurrences` distinct games
inside the deviation window (`deviations_default_last_n_games`). They live outside the
ownership contest the other two generators run (`_state`), because a line puzzle is
identified by its line and nothing else competes for it.

The line's stored FEN sequence is the authority, not a replay from the standard start:
a chapter rooted at a tabiya begins there, and `fen_sequence[0]` is the position the
puzzle shows. The puzzle stores the whole line; how much of it is shown is decided when
it is served, from how far into the line the player actually got.

Three things can be true of a line that already has a puzzle, and they are deliberately
different:

* the stored puzzle matches the line — nothing happens, not even a write;
* the puzzle is inactive and still matches — it is reactivated with its SRS progress
  intact, which is the reason a line going quiet soft-disables rather than deletes;
* the line has changed under it — the puzzle is rebuilt and its SRS state is deleted,
  because the player's progress was against a different solution. Their attempts are
  kept as a record.

A line whose stored data is malformed is skipped and its puzzle soft-disabled, with SRS
untouched: a re-import can heal the line, and the player should not lose progress to a
temporary import problem.

Unlike the other two generators this one does not filter by time class: leaving a
prepared line is the same mistake in a blitz game as in a rapid one, and the line is
what is being practised either way (docs/decisions/005).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql
from core.constants import PLAYER_ID
from core.puzzles.lines import fen_sequence
from core.settings import Settings

# A line stops being the player's for three reasons, not one: the line itself, its
# chapter, or its book can be deactivated. The worklist only sees lines whose whole
# chain is active, so a puzzle under a disabled chapter would otherwise never be
# revisited and would keep serving for ever.
_ORPHANS = """
UPDATE puzzles p SET active = FALSE, updated_at = now()
FROM repertoire_lines rl
JOIN chapters ch ON ch.id = rl.chapter_id
JOIN books bk ON bk.id = ch.book_id
WHERE p.repertoire_line_id = rl.id AND p.is_repertoire = TRUE AND p.active = TRUE
  AND (rl.active = FALSE OR ch.active = FALSE OR bk.active = FALSE)
"""

_WORKLIST = cast(
    LiteralString,
    f"""
WITH windowed AS (
    SELECT pg.chess_game_id
    FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
    WHERE pg.player_id = %(pid)s AND {analysable_sql("cg")}
    ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC
    LIMIT %(window)s
),
line_events AS (
    SELECT grl.line_id, count(DISTINCT grr.chess_game_id) AS games
    FROM game_result_lines grl
    JOIN game_repertoire_results grr ON grr.id = grl.game_repertoire_result_id
    JOIN windowed w ON w.chess_game_id = grr.chess_game_id
    WHERE grr.player_id = %(pid)s AND grr.deviation_by = 'me'
    GROUP BY grl.line_id
)
SELECT rl.id AS line_id, rl.line_name, rl.moves, rl.fen_sequence, bk.color,
       bk.title AS book_title, ch.title AS chapter_title,
       COALESCE(ev.games, 0) AS event_count,
       pz.id AS existing_id, pz.active AS existing_active, pz.fen AS existing_fen,
       pz.solution_line AS existing_solution_line,
       pz.solution_fen_sequence AS existing_solution_fen_sequence
FROM repertoire_lines rl
JOIN chapters ch ON ch.id = rl.chapter_id
JOIN books bk ON bk.id = ch.book_id
LEFT JOIN line_events ev ON ev.line_id = rl.id
LEFT JOIN puzzles pz ON pz.repertoire_line_id = rl.id
WHERE bk.player_id = %(pid)s AND rl.active = TRUE AND ch.active = TRUE AND bk.active = TRUE
""",
)

_UPSERT = """
INSERT INTO puzzles (
    fen, solution_line, solution_fen_sequence, source_types, color, title,
    is_repertoire, repertoire_line_id, player_id, active, created_at, updated_at)
VALUES (%(fen)s, %(line)s::jsonb, %(seq)s::jsonb, ARRAY['deviation'], %(color)s, %(title)s,
        TRUE, %(line_id)s, %(player)s, TRUE, now(), now())
ON CONFLICT (player_id, repertoire_line_id) WHERE is_repertoire = TRUE
DO UPDATE SET active = TRUE,
              fen = EXCLUDED.fen,
              solution_line = EXCLUDED.solution_line,
              solution_fen_sequence = EXCLUDED.solution_fen_sequence,
              title = EXCLUDED.title,
              updated_at = now()
RETURNING id
"""


def _as_list(value: object) -> list[Any] | None:
    """A stored jsonb array as a list, or None if it is not one. A jsonb column can come
    back as a decoded list or, from an older row, as a JSON string."""
    if isinstance(value, list):
        return [item for item in cast(list[Any], value)]
    if isinstance(value, str):
        try:
            decoded: Any = json.loads(value)
        except ValueError:
            return None
        return [item for item in cast(list[Any], decoded)] if isinstance(decoded, list) else None
    return None


@dataclass
class Stats:
    lines: int = 0
    created: int = 0
    rebuilt: int = 0
    reactivated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    orphaned: int = 0
    srs_reset: int = 0
    skipped_bad_line: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "lines": self.lines,
            "created": self.created,
            "rebuilt": self.rebuilt,
            "reactivated": self.reactivated,
            "unchanged": self.unchanged,
            "deactivated": self.deactivated,
            "orphaned": self.orphaned,
            "srs_reset": self.srs_reset,
            "skipped_bad_line": self.skipped_bad_line,
        }


def generate(conn: Connection[Any], config: Settings) -> dict[str, int]:
    """Reconcile repertoire puzzles with the lines. Idempotent; does not commit."""
    stats = Stats()
    with conn.cursor() as cur:
        cur.execute(_ORPHANS)
        stats.orphaned = cur.rowcount
        cur.execute(
            _WORKLIST,
            {"pid": PLAYER_ID, "window": config.deviations_default_last_n_games},
        )
        rows = cur.fetchall()
    stats.lines = len(rows)

    to_deactivate: list[int] = []
    to_reset: list[int] = []
    upserts: list[dict[str, Any]] = []

    for row in rows:
        existing_id = row["existing_id"]
        moves = _as_list(row["moves"])
        fens = _as_list(row["fen_sequence"])
        if moves is None or fens is None or not fens or len(fens) != len(moves) + 1:
            # The line itself is unusable. Stop serving its puzzle, but leave the
            # player's SRS state alone: a re-import can make the line valid again.
            stats.skipped_bad_line += 1
            if existing_id is not None and row["existing_active"]:
                to_deactivate.append(int(existing_id))
            continue

        if int(row["event_count"]) < config.deviation_puzzle_min_occurrences:
            if existing_id is not None and row["existing_active"]:
                to_deactivate.append(int(existing_id))
            continue

        fresh_fen = str(fens[0])
        drifted = existing_id is not None and (
            str(row["existing_fen"]) != fresh_fen
            or _as_list(row["existing_solution_line"]) != moves
            or _as_list(row["existing_solution_fen_sequence"]) != fens
        )
        if existing_id is not None and not drifted:
            if row["existing_active"]:
                stats.unchanged += 1
                continue  # steady state writes nothing at all
            stats.reactivated += 1
        elif existing_id is None:
            stats.created += 1
        else:
            stats.rebuilt += 1
            to_reset.append(int(existing_id))

        upserts.append(
            {
                "fen": fresh_fen,
                "line": json.dumps(moves),
                "seq": json.dumps(fens),
                "color": "w" if str(row["color"]) == "white" else "b",
                "title": f"{row['book_title']} > {row['chapter_title']} > {row['line_name']}",
                "line_id": int(row["line_id"]),
                "player": PLAYER_ID,
            }
        )

    with conn.cursor() as cur:
        if to_deactivate:
            cur.execute("UPDATE puzzles SET active = FALSE, updated_at = now() WHERE id = ANY(%s)", (to_deactivate,))
            stats.deactivated = cur.rowcount
        if to_reset:
            # The solution changed, so progress against the old one is meaningless.
            # Attempts stay: they are a record of what happened, not scheduling state.
            cur.execute("DELETE FROM player_puzzle_state WHERE puzzle_id = ANY(%s)", (to_reset,))
            stats.srs_reset = cur.rowcount
        for params in upserts:
            cur.execute(_UPSERT, params)
    return stats.as_dict()


def rebuild_fen_sequence(fen: str, moves: list[str]) -> list[str]:
    """A line's FEN sequence recomputed from its moves. Only used by the oracle diff:
    generation stores what the importer recorded, and never recomputes it."""
    return fen_sequence(fen, moves)
