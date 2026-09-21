"""What the three generators create, keep, displace and take away.

These run against the scratch database, because the decisions are made by the worklist
queries as much as by the Python around them.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.puzzles.generate import blunder, missed_mate, repertoire
from core.puzzles.generate.blunder import truncate_solution
from core.settings import Settings

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
AFTER_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"

# A position where White wins a knight with a fork: Nxe5 hits the pawn and, after
# ...Nc6, the knight is simply up a pawn.
FORK_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1"

# White to move and mate in one: Ra8#, a back-rank mate.
MATE_FEN = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"

# Two rooks, mate in two: 1.Ra7 Kg8 2.Rb8#.
MATE_IN_TWO_FEN = "7k/8/8/8/8/8/R7/1R5K w - - 0 1"


def _config(**overrides: Any) -> Settings:
    """Settings for a test, with both generator thresholds at two games unless the test is
    about the threshold itself. Two-game fixtures keep the other tests readable; the real
    defaults (three) are exercised by `test_the_occurrence_threshold_*`."""
    return Settings(**{"blunder_puzzle_min_occurrences": 2, "deviation_puzzle_min_occurrences": 2, **overrides})


def _game(
    conn: psycopg.Connection[DictRow],
    game_id: int,
    *,
    time_class: str = "rapid",
    variant: str = "standard",
    fens: list[str] | None = None,
    ply_analysis: list[dict[str, Any]] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, played_at, variant, time_class,"
        " moves, fen_sequence, ply_analysis, starting_fen)"
        " VALUES (%s, 'lichess', %s, now() - (%s || ' minutes')::interval, %s, %s,"
        " %s::jsonb, %s::jsonb, %s::jsonb, %s)",
        (
            game_id,
            f"g{game_id}",
            game_id,
            variant,
            time_class,
            json.dumps(["e4"]),
            json.dumps(fens if fens is not None else [START, AFTER_E4]),
            json.dumps(ply_analysis) if ply_analysis is not None else None,
            fens[0] if variant == "chess960" and fens else None,
        ),
    )
    conn.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, analyzed_at_depth)"
        " VALUES (%s, %s, 'white', 'lichess', 18)",
        (PLAYER_ID, game_id),
    )


def _blunder(
    conn: psycopg.Connection[DictRow],
    game_id: int,
    *,
    ply: int = 0,
    fen: str = FORK_FEN,
    themes: list[str] | None = None,
    best_move: str | None = "Nxe5",
    best_line: str | None = "Nxe5 Nxe5 d4",
    cp_loss: int = 300,
) -> None:
    conn.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, best_move, best_line,"
        " centipawn_loss, classification, themes)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 'blunder', %s::text[])",
        (PLAYER_ID, game_id, ply, fen, best_move, best_line, cp_loss, themes if themes is not None else ["fork"]),
    )


def _player(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me')", (PLAYER_ID,))


def _puzzles(conn: psycopg.Connection[DictRow]) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT id, fen, source_types, themes, solution_line, solution_fen_sequence, color,"
            " active, is_repertoire, repertoire_line_id, acceptance_map, title"
            " FROM puzzles ORDER BY id"
        ).fetchall()
    ]


# --- solution truncation -----------------------------------------------------------


def test_truncation_ends_on_a_player_move_and_respects_the_cap() -> None:
    solution = truncate_solution(FORK_FEN, "Nxe5 Nxe5 d4 Nc6 Bb5", 3)
    assert solution is not None
    assert len(solution) % 2 == 1, "a solution always ends on the player's move"
    assert solution[0] == "Nxe5"


def test_truncation_stops_when_the_material_gain_survives_the_reply() -> None:
    """Nxe5 wins a pawn; after the recapture the player is not up, so the line goes on."""
    short = truncate_solution(FORK_FEN, "Nxe5 Nxe5 d4", 6)
    assert short is not None and len(short) >= 1


# A knight fork of king and rook: Nc7+ wins the rook on a8, and Nxa8 cashes the point.
KNIGHT_FORK_FEN = "r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1"
# A free rook: Rxa1 is up a rook after any reply, so the puzzle is that one move.
FREE_ROOK_FEN = "4k3/8/8/8/8/8/8/rR2K3 w - - 0 1"
# The same capture, but a bishop recaptures: the gain does not survive, so the line goes on.
RECAPTURE_FEN = "4k3/8/8/4b3/8/8/8/rR2K3 w - - 0 1"


def test_truncation_cuts_where_the_point_is_cashed() -> None:
    assert truncate_solution(KNIGHT_FORK_FEN, "Nc7+ Kd8 Nxa8 Kc8 Nb6+ Kb7", 6) == ["Nc7+", "Kd8", "Nxa8"]


def test_truncation_cuts_when_the_gain_survives_the_reply() -> None:
    assert truncate_solution(FREE_ROOK_FEN, "Rxa1 Kd7 Ra7+ Kd6 Rb7", 6) == ["Rxa1"]


def test_truncation_continues_past_a_recapture_up_to_the_cap() -> None:
    line = "Rxa1 Bxa1 Kd2 Kd7 Kc2"
    assert truncate_solution(RECAPTURE_FEN, line, 6) == ["Rxa1", "Bxa1", "Kd2", "Kd7", "Kc2"]
    assert truncate_solution(RECAPTURE_FEN, line, 2) == ["Rxa1", "Bxa1", "Kd2"]
    assert truncate_solution(RECAPTURE_FEN, line, 1) == ["Rxa1"]


def test_truncation_refuses_an_empty_or_unplayable_line() -> None:
    assert truncate_solution(FORK_FEN, "", 3) is None
    assert truncate_solution(FORK_FEN, None, 3) is None
    assert truncate_solution(FORK_FEN, "Qxz9", 3) is None


# --- blunder puzzles ---------------------------------------------------------------


def test_a_recurring_tagged_blunder_becomes_a_puzzle(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1)
    _blunder(clean, 2)
    stats = blunder.generate(clean, _config())
    assert stats["created"] == 1
    rows = _puzzles(clean)
    assert len(rows) == 1
    assert rows[0]["source_types"] == ["blunder"]
    assert rows[0]["themes"] == ["fork"]
    assert rows[0]["color"] == "w"
    # The stored position is playable, and the sequence is one longer than the line.
    assert rows[0]["fen"] == FORK_FEN
    assert len(rows[0]["solution_fen_sequence"]) == len(rows[0]["solution_line"]) + 1
    # Running again changes nothing.
    again = blunder.generate(clean, _config())
    assert again == {**stats, "created": 0, "unchanged": 1}


def test_one_occurrence_is_not_enough(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _blunder(clean, 1)
    assert blunder.generate(clean, _config())["created"] == 0


def test_the_occurrence_threshold_for_blunder_puzzles_defaults_to_three(
    clean: psycopg.Connection[DictRow],
) -> None:
    """The old system built a puzzle only after a position had cost three games. That is a
    different setting from the Blunders page's own minimum, which Rob keeps at two."""
    _player(clean)
    for game_id in (1, 2):
        _game(clean, game_id)
        _blunder(clean, game_id)
    assert blunder.generate(clean, Settings())["created"] == 0
    _game(clean, 3)
    _blunder(clean, 3)
    assert blunder.generate(clean, Settings())["created"] == 1


