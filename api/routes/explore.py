"""POST /explore/ask — a question about a board explored in the browser (core/ai.py
`ask_explore`). The body models here are the bounds on what the browser may send: the chess
validation — is the FEN a position, is every move legal where it is claimed, does the engine
line play — is core's, and a request that fails it is a 400 with a fixed message.

`SnapshotBody` and `AlternativeBody` are shared with POST /games/{id}/ask (api/routes/games.py),
which takes the same alternative.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from api import auth
from core import ai, db

router = APIRouter(prefix="/explore", tags=["explore"], dependencies=[auth.Authed])

Uci = Annotated[str, StringConstraints(max_length=5)]  # e2e4, e7e8q
San = Annotated[str, StringConstraints(max_length=10)]  # exd8=Q+, O-O-O


class SnapshotBody(BaseModel):
    """The in-browser engine's readout for one board, as `engineSnapshot` makes it."""

    depth: int = Field(ge=0, le=64)
    eval_cp: int
    best_move: Uci
    pv: list[Uci] = Field(max_length=ai.ASK_MAX_PV_PLIES)


class AlternativeBody(BaseModel):
    """The move the question names; `engine` is the snapshot after it, absent when the move
    ends the game."""

    move: San
    engine: SnapshotBody | None = None


class GameOrigin(BaseModel):
    id: int = Field(ge=1)
    ply: int = Field(ge=0, le=2000)


class ExploreAskBody(BaseModel):
    seed_fen: str = Field(max_length=100)
    orientation: Literal["white", "black"]
    moves: list[San] = Field(default_factory=list, max_length=ai.ASK_MAX_EXPLORED_MOVES)
    engine: SnapshotBody
    # Trimmed and limited to ai.QUESTION_MAX_CHARS in core; this bound only caps the body.
    question: str = Field(default="", max_length=4 * ai.QUESTION_MAX_CHARS)
    alternative: AlternativeBody | None = None
    game: GameOrigin | None = None
    dry_run: bool = False


@router.post("/ask")
def explore_ask(body: ExploreAskBody) -> dict[str, Any]:
    try:
        return ai.ask_explore(db.transaction, body.model_dump(), dry_run=body.dry_run)
    except ai.ExplainError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
