"""Review's data: the opening prefix (migration 007, the importer, the backfill) and the
position evaluations (`pipeline position-evals`).

The prefix outlives the analysis window, is written once and never overwritten, and is
never given to a variant the pipeline does not analyse. The evaluation step evaluates exactly
the boards Review's opening mistakes read, each rebuilt from one game's prefix and checked
against its key, refuses (counts `failed`) a board that does not rebuild, and gives the engine
the canonical board, never the replayed one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, LiteralString, cast

import chess
import httpx
import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from core import housekeeping, runs, schema
from core.chess.board import moves_to_fen_sequence
from core.constants import ANALYSABLE_VARIANTS, OPENING_PREFIX_PLIES, PLAYER_ID, STOCKFISH_DEPTH, STOCKFISH_STAMP
from core.ingest import backfill, chesscom, lichess
from core.ingest.records import GameRecord
from core.ingest.store import store_game, upsert_game
from core.review import evals, mistakes, positions
from core.settings import Settings
from tests.conftest import stockfish_skip

ME = "player_test"
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
        assert schema.upgrade(c, to=7) == [7]
        got = _prefix(c, "inside")
        assert got["opening_moves"] == LONG[:OPENING_PREFIX_PLIES]
        assert got["opening_keys"] == got["window_keys"] and len(got["opening_keys"]) == OPENING_PREFIX_PLIES + 1
        assert _prefix(c, "outside")["opening_keys"] is None  # not recorded yet, for the backfill
        assert _prefix(c, "c960")["opening_keys"] is None and _prefix(c, "c960")["opening_moves"] is None
        # Between the two Review releases the deployed worklist still reads and writes this table.
        assert c.execute("SELECT pool_id FROM review_pool_state").fetchall() == [{"pool_id": "v1:route:faded"}]
        # The snapshot table goes on before the new page is deployed; the old page is untouched.
        assert schema.upgrade(c, to=8) == [8]
        assert c.execute("SELECT to_regclass('public.review_snapshots') IS NOT NULL AS t").fetchone() == {"t": True}
        assert c.execute("SELECT pool_id FROM review_pool_state").fetchall() == [{"pool_id": "v1:route:faded"}]
        # The page that no longer reads it is live before the table goes, and nothing else goes with it.
        assert schema.upgrade(c) == [n for n, _ in schema.migration_files() if n > 8]
        assert c.execute("SELECT to_regclass('public.review_pool_state') AS t").fetchone() == {"t": None}
        assert _prefix(c, "inside")["opening_moves"] == LONG[:OPENING_PREFIX_PLIES]


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
    _, rows = _candidates(conn, _config())
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
#
# The candidate set and the engine's input are core.review.mistakes / core.review.evals; these
# tests store real games through the importer. The pricing and the section are in
# tests/review/test_mistakes.py.


def _config(**over: Any) -> Settings:
    return Settings(**{"review_position_max_ply": 6, "review_history_months": 12, **over})


class _Stub:
    """An engine that remembers what it was asked and answers a fixed score and the first legal
    move. Picklable, so it also runs in worker processes."""

    def __init__(self) -> None:
        self.boards: list[tuple[str, int]] = []

    @contextmanager
    def __call__(self) -> Iterator[evals.Analyser]:
        def analyse(board: chess.Board) -> evals.Score:
            self.boards.append((board.fen(), len(board.move_stack)))
            best = board.san(next(iter(board.legal_moves)))
            return (35, None, best) if board.turn == chess.WHITE else (None, -3, best)

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


def _candidates(
    conn: psycopg.Connection[DictRow], config: Settings, limit: int | None = None
) -> tuple[int, list[dict[str, Any]]]:
    return mistakes.eval_candidates(conn, config.review_position_max_ply, config.review_history_months, limit)


def _canonical(fen: str) -> str:
    return " ".join(fen.split(" ")[:4] + ["0", "1"])


def test_position_evals_evaluates_the_boards_before_and_after_the_players_decisions(
    clean: psycopg.Connection[DictRow],
) -> None:
    conn = clean
    _player(conn)
    scandi: list[str] = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5", "d4", "Nf6"]
    _games(conn, "s", scandi, 3)  # The player is Black: they move at plies 1, 3, 5 (max ply 6)
    _games(conn, "r", ["e4", "e5"], 2)  # 1...e5 from the same board: its after-board comes too
    _games(conn, "w", ["d4", "Nf6"], 2, color="white")  # The player moved from the start in two games only
    _games(conn, "old", ["c4", "c5"], 3, days_ago=500)  # outside the 12 months before the newest game
    conn.commit()
    stub = _Stub()
    out = evals.run(conn, _config(), limit=None, engine=stub)
    fens = moves_to_fen_sequence(scandi)
    want = {_key(conn, fens[p]) for p in range(1, 7)} | {_key(conn, moves_to_fen_sequence(["e4", "e5"])[2])}
    assert out == {"pending": 7, "evaluated": 7, "terminal": 0, "failed": 0, "fallbacks": 0}
    rows = _evaluated(conn)
    assert set(rows) == want
    after_e4 = rows[_key(conn, fens[1])]
    assert after_e4["eval_cp"] is None and after_e4["mate_in"] == -3 and after_e4["depth"] == STOCKFISH_DEPTH
    assert after_e4["best_move"] is not None and after_e4["terminal"] is None
    assert after_e4["fen"] == _canonical(fens[1])
    # The engine only ever saw canonical boards: clocks 0 1, no move history.
    assert all(fen.endswith(" 0 1") and stack == 0 for fen, stack in stub.boards)
    assert evals.run(conn, _config(), limit=None, engine=stub) == {
        "pending": 0,
        "evaluated": 0,
        "terminal": 0,
        "failed": 0,
        "fallbacks": 0,
    }


def test_a_board_from_different_games_is_stored_the_same_whichever_game_rebuilt_it(
    clean: psycopg.Connection[DictRow],
) -> None:
    """The board after 1.e4 e5 is reached directly and after two knight trips (another halfmove
    clock, another move history): the stored row is identical whichever game is the source."""
    conn = clean
    _player(conn)
    direct: list[str] = ["e4", "e5", "Nf3"]
    trip: list[str] = ["e4", "e5", "Nf3", "Nc6", "Ng1", "Nb8", "Nf3"]
    rows: list[dict[str, Any]] = []
    for order in ((trip, direct), (direct, trip)):
        conn.execute("DELETE FROM player_games")
        conn.execute("DELETE FROM chess_games")
        conn.execute("DELETE FROM position_evals")
        conn.commit()
        _games(conn, "a", order[0], 3, color="white")
        _games(conn, "b", order[1], 3, color="white")
        conn.commit()
        stub = _Stub()
        evals.run(conn, _config(), limit=None, engine=stub)
        key = _key(conn, moves_to_fen_sequence(direct)[2])
        row = _evaluated(conn)[key]
        del row["computed_at"]
        rows.append(row)
    assert rows[0] == rows[1]
    assert rows[0]["fen"] == _canonical(moves_to_fen_sequence(direct)[2])


def test_a_terminal_board_is_stored_from_the_board_and_never_asked_again(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    mate: list[str] = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    _games(conn, "m", mate, 3, color="white")
    conn.commit()
    stub = _Stub()
    out = evals.run(conn, _config(review_position_max_ply=8), limit=None, engine=stub)
    mated = _key(conn, moves_to_fen_sequence(mate)[7])
    row = _evaluated(conn)[mated]
    assert (row["terminal"], row["eval_cp"], row["mate_in"], row["best_move"]) == ("checkmate", None, None, None)
    assert out["terminal"] == 1 and out["evaluated"] == 7 and out["failed"] == 0
    assert not any(fen.startswith(row["fen"].split(" ")[0]) for fen, _ in stub.boards)  # never sent to the engine
    assert _candidates(conn, _config(review_position_max_ply=8)) == (0, [])


STALEMATE: list[str] = [
    str(m) for m in "e3 a5 Qh5 Ra6 Qxa5 h5 h4 Rah6 Qxc7 f6 Qxd7+ Kf7 Qxb7 Qd3 Qxb8 Qh7 Qxc8 Kg6 Qe6".split()
]


def test_a_stored_stalemate_without_a_best_move_is_converted_in_one_write(clean: psycopg.Connection[DictRow]) -> None:
    """A row written before migration 010 for a stalemate (the engine scores it 0 and has no
    move): the catch-up re-reads it, and the whole row becomes a terminal draw at once."""
    conn = clean
    _player(conn)
    _games(conn, "st", STALEMATE, 3, color="white")
    conn.commit()
    fens = moves_to_fen_sequence(STALEMATE)
    stalemate = _key(conn, fens[19])
    conn.execute(
        "INSERT INTO position_evals (board_key, fen, eval_cp, depth) VALUES (%s, %s, 0, 18)", (stalemate, fens[19])
    )
    conn.commit()
    evals.run(conn, _config(review_position_max_ply=24), limit=None, engine=_Stub())
    row = _evaluated(conn)[stalemate]
    assert (row["terminal"], row["eval_cp"], row["mate_in"], row["best_move"]) == ("draw", None, None, None)


def test_a_repetition_is_never_a_terminal_fact_of_a_board() -> None:
    """Four knight trips are an automatic fivefold draw when the game is replayed; the same key,
    built from its position alone, is not over."""
    shuffle: list[str] = ["Nf3", "Nf6", "Ng1", "Ng8"] * 4
    replayed = evals.replay(shuffle, 16)
    assert replayed is not None and replayed.is_game_over()
    board = evals.canonical(replayed)
    assert evals.terminal_of(board) is None and not board.move_stack and board.fen().endswith(" 0 1")
    assert evals.terminal_of(chess.Board("8/8/8/8/8/2k5/8/2K5 w - - 0 1")) == "draw"  # insufficient material
    assert evals.terminal_of(chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")) == "draw"  # stalemate
    assert evals.terminal_of(chess.Board("7k/7Q/6K1/8/8/8/8/8 b - - 0 1")) == "checkmate"


def test_the_evaluated_depth_follows_the_ranked_depth(clean: psycopg.Connection[DictRow]) -> None:
    """Raising `review_position_max_ply` makes exactly the deeper boards candidates (the board
    after the last decision included); lowering it removes nothing."""
    conn = clean
    _player(conn)
    _games(conn, "l", LONG, 3)  # The player is Black: decisions at odd plies
    conn.commit()
    fens = moves_to_fen_sequence(LONG)
    at = {p: _key(conn, fens[p]) for p in range(0, 31)}
    evals.run(conn, _config(review_position_max_ply=24), limit=None, engine=_Stub())
    first = set(_evaluated(conn))
    assert first == {at[p] for p in range(1, 25)}  # boards before (odd) and after (even) plies 1..23, and 24
    pending, rows = _candidates(conn, _config(review_position_max_ply=30))
    assert pending == 6 and {r["key"] for r in rows} == {at[p] for p in range(25, 31)}  # ply 30 = after ply 29
    evals.run(conn, _config(review_position_max_ply=30), limit=None, engine=_Stub())
    evals.run(conn, _config(review_position_max_ply=4), limit=None, engine=_Stub())
    assert set(_evaluated(conn)) == {at[p] for p in range(1, 31)}


def test_position_evals_takes_the_most_played_first_and_respects_the_limit(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    _games(conn, "a", ["e4", "c5"], 5)
    _games(conn, "b", ["e4", "e5"], 3)
    conn.commit()
    out = evals.run(conn, _config(), limit=1, engine=_Stub())
    assert out == {"pending": 3, "evaluated": 1, "terminal": 0, "failed": 0, "fallbacks": 0}
    first = _key(conn, moves_to_fen_sequence(["e4"])[1])  # 8 games
    assert set(_evaluated(conn)) == {first}
    assert evals.run(conn, _config(), limit=1, engine=_Stub())["pending"] == 2


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
        assert broken(ids[:1], moves) == {"pending": 2, "evaluated": 2, "terminal": 0, "failed": 0, "fallbacks": 2}
        assert _evaluated(conn)[after_e4]["fen"] == _canonical(moves_to_fen_sequence(["e4"])[1])
    # The first three fail and the fourth replays: the bound is well past three.
    assert mistakes.EVAL_SOURCES > 3
    assert broken(ids[:3], ["d4", "e5"]) == {"pending": 2, "evaluated": 2, "terminal": 0, "failed": 0, "fallbacks": 2}
    # No source within reach rebuilds it: counted, nothing written, the step fails.
    out = broken(ids[: mistakes.EVAL_SOURCES], ["d4", "e5"])
    assert out["failed"] == 2 and after_e4 not in _evaluated(conn)


def test_a_board_another_engine_scored_is_pending_again_but_a_terminal_one_never(
    clean: psycopg.Connection[DictRow],
) -> None:
    """Rows carry the stamp of the engine that wrote them. Pending: no row, a row with no result,
    a scored row stamped by another engine, a scored row with no stamp (written before the stamp
    was recorded). Not pending: a scored row by this engine, a terminal row whatever its stamp."""
    conn = clean
    _player(conn)
    line = ["e4", "e5", "Nf3"]
    _games(conn, "s", line, 3, color="white")
    conn.commit()
    fens = moves_to_fen_sequence(line)
    k = [_key(conn, f) for f in fens]  # boards after 0..3 plies; the player moves from 0 and 2
    pending, _ = _candidates(conn, _config())
    assert pending == 4
    write = (
        "INSERT INTO position_evals (board_key, fen, eval_cp, best_move, terminal, depth, engine)"
        " VALUES (%s, %s, %s, %s, %s, 18, %s)"
    )
    conn.execute(write, (k[0], fens[0], 20, "e4", None, STOCKFISH_STAMP))  # this engine: done
    conn.execute(write, (k[1], fens[1], 20, "e5", None, "stockfish_0"))  # another engine: pending
    conn.execute(write, (k[2], fens[2], 20, "Nf3", None, None))  # before the stamp: pending
    conn.execute(write, (k[3], fens[3], None, None, "draw", "stockfish_0"))  # terminal: never
    conn.commit()
    pending, rows = _candidates(conn, _config())
    assert (pending, {r["key"] for r in rows}) == (2, {k[1], k[2]})
    out = evals.run(conn, _config(), limit=None, engine=_Stub())
    assert out["evaluated"] == 2 and out["failed"] == 0
    rows_after = _evaluated(conn)
    assert {key: rows_after[key]["engine"] for key in k} == {
        k[0]: STOCKFISH_STAMP,
        k[1]: STOCKFISH_STAMP,
        k[2]: STOCKFISH_STAMP,
        k[3]: "stockfish_0",
    }
    assert rows_after[k[3]]["terminal"] == "draw"
    assert _candidates(conn, _config()) == (0, [])


def test_a_board_counts_per_colour(clean: psycopg.Connection[DictRow]) -> None:
    """The player moved from each board twice as one colour: under the floor of 3, nothing. A third
    Black game brings in the boards Black moves from and the boards their moves led to (the board
    after 1...c5 among them, though as White they moved from it only twice)."""
    conn = clean
    _player(conn)
    line: list[str] = ["c4", "c5", "Nc3", "Nc6"]
    _games(conn, "w", line[:3], 2, color="white")
    _games(conn, "b", line, 2, color="black")
    conn.commit()
    assert _candidates(conn, _config()) == (0, [])
    _games(conn, "x", line, 1, color="black")
    conn.commit()
    fens = moves_to_fen_sequence(line)
    pending, rows = _candidates(conn, _config())
    assert pending == 4 and {r["key"] for r in rows} == {_key(conn, fens[p]) for p in (1, 2, 3, 4)}


def test_ties_go_to_the_lower_key_and_a_limit_of_zero_is_all(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    for i, first in enumerate(("e4", "d4", "c4", "Nf3")):
        _games(conn, f"t{i}_", [first, "a6"], 3)
    conn.commit()
    _, rows = _candidates(conn, _config())
    keys = [r["key"] for r in rows if r["n"] == 3]
    assert keys == sorted(keys) and len(keys) == 8
    _, first_two = _candidates(conn, _config(), 2)
    assert [r["key"] for r in first_two] == [r["key"] for r in rows][:2]
    assert evals.run(conn, _config(), limit=0, engine=_Stub())["evaluated"] == 8
    with pytest.raises(ValueError):
        evals.run(conn, _config(), limit=-1, engine=_Stub())


def test_workers_evaluate_the_same_rows_as_one_engine(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    _games(conn, "s", ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5"], 3)
    conn.commit()
    serial = evals.run(conn, _config(), limit=None, engine=_Stub())
    one = {k: (r["eval_cp"], r["mate_in"], r["best_move"], r["fen"]) for k, r in _evaluated(conn).items()}
    conn.execute("DELETE FROM position_evals")
    conn.commit()
    pooled = evals.run(conn, _config(), limit=None, engine=_Stub(), workers=2)
    assert pooled == serial
    assert {k: (r["eval_cp"], r["mate_in"], r["best_move"], r["fen"]) for k, r in _evaluated(conn).items()} == one


def test_the_engine_score_is_stored_from_whites_point_of_view_with_its_best_move() -> None:
    from chess.engine import Cp, Mate, PovScore

    board = chess.Board()
    e4 = chess.Move.from_uci("e2e4")
    score = evals._score  # type: ignore[reportPrivateUsage]
    assert score({"score": PovScore(Cp(50), chess.BLACK), "pv": [e4]}, board) == (-50, None, "e4")
    assert score({"score": PovScore(Mate(2), chess.BLACK)}, board) == (None, -2, None)
    assert score({"score": PovScore(Cp(-30), chess.WHITE), "pv": []}, board) == (-30, None, None)
    assert score({}, board) is None


def test_an_answer_without_a_move_is_a_failure_not_a_row(clean: psycopg.Connection[DictRow]) -> None:
    """A live board always has a move; a row without one would stay pending for ever."""
    conn = clean
    _player(conn)
    _games(conn, "e", ["e4", "e5"], 3)
    conn.commit()

    @contextmanager
    def no_move() -> Iterator[evals.Analyser]:
        yield lambda board: (20, None, None)

    out = evals.run(conn, _config(), limit=None, engine=no_move)
    assert out["failed"] == 2 and out["evaluated"] == 0 and _evaluated(conn) == {}
    assert [f.split(": ", 1)[1] for f in out["failures"]] == ["no move or no score"] * 2


class _Broken:
    """An engine that raises inside a worker process: the run counts it and names its class."""

    @contextmanager
    def __call__(self) -> Iterator[evals.Analyser]:
        def analyse(board: chess.Board) -> evals.Score:
            raise RuntimeError("engine gone")

        yield analyse


def test_a_worker_failure_is_counted_with_its_class(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    _games(conn, "e", ["e4", "e5"], 3)
    conn.commit()
    out = evals.run(conn, _config(), limit=None, engine=_Broken(), workers=2)
    assert out["failed"] == 2 and _evaluated(conn) == {}
    assert all(f.endswith("RuntimeError") for f in out["failures"]) and "engine gone" not in str(out)


def test_position_evals_runs_last_in_the_hour() -> None:
    """After review, and after housekeeping, so a board that fails holds nothing else up."""
    steps = list(runs.HOURLY_STEPS)
    assert steps[-2:] == ["position-evals", "review-snapshot"] and steps.index("review") < steps.index("housekeep")
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


def test_migration_010_converts_a_stored_mate_zero_and_admits_exactly_two_kinds_of_row(
    fresh_db_url: str, tmp_path: Path
) -> None:
    """A version-9 database with a centipawn row, a mate row and a `mate 0` row (the engine's
    word for a checkmated side to move): the upgrade keeps the first two and makes the third a
    terminal checkmate; afterwards the table refuses every other combination."""
    from psycopg import sql

    from tests.test_schema import FIXTURES, _scratch

    url = _scratch(fresh_db_url, "evals_v9")
    v9 = tmp_path / "v9"
    v9.mkdir()
    for n, path in schema.migration_files():
        if n <= 9:
            (v9 / path.name).write_text(path.read_text())
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL((FIXTURES / "schema_baseline.sql").read_text()))  # type: ignore[arg-type]  # repo fixture
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
        assert schema.upgrade(c, v9)[-1] == 9
        c.execute(
            "INSERT INTO position_evals (board_key, fen, eval_cp, mate_in, depth)"
            " VALUES (1, 'a', 35, NULL, 18), (2, 'b', NULL, -3, 18), (3, 'c', NULL, 0, 18)"
        )
        c.commit()
        assert schema.upgrade(c, to=10) == [10]
        rows = c.execute(
            "SELECT board_key, eval_cp, mate_in, best_move, terminal FROM position_evals ORDER BY board_key"
        ).fetchall()
        assert [tuple(r.values()) for r in rows] == [
            (1, 35, None, None, None),
            (2, None, -3, None, None),
            (3, None, None, None, "checkmate"),
        ]
        c.execute("INSERT INTO position_evals (board_key, fen, depth, terminal) VALUES (4, 'd', 18, 'draw')")
        c.execute(
            "UPDATE position_evals SET terminal = 'draw', eval_cp = NULL, mate_in = NULL, best_move = NULL"
            " WHERE board_key = 1"
        )
        c.commit()
        refused = [
            "(5, 'e', 10, NULL, NULL, 'checkmate')",  # terminal with a score
            "(6, 'f', NULL, NULL, 'Nf3', 'draw')",  # terminal with a best move
            "(7, 'g', NULL, NULL, NULL, NULL)",  # neither a score nor terminal
            "(8, 'h', 10, 2, 'Nf3', NULL)",  # two scores
            "(9, 'i', NULL, NULL, NULL, 'resigned')",  # not a terminal kind
        ]
        for values in refused:
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(
                    cast(
                        LiteralString,
                        "INSERT INTO position_evals (board_key, fen, eval_cp, mate_in, best_move, terminal, depth)"
                        " VALUES " + values[:-1] + ", 18)",
                    )
                )
            c.rollback()


def test_migration_012_leaves_every_existing_row_pending_but_a_terminal_one(fresh_db_url: str, tmp_path: Path) -> None:
    """A version-11 database with a scored row and a terminal row, both written before the
    engine was recorded: after the upgrade both carry no stamp, the scored one is pending again
    (NULL is "another engine", never "none") and the terminal one is not; the next write stamps.
    The games are real so the candidate query has boards to ask about."""
    from psycopg import sql

    from tests.test_schema import FIXTURES, _scratch

    url = _scratch(fresh_db_url, "evals_v11")
    v11 = tmp_path / "v11"
    v11.mkdir()
    for n, path in schema.migration_files():
        if n <= 11:
            (v11 / path.name).write_text(path.read_text())
    line = ["e4", "e5", "Nf3"]
    fens = moves_to_fen_sequence(line)
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL((FIXTURES / "schema_baseline.sql").read_text()))  # type: ignore[arg-type]  # repo fixture
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
        assert schema.upgrade(c, v11)[-1] == 11
        _player(c)
        _games(c, "m", line, 3, color="white")
        c.commit()
        keys = [_key(c, f) for f in fens]
        c.execute(
            "INSERT INTO position_evals (board_key, fen, eval_cp, best_move, terminal, depth)"
            " VALUES (%s, %s, 20, 'e4', NULL, 18), (%s, %s, NULL, NULL, 'draw', 18)",
            (keys[0], fens[0], keys[3], fens[3]),
        )
        c.commit()
        assert schema.upgrade(c, to=12) == [12]
        stamps = c.execute("SELECT board_key, engine FROM position_evals ORDER BY board_key").fetchall()
        assert [r["engine"] for r in stamps] == [None, None]
        pending, rows = _candidates(c, _config())
        assert pending == 3 and keys[0] in {r["key"] for r in rows} and keys[3] not in {r["key"] for r in rows}
        out = evals.run(c, _config(), limit=None, engine=_Stub())
        assert out["evaluated"] == 3 and out["failed"] == 0
        after = {r["board_key"]: r["engine"] for r in c.execute("SELECT board_key, engine FROM position_evals")}
        assert after[keys[0]] == STOCKFISH_STAMP and after[keys[3]] is None
        assert _candidates(c, _config()) == (0, [])


class _NoStart:
    """An engine that cannot start (Stockfish missing, or dying in its UCI handshake)."""

    def __call__(self) -> Any:
        raise FileNotFoundError("secret-looking message that must never be printed")


class _Dies:
    """An engine whose worker process dies mid-search."""

    @contextmanager
    def __call__(self) -> Iterator[evals.Analyser]:
        def analyse(board: chess.Board) -> evals.Score:
            import os

            os._exit(3)

        yield analyse


_RUN_IN_CHILD = """
import json, sys
import psycopg
from psycopg.rows import dict_row
from core.review import evals
from core.settings import Settings
from tests.conftest import stockfish_skip
from tests import test_review_positions as t