def test_the_same_position_twice_in_one_game_counts_once(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _blunder(clean, 1, ply=0)
    _blunder(clean, 1, ply=2)
    assert blunder.generate(clean, _config())["created"] == 0


@pytest.mark.parametrize(
    ("themes", "best_move", "best_line"),
    [
        (["mate"], "Nxe5", "Nxe5 Nxe5"),  # forced mates belong to the other generator
        ([], "Nxe5", "Nxe5 Nxe5"),  # untagged: nobody can say what the lesson is
        (["fork"], None, "Nxe5 Nxe5"),  # no engine move
        (["fork"], "Nxe5", None),  # no engine line
    ],
)
def test_the_certainty_gate(
    clean: psycopg.Connection[DictRow], themes: list[str], best_move: str | None, best_line: str | None
) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    for game_id in (1, 2):
        _blunder(clean, game_id, themes=themes, best_move=best_move, best_line=best_line)
    assert blunder.generate(clean, _config())["created"] == 0


def test_a_dismissed_position_never_generates(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1)
    _blunder(clean, 2)
    clean.execute("INSERT INTO dismissed_blunder_fens (player_id, fen) VALUES (%s, %s)", (PLAYER_ID, FORK_FEN))
    assert blunder.generate(clean, _config())["created"] == 0


def test_a_chess960_game_is_not_evidence(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2, variant="chess960", fens=["bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1"])
    _blunder(clean, 1)
    _blunder(clean, 2)
    assert blunder.generate(clean, _config())["created"] == 0


def test_a_blitz_game_is_not_evidence_under_the_default_focus(clean: psycopg.Connection[DictRow]) -> None:
    """Blitz still occupies a slot in the recent-games window; it just is not what Rob
    is studying, so it does not make a puzzle."""
    _player(clean)
    _game(clean, 1)
    _game(clean, 2, time_class="blitz")
    _blunder(clean, 1)
    _blunder(clean, 2)
    assert blunder.generate(clean, _config())["created"] == 0
    assert blunder.generate(clean, _config(time_class_focus="all"))["created"] == 1


def test_a_position_the_repertoire_already_teaches_is_redundant(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1, fen=START, best_move="e4", best_line="e4 e5 Nf3")
    _blunder(clean, 2, fen=START, best_move="e4", best_line="e4 e5 Nf3")
    clean.execute("INSERT INTO books (id, title, color, player_id) VALUES (1, 'Book', 'white', %s)", (PLAYER_ID,))
    clean.execute("INSERT INTO chapters (id, book_id, title) VALUES (1, 1, 'Ch')")
    clean.execute(
        "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves, fen_sequence)"
        " VALUES (1, 1, 'main', %s::jsonb, %s::jsonb)",
        (json.dumps(["e4", "e5"]), json.dumps([START, AFTER_E4, AFTER_E4_E5])),
    )
    assert blunder.generate(clean, _config())["created"] == 0
    # A black book prescribes nothing at a white-to-move position, so it does not suppress.
    clean.execute("UPDATE books SET color = 'black'")
    assert blunder.generate(clean, _config())["created"] == 1


def test_a_puzzle_whose_evidence_ages_out_is_deactivated(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1)
    _blunder(clean, 2)
    assert blunder.generate(clean, _config())["created"] == 1
    clean.execute("DELETE FROM blunders WHERE chess_game_id = 2")
    stats = blunder.generate(clean, _config())
    assert stats["deactivated"] == 1 and stats["created"] == 0
    assert _puzzles(clean)[0]["active"] is False


def test_a_hand_made_puzzle_is_never_displaced(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1)
    _blunder(clean, 2)
    clean.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id)"
        " VALUES (%s, %s::jsonb, ARRAY['custom'], 'w', %s)",
        (FORK_FEN, json.dumps(["Nxe5"]), PLAYER_ID),
    )
    stats = blunder.generate(clean, _config())
    assert stats["created"] == 0 and stats["skipped_stronger"] == 1
    assert [r["source_types"] for r in _puzzles(clean)] == [["custom"]]


