"""GET /ai/history?fen= — every cached AI answer at a board (core/ai.py `history`): what was
asked, by which prompt, when, and the answer, newest first. A read of what is already paid
for: no cap, no call, no `ai_calls` row. The FEN is checked as a position before anything
reads it, as the explore route does; a short one is a 422.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api import auth
from core import ai, db
from core.puzzles.custom import InvalidPuzzle, full_fen

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[auth.Authed])


@router.get("/history")
def ai_history(fen: str = Query(max_length=100)) -> dict[str, Any]:
    try:
        board = full_fen(fen)
    except InvalidPuzzle:
        raise HTTPException(422, "invalid_fen") from None
    with db.transaction() as conn:
        return {"items": ai.history(conn, board)}
