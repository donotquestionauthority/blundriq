"""The opponent import step (core/scout/importing.py): onboarding, the two cursors, the
Lichess boundary discovered by its own request, a storage failure as a failed run, the
one-time cursor reset. Both platforms are mocked with a transport that records every request."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import DictRow

from core import notify
from core.constants import PLAYER_ID
from core.ingest.records import FetchError
from core.scout import importing
from pipeline import cli

OPP = "opp_acct"
ME = "rob_test"
T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def ms(d: datetime) -> int:
    return int(d.timestamp() * 1000)


def _pgn(moves: str, tags: str = "") -> str:
    return (
        '[Event "Live Chess"]\n[Site "Chess.com"]\n[Date "2026.09.01"]\n'
        f'[White "{OPP}"]\n[Black "someone"]\n[Result "1-0"]\n[ECO "B01"]\n'
        '[ECOUrl "https://www.chess.com/openings/Scandinavian-Defense"]\n[TimeControl "600"]\n' + tags + "\n" + moves
    )


def chesscom_game(
    gid: int, end: datetime, *, rules: str = "chess", moves: str = "1. e4 d5 2. exd5 Qxd5"
) -> dict[str, Any]:
    tags = ""
    if rules == "chess960":
        tags = '[Variant "Chess960"]\n[SetUp "1"]\n[FEN "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1"]\n'
        moves = "1. e4 e5 2. Nc3 Nc6"
    return {
        "url": f"https://www.chess.com/game/live/{gid}",
        "pgn": _pgn(moves, tags),
        "time_control": "600",
        "end_time": int(end.timestamp()),
        "rated": True,
        "rules": rules,
        "white": {"rating": 1500, "result": "win", "username": OPP},
        "black": {"rating": 1480, "result": "resigned", "username": "someone"},
    }


def lichess_game(
    gid: str, created: datetime, *, last: datetime | None = None, status: str = "resign"
) -> dict[str, Any]:
    return {
        "id": gid,
        "rated": True,
        "variant": "standard",
        "speed": "rapid",
        "createdAt": ms(created),
        "lastMoveAt": ms(last or created + timedelta(minutes=10)),
        "status": status,
        "players": {
            "white": {"user": {"name": OPP, "id": OPP}, "rating": 1600},
            "black": {"user": {"name": "Other", "id": "other"}, "rating": 1550},
        },
        "winner": None if status in ("started", "created") else "white",
        "opening": {"eco": "B01", "name": "Scandinavian Defense", "ply": 4},
        "moves": "e4 d5 exd5 Qxd5",
    }


class Platforms:
    """Both platforms behind one transport. Chess.com archives are keyed by 'YYYY/MM'; the
    Lichess export honours `since` (creation time), `ongoing`, `finished` and `sort`, and
    can break the stream after N lines once."""

    def __init__(self) -> None:
        self.archives: dict[str, list[dict[str, Any]]] = {}
        self.finished: list[dict[str, Any]] = []
        self.ongoing: list[dict[str, Any]] = []
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.break_after: int | None = None
        self.chesscom_status = 200

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        self.requests.append((url, dict(request.url.params)))
        if "api.chess.com" in url:
            if self.chesscom_status != 200:
                return httpx.Response(self.chesscom_status)
            if url.endswith("/games/archives"):
                return httpx.Response(
                    200, json={"archives": [f"https://api.chess.com/pub/player/{OPP}/games/{m}" for m in self.archives]}
                )
            month = "/".join(url.rstrip("/").split("/")[-2:])
            return httpx.Response(200, json={"games": self.archives.get(month, [])})
        p = request.url.params
        games: list[dict[str, Any]] = []
        if p.get("finished") != "false":
            since = int(p.get("since", "0"))
            games += [g for g in self.finished if int(g["createdAt"]) >= since]
        if p.get("ongoing") == "true":
            games += self.ongoing
        games.sort(key=lambda g: int(g["createdAt"]), reverse=p.get("sort", "dateDesc") == "dateDesc")
        lines = [json.dumps(g) for g in games]
        if self.break_after is not None and p.get("finished") != "false":
            cut = ("\n".join(lines[: self.break_after]) + "\n").encode()
            self.break_after = None

            def gen() -> Any:
                yield cut
                raise httpx.ReadError("connection reset", request=request)

            return httpx.Response(200, content=gen())
        return httpx.Response(200, content="\n".join(lines).encode())


def profile(
    conn: psycopg.Connection[DictRow],
    *,
    chesscom: str | None = OPP,
    lichess: str | None = OPP,
    initialised: bool = False,
) -> int:
    conn.execute(
        "INSERT INTO players (id, chesscom_username, lichess_username) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
        (PLAYER_ID, ME, ME),
    )
    row = conn.execute(
        "INSERT INTO opponent_profiles (player_id, name, active, is_initialized) VALUES (%s, 'Opp', TRUE, %s) RETURNING id",
        (PLAYER_ID, initialised),
    ).fetchone()
    assert row is not None
    for platform, handle in (("chesscom", chesscom), ("lichess", lichess)):
        if handle:
            conn.execute(
                "INSERT INTO opponent_sources (opponent_profile_id, source_type, username) VALUES (%s, %s, %s)",
                (row["id"], platform, handle),
            )
    conn.commit()
    return int(row["id"])


def cursor(conn: psycopg.Connection[DictRow], pid: int, platform: str) -> datetime | None:
    row = conn.execute(
        "SELECT last_fetched FROM opponent_sources WHERE opponent_profile_id = %s AND source_type = %s", (pid, platform)
    ).fetchone()
    assert row is not None
    return row["last_fetched"]


def set_cursor(conn: psycopg.Connection[DictRow], pid: int, platform: str, at: datetime | None) -> None:
    conn.execute(
        "UPDATE opponent_sources SET last_fetched = %s WHERE opponent_profile_id = %s AND source_type = %s",
        (at, pid, platform),
    )
    conn.commit()


def views(conn: psycopg.Connection[DictRow], pid: int) -> list[str]:
    rows = conn.execute(
        "SELECT cg.platform_game_id FROM opponent_views ov JOIN chess_games cg ON cg.id = ov.chess_game_id"
        " WHERE ov.opponent_profile_id = %s ORDER BY cg.platform, cg.platform_game_id",
        (pid,),
    ).fetchall()
    return [r["platform_game_id"] for r in rows]


def initialised(conn: psycopg.Connection[DictRow], pid: int) -> bool:
    row = conn.execute("SELECT is_initialized FROM opponent_profiles WHERE id = %s", (pid,)).fetchone()
    assert row is not None
    return bool(row["is_initialized"])


# --- onboarding -----------------------------------------------------------------------------


def test_onboarding_takes_the_newest_n_eligible_games_per_source(clean: psycopg.Connection[DictRow]) -> None:
    """A Chess960 game among the newest N+1 consumes no slot, so the N stored are the
    N standard ones and the oldest of them is the (N+1)th raw game; ongoing Lichess games are
    seen, never stored, never counted."""
    conn = clean
    pid = profile(conn)
    p = Platforms()
    p.archives["2026/08"] = [chesscom_game(1, T0 - timedelta(days=40))]
    p.archives["2026/09"] = [
        chesscom_game(2, T0 - timedelta(days=3)),
        chesscom_game(3, T0 - timedelta(days=2)),
        chesscom_game(4, T0 - timedelta(days=1), rules="chess960"),
        chesscom_game(5, T0),
    ]
    p.finished = [lichess_game(f"l{i}", T0 - timedelta(days=i)) for i in range(1, 5)]
    p.ongoing = [lichess_game("long", T0 - timedelta(days=30), status="started")]
    s = importing.import_profiles(conn, window=2, client=p.client(), now=T0)
    assert (s["profiles"], s["sources"], s["fetched"], s["new"], s["skipped"], s["failed"], s["onboarded"]) == (
        1,
        2,
        4,
        4,
        1,
        0,
        1,
    )
    assert views(conn, pid) == ["3", "5", "l1", "l2"]
    assert initialised(conn, pid)
    assert cursor(conn, pid, "chesscom") == T0  # the newest stored game's end time
    assert cursor(conn, pid, "lichess") == T0 - timedelta(days=30)  # the ongoing game's creation time
    n960 = conn.execute("SELECT count(*) AS n FROM chess_games WHERE variant = 'chess960'").fetchone()
    assert n960 and n960["n"] == 0
    # the next run is incremental from the cursors: the ongoing game's creation time reaches back past
    # the two Lichess games the cap left out, so they come in now; then nothing new
    s = importing.import_profiles(conn, window=2, client=p.client(), now=T0)
    assert (s["new"], s["failed"]) == (2, 0) and views(conn, pid) == ["3", "5", "l1", "l2", "l3", "l4"]
    s = importing.import_profiles(conn, window=2, client=p.client(), now=T0)
    assert (s["new"], s["failed"]) == (0, 0)


def test_a_profile_with_no_source_is_initialised_at_once(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    pid = profile(conn, chesscom=None, lichess=None)
    s = importing.import_profiles(conn, window=5, client=Platforms().client(), now=T0)
    assert (s["sources"], s["onboarded"]) == (0, 1) and initialised(conn, pid)


def test_a_profile_with_one_failing_source_stays_uninitialised(clean: psycopg.Connection[DictRow]) -> None:
    """The Lichess side fetches, the Chess.com side 429s: the run is red, the Lichess
    games are stored and its cursor stamped, and the profile onboards on a later run."""
    conn = clean
    pid = profile(conn)
    p = Platforms()
    p.chesscom_status = 429
    p.finished = [lichess_game("l1", T0 - timedelta(days=1))]
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert (s["failed"], s["new"], s["onboarded"]) == (1, 1, 0)
    assert not initialised(conn, pid)
    assert cursor(conn, pid, "lichess") == T0 and cursor(conn, pid, "chesscom") is None
    p.chesscom_status = 200
    p.archives["2026/09"] = [chesscom_game(1, T0 - timedelta(days=1))]
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0 + timedelta(hours=1))
    assert (s["failed"], s["new"], s["onboarded"]) == (0, 1, 1) and initialised(conn, pid)


# --- Chess.com cursor ---------------------------------------------------------------------------


def test_chesscom_equal_second_games_are_both_stored(clean: psycopg.Connection[DictRow]) -> None:
    """Two games ending in the same second, the second one only visible after the
    first set the cursor. Skipping `<=` would lose it for ever; `<` refetches the first and
    the upsert dedups."""
    conn = clean
    pid = profile(conn, lichess=None, initialised=True)
    p = Platforms()
    p.archives["2026/09"] = [chesscom_game(1, T0)]
    importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert cursor(conn, pid, "chesscom") == T0
    p.archives["2026/09"].append(chesscom_game(2, T0))
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0 + timedelta(hours=1))
    assert (s["fetched"], s["new"]) == (2, 1) and views(conn, pid) == ["1", "2"]
    assert p.requests[-2][0].endswith("/games/archives")  # the walk asked for the archives, then the month


def test_chesscom_incremental_walk_starts_at_the_cursors_month(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    pid = profile(conn, lichess=None, initialised=True)
    set_cursor(conn, pid, "chesscom", datetime(2026, 8, 15, tzinfo=UTC))
    p = Platforms()
    p.archives["2026/07"] = [chesscom_game(1, datetime(2026, 7, 1, tzinfo=UTC))]
    p.archives["2026/08"] = [
        chesscom_game(2, datetime(2026, 8, 10, tzinfo=UTC)),
        chesscom_game(3, datetime(2026, 8, 20, tzinfo=UTC)),
    ]
    p.archives["2026/09"] = [chesscom_game(4, datetime(2026, 9, 2, tzinfo=UTC))]
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert views(conn, pid) == ["3", "4"] and s["fetched"] == 2
    months = [u.split("/games/")[-1] for u, _ in p.requests if "/games/20" in u]
    assert months == ["2026/08", "2026/09"]
    assert cursor(conn, pid, "chesscom") == datetime(2026, 9, 2, tzinfo=UTC)


# --- Lichess boundaries (test 6a) ---------------------------------------------------------------


def test_lichess_boundary_comes_from_the_ongoing_request_not_the_capped_stream(
    clean: psycopg.Connection[DictRow],
) -> None:
    """An ongoing correspondence game created before the newest N finished games
    is invisible to a capped onboarding stream; the boundary must still be its creation time,
    so the run after it finishes stores it."""
    conn = clean
    pid = profile(conn, chesscom=None)
    p = Platforms()
    p.finished = [lichess_game(f"l{i}", T0 - timedelta(hours=i)) for i in range(1, 6)]
    long_created = T0 - timedelta(days=20)
    p.ongoing = [lichess_game("long", long_created, status="started")]  # last in the newest-first stream
    importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert views(conn, pid) == ["l1", "l2", "l3", "l4", "l5"]  # the cap closed the stream before the ongoing game
    assert cursor(conn, pid, "lichess") == long_created
    discovery = [q for _, q in p.requests if q.get("finished") == "false"]
    assert len(discovery) == 1 and discovery[0].get("ongoing") == "true" and discovery[0].get("since") == "0"
    # the game finishes; the next incremental run's `since` is at or before its creation time
    p.ongoing = []
    p.finished.append(lichess_game("long", long_created, last=T0 + timedelta(minutes=30)))
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0 + timedelta(hours=1))
    walks = [q for _, q in p.requests[-2:] if q.get("finished") != "false"]
    assert int(walks[-1]["since"]) <= ms(long_created)
    assert s["new"] == 1 and "long" in views(conn, pid)
    assert cursor(conn, pid, "lichess") == T0 + timedelta(hours=1)  # nothing ongoing: the boundary catches up


def test_lichess_stamp_only_after_a_complete_walk(clean: psycopg.Connection[DictRow]) -> None:
    """A transport failure mid-stream leaves the cursor unchanged and counts as a
    failed source; what was committed before it stays; the rerun completes the walk."""
    conn = clean
    pid = profile(conn, chesscom=None, initialised=True)
    set_cursor(conn, pid, "lichess", T0 - timedelta(days=10))
    p = Platforms()
    p.finished = [lichess_game(f"l{i:03d}", T0 - timedelta(minutes=i)) for i in range(150)]
    p.break_after = 120
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert s["failed"] == 1 and len(views(conn, pid)) == 100  # one batch committed
    assert cursor(conn, pid, "lichess") == T0 - timedelta(days=10)
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert s["failed"] == 0 and len(views(conn, pid)) == 150 and cursor(conn, pid, "lichess") == T0


def test_lichess_null_cursor_on_an_initialised_source_walks_the_whole_history(
    clean: psycopg.Connection[DictRow],
) -> None:
    """Reconciliation. The old cursor was an end time; a game created before it and
    finished after it is only reachable from since=0. Interrupted, NULL stays; the rerun completes."""
    conn = clean
    pid = profile(conn, chesscom=None, initialised=True)
    old_end_time = T0 - timedelta(days=5)
    p = Platforms()
    p.finished = [
        lichess_game("recent", T0 - timedelta(days=1)),
        lichess_game("straddler", old_end_time - timedelta(days=3), last=old_end_time + timedelta(days=1)),
        lichess_game("ancient", T0 - timedelta(days=400)),
    ]
    p.break_after = 2
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert s["failed"] == 1 and cursor(conn, pid, "lichess") is None
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert s["failed"] == 0 and views(conn, pid) == ["ancient", "recent", "straddler"]
    walks = [q for _, q in p.requests if q.get("finished") != "false"]
    assert all(q.get("since") == "0" for q in walks)
    assert cursor(conn, pid, "lichess") == T0


def test_reset_lichess_cursors_and_the_cli_switch(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The reset nulls every active Lichess cursor of an initialised profile, none of
    the Chess.com ones, deletes nothing; `import-opponents --reset-lichess-cursors` then walks
    and stamps, and a second plain run is an ordinary incremental one."""
    conn = clean
    pid = profile(conn, initialised=True)
    set_cursor(conn, pid, "chesscom", T0 - timedelta(days=2))
    set_cursor(conn, pid, "lichess", T0 - timedelta(days=2))
    p = Platforms()
    p.archives["2026/09"] = [chesscom_game(1, T0 - timedelta(days=1))]
    p.finished = [lichess_game("old", T0 - timedelta(days=30)), lichess_game("new", T0 - timedelta(days=1))]
    importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert views(conn, pid) == ["1", "new"]  # the old game is behind the end-time cursor
    assert importing.reset_lichess_cursors(conn) == 1
    assert cursor(conn, pid, "lichess") is None and cursor(conn, pid, "chesscom") == T0 - timedelta(days=1)
    assert views(conn, pid) == ["1", "new"]
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(p.handle)))
    assert cli.main(["import-opponents", "--reset-lichess-cursors"]) == 0
    out = capsys.readouterr().out
    assert '"cursors_reset": 1' in out and OPP not in out
    assert views(conn, pid) == ["1", "new", "old"]
    assert cursor(conn, pid, "lichess") is not None
    stamped = cursor(conn, pid, "lichess")
    assert cli.main(["import-opponents"]) == 0
    walks = [q for u, q in p.requests if "lichess" in u and q.get("finished") != "false"]
    assert stamped is not None and int(walks[-1]["since"]) == ms(stamped)
    assert cli.main(["import-opponents", "--reset-lichess-cursors"]) == 0
    assert '"cursors_reset": 1' in capsys.readouterr().out and views(conn, pid) == ["1", "new", "old"]