def test_a_hand_made_puzzle_keeps_its_origin_tag_and_is_still_protected(clean: psycopg.Connection[DictRow]) -> None:
    """A puzzle the player made from a corpus position carries both tags; the corpus tag
    must not make it displaceable."""
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1)
    _blunder(clean, 2)
    clean.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id)"
        " VALUES (%s, %s::jsonb, ARRAY['lichess_cc0', 'custom'], 'w', %s)",
        (FORK_FEN, json.dumps(["Nxe5"]), PLAYER_ID),
    )
    stats = blunder.generate(clean, _config())
    assert stats["created"] == 0 and stats["deactivated"] == 0 and stats["skipped_stronger"] == 1
    assert [(r["source_types"], r["active"]) for r in _puzzles(clean)] == [(["lichess_cc0", "custom"], True)]


def test_a_corpus_puzzle_gives_way_to_the_real_thing(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    _blunder(clean, 1)
    _blunder(clean, 2)
    clean.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id)"
        " VALUES (%s, %s::jsonb, ARRAY['lichess_cc0'], 'w', %s)",
        (FORK_FEN, json.dumps(["Nxe5"]), PLAYER_ID),
    )
    stats = blunder.generate(clean, _config())
    assert stats["created"] == 1 and stats["deactivated"] == 1
    rows = _puzzles(clean)
    assert [(r["source_types"], r["active"]) for r in rows] == [(["lichess_cc0"], False), (["blunder"], True)]


