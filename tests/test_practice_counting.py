"""Which attempts count, queues that hold nothing back, and extra practice past a cleared queue.

An attempt counts toward the day, the streak and the ladder when its session's first attempt
found the puzzle due; every path follows that one rule. A queue no longer holds a due puzzle
back from another. Extra practice serves the filter's not-yet-due puzzles in pages whose
exposures are proof of service, consumed by the first other session that attempts the puzzle.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, LiteralString

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from core import home, schema
from core.constants import PLAYER_ID
from core.puzzles import attempts, serve, srs, stats
from core.repertoire import practice
from tests.test_practice import (
    _config,
    _corpus,
    _deviation,
    _ids,
    _line,
    _player,
    _puzzle,
    _rep_puzzle,
    _segment,
)
from tests.test_schema import FIXTURES, _scratch

MOVES = ["e4", "e5", "Nf3", "Nc6", "Bb5"]
PLY = 4  # three deviations at ply 2 plus the two-ply lookahead: Bb5, the player's move
ANSWER = _segment(MOVES, PLY)


def _visible_line_puzzle(conn: psycopg.Connection[DictRow]) -> int:
    _player(conn)
    _line(conn, MOVES)
    pid = _rep_puzzle(conn, MOVES)
    for g in (1, 2, 3):
        _deviation(conn, g, 2)
    return pid


def _schedule(conn: psycopg.Connection[DictRow], pid: int, hours: float, level: str = "pawn") -> None:
    """Give the puzzle a ladder state due `hours` from now (negative: already due)."""
    conn.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level, next_show_at)"
        " VALUES (%s, %s, %s, now() + make_interval(secs => %s))"
        " ON CONFLICT (player_id, puzzle_id) DO UPDATE SET level = EXCLUDED.level, next_show_at = EXCLUDED.next_show_at",
        (PLAYER_ID, pid, level, hours * 3600),
    )


def _record(
    conn: psycopg.Connection[DictRow],
    pid: int,
    *,
    solved: bool = True,
    session: str | None = None,
    mode: attempts.Mode = "practice",
    config: Any = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    return attempts.record(
        conn,
        pid,
        config or _config(),
        claimed=solved,
        moves_played=ANSWER if solved else "d4",
        attempt_id=attempt_id or str(uuid.uuid4()),
        session_id=session,
        presentation_ply=PLY,
        mode=mode,
    )


def _state(conn: psycopg.Connection[DictRow], pid: int) -> dict[str, Any] | None:
    return srs.states(conn, [pid]).get(pid)


def _rows(conn: psycopg.Connection[DictRow], pid: int) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT solved, counted, mode, session_id::text AS session FROM puzzle_attempts WHERE puzzle_id = %s ORDER BY id",
            (pid,),
        ).fetchall()
    ]


def _one(conn: psycopg.Connection[DictRow], query: LiteralString) -> DictRow:
    row = conn.execute(query).fetchone()
    assert row is not None
    return row


def _solved_today(conn: psycopg.Connection[DictRow]) -> int:
    return int(home.puzzles_today(conn, _config())["solved"])


# --- no queue holds a puzzle back from another ---------------------------------------------------


def test_a_filtered_queue_serves_a_due_puzzle_another_queue_holds(clean: psycopg.Connection[DictRow]) -> None:
    pid = _visible_line_puzzle(clean)
    config = _config()
    everything = serve.play_batch(clean, config, last_n_games=0, ptype="all", subtype=None)
    assert pid in _ids(everything.rows)
    repertoire = serve.play_batch(clean, config, last_n_games=0, ptype="repertoire", subtype=None)
    assert _ids(repertoire.rows) == [pid]
    clean.commit()  # each request is its own transaction: the attempt is after the serve
    # An attempt in one acknowledges it in both.
    _record(clean, pid, session=str(uuid.uuid4()))
    assert serve.pending_members(clean, "all", {pid}) == []
    assert serve.pending_members(clean, "repertoire", {pid}) == []


# --- two copies of one due serving score once ------------------------------------------------------


@pytest.mark.parametrize("first, second", [(True, True), (True, False), (False, True), (False, False)])
def test_two_copies_of_a_due_puzzle_score_once(clean: psycopg.Connection[DictRow], first: bool, second: bool) -> None:
    pid = _visible_line_puzzle(clean)
    a = _record(clean, pid, solved=first, session=str(uuid.uuid4()))
    after_first = _state(clean, pid)
    b = _record(clean, pid, solved=second, session=str(uuid.uuid4()))
    assert a["counted"] is True and a["srs"]["transition"] is not None
    assert b["counted"] is False and b["srs"]["transition"] is None
    assert _state(clean, pid) == after_first
    assert [r["counted"] for r in _rows(clean, pid)] == [True, False]
    assert _solved_today(clean) == (1 if first else 0)


def test_with_no_wrong_retry_delay_a_copy_answered_after_a_miss_is_a_new_due_serving(
    clean: psycopg.Connection[DictRow],
) -> None:
    pid = _visible_line_puzzle(clean)
    config = _config(srs_wrong_retry_hours=0)
    assert _record(clean, pid, solved=False, session=str(uuid.uuid4()), config=config)["counted"] is True
    second = _record(clean, pid, solved=True, session=str(uuid.uuid4()), config=config)
    assert second["counted"] is True and second["srs"]["transition"] is not None
    assert _solved_today(clean) == 1


def _race(fresh_db_url: str, calls: list[Callable[[psycopg.Connection[DictRow]], dict[str, Any]]]) -> list[object]:
    """Run each call on its own connection, released together; each holds its transaction open
    for a moment after its call so the other is inside its own before either commits."""
    barrier = threading.Barrier(len(calls))
    out: list[object] = [None] * len(calls)

    def run(i: int) -> None:
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as c:
            barrier.wait()
            try:
                out[i] = calls[i](c)
                time.sleep(0.4)
                c.commit()
            except attempts.NotAttemptable as exc:
                c.rollback()
                out[i] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return out


def test_two_concurrent_copies_of_a_due_puzzle_score_once(
    clean: psycopg.Connection[DictRow], fresh_db_url: str
) -> None:
    pid = _visible_line_puzzle(clean)
    clean.commit()
    results = _race(fresh_db_url, [lambda c: _record(c, pid, session=str(uuid.uuid4())) for _ in range(2)])
    assert sorted(bool(r["counted"]) for r in results if isinstance(r, dict)) == [False, True]
    assert [r["counted"] for r in _rows(clean, pid)].count(True) == 1
    assert _solved_today(clean) == 1


def test_a_copy_of_an_attempt_that_waited_on_the_original_gets_its_verdict(
    clean: psycopg.Connection[DictRow], fresh_db_url: str
) -> None:
    # A retry sent while the original is still in flight, for a puzzle that is no longer visible:
    # the copy waits on the original's lock, and once that commits it is a replay, not a new attempt.
    pid = _visible_line_puzzle(clean)
    assert pid in _ids(serve.play_batch(clean, _config(), last_n_games=0, ptype="repertoire", subtype=None).rows)
    clean.execute("UPDATE chapters SET active = FALSE WHERE id = 1")
    clean.commit()
    attempt_id, session = str(uuid.uuid4()), str(uuid.uuid4())
    results = _race(fresh_db_url, [lambda c: _record(c, pid, session=session, attempt_id=attempt_id) for _ in range(2)])
    assert all(isinstance(r, dict) and r["solved"] is True for r in results), results
    assert len(_rows(clean, pid)) == 1


# --- a session's first attempt decides -----------------------------------------------------------------


def test_a_session_that_began_before_the_puzzle_was_due_never_counts(clean: psycopg.Connection[DictRow]) -> None:
    pid = _visible_line_puzzle(clean)
    _schedule(clean, pid, hours=5)
    session = str(uuid.uuid4())
    assert _record(clean, pid, solved=False, session=session, mode="extra")["counted"] is False
    _schedule(clean, pid, hours=-1)  # it comes due while the load is still mounted
    before = _state(clean, pid)
    retry = _record(clean, pid, solved=True, session=session, mode="extra")
    assert retry["counted"] is False and retry["srs"]["transition"] is None
    assert _state(clean, pid) == before and _solved_today(clean) == 0
    # A new load after it came due is judged afresh.
    fresh = _record(clean, pid, solved=True, session=str(uuid.uuid4()), mode="extra")
    assert fresh["counted"] is True and fresh["srs"]["transition"] is not None
    assert _solved_today(clean) == 1


def test_a_miss_then_a_solve_in_one_due_load_counts_the_puzzle_and_scores_once(
    clean: psycopg.Connection[DictRow],
) -> None:
    pid = _visible_line_puzzle(clean)
    session = str(uuid.uuid4())
    assert _record(clean, pid, solved=False, session=session)["counted"] is True
    after_miss = _state(clean, pid)
    solve = _record(clean, pid, solved=True, session=session)
    assert solve["counted"] is True and solve["srs"]["transition"]["outcome"] == "unchanged"
    assert _state(clean, pid) == after_miss
    assert _solved_today(clean) == 1


# --- every path, due or not --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["practice", "drill", "extra"])
@pytest.mark.parametrize("due", [True, False])
def test_every_path_counts_exactly_when_the_puzzle_is_due(
    clean: psycopg.Connection[DictRow], mode: attempts.Mode, due: bool
) -> None:
    pid = _visible_line_puzzle(clean)
    _schedule(clean, pid, hours=-1 if due else 5)
    before = _state(clean, pid)
    result = _record(clean, pid, session=str(uuid.uuid4()), mode=mode)
    assert result["counted"] is due
    assert (_state(clean, pid) != before) is due
    assert _solved_today(clean) == (1 if due else 0)
    assert _rows(clean, pid)[0]["mode"] == mode


def test_a_corpus_attempt_always_counts_and_never_reaches_the_ladder(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _corpus(clean, 1, ["fork"])
    served = serve.play_batch(clean, _config(puzzle_mix_batch_size=1), last_n_games=0, ptype="all", subtype=None)
    (row,) = served.rows
    line = row["solution_line"]
    for session in (str(uuid.uuid4()), str(uuid.uuid4())):
        result = attempts.record(
            clean,
            int(row["id"]),
            _config(),
            claimed=True,
            moves_played=",".join(line[0::2]),
            attempt_id=str(uuid.uuid4()),
            session_id=session,
        )
        assert result["counted"] is True and result["srs"]["transition"] is None
    assert _state(clean, int(row["id"])) is None


def test_the_list_and_a_link_no_longer_score_a_puzzle_that_is_not_due(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    pid = _puzzle(clean)
    _schedule(clean, pid, hours=5, level="knight")
    before = _state(clean, pid)
    result = attempts.record(
        clean,
        pid,
        _config(),
        claimed=True,
        moves_played="Nxe5,d4",
        attempt_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
    )
    assert result["solved"] is True and result["counted"] is False
    assert _state(clean, pid) == before and _solved_today(clean) == 0


def test_an_idempotent_replay_answers_whether_it_counted(clean: psycopg.Connection[DictRow]) -> None:
    pid = _visible_line_puzzle(clean)
    _schedule(clean, pid, hours=5)
    attempt_id = str(uuid.uuid4())
    first = _record(clean, pid, session=str(uuid.uuid4()), attempt_id=attempt_id)
    _schedule(clean, pid, hours=-1)
    replay = _record(clean, pid, session=str(uuid.uuid4()), attempt_id=attempt_id)
    assert first["counted"] is False and replay["counted"] is False and len(_rows(clean, pid)) == 1


# --- extra practice: pages -----------------------------------------------------------------------------


def _ladder(conn: psycopg.Connection[DictRow]) -> dict[str, int]:
    """Standard puzzles on distinct boards with every kind of state extra practice must judge."""
    from tests.test_practice import _position

    _player(conn)
    ids: dict[str, int] = {}
    for i, name in enumerate(["soon_knight", "soon_pawn", "later", "due", "king", "fresh"]):
        fen, line = _position(i)
        ids[name] = _puzzle(conn, fen, line, themes=[])
    _schedule(conn, ids["soon_knight"], hours=2, level="knight")
    _schedule(conn, ids["soon_pawn"], hours=2, level="pawn")
    conn.execute(  # the same instant, so level decides between the two
        "UPDATE player_puzzle_state SET next_show_at = (SELECT next_show_at FROM player_puzzle_state"
        " WHERE puzzle_id = %s) WHERE puzzle_id = %s",
        (ids["soon_pawn"], ids["soon_knight"]),
    )
    _schedule(conn, ids["later"], hours=30, level="rook")
    _schedule(conn, ids["due"], hours=-1)
    conn.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level, next_show_at) VALUES (%s, %s, 'king', %s)",
        (PLAYER_ID, ids["king"], srs.KING_SENTINEL),
    )
    return ids


def test_extra_practice_serves_the_not_yet_due_soonest_first_in_pages(clean: psycopg.Connection[DictRow]) -> None:
    ids = _ladder(clean)
    config = _config(puzzle_mix_batch_size=2)
    first = serve.extra_page(clean, config, last_n_games=0, ptype="all", subtype=None, run=None)
    assert _ids(first.rows) == [ids["soon_pawn"], ids["soon_knight"]]
    assert first.run == first.batch_id and all(r["play_batch_id"] == first.batch_id for r in first.rows)
    second = serve.extra_page(clean, config, last_n_games=0, ptype="all", subtype=None, run=first.run)
    assert _ids(second.rows) == [ids["later"]] and second.run == first.run
    done = serve.extra_page(clean, config, last_n_games=0, ptype="all", subtype=None, run=first.run)
    assert done.rows == [] and done.batch_id is None and done.run == first.run
    # A new run starts from the top again.
    again = serve.extra_page(clean, config, last_n_games=0, ptype="all", subtype=None, run=None)
    assert _ids(again.rows) == [ids["soon_pawn"], ids["soon_knight"]] and again.run != first.run
    # Its exposures are under the filter's own extra scope and never queue state.
    scopes = {r["scope"] for r in clean.execute("SELECT DISTINCT scope FROM player_puzzle_exposure").fetchall()}
    assert scopes == {"extra:all"}
    assert serve.pending_members(clean, "all", set(ids.values())) == []


def test_a_corpus_puzzle_is_never_extra_practice() -> None:
    state = {"level": "pawn", "next_show_at": srs.KING_SENTINEL.replace(year=3000)}
    now = srs.KING_SENTINEL.replace(year=2000)
    assert serve.extra_candidate({"srs": state, "source_types": ["blunder"]}, now)
    assert not serve.extra_candidate({"srs": state, "source_types": ["lichess_cc0"]}, now)
    assert not serve.extra_candidate({"srs": None, "source_types": ["blunder"]}, now)


def test_a_new_run_with_nothing_not_yet_due_writes_nothing(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _puzzle(clean)  # never attempted: due, so the queue's
    empty = serve.extra_page(clean, _config(), last_n_games=0, ptype="all", subtype=None, run=None)
    assert empty.rows == [] and empty.batch_id is None and empty.run is None
    assert _one(clean, "SELECT count(*) AS n FROM player_puzzle_exposure")["n"] == 0


def test_extra_practice_follows_the_filter(clean: psycopg.Connection[DictRow]) -> None:
    ids = _ladder(clean)
    line_puzzle = _visible_line_puzzle(clean)
    _schedule(clean, line_puzzle, hours=1)
    page = serve.extra_page(clean, _config(), last_n_games=0, ptype="repertoire", subtype=None, run=None)
    assert _ids(page.rows) == [line_puzzle]
    assert _one(clean, "SELECT DISTINCT scope FROM player_puzzle_exposure")["scope"] == "extra:repertoire"
    assert line_puzzle not in ids.values()


# --- extra practice: proof of service ----------------------------------------------------------------


def _served_then_hidden(conn: psycopg.Connection[DictRow]) -> int:
    pid = _visible_line_puzzle(conn)
    _schedule(conn, pid, hours=5)
    page = serve.extra_page(conn, _config(), last_n_games=0, ptype="repertoire", subtype=None, run=None)
    assert _ids(page.rows) == [pid] and page.rows[0]["presentation_ply"] == PLY
    conn.execute("UPDATE chapters SET active = FALSE WHERE id = 1")
    conn.commit()  # each request is its own transaction: the attempts are after the serve
    return pid


def test_an_extra_load_finishes_after_its_puzzle_is_hidden_and_no_other_load_starts(
    clean: psycopg.Connection[DictRow],
) -> None:
    pid = _served_then_hidden(clean)
    session = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())
    first = _record(clean, pid, solved=False, session=session, mode="extra", attempt_id=attempt_id)
    assert first["counted"] is False
    clean.commit()
    assert _record(clean, pid, solved=True, session=session, mode="extra")["solved"] is True  # Try Again
    with pytest.raises(attempts.NotAttemptable):
        _record(clean, pid, session=str(uuid.uuid4()), mode="extra")
    # The offline queue's replay of the first attempt still gets its verdict.
    assert _record(clean, pid, session=session, mode="extra", attempt_id=attempt_id)["solved"] is False


def test_extra_proof_ends_with_its_window_and_is_never_open_ended(clean: psycopg.Connection[DictRow]) -> None:
    pid = _served_then_hidden(clean)
    clean.execute("UPDATE player_puzzle_exposure SET served_at = served_at - interval '8 days'")
    with pytest.raises(attempts.NotAttemptable):
        _record(clean, pid, session=str(uuid.uuid4()), mode="extra")


def test_two_concurrent_loads_cannot_both_finish_one_hidden_extra_puzzle(
    clean: psycopg.Connection[DictRow], fresh_db_url: str
) -> None:
    pid = _served_then_hidden(clean)
    clean.commit()
    results = _race(
        fresh_db_url, [lambda c: _record(c, pid, session=str(uuid.uuid4()), mode="extra") for _ in range(2)]
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, attempts.NotAttemptable) for r in results) == 1
    assert len(_rows(clean, pid)) == 1


# --- the readers ------------------------------------------------------------------------------------


def test_the_day_and_the_streak_ignore_uncounted_attempts(clean: psycopg.Connection[DictRow]) -> None:
    pid = _visible_line_puzzle(clean)
    _schedule(clean, pid, hours=5)
    _record(clean, pid, session=str(uuid.uuid4()), mode="extra")
    assert _solved_today(clean) == 0
    days = home._per_day(clean, home._PUZZLE_DAYS, "UTC")
    assert days == {}
    _schedule(clean, pid, hours=-1)
    _record(clean, pid, session=str(uuid.uuid4()))
    assert _solved_today(clean) == 1
    assert list(home._per_day(clean, home._PUZZLE_DAYS, "UTC").values()) == [1]


def test_the_line_report_leaves_out_extra_practice_and_keeps_drills(clean: psycopg.Connection[DictRow]) -> None:
    pid = _visible_line_puzzle(clean)
    _schedule(clean, pid, hours=5)
    _record(clean, pid, session=str(uuid.uuid4()), mode="extra")
    assert stats.by_line(clean, [1]) == {} and practice.read(clean, [1]) == []
    _record(clean, pid, session=str(uuid.uuid4()), mode="drill")
    _record(clean, pid, solved=False, session=str(uuid.uuid4()), mode="practice")
    clean.execute(  # a row from before migration 016
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, moves_played, session_id, mode)"
        " VALUES (%s, %s, TRUE, %s, %s, NULL)",
        (pid, PLAYER_ID, ANSWER, str(uuid.uuid4())),
    )
    assert stats.by_line(clean, [1]) == {1: {"loads": 3, "first_right": 2, "first_wrong": 1}}
    assert len(practice.read(clean, [1])) == 3


# --- migration 016 over a populated database -----------------------------------------------------------


def test_migration_016_keeps_every_count_where_it_was(fresh_db_url: str, tmp_path: Path) -> None:
    url = _scratch(fresh_db_url, "attempts_v15")
    v15 = tmp_path / "v15"
    v15.mkdir()
    for n, path in schema.migration_files():
        if n <= 15:
            (v15 / path.name).write_text(path.read_text())
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL((FIXTURES / "schema_baseline.sql").read_text()))  # type: ignore[arg-type]  # repo fixture
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
        assert schema.upgrade(c, v15)[-1] == 15
        _visible_line_puzzle(c)
        pid = int(_one(c, "SELECT id FROM puzzles")["id"])
        _schedule(c, pid, hours=5)  # not due now: the old rows counted anyway, and still do
        c.execute(
            "INSERT INTO player_puzzle_exposure (player_id, puzzle_id, bucket, batch_id, scope, presentation_ply)"
            " VALUES (%s, %s, 'your_puzzles', 1, 'drill', %s)",
            (PLAYER_ID, pid, PLY),
        )
        for solved in (True, False):  # a queue load and a drill load, today
            c.execute(
                "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved, moves_played, session_id, attempt_seq)"
                " VALUES (%s, %s, %s, %s, %s, 1)",
                (pid, PLAYER_ID, solved, ANSWER if solved else "d4", str(uuid.uuid4())),
            )
        c.commit()
        assert schema.upgrade(c) == [16]
        rows = c.execute("SELECT counted, mode FROM puzzle_attempts ORDER BY id").fetchall()
        assert [(r["counted"], r["mode"]) for r in rows] == [(True, None), (True, None)]
        assert home.puzzles_today(c, _config())["solved"] == 1
        assert sum(home._per_day(c, home._PUZZLE_DAYS, "UTC").values()) == 1
        assert stats.by_line(c, [1]) == {1: {"loads": 2, "first_right": 1, "first_wrong": 1}}
        assert len(practice.read(c, [1])) == 2
