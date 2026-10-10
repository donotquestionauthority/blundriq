import json
from collections.abc import Callable
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(app_env) -> TestClient:
    from api.main import create_app

    return TestClient(create_app())


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_settings_require_login(client: TestClient) -> None:
    assert client.get("/settings").status_code == 401


def test_login_logout_flow(client: TestClient) -> None:
    assert client.post("/login", json={"password": "wrong"}).status_code == 401
    r = client.post("/login", json={"password": "correct horse"})
    assert r.status_code == 200 and "bq_session" in r.cookies
    assert client.get("/me").json() == {"authenticated": True}
    assert client.get("/settings").status_code == 200
    bad = client.put("/settings", json={"daily_puzzle_target": 0})
    assert bad.status_code == 422
    good = client.put("/settings", json={"daily_puzzle_target": 15})
    assert good.status_code == 200 and good.json()["daily_puzzle_target"] == 15
    client.post("/logout")
    assert client.get("/me").status_code == 401


def test_startup_fails_closed_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import secrets

    monkeypatch.delenv("PASSWORD_HASH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "dsn-placeholder")
    monkeypatch.setenv("SESSION_SECRET", "s")
    with pytest.raises(secrets.MissingSecret, match="PASSWORD_HASH"):
        secrets.api()


# --- POST /home/pipeline/run ------------------------------------------------------------


@pytest.fixture()
def logged_in(client: TestClient) -> TestClient:
    assert client.post("/login", json={"password": "correct horse"}).status_code == 200
    return client


def _github(status: int, seen: list[httpx.Request]) -> Callable[[], httpx.Client]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, text='{"message": "Bad credentials; token ghp_SECRET"}')

    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def test_run_pipeline_requires_login(client: TestClient) -> None:
    assert client.post("/home/pipeline/run").status_code == 401


def test_run_pipeline_dispatches_the_workflow_on_main(logged_in: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import dispatch

    monkeypatch.setenv("DISPATCH_TOKEN", "token-for-the-test")
    monkeypatch.setenv("DISPATCH_REPO", "someone/some-repo")
    seen: list[httpx.Request] = []
    monkeypatch.setattr(dispatch, "client", _github(204, seen))
    r = logged_in.post("/home/pipeline/run")
    assert r.status_code == 202
    assert datetime.fromisoformat(r.json()["requested_at"]).tzinfo is not None
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url == "https://api.github.com/repos/someone/some-repo/actions/workflows/pipeline.yml/dispatches"
    assert json.loads(seen[0].content) == {"ref": "main"}
    assert seen[0].headers["authorization"] == "Bearer token-for-the-test"
    assert seen[0].headers["x-github-api-version"] == "2022-11-28"


def test_a_refusal_reaches_the_page_as_a_status_code_and_nothing_else(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import dispatch

    monkeypatch.setenv("DISPATCH_TOKEN", "token-for-the-test")
    monkeypatch.setenv("DISPATCH_REPO", "someone/some-repo")
    monkeypatch.setattr(dispatch, "client", _github(401, []))
    r = logged_in.post("/home/pipeline/run")
    assert r.status_code == 502
    assert r.json() == {"detail": "GitHub refused the dispatch (HTTP 401)"}
    assert "SECRET" not in r.text and "some-repo" not in r.text and "token-for" not in r.text


def test_an_unreachable_github_is_said_without_the_url(logged_in: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import dispatch

    monkeypatch.setenv("DISPATCH_TOKEN", "token-for-the-test")
    monkeypatch.setenv("DISPATCH_REPO", "someone/some-repo")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"dns failure for {request.url}")

    monkeypatch.setattr(dispatch, "client", lambda: httpx.Client(transport=httpx.MockTransport(unreachable)))
    r = logged_in.post("/home/pipeline/run")
    assert r.status_code == 502
    assert r.json() == {"detail": "GitHub unreachable"}


def test_run_pipeline_is_unavailable_until_its_secrets_are_set(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import dispatch

    monkeypatch.delenv("DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("DISPATCH_REPO", raising=False)
    seen: list[httpx.Request] = []
    monkeypatch.setattr(dispatch, "client", _github(204, seen))
    r = logged_in.post("/home/pipeline/run")
    assert r.status_code == 503 and r.json() == {"detail": "dispatch is not configured"}
    assert seen == []
    monkeypatch.setenv("DISPATCH_TOKEN", "t")  # one of the two is not enough
    assert logged_in.post("/home/pipeline/run").status_code == 503
    assert seen == []


# --- POST /games/{id}/ask and POST /explore/ask ------------------------------------------------


def _snapshot(**kw: object) -> dict[str, object]:
    return {"depth": 16, "eval_cp": 30, "best_move": "d2d4", "pv": ["d2d4", "d7d5"], **kw}


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_ask_requires_login(client: TestClient) -> None:
    assert client.post("/games/1/ask", json={"ply": 0}).status_code == 401
    assert (
        client.post("/explore/ask", json={"seed_fen": START, "orientation": "white", "engine": _snapshot()}).status_code
        == 401
    )


def test_ask_bodies_are_bounded_before_core_sees_them(logged_in: TestClient) -> None:
    explore = {"seed_fen": START, "orientation": "white", "engine": _snapshot()}
    for body in (
        {**explore, "seed_fen": "x" * 101},
        {**explore, "moves": ["e4"] * 61},
        {**explore, "moves": ["e4" + "x" * 9]},
        {**explore, "engine": _snapshot(pv=["d2d4"] * 13)},
        {**explore, "engine": _snapshot(best_move="d2d4qq")},
        {**explore, "engine": _snapshot(depth=65)},
        {**explore, "orientation": "red"},
        {**explore, "game": {"id": 0, "ply": 0}},
        {**explore, "question": "x" * 2001},
    ):
        assert logged_in.post("/explore/ask", json=body).status_code == 422, body
    for body in ({"ply": -1}, {"ply": 2001}, {"ply": 0, "alternative": {"move": "x" * 11}}):
        assert logged_in.post("/games/1/ask", json=body).status_code == 422, body


def test_ask_turns_a_refusal_into_its_status_and_nothing_else(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import ai

    assert logged_in.post("/games/1/ask", json={"ply": 0}).json() == {"detail": "game not found"}

    def refused(*args: object, **kwargs: object) -> dict[str, object]:
        raise ai.ExplainError(502, "AI provider refused the call (HTTP 529, overloaded_error)")

    monkeypatch.setattr(ai, "ask_explore", refused)
    r = logged_in.post("/explore/ask", json={"seed_fen": START, "orientation": "white", "engine": _snapshot()})
    assert r.status_code == 502 and r.json() == {"detail": "AI provider refused the call (HTTP 529, overloaded_error)"}


def test_ask_explore_hands_core_the_whole_body(logged_in: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import ai

    seen: list[dict[str, object]] = []

    def record(tx: object, body: dict[str, object], *, dry_run: bool, client: object = None) -> dict[str, object]:
        seen.append({**body, "dry_run_arg": dry_run})
        return {"dry_run": True}

    monkeypatch.setattr(ai, "ask_explore", record)
    body = {
        "seed_fen": START,
        "orientation": "black",
        "moves": ["e4"],
        "engine": _snapshot(),
        "question": "why?",
        "alternative": {"move": "d4"},
        "game": {"id": 3, "ply": 0},
        "dry_run": True,
    }
    assert logged_in.post("/explore/ask", json=body).status_code == 200
    assert seen == [{**body, "alternative": {"move": "d4", "engine": None}, "dry_run_arg": True}]
