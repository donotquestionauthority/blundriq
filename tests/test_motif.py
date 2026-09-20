"""Motif tagger: each detector on a hand-built position, then the event grain of
tag_game (one row per opportunity, found by eval tolerance, positional bank, the
material floor from settings)."""

from __future__ import annotations

from typing import Any

import chess

from core.analysis.motifs import detect_motifs, tag_game, won_target_squares


def _themes(fen: str, san: str, min_gain: int = 1) -> set[str]:
    b = chess.Board(fen)
    return detect_motifs(b, b.parse_san(san), min_gain)


def test_detectors_on_textbook_positions() -> None:
    assert _themes("r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1", "Nc7+") == {"fork"}  # king + rook
    assert _themes("4k3/8/8/3q4/8/8/8/3RK3 w - - 0 1", "Rxd5") == {"hangingPiece"}
    assert _themes("4k3/8/8/4n3/3P4/8/8/R4K2 w - - 0 1", "Re1") == {"pin"}  # knight pinned, pawn takes it
    assert _themes("2k4r/8/8/8/8/8/8/R3K3 w - - 0 1", "Ra8+") == {"skewer"}  # king steps off, rook falls
    assert _themes("8/4q1k1/8/8/3N4/8/1B6/K7 w - - 0 1", "Nf5+") >= {"discoveredAttack"}
    assert _themes("4k3/8/8/8/8/8/8/4K2R w - - 0 1", "Rh8+") == set()  # a bare check wins nothing
    assert _themes("4k3/8/8/8/8/8/8/4K3 w - - 0 1", "Kd2") == set()


def test_material_floor_from_settings() -> None:
    fen = "4k3/8/8/3p4/8/8/8/3RK3 w - - 0 1"  # a free pawn
    assert _themes(fen, "Rxd5", 1) == {"hangingPiece"}
    assert _themes(fen, "Rxd5", 2) == set()


def test_won_target_squares() -> None:
    b = chess.Board("r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1")
    assert won_target_squares(b, b.parse_san("Nc7+")) == frozenset({chess.A8})


# tag_game builds its board with core.chess.board.starting_board, which only honours a
# start FEN for Chess960; the tests below use that flag to start from a set position.


def _pa(ply: int, ev: int | None, best: str | None, line: str | None = None, mate: int | None = None) -> dict[str, Any]:
    return {"ply": ply, "eval": ev, "best_move": best, "best_line": line or best, "mate_in_moves": mate}


def test_tag_game_anchors_once_and_judges_by_eval() -> None:
    """White to move at ply 0 in a fork position; a missed fork is one 'motif' row with found=False
    and the real cp loss; the next player ply where the fork is still available emits nothing."""
    start = "r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1"
    b = chess.Board(start)
    # ply 0: white plays Kd2 (missing Nc7+); ply 1: black plays Kd8; ply 2: fork still there, white plays Ke1
    moves = ["Kd2", "Kd8", "Ke1"]
    pa = [_pa(0, 500, "Nc7+"), _pa(1, 100, "Kd8"), _pa(2, 480, "Nc7+"), _pa(3, 100, "Kc8")]
    rows = tag_game(pa, moves, "white", "chess960", b.fen())
    motif_rows = [r for r in rows if r["metric_type"] == "motif"]
    assert len(motif_rows) == 1
    assert motif_rows[0] == {
        "ply": 0,
        "metric_type": "motif",
        "theme": "fork",
        "found": False,
        "mate_in_moves": None,
        "cp_loss": 400,
        "player_color": "white",
    }
    # the same opportunity, taken: found=True and cp_loss 0
    found = tag_game([_pa(0, 500, "Nc7+"), _pa(1, 490, "Kd8")], ["Nc7+"], "white", "chess960", start)
    assert [r["found"] for r in found if r["theme"] == "fork"] == [True]
    assert [r["cp_loss"] for r in found if r["theme"] == "fork"] == [0]


def test_tag_game_positional_and_unknown_states() -> None:
    quiet = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
    # KNOWN, no theme, big loss → positional row; UNKNOWN (no best move) → nothing at all
    rows = tag_game([_pa(0, 0, "Kd2"), _pa(1, -150, "Kd8")], ["Ke2"], "white", "chess960", quiet)
    assert rows == [
        {
            "ply": 0,
            "metric_type": "positional",
            "theme": "untagged",
            "found": None,
            "mate_in_moves": None,
            "cp_loss": 150,
            "player_color": "white",
        }
    ]
    assert tag_game([_pa(0, 0, None), _pa(1, -150, "Kd8")], ["Ke2"], "white", "chess960", quiet) == []


def test_tag_game_mate_row_uses_signed_distance() -> None:
    fen = "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"  # Ra8# is mate in one
    rows = tag_game([_pa(0, 10000, "Ra8#", mate=1), _pa(1, 300, "Kh8")], ["Kf1"], "white", "chess960", fen)
    mates = [r for r in rows if r["metric_type"] == "mate"]
    assert len(mates) == 1 and mates[0]["mate_in_moves"] == 1 and mates[0]["found"] is False
    # the opponent's forced mate (negative distance for white) is not the player's opportunity
    rows = tag_game([_pa(0, -10000, "Kf1", mate=-2), _pa(1, -10000, "Qh1#")], ["Kf1"], "white", "chess960", fen)
    assert not [r for r in rows if r["metric_type"] == "mate"]