def test_a_position_with_no_replayable_line_leaves_the_board_alone(clean: psycopg.Connection[DictRow]) -> None:
    """The ordering rule: prove the puzzle can be built before displacing anything."""
    _player(clean)
    _game(clean, 1)
    _game(clean, 2)
    for game_id in (1, 2):
        _blunder(clean, game_id, best_line="Qxz9")
    clean.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id)"
        " VALUES (%s, %s::jsonb, ARRAY['lichess_cc0'], 'w', %s)",
        (FORK_FEN, json.dumps(["Nxe5"]), PLAYER_ID),
    )
    stats = blunder.generate(clean, _config())
    assert stats["created"] == 0 and stats["skipped_no_solution"] == 1 and stats["deactivated"] == 0
    assert _puzzles(clean)[0]["active"] is True


# --- missed-mate puzzles -----------------------------------------------------------


def _mate_event(
    conn: psycopg.Connection[DictRow],
    game_id: int,
    *,
    mate_in: int = 1,
    fen: str = MATE_FEN,
    best_line: str = "Ra8#",
    found: bool = False,
) -> None:
    _game(conn, game_id, fens=[fen, fen], ply_analysis=[{"best_line": best_line}, {}])
    conn.execute(
        "INSERT INTO player_motif_events (player_id, chess_game_id, ply, metric_type, theme, found,"
        " mate_in_moves, player_color) VALUES (%s, %s, 0, 'mate', 'mate', %s, %s, 'white')",
        (PLAYER_ID, game_id, found, mate_in),
    )


