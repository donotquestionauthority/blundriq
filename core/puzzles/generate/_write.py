"""The two writes both standard generators make, in the order they must happen.

Deactivate first, create second. The unique index that keeps one active puzzle per
board is partial on `active = TRUE`, so creating before deactivating would make it
unsatisfiable for as long as the statement ran.

The create is `ON CONFLICT DO NOTHING`, never a caught unique violation: these run in
a loop inside one transaction, and an aborted transaction would throw away the work
already done. A conflict means another writer — the sibling generator, or serve-time
corpus materialisation — got there first, which is a no-op here and resolves on the
next run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from psycopg import Connection

from core.constants import PLAYER_ID
from core.puzzles.lines import fen_sequence


@dataclass(frozen=True)
class NewPuzzle:
    """A puzzle about to be written. `solution_fen_sequence` is derived, never given."""

    fen: str
    solution_line: list[str]
    source_types: list[str]
    color: str
    themes: list[str] = field(default_factory=lambda: [])
    acceptance_map: dict[str, object] | None = None
    title: str | None = None
    description: str | None = None


def deactivate(conn: Connection[Any], puzzle_ids: list[int]) -> int:
    """Soft-disable puzzles. Their attempts and SRS state are left alone: an inactive
    row is history, and a repertoire line that comes back reuses it."""
    if not puzzle_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("UPDATE puzzles SET active = FALSE, updated_at = now() WHERE id = ANY(%s)", (puzzle_ids,))
        return cur.rowcount


def create(conn: Connection[Any], puzzles: list[NewPuzzle]) -> list[tuple[int, str]]:
    """Insert puzzles, skipping any board that already has an active puzzle.
    Returns `(id, fen)` for the rows actually created."""
    created: list[tuple[int, str]] = []
    if not puzzles:
        return created
    with conn.cursor() as cur:
        for puzzle in puzzles:
            cur.execute(
                """
                INSERT INTO puzzles (
                    fen, solution_line, solution_fen_sequence, source_types, color,
                    themes, title, description, acceptance_map, is_repertoire, player_id, active,
                    created_at, updated_at)
                VALUES (%(fen)s, %(line)s::jsonb, %(seq)s::jsonb, %(sources)s::text[], %(color)s,
                        %(themes)s::text[], %(title)s, %(description)s, %(amap)s::jsonb, FALSE, %(player)s, TRUE,
                        now(), now())
                ON CONFLICT (player_id, canonical_fen) WHERE is_repertoire = FALSE AND active = TRUE
                DO NOTHING
                RETURNING id, fen
                """,
                {
                    "fen": puzzle.fen,
                    "line": json.dumps(puzzle.solution_line),
                    "seq": json.dumps(fen_sequence(puzzle.fen, puzzle.solution_line)),
                    "sources": list(puzzle.source_types),
                    "color": puzzle.color,
                    "themes": list(puzzle.themes),
                    "title": puzzle.title,
                    "description": puzzle.description,
                    "amap": json.dumps(puzzle.acceptance_map) if puzzle.acceptance_map is not None else None,
                    "player": PLAYER_ID,
                },
            )
            row = cur.fetchone()
            if row is not None:
                created.append((int(row["id"]), str(row["fen"])))
    return created
