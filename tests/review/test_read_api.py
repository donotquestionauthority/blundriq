"""The /review routes: login, shapes, the 422 / 404 boundaries, and the worklist routes that are gone."""

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
    assert c.get("/review/positions/white/1").status_code == 401
    assert c.get("/review/habits/faded").status_code == 401


def test_the_page_and_a_habit(client: TestClient) -> None:
    body = client.get("/review?time_class=all").json()
    assert set(body) == {"positions", "habits", "lost_wins", "filter", "meta"}
    card = body["positions"]["ranked"][0]
    assert isinstance(card["key"], str)
    assert client.get(f"/review/positions/{card['colour']}/{card['key']}?time_class=all").status_code == 200
    habit = client.get("/review/habits/missed:pin?time_class=all").json()
    assert habit["total"] == 12 and habit["page_size"] == 50 and habit["rows"][0]["anchor_move"] == "4.c3"
    focused = client.get("/review/habits/missed:pin?time_class=all&opening=black:Scandinavian%20Defense").json()
    assert focused["total"] == 0


def test_parameter_boundaries(client: TestClient) -> None:
    assert client.get("/review?time_class=blitz").status_code == 422
    stale = client.get("/review?opening=white:Nonsense")
    assert stale.status_code == 422 and "unknown opening key" in stale.json()["detail"]
    assert client.get("/review?opening=__all__&group_by=variation").status_code == 200  # an old tab's extra parameter
    assert client.get("/review/habits/missed:a%20b").status_code == 422
    assert client.get("/review/habits/faded?page=0").status_code == 422


def test_the_worklist_pool_routes_are_gone(client: TestClient) -> None:
    assert client.get("/review/pools/v1:route:faded/events").status_code == 404
    assert client.post("/review/pools/v1:route:faded/shown").status_code in (404, 405)
