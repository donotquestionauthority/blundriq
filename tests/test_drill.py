"""A drill of chosen lines (core/puzzles/drill.py): the mint, the whole-line presentation, the
proof of service that keeps a drill attempt gradable, and what `drilled_at` changes in the
generator and the visibility read."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from core.constants import PLAYER_ID
from core.puzzles import attempts, drill, serve, visibility
from core.puzzles.generate import repertoire as gen
from core.settings import Settings
from tests import repertoire_helpers as h
from tests.test_practice import _attempt as _graded
from tests.test_practice import _config, _deviation, _line, _rep_puzzle, _segment

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3"]  # 7 plies: White's last move is ply 6
SCANDI = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5", "d4"]
BLACK_LINE = ["e4", "c5", "Nf3", "d6", "d4", "cxd4"]  # Black's last move is ply 5


@pytest.fixture()
def db(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    h.player(clean)
    h.book(clean, 901, "White: Italian", "white")
    h.chapter(clean, 901, 901, "Main line")
    h.line(clean, 901, 901, "Giuoco", ITALIAN)
    h.line(clean, 902, 901, "Scandi: 4.d4", SCANDI)
    h.book(clean, 903, "Black: Sicilian", "black")
    h.chapter(clean, 903, 903, "Open")
    h.line(clean, 903, 903, "Open Sicilian", BLACK_LINE)
    return clean


def _own_moves(moves: list[str], first: int) -> str:
    return ",".join(m for i, m in enumerate(moves) if i % 2 == first)


def test_a_drill_mints_the_missing_puzzles_serves_whole_lines_in_order_and_skips_what_it_cannot(
    db: psycopg.Connection[DictRow],
) -> None:
    h.line(db, 904, 901, "Off", ["d4", "d5"], active=False)
    out = drill.serve_drill(db, _config(), [902, 901, 903, 902, 904, 777])
    assert out["batch_id"] == 1 and out["skipped"] == [904, 777]
    rows = out["puzzles"]
    assert [r["repertoire_line_id"] for r in rows] == [902, 901, 903]
    # Whole lines: the last player ply, from the first board, with the solver payload intact.
    assert [(r["presentation_ply"], r["fen"] == h.START) for r in rows] == [(6, True), (6, True), (5, True)]
    assert rows[2]["presentation_fen"] == h.spine(None, BLACK_LINE)[5]
    assert all(r["srs"] is None and r["play_batch_id"] == 1 and r["attempt_count"] == 0 for r in rows)
    pz = db.execute(
        "SELECT repertoire_line_id AS l, active, drilled_at IS NOT NULL AS d, title FROM puzzles ORDER BY repertoire_line_id"
    ).fetchall()
    assert [(p["l"], p["active"], p["d"]) for p in pz] == [(901, True, True), (902, True, True), (903, True, True)]
    assert pz[1]["title"] == "White: Italian › Main line › Scandi: 4.d4"
    exposures = db.execute(
        "SELECT scope, batch_id, puzzle_id, presentation_ply FROM player_puzzle_exposure ORDER BY id"
    ).fetchall()
    assert [(e["scope"], e["batch_id"], e["presentation_ply"]) for e in exposures] == [("drill", 1, 6)] * 2 + [
        ("drill", 1, 5)
    ]
    # A second drill: the puzzles exist, drilled_at is kept, the batch id advances.
    stamp = db.execute("SELECT drilled_at FROM puzzles WHERE repertoire_line_id = 901").fetchone()
    again = drill.serve_drill(db, _config(), [901])
    assert again["batch_id"] == 2 and [r["id"] for r in again["puzzles"]] == [rows[1]["id"]]
    assert db.execute("SELECT drilled_at FROM puzzles WHERE repertoire_line_id = 901").fetchone() == stamp


def test_the_cap_and_a_line_named_twice(db: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(drill.TooManyLines):
        drill.serve_drill(db, _config(), list(range(1, 52)))
    assert drill.serve_drill(db, _config(), [901] * 60)["batch_id"] == 1  # one line named sixty times is one line


def test_a_drilled_line_joins_regular_practice_regardless_of_the_deviation_gate(
    db: psycopg.Connection[DictRow],
) -> None:
    config = _config(deviation_puzzle_min_occurrences=3)
    # Before the drill: no deviations, so no puzzle anywhere.
    assert gen.generate(db, config)["created"] == 0
    assert visibility.visible_rows(db, last_n_games=0, lookahead_plies=2) == []
    assert serve.count_eligible(db, config) == 0
    (row,) = drill.serve_drill(db, config, [902])["puzzles"]
    pid = int(row["id"])
    # The generator keeps it (below the gate, drilled); an undrilled puzzle below the gate goes off.
    other = db.execute(
        "INSERT INTO puzzles (fen, solution_line, solution_fen_sequence, source_types, color, is_repertoire,"
        " repertoire_line_id, player_id, title, active) VALUES (%s, %s::jsonb, %s::jsonb, ARRAY['deviation'], 'w', TRUE,"
        " 901, %s, 'x', TRUE) RETURNING id",
        (h.START, json.dumps(ITALIAN), json.dumps(h.spine(None, ITALIAN)), PLAYER_ID),
    ).fetchone()
    assert other is not None
    stats = gen.generate(db, config)
    assert stats["deactivated"] == 1 and stats["unchanged"] == 1
    states = db.execute("SELECT id, active FROM puzzles ORDER BY id").fetchall()
    assert [(s["id"] == pid, s["active"]) for s in states] == [(True, True), (False, False)]
    # Visible in the regular queue, the scope list and the due count, presented as the whole line.
    visible = visibility.visible_rows(db, last_n_games=0, lookahead_plies=2)
    assert [(v["id"], v["presentation_ply"], v["occurrence_count"]) for v in visible] == [(pid, 6, 0)]
    assert visibility.presentation_ply(db, pid, lookahead_plies=2) == 6
    assert serve.count_eligible(db, config) == 1
    scopes = visibility.repertoire_scopes(db, lookahead_plies=2)
    assert [(b["id"], [ln["id"] for ch in b["chapters"] for ln in ch["lines"]]) for b in scopes] == [(901, [902])]
    queued = serve.play_batch(db, config, last_n_games=0, ptype="repertoire", subtype="line:902")
    assert [r["id"] for r in queued.rows] == [pid]
    # Once it has deviations, the lookahead rule takes over as for any line.
    for g in (1, 2, 3):
        fens = h.game(db, g, SCANDI[:4] + ["Nf3"], color="white")
        h.result(
            db,
            g,
            book_id=901,
            chapter_id=901,
            ply=4,
            by="me",
            expected="Nc3",
            played="Nf3",
            fen=fens[4],
            line_ids=[902],
        )
    assert visibility.presentation_ply(db, pid, lookahead_plies=2) == 6
    assert visibility.presentation_ply(db, pid, lookahead_plies=0) == 4


def test_a_drill_exposure_never_holds_a_puzzle_back_from_the_queue(db: psycopg.Connection[DictRow]) -> None:
    """An abandoned drill (never attempted) is not pending work: the regular queue still mints
    the puzzle, and the drill scope is never served by `play_batch`."""
    config = _config()
    (row,) = drill.serve_drill(db, config, [902])["puzzles"]
    pid = int(row["id"])
    queued = serve.play_batch(db, config, last_n_games=0, ptype="repertoire", subtype=None)
    assert [r["id"] for r in queued.rows] == [pid]
    assert queued.scope == "repertoire"
    scopes = db.execute("SELECT DISTINCT scope FROM player_puzzle_exposure ORDER BY scope").fetchall()
    assert [s["scope"] for s in scopes] == ["drill", "repertoire"]


def _record(
    conn: psycopg.Connection[DictRow], pid: int, moves: str, ply: int, *, seq: int, session: str
) -> dict[str, Any]:
    return attempts.record(
        conn,
        pid,
        _config(),
        claimed=True,
        moves_played=moves,
        attempt_id=str(uuid.uuid4()),
        session_id=session,
        presentation_ply=ply,
        attempt_seq=seq,
    )


def test_a_served_drill_attempt_grades_after_the_chapter_is_switched_off_in_every_order_a_load_can_arrive(
    db: psycopg.Connection[DictRow],
) -> None:
    (row,) = drill.serve_drill(db, _config(), [903])["puzzles"]
    pid, ply = int(row["id"]), int(row["presentation_ply"])
    right = _own_moves(BLACK_LINE, 1)
    db.execute("UPDATE chapters SET active = FALSE WHERE id = 903")
    assert visibility.attemptable(db, pid) is None
    load = str(uuid.uuid4())
    # The first (wrong) attempt, Try Again's new id, and a Replay: every one grades.
    assert _record(db, pid, "c5,d6,Nf6", ply, seq=1, session=load)["solved"] is False
    assert _record(db, pid, right, ply, seq=2, session=load)["solved"] is True
    assert _record(db, pid, right, ply, seq=3, session=load)["solved"] is True
    # Another load whose second attempt reaches the server before its first: both grade, and
    # the statistic still counts the load as first-wrong.
    late = str(uuid.uuid4())
    assert _record(db, pid, right, ply, seq=2, session=late)["solved"] is True
    assert _record(db, pid, "c5,d6,Nf6", ply, seq=1, session=late)["solved"] is False
    from core.puzzles import stats

    assert stats.by_line(db, [903]) == {903: {"loads": 2, "first_right": 0, "first_wrong": 2}}
    # An idempotent retry of a recorded id answers whatever the proof says.
    aid = str(uuid.uuid4())
    first = attempts.record(
        db, pid, _config(), claimed=True, moves_played=right, attempt_id=aid, session_id=late, presentation_ply=ply
    )
    retry = attempts.record(
        db, pid, _config(), claimed=True, moves_played=right, attempt_id=aid, session_id=late, presentation_ply=ply
    )
    assert first["solved"] is True and retry["detail"] == "attempt recorded (idempotent)"
    # Past the proof window the exposure proves nothing any more.
    db.execute("UPDATE player_puzzle_exposure SET served_at = served_at - interval '8 days' WHERE scope = 'drill'")
    with pytest.raises(attempts.NotAttemptable):
        _record(db, pid, right, ply, seq=4, session=str(uuid.uuid4()))
    # Nor does a drill exposure that was never attempted: it is not "pending" queue work.
    (other,) = drill.serve_drill(db, _config(), [901])["puzzles"]
    db.execute("UPDATE books SET active = FALSE WHERE id = 901")
    db.execute(
        "UPDATE player_puzzle_exposure SET served_at = served_at - interval '8 days' WHERE puzzle_id = %s",
        (other["id"],),
    )
    with pytest.raises(attempts.NotAttemptable):
        _record(db, int(other["id"]), "e4,Nf3,Bc4,c3", int(other["presentation_ply"]), seq=1, session=str(uuid.uuid4()))
    db.execute("UPDATE player_puzzle_exposure SET served_at = now() WHERE puzzle_id = %s", (other["id"],))
    assert (
        _record(
            db, int(other["id"]), "e4,Nf3,Bc4,c3", int(other["presentation_ply"]), seq=1, session=str(uuid.uuid4())
        )["solved"]
        is True
    )
    # A regular-queue exposure's proof is unchanged by all this: acknowledged is finished.
    db.execute("UPDATE player_puzzle_exposure SET served_at = now() WHERE scope = 'drill'")
    assert _record(db, pid, right, ply, seq=5, session=str(uuid.uuid4()))["solved"] is True


def test_one_drills_proof_is_untouched_by_another_and_by_a_queue_acknowledgement(
    db: psycopg.Connection[DictRow],
) -> None:
    a = drill.serve_drill(db, _config(), [903])
    b = drill.serve_drill(db, _config(), [903, 901])
    pid = int(a["puzzles"][0]["id"])
    assert b["puzzles"][0]["id"] == pid and a["batch_id"] == 1 and b["batch_id"] == 2
    db.execute("UPDATE books SET active = FALSE WHERE id = 903")
    ply = int(a["puzzles"][0]["presentation_ply"])
    assert _record(db, pid, _own_moves(BLACK_LINE, 1), ply, seq=1, session=str(uuid.uuid4()))["solved"] is True
    assert _record(db, pid, _own_moves(BLACK_LINE, 1), ply, seq=1, session=str(uuid.uuid4()))["solved"] is True
    rows = db.execute("SELECT count(*) AS n FROM player_puzzle_exposure WHERE scope = 'drill'").fetchone()
    assert rows is not None and rows["n"] == 3  # nothing skipped, closed or deleted


def test_a_delete_and_a_toggle_wait_for_a_drill_that_holds_the_repertoire_lock(fresh_db_url: str) -> None:
    """The drill's read, mint and stamp are one transaction under the repertoire lock: a delete
    and a toggle started meanwhile both commit after it."""
    from core.repertoire import books, matching
    from core.repertoire import lines as manual

    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as a:
        h.player(a)
        h.book(a, 901, "B", "white")
        h.chapter(a, 901, 901, "C")
        h.line(a, 901, 901, "L", ITALIAN)
        h.line(a, 902, 901, "M", SCANDI)
        a.commit()
        matching.lock(a)  # stands in for a drill paused after its line read
        done: list[str] = []

        def deleter() -> None:
            with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as b:
                manual.delete(b, 901)
                b.commit()
            done.append("delete")

        def toggler() -> None:
            with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as c:
                books.set_active(c, "lines", 902, False, 0)
                c.commit()
            done.append("toggle")

        threads = [threading.Thread(target=deleter), threading.Thread(target=toggler)]
        for t in threads:
            t.start()
        time.sleep(1.0)
        assert done == []  # both blocked behind the lock the drill holds
        out = drill.serve_drill(a, _config(), [901, 902])  # the same connection: the lock is reentrant
        a.commit()
        for t in threads:
            t.join(10.0)
        assert sorted(done) == ["delete", "toggle"]
        assert [r["repertoire_line_id"] for r in out["puzzles"]] == [901, 902]  # both read before either change
        a.execute("DELETE FROM repertoire_lines")
        a.execute("DELETE FROM chapters")
        a.execute("DELETE FROM books")
        a.execute("DELETE FROM players")
        a.commit()


def test_a_drill_waits_for_the_repertoire_lock_so_a_delete_cannot_slip_under_its_mint(fresh_db_url: str) -> None:
    with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as a:
        h.player(a)
        h.book(a, 901, "B", "white")
        h.chapter(a, 901, 901, "C")
        h.line(a, 901, 901, "L", ITALIAN)
        a.commit()
        from core.repertoire import matching

        matching.lock(a)  # a delete (or a toggle, an import) in flight
        done = threading.Event()
        result: dict[str, Any] = {}

        def driller() -> None:
            with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as b:
                result.update(drill.serve_drill(b, _config(), [901]))
                b.commit()
            done.set()

        t = threading.Thread(target=driller)
        t.start()
        assert not done.wait(1.0)  # blocked until the holder commits
        a.execute("DELETE FROM repertoire_lines WHERE id = 901")
        a.commit()
        assert done.wait(10.0)
        t.join()
        assert result["puzzles"] == [] and result["skipped"] == [901]  # the line was gone by the time the drill read it
        a.execute("DELETE FROM chapters")
        a.execute("DELETE FROM books")
        a.execute("DELETE FROM players")
        a.commit()


def test_the_whole_line_is_a_segment_the_grader_accepts_for_both_colours(clean: psycopg.Connection[DictRow]) -> None:
    """The drill's presentation ply is the last player ply, which `_is_segment` already
    accepts; a White line and a Black line from the start position."""
    h.player(clean)
    _line(clean, ITALIAN)
    pid = _rep_puzzle(clean, ITALIAN)
    for g in (1, 2, 3):
        _deviation(clean, g, 2)
    assert visibility.last_player_ply(len(ITALIAN), opens_on_player=True) == 6
    assert _graded(clean, pid, _segment(ITALIAN, 6), 6)["solved"] is True
    assert visibility.last_player_ply(len(BLACK_LINE), opens_on_player=False) == 5
    assert visibility.last_player_ply(5, opens_on_player=False) == 3
    with pytest.raises(attempts.SegmentMismatch):
        _graded(clean, pid, _segment(ITALIAN, 6), 5)


def test_the_round_trip_gap_to_catch_all_to_drill_starts_at_move_one(db: psycopg.Connection[DictRow]) -> None:
    """A gap line saved into the catch-all is drilled from the start position with every own
    move of the line to find, whatever chapter the context came from."""
    from core.repertoire import lines as manual

    stem = ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6"]
    made = manual.add_manual(
        db, moves=stem + ["c3", "Be7", "d4"], line_name="after d6", window=0, catch_all=("white", "Italian")
    )
    (row,) = drill.serve_drill(db, _config(), [int(made["id"])])["puzzles"]
    assert row["fen"] == h.START and row["presentation_ply"] == 8 and row["solution_line"] == stem + ["c3", "Be7", "d4"]
    assert row["presentation_fen"] == h.spine(None, stem + ["c3", "Be7", "d4"])[8]
    own = "e4,Nf3,Bc4,c3,d4"
    assert _record(db, int(row["id"]), own, 8, seq=1, session=str(uuid.uuid4()))["solved"] is True
    assert _record(db, int(row["id"]), "e4,Nf3,Bc4,c3,d3", 8, seq=1, session=str(uuid.uuid4()))["solved"] is False


def test_a_line_with_no_player_move_is_skipped_and_never_stamped(db: psycopg.Connection[DictRow]) -> None:
    h.line(db, 905, 903, "One opponent move", ["e4"])  # a Black line of one White move
    out = drill.serve_drill(db, _config(), [905, 903])
    assert out["skipped"] == [905] and [r["repertoire_line_id"] for r in out["puzzles"]] == [903]
    stamped = db.execute("SELECT drilled_at FROM puzzles WHERE repertoire_line_id = 905").fetchone()
    assert stamped is None or stamped["drilled_at"] is None
    assert visibility.last_player_ply(1, opens_on_player=False) == -1


def test_generate_keeps_the_settings_contract_for_drilled_puzzles(db: psycopg.Connection[DictRow]) -> None:
    """A drilled puzzle whose line is switched off is still an orphan: the orphan rule is
    unchanged by `drilled_at`."""
    drill.serve_drill(db, _config(), [902])
    db.execute("UPDATE repertoire_lines SET active = FALSE WHERE id = 902")
    out = gen.generate(db, Settings())
    assert out["orphaned"] == 1
    assert db.execute("SELECT active FROM puzzles").fetchone()["active"] is False  # type: ignore[index]
