"""Grading an attempt and recording it.

`solved` is always re-derived on the server from the moves the player made; a false claim
is downgraded and recorded as a wrong attempt. Moves are compared as parsed
`chess.Move`s, never as SAN strings, after normalisation on both sides: an
over-disambiguated `Nd4e2` is the same move as `Nde2`, and promotion falls out of move
equality. On the final player ply of a line that is a forced mate, any move that mates is
accepted. A missed-mate puzzle is graded by walking its acceptance map instead: exactly
the optimal number of moves, each in the accepted set at its node, the opponent replying
with the map's canonical defence.

Recording is one transaction in a fixed order: lock the puzzle, insert the attempt row,
score it, read the state back, and let the caller commit. The lock comes before the
insert because a concurrent request in the same session would otherwise not see this
row and both would score. The same response is built on a fresh insert, an idempotent
replay of an `attempt_id` already recorded, and a lost insert race, so the wire shape
cannot differ between them; a replay returns the original verdict with the current state.
"""

from __future__ import annotations

import json
from typing import Any, cast

import chess
from psycopg import Connection

from core.chess.mate_acceptance import walk_acceptance_map
from core.chess.san import normalize_san
from core.chess.san import parse as parse_san
from core.constants import PLAYER_ID
from core.puzzles import srs, visibility
from core.puzzles.lines import is_mate_line
from core.puzzles.serve import CC0_SOURCE, OWN_MATE_SOURCE, lookahead_plies
from core.settings import Settings


def submitted_moves(moves_played: str | None) -> list[str]:
    return [m.strip() for m in (moves_played or "").split(",") if m.strip()]


def validate_line(
    fen: str, solution_line: list[str], color: str, presentation_ply: int | None, moves_played: str | None
) -> bool:
    """Do the submitted player moves reproduce the solution (up to the presented ply)?"""
    if not solution_line:
        return False
    line = (
        solution_line[: presentation_ply + 1]
        if presentation_ply is not None and presentation_ply >= 0
        else list(solution_line)
    )
    submitted = submitted_moves(moves_played)
    if not submitted:
        return False
    try:
        board = chess.Board(fen)
    except (ValueError, AssertionError):
        return False
    mate, last_player_ply = is_mate_line(fen, line, color)
    index = 0
    expected = 0
    for i, san in enumerate(line):
        expected_move = parse_san(board, san)
        if expected_move is None:
            return False
        player_turn = (board.turn == chess.WHITE) == (color == "w")
        if player_turn:
            expected += 1
            if index >= len(submitted):
                return False
            given = parse_san(board, submitted[index])
            accepted = given is not None and given == expected_move
            if not accepted and mate and i == last_player_ply and given is not None:
                probe = board.copy(stack=False)
                probe.push(given)
                accepted = probe.is_checkmate()
            if not accepted:
                return False
            index += 1
        board.push(expected_move)
    return index == len(submitted) == expected


def resolve_solved(
    *,
    fen: str,
    solution_line: list[str],
    color: str,
    presentation_ply: int | None,
    claimed: bool,
    moves_played: str | None,
    acceptance_map: object,
    is_own_mate: bool,
) -> bool:
    """The server's verdict. A `solved=False` claim is taken at its word; a `True` claim is
    checked. A missed-mate puzzle fails closed without its map."""
    if not claimed:
        return False
    if is_own_mate:
        if not acceptance_map:
            return False
        if isinstance(acceptance_map, str):
            try:
                acceptance_map = json.loads(acceptance_map)
            except ValueError:
                return False
        return walk_acceptance_map(fen, acceptance_map, [normalize_san(m) for m in submitted_moves(moves_played)])
    return validate_line(fen, solution_line, color, presentation_ply, moves_played)


