"""Scout reads (core/scout/positions, nodes, report; core/activity), the Compare scout leg,
and the /scout routes: the player-to-move filter, chain collapse, the analysable predicate on
both sides, the best move read from the replay game, dismissal with its way back, colour-aligned
decision nodes, the shrunk win rate, the activity counts."""

from __future__ import annotations

import json
from typing import Any

import chess
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core import activity, blunders
from core.constants import PLAYER_ID
from core.repertoire import branch_compare
from core.repertoire.neighbourhood import parse_arriving
from core.scout import nodes, positions, report
from core.scout.positions import ScoutFilters
from core.settings import Settings
from tests import repertoire_helpers as h

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d4"]
ITALIAN_D3 = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3", "Nf6", "c3"]


def fens(moves: list[str]) -> list[str]:
    b = chess.Board()
    out = [b.fen()]
    for m in moves:
        b.push_san(m)
        out.append(b.fen())
    return out


P6 = fens(ITALIAN)[6]  # after 3...Bc5: White (the player) to move, ply 6
P7 = fens(ITALIAN)[7]  # after 4.c3: Black (the opponent) to move
P8 = fens(ITALIAN)[8]  # after 4...Nf6: White to move
P8_D3 = fens(ITALIAN_D3)[8]  # after 4.d3 Nf6: White to move


def opp_profile(conn: psycopg.Connection[DictRow], name: str = "Giri", pid: int = 1) -> int:
    conn.execute(
        "INSERT INTO opponent_profiles (id, player_id, name, active, is_initialized) VALUES (%s, %s, %s, TRUE, TRUE)",
        (pid, PLAYER_ID, name),
    )
    conn.execute("SELECT setval('opponent_profiles_id_seq', (SELECT max(id) FROM opponent_profiles))")
    return pid


def opp_game(
    conn: psycopg.Connection[DictRow],
    gid: int,
    moves: list[str],
    *,
    profile_id: int = 1,
    played_as: str = "black",
    result: str | None = "win",
    days_ago: float = 1,
    variant: str = "standard",
    family: str | None = None,
    variation: str | None = None,
    eco: str | None = None,
) -> None:
    seq = fens(moves)
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, variant, time_class, moves,"
        " fen_sequence, starting_fen, canonical_family, canonical_variation, opening_eco, opening_name)"
        " VALUES (%s, 'chesscom', %s, %s, now() - make_interval(secs => %s), %s, 'rapid', %s::jsonb, %s::jsonb, %s,"
        " %s, %s, %s, %s)",
        (
            gid,
            f"o{gid}",
            f"https://example.test/o{gid}",
            days_ago * 86400,
            variant,
            json.dumps(moves),
            json.dumps(seq),
            chess.STARTING_FEN if variant == "chess960" else None,
            family,
            variation,
            eco,
            family,
        ),
    )
    conn.execute(
        "INSERT INTO opponent_views (opponent_profile_id, chess_game_id, source_type, played_as, result)"
        " VALUES (%s, %s, 'chesscom', %s, %s)",
        (profile_id, gid, played_as, result),
    )


def blunder(conn: psycopg.Connection[DictRow], gid: int, ply: int, fen: str, played: str, best: str) -> None:
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, centipawn_loss, classification)"
        " VALUES (%s, %s, %s, %s, %s, %s, 300, 'blunder')",
        (PLAYER_ID, gid, ply, fen, played, best),
    )


