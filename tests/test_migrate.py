"""The old→new copy against a synthetic old-shaped database: only the player's
rows, no Chess960-derived rows, vendor columns renamed, annotation sources
mapped, dropped columns ignored, IDs kept and sequences reset."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import DictRow, dict_row

from core import migrate
from core.constants import PLAYER_ID
from tests.conftest import _admin_url, with_dbname

MAPPING = migrate.Mapping(
    renames={
        "books": {"legacy_bid": "source_book_id"},
        "chapters": {"legacy_lid": "source_chapter_id"},
        "repertoire_lines": {"legacy_line_id": "source_line_id"},
    },
    values={"repertoire_annotations": {"source": {"legacy": "course"}}},
)

VENDOR = "legacy"  # stands in for the old schema's source-specific column prefix and enum value
FEN960 = "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w KQkq - 0 1"
# Puzzles 1 and 2 stand for the old shared tier: owned by nobody, solved by Rob anyway.
# Puzzle 2 sits on the same board as his own puzzle 900, so adopting it needs reconciling.
SHARED_FEN = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1"
THIRD_FEN = "rnbqkbnr/pppppppp/8/8/2P5/8/PP1PPPPP/RNBQKBNR b KQkq - 0 1"
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

OLD_SCHEMA = f"""
CREATE TABLE players (id serial PRIMARY KEY, is_me bool, chesscom_username text, lichess_username text,
    user_id int, onboarding_state text, created_at timestamptz DEFAULT now());
CREATE TABLE chess_games (id bigserial PRIMARY KEY, platform text, platform_game_id text, url text, played_at timestamptz,
    moves jsonb, fen_sequence jsonb, variant text DEFAULT 'standard', starting_fen text, analysis_status text DEFAULT 'unanalyzed',
    lost_wins_flag bool, created_at timestamptz DEFAULT now());
CREATE TABLE player_games (player_id int, chess_game_id bigint, player_color text, source text, result text,
    no_repertoire_match bool DEFAULT false, analyzed_at_depth int, lost_wins_reviewed bool, analysis_claimed_by text);
CREATE TABLE books (id serial PRIMARY KEY, {VENDOR}_bid int, title text, color text, active bool DEFAULT true, player_id int);
CREATE TABLE chapters (id serial PRIMARY KEY, book_id int, {VENDOR}_lid int, title text, active bool DEFAULT true, root_fen text);
CREATE TABLE repertoire_lines (id serial PRIMARY KEY, chapter_id int, {VENDOR}_line_id text, line_name text, moves jsonb,
    fen_sequence jsonb, active bool DEFAULT true);
CREATE TABLE repertoire_annotations (id bigserial PRIMARY KEY, player_id int, fen_norm text, text text, source text,
    line_id int, book_id int, chapter_id int);
CREATE TABLE blunders (id bigserial PRIMARY KEY, player_id int, chess_game_id bigint, ply int, fen text, classification text);
CREATE TABLE player_motif_events (id bigserial PRIMARY KEY, player_id int, chess_game_id bigint, ply int, metric_type text,
    theme text, player_color text, endgame_outcome text);
CREATE TABLE game_repertoire_results (id bigserial PRIMARY KEY, player_id int, chess_game_id bigint, book_id int,
    chapter_id int, deviated_at_ply int, deviation_by text, deviation_fen text);
CREATE TABLE game_result_lines (id bigserial PRIMARY KEY, game_repertoire_result_id bigint, line_id int, matched_ply int);
CREATE TABLE opponent_profiles (id serial PRIMARY KEY, player_id int, name text, onboard_attempts int);
CREATE TABLE opponent_sources (id serial PRIMARY KEY, opponent_profile_id int, source_type text, username text);
CREATE TABLE opponent_views (opponent_profile_id int, chess_game_id bigint, source_type text, played_as text, result text);
CREATE TABLE puzzles (id serial PRIMARY KEY, fen text, solution_line jsonb, solution_fen_sequence jsonb,
    source_types text[], color char(1), title text, description text, themes text[], acceptance_map jsonb,
    is_repertoire bool DEFAULT false, repertoire_line_id int, player_id int, active bool DEFAULT true,
    puzzle_kind text DEFAULT 'line', created_by int, created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now());
CREATE TABLE puzzle_attempts (id serial PRIMARY KEY, puzzle_id int, player_id int, solved bool, moves_played text,
    attempt_at timestamptz DEFAULT now(), attempt_id uuid, session_id uuid);