def test_one_missed_mate_is_enough(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _mate_event(clean, 1)
    stats = missed_mate.generate(clean, _config())
    assert stats["created"] == 1
    row = _puzzles(clean)[0]
    assert row["source_types"] == ["own_mate"] and row["themes"] == ["mate"] and row["color"] == "w"
    assert row["acceptance_map"]["n"] == 1
    assert missed_mate.generate(clean, _config())["unchanged"] == 1


def test_a_mate_longer_than_the_window_does_not_qualify(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _mate_event(clean, 1, mate_in=2, fen=MATE_IN_TWO_FEN, best_line="Ra7 Kg8 Rb8#")
    assert missed_mate.generate(clean, _config(missed_mate_max_moves=1))["created"] == 0
    assert missed_mate.generate(clean, _config(missed_mate_max_moves=3))["created"] == 1


def test_a_mate_the_player_found_is_not_a_puzzle(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _mate_event(clean, 1, found=True)
    assert missed_mate.generate(clean, _config())["created"] == 0


def test_a_disagreeing_distance_generates_nothing(clean: psycopg.Connection[DictRow]) -> None:
    """The analyser recorded mate in two where the position is mate in one, so there is
    no puzzle rather than one whose map contradicts its own distance."""
    _player(clean)
    _mate_event(clean, 1, mate_in=2)
    stats = missed_mate.generate(clean, _config())
    assert stats["created"] == 0 and stats["skip_reasons"] == {"distance_mismatch": 1}


def test_a_missed_mate_displaces_a_blunder_puzzle(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _mate_event(clean, 1)
    clean.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id)"
        " VALUES (%s, %s::jsonb, ARRAY['blunder'], 'w', %s)",
        (MATE_FEN, json.dumps(["Ra8#"]), PLAYER_ID),
    )
    stats = missed_mate.generate(clean, _config())
    assert stats["created"] == 1 and stats["deactivated"] == 1


# --- repertoire puzzles ------------------------------------------------------------


TABIYA = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"


def _line(
    conn: psycopg.Connection[DictRow],
    *,
    line_id: int = 1,
    moves: list[str] | None = None,
    fens: list[str] | None = None,
    active: bool = True,
) -> None:
    conn.execute(
        "INSERT INTO books (id, title, color, player_id) VALUES (1, 'Book', 'white', %s) ON CONFLICT (id) DO NOTHING",
        (PLAYER_ID,),
    )
    conn.execute("INSERT INTO chapters (id, book_id, title) VALUES (1, 1, 'Ch') ON CONFLICT (id) DO NOTHING")
    conn.execute(
        "INSERT INTO repertoire_lines (id, chapter_id, line_name, moves, fen_sequence, active)"
        " VALUES (%s, 1, 'main', %s::jsonb, %s::jsonb, %s)",
        (
            line_id,
            json.dumps(moves if moves is not None else ["Nf3", "Nc6"]),
            json.dumps(fens) if fens is not None else json.dumps([TABIYA, AFTER_E4, START]),
            active,
        ),
    )


def _deviation(conn: psycopg.Connection[DictRow], game_id: int, line_id: int = 1) -> None:
    _game(conn, game_id)
    result = conn.execute(
        "INSERT INTO game_repertoire_results (player_id, chess_game_id, book_id, chapter_id,"
        " deviated_at_ply, deviation_by) VALUES (%s, %s, 1, 1, 2, 'me') RETURNING id",
        (PLAYER_ID, game_id),
    ).fetchone()
    assert result is not None
    conn.execute(
        "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply) VALUES (%s, %s, 1)",
        (result["id"], line_id),
    )


def test_a_line_puzzle_is_rooted_at_the_line_start(clean: psycopg.Connection[DictRow]) -> None:
    """A chapter that begins at a tabiya makes a puzzle that begins there too, not at
    the standard starting position."""
    _player(clean)
    _line(clean)
    _deviation(clean, 1)
    _deviation(clean, 2)
    stats = repertoire.generate(clean, _config())
    assert stats["created"] == 1
    row = _puzzles(clean)[0]
    assert row["fen"] == TABIYA
    assert row["is_repertoire"] is True and row["repertoire_line_id"] == 1
    assert row["solution_line"] == ["Nf3", "Nc6"]
    assert row["title"] == "Book › Ch › main"
    assert repertoire.generate(clean, _config())["unchanged"] == 1


def test_renaming_a_chapter_relabels_the_puzzle_without_resetting_progress(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _line(clean)
    _deviation(clean, 1)
    _deviation(clean, 2)
    repertoire.generate(clean, _config())
    puzzle_id = _puzzles(clean)[0]["id"]
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level) VALUES (%s, %s, 'rook')", (PLAYER_ID, puzzle_id)
    )
    clean.execute("UPDATE chapters SET title = 'Renamed' WHERE id = 1")
    stats = repertoire.generate(clean, _config())
    assert stats["retitled"] == 1 and stats["rebuilt"] == 0 and stats["srs_reset"] == 0
    assert _puzzles(clean)[0]["title"] == "Book › Renamed › main"
    level = clean.execute("SELECT level FROM player_puzzle_state WHERE puzzle_id = %s", (puzzle_id,)).fetchone()
    assert level and level["level"] == "rook"
    assert repertoire.generate(clean, _config())["unchanged"] == 1


def test_a_malformed_line_is_skipped_without_losing_progress(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _line(clean)
    _deviation(clean, 1)
    _deviation(clean, 2)
    repertoire.generate(clean, _config())
    puzzle_id = _puzzles(clean)[0]["id"]
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level) VALUES (%s, %s, 'rook')",
        (PLAYER_ID, puzzle_id),
    )
    # The sequence no longer matches the moves.
    clean.execute("UPDATE repertoire_lines SET fen_sequence = %s::jsonb WHERE id = 1", (json.dumps([TABIYA]),))
    stats = repertoire.generate(clean, _config())
    assert stats["skipped_bad_line"] == 1 and stats["deactivated"] == 1 and stats["srs_reset"] == 0
    assert _puzzles(clean)[0]["active"] is False
    level = clean.execute("SELECT level FROM player_puzzle_state").fetchone()
    assert level and level["level"] == "rook"


def test_a_changed_line_rebuilds_and_resets_progress(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _line(clean)
    _deviation(clean, 1)
    _deviation(clean, 2)
    repertoire.generate(clean, _config())
    puzzle_id = _puzzles(clean)[0]["id"]
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level) VALUES (%s, %s, 'rook')",
        (PLAYER_ID, puzzle_id),
    )
    clean.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, player_id, solved) VALUES (%s, %s, true)",
        (puzzle_id, PLAYER_ID),
    )
    clean.execute(
        "UPDATE repertoire_lines SET moves = %s::jsonb, fen_sequence = %s::jsonb WHERE id = 1",
        (json.dumps(["d4", "d5"]), json.dumps([TABIYA, START, AFTER_E4])),
    )
    stats = repertoire.generate(clean, _config())
    assert stats["rebuilt"] == 1 and stats["srs_reset"] == 1
    assert _puzzles(clean)[0]["solution_line"] == ["d4", "d5"]
    states = clean.execute("SELECT count(*) AS n FROM player_puzzle_state").fetchone()
    assert states and states["n"] == 0
    # The attempt is a record of what happened and stays.
    attempts = clean.execute("SELECT count(*) AS n FROM puzzle_attempts").fetchone()
    assert attempts and attempts["n"] == 1