@pytest.fixture()
def scout(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """The player (white) and the opponent (black) both through the Italian: the opponent's
    two games branch at 4.c3 / 4.d3; the player's do the same."""
    conn = clean
    h.player(conn)
    opp_profile(conn)
    opp_game(conn, 101, ITALIAN)
    opp_game(conn, 102, ITALIAN_D3)
    h.game(conn, 1, ITALIAN)
    h.game(conn, 2, ITALIAN_D3, days_ago=2)
    return conn


def flt(**over: Any) -> ScoutFilters:
    return (
        ScoutFilters(profile_id=1, my_last_n=0, opp_last_n=0, color="both", min_freq=1)
        if not over
        else ScoutFilters(**{"profile_id": 1, "my_last_n": 0, "opp_last_n": 0, "color": "both", "min_freq": 1, **over})
    )


def page_fens(conn: psycopg.Connection[DictRow], **over: Any) -> list[tuple[str, int]]:
    rows, _total, _tc = positions.page_rows(conn, flt(**over), 0)
    return [(r["fen"], int(r["tier"])) for r in rows]


# --- positions ---------------------------------------------------------------------------------


def test_only_player_to_move_positions_and_chain_collapse(scout: psycopg.Connection[DictRow]) -> None:
    """Gate 1: P7 (the opponent to move) never surfaces. Gate 2: P6 is a branch point (two
    surfaced successors) and stays; P8 and P8_D3 are leaves and stay; with one branch pruned
    P6 becomes an interior point and is collapsed."""
    got = page_fens(scout)
    assert (P7, 3) not in got
    assert {f for f, _ in got} == {P6, P8, P8_D3}
    assert all(t == 3 for _, t in got)
    scout.execute("DELETE FROM opponent_views WHERE chess_game_id = 102")
    scout.execute("DELETE FROM player_games WHERE chess_game_id = 2")
    assert {f for f, _ in page_fens(scout)} == {P8}  # P6 has one successor now: an interior point


def test_the_blunder_tier_is_never_collapsed_and_ranks_first(scout: psycopg.Connection[DictRow]) -> None:
    scout.execute("DELETE FROM opponent_views WHERE chess_game_id = 102")
    scout.execute("DELETE FROM player_games WHERE chess_game_id = 2")
    blunder(scout, 1, 6, P6, "a3", "c3")
    got = page_fens(scout)
    assert got[0] == (P6, 1) and (P8, 3) in got
    rows, total, tcs = positions.page_rows(scout, flt(), 0)
    assert total == 2 and tcs == {1: 1, 3: 1}
    assert rows[0]["best_move"] == "c3" and rows[0]["move_played"] == "a3" and int(rows[0]["blunder_count"]) == 1


def test_repertoire_tier_collapses_along_the_line(scout: psycopg.Connection[DictRow]) -> None:
    conn = scout
    conn.execute("DELETE FROM opponent_views WHERE chess_game_id = 102")
    conn.execute("DELETE FROM player_games WHERE chess_game_id = 2")
    h.book(conn, 1, "Italian", "white")
    h.chapter(conn, 1, 1, "Main")
    h.line(conn, 1, 1, "Giuoco", ITALIAN)
    got = page_fens(conn)
    assert got == [(P8, 2)]  # P6 is interior on the line; P8 is its furthest surfaced position
    rows, _t, _c = positions.page_rows(conn, flt(), 0)
    assert rows[0]["line_name"] == "Giuoco" and rows[0]["book_color"] == "white"


def test_colour_filter(scout: psycopg.Connection[DictRow]) -> None:
    assert page_fens(scout, color="white") == page_fens(scout)
    assert page_fens(scout, color="black") == []  # the opponent never played white here


def test_chess960_games_count_for_nothing_on_either_side(scout: psycopg.Connection[DictRow]) -> None:
    """Gate 3: a Chess960 game holding the same boards (a standard start, as Chess960 can
    deal) surfaces nothing — the opponent's against the player's standard game, and the
    player's against the opponent's standard game."""
    conn = scout
    conn.execute("DELETE FROM opponent_views WHERE chess_game_id = 102")  # the player still has P8_D3 (game 2)
    assert {f for f, _ in page_fens(conn)} == {P8}
    opp_game(conn, 103, ITALIAN_D3, variant="chess960")
    assert {f for f, _ in page_fens(conn)} == {P8}
    conn.execute("DELETE FROM opponent_views WHERE chess_game_id = 103")
    opp_game(conn, 104, ITALIAN_D3)
    conn.execute("DELETE FROM player_games WHERE chess_game_id = 2")  # the opponent still has P8_D3 (game 104)
    assert {f for f, _ in page_fens(conn)} == {P8}
    h.game(conn, 3, ITALIAN_D3, variant="chess960")
    assert {f for f, _ in page_fens(conn)} == {P8}
    assert positions.opp_since(conn, 1, 3) is None  # the 960 game takes no window slot
    assert positions.my_since(conn, 2) is None


def test_windows_are_the_nth_most_recent_games_date(scout: psycopg.Connection[DictRow]) -> None:
    conn = scout
    opp_game(conn, 104, ["d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7"], days_ago=0.5)
    assert positions.opp_since(conn, 1, 1) is not None and positions.opp_since(conn, 1, 4) is None
    # the newest opponent game is not the Italian: with a one-game window nothing overlaps
    assert page_fens(conn, opp_last_n=1) == []
    assert positions.my_since(conn, 1) is not None and page_fens(conn, my_last_n=2) == page_fens(conn)


def test_min_freq_counts_distinct_opponent_games(scout: psycopg.Connection[DictRow]) -> None:
    got = page_fens(scout, min_freq=2)
    assert {f for f, _ in got} == {P6}  # only P6 is in both of the opponent's games


def test_dismissed_boards_are_excluded_and_listed_for_restore(scout: psycopg.Connection[DictRow]) -> None:
    """Test 8: a tier-3 board with no blunder row, dismissed: absent here, absent from
    Blunders' dismissed list, present in the shared list, back after restore."""
    conn = scout
    blunders.dismiss(conn, P8)
    assert P8 not in {f for f, _ in page_fens(conn)}
    listed = blunders.dismissed(conn)
    assert [b["fen"] for b in listed] == [P8]
    page = blunders.positions(conn, blunders.BlunderFilters(("blunder",), 1, None, 0, "all", True), "all")
    assert page["positions"] == []  # nothing on Blunders knows this board
    blunders.restore(conn, P8)
    assert P8 in {f for f, _ in page_fens(conn)} and blunders.dismissed(conn) == []


# --- details -----------------------------------------------------------------------------------


def ply_analysis(n: int, best: dict[int, str], shift: int = 0) -> str:
    return json.dumps([{"ply": i + shift, "best_move": best.get(i), "eval": 0} for i in range(n)])


def test_best_move_is_read_from_the_replay_game_at_the_aligned_ply(scout: psycopg.Connection[DictRow]) -> None:
    """Test 7: the entry at index 6 whose `ply` is 6 gives the move; shifted by one it gives
    nothing; a game outside the window (`ply_analysis` NULL) gives nothing."""
    conn = scout
    conn.execute("UPDATE chess_games SET ply_analysis = %s::jsonb WHERE id = 1", (ply_analysis(9, {6: "d3", 8: "d4"}),))
    conn.execute("UPDATE chess_games SET ply_analysis = NULL WHERE id = 2")
    d = positions.details(conn, 1, [P6, P8, P8_D3])
    assert d[P6]["best_move"] == "d3" and d[P6]["ply"] == 6 and d[P6]["moves"] == ITALIAN
    assert d[P8]["best_move"] == "d4" and d[P8]["best_move_date"]
    assert d[P8_D3]["best_move"] is None and d[P8_D3]["ply"] == 8  # the replay exists, the analysis does not
    conn.execute("UPDATE chess_games SET ply_analysis = %s::jsonb WHERE id = 1", (ply_analysis(9, {6: "d3"}, shift=1),))
    assert positions.details(conn, 1, [P6])[P6]["best_move"] is None
    # the replay is the most recent game through the board; an older analysed game does not win
    h.game(conn, 3, ITALIAN, days_ago=5)
    conn.execute("UPDATE chess_games SET ply_analysis = %s::jsonb WHERE id = 3", (ply_analysis(9, {6: "Nc3"}),))
    assert positions.details(conn, 1, [P6])[P6]["best_move"] is None


def test_details_games_and_repertoire_move(scout: psycopg.Connection[DictRow]) -> None:
    conn = scout
    h.book(conn, 1, "Italian", "white")
    h.chapter(conn, 1, 1, "Main")
    h.line(conn, 1, 1, "Giuoco", ITALIAN)
    blunder(conn, 1, 6, P6, "a3", "d3")
    d = positions.details(conn, 1, [P6, P8_D3])
    assert [g["class"] for g in d[P6]["my_games"]] == ["blunder"]
    assert len(d[P8_D3]["my_games"]) == 1 and d[P8_D3]["my_games"][0]["class"] == ""
    assert [g["as"] for g in d[P6]["opp_games"]] == ["black", "black"]
    assert d[P6]["opp_games"][0]["url"].startswith("https://www.chess.com/game/live/")
    assert d[P6]["rep_expected_move"] == "c3" and d[P6]["rep_lines"][0]["line_name"] == "Giuoco"
    assert d[P8_D3]["rep_expected_move"] is None
    full = positions.page(conn, flt(), 0)
    assert full["total"] == 3 and full["total_pages"] == 1 and full["tier_counts"] == {"1": 1, "2": 1, "3": 1}
    first = full["positions"][0]
    assert (
        first["fen"] == P6 and first["tier"] == 1 and first["best_move"] == "d3" and first["rep_expected_move"] == "c3"
    )


# --- decision nodes ----------------------------------------------------------------------------

NODE_A = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Nc3"]  # Black to move at ply 7, reached by Nc3
NODE_B = ["e4", "e5", "Nc3", "Nc6", "Nf3", "Nf6", "Bc4"]  # the same board (same clocks), reached by Bc4
NODE = fens(NODE_A)[7]
assert NODE == fens(NODE_B)[7]


def config(**over: Any) -> Settings:
    return Settings(**{"branch_min": 2, "reply_min_freq": 1, "reply_cap": 4, **over})


def test_decision_nodes_are_colour_aligned_with_a_lead_in(clean: psycopg.Connection[DictRow]) -> None:
    """Test 9: the opponent (black) chose Bc5 / Bb4 at NODE; the player reached it as white
    by Nc3 → a card with the lead-in. Reached by two different moves → no lead-in. Reached
    only in the opponent's seat (the player was black) → no card."""
    conn = clean
    h.player(conn)
    opp_profile(conn)
    opp_game(conn, 101, NODE_A + ["Bc5"])
    opp_game(conn, 102, NODE_A + ["Bb4"])
    opp_game(conn, 103, NODE_A + ["Bb4"])
    h.game(conn, 1, NODE_A + ["Bc5", "d3"], color="black")  # the player in the opponent's seat
    assert nodes.decision_nodes(conn, flt(), config()) == []
    h.game(conn, 2, NODE_A + ["Bc5", "d3"], color="white")
    got = nodes.decision_nodes(conn, flt(), config())
    assert len(got) == 1
    n = got[0]
    assert n["fen"] == NODE and n["node_freq"] == 3 and n["distinct_replies"] == 2 and n["my_frequency"] == 1
    assert n["opp_replies"] == [{"move": "Bb4", "cnt": 2}, {"move": "Bc5", "cnt": 1}] and n["replies_more"] == 0
    assert n["lead_in"] == "Nc3" and n["lead_pre_fen"] == fens(NODE_A)[6] and n["my_color"] == "white"
    h.game(conn, 3, NODE_B + ["Bb4", "d3"], color="white")
    n = nodes.decision_nodes(conn, flt(), config())[0]
    assert n["lead_in"] is None and n["lead_pre_fen"] is None and n["my_frequency"] == 2
    capped = nodes.decision_nodes(conn, flt(), config(reply_cap=1))[0]
    assert capped["opp_replies"] == [{"move": "Bb4", "cnt": 2}] and capped["replies_more"] == 1
    assert nodes.decision_nodes(conn, flt(), config(branch_min=3)) == []
    assert nodes.decision_nodes(conn, flt(min_freq=4), config()) == []
    blunders.dismiss(conn, NODE)
    assert nodes.decision_nodes(conn, flt(), config()) == []


def test_decision_nodes_repertoire_coverage_and_960(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    h.player(conn)
    opp_profile(conn)
    opp_game(conn, 101, NODE_A + ["Bc5"])
    opp_game(conn, 102, NODE_A + ["Bb4"])
    h.book(conn, 1, "Italian", "white")
    h.chapter(conn, 1, 1, "Main")
    h.line(conn, 1, 1, "Four knights", NODE_A + ["Bb4", "O-O"])
    n = nodes.decision_nodes(conn, flt(), config())[0]
    assert n["line_name"] == "Four knights" and n["my_frequency"] == 0 and n["lead_in"] == "Nc3"
    conn.execute("UPDATE chess_games SET variant = 'chess960', starting_fen = %s WHERE id = 102", (chess.STARTING_FEN,))
    assert nodes.decision_nodes(conn, flt(), config()) == []  # one standard reply left: not a choice


# --- report and activity -----------------------------------------------------------------------


def test_shrink_and_the_ranking(clean: psycopg.Connection[DictRow]) -> None:
    """Test 10: k = 0 is the raw rate; at k = 10 a 1-game 100 % line ranks below a 10-game 80 % line."""
    lines = [
        {"family": "A", "variation": "A: one", "games": 1, "wins": 1, "draws": 0, "losses": 0, "eco": ""},
        {"family": "B", "variation": "B: ten", "games": 10, "wins": 8, "draws": 0, "losses": 2, "eco": ""},
    ]
    raw = report.shrink([dict(line) for line in lines], 0.5, 0)
    assert [line["variation"] for line in raw] == ["A: one", "B: ten"] and raw[0]["win_pct"] == 100.0
    shrunk = report.shrink([dict(line) for line in lines], 0.5, 10)
    assert [line["variation"] for line in shrunk] == ["B: ten", "A: one"]
    assert shrunk[1]["shrunk_rate"] == pytest.approx((1 + 5) / 11)


def test_report_buckets_and_overall_over_result_bearing_views(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    h.player(conn)
    opp_profile(conn)
    for i in range(3):
        opp_game(
            conn, 200 + i, ITALIAN, family="Italian Game", variation="Italian Game: Giuoco", eco="C50", result="win"
        )
    opp_game(
        conn, 210, ITALIAN_D3, played_as="white", family="Italian Game", variation="Italian Game: Giuoco", result="loss"
    )
    opp_game(conn, 211, ["d4", "d5"], family="Queen's Pawn", variation="Queen's Pawn: 2.c4", result="loss", days_ago=9)
    opp_game(conn, 212, ["c4"], family=None, result=None)  # no result and no family: counts nowhere but activity
    r = report.report(conn, 1, prior_strength=0, opp_since=None)
    assert r["activity"]["total"] == 6 and r["activity"]["last_7"] == 5 and r["activity"]["chesscom_total"] == 6
    assert [(m["family"], m["cnt"], m["pct"]) for m in r["most_played"]] == [
        ("Italian Game", 4, 80),
        ("Queen's Pawn", 1, 20),
    ]
    assert [(m["family"], m["cnt"]) for m in r["as_black"]] == [("Italian Game", 3), ("Queen's Pawn", 1)]
    assert r["as_white"] == [{"family": "Italian Game", "cnt": 1, "pct": 100, "games": r["as_white"][0]["games"]}]
    assert len(r["as_black"][0]["games"]) == 3 and r["as_black"][0]["games"][0]["url"]
    assert [(line["variation"], line["games"], line["wins"], line["eco"]) for line in r["best_lines"]] == [
        ("Italian Game: Giuoco", 4, 3, "C50"),
        ("Queen's Pawn: 2.c4", 1, 0, ""),
    ]
    assert r["worst_lines"][0]["variation"] == "Queen's Pawn: 2.c4"
    games = report.line_games(conn, 1, family="Italian Game", variation=None, opp_since=None)
    assert len(games) == 4 and games[0]["result"] in ("win", "loss") and games[0]["color"] in ("white", "black")
    assert len(report.line_games(conn, 1, family="Queen's Pawn", variation="Queen's Pawn: 2.c4", opp_since=None)) == 1
    with pytest.raises(ValueError):
        report.line_games(conn, 1, family=" ", variation=None, opp_since=None)
    windowed = report.report(conn, 1, prior_strength=0, opp_since=positions.opp_since(conn, 1, 5))
    assert [m["family"] for m in windowed["most_played"]] == ["Italian Game"]


def test_activity_counts(clean: psycopg.Connection[DictRow]) -> None:
    """Test 12: 25 h ago is in 7 d, not 24 h; the player's Chess960 game counts; the
    opponent's counts read the profile's views only."""
    conn = clean
    h.player(conn)
    opp_profile(conn)
    h.game(conn, 1, ["e4"], days_ago=25 / 24)
    h.game(conn, 2, ["e4"], days_ago=0.1, variant="chess960")
    h.game(conn, 3, ["e4"], days_ago=40)
    opp_game(conn, 101, ["d4"], days_ago=0.2)
    mine = activity.counts(conn, activity.Player())
    assert (mine["last_1"], mine["last_7"], mine["last_30"], mine["total"]) == (1, 2, 2, 3)
    assert mine["lichess_total"] == 3 and mine["chesscom_total"] == 0
    theirs = activity.counts(conn, activity.Opponent(1))
    assert (theirs["last_1"], theirs["total"], theirs["chesscom_total"]) == (1, 1, 1)


# --- Compare's scout leg ------------------------------------------------------------------------

PARENT = fens(ITALIAN)[5]  # after 3.Bc4, Black to move
CURRENT = fens(ITALIAN)[6]  # 3...Bc5
TWO_KNIGHTS = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "d3"]
ARRIVING = parse_arriving(PARENT, "Bc5")


def compare(conn: psycopg.Connection[DictRow]) -> dict[str, Any]:
    assert ARRIVING is not None
    return branch_compare.branch_compare(conn, CURRENT, PARENT, arriving=ARRIVING, book_color="black", max_boards=12)


def test_compare_scout_leg(clean: psycopg.Connection[DictRow]) -> None:
    """Test 11: the opponent's games through the parent as the side to move produce the scout
    source; the same board in the repertoire keeps both; a game where the scouted account was
    on the other side produces nothing; the best move comes from the player's analysed game."""
    conn = clean
    h.player(conn)
    opp_profile(conn, "Giri", 1)
    opp_profile(conn, "Caruana", 2)
    opp_game(conn, 101, TWO_KNIGHTS, profile_id=1)
    opp_game(conn, 102, TWO_KNIGHTS, profile_id=1)
    opp_game(conn, 103, TWO_KNIGHTS, profile_id=2)
    opp_game(conn, 104, ITALIAN, profile_id=2)  # the current branch: never in `branches`
    out = compare(conn)
    assert out["current"]["sources"]["scout"] == {
        "total_games": 1,
        "profiles": [{"name": "Caruana", "games": 1}],
        "best_move_san": None,
        "best_move_squares": None,
    }
    assert len(out["branches"]) == 1
    b = out["branches"][0]
    assert b["opponent_move"]["san"] == "Nf6" and b["sources"]["repertoire"] is None
    assert b["sources"]["scout"]["total_games"] == 3
    assert b["sources"]["scout"]["profiles"] == [{"name": "Giri", "games": 2}, {"name": "Caruana", "games": 1}]
    assert b["sources"]["scout"]["best_move_san"] is None
    # the player met the child board in an analysed game: blue + green
    h.game(conn, 1, TWO_KNIGHTS)
    conn.execute("UPDATE chess_games SET ply_analysis = %s::jsonb WHERE id = 1", (ply_analysis(7, {6: "d3"}),))
    b = compare(conn)["branches"][0]
    assert b["sources"]["scout"]["best_move_san"] == "d3" and b["sources"]["scout"]["best_move_squares"] == {
        "from": "d2",
        "to": "d3",
    }
    # the same board in the repertoire keeps both sources
    h.book(conn, 1, "Black book", "black")
    h.chapter(conn, 1, 1, "Main")
    h.line(conn, 1, 1, "Two knights", TWO_KNIGHTS)
    b = compare(conn)["branches"][0]
    assert b["sources"]["repertoire"] is not None and b["sources"]["scout"]["total_games"] == 3
    # the scouted account was White in this game: not their move at the parent
    conn.execute("DELETE FROM opponent_views WHERE chess_game_id IN (101, 102, 103)")
    opp_game(conn, 105, TWO_KNIGHTS, profile_id=1, played_as="white")
    assert compare(conn)["branches"][0]["sources"]["scout"] is None


# --- routes ------------------------------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, scout: psycopg.Connection[DictRow]) -> TestClient:
    from api.main import create_app

    scout.commit()
    c = TestClient(create_app())
    c.post("/login", json={"password": "correct horse"})
    return c


def test_scout_routes(client: TestClient, scout: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    from core.scout import profiles

    r = client.get("/scout/profiles").json()
    assert [p["name"] for p in r["profiles"]] == ["Giri"] and r["profiles"][0]["game_count"] == 2
    r = client.get("/scout/positions/1", params={"my_last_n": 0, "opp_last_n": 0, "min_freq": 1}).json()
    assert r["total"] == 3 and r["positions"][0]["opp_games"]
    assert client.get("/scout/positions/1", params={"color": "red"}).status_code == 422
    assert client.get("/scout/positions/99").status_code == 404
    r = client.get("/scout/decision-nodes/1", params={"my_last_n": 0, "opp_last_n": 0, "min_freq": 1}).json()
    assert r == {"nodes": [], "total": 0}  # one reply per opponent-to-move board: no choice to show
    r = client.get("/scout/report/1").json()
    assert r["activity"]["total"] == 2 and r["most_played"] == [] and r["best_lines"] == []
    assert client.get("/scout/line-games/1").status_code == 400
    assert client.get("/scout/line-games/1", params={"family": "Italian Game"}).json() == {"games": []}
    # dismiss from Scout, list, restore; a short FEN is refused
    assert client.post("/scout/dismiss", json={"fen": P8}).status_code == 200
    assert client.post("/scout/dismiss", json={"fen": P8.rsplit(" ", 1)[0]}).status_code == 422
    r = client.get("/scout/positions/1", params={"my_last_n": 0, "opp_last_n": 0, "min_freq": 1}).json()
    assert P8 not in {p["fen"] for p in r["positions"]}
    listed = client.get("/scout/dismissed").json()["boards"]
    assert [b["fen"] for b in listed] == [P8] and listed[0]["dismissed_at"]
    assert client.request("DELETE", "/scout/dismiss", json={"fen": P8}).status_code == 200
    assert client.get("/scout/dismissed").json() == {"boards": []}
    # add: verification on submit, then the profile is uninitialised; a taken name is 409
    monkeypatch.setattr(profiles, "verify_handle", lambda platform, u, client=None: u.lower() + "_canon")
    r = client.post("/scout/profiles", json={"name": "Nakamura", "chesscom_username": "Hikaru"})
    assert r.status_code == 200
    new_id = r.json()["profile_id"]
    listed = client.get("/scout/profiles").json()["profiles"]
    added = next(p for p in listed if p["id"] == new_id)
    assert (
        added["chesscom_username"] == "hikaru_canon" and added["is_initialized"] is False and added["game_count"] == 0
    )
    assert client.post("/scout/profiles", json={"name": "Nakamura", "lichess_username": "x"}).status_code == 409
    assert client.post("/scout/profiles", json={"name": "Nobody"}).status_code == 400

    def missing(platform: str, u: str, client: Any = None) -> str:
        raise profiles.HandleNotFound(platform)

    monkeypatch.setattr(profiles, "verify_handle", missing)
    r = client.post("/scout/profiles", json={"name": "Ghost", "lichess_username": "nobody"})
    assert r.status_code == 422 and r.json()["detail"] == "lichess_username_not_found"
    assert client.delete(f"/scout/profiles/{new_id}").status_code == 200
    assert client.delete(f"/scout/profiles/{new_id}").status_code == 404
    assert [p["name"] for p in client.get("/scout/profiles").json()["profiles"]] == ["Giri"]
    home = client.get("/home").json()
    assert home["activity"]["total"] == 2 and home["activity"]["last_7"] == 2


def test_verify_handle_against_the_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from core.ingest.records import FetchError
    from core.scout import profiles

    def platform(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/pub/player/hikaru"):
            return httpx.Response(200, json={"username": "hikaru", "player_id": 1})
        if url.endswith("/api/user/DrNykterstein"):
            return httpx.Response(200, json={"username": "DrNykterstein"})
        if url.endswith("/api/user/broken"):
            return httpx.Response(500)
        return httpx.Response(404)

    c = httpx.Client(transport=httpx.MockTransport(platform))
    assert profiles.verify_handle("chesscom", "Hikaru", client=c) == "hikaru"
    assert profiles.verify_handle("lichess", "DrNykterstein", client=c) == "DrNykterstein"
    with pytest.raises(profiles.HandleNotFound) as exc:
        profiles.verify_handle("lichess", "nobody", client=c)
    assert exc.value.platform == "lichess"
    with pytest.raises(FetchError) as fexc:
        profiles.verify_handle("lichess", "broken", client=c)
    assert "broken" not in str(fexc.value)
