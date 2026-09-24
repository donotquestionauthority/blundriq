"""FastAPI application. Thin routes over core/.

Startup reads its secrets once (core/secrets.py) and fails closed if any is
missing. Routes are grouped in api/routes/*; each is a small module that calls
into core/ and never contains SQL of its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import auth as auth_routes
from api.routes import blunders as blunders_routes
from api.routes import deviations as deviations_routes
from api.routes import games as games_routes
from api.routes import home as home_routes
from api.routes import practice as practice_routes
from api.routes import puzzles as puzzles_routes
from api.routes import repertoire as repertoire_routes
from api.routes import settings as settings_routes
from core import secrets

# The UI origin the cookie is allowed to come from. The UI and API share a
# parent domain (blundriq.com) so a SameSite=Lax cookie works; during the
# build the staging origins are also allowed.
ALLOWED_ORIGINS = [
    "https://blundriq.com",
    "https://personal.blundriq.com",
    "http://localhost:5173",
]


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    secrets.api()  # fail closed at startup, not on first request
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="BlundrIQ Personal", lifespan=_lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(games_routes.router)
    app.include_router(home_routes.router)
    app.include_router(practice_routes.router)
    app.include_router(blunders_routes.router)
    app.include_router(deviations_routes.router)
    app.include_router(repertoire_routes.router)
    app.include_router(puzzles_routes.router)
    return app


app = create_app()
