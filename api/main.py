"""FastAPI application. Thin routes over core/.

Startup reads its secrets once (core/secrets.py) and fails closed if any is
missing. Routes are grouped in api/routes/*; each is a small module that calls
into core/ and never contains SQL of its own.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from api.routes import auth as auth_routes
from api.routes import blunders as blunders_routes
from api.routes import deviations as deviations_routes
from api.routes import explore as explore_routes
from api.routes import games as games_routes
from api.routes import home as home_routes
from api.routes import practice as practice_routes
from api.routes import puzzles as puzzles_routes
from api.routes import repertoire as repertoire_routes
from api.routes import review as review_routes
from api.routes import scout as scout_routes
from api.routes import settings as settings_routes
from core import db, secrets

_LOCAL_HOSTS = ("localhost", "127.0.0.1")
# What a browser sends as Origin: scheme, a lower-case ASCII host (labels of letters, digits and
# inner hyphens, no trailing dot) and a port only when it is not the scheme's default.
_ORIGIN = re.compile(
    r"(https?)://((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(?::([1-9][0-9]{0,4}))?"
)
_DEFAULT_PORT = {"https": "443", "http": "80"}


def parse_origins(raw: str) -> list[str]:
    """ALLOWED_ORIGINS -> the exact origins CORS admits, or ValueError. Each entry is written
    the way a browser sends it, `https://chess.example.org` or `https://chess.example.org:8443`,
    with nothing after it; anything else could never match and would fail silently. Plain
    http is for a local host only, and there is no wildcard: the session cookie travels with
    every request, so an origin is named or it is refused. The value is never echoed."""
    origins: list[str] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        if entry != entry.lower():
            raise ValueError("ALLOWED_ORIGINS: write each origin in lower case, as a browser sends it")
        match = _ORIGIN.fullmatch(entry)
        if match is None:
            raise ValueError("ALLOWED_ORIGINS: each entry is scheme://host[:port], nothing after it")
        scheme, host, port = match.groups()
        if port is not None and (port == _DEFAULT_PORT[scheme] or int(port) > 65535):
            raise ValueError("ALLOWED_ORIGINS: leave out a default port, and a port is at most 65535")
        if scheme != "https" and host not in _LOCAL_HOSTS:
            raise ValueError("ALLOWED_ORIGINS: https only, except for a local host")
        origins.append(entry)
    if not origins:
        raise ValueError("ALLOWED_ORIGINS names no origin")
    return origins


class _ConfiguredCors:
    """CORS over the origins ALLOWED_ORIGINS names, built on the first HTTP request. The
    lifespan reads and validates the same value first, so a missing or malformed one stops
    the API at startup; building here rather than at import keeps `api.main` importable
    without the environment, as the tests import it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._cors: CORSMiddleware | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._cors is None:
            self._cors = CORSMiddleware(
                self.app,
                allow_origins=parse_origins(secrets.api().allowed_origins),
                allow_credentials=True,
                allow_methods=["GET", "POST", "PUT", "DELETE"],
                allow_headers=["Content-Type"],
            )
        await self._cors(scope, receive, send)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    parse_origins(secrets.api().allowed_origins)  # fail closed at startup, not on first request
    try:
        yield
    finally:
        db.close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="BlundrIQ Personal", lifespan=_lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(_ConfiguredCors)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(games_routes.router)
    app.include_router(home_routes.router)
    app.include_router(scout_routes.router)
    app.include_router(practice_routes.router)
    app.include_router(blunders_routes.router)
    app.include_router(deviations_routes.router)
    app.include_router(repertoire_routes.router)
    app.include_router(puzzles_routes.router)
    app.include_router(review_routes.router)
    app.include_router(explore_routes.router)
    return app


app = create_app()
