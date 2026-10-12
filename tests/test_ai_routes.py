"""GET /ai/history (api/routes/ai.py): a read of the cached answers at a board."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


@pytest.fixture()
def client(app_env: None, clean: psycopg.Connection[DictRow]) -> TestClient:
    from api.main import create_app

    clean.execute(
        "INSERT INTO ai_explanation_cache (fen, prompt_hash, context_hash, model, prompt_label, explanation, question)"
        " VALUES (%s, 'p', 'c', 'm', 'Ask about this position', 'Because.', 'why?')",
        (AFTER_E4,),
    )
    clean.commit()
    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_history_needs_a_full_fen_and_lists_the_boards_answers(client: TestClient) -> None:
    assert client.get("/ai/history", params={"fen": AFTER_E4.rsplit(" ", 2)[0]}).status_code == 422
    assert client.get("/ai/history", params={"fen": "not a board"}).status_code == 422
    assert client.get("/ai/history").status_code == 422
    r = client.get("/ai/history", params={"fen": AFTER_E4.replace(" 0 1", " 7 4")})
    assert r.status_code == 200
    (item,) = r.json()["items"]
    assert (item["question"], item["prompt_label"], item["model"], item["explanation"]) == (
        "why?",
        "Ask about this position",
        "m",
        "Because.",
    )
    assert item["created_at"].endswith("+00:00")
    assert client.get(
        "/ai/history", params={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"}
    ).json() == {"items": []}


def test_history_is_behind_the_login(app_env: None) -> None:
    from api.main import create_app

    assert TestClient(create_app()).get("/ai/history", params={"fen": AFTER_E4}).status_code == 401