def _line(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [str(m) for m in cast(list[object], value)] if isinstance(value, list) else []


def _response(detail: str, solved: bool, state: dict[str, Any], transition: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "detail": detail,
        "solved": solved,
        "attempt_summary": {"total": state["total"], "solved": state["solved"], "streak": state["streak"]},
        "srs": {
            "level": state["level"],
            "correct_at_level": state["correct_at_level"],
            "advance_threshold": state["advance_threshold"],
            "transition": transition,
        },
    }


class NotAttemptable(LookupError):
    """The puzzle is missing or not visible to the player."""


def _existing(conn: Connection[Any], attempt_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT solved FROM puzzle_attempts WHERE player_id = %s AND attempt_id = %s", (PLAYER_ID, attempt_id)
        )
        return cur.fetchone()


def record(
    conn: Connection[Any],
    puzzle_id: int,
    config: Settings,
    *,
    claimed: bool,
    moves_played: str | None,
    attempt_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Grade, record and score one attempt; the caller commits. Raises NotAttemptable."""
    puzzle = visibility.attemptable(conn, puzzle_id)
    if puzzle is None:
        raise NotAttemptable(puzzle_id)
    if attempt_id is not None:
        existing = _existing(conn, attempt_id)
        if existing is not None:
            return _response(
                "attempt recorded (idempotent)",
                bool(existing["solved"]),
                srs.post_attempt_state(conn, puzzle_id, config),
                None,
            )

    sources = list(puzzle.get("source_types") or [])
    solved = resolve_solved(
        fen=str(puzzle["fen"]),
        solution_line=_line(puzzle["solution_line"]),
        color=str(puzzle["color"]),
        presentation_ply=visibility.presentation_ply(conn, puzzle_id, lookahead_plies=lookahead_plies(config)),
        claimed=claimed,
        moves_played=moves_played,
        acceptance_map=puzzle.get("acceptance_map"),
        is_own_mate=OWN_MATE_SOURCE in sources,
    )
    srs.lock_attempt(conn, puzzle_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, moves_played, attempt_id, session_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id, attempt_id) WHERE attempt_id IS NOT NULL DO NOTHING
            RETURNING id
            """,
            (puzzle_id, PLAYER_ID, solved, moves_played, attempt_id, session_id),
        )
        inserted = cur.fetchone()
    if inserted is None:
        # Lost the race to a request carrying the same attempt_id; report what it recorded.
        winner = _existing(conn, str(attempt_id))
        if winner is None:
            raise RuntimeError("attempt insert returned no row and no winner")
        return _response(
            "attempt recorded (idempotent)",
            bool(winner["solved"]),
            srs.post_attempt_state(conn, puzzle_id, config),
            None,
        )

    transition = None
    if CC0_SOURCE not in sources:
        transition = srs.apply_attempt(
            conn, puzzle_id, solved, attempt_row_id=int(inserted["id"]), session_id=session_id, config=config
        )
    return _response("attempt recorded", solved, srs.post_attempt_state(conn, puzzle_id, config), transition)


# --- game links for the list views ---------------------------------------------------------


def game_links(conn: Connection[Any], fens: list[str]) -> dict[str, list[dict[str, str]]]:
    """{fen: games where the player reached it and blundered or deviated}."""
    out: dict[str, list[dict[str, str]]] = {f: [] for f in fens}
    if not fens:
        return out
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT pf.fen, cg.url, cg.played_at, b.classification, pg.opponent_username
            FROM unnest(%(fens)s::text[]) AS pf(fen)
            JOIN blunders b ON b.canonical_fen = bq_canonical_fen(pf.fen) || ' 0 1' AND b.player_id = %(pid)s
            JOIN chess_games cg ON cg.id = b.chess_game_id
            JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
            ORDER BY pf.fen, cg.played_at DESC NULLS LAST
            """,
            {"fens": fens, "pid": PLAYER_ID},
        )
        for r in cur.fetchall():
            out[r["fen"]].append(_link(r, "blunder", r["classification"] or ""))
        cur.execute(
            """
            SELECT DISTINCT pf.fen, cg.url, cg.played_at, pg.opponent_username
            FROM unnest(%(fens)s::text[]) AS pf(fen)
            JOIN game_repertoire_results grr ON grr.canonical_fen = bq_canonical_fen(pf.fen) || ' 0 1'
                 AND grr.player_id = %(pid)s AND grr.deviation_by = 'me' AND grr.deviated_at_ply IS NOT NULL
            JOIN chess_games cg ON cg.id = grr.chess_game_id
            JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
            ORDER BY pf.fen, cg.played_at DESC NULLS LAST
            """,
            {"fens": fens, "pid": PLAYER_ID},
        )
        for r in cur.fetchall():
            out[r["fen"]].append(_link(r, "deviation", ""))
    return out


def _link(r: dict[str, Any], source: str, classification: str) -> dict[str, str]:
    return {
        "url": r["url"] or "",
        "date": r["played_at"].strftime("%Y-%m-%d") if r["played_at"] else "",
        "source_type": source,
        "classification": classification,
        "opponent": r["opponent_username"] or "",
    }


def correct_game_links(conn: Connection[Any], rows: list[dict[str, Any]]) -> dict[int, list[dict[str, str]]]:
    """{puzzle_id: up to ten games where the player reached the start position and played
    the first solution move}. Repertoire puzzles get none: what they present is a later
    position in the line, not the start."""
    out: dict[int, list[dict[str, str]]] = {int(r["id"]): [] for r in rows}
    targets: list[tuple[int, str, str]] = []
    for r in rows:
        line = _line(r.get("solution_line"))
        if r.get("is_repertoire") or not line:
            continue
        side = str(r["fen"]).split(" ")[1] if len(str(r["fen"]).split(" ")) > 1 else ""
        k = 0 if side == r["color"] else 1
        if k < len(line):
            targets.append((int(r["id"]), str(r["fen"]), line[k]))
    if not targets:
        return out
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.puzzle_id, t.fen AS target_fen, t.player_move, cg.url, cg.played_at, pg.opponent_username,
                   cg.moves, cg.fen_sequence
            FROM unnest(%(ids)s::int[], %(fens)s::text[], %(moves)s::text[]) AS t(puzzle_id, fen, player_move)
            JOIN player_games pg ON pg.player_id = %(pid)s
            JOIN chess_games cg ON cg.id = pg.chess_game_id AND cg.position_keys @> ARRAY[bq_position_key(t.fen)]
            ORDER BY t.puzzle_id, cg.played_at DESC NULLS LAST, cg.id DESC
            """,
            {
                "ids": [t[0] for t in targets],
                "fens": [t[1] for t in targets],
                "moves": [t[2] for t in targets],
                "pid": PLAYER_ID,
            },
        )
        for r in cur.fetchall():
            pid = int(r["puzzle_id"])
            if len(out[pid]) >= 10:
                continue
            fens = [str(f) for f in cast(list[object], r["fen_sequence"] or [])]
            moves = [str(m) for m in cast(list[object], r["moves"] or [])]
            target = " ".join(str(r["target_fen"]).split(" ")[:4])
            for ply, fen in enumerate(fens):
                if ply >= len(moves):
                    break
                if (
                    normalize_san(str(moves[ply])) == normalize_san(str(r["player_move"]))
                    and " ".join(str(fen).split(" ")[:4]) == target
                ):
                    out[pid].append(
                        {
                            "url": r["url"] or "",
                            "date": r["played_at"].strftime("%Y-%m-%d") if r["played_at"] else "",
                            "opponent": r["opponent_username"] or "",
                        }
                    )
                    break
    return out
