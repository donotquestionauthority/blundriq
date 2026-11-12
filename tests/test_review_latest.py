"""Review latest game: GET /review/latest and Home's `latest_review_sources`.

The latest game is fetched through a mocked transport for each site, parsed by the importer's
own parser, refused in order (no handle, no game, Chess960, a game the parser refuses), and
projected exactly as the per-game review projects the same game once stored. Nothing is
written: every table's content digest is equal before and after, and the digest is shown to
catch the writes a row count would miss.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from core import home, player
from core.constants import PLAYER_ID
from core.ingest import chesscom, latest, lichess
from core.ingest.store import store_game
from core.settings import Settings
from tests import repertoire_helpers as h

ME = "latest_player"
ARCHIVES = [
    "https://api.chess.com/pub/player/latest_player/games/2026/10",
    "https://api.chess.com/pub/player/latest_player/games/2026/11",
]
MOVES = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6"


def _pgn(moves: str = MOVES, tags: str = "") -> str:
    return (
        '[Event "Live Chess"]\n[Site "Chess.com"]\n'
        f'[White "{ME}"]\n[Black "opp"]\n[Result "1-0"]\n[ECO "C60"]\n[TimeControl "600"]\n'
        + tags
        + "\n"
        + moves
        + " 1-0\n"
    )


def cc_game(gid: int, end_time: int, **over: Any) -> dict[str, Any]:
    g: dict[str, Any] = {
        "url": f"https://www.chess.com/game/live/{gid}",
        "pgn": _pgn(),
        "time_control": "600",
        "end_time": end_time,
        "rules": "chess",
        "white": {"rating": 1500, "result": "win", "username": ME},
        "black": {"rating": 1480, "result": "resigned", "username": "opp"},
    }
    g.update(over)
    return g


def li_game(gid: str = "li000001", **over: Any) -> dict[str, Any]:
    g: dict[str, Any] = {
        "id": gid,
        "variant": "standard",
        "speed": "rapid",
        "createdAt": 1762900000000,
        "lastMoveAt": 1762900600000,
        "status": "resign",
        "players": {
            "white": {"user": {"name": "Opp", "id": "opp"}, "rating": 1600},
            "black": {"user": {"name": ME, "id": ME}, "rating": 1550},
        },
        "winner": "black",
        "opening": {"eco": "B01", "name": "Scandinavian Defense"},
        "moves": "e4 d5 exd5 Qxd5",
        "clock": {"initial": 600, "increment": 0},
    }
    g.update(over)
    return g


class Sites:
    """A mocked Chess.com + Lichess. Records every request it was sent."""

    def __init__(self) -> None:
        self.archive: dict[str, list[dict[str, Any]]] = {ARCHIVES[0]: [cc_game(1, 100)], ARCHIVES[1]: []}
        self.archives = list(ARCHIVES)
        self.lichess: list[dict[str, Any]] = [li_game()]
        self.status = 200
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        url = str(request.url).split("?")[0]
        if url.endswith("/games/archives"):
            return httpx.Response(200, json={"archives": self.archives})
        if url in self.archive:
            return httpx.Response(200, json={"games": self.archive[url]})
        if url == f"{lichess.API}/games/user/{ME}":
            games = list(self.lichess)
            if request.url.params.get("ongoing") != "true":
                games = [g for g in games if not lichess.is_ongoing(g)]
            if "max" in request.url.params:
                games = games[: int(request.url.params["max"])]
            return httpx.Response(200, content="\n".join(json.dumps(g) for g in games).encode())
        return httpx.Response(404)


@pytest.fixture()
def sites(monkeypatch: pytest.MonkeyPatch) -> Sites:
    s = Sites()
    monkeypatch.setattr(latest, "client", lambda: httpx.Client(transport=httpx.MockTransport(s)))
    return s


def _player(conn: psycopg.Connection[DictRow], chesscom_name: str | None = ME, lichess_name: str | None = ME) -> None:
    conn.execute(
        "INSERT INTO players (id, chesscom_username, lichess_username) VALUES (%s, %s, %s)",
        (PLAYER_ID, chesscom_name, lichess_name),
    )


def _books(conn: psycopg.Connection[DictRow]) -> None:
    h.book(conn, 1, "White", "white")
    h.chapter(conn, 1, 1, "Spanish")
    h.line(conn, 1, 1, "Main", ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"])
    h.book(conn, 2, "Black", "black")
    h.chapter(conn, 2, 2, "Scandinavian")
    h.line(conn, 2, 2, "Qxd5", ["e4", "d5", "exd5", "Qxd5", "Nc3"])


@pytest.fixture()
def client(app_env: None, clean: psycopg.Connection[DictRow], sites: Sites) -> Iterator[TestClient]:
    _player(clean)
    _books(clean)
    clean.commit()
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    yield c


# --- the pickers -------------------------------------------------------------------------------


def test_chesscom_picks_the_greatest_end_time_of_the_newest_archive(sites: Sites) -> None:
    sites.archive[ARCHIVES[1]] = [cc_game(10, 300), cc_game(11, 500), cc_game(12, 400)]
    with latest.client() as http:
        raw = latest.newest_chesscom(http, ME)
    assert raw is not None and raw["url"].endswith("/11")
    # Only the archives list and the newest archive were fetched.
    assert [str(r.url) for r in sites.requests][1:] == [ARCHIVES[1]]


def test_chesscom_with_no_archives_is_none(sites: Sites) -> None:
    sites.archives = []
    with latest.client() as http:
        assert latest.newest_chesscom(http, ME) is None


def test_lichess_asks_for_one_finished_game_newest_first(sites: Sites) -> None:
    ongoing = li_game("li_ongoing", status="started", winner=None)
    sites.lichess = [ongoing, li_game("li_newest"), li_game("li_older")]
    with latest.client() as http:
        raw = latest.newest_lichess(http, ME)
    assert raw is not None and raw["id"] == "li_newest"
    params = sites.requests[0].url.params
    assert (params["max"], params["ongoing"], params["finished"], params["sort"], params["since"]) == (
        "1",
        "false",
        "true",
        "dateDesc",
        "0",
    )


def test_the_importers_stream_is_unchanged(sites: Sites) -> None:
    """The defaults are the importer's request: ongoing games included, no max."""
    with latest.client() as http:
        list(lichess.stream_games(http, ME, 5))
    params = sites.requests[0].url.params
    assert params["ongoing"] == "true" and "max" not in params


