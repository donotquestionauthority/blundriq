"""The Repertoire page's move-pair search: a piece to a square, then the reply, anywhere in a line."""

from __future__ import annotations

import itertools
import json
from typing import Any

import chess
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core.constants import MOVE_PAIR_MAX_ROWS, PLAYER_ID
from core.repertoire import move_pair
from core.repertoire.importing import spine
from tests import repertoire_helpers as h

T = move_pair.Target
CASTLING_ROOT = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
# The opponent's queen to d2, two different replies (the case the feature is for).
QGD = ["d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "O-O", "Qd2"]


def _line(
    db: psycopg.Connection[DictRow], lid: int, chapter: int, name: str, moves: list[str], root: str | None = None
) -> None:
    h.line(db, lid, chapter, name, moves, fens=spine(root, moves))


def _search(
    db: psycopg.Connection[DictRow], colour: Any, first: Any, m1: T, m2: T | None = None, la: int = 2
) -> dict[str, Any]:
    return move_pair.search(db, colour=colour, first=first, move1=m1, move2=m2, lookahead_plies=la)


def _ids(res: dict[str, Any]) -> list[tuple[int, int]]:
    return [(r["lineId"], r["ply"]) for r in res["rows"]]


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    h.book(clean, 1, "White book", "white")
    h.chapter(clean, 1, 1, "Main")
    h.book(clean, 2, "Black book", "black")
    h.chapter(clean, 2, 2, "QGD")
    return clean


def _puzzle(
    db: psycopg.Connection[DictRow], lid: int, moves: list[str], colour: str, *, drilled: bool, root: str | None = None
) -> None:
    fens = spine(root, moves)
    db.execute(
        "INSERT INTO puzzles (fen, solution_line, solution_fen_sequence, source_types, color, is_repertoire,"
        " repertoire_line_id, player_id, title, active, drilled_at) VALUES (%s, %s::jsonb, %s::jsonb,"
        " ARRAY['deviation'], %s, TRUE, %s, %s, 'x', TRUE, CASE WHEN %s THEN now() END)",
        (fens[0], json.dumps(moves), json.dumps(fens), colour, lid, PLAYER_ID, drilled),
    )


def _candidates(
    db: psycopg.Connection[DictRow], colour: str, first: str, m1: T, m2: T | None = None
) -> set[tuple[int, int]]:
    """The (line, ply) pairs the SQL filter keeps, before Python."""
    me = "w" if colour == "white" else "b"
    side = me if first == "me" else ("b" if me == "w" else "w")
    other = "b" if side == "w" else "w"
    rows = db.execute(
        move_pair._CANDIDATES_SQL,  # pyright: ignore[reportPrivateUsage]
        {
            "pid": PLAYER_ID,
            "colour": colour,
            "side": side,
            "has2": m2 is not None,
            **move_pair._params("1", m1, side),  # pyright: ignore[reportPrivateUsage]
            **move_pair._params("2", m2, other),  # pyright: ignore[reportPrivateUsage]
        },
    ).fetchall()
    return {(int(r["line_id"]), int(r["i"])) for r in rows}


# --- what matches ------------------------------------------------------------------------------


def test_the_opponents_queen_to_d2_lists_every_reply(db: psycopg.Connection[DictRow]) -> None:
    _line(db, 1, 2, "h6 line", [*QGD, "h6"])
    _line(db, 2, 2, "Nbd7 line", [*QGD, "Nbd7"])
    _line(db, 3, 2, "No reply", QGD)  # ends on move 1: not a pair
    res = _search(db, "black", "opponent", T("Q", "d2"))
    assert _ids(res) == [(2, 10), (1, 10)]  # "Nbd7 line" < "h6 line" in code-point order
    assert [r["label"] for r in res["rows"]] == ["6.Qd2 Nbd7", "6.Qd2 h6"]
    assert res["replies"] == [
        {"piece": "N", "square": "d7", "sans": ["Nbd7"], "count": 1, "inPractice": 0},
        {"piece": "P", "square": "h6", "sans": ["h6"], "count": 1, "inPractice": 0},
    ]
    assert res["total"] == 2 and res["truncated"] is False
    assert (
        move_pair.verified_rows(
            db, colour="black", first="opponent", move1=T("Q", "d2"), move2=None, lookahead_plies=2
        )[1]
        == 0
    )
    only = _search(db, "black", "opponent", T("Q", "d2"), T("P", "h6"))
    assert _ids(only) == [(1, 10)] and only["replies"] is None
    row = only["rows"][0]
    assert row["move2Uci"] == "h7h6" and row["san1"] == "Qd2" and row["san2"] == "h6"
    assert row["fenBefore2"] == spine(None, QGD)[11] and row["fenAfter"] == spine(None, [*QGD, "h6"])[12]
    assert row["movesToPair"] == [*QGD, "h6"] and row["rootFen"] == chess.STARTING_FEN


def test_who_moves_first_is_read_from_the_board_not_the_ply(db: psycopg.Connection[DictRow]) -> None:
    root = spine(None, ["e4"])[1]  # Black to move at ply 0 of a White chapter rooted at a tabiya
    _line(db, 1, 1, "Sicilian", ["c5", "Nf3", "d6"], root=root)
    res = _search(db, "white", "opponent", T("P", "c5"), T("N", "f3"))
    assert _ids(res) == [(1, 0)] and res["rows"][0]["label"] == "1…c5 2.Nf3"
    assert res["rows"][0]["movesToPair"] == ["c5", "Nf3"] and res["rows"][0]["rootFen"] == root
    assert _ids(_search(db, "white", "me", T("P", "c5"))) == []
    assert _ids(_search(db, "white", "me", T("N", "f3"), T("P", "d6"))) == [(1, 1)]


@pytest.mark.parametrize("at", ["move1", "move2"])
@pytest.mark.parametrize(
    ("stored", "root", "target", "server"),
    [
        ("d4", None, T("P", "d4"), "d4"),
        ("e2e4", None, T("P", "e4"), "e4"),
        ("exd5", "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", T("P", "d5"), "exd5"),
        ("b8=Q", "4k3/1P6/8/8/8/8/8/4K3 w - - 0 1", T("P", "b8"), "b8=Q+"),
        ("bxc8=N", "2r1k3/1P6/8/8/8/8/8/4K3 w - - 0 1", T("P", "c8"), "bxc8=N"),
        ("Kef2", "4k3/8/8/8/8/8/8/4K3 w - - 0 1", T("K", "f2"), "Kf2"),
        ("Nbd2", "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2", T("N", "d2"), "Nd2"),
    ],
)
def test_every_spelling_the_importer_keeps_is_found(
    db: psycopg.Connection[DictRow], at: str, stored: str, root: str | None, target: T, server: str
) -> None:
    board = chess.Board(root) if root else chess.Board()
    if at == "move1":
        board.push(board.parse_san(stored))
        reply = board.san(next(iter(board.legal_moves)))
        _line(db, 1, 1, "x", [stored, reply], root=root)
        res = _search(db, "white", "me", target)
        assert _ids(res) == [(1, 0)] and res["rows"][0]["san1"] == server
    else:
        # The token as the reply: the line starts with Black to move, a quiet step, then the token.
        pre = board.copy()
        pre.turn = chess.BLACK
        step = next(m for m in pre.legal_moves if not pre.is_capture(m) and _still_legal(pre, m, stored))
        piece = chess.piece_symbol(pre.piece_type_at(step.from_square) or chess.PAWN).upper()
        _line(db, 1, 1, "x", [pre.san(step), stored], root=pre.fen())
        res = _search(db, "white", "opponent", T(piece, chess.square_name(step.to_square)), target)
        assert _ids(res) == [(1, 0)] and res["rows"][0]["san2"] == server


def _still_legal(board: chess.Board, step: chess.Move, token: str) -> bool:
    after = board.copy()
    after.push(step)
    try:
        return bool(after.parse_san(token))
    except ValueError:
        return False


CASTLING = [
    ("O-O", "O-O-O"),
    ("0-0", "0-0-0"),
    ("e1h1", "e8a8"),
    ("e1-h1", "e8-a8"),
    ("e1xh1", "e8xa8"),
    ("e1g1", "e8c8"),
]


@pytest.mark.parametrize(("white", "black"), CASTLING)
def test_castling_in_every_spelling_at_either_move(db: psycopg.Connection[DictRow], white: str, black: str) -> None:
    _line(db, 1, 1, "castles", [white, black], root=CASTLING_ROOT)
    _line(db, 2, 2, "castles", [white, black], root=CASTLING_ROOT)
    for colour, first in (("white", "me"), ("black", "opponent")):
        res = _search(db, colour, first, T("K", "g1"), T("K", "c8"))
        assert [r["label"] for r in res["rows"]] == ["1.O-O O-O-O"], (colour, white, black)
        assert _ids(_search(db, colour, first, T("K", "g1")))[0][1] == 0
        assert _search(db, colour, first, T("K", "g1"))["replies"] == [
            {"piece": "K", "square": "c8", "sans": ["O-O-O"], "count": 1, "inPractice": 0}
        ]
    # The rook's square is not where castling goes.
    assert _ids(_search(db, "white", "me", T("R", "f1"))) == []
    assert _ids(_search(db, "white", "me", T("K", "h1"))) == []


def test_a_token_naming_the_square_as_its_origin_is_not_a_match(
    db: psycopg.Connection[DictRow],
) -> None:
    _line(db, 1, 1, "knight", ["d4", "d5", "Nd2", "Nf6", "Nd2f3", "e6"])
    kept = db.execute(
        move_pair._CANDIDATES_SQL,  # pyright: ignore[reportPrivateUsage]
        {
            "pid": PLAYER_ID,
            "colour": "white",
            "side": "w",
            "has2": False,
            **move_pair._params("1", T("N", "d2"), "w"),  # pyright: ignore[reportPrivateUsage]
            **move_pair._params("2", None, "b"),  # pyright: ignore[reportPrivateUsage]
        },
    ).fetchall()
    assert sorted(int(r["i"]) for r in kept) == [
        2
    ]  # the text names d2 at ply 4; the board after it has no knight there
    assert _ids(_search(db, "white", "me", T("N", "d2"))) == [(1, 2)]
    assert _ids(_search(db, "white", "me", T("N", "f3"))) == [(1, 4)]


def test_a_stored_null_move_is_not_a_match_and_is_counted(db: psycopg.Connection[DictRow]) -> None:
    _line(db, 1, 1, "null", ["e4", "--", "d4", "e5"])
    rows, dropped = move_pair.verified_rows(
        db, colour="white", first="me", move1=T("P", "e4"), move2=None, lookahead_plies=2
    )
    assert rows == [] and dropped == 1
    assert _ids(_search(db, "white", "me", T("P", "d4"))) == [(1, 2)]


def test_only_effectively_active_lines_of_the_colour(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 3, "Off book", "white", active=False)
    h.chapter(db, 3, 3, "c")
    h.chapter(db, 4, 1, "Off chapter", active=False)
    _line(db, 1, 1, "on", ["e4", "e5"])
    h.line(db, 2, 1, "off line", ["e4", "c5"], active=False)
    _line(db, 3, 3, "in off book", ["e4", "e6"])
    _line(db, 4, 4, "in off chapter", ["e4", "c6"])
    _line(db, 5, 2, "black book", ["e4", "d5"])
    res = _search(db, "white", "me", T("P", "e4"))
    assert _ids(res) == [(1, 0)]
    assert _ids(_search(db, "black", "opponent", T("P", "e4"))) == [(5, 0)]


# --- in practice -------------------------------------------------------------------------------

TEN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d4", "exd4"]


def test_in_practice_means_inside_the_segment_the_solver_is_given(db: psycopg.Connection[DictRow]) -> None:
    _line(db, 1, 1, "B puzzled", TEN)
    _line(db, 2, 1, "A plain", TEN[:8] + ["d3", "d6"])
    _line(db, 3, 1, "C below gate", TEN[:8] + ["O-O", "O-O"])
    for k, reply in enumerate(["d6", "Nf6", "Bc5", "f6"], start=4):  # d4 is the commoner reply, never practised
        _line(db, k, 1, f"D{k}", [*TEN[:4], "d4", reply])
    _puzzle(db, 1, TEN, "w", drilled=True)
    _puzzle(db, 3, TEN[:8] + ["O-O", "O-O"], "w", drilled=False)  # no events, not drilled: not served
    fens = h.game(db, 1, TEN[:2] + ["d4"])
    h.result(db, 1, book_id=1, chapter_id=1, ply=2, by="me", expected="Nf3", played="d4", fen=fens[2], line_ids=[1])
    # Segment: deviation at ply 2 + 2 plies of lookahead = solution_line[0..4].
    inside = _search(db, "white", "opponent", T("N", "c6"), T("B", "c4"))  # plies 3, 4
    assert [(r["lineId"], r["inPractice"]) for r in inside["rows"]] == [(1, True), (2, False), (3, False)]
    assert inside["rows"][0]["lineId"] == 1  # in practice first, ahead of "A plain"
    past = _search(db, "white", "me", T("B", "c4"), T("B", "c5"))  # plies 4, 5: one past the segment
    assert [(r["lineId"], r["inPractice"]) for r in past["rows"]] == [(2, False), (1, False), (3, False)]
    # A wider lookahead takes the same pair in.
    assert _search(db, "white", "me", T("B", "c4"), T("B", "c5"), la=4)["rows"][0]["lineId"] == 1
    # The summary counts in-practice rows first.
    opened = _search(db, "white", "opponent", T("N", "c6"))
    assert opened["replies"] == [
        {"piece": "B", "square": "c4", "sans": ["Bc4"], "count": 3, "inPractice": 1},
        {"piece": "P", "square": "d4", "sans": ["d4"], "count": 4, "inPractice": 0},
    ]


def test_a_puzzle_whose_line_has_changed_under_it_is_not_in_practice(db: psycopg.Connection[DictRow]) -> None:
    _line(db, 1, 1, "changed", TEN)
    _puzzle(db, 1, [*TEN[:8], "O-O", "O-O"], "w", drilled=True)  # the line was re-imported since
    res = _search(db, "white", "opponent", T("N", "c6"), T("B", "c4"))
    assert [(r["lineId"], r["inPractice"]) for r in res["rows"]] == [(1, False)]


def test_replies_group_by_piece_and_square_so_a_chip_counts_what_it_finds(db: psycopg.Connection[DictRow]) -> None:
    # Two knights can reach d7: Nbd7 and Nfd7 are both "knight to d7".
    _line(
        db,
        5,
        2,
        "e",
        ["d4", "d5", "Nf3", "Nf6", "c4", "e6", "Bg5", "Nbd7", "Nc3", "Be7", "e3", "O-O", "Qd2", "Nb6", "a3", "Nbd7"],
    )
    _line(
        db,
        6,
        2,
        "f",
        ["d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "Nbd7", "Nf3", "O-O", "Qd2", "Nb6", "a3", "Nfd7"],
    )
    res = _search(db, "black", "opponent", T("P", "a3"))
    assert res["replies"] == [{"piece": "N", "square": "d7", "sans": ["Nbd7", "Nfd7"], "count": 2, "inPractice": 0}]
    assert _search(db, "black", "opponent", T("P", "a3"), T("N", "d7"))["total"] == 2


# --- order, cap, parity ------------------------------------------------------------------------


def test_order_is_tier_then_names_in_code_point_order_then_ply(db: psycopg.Connection[DictRow]) -> None:
    h.book(db, 3, "Zeta", "white")
    h.chapter(db, 3, 3, "Main")
    _line(db, 1, 1, "b", ["e4", "e5", "Nf3", "Nc6", "Ng1", "Nb8", "Nf3", "Nc6"])
    _line(db, 2, 3, "a", ["Nf3", "Nf6"])
    _line(db, 3, 1, "B", ["Nc3", "e5", "Nf3", "Nc6"])
    res = _search(db, "white", "me", T("N", "f3"))
    # "White book" < "Zeta"; inside "White book", "B" < "b"; inside a line, by ply.
    assert _ids(res) == [(3, 2), (1, 2), (1, 6), (2, 0)]


def test_the_database_orders_names_by_code_point(db: psycopg.Connection[DictRow]) -> None:
    # Case and accents order differently under a linguistic collation; the read is C order.
    names = ["éclat", "Zeta", "alpha", "Alpha", "zeta", "Échange", "beta"]
    replies = ["e5", "c5", "e6", "c6", "d6", "d5", "Nf6"]
    for k, name in enumerate(names):
        _line(db, 10 + k, 1, name, ["e4", replies[k]])
    _line(db, 30, 1, "alpha", ["e4", "Nc6"])  # the same name: ply then line id decide
    got = [r["line"] for r in _search(db, "white", "me", T("P", "e4"))["rows"]]
    assert got == sorted([*names, "alpha"])  # Python's str order is code-point order
    ids = [(r["line"], r["lineId"]) for r in _search(db, "white", "me", T("P", "e4"))["rows"] if r["line"] == "alpha"]
    assert ids == [("alpha", 12), ("alpha", 30)]


def test_the_cut_is_taken_in_the_response_order(db: psycopg.Connection[DictRow]) -> None:
    # Which rows survive the limit is decided by the same order: C collation, then ply.
    shuffle = ["Nf3", "Nc6", "Ng1", "Nb8"]
    _line(db, 1, 1, "alpha", ["e4", "e5", *shuffle * 5, "Nf3", "Nc6"])  # Nf3 at plies 2, 6, ..., 22
    _line(db, 2, 1, "Zeta", ["e4", "c5", "Nf3", "d6"])
    found, _ = move_pair._matched(db, colour="white", first="me", move1=T("N", "f3"), move2=None, lookahead_plies=2)  # pyright: ignore[reportPrivateUsage]
    found.reverse()  # arrival order must not matter
    first = move_pair._ordered(db, found, 1)  # pyright: ignore[reportPrivateUsage]
    assert [(r["line"], r["ply"]) for r in first] == [("Zeta", 2)]
    alpha = [r for r in found if r["lineId"] == 1]
    assert len(alpha) == 6
    assert [(r["line"], r["ply"]) for r in move_pair._ordered(db, alpha, 1)] == [("alpha", 2)]  # pyright: ignore[reportPrivateUsage]
    for arrival in (alpha, alpha[::-1], alpha[2:] + alpha[:2]):
        assert [r["ply"] for r in move_pair._ordered(db, arrival, 3)] == [2, 6, 10]  # pyright: ignore[reportPrivateUsage]


def test_the_cap_keeps_the_first_rows_and_counts_them_all(db: psycopg.Connection[DictRow]) -> None:
    n = MOVE_PAIR_MAX_ROWS + 3
    for k in range(n):
        h.chapter(db, 100 + k, 1, f"ch{k:04d}")
        _line(db, 1000 + k, 100 + k, "line", ["e4", "e5" if k % 2 else "c5"])
    # Practised, and last by name: the database must order before it cuts.
    h.chapter(db, 99, 1, "zz")
    _line(db, 999, 99, "line", ["e4", "d6", "d4"])
    _puzzle(db, 999, ["e4", "d6", "d4"], "w", drilled=True)
    res = _search(db, "white", "me", T("P", "e4"))
    rows, _ = move_pair.verified_rows(db, colour="white", first="me", move1=T("P", "e4"), move2=None, lookahead_plies=2)
    assert res["rows"][0]["lineId"] == 999 and res["rows"][0]["inPractice"] is True
    n += 1
    assert len(rows) == n and res["total"] == n and res["truncated"] is True
    assert len(res["rows"]) == MOVE_PAIR_MAX_ROWS
    assert _ids(res) == [(r["lineId"], r["ply"]) for r in rows[:MOVE_PAIR_MAX_ROWS]]
    assert res["replies"] == [
        {"piece": "P", "square": "d6", "sans": ["d6"], "count": 1, "inPractice": 1},
        {"piece": "P", "square": "c5", "sans": ["c5"], "count": (n - 1) - (n - 1) // 2, "inPractice": 0},
        {"piece": "P", "square": "e5", "sans": ["e5"], "count": (n - 1) // 2, "inPractice": 0},
    ]
    assert res["rows"][-1]["movesToPair"] == ["e4", "e5" if res["rows"][-1]["lineId"] % 2 else "c5"]


PARITY_LINES = [
    (
        "Italian",
        1,
        None,
        [
            "e4",
            "e5",
            "Nf3",
            "Nc6",
            "Bc4",
            "Bc5",
            "c3",
            "Nf6",
            "d4",
            "exd4",
            "cxd4",
            "Bb4+",
            "Bd2",
            "Bxd2+",
            "Nbxd2",
            "d5",
        ],
    ),
    ("Castles", 1, CASTLING_ROOT, ["e1-h1", "e8xa8", "Rf1f8+", "Rdxf8"]),
    ("Promo", 1, "2r1k3/1P6/8/8/8/8/8/4K3 w - - 0 1", ["bxc8=N", "Kf7", "Nd6+", "Ke6"]),
    ("Long", 1, None, ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4"]),
    ("QGD", 2, None, [*QGD, "h6", "Bh4", "b6"]),
    ("Caro", 2, None, ["e4", "c6", "d4", "d5", "e5", "Bf5", "Nf3", "e6", "Be2", "Nd7", "O-O", "Ne7"]),
]


def _oracle(
    lines: list[tuple[int, str, str | None, list[str], str]], colour: str, first: str, m1: T, m2: T | None
) -> list[tuple[int, int]]:
    """Every (line, ply) by brute force: parse each stored move and compare piece and destination;
    no prefilter. Ordered as the read orders a repertoire with no puzzles."""
    out: list[tuple[str, str, str, int, int]] = []
    for lid, book_colour, root, moves, key in lines:
        if book_colour != colour:
            continue
        board = chess.Board(root) if root else chess.Board()
        me = chess.WHITE if colour == "white" else chess.BLACK
        for i, token in enumerate(moves):
            move = board.parse_san(token)
            if i + 1 < len(moves) and (board.turn == me) == (first == "me"):
                after = board.copy()
                after.push(move)
                reply = after.parse_san(moves[i + 1])
                ok1 = board.piece_type_at(move.from_square) == move_pair.PIECES[m1.piece] and move.to_square == (
                    chess.parse_square(m1.square)
                )
                ok2 = m2 is None or (
                    after.piece_type_at(reply.from_square) == move_pair.PIECES[m2.piece]
                    and reply.to_square == chess.parse_square(m2.square)
                )
                if ok1 and ok2:
                    out.append((key.split("|")[0], key.split("|")[1], key.split("|")[2], i, lid))
            board.push(move)
    out.sort()
    return [(lid, i) for *_, i, lid in out]


def test_parity_with_a_brute_force_scan(db: psycopg.Connection[DictRow]) -> None:
    books = {1: ("White book", "Main", "white"), 2: ("Black book", "QGD", "black")}
    lines: list[tuple[int, str, str | None, list[str], str]] = []
    for lid, (name, chapter, root, moves) in enumerate(PARITY_LINES, start=1):
        _line(db, lid, chapter, name, moves, root=root)
        b, c, colour = books[chapter]
        lines.append((lid, colour, root, moves, f"{b}|{c}|{name}"))
    squares = ["d4", "d5", "e4", "f3", "d2", "c8", "g1", "c8", "f8", "h6", "d6", "e6", "f7"]
    checked = hits = 0
    for colour, first, piece, sq in itertools.product(
        ("white", "black"), ("me", "opponent"), move_pair.PIECES, squares
    ):
        m1 = T(piece, sq)
        for m2 in (None, T("P", "d5"), T("K", "c8"), T("R", "f8"), T("N", "f3")):
            rows, dropped = move_pair.verified_rows(
                db, colour=colour, first=first, move1=m1, move2=m2, lookahead_plies=2
            )  # type: ignore[arg-type]
            want = _oracle(lines, colour, first, m1, m2)
            assert set(want) <= _candidates(db, colour, first, m1, m2), (
                colour,
                first,
                m1,
                m2,
            )  # nothing true filtered out
            assert [(r["lineId"], r["ply"]) for r in rows] == want, (colour, first, m1, m2)
            assert dropped == 0
            checked += 1
            hits += len(want)
    assert checked > 1000 and hits > 30  # the grid finds things: not agreement on empty answers


# --- the prefilter alone, over the grammar of accepted spellings ---------------------------------

FIXED = [
    CASTLING_ROOT,
    "r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1",
    "r3k2r/1P6/8/8/8/8/1p6/R3K2R w KQkq - 0 1",
    "r3k2r/1P6/8/8/8/8/1p6/R3K2R b KQkq - 0 1",
    "rnbqkbnr/pppp1ppp/8/4p3/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2",
    "4k3/1P6/8/3pP3/8/8/8/4K3 w - d6 0 2",
]


def _spellings(b: chess.Board, m: chess.Move) -> set[str]:
    fr, to = chess.square_name(m.from_square), chess.square_name(m.to_square)
    letter = chess.piece_symbol(b.piece_type_at(m.from_square) or chess.PAWN).upper()
    dests = {to}
    if b.is_castling(m):
        dests.add(("h" if to[0] == "g" else "a") + to[1])
    promos = [""] if not m.promotion else ["=Q", "Q", "q", "=q", "=R", "R", "=B", "B", "=N", "N", "n"]
    out = {
        pre + org + sep + d + pr + suf
        for pre, org, sep, d, pr, suf in itertools.product(
            ["", letter, letter.lower()],
            ["", fr[0], fr[1], fr],
            ["", "-", "x", ":"],
            dests,
            promos,
            ["", "+", "#", "!", "?"],
        )
    }
    out |= {(fr + d).upper() for d in dests}
    if b.is_castling(m):
        out |= {
            base + suf
            for base in ("O-O", "O-O-O", "0-0", "0-0-0", "o-o", "o-o-o", "OO", "OOO", "00", "000")
            for suf in ("", "+")
        }
    return out


def test_the_prefilter_keeps_every_spelling_parse_san_accepts(db: psycopg.Connection[DictRow]) -> None:
    sql = "SELECT t1 FROM (SELECT lower(x) AS t1 FROM unnest(%(toks)s::text[]) AS x) s WHERE " + move_pair._clause("1")  # pyright: ignore[reportPrivateUsage]
    accepted: dict[tuple[str, str], set[str]] = {}
    for fen in FIXED:
        b = chess.Board(fen)
        for m in b.legal_moves:
            piece = chess.piece_symbol(b.piece_type_at(m.from_square) or chess.PAWN).upper()
            target = (piece, chess.square_name(m.to_square))  # castling is the king's move to g/c
            for tok in _spellings(b, m):
                try:
                    parsed = b.parse_san(tok)
                except ValueError:
                    continue
                if parsed == m:
                    accepted.setdefault(target, set()).add(tok)
    total = 0
    for (piece, sq), toks in accepted.items():
        kept = {
            r["t1"]
            for r in db.execute(sql, {"toks": sorted(toks), **move_pair._params("1", T(piece, sq), "w")}).fetchall()
        }  # pyright: ignore[reportPrivateUsage]
        assert {t.lower() for t in toks} <= kept, (piece, sq, sorted({t.lower() for t in toks} - kept))
        total += len(toks)
    assert total > 4000 and ("K", "g1") in accepted and ("K", "c8") in accepted


def test_the_board_clause_holds_after_every_legal_move(db: psycopg.Connection[DictRow]) -> None:
    """On the board after each legal move of the fixed positions, the clause accepts that move's
    piece and destination (a promotion under P to the last rank, castling under K)."""
    sql = "SELECT " + move_pair._board_clause("1", "%(fen)s") + " AS ok"  # pyright: ignore[reportPrivateUsage]
    checked = 0
    for fen in FIXED:
        b = chess.Board(fen)
        for m in b.legal_moves:
            piece = chess.piece_symbol(b.piece_type_at(m.from_square) or chess.PAWN).upper()
            mover = "w" if b.turn == chess.WHITE else "b"
            after = b.copy()
            after.push(m)
            params = {"fen": after.fen(), **move_pair._params("1", T(piece, chess.square_name(m.to_square)), mover)}  # pyright: ignore[reportPrivateUsage]
            row = db.execute(sql, params).fetchone()
            assert row is not None and row["ok"] is True, (fen, m.uci())
            checked += 1
    assert checked > 150


def test_the_board_clause_drops_another_piece_on_the_square(db: psycopg.Connection[DictRow]) -> None:
    _line(db, 1, 1, "knight d4", ["Nf3", "d5", "Nd4", "e5"])
    _line(db, 2, 1, "queen d4", ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qd4"])
    _line(db, 3, 1, "bishop f4", ["d3", "d5", "Bf4", "Nf6"])
    assert _candidates(db, "white", "me", T("P", "d4")) == set()
    assert _candidates(db, "white", "opponent", T("P", "d4")) == set()
    assert _candidates(db, "white", "me", T("N", "f4")) == set()
    assert _candidates(db, "white", "me", T("N", "d4")) == {(1, 2)}
    assert _ids(_search(db, "white", "me", T("B", "f4"))) == [(3, 2)]


def test_promotions_pass_the_board_clause_both_ways_and_python_decides(db: psycopg.Connection[DictRow]) -> None:
    promo_root = "2r1k3/1P6/8/8/8/8/8/4K3 w - - 0 1"
    knight_root = "4k3/8/1N6/8/8/8/8/4K3 w - - 0 1"
    _line(db, 1, 1, "promotes", ["bxc8=N", "Kf7"], root=promo_root)
    _line(db, 2, 1, "knight", ["Nc8", "Kd8"], root=knight_root)
    # A knight stands on c8 after both moves: SQL keeps both for either search.
    assert _candidates(db, "white", "me", T("N", "c8")) == {(1, 0), (2, 0)}
    assert _candidates(db, "white", "me", T("P", "c8")) == {(1, 0), (2, 0)}
    assert _ids(_search(db, "white", "me", T("N", "c8"))) == [(2, 0)]  # the pawn's promotion is not a knight move
    assert _ids(_search(db, "white", "me", T("P", "c8"))) == [(1, 0)]  # the knight's move is not a pawn's


def test_castling_searched_as_a_rook_move_passes_the_board_clause_only(db: psycopg.Connection[DictRow]) -> None:
    _line(db, 1, 1, "castles", ["O-O", "Kd8"], root=CASTLING_ROOT)
    board_only = "SELECT " + move_pair._board_clause("1", "%(fen)s") + " AS ok"  # pyright: ignore[reportPrivateUsage]
    after = spine(CASTLING_ROOT, ["O-O"])[1]
    row = db.execute(board_only, {"fen": after, **move_pair._params("1", T("R", "f1"), "w")}).fetchone()  # pyright: ignore[reportPrivateUsage]
    assert row is not None and row["ok"] is True  # the rook does stand on f1
    assert _candidates(db, "white", "me", T("R", "f1")) == set()  # the text clause refuses it
    assert _ids(_search(db, "white", "me", T("R", "f1"))) == []
    assert _candidates(db, "white", "me", T("K", "g1")) == {(1, 0)}
    assert _ids(_search(db, "white", "me", T("K", "g1"))) == [(1, 0)]


def test_scoping_the_visible_rows_to_lines_changes_no_row(db: psycopg.Connection[DictRow]) -> None:
    from core.puzzles import visibility

    _line(db, 1, 1, "A", TEN)
    _line(db, 2, 1, "B", [*TEN[:8], "d3", "d6"])
    _puzzle(db, 1, TEN, "w", drilled=True)
    _puzzle(db, 2, [*TEN[:8], "d3", "d6"], "w", drilled=True)
    fens = h.game(db, 1, TEN[:2] + ["d4"])
    h.result(db, 1, book_id=1, chapter_id=1, ply=2, by="me", expected="Nf3", played="d4", fen=fens[2], line_ids=[1])
    cols = "repertoire_line_id, presentation_ply"
    allrows = db.execute(
        f"SELECT {cols} FROM ({visibility.repertoire_rows_sql(2, collapse=False)}) v ORDER BY 1", {"pid": PLAYER_ID}
    ).fetchall()
    scoped = db.execute(
        f"SELECT {cols} FROM ({visibility.repertoire_rows_sql(2, collapse=False, lines=True)}) v ORDER BY 1",
        {"pid": PLAYER_ID, "scope_ids": [1]},
    ).fetchall()
    assert [dict(r) for r in scoped] == [dict(r) for r in allrows if r["repertoire_line_id"] == 1]
    assert len(allrows) == 2 and scoped[0]["presentation_ply"] == 4


# --- the route ---------------------------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, db: psycopg.Connection[DictRow]) -> TestClient:
    _line(db, 1, 2, "h6 line", [*QGD, "h6"])
    db.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_the_route(client: TestClient) -> None:
    base = {"colour": "black", "first": "opponent", "p1": "Q", "s1": "d2"}
    r = client.get("/repertoire/move-pair", params=base)
    assert r.status_code == 200 and r.json()["rows"][0]["label"] == "6.Qd2 h6"
    assert client.get("/repertoire/move-pair", params={**base, "p2": "P", "s2": "h6"}).json()["total"] == 1
    for bad in (
        {"p1": "X"},
        {"s1": "i9"},
        {"s1": "D2"},
        {"p2": "P"},
        {"s2": "h6"},
        {"first": "both"},
        {"colour": "red"},
    ):
        assert client.get("/repertoire/move-pair", params={**base, **bad}).status_code == 422, bad


def test_the_route_uses_the_lookahead_setting(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = move_pair.search

    def spy(*a: Any, **k: Any) -> dict[str, Any]:
        seen.append(k["lookahead_plies"])
        return real(*a, **k)

    monkeypatch.setattr(move_pair, "search", spy)
    assert client.put("/settings", json={"repertoire_puzzle_lookahead_moves": 3}).status_code == 200
    client.get("/repertoire/move-pair", params={"colour": "black", "first": "opponent", "p1": "Q", "s1": "d2"})
    assert seen == [6]


def test_the_route_reads_only_the_lookahead_setting(client: TestClient, db: psycopg.Connection[DictRow]) -> None:
    db.execute(
        "INSERT INTO settings (id, data) VALUES (1, %s::jsonb) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
        (json.dumps({"daily_puzzle_target": "not a number", "repertoire_puzzle_lookahead_moves": 1}),),
    )
    db.commit()
    r = client.get("/repertoire/move-pair", params={"colour": "black", "first": "opponent", "p1": "Q", "s1": "d2"})
    assert r.status_code == 200 and r.json()["total"] == 1


def test_the_route_needs_login(app_env: None) -> None:
    from api.main import create_app

    assert (
        TestClient(create_app())
        .get("/repertoire/move-pair", params={"colour": "white", "first": "me", "p1": "P", "s1": "e4"})
        .status_code
        == 401
    )
