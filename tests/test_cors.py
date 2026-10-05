"""Which web pages may call the API with the session cookie: exactly the origins
ALLOWED_ORIGINS names (conftest's app_env names https://chess.example.org and the Vite dev
server). Anyone deploying the UI elsewhere sets their own; nothing is built in."""

from __future__ import annotations

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from api.main import _ConfiguredCors, create_app, parse_origins
from core.secrets import MissingSecret

PREFLIGHT = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}


def test_a_configured_origin_gets_a_credentialed_preflight(app_env: None) -> None:
    with TestClient(create_app()) as c:
        r = c.options("/login", headers={"Origin": "https://chess.example.org", **PREFLIGHT})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://chess.example.org"
    assert r.headers["access-control-allow-credentials"] == "true"


def test_an_unrelated_origin_is_refused(app_env: None) -> None:
    with TestClient(create_app()) as c:
        pre = c.options("/login", headers={"Origin": "https://elsewhere.example.net", **PREFLIGHT})
        simple = c.get("/health", headers={"Origin": "https://elsewhere.example.net"})
    assert pre.status_code == 400 and "access-control-allow-origin" not in pre.headers
    assert simple.status_code == 200 and "access-control-allow-origin" not in simple.headers


def test_no_origin_is_built_in(app_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is admitted by default, the dev server included: with one origin configured,
    every other page is refused."""
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://chess.example.org")
    with TestClient(create_app()) as c:
        for origin in ("http://localhost:5173", "https://example.com", "https://app.example.com"):
            r = c.options("/login", headers={"Origin": origin, **PREFLIGHT})
            assert r.status_code == 400, origin


def test_the_built_cors_admits_exactly_the_configured_origins(app_env: None) -> None:
    """Probing a few origins cannot prove nothing else was added; the middleware's own
    configuration can: the configured list, no pattern, the cookie allowed."""
    app = create_app()
    with TestClient(app) as c:
        c.get("/health")
    node = app.middleware_stack
    while node is not None and not isinstance(node, _ConfiguredCors):
        node = getattr(node, "app", None)
    assert node is not None and isinstance(node._cors, CORSMiddleware)  # pyright: ignore[reportPrivateUsage]
    cors = node._cors  # pyright: ignore[reportPrivateUsage]
    assert cors.allow_origins == ["https://chess.example.org", "http://localhost:5173"]
    assert cors.allow_origin_regex is None and cors.allow_all_origins is False


def test_startup_fails_without_allowed_origins(app_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_ORIGINS")
    with pytest.raises(MissingSecret, match="ALLOWED_ORIGINS"), TestClient(create_app()):
        pass


def test_startup_fails_on_a_malformed_value(app_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"), TestClient(create_app()):
        pass


def test_a_request_without_startup_still_reads_the_value(app_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that skips the lifespan (TestClient outside `with`) builds CORS on its first
    request, from the same value, and fails the same way."""
    monkeypatch.delenv("ALLOWED_ORIGINS")
    with pytest.raises(MissingSecret, match="ALLOWED_ORIGINS"):
        TestClient(create_app()).get("/health")


@pytest.mark.parametrize(
    "raw,origins",
    [
        ("https://chess.example.org", ["https://chess.example.org"]),
        (
            " https://a.example.org , https://b.example.org:8443 ,",
            ["https://a.example.org", "https://b.example.org:8443"],
        ),
        ("http://localhost:5173,http://127.0.0.1:5173", ["http://localhost:5173", "http://127.0.0.1:5173"]),
    ],
)
def test_parse_origins_accepts(raw: str, origins: list[str]) -> None:
    assert parse_origins(raw) == origins


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "https://*.example.org",
        "https://chess.example.org/",
        "https://chess.example.org/app",
        "https://chess.example.org?x=1",
        "http://chess.example.org",
        "chess.example.org",
        "https://Chess.example.org",
        "https://user" + "@chess.example.org",  # split: the scanner reads it as an address
        "https://chess.example.org:http",
        "https://chess.example.org:443",
        "http://localhost:80",
        "https://chess.example.org:",
        "https://chess.example.org:99999",
        "https://exa mple.org",
        "https://chess.example.org.",
        "https://%61.example.org",
        "https://b\u00fccher.example",
        "https://-chess.example.org",
        " , ",
    ],
)
def test_parse_origins_refuses(raw: str) -> None:
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        parse_origins(raw)