# --- the route ---------------------------------------------------------------------------------


def test_route_requires_login(app_env: None) -> None:
    from api.main import create_app

    assert TestClient(create_app()).get("/review/latest?source=chesscom").status_code == 401


def test_chesscom_latest_game_and_its_projection(client: TestClient, sites: Sites) -> None:
    sites.archive[ARCHIVES[1]] = [cc_game(20, 900)]
    r = client.get("/review/latest?source=chesscom")
    assert r.status_code == 200, r.text
    body = r.json()
    g = body["game"]
    assert (g["source"], g["player_color"], g["result"]) == ("chesscom", "white", "win")
    assert g["moves"] == ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]
    assert len(g["fen_sequence"]) == 7
    assert "id" not in g
    by = body["repertoire"]["by_ply"]
    assert by["0"]["status"] == "match" and by["0"]["book_move"] == "e4" and by["0"]["line_name"] == "Main"
    assert by["1"]["status"] == "not_your_turn"


def test_lichess_latest_game_as_black(client: TestClient, sites: Sites) -> None:
    r = client.get("/review/latest?source=lichess")
    assert r.status_code == 200, r.text
    g = r.json()["game"]
    assert (g["source"], g["player_color"], g["moves"]) == ("lichess", "black", ["e4", "d5", "exd5", "Qxd5"])
    assert r.json()["repertoire"]["by_ply"]["1"]["book_move"] == "d5"


