"""Serving: visibility, the play queue, skipping, the ladder and grading.

Against the scratch database, because every rule here is a query as much as it is Python.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import chess
import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import (
    BUCKET_CC0_MATE_ENDGAME,
    BUCKET_MOTIFS_FIRST_CLASS,
    BUCKET_MOTIFS_REMAINING,
    BUCKET_OWN_MISSED_MATE,
    BUCKET_YOUR_PUZZLES,
    CC0_SERVE_THEMES,
    PLAYER_ID,
    ROTATION_BUCKET_THEMES,
)
from core.puzzles import attempts, serve, srs, visibility
from core.puzzles.lines import fen_sequence
from core.settings import Settings

START = chess.STARTING_FEN
# White to move wins a knight with a fork.
FORK_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1"
# White to move, mate in one: Ra8#.
MATE_FEN = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"


def _config(**overrides: Any) -> Settings:
    return Settings(**{"puzzle_mix_batch_size": 4, "srs_advance_threshold": 1, **overrides})


def _player(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me') ON CONFLICT DO NOTHING", (PLAYER_ID,))


def _game(
    conn: psycopg.Connection[DictRow], game_id: int, *, rating: int | None = 1500, minutes_ago: int | None = None
) -> None:
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, played_at, variant, time_class, moves, fen_sequence)"
        " VALUES (%s, 'lichess', %s, now() - (%s || ' minutes')::interval, 'standard', 'rapid', %s::jsonb, %s::jsonb)",
        (
            game_id,
            f"g{game_id}",
            minutes_ago if minutes_ago is not None else game_id,
            json.dumps(["e4"]),
            json.dumps([START]),
        ),
    )
    conn.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, analyzed_at_depth, player_rating)"
        " VALUES (%s, %s, 'white', 'lichess', 18, %s)",
        (PLAYER_ID, game_id, rating),
    )


def _blunder(conn: psycopg.Connection[DictRow], game_id: int, fen: str = FORK_FEN) -> None:
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, best_move, best_line, classification, themes)"
        " VALUES (%s, %s, 0, %s, 'Nxe5', 'Nxe5 Nxe5 d4', 'blunder', ARRAY['fork'])",
        (PLAYER_ID, game_id, fen),
    )


def _puzzle(
    conn: psycopg.Connection[DictRow],
    fen: str = FORK_FEN,
    line: list[str] | None = None,
    sources: list[str] | None = None,
    themes: list[str] | None = None,
    acceptance_map: dict[str, Any] | None = None,
) -> int:
    line = line if line is not None else ["Nxe5", "Nxe5", "d4"]
    row = conn.execute(
        "INSERT INTO puzzles (fen, solution_line, solution_fen_sequence, source_types, color, themes, player_id, acceptance_map)"
        " VALUES (%s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::jsonb) RETURNING id",
        (
            fen,
            json.dumps(line),
            json.dumps(fen_sequence(fen, line)),
            sources if sources is not None else ["blunder"],
            fen.split()[1],
            themes if themes is not None else ["fork"],
            PLAYER_ID,
            json.dumps(acceptance_map) if acceptance_map is not None else None,
        ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _fens(moves: list[str], start: str = START) -> list[str]:
    return fen_sequence(start, moves)


def _line(conn: psycopg.Connection[DictRow], moves: list[str], *, line_id: int = 1, color: str = "white") -> None:
    conn.execute(
        "INSERT INTO books (id, title, color, player_id) VALUES (1, 'Book', %s, %s) ON CONFLICT (id) DO NOTHING",
        (color, PLAYER_ID),
    )
    conn.execute("INSERT INTO chapters (id, book_id, title) VALUES (1, 1, 'Ch') ON CONFLICT (id) DO NOTHING")
    conn.execute(
        "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves, fen_sequence) VALUES (%s, 1, 'main', %s::jsonb, %s::jsonb)",
        (line_id, json.dumps(moves), json.dumps(_fens(moves))),
    )


def _rep_puzzle(conn: psycopg.Connection[DictRow], moves: list[str], line_id: int = 1) -> int:
    row = conn.execute(
        "INSERT INTO puzzles (fen, solution_line, solution_fen_sequence, source_types, color, is_repertoire,"
        " repertoire_line_id, player_id, title) VALUES (%s, %s::jsonb, %s::jsonb, ARRAY['deviation'], 'w', TRUE, %s, %s, 'line')"
        " RETURNING id",
        (START, json.dumps(moves), json.dumps(_fens(moves)), line_id, PLAYER_ID),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _deviation(
    conn: psycopg.Connection[DictRow], game_id: int, ply: int, line_id: int = 1, fen: str | None = None
) -> None:
    _game(conn, game_id)
    result = conn.execute(
        "INSERT INTO game_repertoire_results (player_id, chess_game_id, book_id, chapter_id, deviated_at_ply, deviation_by,"
        " deviation_fen) VALUES (%s, %s, 1, 1, %s, 'me', %s) RETURNING id",
        (PLAYER_ID, game_id, ply, fen),
    ).fetchone()
    assert result is not None
    conn.execute(
        "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply) VALUES (%s, %s, 1)",
        (result["id"], line_id),
    )


def _position(i: int) -> tuple[str, list[str]]:
    """The i-th of a family of distinct legal positions (white king, white queen, black king)
    with a one-move solution, for tests that need many boards."""
    board = chess.Board(None)
    board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(chess.B2, chess.Piece(chess.QUEEN, chess.WHITE))
    squares = [sq for sq in chess.SQUARES if chess.square_rank(sq) >= 3]
    placed = 0
    for sq in squares:
        board.remove_piece_at(sq)
        board.set_piece_at(sq, chess.Piece(chess.KING, chess.BLACK))
        board.turn = chess.WHITE
        if board.is_valid():
            if placed == i:
                move = next(m for m in board.legal_moves if m.from_square == chess.B2)
                return board.fen(), [board.san(move)]
            placed += 1
        board.remove_piece_at(sq)
    raise AssertionError("not enough positions")


def _corpus(
    conn: psycopg.Connection[DictRow],
    n: int,
    themes: list[str],
    *,
    rating: int = 1500,
    prefix: str = "c",
    start: int = 0,
) -> None:
    """`n` distinct corpus rows, most popular first; `start` keeps families of rows apart."""
    for i in range(n):
        fen, line = _position(start + i)
        conn.execute(
            "INSERT INTO lichess_puzzles (puzzle_id, fen, solution_line, color, rating, rating_bucket, popularity, nb_plays, themes)"
            " VALUES (%s, %s, %s::jsonb, 'w', %s, %s, %s, %s, %s)",
            (f"{prefix}{i}", fen, json.dumps(line), rating, rating // 100 * 100, 100 - i, 1000 - i, themes),
        )


def _ids(rows: list[dict[str, Any]]) -> list[int]:
    return [int(r["id"]) for r in rows]


def _count(conn: psycopg.Connection[DictRow], table: str, where: str = "TRUE") -> int:
    from typing import LiteralString, cast

    query = cast(LiteralString, f"SELECT count(*) AS n FROM {table} WHERE {where}")  # test literals
    row = conn.execute(query).fetchone()
    assert row is not None
    return int(row["n"])


# --- visibility -------------------------------------------------------------------------


def test_a_standard_puzzle_is_visible_with_its_occurrence_counts(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1)
    _blunder(clean, 2)
    pid = _puzzle(clean)
    rows = visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2)
    assert _ids(rows) == [pid]
    assert rows[0]["occurrence_count"] == 2 and rows[0]["source_breakdown"] == {"blunder": 2}
    assert rows[0]["is_repertoire"] is False and rows[0]["presentation_ply"] is None


def test_the_window_scopes_the_counts_but_not_the_visibility(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1)
    _blunder(clean, 2)
    pid = _puzzle(clean)
    rows = visibility.visible_rows(clean, last_n_games=1, lookahead_plies=2)
    assert _ids(rows) == [pid] and rows[0]["occurrence_count"] == 1


def test_a_dismissed_position_is_hidden_and_not_attemptable(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    clean.execute("INSERT INTO dismissed_blunder_fens (player_id, fen) VALUES (%s, %s)", (PLAYER_ID, FORK_FEN))
    assert visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2) == []
    assert visibility.attemptable(clean, pid) is None
    assert visibility.visible_by_id(clean, pid, lookahead_plies=2) is None


def test_a_missed_mate_puzzle_without_its_map_is_hidden(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean, MATE_FEN, ["Ra8#"], sources=["own_mate"], themes=[])
    assert visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2) == []
    assert visibility.attemptable(clean, pid) is None


def test_a_puzzle_the_repertoire_contradicts_is_conflicted(clean: psycopg.Connection[DictRow]) -> None:
    """The puzzle's line reaches a position where the white book prescribes a different
    move; teaching both would teach a contradiction."""
    _player(clean)
    _line(clean, ["e4", "e5", "Nf3"])
    pid = _puzzle(clean, START, ["e4", "e5", "Bc4"], sources=["custom"], themes=[])
    assert visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2) == []
    assert visibility.attemptable(clean, pid) is None
    # The same line, agreeing with the book, is fine.
    clean.execute(
        "UPDATE puzzles SET solution_line = %s::jsonb, solution_fen_sequence = %s::jsonb WHERE id = %s",
        (json.dumps(["e4", "e5", "Nf3"]), json.dumps(_fens(["e4", "e5", "Nf3"])), pid),
    )
    assert _ids(visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2)) == [pid]
    assert visibility.attemptable(clean, pid) is not None


def test_a_blunder_puzzle_the_repertoire_already_teaches_is_redundant(clean: psycopg.Connection[DictRow]) -> None:
    """A blunder puzzle starting at a position the white book prescribes a move for is
    hidden — even when the book's move is the same one. A custom puzzle there is not."""
    _player(clean)
    _line(clean, ["e4", "e5", "Nf3"])
    after_e4_e5 = _fens(["e4", "e5"])[2]
    hidden = _puzzle(clean, after_e4_e5, ["Nf3"], sources=["blunder"])
    assert visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2) == []
    assert visibility.attemptable(clean, hidden) is None
    clean.execute("UPDATE puzzles SET active = FALSE WHERE id = %s", (hidden,))  # one active puzzle per board
    shown = _puzzle(clean, after_e4_e5, ["Nf3"], sources=["custom"])
    assert _ids(visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2)) == [shown]
    assert visibility.attemptable(clean, shown) is not None


