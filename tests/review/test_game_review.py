"""The per-game review: the repertoire projection over a spine, the Learn-commit adjudication,
and the three /games/{id} routes — their gate, the status table row by row, the null-move
spellings, and `reviewed` idempotency."""

from __future__ import annotations

import json
import uuid
from typing import Any

import chess
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core import games
from core.constants import PLAYER_ID
from core.repertoire import read
from core.review import learn
from tests import repertoire_helpers as h
from tests.review.helpers import analysed_game, make_ctx

NULL_MOVES = ["--", "Z0", "0000", "@@@@"]
GAME = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]


@pytest.fixture()
def corpus(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """Game 1: a White game with the Ruy Lopez, analysed, a blunder row at ply 4 (the Bb5
    decision). Game 2: the same moves as Chess960 (a planted derived row too). Game 3: an
    unanalysed White game whose bulk columns housekeeping has dropped. A White book whose one
    line plays 3.Bb5 and, from the same board, a second line playing 3.Bc4 in another
    chapter (so ply 4 is a conflict)."""
    conn = clean
    ctx = make_ctx(
        GAME, [20, 20, 30, 30, 40, -100, -100], player_color="white", best_lines={2: "Nf3 Nc6", 4: "Bc4 Bc5"}
    )
    analysed_game(conn, 1, ctx)
    conn.execute("UPDATE chess_games SET analysis_status = 'completed' WHERE id IN (1, 2)")
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, best_line,"
        " centipawn_loss, classification, phase) VALUES (%s, 1, 4, %s, 'Bb5', 'Bc4', 'Bc4 Bc5', 140, 'mistake', 'opening')",
        (PLAYER_ID, ctx["fen_sequence"][4]),
    )
    c960 = make_ctx(GAME, [20, 20, 30, 30, 40, -100, -100], player_color="white")
    analysed_game(conn, 2, c960, variant="chess960")
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, classification)"
        " VALUES (%s, 2, 4, %s, 'Bb5', 'Bc4', 'mistake')",
        (PLAYER_ID, c960["fen_sequence"][4]),
    )
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, variant, time_class)"
        " VALUES (3, 'lichess', 'g3', 'https://example.test/3', now(), 'standard', 'rapid')"
    )
    conn.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, result) VALUES (%s, 3, 'white', 'lichess', 'loss')",
        (PLAYER_ID,),
    )
    h.book(conn, 1, "White", "white")
    h.chapter(conn, 1, 1, "Spanish")
    h.line(conn, 1, 1, "Main", ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    h.chapter(conn, 2, 1, "Italian")
    h.line(conn, 2, 2, "Giuoco", ["e4", "e5", "Nf3", "Nc6", "Bc4"])
    return conn


def one(conn: psycopg.Connection[DictRow], sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return dict(row)


def count(conn: psycopg.Connection[DictRow]) -> int:
    return int(one(conn, "SELECT count(*) AS n FROM learn_commits")["n"])


def fens_of(conn: psycopg.Connection[DictRow], gid: int) -> list[str]:
    return [str(f) for f in one(conn, "SELECT fen_sequence FROM chess_games WHERE id = %s", (gid,))["fen_sequence"]]


# --- the projection ---------------------------------------------------------------------------


def test_project_game_none_cases(corpus: psycopg.Connection[DictRow]) -> None:
    fens = fens_of(corpus, 1)
    assert read.project_game(corpus, "red", fens) is None
    assert read.project_game(corpus, "white", []) is None
    assert read.project_game(corpus, "white", [*fens, ""]) is None
    assert read.project_game(corpus, "white", "not a list") is None
    # No Black book exists: not computed, whatever the spine holds.
    assert read.project_game(corpus, "black", fens) is None


def test_project_game_every_ply_keyed_and_opponent_plies_not_looked_up(corpus: psycopg.Connection[DictRow]) -> None:
    fens = fens_of(corpus, 1)
    calls: list[list[str]] = []
    real = read.rep_lines

    def spy(conn: Any, q: list[str], **kw: Any) -> Any:
        calls.append(list(q))
        return real(conn, q, **kw)

    import core.repertoire.read as mod

    mod.rep_lines = spy  # type: ignore[assignment]
    try:
        proj = read.project_game(corpus, "white", fens)
    finally:
        mod.rep_lines = real  # type: ignore[assignment]
    assert proj is not None
    by = proj["by_ply"]
    assert sorted(by) == list(range(len(fens)))
    # Odd plies are Black to move: never looked up.
    assert all(by[p]["status"] == "not_your_turn" for p in (1, 3, 5))
    assert len(calls) == 1 and calls[0] == [fens[0], fens[2], fens[4], fens[6]]
    assert by[0]["status"] == "match" and by[0]["book_move"] == "e4"
    assert by[2]["status"] == "match" and by[2]["book_move"] == "Nf3"
    assert by[4]["status"] == "conflict" and [g["move"] for g in by[4]["conflict"]] == ["Bc4", "Bb5"]
    assert by[6]["status"] == "none"


def test_project_game_one_lookup_per_distinct_fen(corpus: psycopg.Connection[DictRow]) -> None:
    fens = fens_of(corpus, 1)
    spine = [fens[0], fens[1], fens[0], fens[1]]  # a repetition
    calls: list[list[str]] = []
    real = read.rep_lines
    import core.repertoire.read as mod

    def spy(conn: Any, q: list[str], **kw: Any) -> Any:
        calls.append(list(q))
        return real(conn, q, **kw)

    mod.rep_lines = spy  # type: ignore[assignment]
    try:
        proj = read.project_game(corpus, "white", spine)
    finally:
        mod.rep_lines = real  # type: ignore[assignment]
    assert proj is not None and calls == [[fens[0]]]
    assert proj["by_ply"][0]["book_move"] == "e4" and proj["by_ply"][2]["book_move"] == "e4"


# --- the pure adjudication ---------------------------------------------------------------------


START = chess.Board().fen()


def test_canonical_san_corrects_suffix_and_refuses_null_moves() -> None:
    assert learn.canonical_san(START, "e4") == "e4"
    assert learn.canonical_san(START, "e2e4") == "e4"
    assert learn.canonical_san(START, "e5") is None
    assert learn.canonical_san("7k/8/6K1/8/8/8/8/R7 w - - 0 1", "Ra8") == "Ra8#"
    assert learn.canonical_san("7k/8/6K1/8/8/8/8/R7 w - - 0 1", "Ra8+") == "Ra8#"
    for spelling in NULL_MOVES:
        assert learn.canonical_san(START, spelling) is None, spelling
    assert learn.canonical_san("not a fen", "e4") is None


def test_adjudicate_rules_in_order() -> None:
    row = {"chess_game_id": 1, "ply": 4, "committed_move": "Bb5+", "submitted_move": "Bb5"}
    assert learn.adjudicate(row, 2, 4, "Bb5", "Bb5+", board=True) == 409  # another game
    assert learn.adjudicate(row, 1, 5, "Bb5", "Bb5+", board=True) == 409  # another ply
    assert learn.adjudicate(row, 1, 4, "Bb5", None, board=False) == 200  # the bytes, no board needed
    assert learn.adjudicate(row, 1, 4, "Bb5+", None, board=False) == 200
    assert learn.adjudicate(row, 1, 4, "Bf1b5", "Bb5+", board=True) == 200  # a new spelling, same move
    assert learn.adjudicate(row, 1, 4, "Bc4", "Bc4", board=True) == 409  # a different move
    assert learn.adjudicate(row, 1, 4, "--", None, board=True) == 409  # not a move on a board that exists
    assert learn.adjudicate(row, 1, 4, "Bf1b5", None, board=False) == 410  # board gone, nothing to compare


# --- the routes --------------------------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, corpus: psycopg.Connection[DictRow]) -> TestClient:
    corpus.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def commit_body(ply: int = 4, move: str = "Bb5", **extra: Any) -> dict[str, Any]:
    return {"attempt_id": str(uuid.uuid4()), "ply": ply, "committed_move": move, **extra}


def test_routes_require_login(app_env: None) -> None:
    from api.main import create_app

    c = TestClient(create_app())
    assert c.get("/games/1/review").status_code == 401
    assert c.post("/games/1/reviewed").status_code == 401
    assert c.post("/games/1/learn-commit", json=commit_body()).status_code == 401


def test_review_payload(client: TestClient, corpus: psycopg.Connection[DictRow]) -> None:
    body = client.get("/games/1/review").json()
    assert set(body) == {"game", "blunders", "repertoire"}
    g = body["game"]
    assert g["id"] == 1 and g["player_color"] == "white" and g["out_of_window"] is False and g["analyzed"] is True
    assert g["moves"] == GAME and len(g["fen_sequence"]) == 7 and len(g["ply_analysis"]) == 7
    assert g["reviewed_at"] is None
    assert [b["ply"] for b in body["blunders"]] == [4]
    b = body["blunders"][0]
    assert (b["classification"], b["cp_loss"], b["best_move"], b["best_line"]) == ("mistake", 140, "Bc4", "Bc4 Bc5")
    # The Chess960 game at the same board counts for nothing.
    assert b["fen_occurrence_count"] == 1
    by = body["repertoire"]["by_ply"]
    assert by["4"]["status"] == "conflict" and by["1"]["status"] == "not_your_turn"


def test_review_out_of_window_game(client: TestClient) -> None:
    body = client.get("/games/3/review").json()
    assert body["game"]["out_of_window"] is True and body["game"]["moves"] is None
    assert body["blunders"] == [] and body["repertoire"] is None


def test_gate_404_and_422_on_all_three_routes(client: TestClient, corpus: psycopg.Connection[DictRow]) -> None:
    assert client.get("/games/999/review").status_code == 404
    assert client.post("/games/999/reviewed").status_code == 404
    assert client.post("/games/999/learn-commit", json=commit_body()).status_code == 404
    for r in (
        client.get("/games/2/review"),
        client.post("/games/2/reviewed"),
        client.post("/games/2/learn-commit", json=commit_body()),
    ):
        assert r.status_code == 422 and r.json()["detail"] == "not_analysable"
    assert count(corpus) == 0
    assert one(corpus, "SELECT reviewed_at FROM player_games WHERE chess_game_id = 2")["reviewed_at"] is None


def test_reviewed_is_idempotent_and_keeps_the_first_stamp(
    client: TestClient, corpus: psycopg.Connection[DictRow]
) -> None:
    assert client.post("/games/1/reviewed").status_code == 204
    first = one(corpus, "SELECT reviewed_at FROM player_games WHERE chess_game_id = 1")["reviewed_at"]
    assert first is not None
    corpus.execute("UPDATE player_games SET reviewed_at = now() - interval '1 day' WHERE chess_game_id = 1")
    corpus.commit()
    assert client.post("/games/1/reviewed").status_code == 204
    again = one(corpus, "SELECT reviewed_at FROM player_games WHERE chess_game_id = 1")["reviewed_at"]
    assert again < first
    assert client.get("/games/1/review").json()["game"]["reviewed_at"] is not None


def test_learn_commit_creates_a_row_with_snapshots(client: TestClient, corpus: psycopg.Connection[DictRow]) -> None:
    body = commit_body(4, "Bf1b5", elapsed_ms=4200)
    r = client.post("/games/1/learn-commit", json=body)
    assert r.status_code == 201 and r.json()["created"] is True
    row = one(corpus, "SELECT * FROM learn_commits")
    assert row is not None
    assert (row["committed_move"], row["submitted_move"]) == ("Bb5", "Bf1b5")
    assert (row["game_move"], row["engine_move"], row["book_move"]) == ("Bb5", "Bc4", None)  # ply 4 is a conflict
    assert row["in_check"] is False and row["ply_classified"] is True and row["elapsed_ms"] == 4200
    # An untimed rep stores NULL; a timed one at another ply snapshots that ply's book move.
    r2 = client.post("/games/1/learn-commit", json=commit_body(2, "Nf3"))
    assert r2.status_code == 201
    row2 = one(corpus, "SELECT * FROM learn_commits WHERE ply = 2")
    assert row2 is not None and row2["elapsed_ms"] is None and row2["book_move"] == "Nf3"
    assert row2["ply_classified"] is False and row2["engine_move"] is not None


def test_learn_commit_retry_and_conflicts(client: TestClient, corpus: psycopg.Connection[DictRow]) -> None:
    body = commit_body(4, "Bb5")
    assert client.post("/games/1/learn-commit", json=body).status_code == 201
    row_id = one(corpus, "SELECT id FROM learn_commits")["id"]
    # The same bytes: 200, the original id, no second row.
    r = client.post("/games/1/learn-commit", json=body)
    assert r.status_code == 200 and r.json() == {"id": row_id, "created": False}
    # Another spelling of the same move: the board decides, 200.
    r = client.post("/games/1/learn-commit", json={**body, "committed_move": "Bf1b5"})
    assert r.status_code == 200 and r.json()["id"] == row_id
    # A different move, another ply, another game under the same attempt id: 409, nothing written.
    for bad in ({**body, "committed_move": "Bc4"}, {**body, "ply": 2}):
        r = client.post("/games/1/learn-commit", json=bad)
        assert r.status_code == 409 and r.json()["detail"] == {"error": "attempt_conflict", "id": row_id}
    r = client.post("/games/3/learn-commit", json=body)
    assert r.status_code == 409
    assert count(corpus) == 1
    # A repeated attempt needing the board on a game whose moves are gone: 410, still no row.
    corpus.execute("UPDATE chess_games SET moves = NULL, fen_sequence = NULL, ply_analysis = NULL WHERE id = 1")
    corpus.commit()
    r = client.post("/games/1/learn-commit", json={**body, "committed_move": "Bf1b5"})
    assert r.status_code == 410
    assert client.post("/games/1/learn-commit", json=body).status_code == 200  # the bytes still decide
    assert count(corpus) == 1


def test_learn_commit_422_rows_and_410(client: TestClient, corpus: psycopg.Connection[DictRow]) -> None:
    def detail(payload: dict[str, Any], gid: int = 1) -> tuple[int, Any]:
        r = client.post(f"/games/{gid}/learn-commit", json=payload)
        return r.status_code, r.json()["detail"]

    assert detail(commit_body(), 3) == (410, "position_unavailable")
    assert detail(commit_body(4, "")) == (422, "committed_move_required")
    assert detail(commit_body(4, "B" * 33)) == (422, "committed_move_too_long")
    assert detail(commit_body(4, "Bb\x005")) == (422, "committed_move_not_printable")
    assert detail(commit_body(6, "d4")) == (422, "ply_out_of_range")
    assert detail(commit_body(3, "Nc6")) == (422, "not_your_turn")
    assert detail(commit_body(4, "Qh5")) == (422, "illegal_move")
    assert (
        client.post("/games/1/learn-commit", json={"attempt_id": "nope", "ply": 4, "committed_move": "Bb5"}).status_code
        == 422
    )
    corpus.execute("UPDATE chess_games SET fen_sequence = fen_sequence - 0 WHERE id = 1")  # length drift
    corpus.commit()
    assert detail(commit_body(4, "Bb5")) == (422, "length_drift")
    assert count(corpus) == 0


@pytest.mark.parametrize("spelling", NULL_MOVES)
def test_null_move_spellings_never_reach_a_row(
    client: TestClient, corpus: psycopg.Connection[DictRow], spelling: str
) -> None:
    r = client.post("/games/1/learn-commit", json=commit_body(4, spelling))
    assert r.status_code == 422 and r.json()["detail"] == "illegal_move"
    assert count(corpus) == 0
    # A legal SAN beside it inserts one; the null spelling on a repeated attempt is 409, not 200.
    body = commit_body(4, "Bb5")
    assert client.post("/games/1/learn-commit", json=body).status_code == 201
    r = client.post("/games/1/learn-commit", json={**body, "committed_move": spelling})
    assert r.status_code == 409
    assert count(corpus) == 1


def test_lost_race_is_adjudicated(
    client: TestClient, corpus: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row that appears between the lookup and the insert (another request won) is
    adjudicated like a repeat: the same action is 200 with the winner's id, a different one 409."""
    body = commit_body(4, "Bb5")
    real = learn.insert_commit

    def race(conn: Any, game_id: int, ply: int, attempt_id: str, **kw: Any) -> dict[str, Any]:
        # The winner is another request: its own transaction, committed before this insert.
        real(corpus, game_id, 2, attempt_id, **{**kw, "committed": "Nf3", "submitted": "Nf3", "game_move": "Nf3"})
        corpus.commit()
        return real(conn, game_id, ply, attempt_id, **kw)

    monkeypatch.setattr(learn, "insert_commit", race)
    r = client.post("/games/1/learn-commit", json=body)
    assert r.status_code == 409
    monkeypatch.setattr(learn, "insert_commit", real)
    won = one(corpus, "SELECT id FROM learn_commits")["id"]
    r = client.post("/games/1/learn-commit", json={**body, "ply": 2, "committed_move": "Nf3"})
    assert r.status_code == 200 and r.json() == {"id": won, "created": False}


def test_game_reads_after_the_gate(corpus: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(games.GameNotFound):
        games.game_for_review(corpus, 999)
    with pytest.raises(games.NotAnalysable):
        games.game_for_review(corpus, 2)
    with pytest.raises(games.NotAnalysable):
        games.mark_reviewed(corpus, 2)
    g = games.game_for_review(corpus, 1)
    assert g["out_of_window"] is False and json.loads(json.dumps(g["moves"])) == GAME