def test_the_projection_equals_the_per_game_reviews_for_the_same_game(
    client: TestClient, sites: Sites, clean: psycopg.Connection[DictRow]
) -> None:
    """The same record stored, then read by GET /games/{id}/review: the same repertoire answer."""
    sites.archive[ARCHIVES[1]] = [cc_game(30, 900)]
    latest_body = client.get("/review/latest?source=chesscom").json()
    rec = chesscom.parse_game(cc_game(30, 900), ME)
    assert rec is not None
    with clean.transaction():
        store_game(clean, rec)
    clean.commit()
    gid = clean.execute("SELECT id FROM chess_games WHERE platform_game_id = '30'").fetchone()
    assert gid is not None
    stored = client.get(f"/games/{gid['id']}/review").json()
    assert stored["game"]["fen_sequence"] == latest_body["game"]["fen_sequence"]
    assert stored["game"]["moves"] == latest_body["game"]["moves"]
    assert stored["repertoire"] == latest_body["repertoire"]


@pytest.mark.parametrize(
    ("setup", "status", "code"),
    [
        ("no_handle", 422, "no_handle"),
        ("no_games", 404, "no_games"),
        ("chess960", 422, "chess960"),
        ("chess960_unparseable", 422, "chess960"),
        ("other_rules", 422, "not_reviewable"),
        ("bad_moves", 422, "not_reviewable"),
        ("site_down", 502, "fetch_failed"),
        ("rate_limited", 502, "fetch_failed"),
    ],
)
def test_refusals(
    client: TestClient, sites: Sites, clean: psycopg.Connection[DictRow], setup: str, status: int, code: str
) -> None:
    fen960 = "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1"
    if setup == "no_handle":
        clean.execute("UPDATE players SET chesscom_username = '  '")
        clean.commit()
    elif setup == "no_games":
        sites.archives = []
    elif setup == "chess960":
        sites.archive[ARCHIVES[1]] = [
            cc_game(
                40,
                900,
                rules="chess960",
                pgn=_pgn("1. e4 e5", f'[Variant "Chess960"]\n[SetUp "1"]\n[FEN "{fen960}"]\n'),
            )
        ]
    elif setup == "chess960_unparseable":  # no FEN header: the parser refuses it, the refusal still says 960
        sites.archive[ARCHIVES[1]] = [cc_game(41, 900, rules="chess960")]
    elif setup == "other_rules":
        sites.archive[ARCHIVES[1]] = [cc_game(42, 900, rules="bughouse")]
    elif setup == "bad_moves":
        sites.archive[ARCHIVES[1]] = [cc_game(43, 900, pgn=_pgn("1. e4 e5 2. Ke3 Ke6 3. Qh8"))]
    elif setup == "site_down":
        sites.status = 503
    elif setup == "rate_limited":
        sites.status = 429
    r = client.get("/review/latest?source=chesscom")
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert detail["code"] == code
    assert ME not in detail["message"] and "chess.com/game" not in detail["message"]
    if setup == "rate_limited":
        assert "rate limiting" in detail["message"]


def test_a_lichess_960_is_refused(client: TestClient, sites: Sites) -> None:
    sites.lichess = [
        li_game(variant={"key": "chess960"}, initialFen="bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1")
    ]
    r = client.get("/review/latest?source=lichess")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "chess960"


def test_an_unknown_source_is_422(client: TestClient) -> None:
    assert client.get("/review/latest?source=fics").status_code == 422
    assert client.get("/review/latest").status_code == 422


def test_logs_name_no_handle_url_or_game(client: TestClient, sites: Sites, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sites.archive[ARCHIVES[1]] = [cc_game(50, 900)]
    assert client.get("/review/latest?source=chesscom").status_code == 200
    sites.archive[ARCHIVES[1]] = [cc_game(51, 900, rules="chess960")]
    assert client.get("/review/latest?source=chesscom").status_code == 422
    sites.status = 503
    assert client.get("/review/latest?source=lichess").status_code == 502
    # The app's own loggers, `api.*` (the one api/main.py gives a handler) and `core.*`. httpx's
    # request lines carry the URL and are not emitted in production: nothing configures them.
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.split(".")[0] in ("api", "core"))
    assert "review latest refused: chess960" in text and "review latest fetch failed: FetchError" in text
    for leak in (ME, "chess.com/game", "lichess.org/", "api.chess.com", "li000001"):
        assert leak not in text, leak


# --- nothing is written ------------------------------------------------------------------------


def _tables(conn: psycopg.Connection[DictRow]) -> list[str]:
    rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename").fetchall()
    return [str(r["tablename"]) for r in rows]


