"""The Deviations and Repertoire routes, and the notes they carry."""

from __future__ import annotations

from datetime import datetime

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
    assert c.get("/repertoire/coverage", params={"fen": h.START}).status_code == 401
    assert c.get("/repertoire/report").status_code == 401


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
    # A later request of the first-look visit does not turn what it acknowledged into news.
    same_visit = client.get("/deviations", params={"time_class": "all", "stay": r["stay"]}).json()
    assert same_visit["new_count"] == 0 and not same_visit["positions"][0]["is_new"]
    assert client.get("/deviations", params={"time_class": "nope"}).status_code == 422
    assert client.get("/deviations", params={"color": "red"}).status_code == 422
    # The visit's order marker round-trips; garbage is refused like any other parameter.
    r = client.get("/deviations", params={"time_class": "all"}).json()
    assert datetime.fromisoformat(r["stay"]).tzinfo is not None
    again = client.get("/deviations", params={"time_class": "all", "stay": r["stay"]})
    assert again.status_code == 200 and again.json()["stay"] == r["stay"]
    assert client.get("/deviations", params={"stay": "yesterday"}).status_code == 422


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


def test_coverage_route(client: TestClient) -> None:
    fens = h.spine(None, MAIN)
    r = client.get("/repertoire/coverage", params={"fen": fens[4], "move": "d4"})
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["book_move"], body["played_is_book"], body["line_id"], body["line_ply"]) == (
        "match",
        "Bc4",
        False,
        1,
        4,
    )
    assert client.get("/repertoire/coverage", params={"fen": fens[4]}).json()["played_is_book"] is None
    assert client.get("/repertoire/coverage", params={"fen": h.spine(None, ["d4"])[1]}).json()["status"] == "none"
    for bad in (
        "rnbqkbnr/pppppppp w",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
        "bqnbrkrn/pppppppp/8/8/8/8/PPPPPPPP/BQNBRKRN w KQkq - 0 1",  # a 960 start: castling the placement cannot have
    ):
        assert client.get("/repertoire/coverage", params={"fen": bad}).status_code == 400
    for bad_move in ("Nf6", "--", "0000"):
        assert client.get("/repertoire/coverage", params={"fen": fens[4], "move": bad_move}).status_code == 400


def test_the_line_report_route(client: TestClient) -> None:
    """The fixture's two games both leave the Italian at Bc4 (the player played d4). One read
    carries the lines table and both colours' gaps."""
    out = client.get("/repertoire/report", params={"time_class": "all"}).json()
    lines = out["lines"]["books"]
    assert lines[0]["title"] == "Italian" and lines[0]["games"] == 2 and lines[0]["left_by_me"] == 2
    line = lines[0]["chapters"][0]["lines"][0]
    assert line["title"] == "Main" and line["slip"] == {"own_move": 3, "expected": "Bc4", "played": "d4", "count": 2}
    assert line["depth_total"] == 3 and line["depth_reached"] == {"2": 2} and "puzzles" not in line
    assert out["lines"]["drill_max_lines"] == 50
    assert client.get("/repertoire/report").json()["lines"]["books"][0]["games"] == 2  # focus: rapid games count
    assert client.get("/repertoire/report", params={"time_class": "nope"}).status_code == 422
    # The scope: absent and 0 are the default, a number above the window is the window, the
    # effective number comes back with the options; the decisions view and its key shape ride along.
    assert out["scope"] == {"games": 500, "limit": 1000, "options": [100, 250, 500, 1000]}
    assert client.get("/repertoire/report", params={"games": 0}).json()["scope"]["games"] == 500
    assert client.get("/repertoire/report", params={"games": 5000}).json()["scope"]["games"] == 1000
    assert client.get("/repertoire/report", params={"games": 250}).json()["scope"]["games"] == 250
    assert client.get("/repertoire/report", params={"games": "abc"}).status_code == 422
    rows = out["decisions"]["rows"]
    assert rows and all(r["lines"] and r["lines"][0][1] for r in rows) and "board" not in rows[0]
    assert line["trouble_spot"]["own_move"] == 3 and line["recent"] == []
    empty = {"no_book": False, "prepared": [], "gaps": [], "prefill_plies": 10, "max_plies": 60}
    assert out["gaps"]["white"] == {
        "colour": "white",
        "destinations": [{"id": 1, "title": "Giuoco", "book_id": 1, "book": "Italian"}],
        **empty,
    }
    assert out["gaps"]["black"] == {"colour": "black", "destinations": [], **empty}


