"""Repertoire matching: transposition-safe subsequence match, the commitment
anchor, who deviated, and the database step (idempotent rerun, 960 excluded,
unmatched games flagged)."""

from __future__ import annotations

import json

import psycopg
from psycopg.rows import DictRow

from core.chess.board import moves_to_fen_sequence
from core.constants import PLAYER_ID
from core.repertoire.matching import (
    Line,
    match_game,
    match_player,
    player_committed,
    subsequence_match_length,
    who_deviated,
)


def _line(line_id: int, color: str, moves: list[str], book_id: int = 1, chapter_id: int = 1) -> Line:
    return Line(line_id, book_id, chapter_id, color, moves, moves_to_fen_sequence(moves))


def test_commitment_anchor() -> None:
    assert not player_committed(0, "white") and player_committed(1, "white")
    assert not player_committed(1, "black") and player_committed(2, "black")


def test_subsequence_match_semantics() -> None:
    """Ported as-is: every line position must appear in the game, in order. A game that
    reaches the line's positions by another move order matches nothing (the scan for the
    line's first position exhausts the game), so in practice this is a prefix match."""
    line = moves_to_fen_sequence(["d4", "d5", "c4", "e6", "Nc3", "Nf6"])
    assert subsequence_match_length(moves_to_fen_sequence(["d4", "d5", "c4", "e6", "Nf3"]), line) == 4
    assert subsequence_match_length(moves_to_fen_sequence(["c4", "e6", "d4", "d5", "Nc3", "Nf6"]), line) == 0
    assert subsequence_match_length(moves_to_fen_sequence(["e4", "e5"]), line) == 0


def test_who_deviated() -> None:
    rep = ["e4", "d5", "exd5", "Qxd5", "Nc3"]
    assert who_deviated(["e4", "d5", "exd5", "Qxd5", "Nf3"], rep, 4, "black") == ("opponent", "Nc3", "Nf3")
    assert who_deviated(["e4", "d5", "exd5", "Nf6"], rep, 3, "black") == ("me", "Qxd5", "Nf6")
    assert who_deviated(["e4", "d5", "exd5", "Qxd5", "Nc3"], rep, 5, "black") == ("none", None, None)
    assert who_deviated(["e4", "d5"], rep, 2, "black") == ("none", None, None)  # game ended inside the line


def test_match_game_picks_longest_and_keeps_ties() -> None:
    scandi = _line(1, "black", ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5"])
    scandi_alt = _line(2, "black", ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qd6"], chapter_id=2)
    white_line = _line(3, "white", ["e4", "e5"])
    moves = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qd8"]
    r = match_game(1, "black", moves, moves_to_fen_sequence(moves), [scandi, scandi_alt, white_line])
    assert r is not None
    assert r.deviated_at_ply == 5 and r.deviation_by == "me" and r.expected_move == "Qa5" and r.played_move == "Qd8"
    assert r.line_ids == [1, 2] and r.chapter_id == 1  # first line in id order names the chapter
    assert r.deviation_fen == moves_to_fen_sequence(moves)[5]
    # black never followed an own move into the line → no match
    moves2 = ["e4", "c5"]
    assert match_game(2, "black", moves2, moves_to_fen_sequence(moves2), [scandi]) is None


def _seed(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'p')", (PLAYER_ID,))
    conn.execute("INSERT INTO books (id, title, color, player_id) VALUES (1, 'Scandi', 'black', %s)", (PLAYER_ID,))
    conn.execute("INSERT INTO chapters (id, book_id, title) VALUES (1, 1, 'Main')")
    for i, moves in enumerate(
        (["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5"], ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qd6"]), start=1
    ):
        conn.execute(
            "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves, fen_sequence) VALUES (%s, 1, %s, %s::jsonb, %s::jsonb)",
            (i, f"line {i}", json.dumps(moves), json.dumps(moves_to_fen_sequence(moves))),
        )


def _game(conn: psycopg.Connection[DictRow], gid: int, moves: list[str], color: str, variant: str = "standard") -> None:
    start = "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1" if variant == "chess960" else None
    fens = moves_to_fen_sequence(moves, start, variant)
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, played_at, moves, fen_sequence, variant, starting_fen)"
        " VALUES (%s, 'lichess', %s, now() - make_interval(mins => %s), %s::jsonb, %s::jsonb, %s, %s)",
        (gid, f"g{gid}", gid, json.dumps(moves), json.dumps(fens), variant, start),
    )
    conn.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, result) VALUES (%s, %s, %s, 'lichess', 'win')",
        (PLAYER_ID, gid, color),
    )


def test_match_player_round_trip(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _seed(conn)
    _game(conn, 1, ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qd8"], "black")  # I deviated at ply 5
    _game(conn, 2, ["e4", "c5", "Nf3"], "black")  # no commitment → no match
    _game(conn, 3, ["e4", "e5"], "white")  # no white lines at all
    _game(conn, 4, ["e4", "e5", "Nc3", "Nc6"], "black", variant="chess960")  # never a candidate
    conn.commit()

    out = match_player(conn, window=1000)
    assert out == {"candidates": 3, "matched": 1, "no_match": 2, "lines": 2}
    grr = conn.execute("SELECT * FROM game_repertoire_results WHERE player_id = %s", (PLAYER_ID,)).fetchall()
    assert len(grr) == 1 and grr[0]["chess_game_id"] == 1 and grr[0]["deviation_by"] == "me"
    assert grr[0]["deviation_fen"] == moves_to_fen_sequence(["e4", "d5", "exd5", "Qxd5", "Nc3"])[5]
    assert grr[0]["canonical_fen"] == "rnb1kbnr/ppp1pppp/8/3q4/8/2N5/PPPP1PPP/R1BQKBNR b KQkq - 0 1"
    lines = conn.execute("SELECT line_id FROM game_result_lines ORDER BY line_id").fetchall()
    assert [r["line_id"] for r in lines] == [1, 2]
    flagged = conn.execute(
        "SELECT chess_game_id FROM player_games WHERE no_repertoire_match ORDER BY chess_game_id"
    ).fetchall()
    assert [r["chess_game_id"] for r in flagged] == [2, 3]
    flagged960 = conn.execute("SELECT no_repertoire_match FROM player_games WHERE chess_game_id = 4").fetchone()
    assert flagged960 and flagged960["no_repertoire_match"] is False  # never a candidate, never flagged

    # rerun: nothing pending, nothing duplicated
    assert match_player(conn, window=1000)["candidates"] == 0
    n = conn.execute("SELECT count(*) AS n FROM game_result_lines").fetchone()
    assert n and n["n"] == 2


def test_window_limits_candidates(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _seed(conn)
    for gid in range(1, 6):
        _game(conn, gid, ["e4", "d5", "exd5", "Qxd5"], "black")
    conn.commit()
    out = match_player(conn, window=2)
    assert out["candidates"] == 2 and out["matched"] == 2


def test_chess960_never_takes_a_window_slot(clean: psycopg.Connection[DictRow]) -> None:
    """Newest game is Chess960; a window of 2 still holds the two standard games."""
    conn = clean
    _seed(conn)
    _game(conn, 1, ["e4", "e5", "Nc3", "Nc6"], "black", variant="chess960")  # newest
    _game(conn, 2, ["e4", "d5", "exd5", "Qxd5"], "black")
    _game(conn, 3, ["e4", "d5", "exd5", "Qxd5"], "black")  # oldest
    conn.commit()
    out = match_player(conn, window=2)
    assert out["candidates"] == 2 and out["matched"] == 2
