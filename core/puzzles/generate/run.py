"""The generation step: the three generators, in one transaction, in one summary.

Order is documentation, not correctness. Each generator reconciles its own class of
puzzle against the evidence and converges to the same state however often it runs and
in whatever order, so a run that loses a race to its sibling simply resolves on the
next hour.
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from core.puzzles.generate import blunder, missed_mate, repertoire
from core.repertoire import matching
from core.settings import Settings


def generate_all(conn: Connection[Any], config: Settings) -> dict[str, Any]:
    """Run every generator. The caller owns the transaction; the repertoire lock is held for
    it, so no import or toggle changes the lines and results under the generators."""
    matching.lock(conn)
    return {
        "repertoire": repertoire.generate(conn, config),
        "missed_mate": missed_mate.generate(conn, config),
        "blunder": blunder.generate(conn, config),
    }
