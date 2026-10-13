"""POST /puzzles, DELETE /puzzles/{id} — puzzles the player makes by hand."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api import auth
from core import db, settings
from core.puzzles import custom, serve, visibility

router = APIRouter(prefix="/puzzles", tags=["puzzles"], dependencies=[auth.Authed])


class CreateBody(BaseModel):
    fen: str = Field(max_length=100)
    solution_line: list[str] = Field(min_length=1, max_length=custom.MAX_PLIES)
    color: Literal["w", "b"]
    source_types: list[Literal["blunder", "deviation", "scout"]] = Field(default=[], max_length=3)
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=2000)


@router.post("")
def create(body: CreateBody) -> dict[str, Any]:
    with db.transaction() as conn:
        try:
            puzzle_id = custom.create(
                conn,
                fen=body.fen.strip(),
                solution_line=body.solution_line,
                color=body.color,
                context_tags=list(body.source_types),
                title=(body.title or "").strip() or None,
                description=(body.description or "").strip() or None,
            )
        except custom.InvalidPuzzle as exc:
            raise HTTPException(422, str(exc)) from exc
        except custom.BoardTaken as exc:
            raise HTTPException(409, "You already have a puzzle for this position") from exc
        # A puzzle can exist and still not be served: the repertoire may already teach the
        # position, or prescribe a different move inside the line. Say so now, not never.
        lookahead = serve.lookahead_plies(settings.load(conn))
        visible = visibility.visible_by_id(conn, puzzle_id, lookahead_plies=lookahead) is not None
    return {"id": puzzle_id, "visible": visible}


@router.delete("/{puzzle_id}")
def remove(puzzle_id: int) -> dict[str, str]:
    with db.transaction() as conn:
        if not custom.remove(conn, puzzle_id):
            raise HTTPException(404, "puzzle not found")
    return {"detail": "removed"}
