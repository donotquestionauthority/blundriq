"""GET /blunders, GET|POST /blunders/seen, POST /blunders/dismiss|restore|explain, GET /blunders/prompts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field, field_validator

from api import auth
from core import ai, blunders, db, settings
from core.constants import BLUNDER_CLASSES
from core.puzzles import custom

router = APIRouter(prefix="/blunders", tags=["blunders"], dependencies=[auth.Authed])

TimeClass = Literal["focus", "all", "bullet", "blitz", "rapid", "classical"]


def _filters(
    classifications: list[str] = Query(default=[]),
    min_occurrences: int = Query(2, ge=1, le=50),
    since_days: int | None = Query(None, ge=1, le=3650),
    last_n_games: int = Query(0, ge=0, le=20000),
    time_class: TimeClass = Query("focus"),
    show_dismissed: bool = Query(False),
    new_since: AwareDatetime | None = Query(None, description="Mark boards that crossed the threshold after this"),
) -> blunders.BlunderFilters:
    unknown = [c for c in classifications if c not in BLUNDER_CLASSES]
    if unknown:
        raise HTTPException(422, f"unknown classification: {unknown[0][:20]}")
    return blunders.BlunderFilters(
        classifications=tuple(classifications),
        min_occurrences=min_occurrences,
        since_days=since_days,
        last_n_games=last_n_games,
        time_class=time_class,
        show_dismissed=show_dismissed,
        new_since=new_since,
    )


@router.get("")
def list_positions(
    page: int = Query(0, ge=0, le=10000), f: blunders.BlunderFilters = Depends(_filters)
) -> dict[str, Any]:
    with db.transaction() as conn:
        config = settings.load(conn)
        if not f.classifications:
            f = replace(f, classifications=tuple(config.blunders_default_classifications))
        return blunders.positions(conn, f, config.time_class_focus, page)


class FenBody(BaseModel):
    fen: str = Field(max_length=100)

    @field_validator("fen")
    @classmethod
    def _is_a_position(cls, value: str) -> str:
        try:
            return custom.full_fen(value)  # a short FEN has no board key and would dismiss nothing
        except custom.InvalidPuzzle as exc:
            raise ValueError(str(exc)) from exc


@router.get("/seen")
def seen() -> dict[str, str | None]:
    """The boundary the page opens with; Home's count uses the same value."""
    with db.transaction() as conn:
        at = blunders.seen_at(conn)
    return {"seen_at": at.isoformat() if at is not None else None}


@router.post("/seen")
def mark_seen() -> dict[str, str]:
    """Called by the page once the list is on screen."""
    with db.transaction() as conn:
        return {"seen_at": blunders.mark_seen(conn).isoformat()}


@router.post("/dismiss")
def dismiss(body: FenBody) -> dict[str, str]:
    with db.transaction() as conn:
        blunders.dismiss(conn, body.fen)
    return {"detail": "dismissed"}


@router.post("/restore")
def restore(body: FenBody) -> dict[str, str]:
    with db.transaction() as conn:
        blunders.restore(conn, body.fen)
    return {"detail": "restored"}


@router.get("/prompts")
def prompts() -> dict[str, Any]:
    with db.transaction() as conn:
        return {"prompts": ai.prompt_labels(settings.load(conn))}


class ExplainBody(BaseModel):
    chess_game_id: int
    ply: int = Field(ge=0, le=2000)
    prompt_key: str = Field(min_length=1, max_length=20)
    dry_run: bool = False


@router.post("/explain")
def explain(body: ExplainBody) -> dict[str, Any]:
    try:
        return ai.explain(db.transaction, body.chess_game_id, body.ply, body.prompt_key, dry_run=body.dry_run)
    except ai.ExplainError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
