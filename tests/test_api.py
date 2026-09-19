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
