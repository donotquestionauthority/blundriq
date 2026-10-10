"""GET /home — the Home page. POST /home/pipeline/run — start the hourly workflow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from api import auth
from core import db, dispatch, home, secrets, settings

router = APIRouter(prefix="/home", tags=["home"], dependencies=[auth.Authed])


@router.get("")
def show() -> dict[str, Any]:
    with db.transaction() as conn:
        return home.page(conn, settings.load(conn))


@router.post("/pipeline/run", status_code=202)
def run_pipeline() -> dict[str, str]:
    """Nothing is recorded here: the run's own rows are its record, and `GET /home` reports
    them. A run already in progress is not refused; the workflow's concurrency group queues
    the request behind it."""
    try:
        creds = secrets.dispatch()
    except secrets.MissingSecret as exc:
        raise HTTPException(503, "dispatch is not configured") from exc
    try:
        with dispatch.client() as http:
            at = dispatch.run_pipeline(creds, http)
    except dispatch.DispatchError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return {"requested_at": at.isoformat()}