CREATE TABLE player_puzzle_state (player_id int, puzzle_id int, level text DEFAULT 'pawn', correct_at_level int DEFAULT 0,
    last_3_attempts bool[] DEFAULT '{{}}', last_correct_date date, next_show_at timestamptz, updated_at timestamptz DEFAULT now());
CREATE TABLE player_puzzle_exposure (id bigserial PRIMARY KEY, player_id int, puzzle_id int, bucket text, batch_id bigint,
    scope text DEFAULT 'all', served_at timestamptz DEFAULT now(), item_kind text DEFAULT 'puzzle', repertoire_line_id int);
CREATE TABLE player_puzzle_skip (id bigserial PRIMARY KEY, player_id int, scope text, batch_id bigint, puzzle_id int,
    skipped_at timestamptz DEFAULT now(), item_kind text DEFAULT 'puzzle', repertoire_line_id int);
CREATE TABLE dismissed_blunder_fens (id serial PRIMARY KEY, player_id int, fen text, dismissed_at timestamptz DEFAULT now());
"""

OLD_DATA = f"""
INSERT INTO players (id, chesscom_username, lichess_username) VALUES (1, 'me', 'me'), (1065, 'me', 'me');
INSERT INTO chess_games (id, platform, platform_game_id, played_at, moves, fen_sequence, variant, starting_fen) VALUES
  (10, 'lichess', 'a', now(), '["e4"]', '["{START}"]', 'standard', NULL),
  (11, 'lichess', 'b', now(), '["e4"]', '["{FEN960}"]', 'chess960', '{FEN960}'),
  (12, 'chesscom', 'c', now(), NULL, NULL, 'standard', NULL),
  (13, 'chesscom', 'd', now(), NULL, NULL, 'standard', NULL);
INSERT INTO player_games (player_id, chess_game_id, player_color, source, result, analyzed_at_depth, lost_wins_reviewed) VALUES
  (1, 10, 'white', 'lichess', 'win', 18, true), (1, 11, 'white', 'lichess', 'loss', 18, false), (1065, 12, 'black', 'chesscom', 'draw', NULL, false);
INSERT INTO books (id, {VENDOR}_bid, title, color, player_id) VALUES (7, 12345, 'Mine', 'white', 1), (8, 99, 'Theirs', 'black', 1065);
INSERT INTO chapters (id, book_id, {VENDOR}_lid, title) VALUES (70, 7, 555, 'Ch'), (80, 8, 556, 'Other');
INSERT INTO repertoire_lines (id, chapter_id, {VENDOR}_line_id, line_name, moves, fen_sequence) VALUES
  (700, 70, 'L1', 'main', '["e4"]', '["{START}"]'), (800, 80, 'L2', 'x', '["d4"]', '["{START}"]');
