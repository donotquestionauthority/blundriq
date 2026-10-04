"""Review's data: the opening prefix (migration 007, the importer, the backfill) and the
position evaluations (`pipeline position-evals`).

The prefix outlives the analysis window, is written once and never overwritten, and is
never given to a variant the pipeline does not analyse. The evaluation step evaluates exactly
the boards the position statistics admit, each rebuilt from one game's prefix and checked
against its key, and refuses (counts `failed`) a board that does not rebuild.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import chess
import httpx
import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from core import housekeeping, runs, schema
from core.chess.board import moves_to_fen_sequence
from core.constants import ANALYSABLE_VARIANTS, OPENING_PREFIX_PLIES, PLAYER_ID, STOCKFISH_DEPTH
from core.ingest import backfill, chesscom, lichess
from core.ingest.records import GameRecord
from core.ingest.store import store_game, upsert_game
from core.review import evals, positions
from core.settings import Settings

ME = "rob_test"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
_LONG = (
    "e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6 Nf3 Bf5 Bd2 c6 Bc4 e6 Qe2 Bb4 O-O-O Nbd7 Kb1 O-O-O "
    "a3 Bxc3 Bxc3 Qc7 d5 cxd5 Bxd5 exd5 Rhe1 Kb8 Qe7 Rc8"
).split()  # 32 plies: longer than the prefix
LONG: list[str] = [str(m) for m in _LONG]
FEN_960 = "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1"
MIGRATION = schema.MIGRATIONS_DIR / "007_review_positions.sql"


def record(
    gid: str,
    moves: list[str],
    *,
    days_ago: int = 1,
    color: str = "black",
    variant: str = "standard",
    played_at: datetime | None = None,
) -> GameRecord:
    fens = moves_to_fen_sequence(moves, FEN_960 if variant == "chess960" else None, variant)
    return GameRecord(
        platform="lichess",
        platform_game_id=gid,
        url=f"https://lichess.org/{gid}",
        played_at=played_at or NOW - timedelta(days=days_ago),
        time_control="600+0",
        time_class="rapid",
        opening_name=None,
        opening_eco=None,
        moves=moves,
        fen_sequence=fens,
        clocks=None,
        termination="resignation",
        variant=variant,
        starting_fen=fens[0] if variant == "chess960" else None,
        player_color=color,
        opponent_username="opp",
        opponent_rating=1500,
        player_rating=1500,
        result="loss",
    )


def _player(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute(
        "INSERT INTO players (id, chesscom_username, lichess_username) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
        (PLAYER_ID, ME, ME),
    )


def _prefix(conn: psycopg.Connection[DictRow], gid: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT opening_moves, opening_keys, position_keys[1:31] AS window_keys, moves IS NULL AS bare"
        " FROM chess_games WHERE platform_game_id = %s",
        (gid,),
    ).fetchone()
    assert row is not None
    return dict(row)


# --- migration 007 ------------------------------------------------------------------------


def test_the_prefix_length_and_the_variant_list_have_one_authority() -> None:
    """The SQL can't import Python, so its literals are pinned to the constants."""
    text = MIGRATION.read_text()
    assert f"'$[0 to {OPENING_PREFIX_PLIES}]'" in text  # positions: one more than the moves
    assert f"'$[0 to {OPENING_PREFIX_PLIES - 1}]'" in text  # moves
    assert f"'$[0 to {OPENING_PREFIX_PLIES}]'" in schema.SCHEMA_FILE.read_text()
    variants = re.findall(r"variant IN \(([^)]*)\)", text)
    assert variants, "the migration names its variants"
    for found in variants:
        assert tuple(v.strip().strip("'") for v in found.split(",")) == ANALYSABLE_VARIANTS
    assert "DROP" not in text.upper()  # additive: the deployed Review page still reads every table


