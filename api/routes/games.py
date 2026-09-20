"""GET /games, /games/filters, /games/export.csv, /games/opponents — the Games page."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from api import auth
from core import db, games

router = APIRouter(prefix="/games", tags=["games"], dependencies=[auth.Authed])


def _filters(
    since_days: int | None = Query(None, ge=1, le=3650),
    last_n_games: int = Query(0, ge=0, le=20000),
    color: str | None = Query(None, pattern="^(white|black)$"),
    result: str | None = Query(None, pattern="^(win|loss|draw)$"),
    platform: str | None = Query(None, pattern="^(chesscom|lichess)$"),
    variant: str | None = Query(None, pattern="^(standard|chess960)$"),
    book: str | None = Query(None, max_length=200),
    chapter: str | None = Query(None, max_length=200),
    deviation: str | None = Query(None, pattern="^(me|opponent|none|no_match)$"),
    opponent: str | None = Query(None, max_length=50),
) -> games.GameFilters:
    return games.GameFilters(
        since_days=since_days,
        last_n_games=last_n_games,
        color=color,
        result=result,
        platform=platform,
        variant=variant,
        book=book,
        chapter=chapter,
        deviation=deviation,
        opponent=opponent or None,
    )


def _serialise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        g = dict(r)
        if g.get("played_at") is not None:
            g["played_at"] = g["played_at"].isoformat()
        out.append(g)
    return out


@router.get("")
def list_games(page: int = Query(1, ge=1), f: games.GameFilters = Depends(_filters)) -> dict[str, Any]:
    with db.transaction() as conn:
        rows = games.list_games(conn, f, page)
        summary = games.summary(conn, f)
    return {"games": _serialise(rows), "summary": summary, "page": page, "page_size": games.PAGE_SIZE}


@router.get("/filters")
def filters() -> dict[str, Any]:
    with db.transaction() as conn:
        return games.filter_values(conn)


@router.get("/opponents")
def opponents(q: str = Query(..., min_length=1, max_length=50)) -> dict[str, list[str]]:
    with db.transaction() as conn:
        return {"opponents": games.search_opponents(conn, q)}


@router.get("/export.csv")
def export(f: games.GameFilters = Depends(_filters)) -> Response:
    with db.transaction() as conn:
        rows = games.export_games(conn, f)
    return Response(
        games.to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=blundriq_games.csv"},
    )
