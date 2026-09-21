"""Puzzles from forced mates the player had and did not play.

Unlike blunders there is no recurrence threshold: missing a mate once is enough. Every
tagged missed mate within `missed_mate_max_moves` qualifies, and when the same board
produced several the shortest mate wins.

What makes these different is how they are graded. A mate in three usually has more
than one way to force it, and the rule is that any line forcing mate in exactly that
many moves is right while a line forcing it in more is wrong. One stored solution
cannot express that, so generation solves the position exhaustively and stores an
acceptance map of every optimal move at every node the player can reach, plus the one
canonical defence the opponent plays at each of theirs (see
`core.chess.mate_acceptance`).

That solve also checks the stored distance. If the exhaustive search finds a shorter
forced mate than the analyser recorded, the stored distance was not optimal and the
position is skipped rather than served with a map that contradicts its own puzzle.
Positions whose map would be too large, or whose solve costs too much, are skipped for
the same reason: there is one contract, and no weaker one to fall back to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql, evidence_sql
from core.chess.mate_acceptance import BuildStats, build_acceptance_map
from core.constants import PLAYER_ID
from core.puzzles.generate import _state
from core.puzzles.generate._write import NewPuzzle, create, deactivate
from core.settings import Settings


def _worklist_sql(focus: str) -> LiteralString:
    """Built per call: the evidence predicate depends on the time-class focus setting.

    There is no games window here — eligibility is the mate distance, not recency — so
    the time-class filter is applied directly to the event's game.
    """
    return cast(
        LiteralString,
        f"""
WITH mate_events AS (
    SELECT pme.id,
           cg.fen_sequence ->> pme.ply AS fen,
           pme.mate_in_moves,
           pme.player_color,
           cg.ply_analysis -> pme.ply ->> 'best_line' AS best_line
    FROM player_motif_events pme
    JOIN chess_games cg ON cg.id = pme.chess_game_id
    WHERE pme.player_id = %(pid)s
      AND pme.metric_type = 'mate'
      AND pme.found = FALSE
      AND pme.mate_in_moves IS NOT NULL
      AND pme.mate_in_moves <= %(max_moves)s
      AND {analysable_sql("cg")}
      AND {evidence_sql("cg", focus)}
      AND jsonb_typeof(cg.fen_sequence) = 'array'
      AND jsonb_typeof(cg.ply_analysis) = 'array'
      AND jsonb_array_length(cg.fen_sequence) > pme.ply
      AND jsonb_array_length(cg.ply_analysis) > pme.ply
      AND cg.fen_sequence ->> pme.ply IS NOT NULL
      AND cg.ply_analysis -> pme.ply ->> 'best_line' IS NOT NULL
),
qualifying AS (
    SELECT DISTINCT ON (bq_canonical_fen(m.fen) || ' 0 1')
           bq_canonical_fen(m.fen) || ' 0 1' AS canonical_fen,
           m.fen, m.mate_in_moves, m.player_color, m.best_line
    FROM mate_events m
    WHERE NOT EXISTS (
        SELECT 1 FROM dismissed_blunder_fens d
        WHERE d.player_id = %(pid)s AND d.canonical_fen = bq_canonical_fen(m.fen) || ' 0 1')
    ORDER BY bq_canonical_fen(m.fen) || ' 0 1', m.mate_in_moves ASC, m.id
),
{_state.ACTIVE_PUZZLES_CTE}
SELECT COALESCE(q.fen, a.fen) AS fen,
       (q.canonical_fen IS NOT NULL) AS qualifies,
       q.mate_in_moves, q.player_color, q.best_line,
       COALESCE(a.active_class, '{_state.NONE}') AS active_class,
       a.active_puzzle_id
FROM qualifying q
FULL OUTER JOIN active_puzzles a ON a.canonical_fen = q.canonical_fen
""",
    )


@dataclass
class Stats:
    boards: int = 0
    created: int = 0
    deactivated: int = 0
    unchanged: int = 0
    skipped_stronger: int = 0
    skipped_no_map: int = 0
    skip_reasons: dict[str, int] = field(default_factory=lambda: {})


def _stats_dict(stats: Stats) -> dict[str, Any]:
    return {
        "boards": stats.boards,
        "created": stats.created,
        "deactivated": stats.deactivated,
        "unchanged": stats.unchanged,
        "skipped_stronger": stats.skipped_stronger,
        "skipped_no_map": stats.skipped_no_map,
        "skip_reasons": stats.skip_reasons,
    }


def generate(conn: Connection[Any], config: Settings) -> dict[str, Any]:
    """Reconcile missed-mate puzzles with the evidence. Idempotent; does not commit."""
    with conn.cursor() as cur:
        cur.execute(
            _worklist_sql(config.time_class_focus),
            # The acceptance-map builder solves exhaustively and refuses anything past mate in
            # five, so the setting is capped here rather than qualifying boards it cannot build.
            {"pid": PLAYER_ID, "max_moves": min(5, config.missed_mate_max_moves)},
        )
        rows = cur.fetchall()

    stats = Stats(boards=len(rows))
    to_deactivate: list[int] = []
    to_create: list[NewPuzzle] = []

    for row in rows:
        active_class = str(row["active_class"])
        if not row["qualifies"]:
            if active_class == _state.OWN_MATE and row["active_puzzle_id"] is not None:
                to_deactivate.append(int(row["active_puzzle_id"]))
            continue
        if active_class == _state.OWN_MATE:
            stats.unchanged += 1
            continue
        if active_class != _state.NONE and not _state.displaceable(_state.OWN_MATE, active_class):
            stats.skipped_stronger += 1
            continue
        solution = str(row["best_line"] or "").split()
        build = BuildStats()
        acceptance = build_acceptance_map(str(row["fen"]), int(row["mate_in_moves"]), stats=build) if solution else None
        if acceptance is None:
            # Same ordering rule as the blunder generator: prove the puzzle can be
            # built before displacing whatever is on the board.
            stats.skipped_no_map += 1
            stats.skip_reasons[build.skip_reason] = stats.skip_reasons.get(build.skip_reason, 0) + 1
            continue
        if row["active_puzzle_id"] is not None:
            to_deactivate.append(int(row["active_puzzle_id"]))
        to_create.append(
            NewPuzzle(
                fen=str(row["fen"]),
                # The whole forced-mate line, untruncated: the puzzle is the mate.
                solution_line=solution,
                source_types=["own_mate"],
                color="w" if str(row["player_color"]) == "white" else "b",
                themes=["mate"],
                acceptance_map=acceptance,
            )
        )

    stats.deactivated = deactivate(conn, to_deactivate)
    stats.created = len(create(conn, to_create))
    return _stats_dict(stats)