def test_a_populated_upgrade_gives_window_games_their_prefix_and_keeps_review_state(
    fresh_db_url: str, tmp_path: Path
) -> None:
    from psycopg import sql

    from core.review import read
    from tests.test_schema import FIXTURES, _scratch

    url = _scratch(fresh_db_url, "prefix_v6")
    v6 = tmp_path / "v6"
    v6.mkdir()
    for n, path in schema.migration_files():
        if n <= 6:
            (v6 / path.name).write_text(path.read_text())
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL((FIXTURES / "schema_baseline.sql").read_text()))  # type: ignore[arg-type]  # repo fixture
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
        assert schema.upgrade(c, v6)[-1] == 6
        _player(c)
        inside = record("inside", LONG)
        c.execute(
            "INSERT INTO chess_games (platform, platform_game_id, played_at, moves, fen_sequence, variant)"
            " VALUES ('lichess', 'inside', now(), %s::jsonb, %s::jsonb, 'standard'),"
            "        ('lichess', 'outside', now() - interval '1 year', NULL, NULL, 'standard')",
            (json.dumps(inside.moves), json.dumps(inside.fen_sequence)),
        )
        c960 = record("c960", ["e4", "e5"], variant="chess960")
        c.execute(
            "INSERT INTO chess_games (platform, platform_game_id, played_at, moves, fen_sequence, variant, starting_fen)"
            " VALUES ('lichess', 'c960', now(), %s::jsonb, %s::jsonb, 'chess960', %s)",
            (json.dumps(c960.moves), json.dumps(c960.fen_sequence), c960.fen_sequence[0]),
        )
        c.execute(
            "INSERT INTO review_pool_state (player_id, pool_id, last_shown_at) VALUES (1, 'v1:route:faded', now())"
        )
        c.commit()
        assert schema.upgrade(c) == [n for n, _ in schema.migration_files() if n > 6]
        got = _prefix(c, "inside")
        assert got["opening_moves"] == LONG[:OPENING_PREFIX_PLIES]
        assert got["opening_keys"] == got["window_keys"] and len(got["opening_keys"]) == OPENING_PREFIX_PLIES + 1
        assert _prefix(c, "outside")["opening_keys"] is None  # not recorded yet, for the backfill
        assert _prefix(c, "c960")["opening_keys"] is None and _prefix(c, "c960")["opening_moves"] is None
        # Between the two Review PRs the deployed worklist still reads and writes this table.
        assert list(read._pool_state(c)) == ["v1:route:faded"]  # type: ignore[reportPrivateUsage]


# --- the importer ---------------------------------------------------------------------------


