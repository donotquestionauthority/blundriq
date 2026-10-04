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
from core.ingest import backfill
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
    gid: str, moves: list[str], *, days_ago: int = 1, color: str = "black", variant: str = "standard"
) -> GameRecord:
    fens = moves_to_fen_sequence(moves, FEN_960 if variant == "chess960" else None, variant)
    return GameRecord(
        platform="lichess",
        platform_game_id=gid,
        url=f"https://lichess.org/{gid}",
        played_at=NOW - timedelta(days=days_ago),
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
    assert _prefix(conn, "g1")["opening_moves"] == LONG[:OPENING_PREFIX_PLIES]
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


# --- the backfill ---------------------------------------------------------------------------


def _lichess_line(gid: str, moves: list[str], **over: Any) -> str:
    g: dict[str, Any] = {
        "id": gid,
        "rated": True,
        "variant": "standard",
        "speed": "rapid",
        "perf": "rapid",
        "createdAt": int((NOW - timedelta(days=5)).timestamp() * 1000),
        "lastMoveAt": int((NOW - timedelta(days=5)).timestamp() * 1000),
        "status": "resign",
        "players": {
            "white": {"user": {"name": "Opp", "id": "opp"}, "rating": 1600},
            "black": {"user": {"name": ME, "id": ME}, "rating": 1550},
        },
        "winner": "white",
        "moves": " ".join(moves),
        "clock": {"initial": 600, "increment": 0, "totalTime": 600},
    }
    g.update(over)
    return json.dumps(g)


def _stored_bare(conn: psycopg.Connection[DictRow], gid: str, moves: list[str]) -> None:
    """A game stored before migration 007 and since housekept: no payload, no prefix."""
    with conn.transaction():
        store_game(conn, record(gid, moves, days_ago=200))
    conn.execute(
        "UPDATE chess_games SET moves = NULL, fen_sequence = NULL, opening_moves = NULL, opening_keys = NULL"
        " WHERE platform_game_id = %s",
        (gid,),
    )


def test_the_backfill_writes_only_the_missing_prefix(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    conn.execute("INSERT INTO players (id, lichess_username) VALUES (%s, %s)", (PLAYER_ID, ME))
    _stored_bare(conn, "bare0001", LONG)
    with conn.transaction():
        store_game(conn, record("kept0001", ["e4", "e5"]))
    conn.commit()
    before = _prefix(conn, "kept0001")["opening_keys"]
    stream = "\n".join(
        [
            _lichess_line("bare0001", LONG),
            _lichess_line("kept0001", ["d4", "d5"]),  # the platform says otherwise: the row keeps its prefix
            _lichess_line("new00001", ["c4"]),  # not stored: the importer's job, not this one's
            _lichess_line("c9600001", ["e4"], variant="chess960", initialFen=FEN_960),
            _lichess_line("live0001", ["e4"], status="started"),  # ongoing: never read
            _lichess_line("bad00001", ["e4", "Ke9"]),
        ]
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=stream.encode())))
    summary = backfill.backfill_openings(conn, months=12, client=client, now=NOW)
    assert summary == {
        "fetched": 5,
        "updated": 1,
        "already_had": 1,
        "not_in_db": 1,
        "unparseable": 1,
        "chess960": 1,
        "failed": 0,
    }
    got = _prefix(conn, "bare0001")
    assert got["bare"] is True  # moves and fen_sequence stay NULL: housekeeping's decision stands
    assert got["opening_moves"] == LONG[:OPENING_PREFIX_PLIES]
    expected = conn.execute(
        "SELECT bq_opening_keys(%s::jsonb) AS k", (json.dumps(moves_to_fen_sequence(LONG)),)
    ).fetchone()
    assert expected and got["opening_keys"] == expected["k"]
    assert _prefix(conn, "kept0001")["opening_keys"] == before
    assert conn.execute("SELECT count(*) AS n FROM chess_games WHERE platform_game_id = 'new00001'").fetchone() == {
        "n": 0
    }
    again = backfill.backfill_openings(conn, months=12, client=client, now=NOW)
    assert again["updated"] == 0 and again["already_had"] == 2


def test_a_platform_that_fails_fails_the_backfill(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    conn.execute("INSERT INTO players (id, lichess_username) VALUES (%s, %s)", (PLAYER_ID, ME))
    conn.commit()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    assert backfill.backfill_openings(conn, months=12, client=client, now=NOW)["failed"] == 1


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
    _games(conn, "w", ["e4", "d5"], 2, color="white")  # 1...d5 as White twice: still only twice per colour
    conn.commit()
    stub = _Stub()
    out = evals.run(conn, _config(), limit=None, engine=stub)
    fens = moves_to_fen_sequence(scandi)
    want = {_key(conn, fens[p]) for p in range(1, 7)}  # 1.e4 is reached by 3 + 2 + 2 games: one board
    assert out == {"pending": 6, "evaluated": 6, "failed": 0}
    rows = _evaluated(conn)
    assert set(rows) == want
    after_e4 = rows[_key(conn, fens[1])]
    assert after_e4["eval_cp"] is None and after_e4["mate_in"] == -3 and after_e4["depth"] == STOCKFISH_DEPTH
    assert after_e4["fen"] == fens[1]
    assert evals.run(conn, _config(), limit=None, engine=stub) == {"pending": 0, "evaluated": 0, "failed": 0}


def test_position_evals_takes_the_most_played_first_and_respects_the_limit(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    _games(conn, "a", ["e4", "c5"], 5)
    _games(conn, "b", ["e4", "e5"], 3)
    conn.commit()
    out = evals.run(conn, _config(), limit=1, engine=_Stub())
    assert out == {"pending": 3, "evaluated": 1, "failed": 0}
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
    assert {r["ply"] for r in rows} == {1, 2, 3}  # each board at its first occurrence
    assert all(r["n"] == 3 for r in rows)


def test_a_board_that_does_not_rebuild_fails_the_run(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _player(conn)
    _games(conn, "e", ["e4", "e5"], 3)
    conn.commit()
    source = conn.execute("SELECT min(id) AS id FROM chess_games").fetchone()
    assert source is not None
    # The lowest-id game is every board's replay source; break its prefix two ways in turn.
    for broken in (["d4", "e5"], ["--", "e5"]):
        conn.execute(
            "UPDATE chess_games SET opening_moves = %s::jsonb WHERE id = %s", (json.dumps(broken), source["id"])
        )
        conn.execute("DELETE FROM position_evals")
        conn.commit()
        out = evals.run(conn, _config(), limit=None, engine=_Stub())
        assert out["failed"] >= 1, broken
        assert _key(conn, moves_to_fen_sequence(["e4"])[1]) not in _evaluated(conn)


def test_position_evals_runs_hourly_after_review() -> None:
    steps = list(runs.HOURLY_STEPS)
    assert steps.index("position-evals") == steps.index("review") + 1
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