def digests(conn: psycopg.Connection[DictRow]) -> dict[str, str]:
    """One digest of every row's text per table in `public`, the table list read from the
    catalogue so a new table is covered without editing this test."""
    out: dict[str, str] = {}
    for t in _tables(conn):
        row = conn.execute(
            f"SELECT md5(coalesce(string_agg(x::text, E'\\n' ORDER BY x::text), '')) AS d FROM public.{t} x"  # type: ignore[arg-type]
        ).fetchone()
        assert row is not None
        out[t] = str(row["d"])
    conn.commit()
    return out


def counts(conn: psycopg.Connection[DictRow]) -> dict[str, int]:
    out = {t: int(conn.execute(f"SELECT count(*) AS n FROM public.{t}").fetchone()["n"]) for t in _tables(conn)}  # type: ignore[arg-type,index]
    conn.commit()
    return out


def _stored_game(conn: psycopg.Connection[DictRow]) -> None:
    rec = chesscom.parse_game(cc_game(60, 50), ME)
    assert rec is not None
    with conn.transaction():
        store_game(conn, rec)
    conn.execute(
        "UPDATE players SET chesscom_last_checked = '2026-11-01T00:00Z', lichess_last_checked = '2026-11-01T00:00Z'"
    )
    conn.commit()


def test_the_route_writes_nothing(client: TestClient, sites: Sites, clean: psycopg.Connection[DictRow]) -> None:
    _stored_game(clean)
    sites.archive[ARCHIVES[1]] = [cc_game(61, 900)]
    before, before_n = digests(clean), counts(clean)
    calls: list[Callable[[], httpx.Response]] = [
        lambda: client.get("/review/latest?source=chesscom"),
        lambda: client.get("/review/latest?source=lichess"),
    ]
    assert [c().status_code for c in calls] == [200, 200]
    sites.archive[ARCHIVES[1]] = [cc_game(62, 900, rules="chess960")]
    assert client.get("/review/latest?source=chesscom").status_code == 422
    assert digests(clean) == before
    assert counts(clean) == before_n  # supplementary


def test_the_digest_catches_what_a_count_misses(clean: psycopg.Connection[DictRow]) -> None:
    """The proof's own mutation test: an UPDATE and a delete-then-reinsert keep every count."""
    _player(clean)
    _stored_game(clean)
    before, before_n = digests(clean), counts(clean)

    clean.execute("UPDATE players SET chesscom_last_checked = now()")
    clean.commit()
    assert counts(clean) == before_n
    assert digests(clean)["players"] != before["players"]

    clean.execute("UPDATE players SET chesscom_last_checked = '2026-11-01T00:00Z'")
    clean.commit()
    assert digests(clean) == before

    # A delete and a reinsert of a different row: a chess_games row with no player_games row.
    clean.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, url, variant) VALUES (900, 'lichess', 'x1', 'https://example.test/x1', 'standard')"
    )
    clean.commit()
    before, before_n = digests(clean), counts(clean)
    clean.execute("DELETE FROM chess_games WHERE id = 900")
    clean.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, url, variant) VALUES (900, 'lichess', 'x2', 'https://example.test/x1', 'standard')"
    )
    clean.commit()
    assert counts(clean) == before_n
    assert digests(clean)["chess_games"] != before["chess_games"]


# --- Home --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cc", "li", "want"),
    [(ME, ME, ["chesscom", "lichess"]), (None, ME, ["lichess"]), (ME, "", ["chesscom"]), (None, " ", [])],
)
def test_home_lists_the_sites_with_a_handle(
    clean: psycopg.Connection[DictRow], cc: str | None, li: str | None, want: list[str]
) -> None:
    _player(clean, cc, li)
    assert player.sources_with_handle(clean) == want
    assert home.page(clean, Settings())["latest_review_sources"] == want


def test_no_players_row_offers_no_site(clean: psycopg.Connection[DictRow]) -> None:
    assert player.sources_with_handle(clean) == []


def test_home_carries_site_names_only(client: TestClient) -> None:
    r = client.get("/home")
    assert r.status_code == 200
    assert r.json()["latest_review_sources"] == ["chesscom", "lichess"]
    assert ME not in r.text
