"""The /review routes over the read layer's corpus: shapes, paging, the 422 / 404 boundaries, and
the stamp's commit."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from tests.review.test_read import corpus  # noqa: F401  (the fixture)


@pytest.fixture()
def client(app_env: None, corpus: psycopg.Connection[DictRow]) -> TestClient:  # noqa: F811
    corpus.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_requires_login(app_env: None) -> None:
    from api.main import create_app

    c = TestClient(create_app())
    assert c.get("/review").status_code == 401
    assert c.get("/review/pools/faded/events").status_code == 401
    assert c.post("/review/pools/faded/shown").status_code == 401


def test_page_and_drill_down(client: TestClient) -> None:
    body = client.get("/review").json()
    assert set(body) == {"categories", "page", "filter"}
    assert set(body["categories"]) == {"opening", "oversights", "endgame", "faded", "lost_wins"}
    assert body["filter"] == {
        "time_class": "focus",
        "opening": "__all__",
        "openings": [
            {"key": "__all__", "label": "All openings", "to_review_games": 6},
            {"key": "Scandinavian", "label": "Scandinavian", "to_review_games": 4},
            {"key": "Caro-Kann Defense", "label": "Caro-Kann Defense", "to_review_games": 1},
            {"key": "Italian", "label": "Italian", "to_review_games": 1},
        ],
        "group_by": "variation",
    }
    assert body["page"] == {"total_games": 9, "to_review_games": 8}
    sub = body["categories"]["opening"]["families"][0]["subgroups"][0]
    assert sub["representative_game"]["url"] == "https://example.test/1"
    d = client.get(f"/review/pools/{sub['subgroup_id']}/events", params={"reviewed_scope": "all"}).json()
    assert (d["total"], d["page"], d["page_size"], d["total_pages"]) == (5, 1, 50, 1)
    assert [e["chess_game_id"] for e in d["events"]] == [1, 2, 3, 4, 5]
    assert d["events"][0]["best_move"] == "Nf3" and d["events"][0]["reviewed"] is False
    assert client.get(f"/review/pools/{sub['subgroup_id']}/events", params={"page": 2}).json()["events"] == []

    focused = client.get("/review", params={"opening": "Italian", "group_by": "position", "time_class": "all"}).json()
    assert focused["filter"]["opening"] == "Italian" and focused["filter"]["group_by"] == "position"
    assert focused["categories"]["opening"]["families"][0]["label"] == "Italian"


def test_parameter_boundaries(client: TestClient) -> None:
    r = client.get("/review", params={"opening": "Sicilian"})
    assert r.status_code == 422 and "unknown opening key" in r.json()["detail"]
    assert client.get("/review", params={"group_by": "colour"}).status_code == 422
    assert client.get("/review", params={"time_class": "blitz"}).status_code == 422
    assert client.get("/review/pools/faded/events", params={"reviewed_scope": "mine"}).status_code == 422
    assert client.get("/review/pools/faded/events", params={"page": 0}).status_code == 422
    fam = client.get("/review").json()["categories"]["opening"]["families"][0]["family_id"]
    r = client.get(f"/review/pools/{fam}/events", params={"opening": "Sicilian"})
    assert r.status_code == 422 and "unknown opening key" in r.json()["detail"]
    # A route pool never validates the opening.
    assert (
        client.get("/review/pools/v1:route:lapse_defense:knight/events", params={"opening": "Sicilian"}).status_code
        == 200
    )
    for missing in ("v1:route:faded:x", "v1:route:lapse_offense:fork", "v1:fam:" + "0" * 16, "nonsense", "x" * 300):
        assert client.get(f"/review/pools/{missing}/events").status_code == 404, missing
        assert client.post(f"/review/pools/{missing}/shown").status_code == 404, missing


def test_shown_commits_the_canonical_id(client: TestClient, corpus: psycopg.Connection[DictRow]) -> None:  # noqa: F811
    assert client.post("/review/pools/faded/shown").status_code == 204
    assert client.post("/review/pools/v1:route:faded/shown", params={"opening": "Sicilian"}).status_code == 204
    corpus.rollback()
    rows = corpus.execute("SELECT pool_id FROM review_pool_state").fetchall()
    assert [r["pool_id"] for r in rows] == ["v1:route:faded"]
    fam = client.get("/review").json()["categories"]["opening"]["families"][0]["family_id"]
    r = client.post(f"/review/pools/{fam}/shown", params={"opening": "Sicilian"})
    assert r.status_code == 422
    corpus.rollback()
    assert corpus.execute("SELECT count(*) AS n FROM review_pool_state").fetchone() == {"n": 1}


def test_a_planted_chess960_row_is_served_nowhere(client: TestClient, corpus: psycopg.Connection[DictRow]) -> None:  # noqa: F811
    body = client.get("/review", params={"time_class": "all"}).json()
    sub = body["categories"]["opening"]["families"][0]["subgroups"][0]
    d = client.get(
        f"/review/pools/{sub['subgroup_id']}/events", params={"time_class": "all", "reviewed_scope": "all"}
    ).json()
    assert 10 not in {e["chess_game_id"] for e in d["events"]}
    corpus.execute("DELETE FROM review_events WHERE chess_game_id <> 10")
    corpus.commit()
    assert client.get("/review", params={"time_class": "all"}).json()["page"]["total_games"] == 0
    assert client.get(f"/review/pools/{sub['subgroup_id']}/events", params={"time_class": "all"}).status_code == 404
    assert client.post(f"/review/pools/{sub['subgroup_id']}/shown", params={"time_class": "all"}).status_code == 404
