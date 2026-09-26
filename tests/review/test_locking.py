"""The run's two locks: review first, then the repertoire, both before any read and held to
the commit (core/review/run.py). Two connections, one of them on a thread."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from core.constants import LOCK_REPERTOIRE, LOCK_REVIEW, PLAYER_ID
from core.repertoire import books, matching
from core.review import run as review
from tests import repertoire_helpers as h
from tests.conftest import reset_game_data
from tests.review.helpers import analysed_game, events_of, qh_ctx


@pytest.fixture()
def url(fresh_db_url: str) -> Generator[str, None, None]:
    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as c:
        reset_game_data(c)
    yield fresh_db_url
    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as c:
        reset_game_data(c)


def connect(url: str) -> psycopg.Connection[DictRow]:
    return psycopg.Connection[DictRow].connect(url, row_factory=dict_row)


def held(conn: psycopg.Connection[DictRow], key: int) -> bool:
    row = conn.execute(
        "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND classid = %s AND objid = %s"
        " AND pid = pg_backend_pid() AND granted",
        (key, PLAYER_ID),
    ).fetchone()
    return bool(row and row["n"])


def in_thread(fn: Callable[[], None]) -> tuple[threading.Thread, threading.Event]:
    done = threading.Event()

    def body() -> None:
        try:
            fn()
        finally:
            done.set()

    t = threading.Thread(target=body)
    t.start()
    return t, done


def repertoire(conn: psycopg.Connection[DictRow]) -> None:
    h.book(conn, 1, "White", "white")
    h.chapter(conn, 1, 1, "Open")
    h.line(conn, 10, 1, "Nf3", ["e4", "e5", "Nf3"])
    h.line(conn, 11, 1, "Nc3", ["e4", "e5", "Nc3"])


def test_a_second_run_waits_and_then_reads_the_newer_state(url: str) -> None:
    with connect(url) as setup:
        analysed_game(setup, 1, qh_ctx(), days_ago=2)
        setup.commit()
    a_read = threading.Event()
    a_go = threading.Event()
    b_read: list[list[int]] = []
    out: dict[str, dict[str, Any]] = {}

    def run_a() -> None:
        with connect(url) as c:

            def hook(ids: list[int]) -> None:
                assert held(c, LOCK_REVIEW) and held(c, LOCK_REPERTOIRE)
                a_read.set()
                assert a_go.wait(10)

            out["a"] = review.run(c, after_read=hook)
            c.commit()

    def run_b() -> None:
        with connect(url) as c:
            out["b"] = review.run(c, after_read=b_read.append)
            c.commit()

    ta, a_done = in_thread(run_a)
    assert a_read.wait(10)
    with connect(url) as setup:  # a game arrives while A holds the locks
        analysed_game(setup, 2, qh_ctx(), days_ago=1)
        setup.commit()
    tb, b_done = in_thread(run_b)
    assert not b_done.wait(0.5) and b_read == []  # B is waiting before its first read
    a_go.set()
    ta.join(10)
    tb.join(10)
    assert a_done.is_set() and b_done.is_set()
    assert out["a"]["games"] == 1 and out["b"]["games"] == 2 and b_read == [[2, 1]]
    with connect(url) as check:
        assert len(events_of(check, 1)) == 1 and len(events_of(check, 2)) == 1


def test_a_toggle_waits_for_the_run_and_a_run_reads_what_a_rematch_published(url: str) -> None:
    with connect(url) as setup:
        analysed_game(setup, 1, qh_ctx())
        repertoire(setup)
        fens = qh_ctx()["fen_sequence"]
        h.result(
            setup, 1, book_id=1, chapter_id=1, ply=2, by="me", expected="Nf3", played="Qh5", fen=fens[2], line_ids=[10]
        )
        setup.commit()
    # 1. a toggle started while the run is tagging waits for its commit
    read = threading.Event()
    go = threading.Event()
    order: list[str] = []

    def run_holding() -> None:
        with connect(url) as c:

            def hook(ids: list[int]) -> None:
                read.set()
                assert go.wait(10)

            review.run(c, after_read=hook)
            c.commit()
            order.append("run")

    def toggle() -> None:
        with connect(url) as c:
            books.set_active(c, "lines", 10, False, 100)
            c.commit()
            order.append("toggle")

    tr, run_done = in_thread(run_holding)
    assert read.wait(10)
    tt, toggle_done = in_thread(toggle)
    assert not toggle_done.wait(0.5)
    go.set()
    tr.join(10)
    tt.join(10)
    assert order == ["run", "toggle"]
    with connect(url) as check:
        assert events_of(check, 1)[0]["pool_key"] == "line:1:1:10"
        check.execute("UPDATE repertoire_lines SET active = TRUE WHERE id = 10")
        check.commit()
    # 2. a run started while a rematch holds the repertoire lock reads the rows it publishes
    started: list[str] = []

    def run_later() -> None:
        with connect(url) as c:
            review.run(c, after_read=lambda ids: started.append("read"))
            c.commit()

    with connect(url) as rematch:
        with rematch.transaction():
            matching.lock(rematch)
            rematch.execute("DELETE FROM game_repertoire_results WHERE chess_game_id = 1")
            h.result(
                rematch,
                1,
                book_id=1,
                chapter_id=1,
                ply=2,
                by="me",
                expected="Nc3",
                played="Qh5",
                fen=fens[2],
                line_ids=[11],
            )
            t, done = in_thread(run_later)
            assert not done.wait(0.5) and started == []
        t.join(10)
    assert done.is_set()
    with connect(url) as check:
        assert events_of(check, 1)[0]["pool_key"] == "line:1:1:11"


def test_the_lock_order_is_review_then_repertoire_on_every_path(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    src = Path(review.__file__).read_text()
    assert src.index("LOCK_REVIEW, PLAYER_ID") < src.index("matching.lock(conn)")
    for path in Path(review.__file__).parent.glob("*.py"):
        text = path.read_text()
        if path.name != "run.py":
            assert not re.search(r"matching\.lock|pg_advisory", text), path.name
    assert src.count("matching.lock(") == 1 and src.count("pg_advisory_xact_lock(%s") == 1
    seen: list[tuple[bool, bool]] = []
    real = matching.lock

    def spy(conn: Any) -> None:
        seen.append((held(conn, LOCK_REVIEW), held(conn, LOCK_REPERTOIRE)))
        real(conn)

    monkeypatch.setattr(matching, "lock", spy)
    real_load = review.settings_module.load
    under_locks: list[bool] = []

    def load_spy(conn: Any) -> Any:
        under_locks.append(held(conn, LOCK_REVIEW) and held(conn, LOCK_REPERTOIRE))
        return real_load(conn)

    monkeypatch.setattr(review.settings_module, "load", load_spy)
    with connect(url) as c:
        h.player(c)
        review.run(c)
        c.commit()
        assert not held(c, LOCK_REVIEW) and not held(c, LOCK_REPERTOIRE)  # both fell with the commit
    assert seen == [(True, False)]  # the review lock was already held when the repertoire lock was taken
    assert under_locks == [True]  # the settings row is read under both, like every other read
