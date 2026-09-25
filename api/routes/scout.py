"""/scout/* — opponent profiles, the scouting report, the shared positions and decision nodes,
and dismissal. A profile id that is not the player's is a 404 on every route."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from api import auth
from core import blunders, db, settings
from core.ingest.records import FetchError
from core.puzzles import custom
from core.scout import nodes, positions, profiles, report

router = APIRouter(prefix="/scout", tags=["scout"], dependencies=[auth.Authed])

Color = Literal["both", "white", "black"]


def _require_profile(conn: Any, profile_id: int) -> None:
    if not profiles.exists(conn, profile_id):
        raise HTTPException(404, "profile not found")


@router.get("/profiles")
def list_profiles() -> dict[str, Any]:
    with db.transaction() as conn:
        return {"profiles": profiles.listing(conn)}


class ProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    chesscom_username: str | None = Field(default=None, max_length=50)
    lichess_username: str | None = Field(default=None, max_length=50)


@router.post("/profiles")
def create_profile(body: ProfileBody) -> dict[str, Any]:
    try:
        with db.transaction() as conn:
            profile_id = profiles.create(
                conn, body.name, chesscom_username=body.chesscom_username, lichess_username=body.lichess_username
            )
    except profiles.BadProfile as exc:
        raise HTTPException(400, str(exc)) from exc
    except profiles.NameTaken as exc:
        raise HTTPException(409, "name_taken") from exc
    except profiles.HandleNotFound as exc:
        raise HTTPException(422, f"{exc.platform}_username_not_found") from exc
    except FetchError as exc:
        raise HTTPException(502, "platform_unreachable") from exc
    return {"profile_id": profile_id}


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int) -> dict[str, str]:
    with db.transaction() as conn:
        if not profiles.delete(conn, profile_id):
            raise HTTPException(404, "profile not found")
    return {"detail": "removed"}


def _opp_last_n(conn: Any, given: int | None) -> int:
    return given if given is not None else settings.load(conn).scout_default_last_n_games


@router.get("/report/{profile_id}")
def scouting_report(profile_id: int, opp_last_n: int | None = Query(None, ge=0, le=20000)) -> dict[str, Any]:
    with db.transaction() as conn:
        _require_profile(conn, profile_id)
        config = settings.load(conn)
        since = positions.opp_since(conn, profile_id, _opp_last_n(conn, opp_last_n))
        return report.report(conn, profile_id, prior_strength=config.scout_bayesian_prior_strength, opp_since=since)


@router.get("/line-games/{profile_id}")
def line_games(
    profile_id: int,
    family: str = Query("", max_length=200),
    variation: str | None = Query(None, max_length=200),
    opp_last_n: int | None = Query(None, ge=0, le=20000),
) -> dict[str, Any]:
    if not family.strip():
        raise HTTPException(400, "family_required")
    with db.transaction() as conn:
        _require_profile(conn, profile_id)
        since = positions.opp_since(conn, profile_id, _opp_last_n(conn, opp_last_n))
        return {"games": report.line_games(conn, profile_id, family=family, variation=variation, opp_since=since)}


def _filters(
    conn: Any, profile_id: int, my_last_n: int | None, opp_last_n: int | None, color: str, min_freq: int | None
) -> positions.ScoutFilters:
    config = settings.load(conn)
    return positions.ScoutFilters(
        profile_id=profile_id,
        my_last_n=my_last_n if my_last_n is not None else config.scout_default_last_n_games,
        opp_last_n=opp_last_n if opp_last_n is not None else config.scout_default_last_n_games,
        color=color,
        min_freq=min_freq if min_freq is not None else config.scout_default_min_occurrences,
    )


@router.get("/positions/{profile_id}")
def scout_positions(
    profile_id: int,
    my_last_n: int | None = Query(None, ge=0, le=20000),
    opp_last_n: int | None = Query(None, ge=0, le=20000),
    color: Color = Query("both"),
    min_freq: int | None = Query(None, ge=1, le=50),
    page: int = Query(0, ge=0, le=10000),
) -> dict[str, Any]:
    with db.transaction() as conn:
        _require_profile(conn, profile_id)
        return positions.page(conn, _filters(conn, profile_id, my_last_n, opp_last_n, color, min_freq), page)


@router.get("/decision-nodes/{profile_id}")
def decision_nodes(
    profile_id: int,
    my_last_n: int | None = Query(None, ge=0, le=20000),
    opp_last_n: int | None = Query(None, ge=0, le=20000),
    min_freq: int | None = Query(None, ge=1, le=50),
) -> dict[str, Any]:
    with db.transaction() as conn:
        _require_profile(conn, profile_id)
        found = nodes.decision_nodes(
            conn, _filters(conn, profile_id, my_last_n, opp_last_n, "both", min_freq), settings.load(conn)
        )
        return {"nodes": found, "total": len(found)}


class FenBody(BaseModel):
    fen: str = Field(max_length=100)

    @field_validator("fen")
    @classmethod
    def _is_a_position(cls, value: str) -> str:
        try:
            return custom.full_fen(value)  # a short FEN has no board key and would dismiss nothing
        except custom.InvalidPuzzle as exc:
            raise ValueError(str(exc)) from exc


@router.post("/dismiss")
def dismiss(body: FenBody) -> dict[str, str]:
    """The same table Blunders dismisses into; `GET /scout/dismissed` is the way back."""
    with db.transaction() as conn:
        blunders.dismiss(conn, body.fen)
    return {"detail": "dismissed"}


@router.delete("/dismiss")
def restore(body: FenBody) -> dict[str, str]:
    with db.transaction() as conn:
        blunders.restore(conn, body.fen)
    return {"detail": "restored"}


@router.get("/dismissed")
def dismissed() -> dict[str, Any]:
    """Every dismissed board, whether or not it still qualifies anywhere: the one list a
    repertoire or shared position, or a decision node, can be restored from."""
    with db.transaction() as conn:
        return {"boards": blunders.dismissed(conn)}