# --- a storage failure is a failed run (test 4) ----------------------------------------------------


def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int | None, str]]:
    sent: list[tuple[str, int | None, str]] = []
    monkeypatch.setattr(notify, "send_failure", lambda step, run_id, error: sent.append((step, run_id, error)) or True)
    return sent


def test_a_game_that_fails_to_store_fails_the_run_and_holds_the_cursor(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A real database error on the second of three games in one archive: the savepoint keeps
    the first and the third, the run is red and alerts, the cursor stays; the retry is green."""
    conn = clean
    pid = profile(conn, lichess=None, initialised=True)
    set_cursor(conn, pid, "chesscom", T0 - timedelta(days=3))
    p = Platforms()
    p.archives["2026/09"] = [
        chesscom_game(1, T0 - timedelta(days=2)),
        chesscom_game(2, T0 - timedelta(days=1)),
        chesscom_game(3, T0),
    ]
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(p.handle)))
    real_upsert = importing.upsert_game

    def poisoned(conn: Any, record: Any) -> int:
        if record.platform_game_id == "2":
            conn.execute("INSERT INTO chess_games (platform, platform_game_id) VALUES ('nope', 'x')")  # CHECK fails
        return real_upsert(conn, record)

    monkeypatch.setattr(importing, "upsert_game", poisoned)
    sent = _capture_alerts(monkeypatch)
    args = argparse.Namespace(alert=True, profile=None, reset_lichess_cursors=False)
    assert cli._run_step("import-opponents", cli._step_import_opponents, args) == 1
    err = capsys.readouterr().err
    assert "import-opponents: FAILED (StepFailed)" in err and OPP not in err
    assert views(conn, pid) == ["1", "3"]  # the games before and after it are stored
    assert cursor(conn, pid, "chesscom") == T0 - timedelta(days=3)
    run = conn.execute("SELECT status, error FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run and run["status"] == "failed" and '"failed": 1' in run["error"]
    assert [s[0] for s in sent] == ["import-opponents"]
    # the next run stores it, advances the cursor and is green
    monkeypatch.setattr(importing, "upsert_game", real_upsert)
    assert cli._run_step("import-opponents", cli._step_import_opponents, args) == 0
    assert views(conn, pid) == ["1", "2", "3"] and cursor(conn, pid, "chesscom") == T0
    assert len(sent) == 1


def test_a_source_whose_fetch_raises_fails_the_run_and_the_others_still_run(
    clean: psycopg.Connection[DictRow],
    app_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = clean
    pid = profile(conn, initialised=True)
    p = Platforms()
    p.chesscom_status = 500
    p.finished = [lichess_game("l1", T0 - timedelta(days=1))]
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(p.handle)))
    sent = _capture_alerts(monkeypatch)
    args = argparse.Namespace(alert=True, profile=None, reset_lichess_cursors=False)
    assert cli._run_step("import-opponents", cli._step_import_opponents, args) == 1
    capsys.readouterr()
    assert views(conn, pid) == ["l1"]  # the Lichess source was still processed
    assert cursor(conn, pid, "chesscom") is None and cursor(conn, pid, "lichess") is not None
    assert len(sent) == 1


def test_fetch_error_class_reaches_the_summary_not_the_console(clean: psycopg.Connection[DictRow]) -> None:
    conn = clean
    profile(conn, lichess=None, initialised=True)
    p = Platforms()
    p.chesscom_status = 429
    s = importing.import_profiles(conn, window=5, client=p.client(), now=T0)
    assert s["failed"] == 1
    with pytest.raises(FetchError):
        list(importing.walk.chesscom_games(p.client(), OPP))
