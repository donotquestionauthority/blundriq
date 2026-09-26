"""The per-game replace and the run around it (core/review/write.py, run.py).

Each run is its own transaction (the tests commit between runs so `now()` moves on).
"""

from __future__ import annotations

import argparse
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core import notify, settings
from core.constants import PLAYER_ID
from core.review import run as review
from core.review.detect import REVIEW_CONFIG_VERSION
from pipeline import cli
from tests.review.helpers import analysed_game, events_of, plant_event, qh_ctx


def run(conn: psycopg.Connection[DictRow], **kw: Any) -> dict[str, Any]:
    out = review.run(conn, **kw)
    conn.commit()
    return out


def set_setting(conn: psycopg.Connection[DictRow], **values: Any) -> None:
    current = settings.load(conn)
    settings.save(conn, current.model_copy(update=values))


def state(conn: psycopg.Connection[DictRow]) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM review_detection_state WHERE player_id = %s", (PLAYER_ID,)).fetchone()


def test_a_run_writes_every_window_game_and_the_state_row(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    analysed_game(conn, 1, qh_ctx(), days_ago=1)
    analysed_game(conn, 2, qh_ctx(cps=[20] * 9), days_ago=2)  # tags with no events
    conn.commit()
    out = run(conn)
    assert {k: out[k] for k in ("games", "events", "unknown", "failed", "deleted")} == {
        "games": 2,
        "events": 1,
        "unknown": 0,
        "failed": 0,
        "deleted": 0,
    }
    assert "failures" not in out and isinstance(out["seconds"], float)
    rows = events_of(conn, 1)
    assert len(rows) == 1 and rows[0]["config_version"] == REVIEW_CONFIG_VERSION
    assert rows[0]["board_key"] is not None and rows[0]["meaning_changed_at"] is None
    assert events_of(conn, 2) == []
    st = state(conn)
    assert st and (st["config_version"], st["unknown_candidates"], st["window_games"]) == (REVIEW_CONFIG_VERSION, 0, 2)


def test_rerunning_keeps_first_detected_at_and_is_a_no_op(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    analysed_game(conn, 1, qh_ctx())
    conn.commit()
    run(conn)
    before = events_of(conn, 1)
    run(conn)
    after = events_of(conn, 1)
    assert after == before  # discovery time survives; nothing was reclassified
    assert after[0]["meaning_changed_at"] is None


def test_meaning_changed_at_stamps_a_stored_fact_change_not_a_repricing(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    analysed_game(conn, 1, qh_ctx())
    conn.commit()
    run(conn)
    first = events_of(conn, 1)[0]
    # a repricing: the eval after the anchor moves, the route and key do not
    conn.execute("UPDATE chess_games SET ply_analysis = jsonb_set(ply_analysis, '{5,eval}', '-600') WHERE id = 1")
    conn.commit()
    run(conn)
    repriced = events_of(conn, 1)[0]
    assert repriced["cost"] != first["cost"]
    assert repriced["first_detected_at"] == first["first_detected_at"] and repriced["meaning_changed_at"] is None
    # a reclassification: the early cap drops below the anchor, the opening-candidate flag flips
    set_setting(conn, review_early_ply_cap=3)
    run(conn)
    changed = events_of(conn, 1)[0]
    assert changed["opening_candidate"] is False and first["opening_candidate"] is True
    assert changed["first_detected_at"] == first["first_detected_at"]
    assert changed["meaning_changed_at"] is not None and changed["meaning_changed_at"] > first["first_detected_at"]
    # unchanged after that: the stamp stays where it was
    run(conn)
    assert events_of(conn, 1)[0]["meaning_changed_at"] == changed["meaning_changed_at"]


def test_a_removed_event_is_gone_and_its_return_is_a_fresh_discovery(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    analysed_game(conn, 1, qh_ctx())
    conn.commit()
    run(conn)
    first = events_of(conn, 1)[0]
    set_setting(conn, review_conf_es_drop=99)  # nothing confirms
    assert run(conn)["events"] == 0
    assert events_of(conn, 1) == []
    set_setting(conn, review_conf_es_drop=15)
    run(conn)
    back = events_of(conn, 1)[0]
    assert back["first_detected_at"] > first["first_detected_at"] and back["meaning_changed_at"] is None


def test_a_game_that_cannot_be_tagged_keeps_its_rows_and_fails_the_run(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    analysed_game(conn, 1, qh_ctx(), days_ago=1)
    analysed_game(conn, 2, qh_ctx(), days_ago=2)
    conn.commit()
    run(conn)
    # game 2's stored analysis loses an entry: the detector fails closed on it
    conn.execute("UPDATE chess_games SET ply_analysis = ply_analysis - 8 WHERE id = 2")
    conn.execute("UPDATE chess_games SET ply_analysis = jsonb_set(ply_analysis, '{5,eval}', '-600') WHERE id = 1")
    conn.commit()
    out = run(conn)
    assert out["failed"] == 1 and out["games"] == 2 and out["failures"] == ["2: untaggable"]
    assert len(events_of(conn, 2)) == 1  # the prior generation stays
    assert events_of(conn, 1)[0]["cost"] != 51.03  # the other game was still replaced
    # a detector that raises is a failed game too, reported by its class chain only
    analysed_game(conn, 3, qh_ctx(), days_ago=0.5)
    conn.commit()
    ctx = qh_ctx()
    ctx["fen_sequence"] = [1] * 9  # not FENs at all: the detector raises instead of answering
    assert review.tag_one((ctx, settings.load(conn).model_dump())) == (1, None, "AttributeError")


def test_failed_games_fail_the_step_through_the_cli(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = clean
    analysed_game(conn, 1, qh_ctx())
    conn.execute("UPDATE chess_games SET ply_analysis = ply_analysis - 8 WHERE id = 1")
    conn.commit()
    sent: list[str] = []
    monkeypatch.setattr(notify, "send_failure", lambda step, run_id, error: sent.append(step) or True)
    assert cli._run_step("review", cli._step_review, argparse.Namespace(alert=True, workers=None)) == 1
    assert "review: FAILED (StepFailed)" in capsys.readouterr().err
    row = conn.execute("SELECT status, error FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row and row["status"] == "failed" and '"failed": 1' in row["error"] and sent == ["review"]


def test_games_leaving_the_window_lose_their_rows(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    set_setting(conn, analysis_game_limit=50)
    for gid in range(1, 51):
        analysed_game(conn, gid, qh_ctx(), days_ago=gid)
    conn.commit()
    assert run(conn)["events"] == 50
    analysed_game(conn, 51, qh_ctx(), days_ago=0.5)  # a newer game pushes the oldest out
    conn.commit()
    out = run(conn)
    assert (out["games"], out["events"], out["deleted"]) == (50, 50, 1)
    assert len(events_of(conn, 51)) == 1 and events_of(conn, 50) == []


def test_a_chess960_game_is_never_in_the_window_and_planted_rows_go(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    ctx = qh_ctx()
    analysed_game(conn, 1, ctx, days_ago=1)
    analysed_game(conn, 2, ctx, days_ago=2, variant="chess960")
    plant_event(conn, 2, 4, ctx["fen_sequence"][4])
    conn.commit()
    out = run(conn)
    assert (out["games"], out["deleted"]) == (1, 1)
    assert events_of(conn, 2) == [] and len(events_of(conn, 1)) == 1


def test_a_clock_decided_game_tags_as_zero_and_clears_old_rows(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    ctx = qh_ctx()
    analysed_game(conn, 1, ctx, termination="timeout")
    plant_event(conn, 1, 4, ctx["fen_sequence"][4])
    conn.commit()
    out = run(conn)
    assert (out["games"], out["events"], out["failed"]) == (1, 0, 0) and events_of(conn, 1) == []


def test_workers_give_the_same_generation(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    for gid in range(1, 5):
        analysed_game(conn, gid, qh_ctx(), days_ago=gid)
    conn.commit()
    serial = run(conn, workers=1)
    rows = [events_of(conn, g) for g in range(1, 5)]
    parallel = run(conn, workers=2)
    assert parallel["events"] == serial["events"] == 4
    assert [events_of(conn, g) for g in range(1, 5)] == rows


def test_the_run_is_a_fixed_number_of_statements_whatever_the_window_holds(
    clean: psycopg.Connection[DictRow], fresh_db_url: str
) -> None:
    """The locks are held for as long as the run takes, and Supabase is a network away: the
    generation is published in one statement, not one per game."""
    from psycopg.rows import dict_row

    class Counting(psycopg.Connection[DictRow]):
        statements = 0

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            Counting.statements += 1
            return super().execute(*args, **kwargs)

    conn = clean
    counts: list[int] = []
    have = 0
    for n in (1, 40):
        for gid in range(have + 1, n + 1):
            analysed_game(conn, gid, qh_ctx(), days_ago=gid)
        have = n
        conn.commit()
        with Counting.connect(fresh_db_url, row_factory=dict_row) as c:
            Counting.statements = 0
            assert review.run(c)["games"] == n
            c.commit()
            counts.append(Counting.statements)
    assert counts[0] == counts[1] <= 10
