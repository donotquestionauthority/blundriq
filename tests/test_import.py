"""Importers: parsing both platforms' payloads, the store's merge rules, the
"only importers write chess_games" rule, NULLS LAST ordering, empty import no-op."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import DictRow

from core.chess.board import canonicalize_chess960_fen, moves_to_fen_sequence
from core.chess.platform import chesscom_termination, chesscom_time_class, lichess_termination, lichess_time_class
from core.constants import PLAYER_ID
from core.ingest import chesscom, lichess
from core.ingest.records import FetchError
from core.ingest.run import import_chesscom, import_lichess
from core.ingest.store import store_game, upsert_game

ME = "rob_test"
FEN_960_SHREDDER = "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1"
FEN_960_XFEN = "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1"


def _pgn(moves: str, extra_tags: str = "") -> str:
    return (
        '[Event "Live Chess"]\n[Site "Chess.com"]\n[Date "2026.09.01"]\n'
        f'[White "{ME}"]\n[Black "opp_one"]\n[Result "1-0"]\n[ECO "B01"]\n'
        '[ECOUrl "https://www.chess.com/openings/Scandinavian-Defense-Mieses-Kotrc-Variation"]\n'
        '[TimeControl "600"]\n' + extra_tags + "\n" + moves + " 1-0\n"
    )


def _chesscom_game(**over: Any) -> dict[str, Any]:
    g: dict[str, Any] = {
        "url": "https://www.chess.com/game/live/1001",
        "pgn": _pgn(
            "1. e4 {[%clk 0:09:58.1]} 1... d5 {[%clk 0:09:55]} 2. exd5 {[%clk 0:09:50]} 2... Qxd5 {[%clk 0:09:40.9]}"
        ),
        "time_control": "600",
        "end_time": 1756742400,
        "rated": True,
        "rules": "chess",
        "white": {"rating": 1500, "result": "win", "username": ME},
        "black": {"rating": 1480, "result": "resigned", "username": "opp_one"},
    }
    g.update(over)
    return g


def _lichess_game(**over: Any) -> dict[str, Any]:
    g: dict[str, Any] = {
        "id": "abcd1234",
        "rated": True,
        "variant": "standard",
        "speed": "rapid",
        "perf": "rapid",
        "createdAt": 1756742000000,
        "lastMoveAt": 1756742400000,
        "status": "resign",
        "players": {
            "white": {"user": {"name": "Opp_Two", "id": "opp_two"}, "rating": 1600},
            "black": {"user": {"name": ME.capitalize(), "id": ME}, "rating": 1550},
        },
        "winner": "black",
        "opening": {"eco": "B01", "name": "Scandinavian Defense: Mieses-Kotroc Variation", "ply": 4},
        "moves": "e4 d5 exd5 Qxd5",
        "clocks": [59900, 59500, 59000, 58000],
        "clock": {"initial": 600, "increment": 0, "totalTime": 600},
    }
    g.update(over)
    return g


# --- parsing ----------------------------------------------------------------------


def test_chesscom_parse_standard_game() -> None:
    rec = chesscom.parse_game(_chesscom_game(), ME)
    assert rec is not None
    assert rec.platform_game_id == "1001" and rec.player_color == "white" and rec.result == "win"
    assert rec.moves == ["e4", "d5", "exd5", "Qxd5"] and len(rec.fen_sequence) == 5
    assert rec.clocks == [598, 595, 590, 580]
    assert rec.termination == "resignation" and rec.time_class == "rapid"
    assert rec.opening_eco == "B01" and rec.opening_name == "Scandinavian Defense Mieses Kotrc Variation"
    assert rec.opponent_username == "opp_one" and rec.opponent_rating == 1480 and rec.player_rating == 1500
    assert rec.played_at == datetime(2025, 9, 1, 16, 0, tzinfo=UTC)
    assert rec.variant == "standard" and rec.starting_fen is None


def test_chesscom_parse_as_black_and_results() -> None:
    g = _chesscom_game(
        white={"rating": 1500, "result": "checkmated", "username": "opp_one"},
        black={"rating": 1480, "result": "win", "username": ME},
    )
    rec = chesscom.parse_game(g, ME)
    assert rec and rec.player_color == "black" and rec.result == "win" and rec.termination == "checkmate"
    g = _chesscom_game(white={"result": "agreed", "username": ME}, black={"result": "agreed", "username": "x"})
    rec = chesscom.parse_game(g, ME)
    assert rec and rec.result == "draw" and rec.termination == "agreement"


def test_chesscom_clocks_all_or_nothing() -> None:
    rec = chesscom.parse_game(_chesscom_game(pgn=_pgn("1. e4 {[%clk 0:09:58]} 1... d5 2. exd5 Qxd5")), ME)
    assert rec and rec.clocks is None and len(rec.moves) == 4


def test_chesscom_chess960_canonicalises_shredder_fen() -> None:
    pgn = _pgn("1. e4 e5 2. Nc3 Nc6", f'[Variant "Chess960"]\n[SetUp "1"]\n[FEN "{FEN_960_SHREDDER}"]\n')
    rec = chesscom.parse_game(_chesscom_game(rules="chess960", pgn=pgn), ME)
    assert rec and rec.variant == "chess960" and rec.starting_fen == FEN_960_XFEN
    assert rec.fen_sequence[0] == rec.starting_fen  # the schema CHECK requires byte equality
    assert chesscom.parse_game(_chesscom_game(rules="chess960"), ME) is None  # 960 without a FEN header


def test_chesscom_skips_other_variants_and_bad_moves() -> None:
    assert chesscom.parse_game(_chesscom_game(rules="bughouse"), ME) is None
    assert chesscom.parse_game(_chesscom_game(pgn=""), ME) is None
    assert chesscom.parse_game(_chesscom_game(pgn=_pgn("1. e4 e5 2. Kxe8")), ME) is None


def test_chesscom_archives_since() -> None:
    urls = [f"https://api.chess.com/pub/player/{ME}/games/2026/0{m}" for m in range(1, 10)]
    kept = chesscom.archives_since(urls, datetime(2026, 7, 15, tzinfo=UTC))
    assert [u[-2:] for u in kept] == ["07", "08", "09"]


def test_lichess_parse_game() -> None:
    rec = lichess.parse_game(_lichess_game(), ME)
    assert rec is not None
    assert (
        rec.platform == "lichess" and rec.platform_game_id == "abcd1234" and rec.url == "https://lichess.org/abcd1234"
    )
    assert rec.player_color == "black" and rec.result == "win" and rec.termination == "resignation"
    assert rec.moves == ["e4", "d5", "exd5", "Qxd5"] and rec.clocks == [599, 595, 590, 580]
    assert rec.time_control == "600+0" and rec.time_class == "rapid"
    assert rec.opening_eco == "B01" and rec.opponent_username == "Opp_Two" and rec.player_rating == 1550
    assert rec.played_at == datetime.fromtimestamp(1756742400, tz=UTC)


def test_lichess_variants_and_edge_cases() -> None:
    assert lichess.parse_game(_lichess_game(variant="atomic"), ME) is None
    assert lichess.parse_game(_lichess_game(moves=""), ME) is None
    rec = lichess.parse_game(
        _lichess_game(variant={"key": "chess960"}, initialFen=FEN_960_SHREDDER, moves="e4 e5 Nc3"), ME
    )
    assert rec and rec.variant == "chess960" and rec.starting_fen == FEN_960_XFEN
    draw = lichess.parse_game(_lichess_game(status="draw", winner=None), ME)
    assert draw and draw.result == "draw" and draw.termination == "draw"
    lost = lichess.parse_game(_lichess_game(status="outoftime", winner="white"), ME)
    assert lost and lost.result == "loss" and lost.termination == "timeout"


def test_platform_vocabularies() -> None:
    assert chesscom_time_class("180+2") == "blitz" and chesscom_time_class("1/86400") == "correspondence"
    assert chesscom_time_class("900+10") == "rapid" and chesscom_time_class("garbage") is None
    assert lichess_time_class("ultraBullet") == "bullet" and lichess_time_class("classical") == "classical"
    assert lichess_termination("noStart") == "abandonment" and lichess_termination("weird") == "other"
    g = {"white": {"username": ME, "result": "win"}, "black": {"username": "o", "result": "timeout"}}
    assert chesscom_termination(g, ME) == "timeout"


def test_fen_sequence_invariants() -> None:
    assert len(moves_to_fen_sequence(["e4", "e5"])) == 3
    with pytest.raises(ValueError, match="ply 1"):
        moves_to_fen_sequence(["e4", "Ke8"])
    assert canonicalize_chess960_fen(FEN_960_XFEN) == FEN_960_XFEN  # idempotent
    with pytest.raises(ValueError):
        canonicalize_chess960_fen("not a fen")


# --- store ------------------------------------------------------------------------


def _player(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute(
        "INSERT INTO players (id, chesscom_username, lichess_username) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
        (PLAYER_ID, ME, ME),
    )


def test_store_is_idempotent_and_ratchets_nulls(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    rec = chesscom.parse_game(_chesscom_game(), ME)
    assert rec is not None
    with conn.transaction():
        assert store_game(conn, rec) is True
        assert store_game(conn, rec) is False  # same game again: no new player_games row
    n = conn.execute("SELECT count(*) AS n FROM chess_games").fetchone()
    assert n and n["n"] == 1
    # a second write with an opening name fills the blank; a blank never overwrites a value
    from dataclasses import replace

    bare = replace(rec, opening_name="", opening_eco="", termination=None)
    gid = upsert_game(conn, bare)
    row = conn.execute(
        "SELECT opening_name, canonical_family, termination FROM chess_games WHERE id = %s", (gid,)
    ).fetchone()
    assert (
        row
        and row["opening_name"] == "Scandinavian Defense Mieses Kotrc Variation"
        and row["termination"] == "resignation"
    )
    assert row["canonical_family"] == "Scandinavian Defense"


def test_store_chess960_satisfies_schema_checks(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    pgn = _pgn("1. e4 e5 2. Nc3 Nc6", f'[Variant "Chess960"]\n[SetUp "1"]\n[FEN "{FEN_960_SHREDDER}"]\n')
    rec = chesscom.parse_game(_chesscom_game(rules="chess960", pgn=pgn, url="https://www.chess.com/game/live/1002"), ME)
    assert rec is not None
    with conn.transaction():
        store_game(conn, rec)
    row = conn.execute(
        "SELECT variant, starting_fen, fen_sequence->>0 AS f0 FROM chess_games WHERE platform_game_id = '1002'"
    ).fetchone()
    assert row and row["variant"] == "chess960" and row["starting_fen"] == row["f0"] == FEN_960_XFEN


# --- the step against a fake platform ------------------------------------------------


def _chesscom_transport(archives: dict[str, list[dict[str, Any]]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/games/archives"):
            return httpx.Response(200, json={"archives": list(archives)})
        if url in archives:
            return httpx.Response(200, json={"games": archives[url]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_import_chesscom_incremental_and_empty(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    conn.commit()
    month = datetime.now(UTC).strftime("%Y/%m")
    url = f"https://api.chess.com/pub/player/{ME}/games/{month}"
    client = httpx.Client(
        transport=_chesscom_transport({url: [_chesscom_game(url="https://www.chess.com/game/live/2001")]})
    )
    s = import_chesscom(conn, ME, client=client)
    assert (s.fetched, s.new, s.skipped) == (1, 1, 0)
    s = import_chesscom(conn, ME, client=client)  # nothing new; same-second game flows to the upsert and dedups
    assert (s.fetched, s.new) == (1, 0)
    empty = httpx.Client(transport=_chesscom_transport({}))
    s = import_chesscom(conn, ME, client=empty)
    assert (s.fetched, s.new, s.skipped) == (0, 0, 0)
    n = conn.execute("SELECT count(*) AS n FROM player_games WHERE player_id = %s", (PLAYER_ID,)).fetchone()
    assert n and n["n"] == 1
    checked = conn.execute("SELECT chesscom_last_checked FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    assert checked and checked["chesscom_last_checked"] is not None


def test_import_fails_closed_on_platform_errors(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    conn.commit()
    limited = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(FetchError, match="429"):
        import_chesscom(conn, ME, client=limited)
    checked = conn.execute("SELECT chesscom_last_checked FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    assert checked and checked["chesscom_last_checked"] is None  # a failed import never counts as checked


def test_import_lichess_stream(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    conn.commit()
    lines = "\n".join(json.dumps(_lichess_game(id=f"g{i}", lastMoveAt=1756742400000 + i)) for i in range(3))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=lines.encode())))
    s = import_lichess(conn, ME, client=client)
    assert (s.fetched, s.new) == (3, 3)
    assert import_lichess(conn, ME, client=client).new == 0


# --- repository rules ---------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def _py_files() -> list[Path]:
    return [p for d in ("core", "api", "pipeline") for p in (ROOT / d).rglob("*.py")]


def test_only_the_store_and_the_migration_insert_chess_games() -> None:
    writers = {
        p.relative_to(ROOT).as_posix() for p in _py_files() if re.search(r"INSERT INTO chess_games", p.read_text())
    }
    assert writers == {"core/ingest/store.py"}
    # Bulk copy bypasses everything an ordinary write goes through, so the places that use
    # it are listed rather than left to spread: the one-time migration, the corpus reload
    # (a few hundred thousand rows of reference data) and the repertoire import's notes
    # (tens of thousands per file); each would otherwise be a network round trip per row.
    copiers = {
        p.relative_to(ROOT).as_posix() for p in _py_files() if "COPY" in p.read_text() and "FROM STDIN" in p.read_text()
    }
    assert copiers == {"core/migrate.py", "core/puzzles/corpus.py", "core/repertoire/annotations.py"}


def test_played_at_ordering_always_puts_nulls_last() -> None:
    """Every ORDER BY on played_at says NULLS LAST, so an undated game never sorts as newest."""
    for p in _py_files():
        for m in re.finditer(r"ORDER BY[^\n]*played_at DESC(?! NULLS LAST)", p.read_text()):
            raise AssertionError(f"{p.relative_to(ROOT)}: {m.group(0)!r} lacks NULLS LAST")


def test_lichess_interrupted_stream_is_completed_by_the_next_run(clean: psycopg.Connection[DictRow]) -> None:
    """150 games newest first; the transport dies after the first committed batch of 100.
    The next ordinary run must fetch the 50 the interrupted run never received."""
    conn = clean
    _player(conn)
    conn.commit()
    games = [_lichess_game(id=f"g{i:03d}", lastMoveAt=1756742400000 - i * 60000) for i in range(150)]
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = "\n".join(json.dumps(g) for g in games).encode()
        if calls["n"] == 1:
            # deliver the first 120 games, then break the connection mid-stream
            cut = b"\n".join(body.split(b"\n")[:120]) + b"\n"

            def gen() -> Any:
                yield cut
                raise httpx.ReadError("connection reset", request=request)

            return httpx.Response(200, content=gen())
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(flaky))
    with pytest.raises(FetchError):
        import_lichess(conn, ME, client=client)
    stored = conn.execute("SELECT count(*) AS n FROM player_games WHERE source = 'lichess'").fetchone()
    assert stored and stored["n"] == 100  # one batch committed, the run failed, nothing stamped
    checked = conn.execute("SELECT lichess_last_checked FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    assert checked and checked["lichess_last_checked"] is None

    s = import_lichess(conn, ME, client=client)
    assert (s.fetched, s.new) == (150, 50)
    stored = conn.execute("SELECT count(*) AS n FROM player_games WHERE source = 'lichess'").fetchone()
    assert stored and stored["n"] == 150
    checked = conn.execute("SELECT lichess_last_checked FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    assert checked and checked["lichess_last_checked"] is not None


def test_lichess_game_finishing_after_an_import_is_fetched_by_the_next(clean: psycopg.Connection[DictRow]) -> None:
    """The mock honours `since` (by creation time), `ongoing`, and a clock: a game created
    at 12:55 is still running during the 13:00 import and finishes at 13:10. The 14:00
    import must store it. Lichess games are created before they finish, so a boundary
    at an import's own start time would skip it for ever."""
    conn = clean
    _player(conn)
    conn.commit()
    t = lambda h, m: datetime(2026, 9, 20, h, m, tzinfo=UTC)  # noqa: E731
    ms = lambda d: int(d.timestamp() * 1000)  # noqa: E731
    clock = {"now": t(13, 0)}
    finished = _lichess_game(id="long1", createdAt=ms(t(12, 55)), lastMoveAt=ms(t(13, 10)))
    ongoing = {**finished, "status": "started", "winner": None, "lastMoveAt": ms(t(12, 59)), "moves": "e4 d5"}

    def platform(request: httpx.Request) -> httpx.Response:
        since = int(request.url.params.get("since", "0"))
        include_ongoing = request.url.params.get("ongoing") == "true"
        games: list[dict[str, Any]] = []
        for g in [ongoing] if clock["now"] < t(13, 10) else [finished]:
            if int(g["createdAt"]) < since:
                continue
            if g["status"] in ("created", "started") and not include_ongoing:
                continue
            games.append(g)
        return httpx.Response(200, content="\n".join(json.dumps(g) for g in games).encode())

    client = httpx.Client(transport=httpx.MockTransport(platform))
    s = import_lichess(conn, ME, client=client, now=t(13, 0))
    assert (s.fetched, s.new) == (0, 0)  # the game was ongoing: seen, not stored
    boundary = conn.execute("SELECT lichess_last_checked FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    assert boundary and boundary["lichess_last_checked"] == t(12, 55)  # the next import starts before it

    clock["now"] = t(14, 0)
    s = import_lichess(conn, ME, client=client, now=t(14, 0))
    assert (s.fetched, s.new) == (1, 1)
    stored = conn.execute("SELECT platform_game_id FROM chess_games").fetchall()
    assert [r["platform_game_id"] for r in stored] == ["long1"]
    boundary = conn.execute("SELECT lichess_last_checked FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    assert boundary and boundary["lichess_last_checked"] == t(14, 0)  # nothing ongoing: the boundary catches up

    # a third, empty import must not disturb anything
    clock["now"] = t(15, 0)
    assert import_lichess(conn, ME, client=client, now=t(15, 0)).fetched == 0
