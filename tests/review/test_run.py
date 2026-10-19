"""The review step's fingerprint: a run whose inputs are the published generation's inputs reads
and writes nothing; any changed input, and only a changed input, makes a full run; a failed run
publishes nothing and records no proof; a client from before migration 015 leaves no proof.

Each run is its own transaction (the tests commit between runs so `now()` moves on).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from core import notify, schema, settings
from core.constants import PLAYER_ID
from core.review import run as review
from core.review import stamp, window
from core.review.window import KNOB_FIELDS
from pipeline import cli
from tests import repertoire_helpers as h
from tests.review.helpers import analysed_game, events_of, qh_ctx
from tests.review.test_write import run, set_setting, state

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def generation(conn: psycopg.Connection[DictRow]) -> list[tuple[Any, ...]]:
    """Every row the step owns, as published: the events and the state row, every column."""
    rows = [
        tuple(r.values())
        for r in conn.execute(
            "SELECT * FROM review_events WHERE player_id = %s ORDER BY chess_game_id, anchor_ply", (PLAYER_ID,)
        ).fetchall()
    ]
    st = state(conn)
    return [*rows, tuple(st.values()) if st else ()]


def full_then_skip(conn: psycopg.Connection[DictRow]) -> dict[str, Any]:
    first = run(conn)
    assert "skipped" not in first and first["failed"] == 0
    second = run(conn)
    assert second["skipped"] is True
    return second


def test_a_second_run_over_the_same_inputs_reads_and_writes_nothing(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    analysed_game(conn, 1, qh_ctx(), days_ago=1)
    analysed_game(conn, 2, qh_ctx(cps=[20] * 9), days_ago=2)
    conn.commit()
    first = run(conn)
    assert first["games"] == 2 and first["events"] == 1
    st = state(conn)
    assert st and st["fingerprint"] and st["fingerprint_at"] == st["computed_at"]
    before = generation(conn)
    reads: list[list[int]] = []
    out = run(conn, after_read=reads.append)
    assert out == {"skipped": True, "games": 2, "failed": 0, "seconds": out["seconds"]}
    assert reads == []  # the window was not read
    assert generation(conn) == before  # computed_at included: nothing was touched


def _rewrite_eval(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("UPDATE chess_games SET ply_analysis = jsonb_set(ply_analysis, '{5,eval}', '-600') WHERE id = 1")


def _reanalyse_deeper(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("UPDATE chess_games SET ply_analysis_depth = 20 WHERE id = 1")
    conn.execute("UPDATE player_games SET analyzed_at_depth = 20 WHERE chess_game_id = 1")


def _window_shrinks(conn: psycopg.Connection[DictRow]) -> None:
    set_setting(conn, analysis_game_limit=50)  # game 51 leaves


def _new_game(conn: psycopg.Connection[DictRow]) -> None:
    analysed_game(conn, 52, qh_ctx(cps=[20] * 9), days_ago=0.5)  # enters, nothing leaves


def _result_rematched(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("UPDATE game_repertoire_results SET deviated_at_ply = 3 WHERE chess_game_id = 1")


def _matched_ply_changes(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("UPDATE game_result_lines SET matched_ply = 4")


def _line_edited(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("""UPDATE repertoire_lines SET moves = moves || '["Nc3"]'::jsonb WHERE id = 1""")


def _motif_in_window(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute(
        "INSERT INTO player_motif_events (player_id, chess_game_id, ply, metric_type, theme, found, player_color)"
        " VALUES (%s, 1, 4, 'motif', 'fork', FALSE, 'white')",
        (PLAYER_ID,),
    )


def _schema_bumped(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO schema_version (version) VALUES (999)")


CHANGES = {
    "a game's analysis rewritten": _rewrite_eval,
    "a game re-analysed at another depth": _reanalyse_deeper,
    "a game leaving the window": _window_shrinks,
    "a new game entering it": _new_game,
    "a result row rematched": _result_rematched,
    "a matched ply": _matched_ply_changes,
    "a line's moves": _line_edited,
    "a missed motif in a window game": _motif_in_window,
    "a migration": _schema_bumped,
}


def _seed(conn: psycopg.Connection[DictRow], *, limit: int = 60) -> None:
    """Fifty-one analysed games (all in the window under the default limit, game 51 the oldest and
    the first to leave under the smallest), game 1 matched to a line."""
    set_setting(conn, analysis_game_limit=limit)
    ctx = qh_ctx()
    analysed_game(conn, 1, ctx, days_ago=1)
    for gid in range(2, 52):
        analysed_game(conn, gid, qh_ctx(cps=[20] * 9), days_ago=gid)
    h.book(conn, 1, "B", "white")
    h.chapter(conn, 1, 1, "C")
    h.line(conn, 1, 1, "L", ctx["moves"][:4])
    rid = h.result(
        conn,
        1,
        book_id=1,
        chapter_id=1,
        ply=5,
        by="me",
        expected="x",
        played="y",
        fen=ctx["fen_sequence"][5],
        line_ids=[1],
    )
    assert rid
    conn.commit()


@pytest.mark.parametrize("change", list(CHANGES))
def test_each_input_alone_makes_a_full_run(clean: psycopg.Connection[DictRow], change: str) -> None:
    conn = clean
    _seed(conn)
    full_then_skip(conn)
    before = state(conn)
    CHANGES[change](conn)
    conn.commit()
    try:
        reads: list[list[int]] = []
        out = run(conn, after_read=reads.append)
        assert "skipped" not in out and len(reads) == 1, change
        if change == "a game leaving the window":
            assert 51 not in reads[0] and out["deleted"] == 0 and events_of(conn, 51) == []  # it tagged no events
        after = state(conn)
        assert before and after and after["fingerprint"] != before["fingerprint"]
        assert after["fingerprint_at"] == after["computed_at"] > before["computed_at"]
        assert run(conn)["skipped"] is True  # and the new generation is proven in its turn
    finally:
        conn.execute("DELETE FROM schema_version WHERE version = 999")  # the one change `clean` does not undo
        conn.commit()


def test_each_knob_alone_makes_a_full_run(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    _seed(conn)
    full_then_skip(conn)
    current = settings.load(conn)
    for knob in KNOB_FIELDS:
        set_setting(conn, **{knob: int(getattr(current, knob)) + 1})
        assert "skipped" not in run(conn), knob
        assert run(conn)["skipped"] is True, knob
        set_setting(conn, **{knob: int(getattr(current, knob))})
        run(conn)


def test_a_motif_outside_the_window_and_a_rematch_to_the_same_rows_do_not(
    clean: psycopg.Connection[DictRow],
) -> None:
    conn = clean
    _seed(conn, limit=50)  # game 51 is outside the window
    full_then_skip(conn)
    conn.execute(
        "INSERT INTO player_motif_events (player_id, chess_game_id, ply, metric_type, theme, found, player_color)"
        " VALUES (%s, 51, 4, 'motif', 'fork', FALSE, 'white')",
        (PLAYER_ID,),
    )
    # a rematch that reproduces the same facts under new ids: not an input the detector sees
    old = conn.execute("SELECT * FROM game_repertoire_results WHERE chess_game_id = 1").fetchone()
    assert old
    conn.execute("DELETE FROM game_repertoire_results WHERE chess_game_id = 1")
    h.result(
        conn,
        1,
        book_id=old["book_id"],
        chapter_id=old["chapter_id"],
        ply=old["deviated_at_ply"],
        by=old["deviation_by"],
        expected=old["expected_move"],
        played=old["played_move"],
        fen=old["deviation_fen"],
        line_ids=[1],
    )
    conn.commit()
    assert run(conn)["skipped"] is True


def test_other_code_makes_a_full_run(clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    conn = clean
    _seed(conn)
    full_then_skip(conn)
    monkeypatch.setattr(stamp, "code_stamp", lambda: "other code")
    assert "skipped" not in run(conn)
    assert run(conn)["skipped"] is True
    monkeypatch.undo()
    assert "skipped" not in run(conn)  # back on this code: one more full run, then proven again
    assert run(conn)["skipped"] is True


def _run_step(monkeypatch: pytest.MonkeyPatch, sent: list[str]) -> int:
    monkeypatch.setattr(notify, "send_failure", lambda step, run_id, error: sent.append(step) or True)
    return cli._run_step("review", cli._step_review, argparse.Namespace(alert=True, workers=None))


@pytest.mark.parametrize("failure", ["untaggable", "raises"])
def test_a_failed_run_publishes_nothing_and_records_no_proof_through_the_cli(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """Through the CLI boundary on its own connection: the prior generation stays whole, the run
    row is red with the counts of what would have been written, the alert goes out, and the
    next run — the failure gone — is full and proven."""
    conn = clean
    _seed(conn)
    full_then_skip(conn)
    before = generation(conn)
    cost = events_of(conn, 1)[0]["cost"]
    _rewrite_eval(conn)  # game 1 would be repriced
    if failure == "untaggable":
        conn.execute("UPDATE chess_games SET ply_analysis = ply_analysis - 8 WHERE id = 2")
    else:
        conn.execute("UPDATE chess_games SET fen_sequence = '[1,1,1,1,1,1,1,1,1]'::jsonb WHERE id = 2")
    conn.commit()
    sent: list[str] = []
    assert _run_step(monkeypatch, sent) == 1
    assert "review: FAILED (StepFailed)" in capsys.readouterr().err and sent == ["review"]
    runs = conn.execute("SELECT status, error FROM pipeline_runs ORDER BY id").fetchall()
    assert len(runs) == 1 and runs[0]["status"] == "failed"  # the one row the CLI boundary wrote
    assert '"failed": 1' in runs[0]["error"] and '"games": 51' in runs[0]["error"] and '"events": 1' in runs[0]["error"]
    assert generation(conn) == before  # nothing published: game 1 not repriced, the state row untouched
    if failure == "untaggable":
        conn.execute("UPDATE chess_games SET ply_analysis = ply_analysis || %s::jsonb WHERE id = 2", ("[{}]",))
        conn.execute(
            "UPDATE chess_games SET ply_analysis = (SELECT ply_analysis FROM chess_games WHERE id = 3) WHERE id = 2"
        )
    else:
        conn.execute(
            "UPDATE chess_games SET fen_sequence = (SELECT fen_sequence FROM chess_games WHERE id = 3) WHERE id = 2"
        )
    conn.commit()
    sent = []
    assert _run_step(monkeypatch, sent) == 0 and sent == []
    conn.rollback()
    assert generation(conn) != before and events_of(conn, 1)[0]["cost"] != cost
    st = state(conn)
    assert st and st["fingerprint"] and st["fingerprint_at"] == st["computed_at"]
    assert run(conn)["skipped"] is True


OLD_RECORD_STATE = """
    INSERT INTO review_detection_state (player_id, config_version, unknown_candidates, window_games)
    VALUES (%(pid)s, %(cv)s, %(unknown)s, %(games)s)
    ON CONFLICT (player_id) DO UPDATE
    SET config_version = EXCLUDED.config_version, unknown_candidates = EXCLUDED.unknown_candidates,
        window_games = EXCLUDED.window_games, computed_at = now()