def test_the_importer_writes_the_prefix_once_and_never_for_chess960(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    with conn.transaction():
        store_game(conn, record("g1", LONG))
        store_game(conn, record("short", ["e4", "e5", "Nf3"]))
        store_game(conn, record("c960", ["e4", "e5"], variant="chess960"))
    got = _prefix(conn, "g1")
    assert got["opening_moves"] == LONG[:OPENING_PREFIX_PLIES]
    assert got["opening_keys"] == got["window_keys"]
    short = _prefix(conn, "short")
    assert short["opening_moves"] == ["e4", "e5", "Nf3"] and len(short["opening_keys"]) == 4
    assert _prefix(conn, "c960")["opening_keys"] is None
    # A later write never replaces a recorded prefix (the NULL → value ratchet).
    upsert_game(conn, replace(record("g1", ["d4", "d5"])))
    again = _prefix(conn, "g1")
    assert again["opening_moves"] == LONG[:OPENING_PREFIX_PLIES] and again["opening_keys"] == got["opening_keys"]
    conn.commit()


def test_housekeeping_leaves_the_prefix_when_it_nulls_the_payload(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    with conn.transaction():
        for i in range(3):
            store_game(conn, record(f"h{i}", LONG, days_ago=i + 1))
    conn.commit()
    assert housekeeping.run(conn, window=1)["payload_nulled"] == 2
    old = _prefix(conn, "h2")
    assert old["bare"] is True and old["opening_moves"] == LONG[:OPENING_PREFIX_PLIES]
    assert old["opening_keys"] is not None and len(old["opening_keys"]) == OPENING_PREFIX_PLIES + 1


# --- the history boundary and the backfill --------------------------------------------------


def _store(conn: psycopg.Connection[DictRow], *records: GameRecord) -> None:
    with conn.transaction():
        for rec in records:
            store_game(conn, rec)


def _housekept(conn: psycopg.Connection[DictRow], *gids: str) -> None:
    """Stored before migration 007 and since housekept: no payload, no prefix."""
    conn.execute(
        "UPDATE chess_games SET moves = NULL, fen_sequence = NULL, opening_moves = NULL, opening_keys = NULL"
        " WHERE platform_game_id = ANY(%s)",
        (list(gids),),
    )
    conn.commit()


@pytest.mark.parametrize(
    ("newest", "months", "start"),
    [
        (datetime(2026, 10, 4, 12, tzinfo=UTC), 12, datetime(2025, 10, 4, 12, tzinfo=UTC)),  # the default year
        (datetime(2026, 3, 31, 10, tzinfo=UTC), 1, datetime(2026, 2, 28, 10, tzinfo=UTC)),  # a 31-day month
        (datetime(2024, 3, 31, 10, tzinfo=UTC), 1, datetime(2024, 2, 29, 10, tzinfo=UTC)),  # a leap February
        (datetime(2026, 7, 1, 0, 30, tzinfo=UTC), 2, datetime(2026, 5, 1, 0, 30, tzinfo=UTC)),  # across month starts
    ],
)
def test_the_history_starts_whole_calendar_months_before_the_newest_game(
    clean: psycopg.Connection[DictRow], newest: datetime, months: int, start: datetime
) -> None:
    conn = clean
    _player(conn)
    assert positions.history_start(conn, months) is None  # no game, no history
    _store(
        conn,
        record("n1", ["e4"], played_at=newest),
        record("c960", ["e4"], variant="chess960", played_at=newest + timedelta(days=9)),
    )
    assert positions.history_start(conn, months) == start  # a newer Chess960 game does not move it
    # Counted on the UTC calendar, whatever the session's time zone (New York's 31 March 06:00
    # would give 28 February 06:00 local, another instant).
    conn.execute("SELECT set_config('TimeZone', 'America/New_York', false)")
    assert positions.history_start(conn, months) == start


def _cc_game(gid: str, end: datetime, moves: str, **over: Any) -> dict[str, Any]:
    g: dict[str, Any] = {
        "url": f"https://www.chess.com/game/live/{gid}",
        "pgn": (
            f'[Event "Live Chess"]\n[Site "Chess.com"]\n[White "{ME}"]\n[Black "opp"]\n[Result "0-1"]\n'
            f'[TimeControl "600"]\n\n{moves} 0-1\n'
        ),
        "time_control": "600",
        "end_time": int(end.timestamp()),
        "rules": "chess",
        "white": {"rating": 1500, "result": "resigned", "username": ME},
        "black": {"rating": 1500, "result": "win", "username": "opp"},
    }
    g.update(over)
    return g


def _cc_record(game: dict[str, Any]) -> GameRecord:
    rec = chesscom.parse_game(game, ME)
    assert rec is not None
    return rec


def test_the_chesscom_backfill_fills_exactly_the_history_the_positions_count(
    clean: psycopg.Connection[DictRow],
) -> None:
    """Newest game 4 October 2026, 12 months: the history starts 4 October 2025 12:00. Games
    from 6 October and from that exact instant get their prefix and count; one a minute
    earlier is outside both."""
    conn = clean
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, %s)", (PLAYER_ID, ME))
    newest = datetime(2026, 10, 4, 12, tzinfo=UTC)
    start = datetime(2025, 10, 4, 12, tzinfo=UTC)
    early = [_cc_game(f"80{i}", datetime(2025, 10, 6, 9 + i, tzinfo=UTC), "1. d4 d5") for i in range(3)]
    edge = _cc_game("810", start, "1. d4 d5")
    before = _cc_game("811", start - timedelta(minutes=1), "1. d4 d5")
    kept = _cc_game("812", datetime(2025, 10, 7, tzinfo=UTC), "1. e4 e5")
    _store(conn, *(_cc_record(g) for g in (*early, edge, before, kept)), _cc_record(_cc_game("899", newest, "1. c4")))
    _housekept(conn, "800", "801", "802", "810", "811")
    kept_keys = _prefix(conn, "812")["opening_keys"]
    october = [
        *early,
        early[0],  # the same game twice in an archive counts once
        edge,
        before,
        _cc_game("812", datetime(2025, 10, 7, tzinfo=UTC), "1. d4 Nf6"),  # a prefix is never overwritten
        _cc_game("820", datetime(2025, 10, 8, tzinfo=UTC), "1. e4"),  # not stored: the importer's job
        _cc_game(
            "821",
            datetime(2025, 10, 8, tzinfo=UTC),
            "1. e4",
            rules="chess960",
            pgn=(
                f'[Event "Live Chess"]\n[White "{ME}"]\n[Black "opp"]\n[Result "0-1"]\n[Variant "Chess960"]\n'
                '[SetUp "1"]\n[FEN "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1"]\n\n1. e4 0-1\n'
            ),
        ),
    ]
    archives = {
        f"https://api.chess.com/pub/player/{ME}/games/2025/09": [
            _cc_game("790", datetime(2025, 9, 30, tzinfo=UTC), "1. d4")
        ],
        f"https://api.chess.com/pub/player/{ME}/games/2025/10": october,
    }
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        asked.append(url)
        if url.endswith("/games/archives"):
            return httpx.Response(200, json={"archives": list(archives)})
        return httpx.Response(200, json={"games": archives[url]})

    summary = backfill.backfill_openings(conn, months=12, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not any(u.endswith("2025/09") for u in asked)  # before the history's month
    assert summary == {
        "fetched": 8,  # the game a minute before the history is not even read
        "updated": 4,
        "already_had": 1,
        "not_in_db": 1,
        "unparseable": 0,
        "chess960": 1,
        "not_returned": 0,
        "failed": 0,
    }
    for gid in ("800", "801", "802", "810"):
        assert _prefix(conn, gid)["opening_moves"] == ["d4", "d5"], gid
        assert _prefix(conn, gid)["bare"] is True  # moves and fen_sequence stay NULL
    assert _prefix(conn, "811")["opening_keys"] is None  # one minute before the history
    assert _prefix(conn, "812")["opening_keys"] == kept_keys
    # The reader agrees with the backfill: four games reach 1.d4 d5 inside the history.
    _, rows = positions.eval_candidates(conn, _config(review_position_min_games=3), None)
    after_d4 = _key(conn, moves_to_fen_sequence(["d4"])[1])
    assert {r["key"]: r["n"] for r in rows}.get(after_d4) == 4
    again = backfill.backfill_openings(conn, months=12, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert again["updated"] == 0


def _lichess_game(gid: str, moves: list[str], created: datetime, last: datetime, **over: Any) -> dict[str, Any]:
    g: dict[str, Any] = {
        "id": gid,
        "rated": True,
        "variant": "standard",
        "speed": "correspondence",
        "perf": "correspondence",
        "createdAt": int(created.timestamp() * 1000),
        "lastMoveAt": int(last.timestamp() * 1000),
        "status": "resign",
        "players": {
            "white": {"user": {"name": "Opp", "id": "opp"}, "rating": 1600},
            "black": {"user": {"name": ME, "id": ME}, "rating": 1550},
        },
        "winner": "white",
        "moves": " ".join(moves),
    }
    g.update(over)
    return g


def test_the_lichess_backfill_fetches_the_missing_games_by_id_whatever_their_creation_time(
    clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correspondence game created before the history and finished inside it is fetched: by
    id, not by a creation-time stream."""
    conn = clean
    conn.execute("INSERT INTO players (id, lichess_username) VALUES (%s, %s)", (PLAYER_ID, ME))
    newest = datetime(2026, 10, 4, 12, tzinfo=UTC)
    games = {
        "corr0001": _lichess_game(
            "corr0001", LONG, datetime(2025, 9, 1, tzinfo=UTC), datetime(2025, 11, 20, tzinfo=UTC)
        ),
        "bad00001": _lichess_game(
            "bad00001", ["e4", "e5"], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
        ),
        "gone0001": _lichess_game(
            "gone0001", ["e4"], datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
        ),
        "old00001": _lichess_game(
            "old00001", ["e4"], datetime(2025, 9, 1, tzinfo=UTC), datetime(2025, 10, 1, tzinfo=UTC)
        ),
    }
    stored = [lichess.parse_game(g, ME) for g in games.values()]
    _store(conn, *(r for r in stored if r is not None), record("newest01", ["e4"], played_at=newest))
    _store(conn, record("c9600001", ["e4"], variant="chess960", played_at=datetime(2026, 3, 1, tzinfo=UTC)))
    _housekept(conn, *games)
    games["bad00001"]["moves"] = "e4 Ke9"  # what the platform answers no longer parses
    del games["gone0001"]  # and this one it no longer returns
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/api/games/export/_ids", request.url
        ids = request.content.decode().split(",")
        requests.append(ids)
        body = "\n".join(json.dumps(games[i]) for i in ids if i in games)
        return httpx.Response(200, content=body.encode())

    monkeypatch.setattr(lichess, "IDS_PER_REQUEST", 2)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    summary = backfill.backfill_openings(conn, months=12, client=client)
    # Asked: the stored games inside the history (from 4 October 2025) without a prefix, oldest
    # first. Not the one finished on 1 October, not the newest (it has one), not Chess960.
    assert requests == [["corr0001", "bad00001"], ["gone0001"]]
    assert summary == {
        "fetched": 2,
        "updated": 1,
        "already_had": 0,
        "not_in_db": 0,
        "unparseable": 1,
        "chess960": 0,
        "not_returned": 1,
        "failed": 0,
    }
    corr = _prefix(conn, "corr0001")
    assert corr["bare"] is True and corr["opening_moves"] == LONG[:OPENING_PREFIX_PLIES]
    expected = conn.execute(
        "SELECT bq_opening_keys(%s::jsonb) AS k", (json.dumps(moves_to_fen_sequence(LONG)),)
    ).fetchone()
    assert expected and corr["opening_keys"] == expected["k"]
    assert _prefix(conn, "old00001")["opening_keys"] is None
    requests.clear()
    again = backfill.backfill_openings(conn, months=12, client=client)
    assert again["updated"] == 0 and requests == [["bad00001", "gone0001"]]  # only what is still missing


def test_a_platform_that_fails_fails_the_backfill(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    conn.execute("INSERT INTO players (id, lichess_username) VALUES (%s, %s)", (PLAYER_ID, ME))
    _store(conn, record("ok000001", ["e4"]), record("bare0001", ["d4"], days_ago=3))
    _housekept(conn, "bare0001")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    assert backfill.backfill_openings(conn, months=12, client=client)["failed"] == 1


# --- position evaluations -------------------------------------------------------------------


def _config(**over: Any) -> Settings:
    return Settings(
        **{"review_position_min_games": 3, "review_position_max_ply": 6, "review_history_months": 12, **over}
    )


class _Stub:
    """An engine that remembers what it was asked and answers a fixed score."""

    def __init__(self) -> None:
        self.boards: list[str] = []

    @contextmanager
    def __call__(self) -> Iterator[evals.Analyser]:
        def analyse(board: chess.Board) -> evals.Score:
            self.boards.append(board.fen())
            return (35, None) if board.turn == chess.WHITE else (None, -3)

        yield analyse


def _games(conn: psycopg.Connection[DictRow], prefix: str, moves: list[str], n: int, **kw: Any) -> None:
    with conn.transaction():
        for i in range(n):
            store_game(conn, record(f"{prefix}{i}", moves, **kw))


def _evaluated(conn: psycopg.Connection[DictRow]) -> dict[int, dict[str, Any]]:
    return {r["board_key"]: dict(r) for r in conn.execute("SELECT * FROM position_evals")}


def _key(conn: psycopg.Connection[DictRow], fen: str) -> int:
    row = conn.execute("SELECT bq_position_key(%s) AS k", (fen,)).fetchone()
    assert row is not None
    return int(row["k"])


def test_position_evals_evaluates_exactly_the_counted_boards(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    scandi: list[str] = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5", "d4", "Nf6"]
    _games(conn, "s", scandi, 3)  # three games: boards at plies 1..6 qualify (min 3, max ply 6)
    _games(conn, "r", ["e4", "e5"], 2)  # 1...e5 reached twice: below the floor
    _games(conn, "old", ["d4", "d5"], 3, days_ago=500)  # outside the 12 months before the newest game
    conn.commit()
    stub = _Stub()
    out = evals.run(conn, _config(), limit=None, engine=stub)
    fens = moves_to_fen_sequence(scandi)
    want = {_key(conn, fens[p]) for p in range(1, 7)}  # 1.e4 is reached by 3 + 2 + 2 games: one board
    assert out == {"pending": 6, "evaluated": 6, "failed": 0, "fallbacks": 0}
    rows = _evaluated(conn)
    assert set(rows) == want
    after_e4 = rows[_key(conn, fens[1])]
    assert after_e4["eval_cp"] is None and after_e4["mate_in"] == -3 and after_e4["depth"] == STOCKFISH_DEPTH
    assert after_e4["fen"] == fens[1]
    assert evals.run(conn, _config(), limit=None, engine=stub) == {
        "pending": 0,
        "evaluated": 0,
        "failed": 0,
        "fallbacks": 0,
    }


def test_position_evals_takes_the_most_played_first_and_respects_the_limit(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    _games(conn, "a", ["e4", "c5"], 5)
    _games(conn, "b", ["e4", "e5"], 3)
    conn.commit()
    out = evals.run(conn, _config(), limit=1, engine=_Stub())
    assert out == {"pending": 3, "evaluated": 1, "failed": 0, "fallbacks": 0}
    first = _key(conn, moves_to_fen_sequence(["e4"])[1])  # 8 games
    assert set(_evaluated(conn)) == {first}
    assert evals.run(conn, _config(), limit=1, engine=_Stub())["pending"] == 2


def test_a_repeated_board_is_one_occurrence_and_the_start_is_never_a_position(
    clean: psycopg.Connection[DictRow],
) -> None:
    conn = clean
    _player(conn)
    shuffle: list[str] = ["Nf3", "Nf6", "Ng1", "Ng8"] * 3
    _games(conn, "k", shuffle, 3)
    conn.commit()
    _, rows = positions.eval_candidates(conn, _config(review_position_max_ply=12), None)
    fens = moves_to_fen_sequence(shuffle)
    assert {r["key"] for r in rows} == {_key(conn, fens[p]) for p in (1, 2, 3)}  # never fens[0], nor twice
    assert {r["sources"][0]["ply"] for r in rows} == {1, 2, 3}  # each board at its first occurrence
    assert all(r["n"] == 3 for r in rows)


def test_a_board_is_rebuilt_from_the_next_game_and_fails_only_when_none_rebuilds(
    clean: psycopg.Connection[DictRow],
) -> None:
    conn = clean
    _player(conn)
    _games(conn, "e", ["e4", "e5"], 4)
    conn.commit()
    ids = [r["id"] for r in conn.execute("SELECT id FROM chess_games ORDER BY id")]
    after_e4 = _key(conn, moves_to_fen_sequence(["e4"])[1])

    def broken(game_ids: list[int], moves: list[str]) -> dict[str, Any]:
        conn.execute(
            "UPDATE chess_games SET opening_moves = %s::jsonb WHERE id = ANY(%s)", (json.dumps(moves), game_ids)
        )
        conn.execute("DELETE FROM position_evals")
        conn.commit()
        return evals.run(conn, _config(), limit=None, engine=_Stub())

    # The lowest-id game no longer replays (a different move, then the null move): the next one does.
    for moves in (["d4", "e5"], ["--", "e5"]):
        assert broken(ids[:1], moves) == {"pending": 2, "evaluated": 2, "failed": 0, "fallbacks": 2}, moves
        assert _evaluated(conn)[after_e4]["fen"] == moves_to_fen_sequence(["e4"])[1]
    # No source within reach rebuilds it: counted, nothing written, the step fails.
    out = broken(ids[: positions.EVAL_SOURCES], ["d4", "e5"])
    assert out["failed"] == 2 and after_e4 not in _evaluated(conn)


def test_a_board_counts_per_colour(clean: psycopg.Connection[DictRow]) -> None:
    """Two games as White and two as Black reach 1.c4 c5: twice per colour, under a floor of 3."""
    conn = clean
    _player(conn)
    _games(conn, "w", ["c4", "c5"], 2, color="white")
    _games(conn, "b", ["c4", "c5"], 2, color="black")
    conn.commit()
    assert positions.eval_candidates(conn, _config(), None) == (0, [])
    _games(conn, "x", ["c4", "c5"], 1, color="black")
    conn.commit()
    pending, rows = positions.eval_candidates(conn, _config(), None)
    assert pending == 2 and {r["n"] for r in rows} == {3}


def test_ties_go_to_the_lower_key_and_a_limit_of_zero_is_all(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    for i, first in enumerate(("e4", "d4", "c4", "Nf3")):
        _games(conn, f"t{i}_", [first], 3)
    conn.commit()
    _, rows = positions.eval_candidates(conn, _config(), None)
    keys = [r["key"] for r in rows]
    assert keys == sorted(keys) and len(keys) == 4
    _, first_two = positions.eval_candidates(conn, _config(), 2)  # the limit keeps the lower keys
    assert [r["key"] for r in first_two] == keys[:2]
    assert evals.run(conn, _config(), limit=0, engine=_Stub())["evaluated"] == 4
    with pytest.raises(ValueError):
        evals.run(conn, _config(), limit=-1, engine=_Stub())


def test_the_engine_score_is_stored_from_whites_point_of_view() -> None:
    from chess.engine import Cp, Mate, PovScore

    assert evals._score({"score": PovScore(Cp(50), chess.BLACK)}) == (-50, None)  # type: ignore[reportPrivateUsage]
    assert evals._score({"score": PovScore(Mate(2), chess.BLACK)}) == (None, -2)  # type: ignore[reportPrivateUsage]
    assert evals._score({"score": PovScore(Cp(-30), chess.WHITE)}) == (-30, None)  # type: ignore[reportPrivateUsage]
    assert evals._score({}) is None  # type: ignore[reportPrivateUsage]


def test_position_evals_runs_last_in_the_hour() -> None:
    """After review, and after housekeeping, so a board that fails holds nothing else up."""
    steps = list(runs.HOURLY_STEPS)
    assert steps[-1] == "position-evals" and steps.index("review") < steps.index("housekeep")
    assert runs.CHAIN_THROUGH_STEP == "housekeep"  # its failure does not make the chain look stale
    from pipeline.cli import hourly_steps

    assert list(hourly_steps()) == steps


def test_replay_refuses_what_is_not_a_legal_move() -> None:
    assert evals.replay(["e4", "e5"], 2) is not None
    assert evals.replay(["e4"], 2) is None
    for bad in ("--", "Z0", "0000", "Ke2"):
        assert evals.replay(["e4", bad], 2) is None, bad


@pytest.mark.parametrize("ply", [0, 1])
def test_replay_counts_plies_from_the_start(ply: int) -> None:
    board = evals.replay(["e4"], ply)
    assert board is not None and len(board.move_stack) == ply
