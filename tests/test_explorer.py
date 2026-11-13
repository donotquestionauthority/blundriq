"""The Opening Explorer: the repertoire's reading of one board (`read.board_moves`, held to
`project_ply` over `rep_lines`), the lines through it, the player's games from it
(`explorer.moves_from`), the off-book judgement of a path's last move, and the two routes."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import timedelta
from typing import Any, LiteralString, cast

import chess
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core import explorer
from core.constants import EXPLORER_LINES_PAGE, EXPLORER_MAX_PLIES, OPENING_PREFIX_PLIES, PLAYER_ID
from core.repertoire import read
from core.repertoire.importing import spine
from core.settings import Settings
from tests import repertoire_helpers as h
from tests.review.position_helpers import NOW, Games

Conn = psycopg.Connection[DictRow]


def moves(text: str) -> list[str]:
    return [str(m) for m in text.split()]


SHUFFLE = moves("Nf3 Nf6 Ng1 Ng8")
RUY = moves("e4 e5 Nf3 Nc6 Bb5")
ITALIAN = moves("e4 e5 Nf3 Nc6 Bc4")
LONG = moves(
    "e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6 Nf3 Bf5 Bd2 c6 Bc4 e6 Qe2 Bb4 O-O-O Nbd7 Kb1 O-O-O "
    "a3 Bxc3 Bxc3 Qc7 d5 cxd5 Bxd5 exd5 Rhe1 Kb8 Qe7 Rc8"
)  # 32 plies, Black's 31st-ply move Rc8 past the statistics' prefix
DEEP = moves("Nc3 Nc6 Nb1 Nb8") * 25 + moves("d4 d5 c4 c6 Nc3")  # the Slav board first reached at line ply 104


def fen_after(path: list[str]) -> str:
    return spine(None, path)[-1]


def repertoire(conn: Conn) -> None:
    """A White book and a Black book that between them hold every case the reduction has."""
    h.player(conn)
    h.book(conn, 1, "White book", "white")
    h.chapter(conn, 1, 1, "Open games")
    h.line(conn, 1, 1, "Ruy", RUY)
    h.line(conn, 2, 1, "Italian", ITALIAN)  # conflicts with the Ruy after 2…Nc6
    h.line(conn, 3, 1, "Sicilian", moves("e4 c5 Nf3"))
    h.line(conn, 4, 1, "Shuffle", SHUFFLE + RUY)  # the start board twice; the Ruy boards transposed
    h.line(conn, 5, 1, "Indian", moves("d4 Nf6"))  # ends on the opponent's move…
    h.line(conn, 6, 1, "Indian stop", moves("d4 Nf6 c4"))
    h.line(conn, 7, 1, "Stub", moves("d4 e6"))  # …and here it ends on the player's turn
    bad = moves("d4 e5")
    h.line(conn, 8, 1, "Broken", [*bad, "Zz9"], fens=[*spine(None, bad), fen_after([*bad, "e3"])])
    h.chapter(conn, 2, 1, "Deep")
    h.line(conn, 9, 2, "A deep", DEEP)
    h.chapter(conn, 3, 1, "Inactive", active=False)
    h.line(conn, 10, 3, "Hidden", moves("e4 e5 Nf3 Nc6 d4"))
    h.book(conn, 2, "Black book", "black")
    h.chapter(conn, 4, 2, "Replies")
    h.line(conn, 11, 4, "Open", moves("e4 e5 Nf3 Nc6"))
    h.line(conn, 12, 4, "Sicilian", moves("e4 c5"))
    h.line(conn, 13, 4, "QGD", moves("d4 d5 c4 e6"))
    h.line(conn, 14, 4, "Scandi long", LONG)
    conn.commit()


@pytest.fixture()
def rep(clean: Conn) -> Conn:
    repertoire(clean)
    return clean


# --- the repertoire's reading, held to project_ply ----------------------------------------


def _expected(conn: Conn, fen: str, colour: str) -> tuple[str, dict[str, tuple[int, int, int]], Any, int]:
    """(status, {move: (line_id, line_ply, lines)}, representative, lines_through) from the
    whole-line readers: `project_ply` over `rep_lines` at the player's turn; at the opponent's,
    every occurrence `_LINES_SQL` returns, reduced to its distinct moves."""
    if read.side_to_move(fen) == colour[0]:
        occ = read.rep_lines(conn, [fen], book_color=cast(read.BookColor, colour), with_stats=False)[fen]
        e = read.project_ply(fen, occ)
        found: dict[str, tuple[int, int, int]] = {}
        if e["status"] == read.STATUS_CONFLICT:
            found = {g["move"]: (g["line_id"], g["line_ply"], g["more_lines"] + 1) for g in e["conflict"]}
        elif e["status"] in (read.STATUS_MATCH, read.STATUS_AGREE):
            members = [o for o in read.canonicalise(fen, [o for o in occ if o["expected_move"] is not None])]
            pool = [o for o in members if o["occurrence_fen"] == fen] or members
            lines = len({o["line_id"] for o in pool if o["canonical_move"] == e["book_move"]})
            found = {e["book_move"]: (e["line_id"], e["line_ply"], lines)}
        selected = e["status"] in (read.STATUS_MATCH, read.STATUS_AGREE, read.STATUS_END_OF_LINE)
        rep = (e["line_id"], e["line_ply"]) if selected else None
        return e["status"], found, rep, len({o["line_id"] for o in occ})
    query = cast(LiteralString, read._LINES_SQL.format(color_clause=f"AND bk.color = '{colour}'"))  # pyright: ignore[reportPrivateUsage]
    rows = conn.execute(query, {"pid": PLAYER_ID, "fens": [fen]}).fetchall()
    if not rows:
        return read.STATUS_NONE, {}, None, 0
    occ = [
        {
            "book": r["book_title"],
            "chapter": r["chapter_title"],
            "line_name": r["line_name"],
            "line_id": r["line_id"],
            "line_ply": r["line_ply"],
            "expected_move": r["expected_move"],
        }
        for r in rows
    ]
    through = len({o["line_id"] for o in occ})
    try:
        pool = read.canonicalise(fen, [o for o in occ if o["expected_move"] is not None])
    except ValueError:
        return read.STATUS_UNREADABLE, {}, None, through
    groups = read.conflict_groups(pool)
    return (
        read.STATUS_REPLIES,
        {g["move"]: (g["line_id"], g["line_ply"], g["more_lines"] + 1) for g in groups},
        None,
        through,
    )


def _actual(conn: Conn, fen: str, colour: str) -> tuple[str, dict[str, tuple[int, int, int]], Any, int]:
    got = read.board_moves(conn, fen, colour)
    rep = got["representative"]
    return (
        got["status"],
        {m["san"]: (m["line_id"], m["line_ply"], m["lines"]) for m in got["moves"]},
        (rep["line_id"], rep["line_ply"]) if rep else None,
        got["lines_through"],
    )


def every_line_board(conn: Conn) -> set[tuple[str, str]]:
    rows = conn.execute(
        "SELECT bk.color, f.fen FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id"
        " JOIN books bk ON bk.id = ch.book_id, jsonb_array_elements_text(rl.fen_sequence) AS f(fen)"
    ).fetchall()
    return {(str(r["color"]), str(r["fen"])) for r in rows}


def test_board_moves_agrees_with_project_ply_at_every_board_of_every_line(rep: Conn) -> None:
    boards = every_line_board(rep)
    # The same boards with other counters: every occurrence there is a transposition.
    boards |= {(c, f.rsplit(" ", 2)[0] + " 9 40") for c, f in list(boards)}
    seen: set[str] = set()
    for colour, fen in sorted(boards):
        for side in ("white", "black"):
            assert _actual(rep, fen, side) == _expected(rep, fen, side), (colour, side, fen)
            seen.add(read.board_moves(rep, fen, side)["status"])
    assert seen == {"match", "agree", "end_of_line", "conflict", "unreadable", "none", "replies"}


def test_exact_outranks_transposed_and_a_transposed_line_twice_follows_project_ply(rep: Conn) -> None:
    # After the Shuffle's Ruy the board is the Ruy's with other counters: exact only for the
    # Shuffle, transposed for the Ruy and the Italian, which disagree.
    got = read.board_moves(rep, fen_after([*SHUFFLE, *RUY[:4]]), "white")
    assert (got["status"], [m["san"] for m in got["moves"]]) == ("match", ["Bb5"])
    assert (got["representative"]["line_id"], got["representative"]["line_ply"], got["lines_through"]) == (4, 8, 3)
    # With counters no line has, the start board is transposed for every line, the Shuffle's two
    # occurrences included: the transposed branch's single-move rule makes it a conflict.
    far = chess.STARTING_FEN.rsplit(" ", 2)[0] + " 9 40"
    assert read.board_moves(rep, far, "white")["status"] == "conflict"
    start = read.board_moves(rep, chess.STARTING_FEN, "white")
    assert start["status"] == "conflict"
    assert {m["san"]: m["lines"] for m in start["moves"]} == {"e4": 3, "Nf3": 1, "d4": 4, "Nc3": 1}
    assert start["lines_through"] == 9  # active lines of the White book, inactive chapter excluded


def test_one_line_holding_a_transposed_board_twice_with_two_moves_is_a_conflict(clean: Conn) -> None:
    h.player(clean)
    h.book(clean, 1, "W", "white")
    h.chapter(clean, 1, 1, "C")
    h.line(clean, 1, 1, "Fianchetto", moves("g3 g6 Nf3 Nf6 Ng1 Ng8 Bg2"))  # the board after 1.g3 g6 twice
    clean.commit()
    transposed = fen_after(moves("g3 g6")).rsplit(" ", 2)[0] + " 9 40"
    got = read.board_moves(clean, transposed, "white")
    assert (got["status"], [m["san"] for m in got["moves"]]) == ("conflict", ["Nf3", "Bg2"])
    assert _actual(clean, transposed, "white") == _expected(clean, transposed, "white")
    assert read.board_moves(clean, fen_after(moves("g3 g6")), "white")["status"] == "match"


def test_conflict_end_of_line_unreadable_and_replies(rep: Conn) -> None:
    open_board = fen_after(moves("e4 e5 Nf3 Nc6"))
    got = read.board_moves(rep, open_board, "white")
    assert got["status"] == "conflict"
    assert [(m["san"], m["line_id"], m["lines"]) for m in got["moves"]] == [("Bc4", 2, 1), ("Bb5", 1, 1)]
    stub = read.board_moves(rep, fen_after(moves("d4 e6")), "white")
    assert (stub["status"], stub["moves"], stub["representative"]["line_id"]) == ("end_of_line", [], 7)
    assert read.board_moves(rep, fen_after(moves("d4 e5")), "white")["status"] == "unreadable"
    replies = read.board_moves(rep, chess.STARTING_FEN, "black")
    assert replies["status"] == "replies"
    assert {m["san"]: m["lines"] for m in replies["moves"]} == {"e4": 3, "d4": 1}
    assert read.board_moves(rep, fen_after(moves("c4")), "black")["status"] == "none"


def test_one_move_under_two_spellings_counts_its_lines_once(clean: Conn) -> None:
    h.player(clean)
    h.book(clean, 1, "W", "white")
    h.chapter(clean, 1, 1, "C")
    h.line(clean, 1, 1, "a", moves("e4 e5 Nf3"))
    h.line(clean, 2, 1, "b", moves("e4 e5 Ng1f3"), fens=spine(None, moves("e4 e5 Nf3")))
    h.line(clean, 3, 1, "c", ["e4", "e5", "Nf3", "Nc6"])
    clean.commit()
    got = read.board_moves(clean, fen_after(moves("e4 e5")), "white")
    assert (got["status"], [(m["san"], m["lines"]) for m in got["moves"]]) == ("match", [("Nf3", 3)])


def test_a_line_continuing_past_the_prefix_and_a_deep_transposed_representative(rep: Conn) -> None:
    at31 = read.board_moves(rep, fen_after(LONG[:31]), "black")
    assert (at31["status"], [m["san"] for m in at31["moves"]], at31["moves"][0]["line_ply"]) == ("match", ["Rc8"], 31)
    slav = read.board_moves(rep, fen_after(moves("d4 d5 c4 c6")), "white")
    assert (slav["status"], slav["moves"][0]["san"], slav["moves"][0]["line_ply"]) == ("agree", "Nc3", 104)
    page = read.board_lines(rep, fen_after(moves("d4 d5 c4 c6")), "white", offset=0, limit=EXPLORER_LINES_PAGE)
    assert page == {
        "total": 1,
        "rows": [{"book": "White book", "chapter": "Deep", "line_name": "A deep", "line_id": 9, "line_ply": 104}],
    }


# --- the lines through a board ---------------------------------------------------------------


def test_a_line_holding_the_board_twice_is_one_row_at_its_earliest_occurrence(rep: Conn) -> None:
    page = read.board_lines(rep, chess.STARTING_FEN, "white", offset=0, limit=EXPLORER_LINES_PAGE)
    assert page["total"] == 9
    assert [(r["line_id"], r["line_ply"]) for r in page["rows"]] == [
        (9, 0),
        (8, 0),
        (5, 0),
        (6, 0),
        (2, 0),
        (1, 0),
        (4, 0),
        (3, 0),
        (7, 0),
    ]  # chapter "Deep" < "Open games", then line name in byte order
    one = read.board_lines(rep, fen_after(SHUFFLE), "white", offset=0, limit=EXPLORER_LINES_PAGE)
    assert [(r["line_id"], r["line_ply"]) for r in one["rows"] if r["line_id"] == 4] == [(4, 0)]


def test_lines_pages_in_byte_order_and_past_the_end(clean: Conn) -> None:
    h.player(clean)
    h.book(clean, 1, "W", "white")
    h.chapter(clean, 1, 1, "C")
    board = chess.Board()
    pairs: list[list[str]] = []
    for first in sorted(board.san(m) for m in board.legal_moves):
        board.push_san(first)
        pairs += [[first, board.san(r)] for r in board.legal_moves]
        board.pop()
    for i, pair in enumerate(pairs[:55]):
        h.line(clean, i + 1, 1, ("a" if i % 2 else "B") + f"{i:02d}", pair)
    clean.commit()
    first = read.board_lines(clean, chess.STARTING_FEN, "white", offset=0, limit=50)
    rest = read.board_lines(clean, chess.STARTING_FEN, "white", offset=50, limit=50)
    names = [r["line_name"] for r in first["rows"] + rest["rows"]]
    assert (first["total"], len(first["rows"]), len(rest["rows"])) == (55, 50, 5)
    assert names == sorted(names, key=lambda n: n.encode()) and names[0].startswith("B")
    assert read.board_lines(clean, chess.STARTING_FEN, "white", offset=100, limit=50) == {"total": 55, "rows": []}


# --- the player's games from a board ---------------------------------------------------------


def scope(time_class: str = "all", **over: Any) -> explorer.Scope:
    config = Settings(**over)
    return explorer.Scope(config.explorer_history_months, time_class, config.time_class_focus)


def stats(conn: Conn, path: list[str], colour: str = "white", sc: explorer.Scope | None = None) -> tuple[Any, Any]:
    board = chess.Board()
    for m in path:
        board.push_san(m)
    node, rows = explorer.moves_from(conn, sc or scope(), colour, board)
    return node, {r["san"]: r for r in rows}


def test_first_occurrence_only_from_the_start_and_transpositions_merged(clean: Conn) -> None:
    g = Games(clean)
    g.add([*SHUFFLE, "e4"], colour="white", result="win")  # the start again at ply 4: not a second visit
    g.add(moves("d4 d5 c4 e6 Nc3"), colour="white", result="draw")
    g.add(moves("c4 e6 d4 d5 Nc3"), colour="white", result="loss")
    g.add(moves("e4 e5"), colour="black")
    node, rows = stats(clean, [])
    assert {k: r["n"] for k, r in rows.items()} == {"Nf3": 1, "d4": 1, "c4": 1}
    assert (node["n"], node["w"], node["d"], node["l"], node["continued"]) == (3, 1, 1, 1, 3)
    _, qgd = stats(clean, moves("d4 d5 c4 e6"))
    assert (qgd["Nc3"]["n"], qgd["Nc3"]["w"], qgd["Nc3"]["d"], qgd["Nc3"]["l"]) == (2, 0, 1, 1)


def test_the_prefix_last_board_counts_but_has_no_next_move(clean: Conn) -> None:
    g = Games(clean)
    g.add(LONG, colour="black", result="win")
    node, rows = stats(clean, LONG[:29], "black")
    assert (node["n"], node["continued"], list(rows)) == (1, 1, [LONG[29]])
    node, rows = stats(clean, LONG[:30], "black")
    assert (node["n"], node["continued"], rows) == (1, 0, {})
    g.add(moves("e4 e5 Qh5 Nc6 Bc4 Nf6 Qxf7#"), colour="white", result="win")
    end, after = stats(clean, moves("e4 e5 Qh5 Nc6 Bc4 Nf6 Qxf7#"))
    assert (end["n"], end["continued"], after) == (1, 0, {})


def test_ratings_scores_and_expectation(clean: Conn) -> None:
    g = Games(clean)
    g.add(moves("e4 e5"), colour="white", result="win", mine=1500, theirs=1500)
    g.add(moves("e4 c5"), colour="white", result="draw", mine=1500, theirs=1900)
    g.add(moves("e4 c6"), colour="white", result="loss", mine=None, theirs=1500)
    node, rows = stats(clean, [])
    e2 = 1 / (1 + 10 ** (400 / 400))
    assert (node["n"], node["n_rated"]) == (3, 2)
    assert node["score"] == pytest.approx(0.5, abs=1e-4)
    assert node["vs_expectation"] == pytest.approx(((1 - 0.5) + (0.5 - e2)) / 2, abs=1e-4)
    assert (rows["e4"]["w"], rows["e4"]["d"], rows["e4"]["l"], rows["e4"]["n_rated"]) == (1, 1, 1, 2)
    _, unrated = stats(clean, moves("e4"))
    assert (unrated["c6"]["n"], unrated["c6"]["n_rated"], unrated["c6"]["vs_expectation"]) == (1, 0, None)


def test_time_class_chess960_and_colour(clean: Conn) -> None:
    g = Games(clean)
    g.add(moves("e4 e5"), colour="white", time_class="rapid")
    g.add(moves("d4 d5"), colour="white", time_class="blitz")
    g.add(moves("c4 e5"), colour="white", variant="chess960")
    # A Chess960 game whose row carries a prefix is still history, never counted.
    g.add(moves("Nc3 e5"), colour="white", variant="chess960")
    clean.execute(
        "UPDATE chess_games SET opening_moves = %s::jsonb, opening_keys = bq_opening_keys(%s::jsonb) WHERE id = %s",
        ('["Nc3", "e5"]', json.dumps(spine(None, moves("Nc3 e5"))), g.next_id - 1),
    )
    g.add(moves("Nf3 d5"), colour="black")
    _, everything = stats(clean, [], sc=scope("all"))
    _, focus = stats(clean, [], sc=scope("focus"))
    assert set(everything) == {"e4", "d4"} and set(focus) == {"e4"}
    _, focus_all = stats(clean, [], sc=scope("focus", time_class_focus="all"))
    assert set(focus_all) == {"e4", "d4"}  # `focus` is what the setting says it is
    _, black = stats(clean, [], "black")
    assert set(black) == {"Nf3"}


def test_the_months_boundary_is_counted_on_the_utc_calendar(clean: Conn) -> None:
    clean.execute("SET TIME ZONE 'Pacific/Kiritimati'")  # UTC+14: a local calendar would move the bound
    g = Games(clean)
    newest = NOW.replace(day=31, month=3, hour=0, minute=30)
    g.add(moves("Nf3 Nf6"), colour="white", at=newest)
    bound = newest.replace(month=2, day=28)  # a month back from 31 March, on the UTC calendar
    g.add(moves("e4 e5"), colour="white", at=bound)
    g.add(moves("d4 d5"), colour="white", at=bound - timedelta(seconds=1))
    _, rows = stats(clean, [], sc=scope("all", explorer_history_months=1))
    assert set(rows) == {"Nf3", "e4"}
    _, wider = stats(clean, [], sc=scope("all", explorer_history_months=2))
    assert set(wider) == {"Nf3", "e4", "d4"}
    _, review_setting = stats(clean, [], sc=scope("all", explorer_history_months=1, review_history_months=36))
    assert set(review_setting) == {"Nf3", "e4"}  # the explorer's own setting, not Review's


# --- the answer and its arrival --------------------------------------------------------------


def answer(conn: Conn, path: list[str], colour: str = "white", time_class: str = "all") -> dict[str, Any]:
    return explorer.position(conn, scope(time_class), colour, path)


def test_arrival_is_judged_on_the_path_last_move(rep: Conn) -> None:
    assert answer(rep, [])["arrival"] is None
    assert answer(rep, moves("e4"), "black")["arrival"] is None  # the opponent's move
    assert answer(rep, moves("e4 e5"))["arrival"] is None
    ruy = answer(rep, moves("e4 e5 Nf3 Nc6 f1b5"))  # the book move under another spelling
    assert ruy["arrival"] == {"off_book": False, "book_moves": ["Bc4", "Bb5"]}
    off = answer(rep, moves("e4 e5 Nf3 Nc6 d4"))  # outside every conflict group
    assert off["arrival"] == {"off_book": True, "book_moves": ["Bc4", "Bb5"]}
    assert answer(rep, moves("e4 c5 Nc3"))["arrival"] == {"off_book": True, "book_moves": ["Nf3"]}
    assert answer(rep, moves("c4 e5 Nc3"))["arrival"] == {"off_book": False, "book_moves": []}  # no line
    assert answer(rep, moves("d4 e5 e3"))["arrival"] == {"off_book": False, "book_moves": []}  # unreadable
    assert answer(rep, moves("d4 e6 Nf3"))["arrival"] == {"off_book": False, "book_moves": []}  # end of line
    deep = answer(rep, LONG[:32], "black")  # the player's move at line ply 31, past the prefix
    assert deep["arrival"] == {"off_book": False, "book_moves": ["Rc8"]}
    assert answer(rep, [*LONG[:31], "a6"], "black")["arrival"] == {"off_book": True, "book_moves": ["Rc8"]}


def test_the_answer_rows_and_the_prefix_boundary(rep: Conn) -> None:
    g = Games(rep)
    g.add(moves("e4 e5 Nf3 Nc6 Bb5"), colour="white", result="win")
    g.add(moves("e4 e5 Nf3 Nc6 d4"), colour="white", result="loss")
    g.add(moves("e4 e5 Nf3 Nc6 d4"), colour="white", result="loss")
    g.add(moves("e4 e5 Nf3 Nc6 Ba6"), colour="white", result="draw")  # as many games as Bb5, not the book's
    got = answer(rep, moves("e4 e5 Nf3 Nc6"))
    assert (got["ply"], got["player_to_move"], got["beyond_prefix"], got["lines_through"]) == (4, True, False, 3)
    assert [(r["san"], r["n"], r["in_repertoire"]) for r in got["moves"]] == [
        ("d4", 2, False),
        ("Bb5", 1, True),
        ("Ba6", 1, False),
        ("Bc4", 0, True),
    ]
    assert got["repertoire"]["status"] == "conflict"
    assert got["node"]["n"] == 4
    assert got["repertoire"]["moves"][1] == {
        "san": "Bb5",
        "book": "White book",
        "chapter": "Open games",
        "line_name": "Ruy",
        "line_id": 1,
        "line_ply": 4,
        "lines": 1,
    }
    at29 = answer(rep, LONG[:29], "black")
    assert (at29["beyond_prefix"], at29["node"] is not None) == (False, True)
    at31 = answer(rep, LONG[:31], "black")
    assert (at31["beyond_prefix"], at31["node"]) == (True, None)
    assert [(r["san"], r["n"], r["in_repertoire"]) for r in at31["moves"]] == [("Rc8", 0, True)]


def test_a_path_is_replayed_and_refused_when_not_legal() -> None:
    assert len(explorer.replay(SHUFFLE * 15)) == EXPLORER_MAX_PLIES
    for bad in (["e4", "--"], ["e4", "Z0"], ["e5"], ["e4", "0000"], [*SHUFFLE * 15, "Nf3"]):
        with pytest.raises(explorer.ExplorerPathError):
            explorer.replay(bad)


# --- the routes ------------------------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, rep: Conn) -> Generator[TestClient]:
    from api.main import create_app

    with TestClient(create_app()) as c:
        assert c.post("/login", json={"password": "correct horse"}).status_code == 200
        yield c


def test_routes_require_login(app_env: None) -> None:
    from api.main import create_app

    with TestClient(create_app()) as c:
        assert c.get("/explorer/position").status_code == 401
        assert c.get("/explorer/lines").status_code == 401


def test_position_route_bounds(client: TestClient) -> None:
    ok = client.get("/explorer/position", params={"colour": "white", "moves": "e4,e5,Nf3,Nc6"})
    assert ok.status_code == 200 and ok.json()["repertoire"]["status"] == "conflict"
    assert client.get("/explorer/position").json()["fen"] == chess.STARTING_FEN
    sixty = ",".join(SHUFFLE * 15)
    assert client.get("/explorer/position", params={"moves": sixty}).status_code == 200
    over = client.get("/explorer/position", params={"moves": sixty + ",Nf3"})
    assert (over.status_code, over.json()["detail"]) == (422, f"a path has at most {EXPLORER_MAX_PLIES} moves")
    for bad in ("e4,--", "e4,e4", "e4,,e5", "e4,0000"):
        assert client.get("/explorer/position", params={"moves": bad}).status_code == 422, bad
    long_token = client.get("/explorer/position", params={"moves": "e4,Nf3xxxxxxxx"})
    assert (long_token.status_code, long_token.json()["detail"]) == (422, "a move is 1 to 10 characters")
    too_long = client.get("/explorer/position", params={"moves": "a" * 701})
    assert too_long.status_code == 422 and too_long.json()["detail"][0]["type"] == "string_too_long"
    assert client.get("/explorer/position", params={"colour": "red"}).status_code == 422
    assert client.get("/explorer/position", params={"time_class": "blitz"}).status_code == 422
    at30 = client.get("/explorer/position", params={"colour": "black", "moves": ",".join(LONG[:OPENING_PREFIX_PLIES])})
    assert (at30.json()["beyond_prefix"], at30.json()["node"]) == (True, None)


def test_lines_route_pages(client: TestClient) -> None:
    got = client.get("/explorer/lines", params={"colour": "white", "moves": ""}).json()
    assert (got["total"], len(got["rows"]), got["offset"], got["page_size"]) == (9, 9, 0, EXPLORER_LINES_PAGE)
    assert client.get("/explorer/lines", params={"offset": 50}).json()["rows"] == []
    for offset in (-50, 25, 100_050):
        assert client.get("/explorer/lines", params={"offset": offset}).status_code == 422, offset
    assert client.get("/explorer/lines", params={"offset": 100_000}).status_code == 200
    assert client.get("/explorer/lines", params={"moves": "e4,--"}).status_code == 422


def test_the_setting_defaults_to_twelve_months_and_is_bounded() -> None:
    from pydantic import ValidationError

    assert Settings().explorer_history_months == 12
    assert Settings(explorer_history_months=36).explorer_history_months == 36
    for bad in (0, 37):
        with pytest.raises(ValidationError):
            Settings(explorer_history_months=bad)


def test_the_scope_is_the_explorer_settings_and_load_fields_refuses_unknown_names(conn: Conn) -> None:
    from core import settings

    assert explorer.load_scope(conn, "focus") == explorer.Scope(12, "focus", "rapid_plus")  # no row: defaults
    settings.save(conn, Settings(explorer_history_months=3, review_history_months=36, time_class_focus="all"))
    assert explorer.load_scope(conn, "all") == explorer.Scope(3, "all", "all")
    assert settings.load_fields(conn, "explorer_history_months") == {"explorer_history_months": 3}
    with pytest.raises(ValueError):
        settings.load_fields(conn, "no_such_setting")
    conn.execute("DELETE FROM settings")
    conn.commit()
