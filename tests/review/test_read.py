"""The worklist read layer: the pure derivations over in-memory events, and the page, drill-down and
stamp over the scratch database."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.review import read
from core.settings import Settings
from tests import repertoire_helpers as h

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
KNOBS = {"pool_floor_line": 5, "pool_floor_eco": 8, "half_life": 200, "faded_peak_es": 62}


def ev(gid: int, ply: int = 10, **over: Any) -> read.Event:
    e: read.Event = {
        "chess_game_id": gid,
        "anchor_ply": ply,
        "base_route": "lapse_defense",
        "opening_candidate": False,
        "pool_key": None,
        "evidence": {},
        "cost": 10.0,
        "phase": "middlegame",
        "piece_label": "knight",
        "book_relation": None,
        "board_key": 1000 + gid,
        "played_at": NOW - timedelta(days=gid),
        "time_class": "rapid",
        "termination": "resignation",
        "url": f"https://example.test/{gid}",
        "opening_name": "Scandinavian: Main",
        "canonical_family": "Scandinavian",
        "canonical_variation": "Main Line",
        "result": "loss",
        "opponent_username": f"opp{gid}",
        "reviewed_at": None,
        "recency_rank": gid,
    }
    e.update(over)
    return e


# --- Layer 2 ----------------------------------------------------------------------------------


def test_floors_per_key_type_and_candidates_only() -> None:
    line = [ev(i, opening_candidate=True, pool_key="line:1") for i in range(5)]
    eco = [ev(10 + i, opening_candidate=True, pool_key="eco:B01") for i in range(7)]
    # A late material event on the line key neither lifts the pool nor displays as opening.
    late = ev(99, ply=60, opening_candidate=False, pool_key="line:1")
    events = line + eco + [late]
    read.derive_display(events, KNOBS)
    assert all(e["displayed_route"] == "opening" for e in line)  # 5 >= line floor 5
    assert all(e["displayed_route"] == "lapse_defense" for e in eco)  # 7 < eco floor 8
    assert late["displayed_route"] == "lapse_defense"
    four = [ev(i, opening_candidate=True, pool_key="line:2") for i in range(4)]
    read.derive_display(four, KNOBS)
    assert all(e["displayed_route"] == "lapse_defense" for e in four)


def test_a_focused_family_waives_the_floor_without_touching_the_base_set() -> None:
    thin = [ev(i, opening_candidate=True, pool_key="line:9") for i in range(2)]
    other = [ev(50, canonical_family="Italian", opening_candidate=True, pool_key="line:5")]
    events = thin + other
    read.derive_display(events, KNOBS)
    assert {e["displayed_route"] for e in events} == {"lapse_defense"}
    focused = [dict(e) for e in read.apply_opening_filter(events, "Scandinavian")]
    read.derive_display(focused, KNOBS, floor_override=1)
    assert [e["chess_game_id"] for e in focused] == [0, 1]
    assert all(e["displayed_route"] == "opening" for e in focused)
    assert all(e["displayed_route"] == "lapse_defense" for e in events)  # the base set is untouched
    assert [e["chess_game_id"] for e in read.apply_opening_filter(events, read.OPENING_UNCLASSIFIED)] == []


# --- Representatives and rotation ---------------------------------------------------------------


def test_rotation_alternates_games_not_anchors() -> None:
    # Game 1 owns the two costliest anchors; the runner-up under the cooldown is a different game.
    members = [ev(1, ply=10, cost=50), ev(1, ply=20, cost=40), ev(2, ply=10, cost=30), ev(3, ply=10, cost=20)]
    rep = read.pick_representative(members, 200, None, NOW)
    assert (rep["chess_game_id"], rep["anchor_ply"]) == (1, 10)
    rep = read.pick_representative(members, 200, NOW - timedelta(hours=1), NOW)
    assert rep["chess_game_id"] == 2
    rep = read.pick_representative(members, 200, NOW - timedelta(hours=25), NOW)
    assert rep["chess_game_id"] == 1
    # Un-reviewed games are preferred; once every game is reviewed the ranking is over all.
    members[0]["reviewed_at"] = members[1]["reviewed_at"] = NOW
    assert read.pick_representative(members, 200, None, NOW)["chess_game_id"] == 2
    for e in members:
        e["reviewed_at"] = NOW
    assert read.pick_representative(members, 200, None, NOW)["chess_game_id"] == 1
    # A single eligible game is served whatever the stamp says.
    assert read.pick_representative(members[:2], 200, NOW, NOW)["chess_game_id"] == 1


def test_collapse_counts_extra_anchors_and_recency_weights_the_score() -> None:
    members = [
        ev(1, ply=10, cost=10, recency_rank=0),
        ev(1, ply=30, cost=10, recency_rank=0),
        ev(2, cost=10, recency_rank=200),
    ]
    collapsed = {g["game_id"]: g for g in read.collapse_to_games(members, 200)}
    assert collapsed[1]["extra"] == 1 and collapsed[2]["extra"] == 0
    assert collapsed[1]["score"] == 10.0 and collapsed[2]["score"] == 5.0
    assert collapsed[1]["rep"]["anchor_ply"] == 10  # equal scores: the earlier ply, the old key


# --- Lost wins ----------------------------------------------------------------------------------


def test_lost_wins_qualification() -> None:
    events = [
        ev(1, base_route="faded", result="loss"),
        ev(2, evidence={"game_peak_es": 70}, result="draw"),
        ev(3, evidence={"game_peak_es": 70}, result="win"),  # won: not lost
        ev(4, base_route="faded", result="loss", termination="timeout"),  # clock-decided: out
        ev(5, evidence={"game_peak_es": 61}, result="loss"),  # below the faded peak
        ev(6, base_route="lapse_offense", result="loss"),  # a missed win alone was never winning
        ev(7, evidence={"game_peak_es": "not a number"}, result="loss"),
    ]
    assert read._lost_win_game_ids(events, KNOBS) == {1, 2}
    events[0]["reviewed_at"] = NOW
    rows = read.select_lost_wins(events, KNOBS)
    assert [(g["game_id"], g["reviewed"]) for g in rows] == [(2, False), (1, True)]


# --- Pool ids -----------------------------------------------------------------------------------


def test_pool_id_grammar() -> None:
    assert read.parse_pool_id("v1:route:lapse_defense:knight") == ("route", "lapse_defense", "knight")
    assert read.parse_pool_id("v1:route:faded") == ("route", "faded", None)
    assert read.parse_pool_id("faded") == ("route", "faded", None)
    assert read.parse_pool_id("v1:fam:" + "a" * 16) == ("family", "a" * 16, None)
    assert read.parse_pool_id(f"v1:fam:{'a' * 16}:rep:{'b' * 16}") == ("subgroup", "a" * 16, ("rep", "b" * 16))
    for bad in (
        "v1:route:faded:extra",  # a single pool takes no sub key
        "v1:route:lapse_defense",  # a sub-pooled route needs one
        "lapse_offense",
        "v1:route:opening",
        "v1:fam:",
        "v1:fam:x:zzz:y",
        "v1:fam:x:var",
        "lapse_defense:knight",
        "v2:route:faded",
        "",
        "x" * 257,
    ):
        with pytest.raises(read.PoolNotFound):
            read.parse_pool_id(bad)
    assert read.canonical_pool_id(read.parse_pool_id("faded")) == "v1:route:faded"
    assert read.canonical_pool_id(("subgroup", "a", ("pos", "b"))) == "v1:fam:a:pos:b"


def test_digest_ids_never_carry_labels() -> None:
    fid = read.family_id("Scandinavian")
    assert fid.startswith("v1:fam:") and len(fid) == len("v1:fam:") + 16
    assert read.family_id(None) != read.family_id("None")
    assert read.subgroup_id(fid, "var", "Main Line") != read.subgroup_id(fid, "pos", "Main Line")


# --- Over the database ----------------------------------------------------------------------


def game(
    conn: psycopg.Connection[DictRow],
    gid: int,
    *,
    days_ago: float = 1,
    time_class: str = "rapid",
    variant: str = "standard",
    family: str | None = "Scandinavian",
    variation: str | None = "Main Line",
    result: str = "loss",
    termination: str = "resignation",
    reviewed: bool = False,
) -> None:
    h.player(conn)
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, variant, starting_fen, time_class,"
        " opening_name, canonical_family, canonical_variation, termination)"
        " VALUES (%s, 'lichess', %s, %s, now() - make_interval(secs => %s), %s, %s, %s, 'Scandi: Main', %s, %s, %s)",
        (
            gid,
            f"g{gid}",
            f"https://example.test/{gid}",
            days_ago * 86400,
            variant,
            "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1" if variant == "chess960" else None,
            time_class,
            family,
            variation,
            termination,
        ),
    )
    conn.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, result, opponent_username,"
        " analyzed_at_depth, reviewed_at) VALUES (%s, %s, 'white', 'lichess', %s, %s, 18, %s)",
        (PLAYER_ID, gid, result, f"opp{gid}", datetime.now(UTC) if reviewed else None),
    )


def plant(
    conn: psycopg.Connection[DictRow],
    gid: int,
    ply: int,
    *,
    route: str = "lapse_defense",
    candidate: bool = False,
    pool_key: str | None = None,
    cost: float = 10,
    piece: str | None = "knight",
    evidence: dict[str, Any] | None = None,
    book_relation: str | None = None,
    board_key: int = 7,
) -> None:
    conn.execute(
        "INSERT INTO review_events (player_id, chess_game_id, anchor_ply, config_version, base_route, opening_candidate,"
        " pool_key, evidence, cost, phase, piece_label, book_relation, board_key, first_detected_at)"
        " VALUES (%s, %s, %s, 2, %s, %s, %s, %s::jsonb, %s, 'opening', %s, %s, %s, now())",
        (
            PLAYER_ID,
            gid,
            ply,
            route,
            candidate,
            pool_key,
            json.dumps(evidence or {}),
            cost,
            piece,
            book_relation,
            board_key,
        ),
    )


@pytest.fixture()
def corpus(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """Six Scandinavian games with a qualifying line pool (5 candidates, one game reviewed), one
    of them also hanging a rook late; an Italian game with two thin candidates; a blitz game;
    a won endgame-only game; a faded loss; and a Chess960 game carrying a planted row."""
    conn = clean
    for gid in range(1, 6):
        game(conn, gid, days_ago=gid, reviewed=gid == 5)
        plant(conn, gid, 8, candidate=True, pool_key="line:1", cost=20 - gid, book_relation="deviation_before")
    plant(conn, 1, 40, piece="rook", cost=30)
    game(conn, 6, days_ago=6, family="Italian", variation="Giuoco")
    plant(conn, 6, 6, candidate=True, pool_key="line:2", cost=5, board_key=8)
    plant(conn, 6, 10, candidate=True, pool_key="line:2", cost=4, board_key=9)
    game(conn, 7, days_ago=7, time_class="blitz", family=None, variation=None)
    plant(conn, 7, 12, candidate=True, pool_key="eco:B01", cost=9)
    game(conn, 8, days_ago=8, result="win")
    plant(conn, 8, 70, route="endgame_technique", cost=12, piece=None)
    game(conn, 9, days_ago=9, result="loss")
    plant(conn, 9, 50, route="faded", cost=15, piece=None, evidence={"game_peak_es": 80})
    game(conn, 10, days_ago=10, variant="chess960")
    plant(conn, 10, 8, candidate=True, pool_key="line:1", cost=99, board_key=5)
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, classification, best_move, best_line)"
        " VALUES (%s, 1, 8, 'x', 'blunder', 'Nf3', 'Nf3 e6 Bd3')",
        (PLAYER_ID,),
    )
    return conn


def test_page_over_the_corpus(corpus: psycopg.Connection[DictRow]) -> None:
    p = read.page(corpus, Settings(), now=NOW)
    cats = p["categories"]
    assert p["page"] == {"total_games": 8, "to_review_games": 7}  # the blitz and Chess960 games are out
    assert p["filter"]["time_class"] == "focus"
    assert [o["key"] for o in p["filter"]["openings"]] == ["__all__", "Scandinavian", "Italian"]
    assert p["filter"]["openings"][1]["to_review_games"] == 4

    (fam,) = cats["opening"]["families"]
    assert fam["label"] == "Scandinavian" and fam["event_count"] == 5
    assert (fam["total_games"], fam["to_review_games"]) == (5, 4)
    assert cats["opening"]["total_games"] == 5
    (sub,) = fam["subgroups"]
    assert sub["label"] == "Main Line" and sub["kind"] == "variation"
    assert sub["book_relation_verdict"] == "deviation_before"
    rep = sub["representative_game"]
    assert rep["chess_game_id"] == 1 and rep["best_move"] == "Nf3" and rep["best_line"] == "Nf3 e6 Bd3"
    assert rep["expected_move"] is None and rep["displayed_route"] == "opening"
    assert 0 < sub["severity"] < sub["raw_severity"]

    # The Italian pair (2 < 5) and the late rook stay in Tactical oversights, by piece.
    defense = {p["label"]: p for p in cats["oversights"]["defense"]["pools"]}
    assert set(defense) == {"knight", "rook"}
    assert defense["knight"]["total_games"] == 1 and defense["knight"]["event_count"] == 2
    assert defense["rook"]["representative_game"]["anchor_ply"] == 40
    assert defense["rook"]["book_relation_verdict"] is None and defense["rook"]["pool_key"] is None
    assert cats["oversights"]["total_games"] == 2 and cats["oversights"]["offense"]["pools"] == []

    # A won endgame-only game reaches the Endgame pool and the page's counts.
    (eg,) = cats["endgame"]["pools"]
    assert eg["pool_id"] == "v1:route:endgame_technique" and eg["representative_game"]["chess_game_id"] == 8
    assert cats["endgame"]["total_games"] == 1
    (fd,) = cats["faded"]["pools"]
    assert fd["pool_id"] == "v1:route:faded"
    assert [g["chess_game_id"] for g in cats["lost_wins"]["games"]] == [9]  # game 8 was won
    assert cats["lost_wins"]["to_review_games"] == 1

    # Nothing about the displayed route reached the table.
    cols = {
        r["column_name"]
        for r in corpus.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'review_events'")
    }
    assert "displayed_route" not in cols


def test_all_time_classes_and_a_focused_opening(corpus: psycopg.Connection[DictRow]) -> None:
    p = read.page(corpus, Settings(), time_class="all", now=NOW)
    assert p["page"]["total_games"] == 9
    assert [o["key"] for o in p["filter"]["openings"]] == ["__all__", "Scandinavian", "Italian", "__unclassified__"]
    # The eco candidate is one short of nothing: alone it never clears 8, so it is a defense event.
    assert p["categories"]["oversights"]["total_games"] == 3

    p = read.page(corpus, Settings(), opening="Italian", group_by="position", now=NOW)
    (fam,) = p["categories"]["opening"]["families"]
    assert fam["label"] == "Italian" and fam["event_count"] == 2 and fam["confidence"] == "low"
    assert [s["kind"] for s in fam["subgroups"]] == ["position", "position"]
    assert {s["label"] for s in fam["subgroups"]} == {"Scandi: Main"}
    # Only Opening problems changed: the base categories and the page totals are untouched.
    assert p["categories"]["oversights"]["total_games"] == 2
    assert p["page"]["total_games"] == 8
    with pytest.raises(read.ReviewParamError, match="unknown opening key"):
        read.page(corpus, Settings(), opening="Sicilian", now=NOW)
    with pytest.raises(read.ReviewParamError):
        read.page(corpus, Settings(), group_by="colour", now=NOW)


def test_group_by_repertoire_reads_the_deepest_line(corpus: psycopg.Connection[DictRow]) -> None:
    conn = corpus
    h.book(conn, 1, "Scandi", "black")
    h.chapter(conn, 1, 1, "Main")
    conn.execute(
        "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves) VALUES (1, 1, 'Qa5', '[\"e4\"]'::jsonb), (2, 1, 'Qd8', '[\"d4\"]'::jsonb)"
    )
    for gid, lines in ((1, [(1, 4), (2, 6)]), (2, [(1, 4)])):
        row = conn.execute(
            "INSERT INTO game_repertoire_results (player_id, chess_game_id, book_id, chapter_id, expected_move, deviated_at_ply)"
            " VALUES (%s, %s, 1, 1, 'c4', 9) RETURNING id",
            (PLAYER_ID, gid),
        ).fetchone()
        assert row
        for line_id, ply in lines:
            conn.execute(
                "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply) VALUES (%s, %s, %s)",
                (row["id"], line_id, ply),
            )
    p = read.page(conn, Settings(), group_by="repertoire", now=NOW)
    (fam,) = p["categories"]["opening"]["families"]
    by_label = {s["label"]: s for s in fam["subgroups"]}
    assert set(by_label) == {"Main — Qd8", "Main — Qa5", "Not in your repertoire"}
    assert by_label["Main — Qd8"]["representative_game"]["chess_game_id"] == 1  # the deepest match wins
    assert by_label["Main — Qd8"]["representative_game"]["expected_move"] == "c4"
    assert by_label["Not in your repertoire"]["total_games"] == 3
    # A matched line without a name is retired, not unprepared (the game's pool key proves the match).
    conn.execute("UPDATE repertoire_lines SET line_name = '' WHERE id = 2")
    p = read.page(conn, Settings(), group_by="repertoire", now=NOW)
    (fam,) = p["categories"]["opening"]["families"]
    assert "Retired line" in {s["label"] for s in fam["subgroups"]}


def test_drill_down_reconciles_with_the_page_and_paginates(corpus: psycopg.Connection[DictRow]) -> None:
    p = read.page(corpus, Settings(), now=NOW)
    sub = p["categories"]["opening"]["families"][0]["subgroups"][0]
    d = read.pool_events(corpus, Settings(), sub["subgroup_id"])
    assert d["total"] == sub["to_review_games"] == 4
    assert [r["chess_game_id"] for r in d["rows"]] == [1, 2, 3, 4]
    assert d["rows"][0]["best_move"] == "Nf3" and d["rows"][0]["extra_in_game"] == 0
    d = read.pool_events(corpus, Settings(), sub["subgroup_id"], reviewed_scope="all", limit=2, offset=2)
    assert d["total"] == sub["total_games"] == 5
    assert [(r["chess_game_id"], r["reviewed"]) for r in d["rows"]] == [(3, False), (4, False)]
    d = read.pool_events(corpus, Settings(), sub["subgroup_id"], reviewed_scope="all", limit=2, offset=4)
    assert [(r["chess_game_id"], r["reviewed"]) for r in d["rows"]] == [(5, True)]
    # The family id itself drills to the same games; a route pool ignores the opening entirely.
    fam = p["categories"]["opening"]["families"][0]
    assert read.pool_events(corpus, Settings(), fam["family_id"])["total"] == 4
    knight = read.pool_events(corpus, Settings(), "v1:route:lapse_defense:knight", opening="Sicilian")
    assert [r["chess_game_id"] for r in knight["rows"]] == [6] and knight["rows"][0]["extra_in_game"] == 1
    assert "best_move" not in knight["rows"][0]
    # Under a focus the thin Italian line drills at the waived floor and its members show as opening.
    p = read.page(corpus, Settings(), opening="Italian", now=NOW)
    fam = p["categories"]["opening"]["families"][0]
    d = read.pool_events(corpus, Settings(), fam["subgroups"][0]["subgroup_id"], opening="Italian")
    assert d["total"] == 1 and d["rows"][0]["displayed_route"] == "opening"
    with pytest.raises(read.PoolNotFound):
        read.pool_events(corpus, Settings(), fam["subgroups"][0]["subgroup_id"])  # not emitted at __all__
    with pytest.raises(read.ReviewParamError):
        read.pool_events(corpus, Settings(), fam["family_id"], opening="Sicilian")
    with pytest.raises(read.ReviewParamError):
        read.pool_events(corpus, Settings(), fam["family_id"], reviewed_scope="mine")
    for missing in ("v1:route:lapse_offense:fork", "v1:fam:" + "0" * 16, "v1:route:faded:x"):
        with pytest.raises(read.PoolNotFound):
            read.pool_events(corpus, Settings(), missing)


def test_shown_stamp_keys_on_the_canonical_id_and_writes_nothing_else(corpus: psycopg.Connection[DictRow]) -> None:
    read.touch_shown(corpus, Settings(), "faded")
    read.touch_shown(corpus, Settings(), "v1:route:faded")
    rows = corpus.execute("SELECT pool_id FROM review_pool_state").fetchall()
    assert [r["pool_id"] for r in rows] == ["v1:route:faded"]
    for bad in ("v1:route:faded:x", "v1:route:lapse_offense:fork", "v1:fam:" + "0" * 16):
        with pytest.raises(read.PoolNotFound):
            read.touch_shown(corpus, Settings(), bad)
    with pytest.raises(read.ReviewParamError):
        read.touch_shown(corpus, Settings(), "v1:fam:" + "0" * 16, opening="Sicilian")
    assert corpus.execute("SELECT count(*) AS n FROM review_pool_state").fetchone() == {"n": 1}
    # The stamp rotates the representative: the runner-up game until the cooldown passes.
    p = read.page(corpus, Settings(), now=NOW)
    (fd,) = p["categories"]["faded"]["pools"]
    assert fd["representative_game"]["chess_game_id"] == 9
    corpus.execute("UPDATE review_pool_state SET last_shown_at = now() - interval '25 hours'")
    sub = p["categories"]["opening"]["families"][0]["subgroups"][0]
    read.touch_shown(corpus, Settings(), sub["subgroup_id"])
    p = read.page(corpus, Settings(), now=datetime.now(UTC))
    assert p["categories"]["opening"]["families"][0]["subgroups"][0]["representative_game"]["chess_game_id"] == 2


def test_a_chess960_row_is_invisible_everywhere(corpus: psycopg.Connection[DictRow]) -> None:
    rows = read.fetch_events(corpus, "all", "all")
    assert 10 not in {r["chess_game_id"] for r in rows}
    p = read.page(corpus, Settings(), time_class="all", now=NOW)
    assert p["page"]["total_games"] == 9
    sub = p["categories"]["opening"]["families"][0]["subgroups"][0]
    assert sub["total_games"] == 5
    d = read.pool_events(corpus, Settings(), sub["subgroup_id"], time_class="all", reviewed_scope="all")
    assert 10 not in {r["chess_game_id"] for r in d["rows"]}
    # Only the planted row: no node, so no stamp.
    corpus.execute("DELETE FROM review_events WHERE chess_game_id <> 10")
    with pytest.raises(read.PoolNotFound):
        read.touch_shown(corpus, Settings(), sub["subgroup_id"], time_class="all")
    assert read.page(corpus, Settings(), time_class="all", now=NOW)["page"]["total_games"] == 0
