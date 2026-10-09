"""The two writes both standard generators make, in the order they must happen.

Deactivate first, create second. The unique index that keeps one active puzzle per
board is partial on `active = TRUE`, so creating before deactivating would make it
unsatisfiable for as long as the statement ran.

A board that comes back keeps its history. Before inserting, `create` looks for an
inactive puzzle the same generator wrote on the same board (the same source tags: never
a hand-made, corpus or repertoire row) and reactivates it with the new solution, keeping
its id, its attempts and its spaced-repetition row; a board whose evidence left the window
and returned, or that a new engine stopped and then started flagging, does not restart
from nothing.

The insert is `ON CONFLICT DO NOTHING`, never a caught unique violation: these run in a
loop inside one transaction, and an aborted transaction would throw away the work already
done. A conflict means another writer — the sibling generator, or serve-time corpus
materialisation — got there first, which is a no-op here and resolves on the next run.
The reactivation is an UPDATE and has no ON CONFLICT, so it runs in a savepoint: the
same competing writer between the generator's read and this write makes the unique index
refuse it, the savepoint rolls back that one statement, the board is skipped exactly as
the insert's conflict is, and the transaction stays usable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from psycopg import Connection
from psycopg.errors import UniqueViolation

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


@dataclass(frozen=True)
class Written:
    """What `create` did: `(id, fen)` of the rows it inserted and of the rows it brought back."""

    created: list[tuple[int, str]] = field(default_factory=lambda: [])
    reactivated: list[tuple[int, str]] = field(default_factory=lambda: [])


def _reactivate(conn: Connection[Any], params: dict[str, Any]) -> tuple[int, str] | None:
    """Bring back the inactive puzzle this generator last wrote on the board, if there is one:
    the same source tags exactly, the most recently deactivated first. None when there is none,
    or when the board gained an active owner since the generator read it (the savepoint undoes
    the refused update; the next run sees the new owner)."""
    try:
        with conn.transaction():
            row = conn.execute(
                """
                UPDATE puzzles SET
                    active = TRUE, fen = %(fen)s, solution_line = %(line)s::jsonb,
                    solution_fen_sequence = %(seq)s::jsonb, color = %(color)s, themes = %(themes)s::text[],
                    title = %(title)s, description = %(description)s, acceptance_map = %(amap)s::jsonb,
                    updated_at = now()
                WHERE id = (
                    SELECT id FROM puzzles
                    WHERE player_id = %(player)s AND is_repertoire = FALSE AND active = FALSE
                      AND canonical_fen = bq_canonical_fen(%(fen)s) || ' 0 1' AND source_types = %(sources)s::text[]
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                )
                RETURNING id, fen
                """,
                params,
            ).fetchone()
    except UniqueViolation:
        return None
    return (int(row["id"]), str(row["fen"])) if row is not None else None


def create(conn: Connection[Any], puzzles: list[NewPuzzle]) -> Written:
    """Reactivate or insert puzzles, skipping any board that already has an active puzzle."""
    out = Written()
    if not puzzles:
        return out
    with conn.cursor() as cur:
        for puzzle in puzzles:
            params = {
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
            }
            # A hand-made puzzle is new every time: the player removed the old one on purpose.
            back = None if "custom" in puzzle.source_types else _reactivate(conn, params)
            if back is not None:
                out.reactivated.append(back)
                continue
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
                params,
            )
            row = cur.fetchone()
            if row is not None:
                out.created.append((int(row["id"]), str(row["fen"])))
    return out
