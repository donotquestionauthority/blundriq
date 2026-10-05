"""Review's opening mistakes: pricing, the decisions, the support, the status and its unknown
visits, and the section over games stored with an opening prefix and evaluations planted by
key."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core.chess.board import moves_to_fen_sequence
from core.constants import REVIEW_FIXED_RUN, REVIEW_RECENT_DAYS
from core.review import mistakes as m
from core.review.detect import position_es
from core.settings import Settings
from tests.review.position_helpers import Games, key_of, scope

K = 0.00368208  # the detector's win-probability constant


def cp_for(es: float) -> int:
    """The centipawns (White's view) at which White's expected score is `es`."""
    return round(-math.log(100.0 / es - 1.0) / K)


# --- pricing ----------------------------------------------------------------------------------


def test_a_terminal_board_takes_its_outcome_and_mate_zero_alone_is_unknown() -> None:
    # The player's move mates: the board after it has the opponent to move and checkmated.
    assert m.board_es(None, None, "checkmate", True, player_to_move=False) == 100.0
    assert m.board_es(None, None, "checkmate", False, player_to_move=True) == 0.0
    assert m.board_es(None, None, "draw", True, player_to_move=False) == 50.0
    assert m.board_es(None, 0, None, True, player_to_move=False) is None  # never read as 0 or 100
    assert m.board_es(None, None, None, True, player_to_move=True) is None
    assert m.board_es(120, None, None, False, player_to_move=True) == position_es(120, None, False)


def _row(b: tuple[Any, Any, Any], a: tuple[Any, Any, Any], colour: str = "white") -> dict[str, Any]:
    return {
        "player_color": colour,
        "b_cp": b[0],
        "b_mate": b[1],
        "b_terminal": b[2],
        "a_cp": a[0],
        "a_mate": a[1],
        "a_terminal": a[2],
    }


def test_a_mating_move_costs_nothing_and_stalemating_a_won_board_costs_the_win() -> None:
    assert m.decision_loss(_row((None, 2, None), (None, None, "checkmate"))) == 0.0
    won = position_es(800, None, True)
    assert won is not None
    assert m.decision_loss(_row((800, None, None), (None, None, "draw"))) == pytest.approx(won - 50)
    assert m.decision_loss(_row((800, None, None), (None, None, None))) is None  # unknown after-board
    # Black's view: a White score of +200 after Black's move is a loss for Black.
    assert m.decision_loss(_row((0, None, None), (200, None, None), "black")) == pytest.approx(
        50 - (position_es(200, None, False) or 0)
    )


# --- the status decision (pure) ---------------------------------------------------------------

C, F, U = m.COSTLY, m.FINE, m.UNKNOWN


def test_three_fine_visits_after_a_costly_one_are_fixed_and_two_are_not() -> None:
    assert REVIEW_FIXED_RUN == 3
    assert m.fixed_eligible([C, F, F, F]) and m.status_of([C, F, F, F], 0) == m.FIXED
    assert not m.fixed_eligible([C, F, F]) and m.status_of([C, F, F], 0) == m.STILL
    assert not m.fixed_eligible([F, F, F, F])  # nothing was ever costly: not "fixed"


def test_an_unknown_visit_is_never_evidence_that_the_board_is_fixed() -> None:
    before = [C, C, F, F, F]
    assert m.status_of([*before, U], 0) == m.AWAITING  # newest game waits for the engine
    assert not m.fixed_eligible([*before, U])
    assert m.status_of([*before, C], 0) == m.STILL  # filled as costly
    assert m.status_of([*before, F], 0) == m.FIXED  # filled as fine: four in a row
    assert m.status_of([C, F, U, F, F], 0) == m.STILL  # the unknown ends the run: two, not four
    assert not m.fixed_eligible([C, F, U, F, F])


def test_a_board_not_reached_lately_says_so_and_can_still_be_fixed() -> None:
    stale = REVIEW_RECENT_DAYS + 1
    assert m.status_of([C, F, U], stale) == m.NOT_REACHED  # staleness first, before an unknown newest visit
    assert m.status_of([C, F, F, F], stale) == m.NOT_REACHED and m.fixed_eligible([C, F, F, F])
    assert m.status_of([C, C], stale) == m.NOT_REACHED and not m.fixed_eligible([C, C])
    assert m.status_of([C, F, F, F], REVIEW_RECENT_DAYS) == m.FIXED  # exactly 30 days is recent


def _board(
    losses: Sequence[float | None], ages: Sequence[float] | None = None, games: Sequence[int] | None = None
) -> m.Board:
    b = m.Board("white", 1)
    for i, loss in enumerate(losses):
        b.visits.append(
            {
                "id": games[i] if games else i + 1,
                "loss": loss,
                "age": ages[i] if ages else 0.0,
                "played_at": None,
                "san": "e4",
                "a_terminal": None,
            }
        )
    return m.measure(b, floor=3.0, half_life=30.0, months=12)


def test_the_floor_is_subtracted_so_four_six_point_losses_charge_twelve() -> None:
    b = _board([6.0, 6.0, 6.0, 6.0])
    assert b.costly == 4 and b.per_month_12 * 100 * 12 == pytest.approx(12.0)
    assert b.per_month == pytest.approx(12.0 / 100 * 30 * math.log(2) / 30)
    just_under = _board([2.9, 2.9])
    assert just_under.costly == 0 and just_under.per_month == 0 and just_under.states == [F, F]
    assert _board([3.0]).states == [F]  # a loss equal to the floor is fine


def test_an_unknown_decision_counts_in_neither_the_cost_nor_the_sample() -> None:
    b = _board([10.0, None, 0.0], games=[1, 2, 3])
    assert b.known == 2 and b.games == 3 and b.states == [C, U, F]
    assert b.per_visit == pytest.approx(7.0 / 2 / 100)


def test_recent_decisions_weigh_more() -> None:
    old = _board([20.0, 0.0], ages=[90.0, 0.0])
    new = _board([20.0, 0.0], ages=[0.0, 90.0])
    assert new.per_month > old.per_month and new.per_month_12 == old.per_month_12
    w = 0.5 ** (90 / 30)
    assert old.per_visit == pytest.approx(w * 17.0 / (w + 1) / 100)  # weighted, not 17 / 2


# --- the section over stored games ------------------------------------------------------------

LINE: list[str] = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5", "d4", "Nf6"]  # The player is Black: plies 1, 3, 5, 7


def _plant(conn: psycopg.Connection[DictRow], moves: Sequence[str], white_es: float) -> int:
    """Store White's expected score for the board after `moves`."""
    key = key_of(conn, moves)
    conn.execute(
        "INSERT INTO position_evals (board_key, fen, eval_cp, best_move, depth) VALUES (%s, 'x', %s, 'Nf3', 18)"
        " ON CONFLICT (board_key) DO UPDATE SET eval_cp = EXCLUDED.eval_cp",
        (key, cp_for(white_es)),
    )
    return key


def _flat(conn: psycopg.Connection[DictRow], lines: Sequence[Sequence[str]]) -> None:
    """Every board of every line priced 50: every decision fine."""
    for line in lines:
        for p in range(len(line) + 1):
            _plant(conn, line[:p], 50.0)


def _section(conn: psycopg.Connection[DictRow], **over: Any) -> dict[str, Any]:
    conn.commit()
    return m.section(conn, scope(Settings(**over)))


def _ranked_keys(section: dict[str, Any]) -> list[str]:
    return [c["key"] for c in section["ranked"]]


def test_a_clean_opening_charges_nothing_however_the_game_ends(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    for day in range(1, 11):
        g.add(LINE, result="loss", days=day)
    _flat(clean, [LINE])
    got = _section(clean)
    assert got["ranked"] == [] and got["fixed"] == []
    assert got["coverage"] == {"decisions": 40, "covered": 40, "evaluated": 40, "eval_min_games": 3}


def _scandi_with_costly_qa5(conn: psycopg.Connection[DictRow], g: Games, costly: int, fine: int) -> int:
    """`costly` games play the costly 3...Qa5, `fine` games a fine 3...Qd8 instead."""
    qd8: list[str] = [*LINE[:5], "Qd8"]
    for i in range(costly):
        g.add(LINE, days=1 + i)
    for i in range(fine):
        g.add(qd8, days=20 + i)
    _flat(conn, [LINE, qd8])
    _plant(conn, LINE[:6], 70.0)  # White's view after 3...Qa5: Black gave 20 points away
    return key_of(conn, LINE[:5])


def test_the_same_error_in_three_of_five_games_ranks_and_three_of_three_does_not(
    clean: psycopg.Connection[DictRow],
) -> None:
    g = Games(clean)
    board = _scandi_with_costly_qa5(clean, g, costly=3, fine=0)
    assert _section(clean)["ranked"] == []  # three games: the floor is five
    for i in range(2):
        g.add([*LINE[:5], "Qd8"], days=30 + i)
    _flat(clean, [[*LINE[:5], "Qd8"]])
    _plant(clean, LINE[:6], 70.0)
    got = _section(clean)
    assert _ranked_keys(got) == [str(board)]
    card = got["ranked"][0]
    assert (card["games"], card["costly_games"], card["decisions"], card["evaluated"]) == (5, 3, 5, 5)
    assert card["status"] == m.STILL  # the newest visits are the costly ones
    assert card["line_san"] == LINE[:5] and card["best_move"] == "Nf3"
    assert [(r["san"], r["n"], r["costly"]) for r in card["moves"]] == [("Qa5", 3, 3), ("Qd8", 2, 0)]
    assert card["moves"][0]["mean_loss"] == pytest.approx(20.0, abs=0.2)


def test_one_slip_in_many_games_is_not_a_pattern(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    _scandi_with_costly_qa5(clean, g, costly=2, fine=18)
    assert _section(clean)["ranked"] == []  # costly in two games: the floor is three
    assert _ranked_keys(_section(clean, review_min_costly_games=2)) != []


def test_the_section_ranks_only_the_opening_and_a_later_error_lands_nowhere(clean: psycopg.Connection[DictRow]) -> None:
    """A 20-point error at ply 5 is charged at its board; with the ranked depth at 4 it is not a
    decision at all, and nothing else is charged in its place."""
    g = Games(clean)
    board = _scandi_with_costly_qa5(clean, g, costly=5, fine=0)
    assert _ranked_keys(_section(clean)) == [str(board)]
    shallow = _section(clean, review_position_max_ply=4)
    assert shallow["ranked"] == [] and shallow["coverage"]["decisions"] == 10


def test_a_repeated_move_is_one_decision_and_another_move_on_the_return_is_two(
    clean: psycopg.Connection[DictRow],
) -> None:
    g = Games(clean)
    shuffle: list[str] = ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1", "Ng8", "e4"]
    for day in range(1, 6):
        g.add(shuffle, colour="white", days=day)
    _flat(clean, [shuffle])
    start = key_of(clean, [])
    rows = m.decision_rows(clean, scope(), min_games=1, colour="white", key=start)
    # Nf3 from the start at plies 0 and 4 is one decision per game; e4 at ply 8 is another.
    assert sorted((r["id"], r["san"]) for r in rows) == sorted(
        [(gid, san) for gid in range(1, 6) for san in ("Nf3", "e4")]
    )
    assert {(r["san"], r["ply"]) for r in rows} == {("Nf3", 0), ("e4", 8)}  # the first visit's ply


def test_a_short_game_stops_where_it_ends(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    g.add(LINE[:3], days=1)  # The player moved once (1...d5), and the game ended after 2.exd5
    _flat(clean, [LINE[:3]])
    assert _section(clean)["coverage"]["decisions"] == 1


def test_an_unevaluated_board_is_unknown_on_the_card_and_in_the_coverage(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    board = _scandi_with_costly_qa5(clean, g, costly=5, fine=1)
    newest = g.add([*LINE[:5], "Bd7"], days=0.5)  # the newest visit: a move whose board is not evaluated
    got = _section(clean)
    card = got["ranked"][0]
    assert card["key"] == str(board) and card["status"] == m.AWAITING
    assert (card["decisions"], card["evaluated"]) == (7, 6) and card["strip"][-1] == m.UNKNOWN
    cov = got["coverage"]
    assert cov["covered"] == cov["decisions"] and cov["evaluated"] == cov["decisions"] - 1
    assert newest
    # The engine reaches it: costly, and the board is still costing; the strip's last visit is costly.
    _plant(clean, [*LINE[:5], "Bd7"], 80.0)
    card = _section(clean)["ranked"][0]
    assert card["status"] == m.STILL and card["strip"][-1] == m.COSTLY


def test_fixed_holds_a_board_not_reached_lately_with_that_chip(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    qd8: list[str] = [*LINE[:5], "Qd8"]
    for i in range(3):
        g.add(LINE, days=100 + i)  # costly, long ago
    for i in range(3):
        g.add(qd8, days=60 + i)  # then fine three times, also more than 30 days back
    g.anchor(days=0)
    _flat(clean, [LINE, qd8, ["Nf3", "Nf6", "g3", "g6"]])
    _plant(clean, LINE[:6], 70.0)
    got = _section(clean)
    assert got["ranked"] == []
    assert [(c["key"], c["status"], c["fixed"]) for c in got["fixed"]] == [
        (str(key_of(clean, LINE[:5])), m.NOT_REACHED, True)
    ]


def test_a_breadcrumb_points_to_the_ranked_board_earlier_on_the_line(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    for i in range(6):
        g.add(LINE, days=1 + i)
    _flat(clean, [LINE])
    for p in (2, 3, 4, 5):
        _plant(clean, LINE[:p], 70.0)  # 1...d5 gives 20 away; 2...Qxd5 keeps it at 70
    for p in (6, 7, 8):
        _plant(clean, LINE[:p], 90.0)  # 3...Qa5 gives another 20 away
    got = _section(clean)
    cards = {c["key"]: c for c in got["ranked"]}
    first, later = str(key_of(clean, LINE[:1])), str(key_of(clean, LINE[:5]))
    assert set(cards) == {first, later}
    assert cards[later]["parent_key"] == first and cards[first]["parent_key"] is None


def test_the_position_page_carries_the_players_moves(clean: psycopg.Connection[DictRow]) -> None:
    from core.review import position as pp

    g = Games(clean)
    _scandi_with_costly_qa5(clean, g, costly=3, fine=2)
    clean.commit()
    page = pp.position_page(clean, scope(), "black", key_of(clean, LINE[:5]), 1)
    assert page is not None
    detail = page["mistake"]
    assert detail["ranked"] is True and detail["best_move"] == "Nf3"
    assert [(r["san"], r["n"], r["costly"]) for r in detail["moves"]] == [("Qa5", 3, 3), ("Qd8", 2, 0)]
    # The player is not to move after 3...Qa5: no mistakes block there.
    after = pp.position_page(clean, scope(results_min_games=3), "black", key_of(clean, LINE[:6]), 1)
    assert after is not None and after["mistake"] is None


def test_the_page_carries_the_section(clean: psycopg.Connection[DictRow]) -> None:
    from core.review import read

    g = Games(clean)
    board = _scandi_with_costly_qa5(clean, g, costly=5, fine=0)
    clean.commit()
    page = read.page(clean, Settings(), time_class="all")
    assert [c["key"] for c in page["mistakes"]["ranked"]] == [str(board)]
    assert set(page["mistakes"]) == {"ranked", "fixed", "coverage"}


def test_fens_and_lines_replay(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    _scandi_with_costly_qa5(clean, g, costly=5, fine=0)
    card = _section(clean)["ranked"][0]
    assert card["fen"] == moves_to_fen_sequence(LINE[:5])[5]
    assert card["last_move"] == "b1c3"


def test_the_section_follows_the_time_class_and_the_opening(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    qd8: list[str] = [*LINE[:5], "Qd8"]
    for i in range(5):
        g.add(LINE, days=1 + i, time_class="blitz", family="Scandinavian Defense")
        g.add(qd8, days=1 + i, time_class="rapid", family="Scandinavian Defense")
    _flat(clean, [LINE, qd8])
    _plant(clean, LINE[:6], 70.0)
    clean.commit()
    board = str(key_of(clean, LINE[:5]))

    def ranked(time_class: str, opening: str = "__all__") -> list[str]:
        return [c["key"] for c in m.section(clean, scope(time_class=time_class, opening=opening))["ranked"]]

    assert ranked("all") == [board]
    assert ranked("focus") == []  # the costly games are blitz; the rapid ones played a fine move
    assert ranked("all", "black:Scandinavian Defense") == [board]
    assert ranked("all", "white:Scandinavian Defense") == []


def test_fixed_is_ordered_by_what_the_board_cost_over_the_history(clean: psycopg.Connection[DictRow]) -> None:
    """A board that cost a lot long ago comes before one that cost a little lately: Fixed? is
    ranked by the twelve months, not by the current rate."""
    g = Games(clean)
    old_line: list[str] = ["e4", "e5", "Nf3", "Nc6"]  # The player is Black: the costly move is 1...e5
    new_line: list[str] = ["d4", "d5", "c4", "e6"]  # and here 1...d5
    for i in range(3):
        g.add(old_line, days=200 + i)
        g.add(new_line, days=40 + i)
    for i in range(3):
        g.add(["e4", "c5"], days=5 + i)  # then fine from the same board
        g.add(["d4", "Nf6"], days=5 + i)
    _flat(clean, [old_line, new_line, ["e4", "c5"], ["d4", "Nf6"]])
    _plant(clean, old_line[:2], 90.0)  # 40 points given away
    _plant(clean, new_line[:2], 60.0)  # 10 points given away
    fixed = _section(clean)["fixed"]
    assert [c["key"] for c in fixed] == [str(key_of(clean, ["e4"])), str(key_of(clean, ["d4"]))]
    assert fixed[0]["per_month"] < fixed[1]["per_month"]


def test_every_game_of_every_move_is_reachable_a_page_at_a_time(clean: psycopg.Connection[DictRow]) -> None:
    """55 costly games (two still with their moves), an older fine alternative and a move the
    engine has not checked: the costly list is complete over two pages, newest first, and each
    move opens all of its games, fine and unknown included, at the ply played."""
    g = Games(clean)
    qd8: list[str] = [*LINE[:5], "Qd8"]
    bd7: list[str] = [*LINE[:5], "Bd7"]
    for i in range(55):
        g.add(LINE, days=1 + i, keep_moves=i < 2)
    for i in range(3):
        g.add(qd8, days=200 + i)
    g.add(bd7, days=300)
    _flat(clean, [LINE, qd8])
    _plant(clean, LINE[:6], 70.0)
    clean.commit()
    board = key_of(clean, LINE[:5])
    sc = scope()

    first = m.move_games(clean, sc, "black", board, move=None, page=1)
    second = m.move_games(clean, sc, "black", board, move=None, page=2)
    assert first is not None and second is not None
    assert (first["total"], first["total_pages"], len(first["rows"]), len(second["rows"])) == (55, 2, 50, 5)
    rows = first["rows"] + second["rows"]
    assert len({r["chess_game_id"] for r in rows}) == 55 and {r["state"] for r in rows} == {m.COSTLY}
    assert [r["played_at"] for r in rows] == sorted((r["played_at"] for r in rows), reverse=True)
    assert [r["has_moves"] for r in rows[:3]] == [True, True, False]
    assert {(r["san"], r["ply"]) for r in rows} == {("Qa5", 5)}

    fine = m.move_games(clean, sc, "black", board, move="Qd8", page=1)
    assert fine is not None and fine["total"] == 3
    assert {(r["state"], r["ply"], r["has_moves"]) for r in fine["rows"]} == {(m.FINE, 5, False)}
    unknown = m.move_games(clean, sc, "black", board, move="Bd7", page=1)
    assert unknown is not None and [(r["state"], r["loss"]) for r in unknown["rows"]] == [(m.UNKNOWN, None)]
    nothing = m.move_games(clean, sc, "black", board, move="Nf6", page=1)
    assert nothing is not None and nothing["total"] == 0 and nothing["rows"] == []
    assert m.move_games(clean, sc, "white", board, move=None, page=1) is None


def test_a_moves_games_follow_the_filters(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    qd8: list[str] = [*LINE[:5], "Qd8"]
    g.add(qd8, days=1, time_class="blitz")
    g.add(qd8, days=2, time_class="rapid")
    _flat(clean, [qd8])
    clean.commit()
    board = key_of(clean, LINE[:5])
    every = m.move_games(clean, scope(time_class="all"), "black", board, move="Qd8", page=1)
    focus = m.move_games(clean, scope(time_class="focus"), "black", board, move="Qd8", page=1)
    assert every is not None and focus is not None
    assert (every["total"], focus["total"]) == (2, 1)