def test_a_repertoire_puzzle_needs_three_deviations_and_is_presented_past_the_furthest(
    clean: psycopg.Connection[DictRow],
) -> None:
    _player(clean)
    moves = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"]
    _line(clean, moves)
    pid = _rep_puzzle(clean, moves)
    _deviation(clean, 1, 2)
    _deviation(clean, 2, 2)
    assert visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2) == []
    _deviation(clean, 3, 4)
    rows = visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2)
    assert _ids(rows) == [pid]
    # Furthest deviation at ply 4 plus a two-ply lookahead is ply 6: Ba4, the player's move.
    assert rows[0]["presentation_ply"] == 6 and rows[0]["presentation_fen"] == _fens(moves)[6]
    assert visibility.presentation_ply(clean, pid, lookahead_plies=2) == 6
    # A lookahead past the end snaps to the player's parity: ply 6 is the last of the line.
    assert visibility.presentation_ply(clean, pid, lookahead_plies=4) == 6
    assert rows[0]["occurrence_count"] == 3 and rows[0]["source_breakdown"] == {"deviation": 3}


def test_a_repertoire_puzzle_under_an_inactive_chapter_is_not_served(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    moves = ["e4", "e5", "Nf3"]
    _line(clean, moves)
    pid = _rep_puzzle(clean, moves)
    for g in (1, 2, 3):
        _deviation(clean, g, 2)
    assert _ids(visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2)) == [pid]
    assert visibility.attemptable(clean, pid) is not None
    clean.execute("UPDATE chapters SET active = FALSE WHERE id = 1")
    assert visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2) == []
    assert visibility.attemptable(clean, pid) is None
    assert visibility.visible_by_id(clean, pid, lookahead_plies=2) is None