def test_the_manual_line_routes(client: TestClient) -> None:
    """POST /repertoire/lines: 201 into a chapter and into the catch-all, 409 for a duplicate and
    for a conflict, 404 for a chapter that is not a destination, 422 for a line that does not
    play or no destination; DELETE /repertoire/lines/{id}: 200 then 404."""
    scandi = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5", "d4"]
    # The fixture's line has an explicit id; the serial must not collide with it.
    from core import db as _db

    with _db.transaction() as conn:
        for seq in ("repertoire_lines_id_seq", "books_id_seq", "chapters_id_seq"):
            conn.execute(f"SELECT setval('{seq}', 1000)")  # type: ignore[arg-type]  # test literals
    made = client.post("/repertoire/lines", json={"moves": scandi, "line_name": "Scandi", "chapter_id": 1})
    assert made.status_code == 201
    assert made.json()["chapter_id"] == 1 and made.json()["book_id"] == 1 and "rematch" in made.json()
    dup = client.post("/repertoire/lines", json={"moves": scandi, "line_name": "again", "chapter_id": 1})
    assert dup.status_code == 409 and dup.json()["detail"] == "duplicate"
    winawer = ["e4", "e6", "d4", "d5", "Nc3", "Bb4", "e5", "c5", "a3", "Bxc3+", "bxc3"]
    assert (
        client.post("/repertoire/lines", json={"moves": winawer, "line_name": "check", "chapter_id": 1}).status_code
        == 201
    )
    twice = client.post(
        "/repertoire/lines", json={"moves": winawer[:9] + ["Bxc3", "bxc3"], "line_name": "x", "chapter_id": 1}
    )
    assert twice.status_code == 409 and twice.json()["duplicate"]["kind"] == "identical"
    assert dup.json()["duplicate"]["kind"] == "identical" and dup.json()["duplicate"]["line_id"] == made.json()["id"]
    conflict = client.post(
        "/repertoire/lines", json={"moves": ["e4", "e5", "Nf3", "Nc6", "Bb5"], "line_name": "Spanish", "chapter_id": 1}
    )
    assert conflict.status_code == 409 and conflict.json()["detail"] == "activation_conflict"
    assert conflict.json()["refusal"]["reason"] == "anchor" and conflict.json()["refusal"]["rivals"][0]["line_id"] == 1
    catch = client.post(
        "/repertoire/lines",
        json={
            "moves": ["d4", "d5", "c4", "e6"],
            "line_name": "QGD",
            "catch_all": {"colour": "black", "family": "Queen's Gambit"},
        },
    )
    assert catch.status_code == 201 and catch.json()["book_id"] != 1
    books = client.get("/repertoire").json()["books"]
    assert [(b["title"], b["color"]) for b in books if b["title"] == "Catch-all"] == [("Catch-all", "black")]
    sections = client.get(f"/repertoire/{catch.json()['book_id']}/sections").json()["sections"]
    assert sections[0]["title"] == "Queen's Gambit" and sections[0]["lines"][0]["origin"] == "manual"
    assert (
        client.post("/repertoire/lines", json={"moves": scandi, "line_name": "x", "chapter_id": 99}).status_code == 404
    )
    assert (
        client.post(
            "/repertoire/lines",
            json={"moves": ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nxe4"], "line_name": "x", "chapter_id": 1},
        ).status_code
        == 422
    )
    assert client.post("/repertoire/lines", json={"moves": scandi, "line_name": "x"}).status_code == 422
    assert (
        client.post(
            "/repertoire/lines",
            json={"moves": scandi, "line_name": "x", "chapter_id": 1, "catch_all": {"colour": "white", "family": None}},
        ).status_code
        == 422
    )
    gone = client.delete(f"/repertoire/lines/{made.json()['id']}")
    assert gone.status_code == 200 and gone.json()["detail"] == "deleted" and gone.json()["loads"] == 0
    assert client.delete(f"/repertoire/lines/{made.json()['id']}").status_code == 404
    report = client.get("/repertoire/report", params={"time_class": "all"}).json()["lines"]
    assert report["drill_max_lines"] == 50
    titles = [ln["title"] for b in report["books"] for ch in b["chapters"] for ln in ch["lines"]]
    assert "Scandi" not in titles and "QGD" in titles