def test_an_unchanged_line_comes_back_with_its_progress(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _line(clean)
    _deviation(clean, 1)
    _deviation(clean, 2)
    repertoire.generate(clean, _config())
    puzzle_id = _puzzles(clean)[0]["id"]
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level) VALUES (%s, %s, 'rook')",
        (PLAYER_ID, puzzle_id),
    )
    clean.execute("UPDATE puzzles SET active = FALSE")
    stats = repertoire.generate(clean, _config())
    assert stats["reactivated"] == 1 and stats["srs_reset"] == 0
    assert _puzzles(clean)[0]["active"] is True
    level = clean.execute("SELECT level FROM player_puzzle_state").fetchone()
    assert level and level["level"] == "rook"


def test_a_line_that_goes_quiet_is_deactivated(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _line(clean)
    _deviation(clean, 1)
    _deviation(clean, 2)
    repertoire.generate(clean, _config())
    clean.execute("DELETE FROM game_repertoire_results WHERE chess_game_id = 2")
    stats = repertoire.generate(clean, _config())
    assert stats["deactivated"] == 1
    assert _puzzles(clean)[0]["active"] is False


def test_an_inactive_line_takes_its_puzzle_with_it(clean: psycopg.Connection[DictRow]) -> None:
    _player(clean)
    _line(clean)
    _deviation(clean, 1)
    _deviation(clean, 2)
    repertoire.generate(clean, _config())
    clean.execute("UPDATE repertoire_lines SET active = FALSE WHERE id = 1")
    stats = repertoire.generate(clean, _config())
    assert stats["orphaned"] == 1
    assert _puzzles(clean)[0]["active"] is False


def test_a_hand_made_mate_puzzle_is_not_the_generator_s_to_retire(clean: psycopg.Connection[DictRow]) -> None:
    """Tagged own_mate and custom, with the mate event gone: the missed-mate generator only
    cleans up its own rows, and a hand-made puzzle is not one of them."""
    _player(clean)
    clean.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id, acceptance_map)"
        " VALUES (%s, %s::jsonb, ARRAY['own_mate', 'custom'], 'w', %s, '{}'::jsonb)",
        (MATE_FEN, json.dumps(["Ra8#"]), PLAYER_ID),
    )
    stats = missed_mate.generate(clean, _config())
    assert stats["deactivated"] == 0
    assert [r["active"] for r in _puzzles(clean)] == [True]


def test_a_hand_made_puzzle_survives_a_missed_mate_too(clean: psycopg.Connection[DictRow]) -> None:
    """Both generators respect a puzzle Rob made himself. A missed mate is the stronger
    lesson than a blunder, but not stronger than his own decision to practise a position —
    and displacing it would give the board a new puzzle id and throw the progress away."""
    _player(clean)
    _mate_event(clean, 1)
    row = clean.execute(
        "INSERT INTO puzzles (fen, solution_line, source_types, color, player_id)"
        " VALUES (%s, %s::jsonb, ARRAY['custom'], 'w', %s) RETURNING id",
        (MATE_FEN, json.dumps(["Ra8#"]), PLAYER_ID),
    ).fetchone()
    assert row is not None
    custom_id = int(row["id"])
    clean.execute(
        "INSERT INTO player_puzzle_state (player_id, puzzle_id, level) VALUES (%s, %s, 'rook')",
        (PLAYER_ID, custom_id),
    )

    stats = missed_mate.generate(clean, _config())
    assert stats["created"] == 0 and stats["deactivated"] == 0 and stats["skipped_stronger"] == 1

    puzzles = _puzzles(clean)
    assert [(p["id"], p["source_types"], p["active"]) for p in puzzles] == [(custom_id, ["custom"], True)]
    level = clean.execute("SELECT level FROM player_puzzle_state WHERE puzzle_id = %s", (custom_id,)).fetchone()
    assert level and level["level"] == "rook"