"""  # core/review/write.py before migration 015, verbatim


def test_a_generation_an_older_client_publishes_carries_no_proof(
    clean: psycopg.Connection[DictRow], fresh_db_url: str
) -> None:
    conn = clean
    _seed(conn)
    full_then_skip(conn)
    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as other:  # another client
        other.execute(OLD_RECORD_STATE, {"pid": PLAYER_ID, "cv": 2, "unknown": 0, "games": 51})
        other.commit()
    st = state(conn)
    assert st and st["fingerprint"] and st["fingerprint_at"] < st["computed_at"]
    reads: list[list[int]] = []
    assert "skipped" not in run(conn, after_read=reads.append) and len(reads) == 1
    st = state(conn)
    assert st and st["fingerprint_at"] == st["computed_at"]
    assert run(conn)["skipped"] is True


def test_migration_015_leaves_the_existing_row_without_proof(fresh_db_url: str, tmp_path: Path) -> None:
    from tests.test_schema import _scratch

    url = _scratch(fresh_db_url, "v14")
    v14 = tmp_path / "v14"
    v14.mkdir()
    for n, path in schema.migration_files():
        if n <= 14:
            (v14 / path.name).write_text(path.read_text())
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL((FIXTURES / "schema_baseline.sql").read_text()))  # type: ignore[arg-type]  # repo fixture
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
        assert schema.upgrade(c, v14)[-1] == 14
        analysed_game(c, 1, qh_ctx(), days_ago=1)
        c.execute(OLD_RECORD_STATE, {"pid": PLAYER_ID, "cv": 2, "unknown": 0, "games": 1})
        c.commit()
        assert 15 in schema.upgrade(c)
        st = state(c)
        assert st and st["fingerprint"] is None and st["fingerprint_at"] is None
        reads: list[list[int]] = []
        assert "skipped" not in review.run(c, after_read=reads.append) and reads == [[1]]
        c.commit()
        st = state(c)
        assert st and st["fingerprint"] and st["fingerprint_at"] == st["computed_at"]
        assert review.run(c)["skipped"] is True


def test_the_fingerprint_statement_returns_scalars_and_shares_the_window_rows(
    clean: psycopg.Connection[DictRow],
) -> None:
    """One row, no payload: no column of the fingerprint read is jsonb or a FEN list, and the rows
    it digests are the rows the read returns — one shared fragment, so the two cannot drift."""
    conn = clean
    _seed(conn)
    cur = conn.execute(window._FINGERPRINT, {"pid": PLAYER_ID, "window": 60})  # type: ignore[reportPrivateUsage]
    rows = cur.fetchall()
    assert len(rows) == 1 and cur.description
    jsonb_oid = conn.execute("SELECT 'jsonb'::regtype::oid AS o").fetchone()["o"]  # type: ignore[index]
    assert all(d.type_code != jsonb_oid for d in cur.description)
    assert {d.name for d in cur.description} == {
        "games",
        "games_digest",
        "repertoire_digest",
        "motif_digest",
        "schema_version",
        "proven",
    }
    assert window._WINDOW_ROWS in window._WINDOW and window._WINDOW_ROWS in window._FINGERPRINT  # type: ignore[reportPrivateUsage]