engine = {"none": None, "no_start": t._NoStart(), "dies": t._Dies()}[sys.argv[2]]
with psycopg.connect(sys.argv[1], row_factory=dict_row) as conn:
    kw = {} if engine is None else {"engine": engine}
    out = evals.run(conn, Settings(review_position_max_ply=4), limit=None, workers=2, **kw)
print(json.dumps(out))
"""


def _run_in_a_process(url: str, engine: str, seconds: int = 90) -> tuple[dict[str, Any], str]:
    """Run position-evals with two workers in a fresh process, from its main thread as the CLI
    does (a pool behaves differently at shutdown when started from another thread), and kill it
    rather than hang the suite; nothing it started may outlive it. (summary, its stderr)."""
    import os
    import signal
    import subprocess
    import sys
    import time

    proc = subprocess.Popen(
        [sys.executable, "-c", _RUN_IN_CHILD, url, engine],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        out, err = proc.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        pytest.fail("the run did not return")
    assert proc.returncode == 0, err[-2000:]
    # Nothing it started (workers, engines) may outlive it. Where the start method is
    # `forkserver` (the Linux default since Python 3.14) the server exits on its own once its
    # parent has gone, a moment after the run returns, so the group gets a few seconds to empty.
    deadline = time.monotonic() + 5.0
    while True:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:  # it emptied in between
                break
            pytest.fail("the run left processes behind")
        time.sleep(0.1)
    return json.loads(out.strip().splitlines()[-1]), err


def test_engines_that_do_not_start_stop_the_run_promptly_and_quietly(
    clean: psycopg.Connection[DictRow], fresh_db_url: str
) -> None:
    conn = clean
    _player(conn)
    _games(conn, "s", ["e4", "d5", "exd5", "Qxd5"], 3)
    conn.commit()
    out, printed = _run_in_a_process(fresh_db_url, "no_start")
    assert out["evaluated"] == 0 and out["failed"] == out["pending"] == 4 and _evaluated(conn) == {}
    assert any("engine did not start: FileNotFoundError" in f for f in out["failures"])
    assert "Traceback" not in printed and "secret-looking" not in printed + json.dumps(out)
    # The one-engine path raises, as before: the CLI reports the class chain.
    with pytest.raises(FileNotFoundError):
        evals.run(conn, _config(), limit=None, engine=_NoStart())


def test_a_worker_that_dies_ends_the_run_with_every_board_counted(
    clean: psycopg.Connection[DictRow], fresh_db_url: str
) -> None:
    conn = clean
    _player(conn)
    _games(conn, "s", ["e4", "d5", "exd5", "Qxd5"], 3)
    conn.commit()
    out, printed = _run_in_a_process(fresh_db_url, "dies")
    assert out["evaluated"] == 0 and out["failed"] == 4 and _evaluated(conn) == {}
    assert all(f.endswith("BrokenProcessPool") for f in out["failures"]) and "Traceback" not in printed


@pytest.mark.skipif(stockfish_skip() is not None, reason=stockfish_skip() or "")
def test_workers_close_their_engines_so_the_run_ends(clean: psycopg.Connection[DictRow], fresh_db_url: str) -> None:
    """python-chess drives Stockfish from a non-daemon thread, and a worker process waits for
    its non-daemon threads before it ends: with the engine left open, the pool's shutdown waited
    for ever after the last board. Only the real engine shows it."""
    conn = clean
    _player(conn)
    _games(conn, "s", ["e4", "d5", "exd5", "Qxd5"], 3)
    conn.commit()
    out, _ = _run_in_a_process(fresh_db_url, "none")
    assert out == {"pending": 4, "evaluated": 4, "terminal": 0, "failed": 0, "fallbacks": 0}
    assert all(r["best_move"] for r in _evaluated(conn).values())
