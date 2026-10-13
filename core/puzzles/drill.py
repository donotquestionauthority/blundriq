"""A drill: the repertoire puzzles of lines the player chose, served whole, in the order chosen.

A drill is not the queue. It has no batch to compose, nothing due or pending, no skip: the
player checked lines on the line report and wants each of them from move 1 until the list is
done. What a drill shares with the queue is the solver, the attempt route and the visibility
read, so a drill attempt is graded, scored and counted like any other.

Serving a drill does three things under the repertoire lock and then the queue lock (that
order, the only order anything takes both in): mints the puzzle of any chosen line that has
none (`generate.repertoire.ensure_line_puzzle`, the generator's own rules); stamps `drilled_at`
on every served puzzle where it is NULL, which keeps the puzzle in the player's practice set
regardless of the deviation gate from then on; and writes one exposure row per served puzzle
under `DRILL_SCOPE`. That exposure is **proof of service and nothing else**: the attempt route
grades an attempt on a no-longer-visible puzzle against it for `DRILL_PROOF_DAYS` whether or not
anything acknowledged it, and the queue ignores the scope entirely (`serve._all_pending_ids`),
so an abandoned, reloaded or second-tab drill holds nothing back from regular practice and
nothing ever closes one. The rows stay; a row per line per drill is the drill's own history.

Every served row is presented in full — `presentation_ply` is the player's last move of the
line — so the solver plays the whole line from its first board, the opponent's replies played
for the player as a line always is.
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from core.constants import DRILL_MAX_LINES, LOCK_PUZZLE_QUEUE, PLAYER_ID
from core.puzzles import visibility
from core.puzzles.generate import repertoire as line_puzzles
from core.puzzles.lines import fen_sequence
from core.puzzles.serve import DRILL_SCOPE, attach, bucket_of, lookahead_plies
from core.repertoire import matching
from core.settings import Settings


class TooManyLines(ValueError):
    pass


def serve_drill(conn: Connection[Any], config: Settings, line_ids: list[int]) -> dict[str, Any]:
    """`{batch_id, puzzles, skipped}`: the rows to play, in the order asked (a line named twice
    is served once, at its first place), and the ids of lines that could not be served (not
    the player's, switched off somewhere up the chain, or malformed). The caller commits."""
    wanted: list[int] = []
    for lid in line_ids:
        if lid not in wanted:
            wanted.append(lid)
    if len(wanted) > DRILL_MAX_LINES:
        raise TooManyLines(f"a drill holds {DRILL_MAX_LINES} lines at most")
    matching.lock(conn)
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_PUZZLE_QUEUE, PLAYER_ID))
    lookahead = lookahead_plies(config)
    served: list[dict[str, Any]] = []
    skipped: list[int] = []
    batch_row = conn.execute(
        "SELECT COALESCE(max(batch_id), 0) + 1 AS b FROM player_puzzle_exposure WHERE player_id = %s AND scope = %s",
        (PLAYER_ID, DRILL_SCOPE),
    ).fetchone()
    assert batch_row is not None
    batch_id = int(batch_row["b"])
    for lid in wanted:
        puzzle_id = line_puzzles.ensure_line_puzzle(conn, lid)
        if puzzle_id is None:
            skipped.append(lid)
            continue
        shape = conn.execute(
            "SELECT jsonb_array_length(solution_line) AS n, (split_part(fen, ' ', 2) = color) AS opens FROM puzzles"
            " WHERE id = %s",
            (puzzle_id,),
        ).fetchone()
        assert shape is not None
        ply = visibility.last_player_ply(int(shape["n"]), opens_on_player=bool(shape["opens"]))
        if ply < 0:  # a line with no move of the player's to find: nothing to drill, nothing stamped
            skipped.append(lid)
            continue
        conn.execute(
            "UPDATE puzzles SET drilled_at = clock_timestamp(), updated_at = now()"
            " WHERE id = %s AND drilled_at IS NULL",
            (puzzle_id,),
        )
        row = visibility.visible_by_id(conn, puzzle_id, lookahead_plies=lookahead)
        if row is None:  # cannot happen for a line the mint just accepted; stated, not assumed
            skipped.append(lid)
            continue
        row["presentation_ply"] = ply
        seq = fen_sequence(str(row["fen"]), [str(m) for m in row["solution_line"]])
        row["presentation_fen"] = seq[ply] if ply < len(seq) else None
        row["play_batch_id"] = batch_id
        conn.execute(
            "INSERT INTO player_puzzle_exposure"
            " (player_id, scope, batch_id, puzzle_id, bucket, presentation_ply, served_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())",
            (PLAYER_ID, DRILL_SCOPE, batch_id, puzzle_id, bucket_of(row), ply),
        )
        served.append(row)
    attach(conn, served)
    return {"batch_id": batch_id, "puzzles": served, "skipped": skipped}