INSERT INTO repertoire_annotations (id, player_id, fen_norm, text, source, line_id, book_id, chapter_id) VALUES
  (1, 1, '{START}', 'from the course', '{VENDOR}', 700, 7, 70),
  (2, 1, 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1', 'my note', 'manual', 700, 7, 70),
  (3, 1, '{START}', 'from a study', 'lichess', 700, 7, 70);
INSERT INTO blunders (id, player_id, chess_game_id, ply, fen, classification) VALUES
  (1, 1, 10, 0, '{START}', 'blunder'), (2, 1, 11, 0, '{FEN960}', 'blunder'), (3, 1065, 10, 0, '{START}', 'miss');
INSERT INTO player_motif_events (id, player_id, chess_game_id, ply, metric_type, theme, player_color, endgame_outcome) VALUES
  (1, 1, 10, 0, 'motif', 'fork', 'white', NULL), (2, 1, 10, 0, 'endgame', 'KRvK', 'white', 'held'), (3, 1, 11, 0, 'motif', 'pin', 'white', NULL);
INSERT INTO game_repertoire_results (id, player_id, chess_game_id, book_id, chapter_id, deviated_at_ply, deviation_by, deviation_fen) VALUES
  (1, 1, 10, 7, 70, 1, 'me', '{START}'), (2, 1, 11, 7, 70, 1, 'me', '{FEN960}');
INSERT INTO game_result_lines (id, game_repertoire_result_id, line_id, matched_ply) VALUES (1, 1, 700, 1), (2, 2, 700, 1);
INSERT INTO opponent_profiles (id, player_id, name, onboard_attempts) VALUES (5, 1, 'rival', 3), (6, 1065, 'other', 0);
INSERT INTO opponent_sources (id, opponent_profile_id, source_type, username) VALUES (1, 5, 'lichess', 'rival'), (2, 6, 'lichess', 'x');
INSERT INTO opponent_views (opponent_profile_id, chess_game_id, source_type, played_as, result) VALUES (5, 12, 'chesscom', 'white', 'win'), (6, 12, 'chesscom', 'black', 'loss');
INSERT INTO puzzles (id, fen, solution_line, source_types, color, player_id, puzzle_kind, created_by) VALUES
  (900, '{START}', '["e4"]', ARRAY['blunder'], 'w', 1, 'line', NULL),
  (1, '{SHARED_FEN}', '["d5"]', ARRAY['lichess_cc0'], 'b', NULL, 'line', NULL),
  (2, '{START}', '["e4"]', ARRAY['lichess_cc0'], 'w', NULL, 'line', NULL),
  (3, '{THIRD_FEN}', '["e5"]', ARRAY['lichess_cc0'], 'b', NULL, 'line', NULL),
  (901, 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1', '["e5"]', ARRAY['manual','blunder'], 'b', 1, 'line', 4),
  (902, '8/8/8/8/8/5k2/6q1/7K b - - 0 1', '["Qg1#"]', ARRAY['own_mate'], 'b', 1, 'endgame_drill', NULL),
  (903, '{START}', '["d4"]', ARRAY['blunder'], 'w', 1065, 'line', NULL);
INSERT INTO puzzle_attempts (id, puzzle_id, player_id, solved, moves_played) VALUES
  (1, 900, 1, true, 'e4'), (2, 902, 1, false, 'Qg1'), (3, 903, 1065, true, 'd4'),
  (4, 1, 1, true, 'd5'), (5, 2, 1, true, 'e4'), (6, 3, 1065, true, 'e5');
INSERT INTO player_puzzle_state (player_id, puzzle_id, level, correct_at_level) VALUES
  (1, 900, 'rook', 1), (1, 902, 'pawn', 0), (1, 1, 'queen', 1), (1, 2, 'queen', 1);
INSERT INTO player_puzzle_exposure (id, player_id, puzzle_id, bucket, batch_id, item_kind, repertoire_line_id) VALUES
  (1, 1, 900, 'your_puzzles', 1, 'puzzle', NULL), (2, 1, NULL, 'your_puzzles', 1, 'repertoire_drill', 700);
INSERT INTO player_puzzle_skip (id, player_id, scope, batch_id, puzzle_id, item_kind, repertoire_line_id) VALUES
  (1, 1, 'all', 1, 900, 'puzzle', NULL), (2, 1, 'all', 1, NULL, 'repertoire_drill', 700);
INSERT INTO dismissed_blunder_fens (id, player_id, fen) VALUES (1, 1, '{START}'), (2, 1065, '{START}');
"""


@pytest.fixture()
def old_db(fresh_db_url: str) -> psycopg.Connection[DictRow]:
    admin, name = _admin_url(fresh_db_url)
    target = f"{name}_old"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(target)))
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
    src = psycopg.Connection[DictRow].connect(with_dbname(fresh_db_url, target), row_factory=dict_row)
    src.execute(sql.SQL(OLD_SCHEMA))  # type: ignore[arg-type]
    src.execute(sql.SQL(OLD_DATA))  # type: ignore[arg-type]
    src.commit()
    return src


def test_migrate_copies_the_player_and_drops_960_derivatives(
    old_db: psycopg.Connection[DictRow], clean: psycopg.Connection[DictRow]
) -> None:
    dst = clean
    counts = migrate.migrate(old_db, dst, MAPPING)
    dst.commit()
    assert counts == {
        "players": 1,
        "chess_games": 3,  # game 13 has no owner among the player's games or scouted opponents
        "player_games": 2,
        "books": 1,
        "chapters": 1,
        "repertoire_lines": 1,
        "repertoire_annotations": 2,
        "blunders": 1,
        "player_motif_events": 1,
        "game_repertoire_results": 1,
        "game_result_lines": 1,
        "opponent_profiles": 1,
        "opponent_sources": 1,
        "opponent_views": 1,
        # 900 and 901 are his; 1 and 2 are shared puzzles his own history reaches. 902 is an
        # endgame drill, 903 belongs to another player, and 3 is shared but only ever
        # solved by someone else.
        "puzzles": 4,
        "puzzle_attempts": 3,
        "player_puzzle_state": 3,
        "player_puzzle_exposure": 1,
        "player_puzzle_skip": 1,
        "dismissed_blunder_fens": 1,
        "puzzles adopted from the shared tier": 2,
        "puzzles retired to keep one per board": 1,
    }
    book = dst.execute("SELECT id, source_book_id, title FROM books").fetchone()
    assert book and (book["id"], book["source_book_id"], book["title"]) == (7, 12345, "Mine")
    line = dst.execute("SELECT id, source_line_id FROM repertoire_lines").fetchone()
    assert line and (line["id"], line["source_line_id"]) == (700, "L1")
    sources = dst.execute("SELECT source FROM repertoire_annotations ORDER BY id").fetchall()
    assert [r["source"] for r in sources] == ["course", "manual"]
    assert dst.execute("SELECT chess_game_id FROM blunders").fetchall()[0]["chess_game_id"] == 10
    ev = dst.execute("SELECT theme FROM player_motif_events").fetchall()
    assert [r["theme"] for r in ev] == ["fork"]
    pg = dst.execute("SELECT player_id, chess_game_id FROM player_games ORDER BY 2").fetchall()
    assert [(r["player_id"], r["chess_game_id"]) for r in pg] == [(PLAYER_ID, 10), (PLAYER_ID, 11)]
    g960 = dst.execute("SELECT variant, starting_fen, position_keys FROM chess_games WHERE id = 11").fetchone()
    assert g960 and g960["variant"] == "chess960" and g960["starting_fen"] == FEN960 and len(g960["position_keys"]) == 1
    # sequences continue after the copied ids
    nxt = dst.execute(
        "INSERT INTO books (title, color, player_id) VALUES ('new', 'white', %s) RETURNING id", (PLAYER_ID,)
    ).fetchone()
    assert nxt and nxt["id"] == 8
    # A hand-made puzzle was 'manual' in the old vocabulary and is 'custom' here.
    sources = dst.execute("SELECT id, source_types, player_id, active FROM puzzles ORDER BY id").fetchall()
    assert [(r["id"], sorted(r["source_types"])) for r in sources] == [
        (1, ["lichess_cc0"]),
        (2, ["lichess_cc0"]),
        (900, ["blunder"]),
        (901, ["blunder", "custom"]),
    ]
    # An adopted puzzle becomes his: there is nobody else here to own it.
    assert {r["player_id"] for r in sources} == {PLAYER_ID}
    # Puzzle 2 and puzzle 900 share a board. The one carrying more progress keeps serving;
    # the other is retired, not deleted, so its attempts and SRS row survive.
    active = {r["id"]: r["active"] for r in sources}
    assert active[2] is True and active[900] is False
    progress = dst.execute("SELECT puzzle_id, level FROM player_puzzle_state ORDER BY puzzle_id").fetchall()
    assert [(r["puzzle_id"], r["level"]) for r in progress] == [(1, "queen"), (2, "queen"), (900, "rook")]
    with pytest.raises(RuntimeError, match="not empty"):
        migrate.migrate(old_db, dst, MAPPING)


def test_only_migrates_the_named_tables_into_a_populated_database(
    old_db: psycopg.Connection[DictRow], clean: psycopg.Connection[DictRow]
) -> None:
    """A later phase adds its tables to a database earlier phases already filled."""
    dst = clean
    first = ["players", "chess_games", "player_games"]
    assert set(migrate.migrate(old_db, dst, MAPPING, only=first)) == set(first)
    dst.commit()
    # The already-populated tables do not block the second pass.
    second = ["puzzles", "puzzle_attempts", "player_puzzle_state"]
    counts = migrate.migrate(old_db, dst, MAPPING, only=second)
    dst.commit()
    assert counts == {
        "puzzles": 4,
        "puzzle_attempts": 3,
        "player_puzzle_state": 3,
        "puzzles adopted from the shared tier": 2,
        "puzzles retired to keep one per board": 1,
    }
    with pytest.raises(RuntimeError, match="not empty"):
        migrate.migrate(old_db, dst, MAPPING, only=["puzzles"])
    with pytest.raises(RuntimeError, match="no migration step"):
        migrate.migrate(old_db, dst, MAPPING, only=["nonexistent"])
