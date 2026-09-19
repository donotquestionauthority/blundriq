"""GET /settings, PUT /settings, GET /settings/schema — the Preferences page's API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from api import auth
from core import db, settings

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[auth.Authed])


@router.get("")
def get_settings() -> dict[str, Any]:
    with db.transaction() as conn:
        return settings.load(conn).model_dump(mode="json")


@router.put("")
def put_settings(body: dict[str, Any]) -> dict[str, Any]:
    try:
        values = settings.Settings.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc
    with db.transaction() as conn:
        settings.save(conn, values)
    return values.model_dump(mode="json")


@router.get("/schema")
def get_schema() -> dict[str, Any]:
    return settings.schema()