def test_one_repertoire_puzzle_per_presented_position(clean: psycopg.Connection[DictRow]) -> None:
    """Two lines that share a prefix collapse to the one the player has deviated from most."""
    _player(clean)
    a = ["e4", "e5", "Nf3", "Nc6", "Bb5"]
    b = ["e4", "e5", "Nf3", "Nc6", "Bc4"]
    _line(clean, a, line_id=1)
    _line(clean, b, line_id=2)
    pa = _rep_puzzle(clean, a, 1)
    pb = _rep_puzzle(clean, b, 2)
    for g in (1, 2, 3):
        _deviation(clean, g, 2, line_id=1)
    for g in (4, 5, 6, 7):
        _deviation(clean, g, 2, line_id=2)
    rows = visibility.visible_rows(clean, last_n_games=0, lookahead_plies=2)
    assert _ids(rows) == [pb] and pa not in _ids(rows)
    assert visibility.visible_by_id(clean, pb, lookahead_plies=2) is not None


def test_the_playable_payload_is_the_solver_s_fields_only(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    row = visibility.visible_by_id(clean, pid, lookahead_plies=2)
    assert row is not None
    assert set(visibility.playable(row)) == set(visibility.PLAYABLE_FIELDS)


# --- the ladder ---------------------------------------------------------------------------


def _attempt_row(conn: psycopg.Connection[DictRow], pid: int, solved: bool, session: str | None) -> int:
    row = conn.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_id, session_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (pid, PLAYER_ID, solved, str(uuid.uuid4()), session),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _state(conn: psycopg.Connection[DictRow], pid: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM player_puzzle_state WHERE puzzle_id = %s", (pid,)).fetchone()
    assert row is not None
    return dict(row)


def test_a_solve_advances_and_a_never_attempted_puzzle_is_due(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    config = _config(srs_advance_threshold=2)
    assert srs.is_due(None, datetime.now(UTC))
    t = srs.apply_attempt(
        clean, pid, True, attempt_row_id=_attempt_row(clean, pid, True, None), session_id=None, config=config
    )
    assert t["outcome"] == "advanced" and t["new_level"] == "pawn" and t["new_correct_at_level"] == 1
    state = _state(clean, pid)
    assert state["level"] == "pawn" and not srs.is_due(srs.states(clean, [pid])[pid], datetime.now(UTC))
    assert state["next_show_at"] - datetime.now(UTC) > timedelta(hours=23)


def test_progress_is_credited_once_a_day(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    config = _config(srs_advance_threshold=3)
    outcomes = [
        srs.apply_attempt(
            clean, pid, True, attempt_row_id=_attempt_row(clean, pid, True, None), session_id=None, config=config
        )["outcome"]
        for _ in range(3)
    ]
    assert outcomes == ["advanced", "unchanged", "unchanged"] and _state(clean, pid)["correct_at_level"] == 1
    assert _count(clean, "puzzle_attempts") == 3


def test_reaching_the_threshold_promotes_and_king_is_mastery(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    config = _config(srs_advance_threshold=1)
    levels: list[str] = []
    for _ in range(6):
        clean.execute(
            "UPDATE player_puzzle_state SET last_correct_date = last_correct_date - 1 WHERE puzzle_id = %s", (pid,)
        )
        t = srs.apply_attempt(
            clean, pid, True, attempt_row_id=_attempt_row(clean, pid, True, None), session_id=None, config=config
        )
        levels.append(t["new_level"])
    assert levels == ["knight", "bishop", "rook", "queen", "king", "king"]
    state = _state(clean, pid)
    assert state["next_show_at"] == srs.KING_SENTINEL
    assert not srs.is_due(srs.states(clean, [pid])[pid], datetime.now(UTC))
    assert srs.mastered_count(clean) == 1


def test_two_wrongs_in_the_window_demote_and_pawn_floors(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    config = _config(srs_drop_window=3, srs_drop_wrongs=2)
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level) VALUES (%s, %s, 'bishop')", (PLAYER_ID, pid)
    )
    t1 = srs.apply_attempt(
        clean, pid, False, attempt_row_id=_attempt_row(clean, pid, False, None), session_id=None, config=config
    )
    assert t1["outcome"] == "unchanged" and t1["new_level"] == "bishop"
    assert _state(clean, pid)["next_show_at"] - datetime.now(UTC) < timedelta(hours=2)
    t2 = srs.apply_attempt(
        clean, pid, False, attempt_row_id=_attempt_row(clean, pid, False, None), session_id=None, config=config
    )
    assert t2["outcome"] == "demoted" and t2["new_level"] == "knight" and _state(clean, pid)["last_3_attempts"] == []
    levels = [
        srs.apply_attempt(
            clean, pid, False, attempt_row_id=_attempt_row(clean, pid, False, None), session_id=None, config=config
        )["new_level"]
        for _ in range(4)
    ]
    assert levels == ["knight", "pawn", "pawn", "pawn"]


def test_only_the_first_attempt_of_a_session_scores(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    config = _config(srs_advance_threshold=1, srs_drop_wrongs=1)
    session = str(uuid.uuid4())
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level) VALUES (%s, %s, 'rook')", (PLAYER_ID, pid)
    )
    first = srs.apply_attempt(
        clean, pid, False, attempt_row_id=_attempt_row(clean, pid, False, session), session_id=session, config=config
    )
    assert first["outcome"] == "demoted"
    retry = srs.apply_attempt(
        clean, pid, True, attempt_row_id=_attempt_row(clean, pid, True, session), session_id=session, config=config
    )
    assert retry["outcome"] == "unchanged" and _state(clean, pid)["level"] == "bishop"


def test_the_attempt_summary_counts_play_throughs_and_signs_the_streak(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    s1, s2, s3 = (str(uuid.uuid4()) for _ in range(3))
    _attempt_row(clean, pid, True, s1)
    _attempt_row(clean, pid, False, s2)
    _attempt_row(clean, pid, True, s2)  # a right answer after a wrong guess is not a solve
    _attempt_row(clean, pid, False, s3)
    summary = srs.attempt_summaries(clean, [pid])[pid]
    assert (summary["total"], summary["solved"], summary["streak"]) == (3, 1, -2)


def test_a_mastered_puzzle_un_retires_when_its_pattern_recurs(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level, next_show_at, updated_at)"
        " VALUES (%s, %s, 'king', %s, now() - interval '1 day')",
        (PLAYER_ID, pid, srs.KING_SENTINEL),
    )
    config = _config(srs_king_demotion_min_hits=2)
    _game(clean, 1, minutes_ago=60)  # before mastery: does not count
    _blunder(clean, 1)
    clean.execute("UPDATE chess_games SET played_at = now() - interval '2 days' WHERE id = 1")
    _game(clean, 2, minutes_ago=30)
    _blunder(clean, 2)
    assert srs.demote_kings(clean, config) == {"candidates": 0, "demoted": 0}
    _game(clean, 3, minutes_ago=10)
    _blunder(clean, 3)
    assert srs.demote_kings(clean, config) == {"candidates": 1, "demoted": 1}
    assert _state(clean, pid)["level"] == "pawn"
    assert srs.demote_kings(clean, config) == {"candidates": 0, "demoted": 0}


# --- grading ---------------------------------------------------------------------------


def test_moves_are_compared_as_moves_not_strings() -> None:
    line = ["Nxe5", "Nxe5", "d4"]
    assert attempts.validate_line(FORK_FEN, line, "w", None, "Nxe5,d4")
    assert attempts.validate_line(FORK_FEN, line, "w", None, "Nf3xe5!,d4")
    assert not attempts.validate_line(FORK_FEN, line, "w", None, "Nxe5")
    assert not attempts.validate_line(FORK_FEN, line, "w", None, "Nxe5,d4,Nc3")
    assert not attempts.validate_line(FORK_FEN, line, "w", None, "Bxf7+,d4")
    assert not attempts.validate_line(FORK_FEN, line, "w", None, "")


def test_any_mate_is_accepted_on_the_final_ply() -> None:
    # Two rooks: 1.Ra7 Kg8 2.Rb8# is the line, but 2.Rbb7 is not mate and 2.Rb8# is; a
    # different mating move on the last ply is accepted too.
    fen = "7k/8/8/8/8/8/R7/1R5K w - - 0 1"
    line = ["Ra7", "Kg8", "Rb8#"]
    assert attempts.validate_line(fen, line, "w", None, "Ra7,Rb8")
    assert not attempts.validate_line(fen, line, "w", None, "Ra7,Rb7")
    # And on a non-final ply a different move is still wrong.
    assert not attempts.validate_line(fen, line, "w", None, "Rb7,Ra8")


def test_a_repertoire_line_is_graded_up_to_the_presented_ply() -> None:
    moves = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"]
    assert attempts.validate_line(START, moves, "w", 4, "e4,Nf3,Bb5")
    assert not attempts.validate_line(START, moves, "w", 4, "e4,Nf3,Bb5,Ba4")
    assert attempts.validate_line(START, moves, "w", None, "e4,Nf3,Bb5,Ba4")


def test_a_missed_mate_is_graded_by_its_map_and_fails_closed_without_one() -> None:
    from core.chess.mate_acceptance import build_acceptance_map

    built = build_acceptance_map(MATE_FEN, 1)
    assert built is not None
    ok = attempts.resolve_solved(
        fen=MATE_FEN,
        solution_line=["Ra8#"],
        color="w",
        presentation_ply=None,
        claimed=True,
        moves_played="Ra8#",
        acceptance_map=built,
        is_own_mate=True,
    )
    assert ok
    assert not attempts.resolve_solved(
        fen=MATE_FEN,
        solution_line=["Ra8#"],
        color="w",
        presentation_ply=None,
        claimed=True,
        moves_played="Ra8#",
        acceptance_map=None,
        is_own_mate=True,
    )
    assert not attempts.resolve_solved(
        fen=MATE_FEN,
        solution_line=["Ra8#"],
        color="w",
        presentation_ply=None,
        claimed=False,
        moves_played="Ra8#",
        acceptance_map=built,
        is_own_mate=True,
    )


def test_recording_grades_scores_and_replays_idempotently(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    config = _config(srs_advance_threshold=1)
    attempt_id, session = str(uuid.uuid4()), str(uuid.uuid4())
    first = attempts.record(
        clean, pid, config, claimed=True, moves_played="Nxe5,d4", attempt_id=attempt_id, session_id=session
    )
    assert first["detail"] == "attempt recorded" and first["solved"] is True
    assert first["srs"]["level"] == "knight" and first["srs"]["transition"]["outcome"] == "promoted"
    assert first["attempt_summary"] == {"total": 1, "solved": 1, "streak": 1}
    replay = attempts.record(
        clean, pid, config, claimed=False, moves_played=None, attempt_id=attempt_id, session_id=session
    )
    assert replay["detail"] == "attempt recorded (idempotent)" and replay["solved"] is True
    assert replay["srs"]["transition"] is None and replay["srs"]["level"] == "knight"
    assert _count(clean, "puzzle_attempts") == 1


def test_a_false_solve_claim_is_downgraded_and_recorded_as_wrong(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    out = attempts.record(
        clean, pid, _config(), claimed=True, moves_played="Bxf7+,d4", attempt_id=str(uuid.uuid4()), session_id=None
    )
    assert out["solved"] is False and out["attempt_summary"]["streak"] == -1


def test_a_corpus_puzzle_is_not_on_the_ladder(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean, sources=["lichess_cc0"])
    out = attempts.record(
        clean, pid, _config(), claimed=True, moves_played="Nxe5,d4", attempt_id=str(uuid.uuid4()), session_id=None
    )
    assert out["solved"] is True and out["srs"]["transition"] is None
    assert _count(clean, "player_puzzle_state") == 0


def test_an_invisible_puzzle_cannot_be_attempted(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    clean.execute("UPDATE puzzles SET active = FALSE WHERE id = %s", (pid,))
    with pytest.raises(attempts.NotAttemptable):
        attempts.record(clean, pid, _config(), claimed=True, moves_played="Nxe5,d4", attempt_id=None, session_id=None)
    with pytest.raises(attempts.NotAttemptable):
        attempts.record(
            clean, 999999, _config(), claimed=True, moves_played="Nxe5,d4", attempt_id=None, session_id=None
        )


# --- the queue -----------------------------------------------------------------------------


def test_bucket_routing_and_theme_classes_cover_the_served_vocabulary() -> None:
    assert serve.rotation_bucket_for_themes(["fork", "mateIn2"]) == BUCKET_MOTIFS_FIRST_CLASS
    assert serve.rotation_bucket_for_themes(["mateIn2", "sacrifice"]) == BUCKET_CC0_MATE_ENDGAME
    assert serve.rotation_bucket_for_themes(["sacrifice"]) == BUCKET_MOTIFS_REMAINING
    assert serve.rotation_bucket_for_themes(["crushing"]) is None
    assert set().union(*ROTATION_BUCKET_THEMES.values()) == set(CC0_SERVE_THEMES)
    assert serve.bucket_of({"source_types": ["own_mate"]}) == BUCKET_OWN_MISSED_MATE
    assert serve.bucket_of({"source_types": ["blunder"]}) == BUCKET_YOUR_PUZZLES
    assert serve.bucket_of({"source_types": ["lichess_cc0"], "themes": ["pin"]}) == BUCKET_MOTIFS_FIRST_CLASS


def test_largest_remainder_and_window_placement() -> None:
    assert serve.largest_remainder({"a": 25, "b": 35, "c": 40}, 12) == {"a": 3, "b": 4, "c": 5}
    assert serve.largest_remainder({"a": 1, "b": 1, "c": 1}, 4) == {"a": 2, "b": 1, "c": 1}
    assert serve.largest_remainder({"a": 0, "b": 5}, 3) == {"a": 0, "b": 3}
    assert serve.place_window(1500, -150, 150, 1050, 2700) == (1350, 1650)
    assert serve.place_window(900, -150, 150, 1050, 2700) == (1050, 1050), "a one-point band is a band"
    assert serve.place_window(800, -150, 150, 1050, 2700) == (1050, 1350)
    assert serve.place_window(3000, -150, 150, 1050, 2700) == (2400, 2700)
    assert serve.place_window(1100, -150, 150, 1050, 2700) == (1050, 1250)


def test_the_mint_ahead_threshold_stays_below_the_batch_size() -> None:
    assert serve.mint_ahead_threshold(_config(puzzle_mix_batch_size=12)) == 4
    assert serve.mint_ahead_threshold(_config(puzzle_mix_batch_size=3)) == 2
    assert serve.mint_ahead_threshold(_config(puzzle_mix_batch_size=1)) == 1


def test_a_batch_mixes_the_buckets_and_materialises_corpus_puzzles(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    own = _puzzle(clean)
    own_too = _puzzle(clean, MATE_FEN, ["Ra8#"], sources=["custom"], themes=[])
    _corpus(clean, 6, ["fork"], prefix="f")
    _corpus(clean, 6, ["sacrifice"], prefix="s", start=6)
    _corpus(clean, 6, ["mateIn2"], prefix="m", start=12)
    config = _config(puzzle_mix_batch_size=8)
    served = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert served.batch_id == 1 and served.scope == "all" and served.mint_ahead == 4
    assert len(served.rows) == 8 and {own, own_too} <= set(_ids(served.rows))
    buckets = clean.execute("SELECT bucket, count(*) AS n FROM player_puzzle_exposure GROUP BY bucket").fetchall()
    counts = {r["bucket"]: r["n"] for r in buckets}
    # 25/20/35/10 over the four suppliable buckets, largest remainder, into eight slots.
    assert counts == {
        BUCKET_YOUR_PUZZLES: 2,
        BUCKET_MOTIFS_FIRST_CLASS: 2,
        BUCKET_MOTIFS_REMAINING: 3,
        BUCKET_CC0_MATE_ENDGAME: 1,
    }
    assert _count(clean, "puzzles", "source_types @> ARRAY['lichess_cc0']") == 6
    # The queue is stable until something is acknowledged.
    again = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert _ids(again.rows) == _ids(served.rows) and again.batch_id == 1


def test_acknowledged_items_leave_the_queue_and_a_low_queue_mints_ahead(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _corpus(clean, 20, ["fork"])
    config = _config(puzzle_mix_batch_size=4)
    first = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert len(first.rows) == 4 and first.mint_ahead == 3
    ids = _ids(first.rows)
    # A skip in the batch acknowledges; an attempt from before the serve does not.
    clean.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at) VALUES (%s, %s, TRUE, now() - interval '1 day')",
        (ids[1], PLAYER_ID),
    )
    assert serve.skip(clean, "all", 1, ids[0], config) == "DEFERRED"
    assert serve.skip(clean, "all", 1, ids[0], config) == "ALREADY_CONSUMED"
    second = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert ids[0] not in _ids(second.rows) and ids[1] in _ids(second.rows)
    # Three pending is at the threshold: a second batch is minted behind them, oldest first.
    assert len(second.rows) == 7 and [r["play_batch_id"] for r in second.rows] == [1, 1, 1, 2, 2, 2, 2]
    assert second.batch_id == 2
    # Two batches pending and only three items left: still no third batch.
    for pid in ids[2:] + _ids(second.rows)[3:5]:
        clean.execute(
            "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at) VALUES (%s, %s, TRUE, clock_timestamp())",
            (pid, PLAYER_ID),
        )
    third = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert [r["play_batch_id"] for r in third.rows] == [1, 2, 2] and third.batch_id == 2
    # Once batch 1 is gone entirely the queue may run ahead again.
    clean.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at) VALUES (%s, %s, TRUE, clock_timestamp())",
        (ids[1], PLAYER_ID),
    )
    fourth = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert [r["play_batch_id"] for r in fourth.rows] == [2, 2, 3, 3, 3, 3]


def test_a_skip_of_something_not_pending_is_a_state_miss(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _corpus(clean, 4, ["fork"])
    config = _config()
    first = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    pid = _ids(first.rows)[0]
    assert serve.skip(clean, "all", 99, pid, config) == "STATE_MISS"
    assert serve.skip(clean, "motif", 1, pid, config) == "STATE_MISS"
    clean.execute("UPDATE puzzles SET active = FALSE WHERE id = %s", (pid,))
    assert serve.skip(clean, "all", 1, pid, config) == "STATE_MISS"
    assert _count(clean, "player_puzzle_skip") == 0


def test_a_scope_has_its_own_batches_and_draws_only_its_content(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    own = _puzzle(clean)
    _corpus(clean, 6, ["fork"], prefix="f")
    _corpus(clean, 6, ["pin"], prefix="p", start=6)
    config = _config(puzzle_mix_batch_size=4)
    pins = serve.play_batch(clean, config, last_n_games=0, ptype="motif", subtype="pin")
    assert pins.scope == "motif:pin" and len(pins.rows) == 4
    assert all("pin" in r["themes"] for r in pins.rows) and own not in _ids(pins.rows)
    blunders = serve.play_batch(clean, config, last_n_games=0, ptype="blunder", subtype=None)
    assert _ids(blunders.rows) == [own]
    # The 'all' scope is untouched by the others, and a puzzle pending elsewhere is not re-minted.
    everything = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert everything.batch_id == 1 and not (set(_ids(everything.rows)) & set(_ids(pins.rows)))


def test_a_missed_share_is_never_repaid(clean: psycopg.Connection[DictRow]) -> None:
    """With no corpus, only the player's own puzzles supply; when the corpus appears the
    next batch is the configured mix, not the mix plus the corpus share it missed."""
    _player(clean)
    for i in range(6):
        fen, line = _position(i)
        _puzzle(clean, fen, line, themes=[])
    config = _config(puzzle_mix_batch_size=4)
    first = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert len(first.rows) == 4
    for r in first.rows:
        clean.execute(
            "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at) VALUES (%s, %s, TRUE, clock_timestamp())",
            (r["id"], PLAYER_ID),
        )
    _corpus(clean, 10, ["fork"])
    second = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    counts = clean.execute(
        "SELECT bucket, count(*) AS n FROM player_puzzle_exposure WHERE batch_id = 2 GROUP BY bucket"
    ).fetchall()
    assert {r["bucket"]: r["n"] for r in counts} == {BUCKET_YOUR_PUZZLES: 2, BUCKET_MOTIFS_FIRST_CLASS: 2}
    assert second.batch_id == 2


def test_an_unacknowledged_corpus_puzzle_is_fresh_and_served_from_its_row(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _corpus(clean, 1, ["fork"])
    config = _config(puzzle_mix_batch_size=2)
    first = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    pid = _ids(first.rows)[0]
    # Retire the batch without acknowledging the puzzle: it comes back on the same row.
    clean.execute("DELETE FROM player_puzzle_exposure")
    second = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert _ids(second.rows) == [pid]
    assert _count(clean, "puzzles") == 1
    # Once attempted it is seen, and with the corpus exhausted it rotates back by recency.
    clean.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at) VALUES (%s, %s, TRUE, clock_timestamp())",
        (pid, PLAYER_ID),
    )
    third = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert _ids(third.rows) == [pid]


def test_the_rating_window_follows_the_latest_game_and_the_loaded_corpus(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    assert serve.rating_window(clean, _config()) == (None, None)
    _corpus(clean, 3, ["fork"], rating=1400, prefix="a")
    _corpus(clean, 3, ["fork"], rating=2000, prefix="b", start=3)
    assert serve.rating_window(clean, _config()) == (None, None)
    _game(clean, 1, rating=1500)
    config = _config(cc0_difficulty_tier="normal", lichess_rating_offsets={"rapid": -250, "default": -325})
    # A Lichess rating is already on the corpus scale: 1500 ±150.
    assert serve.rating_window(clean, config) == (1400, 1650)
    # A Chess.com rating sits below it by the offset for its time class: 1500 rapid is 1750.
    clean.execute("UPDATE player_games SET source = 'chesscom' WHERE chess_game_id = 1")
    assert serve.rating_window(clean, config) == (1600, 1900)
    only_hard = serve.draw_candidate(clean, ["fork"], 1600, 2000, set(), 40)
    assert only_hard is not None and only_hard["puzzle_id"].startswith("b")


def test_the_deterministic_draw_is_the_most_popular_unseen(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _corpus(clean, 3, ["fork"])
    top = serve.draw_candidate(clean, ["fork"], None, None, set(), 1)
    assert top is not None and top["puzzle_id"] == "c0"
    pid = _puzzle(clean, top["fen"], list(top["solution_line"]), sources=["lichess_cc0"], themes=["fork"])
    clean.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, attempt_at) VALUES (%s, %s, TRUE, clock_timestamp())",
        (pid, PLAYER_ID),
    )
    nxt = serve.draw_candidate(clean, ["fork"], None, None, set(), 1)
    assert nxt is not None and nxt["puzzle_id"] == "c1"
    third = serve.draw_candidate(clean, ["fork"], None, None, {nxt["fen"]}, 1)
    assert third is not None and third["puzzle_id"] == "c2"
    assert serve.draw_candidate(clean, ["pin"], None, None, set(), 1) is None


def test_the_browse_list_and_the_due_count_agree_with_the_ladder(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    a = _puzzle(clean)
    b = _puzzle(clean, MATE_FEN, ["Ra8#"], sources=["custom"], themes=[])
    config = _config()
    assert serve.count_eligible(clean, config) == 2
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level, next_show_at) VALUES (%s, %s, 'knight', now() + interval '1 day')",
        (PLAYER_ID, a),
    )
    assert serve.count_eligible(clean, config) == 1
    rows = serve.browse(clean, config, last_n_games=0)
    assert _ids(rows) == [b, a], "ready-now sorts before scheduled"
    clean.execute(
        "UPDATE player_puzzle_state SET level = 'king', next_show_at = %s WHERE puzzle_id = %s", (srs.KING_SENTINEL, a)
    )
    assert serve.count_eligible(clean, config) == 1
    assert _ids(serve.retired(clean)) == [a]
    moves = ["e4", "e5", "Nf3"]
    _line(clean, moves)
    rep = _rep_puzzle(clean, moves)
    for g in (1, 2, 3):
        _deviation(clean, g, 2)
    assert serve.count_eligible(clean, config) == 2
    assert rep in _ids(serve.browse(clean, config, last_n_games=0))


def test_a_line_that_opens_with_the_opponent_s_move_is_graded_on_the_player_s_plies() -> None:
    """A black repertoire puzzle starts with White to move; the player's plies are the odd
    ones, and the mate relaxation applies to the last of those."""
    from core.puzzles.lines import is_mate_line

    fen = "6k1/8/8/8/8/8/r4PPP/6K1 w - - 0 1"
    line = ["Kh1", "Ra1#"]
    assert is_mate_line(fen, line, "b") == (True, 1)
    assert attempts.validate_line(fen, line, "b", None, "Ra1")
    assert not attempts.validate_line(fen, line, "b", None, "Kh1,Ra1")
