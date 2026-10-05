"""Review's position ranking: the numbers, the one ordered status decision, carry-down and the
breadcrumbs, as pure functions and over games stored with an opening prefix."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import (
    REVIEW_FIXED_MAX,
    REVIEW_LEAK_THRESHOLD,
    REVIEW_RANKED_MAX,
    REVIEW_SHRINK_GAMES,
)
from core.review import positions as p
from core.settings import Settings
from tests.review.position_helpers import (
    ITALIAN,
    NOW,
    QGD,
    QGD_TRANSPOSED,
    SCANDI,
    Games,
    key_of,
    nodes,
    scope,
)

T = REVIEW_LEAK_THRESHOLD


# --- the status decision -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lk", "ck", "recent", "n_eff", "status"),
    [
        (0.10, 0.10, 2, 40, p.NOT_REACHED),  # 1: stale and leaking
        (0.00, 0.10, 2, 40, p.NOT_REACHED),  # 1: a stale NEW leak is not costing points now
        (0.10, -0.05, 0, 40, p.NOT_REACHED),  # 1: stale beats looks fixed
        (0.00, 0.00, 2, 40, None),  # 2
        (0.10, 0.10, 3, 40, p.STILL_LEAKING),  # 3
        (0.00, 0.10, 3, 40, p.NEW_LEAK),  # 4
        (0.10, 0.00, 5, 15, p.LOOKS_FIXED),  # 5
        (0.10, -0.02, 4, 400, p.IMPROVING),  # 5 refused: four recent games, however large n_eff
        (0.10, -0.02, 5, 14.9, p.IMPROVING),  # 5 refused: too small an effective sample
        (0.10, 0.01, 20, 40, p.TOO_EARLY),  # 6
        (0.10, -0.01, 3, 40, p.IMPROVING),  # 7
        (0.01, 0.01, 20, 40, None),  # 8
        (T, T, 20, 40, None),  # the threshold itself is not a leak
    ],
)
def test_one_ordered_status(lk: float, ck: float, recent: int, n_eff: float, status: str | None) -> None:
    assert p.status_of(lk, ck, recent, n_eff) == status


def test_the_status_grid_never_contradicts_its_gates() -> None:
    values = [-0.05, 0.0, T, T + 0.001, 0.1]
    for lk, ck, recent, n_eff in itertools.product(values, values, range(0, 8), [5.0, 14.9, 15.0, 60.0]):
        status = p.status_of(lk, ck, recent, n_eff)
        section = p.SECTION_OF.get(status) if status else None
        if status is not None:
            assert lk > T or ck > T
        if recent < 3:
            assert status in (p.NOT_REACHED, None)
        if section == p.RANKED:
            assert ck > 0 and recent >= 3
        if status == p.LOOKS_FIXED:
            assert recent >= 5 and n_eff >= 15 and ck <= 0


# --- the numbers ----------------------------------------------------------------------------


def _row(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "player_color": "black",
        "key": 7,
        "n": 20,
        "sd": 2.0,
        "swd": 1.5,
        "sw": 10.0,
        "sww": 6.0,
        "ss": 8.0,
        "se": 10.0,
        "sws": 4.0,
        "swe": 5.5,
        "recent": 9,
        "edges": [["11", 12], ["-3", 5]],
        "buckets": [[0, 8, 0.8], [1, 7, 2.0], [11, 9, -0.9], [12, 30, 9.0]],
        "eval_cp": -40,
        "mate_in": None,
    }
    row.update(over)
    return row


def test_the_numbers_of_a_node() -> None:
    config = Settings()
    node = p.build_node(_row(), config)
    k = REVIEW_SHRINK_GAMES
    assert node.long_deficit == pytest.approx(2.0 / 20 * 20 / (20 + k))
    n_eff = 10.0**2 / 6.0
    assert node.n_eff == pytest.approx(n_eff)
    assert node.current_deficit == pytest.approx(0.15 * n_eff / (n_eff + k))
    half_life = config.review_recency_half_life_days
    assert node.leak_per_month == pytest.approx(node.current_deficit * 10.0 * 30 * math.log(2) / half_life)
    assert (node.score, node.expected) == (0.4, 0.5)
    assert (node.current_score, node.current_expected) == (0.4, 0.55)
    assert node.edges == [(11, 12), (-3, 5)]
    # One bucket per 30 days, oldest first; fewer than eight games is empty; past the history, ignored.
    assert len(node.trend) == config.review_history_months
    assert node.trend[-1] == 10.0 and node.trend[-2] is None and node.trend[0] == -10.0
    assert node.status == p.STILL_LEAKING
    es = p.es_at(node)
    assert es is not None and es > 50  # Black, and White-POV -40 favours Black
    assert len(p.build_node(_row(), Settings(review_history_months=36)).trend) == 36


def test_an_unevaluated_board_has_no_engine_word() -> None:
    assert p.es_at(p.build_node(_row(eval_cp=None), Settings())) is None
    assert p.es_at(p.build_node(_row(eval_cp=None, mate_in=-3), Settings())) == 100.0  # mated White: Black wins


# --- carry-down ------------------------------------------------------------------------------


def node(
    key: int,
    *,
    n: int = 40,
    leak: float = 1.0,
    status: str = p.STILL_LEAKING,
    edges: list[tuple[int, int]] | None = None,
    colour: str = "black",
    lk: float = 0.1,
) -> p.Node:
    return p.Node(
        colour=colour,
        key=key,
        n=n,
        long_deficit=lk,
        current_deficit=0.1,
        n_eff=30.0,
        leak_per_month=leak,
        recent=10,
        score=0.4,
        expected=0.5,
        current_score=0.4,
        current_expected=0.5,
        status=status,
        edges=edges or [],
        trend=[],
        eval_cp=None,
        mate_in=None,
    )


def graph(*ns: p.Node) -> dict[tuple[str, int], p.Node]:
    return {(x.colour, x.key): x for x in ns}


def test_a_forced_chain_collapses_onto_the_position_that_carries_it() -> None:
    g = graph(
        node(1, n=100, leak=3.0, edges=[(2, 95)]),
        node(2, n=95, leak=1.0, edges=[(3, 50), (4, 45)]),
        node(3, n=50, leak=0.3),
        node(4, n=45, leak=0.2),
    )
    assert p.walk(g[("black", 1)], g).key == 2  # forced
    # A child carrying at least 80% of the leak replaces its parent even when not forced.
    g = graph(node(1, n=100, leak=2.0, edges=[(2, 60), (3, 40)]), node(2, n=60, leak=1.7), node(3, n=40, leak=0.3))
    assert p.walk(g[("black", 1)], g).key == 2
    # A split parent stays, and its child is a card of its own.
    g = graph(node(1, n=100, leak=2.0, edges=[(2, 55), (3, 45)]), node(2, n=55, leak=1.0), node(3, n=45, leak=0.9))
    assert p.walk(g[("black", 1)], g).key == 1
    assert [c.key for c in p.select_cards(g)[p.RANKED]] == [1, 2, 3]


def test_the_walk_stops_below_the_floor_across_sections_and_on_a_revisit() -> None:
    # Ten games reach the parent, nine continue to one child, which is under the floor: not a node.
    g = graph(node(1, n=10, leak=1.0, edges=[(2, 9)]))
    assert p.walk(g[("black", 1)], g).key == 1
    # A qualifying dominant child in the other section.
    g = graph(node(1, n=50, leak=1.0, edges=[(2, 49)]), node(2, n=49, status=p.LOOKS_FIXED))
    assert p.walk(g[("black", 1)], g).key == 1
    # A cycle in the edge data: the walk stops on the revisit, from either start.
    g = graph(node(1, n=50, leak=1.0, edges=[(2, 49)]), node(2, n=49, leak=1.0, edges=[(1, 48)]))
    assert p.walk(g[("black", 1)], g).key == 2
    assert p.walk(g[("black", 2)], g).key == 1
    # The dominant child is the most frequent, ties to the lower key in signed order.
    assert p.dominant_child(node(1, edges=[(5, 10), (-5, 10), (3, 9)])) == (-5, 10)


def test_the_cap_applies_after_walks_that_end_on_one_node_are_merged() -> None:
    many = [node(k, n=40, leak=10.0 - k * 0.1) for k in range(1, REVIEW_RANKED_MAX + 1)]
    # One more start, the worst of all, whose walk is forced onto the last of them.
    many.append(node(100, n=41, leak=50.0, edges=[(REVIEW_RANKED_MAX, 40)]))
    cards = p.select_cards(graph(*many))[p.RANKED]
    assert len(cards) == REVIEW_RANKED_MAX
    assert {c.key for c in cards} == set(range(1, REVIEW_RANKED_MAX + 1))


def test_fixed_ranks_by_points_lost_and_has_its_own_cap() -> None:
    many = [node(k, n=20 + k, status=p.LOOKS_FIXED, leak=-1.0) for k in range(1, REVIEW_FIXED_MAX + 3)]
    cards = p.select_cards(graph(*many))[p.FIXED]
    assert [c.key for c in cards] == list(range(REVIEW_FIXED_MAX + 2, 2, -1))


def test_breadcrumbs_point_to_a_shorter_line_and_never_loop() -> None:
    a, b, c = node(1), node(2), node(3)
    # Transpositions: 1 sits earlier in 2's line and 2 earlier in 1's. Only the shorter line can be a parent.
    lines = {
        ("black", 1): {"line": ["x"] * 6, "line_keys": [9, 2, 8, 8, 8, 1]},
        ("black", 2): {"line": ["x"] * 4, "line_keys": [1, 9, 9, 2]},
        ("black", 3): {"line": ["x"] * 8, "line_keys": [9, 1, 9, 2, 9, 9, 9, 3]},
    }
    up = p.parents([a, b, c], lines)
    assert up == {("black", 1): 2, ("black", 3): 2}  # the deepest earlier card in 3's line is 2
    seen: set[int] = set()
    for start in (1, 2, 3):
        cur: int | None = start
        seen.clear()
        while cur is not None:
            assert cur not in seen
            seen.add(cur)
            cur = up.get(("black", cur))


# --- over stored games -----------------------------------------------------------------------


def test_the_elo_expectation_and_the_counts(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    g.anchor()
    for i in range(3):
        g.add(QGD, mine=1500, theirs=1700, result="draw", days=2 + i)
    g.add(QGD, mine=None, theirs=1500)  # no rating: left out and counted
    g.add(QGD, variant="chess960")  # history only: never counted
    g.add(QGD, prefix=False)  # no prefix recorded yet: counted as such
    sc = scope(results_min_games=3)
    got = nodes(clean, sc)[("black", key_of(clean, QGD))]
    expected = 1 / (1 + 10 ** (200 / 400))
    assert got.n == 3
    assert got.expected == pytest.approx(expected) and got.score == 0.5
    meta = p.meta(clean, sc)
    assert (meta["games_counted"], meta["games_without_ratings"], meta["games_without_prefix"]) == (4, 1, 1)


def test_a_repeated_board_counts_once(clean: psycopg.Connection[DictRow]) -> None:
    repeated = "Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8 e4".split()
    plain = "Nf3 Nf6 Ng1 Ng8 e4".split()
    sc = scope(results_min_games=3)

    def run(moves: Sequence[str]) -> dict[tuple[str, int], p.Node]:
        g = Games(clean)
        for i in range(10):
            g.add(moves, colour="white", result=("loss", "draw", "win")[i % 3], days=1 + 7 * i)
        got = nodes(clean, sc)
        clean.rollback()
        return got

    with_repeats, without = run(repeated), run(plain)
    assert with_repeats.keys() == without.keys()
    for k, a in with_repeats.items():
        b = without[k]
        assert (a.n, a.long_deficit, a.current_deficit, a.n_eff, a.status, a.trend, sorted(a.edges)) == (
            b.n,
            b.long_deficit,
            b.current_deficit,
            b.n_eff,
            b.status,
            b.trend,
            sorted(b.edges),
        )
        assert a.n == 10
    start = key_of(clean, [])
    assert all(start not in [dst for dst, _ in x.edges] for x in with_repeats.values())  # no edge back to the start
    assert ("white", start) not in with_repeats  # the start position is never a node
    for x in with_repeats.values():
        p.walk(x, with_repeats)  # finite


def test_two_move_orders_are_one_position(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    for i in range(4):
        g.add(QGD, days=1 + i)
        g.add(QGD_TRANSPOSED, days=1 + i)
    got = nodes(clean, scope(results_min_games=3))
    target = key_of(clean, QGD)
    assert key_of(clean, QGD_TRANSPOSED) == target
    assert got[("black", target)].n == 8
    into = {k[1] for k, x in got.items() if any(dst == target for dst, _ in x.edges)}
    assert into == {key_of(clean, QGD[:4]), key_of(clean, QGD_TRANSPOSED[:4])}


def test_as_of_is_the_newest_game_under_the_time_class(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    g.add(ITALIAN, colour="white", family="Italian Game", days=5)
    g.add(SCANDI, family="Scandinavian Defense", days=3)
    g.add(QGD, time_class="blitz", days=1)
    assert p.meta(clean, scope(time_class="focus"))["as_of"] == NOW - timedelta(days=3)
    # The opening filter never moves it: an opening he stopped playing does not look current.
    italian = scope(time_class="focus", opening="white:Italian Game")
    assert p.meta(clean, italian)["as_of"] == NOW - timedelta(days=3)
    assert p.meta(clean, scope(time_class="all"))["as_of"] == NOW - timedelta(days=1)


def _scandi_sample(g: Games, *, recent_losses: bool) -> None:
    """Thirty Scandinavian games: losses for six months; the last month losses or wins."""
    for i in range(24):
        g.add(SCANDI, family="Scandinavian Defense", days=31 + i * 7)
    for i in range(10):
        g.add(SCANDI, family="Scandinavian Defense", days=1 + i * 3, result="loss" if recent_losses else "win")


def test_bad_for_six_months_and_still_bad_is_ranked_first(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    _scandi_sample(g, recent_losses=True)
    for i in range(12):
        g.add(ITALIAN, colour="white", family="Italian Game", days=2 + i, result=("win", "loss")[i % 2])
    ranked = p.ranked_positions(clean, scope())
    assert ranked["ranked"][0]["status"] == p.STILL_LEAKING
    assert ranked["ranked"][0]["key"] == str(key_of(clean, SCANDI))  # the forced chain collapses onto its end
    assert ranked["ranked"][0]["line_san"] == SCANDI


def test_bad_for_five_months_then_good_with_enough_games_looks_fixed(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    _scandi_sample(g, recent_losses=False)
    ranked = p.ranked_positions(clean, scope())
    assert ranked["ranked"] == []
    assert [c["status"] for c in ranked["fixed"]] == [p.LOOKS_FIXED]
    card = ranked["fixed"][0]
    assert card["score"] < card["expected"] and card["current_score"] > card["current_expected"]


def test_a_position_no_longer_reached_leaves_ranked_exactly_when_it_goes_stale(
    clean: psycopg.Connection[DictRow],
) -> None:
    """Newer games in other openings move `as_of` on; the target gets none. It is in exactly one
    section at every step, and moves to Fixed? when its recent games drop below three."""

    g = Games(clean)
    for i in range(20):
        g.add(SCANDI, family="Scandinavian Defense", days=1 + i * 4)
    target = key_of(clean, SCANDI)
    seen: list[tuple[str, int]] = []
    for step in range(0, 40, 3):
        g.add(ITALIAN, colour="white", result="draw", at=NOW + timedelta(days=step))
        ranked = p.ranked_positions(clean, scope())
        sections = [s for s in (p.RANKED, p.FIXED) if any(c["key"] == str(target) for c in ranked[s])]
        assert len(sections) == 1
        recent = nodes(clean, scope())[("black", target)].recent
        assert (sections[0] == p.FIXED) == (recent < 3)
        seen.append((sections[0], recent))
    assert seen[0][0] == p.RANKED and seen[-1][0] == p.FIXED


def test_the_opening_filter_narrows_the_games(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    _scandi_sample(g, recent_losses=True)
    for i in range(12):
        g.add(ITALIAN, colour="white", family="Italian Game", days=2 + i)
    italian = p.ranked_positions(clean, scope(opening="white:Italian Game"))
    assert italian["ranked"] and all(c["colour"] == "white" for c in italian["ranked"])
    assert all(c["line_san"][:2] == ["e4", "e5"] for c in italian["ranked"])
    options = p.opening_options(clean, Settings(), "all")
    assert [o["key"] for o in options] == ["black:Scandinavian Defense", "white:Italian Game"]
    assert options[0]["label"] == "Scandinavian Defense · Black"


def test_the_history_and_the_ply_window(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    g.anchor()
    for i in range(5):
        g.add(QGD, days=2 + i)
        g.add(QGD, days=400 + i)  # older than twelve months
    sc = scope(Settings(review_position_max_ply=4), results_min_games=3)
    got = nodes(clean, sc)
    assert ("black", key_of(clean, QGD)) not in got  # ply 5, past the window
    assert got[("black", key_of(clean, QGD[:4]))].n == 5


def test_cards_carry_strings_for_keys(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    _scandi_sample(g, recent_losses=True)
    card = p.ranked_positions(clean, scope())["ranked"][0]
    assert isinstance(card["key"], str) and int(card["key"]) == key_of(clean, SCANDI)
    assert card["fen"] and card["last_move"] == "d5a5"
    assert len(card["trend"]) == Settings().review_history_months


def test_a_node_dataclass_is_what_a_card_serialises() -> None:
    n = replace(node(-(2**63)), trend=[None, 1.5])
    c = p.card(n, {"line": ["e4"], "line_keys": [1]}, 5)
    assert c["key"] == str(-(2**63)) and c["parent_key"] == "5" and c["line_san"] == ["e4"]


def test_the_carry_down_thresholds_are_inclusive() -> None:
    # Exactly 90% of the games reach the child, which carries little of the leak: forced.
    g = graph(node(1, n=100, leak=2.0, edges=[(2, 90), (3, 10)]), node(2, n=90, leak=0.1), node(3, n=10, leak=0.1))
    assert p.walk(g[("black", 1)], g).key == 2
    g = graph(node(1, n=100, leak=2.0, edges=[(2, 89), (3, 11)]), node(2, n=89, leak=0.1), node(3, n=11, leak=0.1))
    assert p.walk(g[("black", 1)], g).key == 1
    # Exactly 80% of the section score: carries it.
    g = graph(node(1, n=100, leak=2.0, edges=[(2, 50), (3, 50)]), node(2, n=50, leak=1.6), node(3, n=50, leak=0.1))
    assert p.walk(g[("black", 1)], g).key == 2
    g = graph(node(1, n=100, leak=2.0, edges=[(2, 50), (3, 50)]), node(2, n=50, leak=1.59), node(3, n=50, leak=0.1))
    assert p.walk(g[("black", 1)], g).key == 1


def test_the_cap_counts_cards_not_starting_points() -> None:
    """Sixteen leaking positions, plus the worst start of all, whose walk ends on the first of
    them. Every walk is taken and merged before the fifteen best cards are kept, whatever order
    the positions arrive in."""
    many = [node(k, n=40, leak=10.0 - k * 0.1) for k in range(REVIEW_RANKED_MAX + 1, 0, -1)]
    merging = node(100, n=41, leak=50.0, edges=[(1, 40)])
    cards = p.select_cards(graph(merging, *many))[p.RANKED]
    assert [c.key for c in cards] == list(range(1, REVIEW_RANKED_MAX + 1))


def test_a_breadcrumb_never_crosses_colours() -> None:
    black, white = node(1, colour="black"), node(2, colour="white")
    lines = {
        ("black", 1): {"line": ["x"] * 4, "line_keys": [9, 2, 9, 1]},
        ("white", 2): {"line": ["x"] * 2, "line_keys": [9, 2]},
    }
    assert p.parents([black, white], lines) == {}


def test_a_game_exactly_thirty_days_old_is_recent(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    g.anchor()
    for _ in range(3):
        g.add(QGD, days=30)
    assert nodes(clean, scope(results_min_games=3))[("black", key_of(clean, QGD))].recent == 3


def test_the_line_shown_is_the_most_frequent_then_the_most_recent(clean: psycopg.Connection[DictRow]) -> None:
    g = Games(clean)
    for days in (5, 6, 30):
        g.add(QGD_TRANSPOSED, days=days)
    for days in (1, 2):
        g.add(QGD, days=days)
    sc = scope(results_min_games=3)
    target = ("black", key_of(clean, QGD))
    assert p.line_rows(clean, sc, [target])[target]["line"] == QGD_TRANSPOSED  # three games against two
    g.add(QGD, days=3)  # a tie: three each, and the newest game took the other order
    assert p.line_rows(clean, sc, [target])[target]["line"] == QGD
