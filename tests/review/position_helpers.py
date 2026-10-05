"""Builders for games that carry an opening prefix, shared by the Review position tests."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import DictRow

from core.chess.board import moves_to_fen_sequence
from core.constants import OPENING_PREFIX_PLIES, PLAYER_ID, REVIEW_RESULTS_MIN_GAMES
from core.review import positions
from core.review.filters import parse_opening
from core.settings import Settings
from tests import repertoire_helpers as h

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
FEN_960 = "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1"


# Lines used across the tests. A transposition: both reach the board after 1.d4 d5 2.c4 e6 3.Nc3.
def line(text: str) -> list[str]:
    return [str(m) for m in text.split()]


QGD = line("d4 d5 c4 e6 Nc3")
QGD_TRANSPOSED = line("d4 e6 c4 d5 Nc3")
SCANDI = line("e4 d5 exd5 Qxd5 Nc3 Qa5")
ITALIAN = line("e4 e5 Nf3 Nc6 Bc4 Bc5 c3")


class Games:
    """Inserts games with explicit ids; `add` returns the id it used."""

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self.conn = conn
        self.next_id = 1
        h.player(conn)

    def add(
        self,
        moves: Sequence[str],
        *,
        colour: str = "black",
        result: str | None = "loss",
        days: float = 1,
        mine: int | None = 1500,
        theirs: int | None = 1500,
        time_class: str = "rapid",
        family: str | None = None,
        variant: str = "standard",
        keep_moves: bool = False,
        ply_analysis: list[Any] | None = None,
        reviewed: bool = False,
        prefix: bool = True,
        at: datetime | None = None,
    ) -> int:
        gid = self.next_id
        self.next_id += 1
        moves = list(moves)
        fens = moves_to_fen_sequence(moves, FEN_960 if variant == "chess960" else None, variant)
        played = at or NOW - timedelta(days=days)
        standard = variant == "standard"
        self.conn.execute(
            "INSERT INTO chess_games (id, platform, platform_game_id, url, played_at, variant, starting_fen,"
            " time_class, canonical_family, canonical_variation, termination, moves, fen_sequence, ply_analysis,"
            " opening_moves, opening_keys)"
            " VALUES (%(id)s, 'lichess', %(pid)s, %(url)s, %(played)s, %(variant)s, %(start)s, %(tc)s, %(family)s,"
            " %(variation)s, 'resignation', %(moves)s::jsonb, %(fens)s::jsonb, %(pa)s::jsonb,"
            " %(omoves)s::jsonb, CASE WHEN %(prefix)s THEN bq_opening_keys(%(ofens)s::jsonb) END)",
            {
                "id": gid,
                "pid": f"g{gid}",
                "url": f"https://example.test/{gid}",
                "played": played,
                "variant": variant,
                "start": FEN_960 if variant == "chess960" else None,
                "tc": time_class,
                "family": family,
                "variation": "Main" if family else None,
                "moves": json.dumps(moves) if keep_moves else None,
                "fens": json.dumps(fens) if keep_moves else None,
                "pa": json.dumps(ply_analysis) if ply_analysis is not None else None,
                "omoves": json.dumps(moves[:OPENING_PREFIX_PLIES]) if (prefix and standard) else None,
                "ofens": json.dumps(fens[: OPENING_PREFIX_PLIES + 1]),
                "prefix": prefix and standard,
            },
        )
        self.conn.execute(
            "INSERT INTO player_games (player_id, chess_game_id, player_color, source, result, player_rating,"
            " opponent_rating, opponent_username, analyzed_at_depth, reviewed_at)"
            " VALUES (%s, %s, %s, 'lichess', %s, %s, %s, %s, %s, %s)",
            (
                PLAYER_ID,
                gid,
                colour,
                result,
                mine,
                theirs,
                f"opp{gid}",
                18 if ply_analysis is not None else None,
                NOW if reviewed else None,
            ),
        )
        return gid

    def anchor(self, days: float = 0) -> int:
        """A newest game in an unrelated opening, so `as_of` is fixed."""
        return self.add("Nf3 Nf6 g3 g6".split(), colour="white", result="draw", days=days)


def key_of(conn: psycopg.Connection[DictRow], moves: Sequence[str]) -> int:
    fen = moves_to_fen_sequence(list(moves))[-1]
    row = conn.execute("SELECT bq_position_key(%s) AS k", (fen,)).fetchone()
    assert row is not None
    return int(row["k"])


def scope(
    config: Settings | None = None,
    time_class: str = "all",
    opening: str = "__all__",
    results_min_games: int = REVIEW_RESULTS_MIN_GAMES,
) -> positions.Scope:
    return positions.Scope(config or Settings(), time_class, parse_opening(opening), results_min_games)


def nodes(conn: psycopg.Connection[DictRow], sc: positions.Scope) -> dict[tuple[str, int], positions.Node]:
    return {(n.colour, n.key): n for n in (positions.build_node(r, sc.config) for r in positions.node_rows(conn, sc))}
