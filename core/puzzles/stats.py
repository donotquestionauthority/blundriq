"""How a puzzle has been practised, per repertoire line: loads, and the first attempt's verdict.

A **load** is one play-through of the solver: the hook mints one `session_id` when a puzzle is
loaded and keeps it across Try Again and Replay, so a load is a distinct session; an attempt
recorded without a session (a row from before the field) is a load of its own. The **first**
attempt of a load is the one with the lowest `attempt_seq`, the order the solver numbered them
in, which is the player's order whatever order the requests reached the server; rows without a
sequence (an older client's) sort after the numbered ones and among themselves by id. Replay's
completion is a later sequence in the same load and counts for nothing here. These are the
numbers the line report shows and the Repertoire page's delete confirm quotes.
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from core.constants import PLAYER_ID

_BY_LINE = """
WITH firsts AS (
    SELECT DISTINCT ON (p.repertoire_line_id, pa.session_id)
           p.repertoire_line_id AS line_id, pa.session_id, pa.solved
    FROM puzzle_attempts pa JOIN puzzles p ON p.id = pa.puzzle_id
    WHERE pa.player_id = %(pid)s AND p.repertoire_line_id = ANY(%(lines)s) AND pa.session_id IS NOT NULL
    ORDER BY p.repertoire_line_id, pa.session_id, pa.attempt_seq NULLS LAST, pa.id
),
loose AS (
    SELECT p.repertoire_line_id AS line_id, pa.solved
    FROM puzzle_attempts pa JOIN puzzles p ON p.id = pa.puzzle_id
    WHERE pa.player_id = %(pid)s AND p.repertoire_line_id = ANY(%(lines)s) AND pa.session_id IS NULL
),
every AS (SELECT line_id, solved FROM firsts UNION ALL SELECT line_id, solved FROM loose)
SELECT line_id, count(*)::int AS loads,
       count(*) FILTER (WHERE solved)::int AS first_right,
       count(*) FILTER (WHERE NOT solved)::int AS first_wrong
FROM every GROUP BY line_id
"""


def empty() -> dict[str, int]:
    return {"loads": 0, "first_right": 0, "first_wrong": 0}


def by_line(conn: Connection[Any], line_ids: list[int]) -> dict[int, dict[str, int]]:
    """`{line_id: {loads, first_right, first_wrong}}` for the lines that have any; a line
    with no puzzle or no attempts is absent (the caller fills `empty()`)."""
    if not line_ids:
        return {}
    rows = conn.execute(_BY_LINE, {"pid": PLAYER_ID, "lines": sorted(set(line_ids))}).fetchall()
    keys = ("loads", "first_right", "first_wrong")
    return {int(r["line_id"]): {k: int(r[k]) for k in keys} for r in rows}
