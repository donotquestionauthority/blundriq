"""The Deviations and Repertoire routes, and the notes they carry."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from tests import repertoire_helpers as h

MAIN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]


@pytest.fixture()
def client(app_env: None, clean: psycopg.Connection[DictRow]) -> TestClient:
    h.player(clean)
    h.book(clean, 1, "Italian", "white")
    h.chapter(clean, 1, 1, "Giuoco")
    h.line(clean, 1, 1, "Main", MAIN)
    for gid, days in ((1, 2), (2, 1)):
        fens = h.game(clean, gid, MAIN[:4] + ["d4"], days_ago=days)
        h.result(
            clean, gid, book_id=1, chapter_id=1, ply=4, by="me", expected="Bc4", played="d4", fen=fens[4], line_ids=[1]
        )
    clean.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_everything_needs_login(app_env: None) -> None:
    from api.main import create_app

    c = TestClient(create_app())
    assert c.get("/deviations").status_code == 401
    assert c.get("/repertoire").status_code == 401
    assert c.get("/repertoire/annotation", params={"fen": h.START}).status_code == 401
    assert c.get("/repertoire/conflicts").status_code == 401


def test_deviations_list_and_seen(client: TestClient) -> None:
    assert client.get("/deviations/seen").json() == {"seen_at": None}
    r = client.get("/deviations", params={"time_class": "all"}).json()
    assert r["total"] == 1 and r["positions"][0]["count"] == 2 and not r["positions"][0]["is_new"]
    assert r["to_acknowledge"] == [[1, 1, 4, "Bc4"]]
    assert client.post("/deviations/seen", json={"patterns": [[1, 1, "x", "Bc4"]]}).status_code == 422
    assert client.post("/deviations/seen", json={"patterns": [[1, 1, 4, True]]}).status_code == 422
    assert client.post("/deviations/seen", json={"patterns": r["to_acknowledge"]}).status_code == 200
    assert client.get("/deviations/seen").json()["seen_at"] is not None
    assert client.get("/home").json()["new_deviations"] == 0
    assert client.get("/deviations", params={"time_class": "nope"}).status_code == 422
    assert client.get("/deviations", params={"color": "red"}).status_code == 422


def test_books_sections_and_toggles(client: TestClient) -> None:
    books = client.get("/repertoire").json()["books"]
    assert books[0]["title"] == "Italian" and books[0]["active_lines"] == 1
    sections = client.get("/repertoire/1/sections").json()["sections"]
    assert sections[0]["lines"][0]["name"] == "Main" and sections[0]["lines"][0]["moves"] == MAIN
    assert client.get("/repertoire/9/sections").status_code == 404
    r = client.patch("/repertoire/lines/1", json={"active": False})
    assert r.status_code == 200 and r.json()["rematch"]["candidates"] == 2
    assert client.get("/repertoire").json()["books"][0]["active_lines"] == 0
    assert client.get("/deviations", params={"time_class": "all"}).json()["total"] == 0
    assert client.patch("/repertoire/lines/9", json={"active": True}).status_code == 404
    assert client.patch("/repertoire/things/1", json={"active": True}).status_code == 422


def test_conflicts_listing_refusal_and_held_back(client: TestClient, clean: psycopg.Connection[DictRow]) -> None:
    h.chapter(clean, 2, 1, "Intro")
    h.line(clean, 2, 2, "Spanish", MAIN[:4] + ["Bb5"], active=False)
    h.line(clean, 3, 2, "Same", MAIN)
    clean.commit()
    after_nc6 = h.spine(None, MAIN[:4])[4]
    r = client.get("/repertoire/conflicts").json()
    assert r["contested"] == 0
    assert [p["fen"] for p in r["positions"]] == [after_nc6] and r["positions"][0]["contested"] is False
    assert [g["move"] for g in r["positions"][0]["moves"]] == ["Bc4", "Bb5"]
    assert [[ln["line_id"] for ln in g["lines"]] for g in r["duplicates"]] == [[1, 3]]
    # A line the gate refuses: 409 with the refusal, nothing changed.
    r = client.patch("/repertoire/lines/2", json={"active": True})
    assert r.status_code == 409 and r.json()["detail"] == "activation_conflict"
    refusal = r.json()["refusal"]
    assert (refusal["line_id"], refusal["fen"], refusal["move"], refusal["reason"]) == (2, after_nc6, "Bb5", "anchor")
    assert refusal["rivals"][0]["line_id"] == 1 and refusal["rivals"][0]["move"] == "Bc4"
    # Under a chapter that is off the flip is latent; the chapter then comes on with the
    # refused line held back and the agreeing line accepted.
    assert client.patch("/repertoire/chapters/2", json={"active": False}).status_code == 200
    r = client.patch("/repertoire/lines/2", json={"active": True})
    assert r.status_code == 200 and r.json()["held_back"] == []
    r = client.patch("/repertoire/chapters/2", json={"active": True})
    assert r.status_code == 200 and r.json()["detail"] == "updated"
    assert [x["line_id"] for x in r.json()["held_back"]] == [2] and r.json()["rematch"]["candidates"] == 2
    sections = client.get("/repertoire/1/sections").json()["sections"]
    assert sections[1]["active"] is True and [ln["active"] for ln in sections[1]["lines"]] == [False, True]
    # Already on: a no-op answer.
    r = client.patch("/repertoire/chapters/2", json={"active": True})
    assert r.status_code == 200 and r.json()["held_back"] == [] and r.json()["rematch"]["candidates"] == 0


def test_notes_round_trip(client: TestClient) -> None:
    fen = h.spine(None, MAIN)[4]
    assert client.get("/repertoire/annotation", params={"fen": fen}).json() is None
    assert client.get("/repertoire/annotation", params={"fen": "rnbqkbnr/pppppppp w"}).status_code == 400
    r = client.put("/repertoire/annotation", json={"fen": fen, "text": " Eyes f7 <b>!</b> "})
    assert r.status_code == 200 and r.json()["text"] == "Eyes f7 !" and r.json()["source"] == "manual"
    assert client.put("/repertoire/annotation", json={"fen": fen, "text": "[%cal Gg1f3]"}).status_code == 400
    assert client.put("/repertoire/annotation", json={"fen": fen, "text": "x", "line_id": 9}).status_code == 404
    off = h.spine(None, ["d4"])[1]
    assert client.put("/repertoire/annotation", json={"fen": off, "text": "x", "line_id": 1}).status_code == 400
    r = client.put("/repertoire/annotation", json={"fen": fen, "text": "on the line", "line_id": 1})
    assert r.json() == {"detail": "Note saved", "line_id": 1}
    line = client.get("/repertoire/lines/1/annotated").json()
    assert line["line_name"] == "Main" and line["positions"][4]["annotation"]["text"] == "on the line"
    assert len(line["positions"]) == 7 and line["positions"][6]["move"] is None
    assert client.get("/repertoire/lines/9/annotated").status_code == 404
    assert client.delete("/repertoire/annotation", params={"fen": fen, "line_id": 1}).status_code == 200
    assert client.delete("/repertoire/annotation", params={"fen": fen, "line_id": 1}).status_code == 404
    assert client.get("/repertoire/annotation", params={"fen": fen}).json()["text"] == "Eyes f7 !"
    assert client.delete("/repertoire/annotation", params={"fen": fen}).status_code == 200
    assert client.get("/repertoire/annotation", params={"fen": fen}).json() is None
