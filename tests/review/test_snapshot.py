"""The hourly snapshot of Review's position sections: it is exactly what the page would compute,
and the page uses it only while it still describes the data and the settings."""

from __future__ import annotations

import json
from typing import Any, LiteralString, cast

import psycopg
import pytest
from psycopg.rows import DictRow

from core import settings as settings_store
from core.review import read, snapshot
from core.settings import Settings
from tests.review.position_helpers import ITALIAN, QGD, SCANDI, Games


def _dump(page: dict[str, Any]) -> str:
    return json.dumps(page, sort_keys=True, default=str)


@pytest.fixture()
def corpus(clean: psycopg.Connection[DictRow]) -> Games:
    g = Games(clean)
    for i in range(14):
        g.add(SCANDI, family="Scandinavian Defense", days=1 + i * 3, result=("loss", "draw")[i % 2])
        g.add(ITALIAN, colour="white", family="Italian Game", days=2 + i * 3, result=("win", "loss")[i % 2])
    for i in range(3):
        g.add(QGD, family="Queen's Gambit Declined", days=4 + i, time_class="blitz")
    clean.commit()
    return g


def _live(conn: psycopg.Connection[DictRow], time_class: str, opening: str) -> str:
    conn.execute("SAVEPOINT live")
    conn.execute("DELETE FROM review_snapshots")
    page = read.page(conn, settings_store.load(conn), time_class=time_class, opening=opening)
    conn.execute("ROLLBACK TO SAVEPOINT live")
    return _dump(page)


def test_the_snapshot_is_exactly_the_page_for_every_filter(clean: psycopg.Connection[DictRow], corpus: Games) -> None:
    config = Settings()
    summary = snapshot.build(clean, config)
    pairs = clean.execute("SELECT time_class, opening FROM review_snapshots ORDER BY 1, 2").fetchall()
    assert summary["snapshots"] == len(pairs) == 2 * 3  # two time classes, all openings and two families each
    for pair in pairs:
        assert snapshot.read(clean, config, pair["time_class"], pair["opening"]) is not None
        served = _dump(read.page(clean, config, time_class=pair["time_class"], opening=pair["opening"]))
        assert served == _live(clean, pair["time_class"], pair["opening"])


@pytest.mark.parametrize(
    "changed",
    [
        {"review_recency_half_life_days": 45},
        {"review_position_min_games": 5},
        {"review_position_max_ply": 12},
        {"review_history_months": 6},
        {"time_class_focus": "all"},
    ],
)
def test_a_changed_setting_makes_the_page_compute_for_itself(
    clean: psycopg.Connection[DictRow], corpus: Games, changed: dict[str, Any]
) -> None:
    snapshot.build(clean, Settings())
    assert snapshot.read(clean, Settings(), "focus", "__all__") is not None
    assert snapshot.read(clean, Settings(**changed), "focus", "__all__") is None


CHANGES = {
    "a new game outside the time class": "INSERT",
    "a game's family": "UPDATE chess_games SET canonical_family = 'Elsewhere', canonical_variation = 'x' WHERE id = 3",
    "a game's result": "UPDATE player_games SET result = 'win' WHERE chess_game_id = 3",
    "a game's rating": "UPDATE player_games SET opponent_rating = 2400 WHERE chess_game_id = 3",
    "an older game's date": "UPDATE chess_games SET played_at = played_at - interval '1 day' WHERE id = 20",
    "a game gaining its prefix": "UPDATE chess_games SET opening_keys = bq_opening_keys('[]'::jsonb) WHERE id = 29",
    "a new evaluation": "INSERT INTO position_evals (board_key, fen, eval_cp, depth) VALUES (1, 'x', 10, 18)",
    "a changed evaluation": "UPDATE position_evals SET eval_cp = eval_cp + 1",
    "a migration": "INSERT INTO schema_version (version) VALUES (999)",
}


@pytest.mark.parametrize("change", list(CHANGES))
def test_a_change_to_the_data_makes_the_page_compute_for_itself(
    clean: psycopg.Connection[DictRow], corpus: Games, change: str
) -> None:
    config = Settings()
    clean.execute("INSERT INTO position_evals (board_key, fen, eval_cp, depth) VALUES (2, 'y', 5, 18)")
    clean.execute("UPDATE chess_games SET opening_keys = NULL WHERE id = 29")
    clean.commit()
    snapshot.build(clean, config)
    assert snapshot.read(clean, config, "focus", "__all__") is not None
    if CHANGES[change] == "INSERT":
        corpus.add(QGD, time_class="blitz", days=0.5)
    else:
        clean.execute(cast(LiteralString, CHANGES[change]))
    assert snapshot.read(clean, config, "focus", "__all__") is None


def test_other_code_and_an_unbuilt_opening_compute_for_themselves(
    clean: psycopg.Connection[DictRow], corpus: Games, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Settings()
    snapshot.build(clean, config)
    monkeypatch.setattr(snapshot, "_code_stamp", lambda: "other code")
    assert snapshot.read(clean, config, "focus", "__all__") is None
    monkeypatch.undo()
    assert snapshot.read(clean, config, "focus", "__all__") is not None
    # An opening the snapshot was not built for is the live path's to judge (a 422 here).
    assert snapshot.read(clean, config, "focus", "white:Nonsense") is None
    with pytest.raises(read.ReviewParamError, match="unknown opening key"):
        read.page(clean, config, time_class="focus", opening="white:Nonsense")


def test_the_code_stamp_covers_the_review_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import constants

    snapshot._code_stamp.cache_clear()  # type: ignore[reportPrivateUsage]
    before = snapshot._code_stamp()  # type: ignore[reportPrivateUsage]
    monkeypatch.setattr(constants, "REVIEW_LEAK_THRESHOLD", 0.05)
    snapshot._code_stamp.cache_clear()  # type: ignore[reportPrivateUsage]
    assert snapshot._code_stamp() != before  # type: ignore[reportPrivateUsage]
    monkeypatch.undo()
    snapshot._code_stamp.cache_clear()  # type: ignore[reportPrivateUsage]
    assert snapshot._code_stamp() == before  # type: ignore[reportPrivateUsage]


def test_a_rebuild_replaces_every_row(clean: psycopg.Connection[DictRow], corpus: Games) -> None:
    config = Settings()
    clean.execute(
        "INSERT INTO review_snapshots (time_class, opening, fingerprint, body)"
        " VALUES ('focus', 'black:Gone', 'old', '{}'::jsonb)"
    )
    clean.commit()
    snapshot.build(clean, config)
    openings = {r["opening"] for r in clean.execute("SELECT opening FROM review_snapshots").fetchall()}
    assert "black:Gone" not in openings and "__all__" in openings
