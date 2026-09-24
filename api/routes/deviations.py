"""GET /deviations, GET|POST /deviations/seen — the Deviations page."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator

from api import auth
from core import db, deviations, settings

router = APIRouter(prefix="/deviations", tags=["deviations"], dependencies=[auth.Authed])

TimeClass = Literal["focus", "all", "bullet", "blitz", "rapid", "classical"]


def _filters(
    min_occurrences: int = Query(2, ge=1, le=50),
    since_days: int | None = Query(None, ge=1, le=3650),
    last_n_games: int = Query(0, ge=0, le=20000),
    time_class: TimeClass = Query("focus"),
    color: str | None = Query(None, pattern="^(white|black)$"),
) -> deviations.DeviationFilters:
    return deviations.DeviationFilters(
        min_occurrences=min_occurrences,
        since_days=since_days,
        last_n_games=last_n_games,
        time_class=time_class,
        color=color,
    )


@router.get("")
def list_patterns(
    page: int = Query(0, ge=0, le=10000), f: deviations.DeviationFilters = Depends(_filters)
) -> dict[str, Any]:
    with db.transaction() as conn:
        config = settings.load(conn)
        # Patterns are marked NEW only once the list has been looked at at least once.
        f = replace(f, min_ply=config.deviations_default_min_ply, mark_new=deviations.seen_at(conn) is not None)
        return deviations.positions(conn, f, config.time_class_focus, page)


@router.get("/seen")
def seen() -> dict[str, str | None]:
    with db.transaction() as conn:
        at = deviations.seen_at(conn)
    return {"seen_at": at.isoformat() if at else None}


class SeenBody(BaseModel):
    patterns: list[list[Any]] = Field(max_length=20000)

    @field_validator("patterns")
    @classmethod
    def _keys(cls, value: list[list[Any]]) -> list[list[Any]]:
        for k in value:
            if (
                len(k) != 4
                or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 2**31 - 1 for x in k[:3])
                or not isinstance(k[3], str)
                or len(k[3]) > 20
            ):
                raise ValueError("not a pattern key")
        return value


@router.post("/seen")
def mark_seen(body: SeenBody) -> dict[str, str]:
    """Called by the page once the list is on screen, with the response's `to_acknowledge`."""
    keys = [(int(k[0]), int(k[1]), int(k[2]), str(k[3])) for k in body.patterns]
    with db.transaction() as conn:
        return {"seen_at": deviations.mark_seen(conn, keys).isoformat()}
