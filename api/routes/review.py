"""GET /review, GET /review/pools/{pool_id}/events, POST /review/pools/{pool_id}/shown — the Review worklist.

A bad parameter (an unknown opening key, group_by or reviewed_scope) is 422 — the client's
stale-key recovery reads `unknown opening key` in the detail; a pool id that does not parse or
names no current node is 404, so nothing is learned from probing ids. The GETs read only; the
stamp commits once through `db.transaction`.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response

from api import auth
from core import db, settings
from core.review import read

router = APIRouter(prefix="/review", tags=["review"], dependencies=[auth.Authed])

PAGE_SIZE = 50

TimeClass = Literal["focus", "all"]
GroupBy = Literal["variation", "repertoire", "position"]
ReviewedScope = Literal["to_review", "all"]


def _pool_id(pool_id: str) -> str:
    if len(pool_id) > 256:
        raise HTTPException(404, "pool not found")
    return pool_id


@router.get("")
def worklist(
    time_class: TimeClass = Query("focus"),
    opening: str = Query(read.OPENING_ALL, max_length=200),
    group_by: GroupBy = Query("variation"),
) -> dict[str, Any]:
    with db.transaction() as conn:
        try:
            return read.page(conn, settings.load(conn), time_class=time_class, opening=opening, group_by=group_by)
        except read.ReviewParamError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.get("/pools/{pool_id}/events")
def pool_events(
    pool_id: str,
    time_class: TimeClass = Query("focus"),
    opening: str = Query(read.OPENING_ALL, max_length=200),
    reviewed_scope: ReviewedScope = Query("to_review"),
    page: int = Query(1, ge=1, le=10000),
) -> dict[str, Any]:
    """The drill-down: one row per game, `total` from the same derivation as the page's counts."""
    with db.transaction() as conn:
        try:
            result = read.pool_events(
                conn,
                settings.load(conn),
                _pool_id(pool_id),
                time_class=time_class,
                opening=opening,
                reviewed_scope=reviewed_scope,
                limit=PAGE_SIZE,
                offset=(page - 1) * PAGE_SIZE,
            )
        except read.ReviewParamError as exc:
            raise HTTPException(422, str(exc)) from exc
        except read.PoolNotFound as exc:
            raise HTTPException(404, "pool not found") from exc
    total = result["total"]
    return {
        "events": result["rows"],
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "total_pages": max(1, -(-total // PAGE_SIZE)),
    }


@router.post("/pools/{pool_id}/shown", status_code=204)
def mark_shown(
    pool_id: str,
    time_class: TimeClass = Query("focus"),
    opening: str = Query(read.OPENING_ALL, max_length=200),
) -> Response:
    """Records that the node was shown, for representative rotation. Validated against the nodes
    the same filter would emit before anything is written."""
    with db.transaction() as conn:
        try:
            read.touch_shown(conn, settings.load(conn), _pool_id(pool_id), time_class=time_class, opening=opening)
        except read.ReviewParamError as exc:
            raise HTTPException(422, str(exc)) from exc
        except read.PoolNotFound as exc:
            raise HTTPException(404, "pool not found") from exc
    return Response(status_code=204)
