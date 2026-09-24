"""The repertoire read side, and its one write.

GET /repertoire, GET /repertoire/{book_id}/sections, PATCH /repertoire/{books|chapters|lines}/{id} — the
Repertoire page. GET|PUT|DELETE /repertoire/annotation, GET /repertoire/lines/{id}/annotated — the note
editor and the line walk-through, on every card and in Practice.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api import auth
from core import db, settings
from core.repertoire import annotations, books

router = APIRouter(prefix="/repertoire", tags=["repertoire"], dependencies=[auth.Authed])


@router.get("")
def list_books() -> dict[str, Any]:
    with db.transaction() as conn:
        return {"books": books.books(conn)}


class ActiveBody(BaseModel):
    active: bool


@router.patch("/{kind}/{id}")
def set_active(kind: Literal["books", "chapters", "lines"], id: int, body: ActiveBody) -> dict[str, Any]:
    with db.transaction() as conn:
        window = settings.load(conn).analysis_game_limit
        result = books.set_active(conn, kind, id, body.active, window)
    if result is None:
        raise HTTPException(404, "Not found")
    return {"detail": "updated", "rematch": result}


def _fen_norm_or_400(fen: str) -> str:
    try:
        return annotations.normalize_fen(fen)
    except ValueError as exc:
        raise HTTPException(400, "Not a position") from exc


@router.get("/annotation")
def get_annotation(fen: str = Query(max_length=100)) -> dict[str, Any] | None:
    _fen_norm_or_400(fen)
    with db.transaction() as conn:
        note = annotations.get(conn, fen)
    if note is None:
        return None
    return {**note, "updated_at": note["updated_at"].isoformat() if note.get("updated_at") else None}


class NoteBody(BaseModel):
    fen: str = Field(max_length=100)
    text: str = Field(max_length=8000)
    line_id: int | None = None


@router.put("/annotation")
def put_annotation(body: NoteBody) -> dict[str, Any]:
    _fen_norm_or_400(body.fen)
    with db.transaction() as conn:
        result = annotations.upsert(conn, [{"fen": body.fen, "text": body.text}], body.line_id, source="manual")
        if result["skipped_no_line"]:
            raise HTTPException(404, "Line not found for this note")
        if result["skipped_off_spine"]:
            raise HTTPException(400, "This position is not part of the selected line")
        if result["blank"]:
            raise HTTPException(400, "Note is empty after cleanup")
        if body.line_id is not None:
            return {"detail": "Note saved", "line_id": body.line_id}
        note = annotations.get(conn, body.fen)
    assert note is not None
    return {**note, "updated_at": note["updated_at"].isoformat() if note.get("updated_at") else None}


@router.delete("/annotation")
def delete_annotation(fen: str = Query(max_length=100), line_id: int | None = Query(None)) -> dict[str, str]:
    _fen_norm_or_400(fen)
    with db.transaction() as conn:
        removed = annotations.delete(conn, fen, line_id)
    if not removed:
        raise HTTPException(404, "No note for this position")
    return {"detail": "Note deleted"}


@router.get("/lines/{line_id}/annotated")
def line_annotated(line_id: int) -> dict[str, Any]:
    with db.transaction() as conn:
        line = annotations.line_with_notes(conn, line_id)
    if line is None:
        raise HTTPException(404, "Line not found")
    return line


@router.get("/{book_id}/sections")
def book_sections(book_id: int) -> dict[str, Any]:
    with db.transaction() as conn:
        result = books.sections(conn, book_id)
    if result is None:
        raise HTTPException(404, "Book not found")
    return {"sections": result}
