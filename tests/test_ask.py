"""A question about the position on show (core/ai.py `ask_review`, `ask_explore`): the context
from a reviewed game's stored analysis, the browser engine's snapshot parsed as chess, the
move a question names, and the refusals that cost nothing. No network: provider calls go
through an httpx mock transport."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import chess
import httpx
import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from core import ai, prompts, settings
from core.chess.board import moves_to_fen_sequence
from core.constants import PLAYER_ID
from core.settings import Settings

MOVES = ["e4", "e5", "Nf3", "Nc6", "Bc4", "h6", "d3", "Nf6"]
FENS = moves_to_fen_sequence(MOVES)
START = chess.STARTING_FEN
STALEMATE_TRAP = "7k/8/5KQ1/8/8/8/8/8 w - - 0 1"  # Qf7 stalemates, Qg7 mates, Qh6+ plays on
ANALYSIS = [
    {"ply": 0, "eval": 30, "best_move": "e4", "best_line": "e4 e5 Nf3", "mate_in_moves": None},
    {"ply": 3, "eval": None, "best_move": None, "best_line": None, "mate_in_moves": None},
    {"ply": 7, "eval": 40, "best_move": "d6", "best_line": "d6 O-O Nf6", "mate_in_moves": None},
    {"ply": 8, "eval": 120, "best_move": "Ng5", "best_line": "Ng5 d5 exd5", "mate_in_moves": None},
]

Tx = Callable[[], Any]


def _snap(**kw: Any) -> dict[str, Any]:
    return {"depth": 16, "eval_cp": 30, "best_move": "d2d4", "pv": ["d2d4", "d7d5", "c2c4"], **kw}


def _body(**kw: Any) -> dict[str, Any]:
    return {"seed_fen": START, "orientation": "white", "moves": [], "engine": _snap(), "question": "", **kw}


# --- formatting and the snapshot (no database) ------------------------------------------------


@pytest.mark.parametrize(
    ("cp", "mate", "text"),
    [
        (80, None, "+0.8"),
        (-130, None, "-1.3"),
        (0, None, "0.0"),
        (None, None, ""),
        (9997, None, "mate in 3 for White"),
        (-9998, None, "mate in 2 for Black"),
        (9000, None, "mate in 1000 for White"),
        (10000, 3, "mate in 3 for White"),
        (-9000, -2, "mate in 2 for Black"),
    ],
)
def test_evals_are_prose_and_mate_distances_are_moves(cp: int | None, mate: int | None, text: str) -> None:
    assert ai.fmt_eval(cp, mate) == text


def test_a_snapshot_is_rendered_in_the_servers_own_words() -> None:
    snap = ai._snapshot(chess.Board(), _snap())
    assert snap == {"best_move": "d4", "line": "1. d4 d5 2. c4", "eval": "+0.3", "depth": "16", "agrees": True}


def test_a_final_move_that_disagrees_with_the_scored_line_is_kept_beside_it() -> None:
    snap = ai._snapshot(chess.Board(), _snap(best_move="e2e4"))
    assert snap["best_move"] == "e4" and snap["line"] == "1. d4 d5 2. c4" and snap["agrees"] is False


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (_snap(pv=["d2d4"] * 13), "the engine line must have 1 to 12 moves"),
        (_snap(pv=[]), "the engine line must have 1 to 12 moves"),
        (_snap(best_move="e2e5"), "the engine's best move is not a legal move"),
        (_snap(best_move="0000"), "the engine's best move is not a legal move"),
        (_snap(pv=["d2d4", "0000"]), "a move in the engine line is not a legal move"),
        (_snap(pv=["d2d4", "d2d3"]), "a move in the engine line is not a legal move"),
        (_snap(pv=["d2d4", "xx"]), "a move in the engine line is not a move"),
        (_snap(eval_cp=10000), "the engine evaluation is out of range"),
        (_snap(eval_cp=-10000), "the engine evaluation is out of range"),
        (_snap(eval_cp="30"), "the engine evaluation is out of range"),
        (_snap(depth=0), "the engine depth is out of range"),
        (_snap(depth=31), "the engine depth is out of range"),
        (None, "the engine readout is missing"),
    ],
)
def test_a_snapshot_that_is_not_chess_is_refused_with_a_fixed_message(raw: Any, message: str) -> None:
    with pytest.raises(ai.ExplainError) as err:
        ai._snapshot(chess.Board(), raw)
    assert err.value.status == 400 and err.value.detail == message


def test_the_agreement_flags_are_booleans_because_the_template_branches_on_them() -> None:
    """A string "false" is a non-empty value and would take the agreeing branch."""
    text = str(Settings().ai_ask_prompt.text)
    base = ai.ask_context(
        origin="explore",
        board=chess.Board(),
        color="white",
        question="q",
        engine_source="s",
        snapshot=ai._snapshot(chess.Board(), _snap(best_move="e2e4")),
        game=ai._NO_GAME,
        alternative=ai._alternative(chess.Board(), "e4", None),
        explored=[],
        seed_fen="",
    )
    assert base["line_agrees"] is False and base["alt_line_agrees"] is False
    assert "it finalised on e4" in prompts.render(text, base)
    assert "it finalised on" not in prompts.render(text, {**base, "line_agrees": "false"})
    strings = {k: v for k, v in base.items() if k not in ("line_agrees", "alt_line_agrees")}
    assert all(isinstance(v, str) for v in strings.values())


# --- the alternative (no database) -----------------------------------------------------------


def test_an_alternative_carries_the_engines_final_reply_separately_from_its_line() -> None:
    board = chess.Board()
    alt = ai._alternative(board, "d4", {"move": "e4", "engine": _snap(pv=["e7e5", "g1f3"], best_move="c7c5")})
    assert alt == {
        "alt_move": "e4",
        "alt_outcome": "",
        "alt_eval": "+0.3",
        "alt_line": "1... e5 2. Nf3",
        "alt_depth": "16",
        "alt_best": "c5",
        "alt_line_agrees": False,
    }
    agreeing = ai._alternative(board, "d4", {"move": "e4", "engine": _snap(pv=["e7e5", "g1f3"], best_move="e7e5")})
    assert agreeing["alt_best"] == "e5" and agreeing["alt_line_agrees"] is True
    text = str(Settings().ai_ask_prompt.text)
    ctx = dict.fromkeys(ai._NO_GAME, "") | {
        "fen": START,
        "color": "white",
        "side_to_move": "White",
        "question": "q",
        "engine_source": "s",
        "eval": "+0.3",
        "best_move": "d4",
        "best_line": "1. d4",
        "line_agrees": True,
        "explored_moves": "",
        "seed_fen": "",
    }
    assert "the last line it scored was 1... e5 2. Nf3 and it finalised on the reply c5" in prompts.render(
        text, ctx | alt
    )
    assert "and replies 1... e5 2. Nf3" in prompts.render(text, ctx | agreeing)
    assert ai.context_hash(ctx | alt) != ai.context_hash(ctx | agreeing)  # only the final reply differs


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"move": "d4", "engine": _snap()}, "alternative is the engine's move"),
        ({"move": "e5", "engine": _snap()}, "alternative is not a legal move"),
        ({"move": "--", "engine": _snap()}, "alternative is not a legal move"),
        ({"move": "0000", "engine": _snap()}, "alternative is not a legal move"),
        ({"move": "e4"}, "the alternative needs an engine line"),
        (
            {"move": "e4", "engine": _snap(best_move="e7e5", pv=["d2d4"])},
            "a move in the engine line is not a legal move",
        ),
        ({"move": "e4", "engine": _snap(pv=["e7e5"])}, "the engine's best move is not a legal move"),
        ("e4", "alternative is not a move"),
    ],
)
def test_an_alternative_that_is_not_chess_is_refused(raw: Any, message: str) -> None:
    with pytest.raises(ai.ExplainError) as err:
        ai._alternative(chess.Board(), "d4", raw)
    assert err.value.status == 400 and err.value.detail == message


def test_an_alternative_that_ends_the_game_has_an_outcome_and_no_line() -> None:
    board = chess.Board(STALEMATE_TRAP)
    stalemate = ai._alternative(board, "Qh6+", {"move": "Qf7"})
    assert stalemate["alt_move"] == "Qf7" and stalemate["alt_outcome"] == "stalemate"
    assert stalemate["alt_line"] == "" and stalemate["alt_best"] == "" and stalemate["alt_line_agrees"] is False
    assert ai._alternative(board, "Qh6+", {"move": "Qg7"})["alt_outcome"] == "checkmate"
    with pytest.raises(ai.ExplainError) as err:
        ai._alternative(board, "Qh6+", {"move": "Qf7", "engine": _snap(best_move="h8g8", pv=["h8g8"])})
    assert err.value.status == 400 and err.value.detail == "a finished board has no engine line"
    text = str(Settings().ai_ask_prompt.text)
    ctx = dict.fromkeys(ai._NO_GAME, "") | {
        "fen": STALEMATE_TRAP,
        "color": "white",
        "side_to_move": "White",
        "question": "Why can't I play Qf7?",
        "engine_source": "s",
        "eval": "mate in 1 for White",
        "best_move": "Qg7",
        "best_line": "1. Qg7#",
        "line_agrees": True,
        "explored_moves": "",
        "seed_fen": "",
    }
    rendered = prompts.render(text, ctx | stalemate)
    assert "The move I am asking about: Qf7. It ends the game: stalemate." in rendered
    assert "replies" not in rendered and "finalised" not in rendered


# --- fixtures --------------------------------------------------------------------------------


@pytest.fixture()
def tx(clean: psycopg.Connection[DictRow], fresh_db_url: str) -> Tx:
    clean.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me')", (PLAYER_ID,))
    clean.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, played_at, variant, time_class, moves, fen_sequence,"
        " ply_analysis, ply_analysis_depth, analysis_status, opening_name)"
        " VALUES (1, 'lichess', 'g1', now(), 'standard', 'rapid', %s::jsonb, %s::jsonb, %s::jsonb, 18, 'completed',"
        " 'Italian')",
        (json.dumps(MOVES), json.dumps(FENS), json.dumps(ANALYSIS)),
    )
    clean.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, player_rating)"
        " VALUES (%s, 1, 'black', 'lichess', 1500)",
        (PLAYER_ID,),
    )
    clean.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, best_line, post_blunder_line,"
        " centipawn_loss, classification) VALUES (%s, 1, 7, %s, 'Nf6', 'd6', 'd6 O-O Nf6', 'Ng5 d5', 250, 'blunder')",
        (PLAYER_ID, FENS[7]),
    )
    clean.commit()

    @contextmanager
    def open_tx() -> Generator[psycopg.Connection[DictRow]]:
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as conn:
            yield conn

    return open_tx


def _configure(tx: Tx, **kw: Any) -> None:
    with tx() as conn:
        settings.save(conn, Settings(**kw))


def _client(
    text: str = "Because d6 holds e5.", status: int = 200, seen: list[httpx.Request] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"type": "overloaded_error"}})
        content = [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": text}]
        return httpx.Response(200, json={"content": content, "usage": {"input_tokens": 700, "output_tokens": 300}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _calls(tx: Tx) -> list[dict[str, Any]]:
    with tx() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ai_calls ORDER BY id").fetchall()]


def _cache_rows(tx: Tx) -> int:
    with tx() as conn:
        row = conn.execute("SELECT count(*) AS n FROM ai_explanation_cache").fetchone()
        assert row is not None
        return int(row["n"])


def _housekeep(tx: Tx) -> None:
    with tx() as conn:
        conn.execute("UPDATE chess_games SET moves = NULL, fen_sequence = NULL, ply_analysis = NULL WHERE id = 1")


# --- ask_review --------------------------------------------------------------------------------


def test_a_review_question_is_grounded_in_the_games_stored_analysis(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    out = ai.ask_review(tx, 1, 7, "", dry_run=True)
    assert out["model"] == "claude-opus-5-5" and out["request_body"]["thinking"] == {"type": "adaptive"}
    text = out["rendered_prompt"]
    assert f"Position (FEN): {FENS[7]}" in text
    assert "I play black; Black to move." in text and "Opening: Italian" in text
    assert "The moves before this position: 1. e4 e5 2. Nf3 Nc6 3. Bc4 h6 4. d3" in text
    assert "In the game, you played Nf6 here (blunder, 250 centipawns lost)." in text
    assert (
        "Engine (Stockfish 19 at depth 18, from the game's stored analysis): evaluation +0.4; best move d6; line 4... d6 5. O-O Nf6"
        in text
    )
    assert text.endswith("My question: Why is d6 the engine's choice here?")
    assert "repertoire" not in text and "played out" not in text  # no book, nothing explored
    assert _calls(tx) == [] and _cache_rows(tx) == 0


def test_the_last_ply_and_an_opponent_move_are_described_as_such(tx: Tx) -> None:
    _configure(tx)
    last = ai.ask_review(tx, 1, 8, "what now?", dry_run=True)["rendered_prompt"]
    assert "White to move" in last and "In the game," not in last
    assert "best move Ng5; line 5. Ng5 d5 6. exd5" in last and last.endswith("My question: what now?")
    opponent = ai.ask_review(tx, 1, 0, dry_run=True)["rendered_prompt"]
    assert "In the game, your opponent played e4 here." in opponent and "The moves before" not in opponent


def test_a_repeat_review_question_is_free_and_shares_the_caps(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx, ai_explain_max_per_day=2)
    seen: list[httpx.Request] = []
    first = ai.ask_review(tx, 1, 7, "", client=_client(seen=seen))
    assert first == {
        "explanation": "Because d6 holds e5.",
        "cached": False,
        "model": "claude-opus-5-5",
        "prompt_label": "Ask about this position",
    }
    sent = json.loads(seen[0].content)
    assert sent["max_tokens"] == 16000 and "temperature" not in sent
    assert ai.ask_review(tx, 1, 7, "   ", client=_client(status=500))["cached"] is True  # blank = the default
    assert ai.ask_review(tx, 1, 7, "why not Nf6?", client=_client("Asked."))["cached"] is False
    assert [c["prompt_key"] for c in _calls(tx)] == ["ask", "ask"] and _calls(tx)[0]["output_tokens"] == 300
    with pytest.raises(ai.ExplainError) as err:
        ai.ask_review(tx, 1, 8, "", client=_client())
    assert err.value.status == 429 and err.value.detail["window"] == "daily"  # type: ignore[index]


def test_a_review_question_with_an_alternative_pays_for_a_new_line_only(
    tx: Tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    alt = {"move": "Nf6", "engine": {"depth": 16, "eval_cp": 110, "best_move": "f3g5", "pv": ["f3g5", "d7d5"]}}
    out = ai.ask_review(tx, 1, 7, "Why can't I play Nf6?", alt, dry_run=True)["rendered_prompt"]
    assert (
        "The move I am asking about: Nf6. After it the engine (in-browser Stockfish at depth 16) gives +1.1 and replies 5. Ng5 d5"
        in out
    )
    assert ai.ask_review(tx, 1, 7, "Why can't I play Nf6?", alt, client=_client())["cached"] is False
    assert ai.ask_review(tx, 1, 7, "Why can't I play Nf6?", alt, client=_client(status=500))["cached"] is True
    other = {**alt, "engine": {**alt["engine"], "pv": ["f3g5", "h6g5"]}}
    assert ai.ask_review(tx, 1, 7, "Why can't I play Nf6?", other, client=_client())["cached"] is False
    assert len(_calls(tx)) == 2


def test_review_refusals_cost_nothing(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    seen: list[httpx.Request] = []
    cases: list[tuple[tuple[Any, ...], int, str]] = [
        ((2, 7, ""), 404, "game not found"),
        ((1, 9, ""), 422, "ply is past the end of the game"),
        ((1, -1, ""), 422, "ply is past the end of the game"),
        ((1, 3, ""), 422, "no engine analysis for this position"),
        ((1, 5, ""), 422, "no engine analysis for this position"),
        ((1, 7, "x" * 501), 422, "the question is longer than 500 characters"),
        ((1, 7, "", {"move": "d6", "engine": _snap()}), 400, "alternative is the engine's move"),
    ]
    for args, status, detail in cases:
        for dry in (True, False):
            with pytest.raises(ai.ExplainError) as err:
                ai.ask_review(tx, *args, dry_run=dry, client=_client(seen=seen))
            assert (err.value.status, err.value.detail) == (status, detail), args
    with tx() as conn:
        conn.execute("UPDATE chess_games SET variant = 'chess960', starting_fen = %s WHERE id = 1", (FENS[0],))
    with pytest.raises(ai.ExplainError) as err:
        ai.ask_review(tx, 1, 7, "", client=_client(seen=seen))
    assert err.value.status == 422
    assert seen == [] and _calls(tx) == [] and _cache_rows(tx) == 0


def test_a_game_with_moves_but_no_analysis_has_nothing_to_ask_about(tx: Tx) -> None:
    with tx() as conn:
        conn.execute("UPDATE chess_games SET ply_analysis = NULL WHERE id = 1")
    _configure(tx)
    with pytest.raises(ai.ExplainError) as err:
        ai.ask_review(tx, 1, 7, "", dry_run=True)
    assert (err.value.status, err.value.detail) == (422, "no engine analysis for this position")
    assert _calls(tx) == []


def test_a_housekept_game_is_refused_before_anything_is_indexed(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    _housekeep(tx)
    seen: list[httpx.Request] = []
    for dry in (True, False):
        with pytest.raises(ai.ExplainError) as err:
            ai.ask_review(tx, 1, 0, "", dry_run=dry, client=_client(seen=seen))
        assert (err.value.status, err.value.detail) == (422, "the game's moves are no longer stored")
        with pytest.raises(ai.ExplainError) as err:
            ai.ask_explore(tx, _body(game={"id": 1, "ply": 0}), dry_run=dry, client=_client(seen=seen))
        assert (err.value.status, err.value.detail) == (422, "the game's moves are no longer stored")
    assert seen == [] and _calls(tx) == [] and _cache_rows(tx) == 0


# --- ask_explore -------------------------------------------------------------------------------


def test_an_explore_question_carries_the_line_and_the_snapshot_in_the_servers_words(tx: Tx) -> None:
    _configure(tx)
    body = _body(
        seed_fen=FENS[7],
        orientation="black",
        moves=["Nf6", "Ng5"],
        engine={"depth": 20, "eval_cp": -9998, "best_move": "d7d5", "pv": ["d7d5", "e4d5", "c6a5"]},
        question="Is this already lost?",
    )
    text = ai.ask_explore(tx, body, dry_run=True)["rendered_prompt"]
    assert f"From the position I started from ({FENS[7]}) I have played out: 4... Nf6 5. Ng5" in text
    assert "I play black; Black to move." in text
    assert (
        "Engine (in-browser Stockfish 19 at depth 20): evaluation mate in 2 for Black; best move d5; line 5... d5 6. exd5 Na5"
        in text
    )
    assert "d7d5" not in text and "e4d5" not in text and "In the game" not in text
    assert text.endswith("My question: Is this already lost?")


def test_an_explore_question_from_a_review_board_carries_the_game_until_a_move_is_played(tx: Tx) -> None:
    _configure(tx)
    seed = _body(seed_fen=FENS[7], orientation="black", engine=_snap(best_move="d7d6", pv=["d7d6", "e1g1"]))
    text = ai.ask_explore(tx, {**seed, "game": {"id": 1, "ply": 7}}, dry_run=True)["rendered_prompt"]
    assert "Opening: Italian" in text and "In the game, you played Nf6 here (blunder, 250 centipawns lost)." in text
    moved = {**seed, "moves": ["Nf6"], "engine": _snap(best_move="f3g5", pv=["f3g5"]), "game": {"id": 1, "ply": 7}}
    text = ai.ask_explore(tx, moved, dry_run=True)["rendered_prompt"]
    assert "In the game" not in text and "Opening" not in text and "played out: 4... Nf6" in text


def test_explore_refusals_cost_nothing(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    seen: list[httpx.Request] = []
    two_white_kings = "k7/8/8/8/8/8/8/K6K w - - 0 1"
    cases: list[tuple[dict[str, Any], int, str]] = [
        (
            _body(seed_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"),
            400,
            "the position is not a full six-field FEN",
        ),
        (_body(seed_fen="not a fen at all x"), 400, "the position is not a valid FEN"),
        (_body(seed_fen=two_white_kings), 400, "the position is not a legal chess position"),
        (_body(moves=["e5"]), 400, "an explored move is not legal"),
        (_body(moves=["--"]), 400, "an explored move is not legal"),
        (_body(moves=["Z0"]), 400, "an explored move is not legal"),
        (_body(moves=["e4"] * 61), 400, "at most 60 explored moves"),
        (_body(seed_fen=STALEMATE_TRAP, moves=["Qg7#"]), 422, "nothing to ask on a finished board"),
        (_body(orientation="red"), 400, "orientation must be white or black"),
        (_body(question="x" * 501), 422, "the question is longer than 500 characters"),
        (_body(game={"id": 1, "ply": 7}), 400, "seed is not the game's board at that ply"),
        (_body(game={"id": 1, "ply": 40}), 422, "ply is past the end of the game"),
        (_body(game={"id": 2, "ply": 0}), 404, "game not found"),
    ]
    for body, status, detail in cases:
        for dry in (True, False):
            with pytest.raises(ai.ExplainError) as err:
                ai.ask_explore(tx, body, dry_run=dry, client=_client(seen=seen))
            assert (err.value.status, err.value.detail) == (status, detail), body
    with tx() as conn:
        conn.execute("UPDATE chess_games SET variant = 'chess960', starting_fen = %s WHERE id = 1", (FENS[0],))
    with pytest.raises(ai.ExplainError) as err:
        ai.ask_explore(tx, _body(game={"id": 1, "ply": 0}), client=_client(seen=seen))
    assert err.value.status == 422
    assert seen == [] and _calls(tx) == [] and _cache_rows(tx) == 0


def test_an_explore_answer_is_recorded_under_the_ask_key(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    body = _body(alternative={"move": "e4", "engine": _snap(pv=["e7e5", "g1f3"], best_move="c7c5")})
    out = ai.ask_explore(tx, body, client=_client("Because c5 bites."))
    assert out["explanation"] == "Because c5 bites." and out["cached"] is False
    assert [c["prompt_key"] for c in _calls(tx)] == ["ask"] and _cache_rows(tx) == 1
    assert ai.ask_explore(tx, body, client=_client(status=500))["cached"] is True


def test_a_terminal_alternative_from_explore_renders_its_outcome(tx: Tx) -> None:
    _configure(tx)
    body = _body(
        seed_fen=STALEMATE_TRAP,
        engine={"depth": 12, "eval_cp": 9999, "best_move": "g6h6", "pv": ["g6h6", "h8g8"]},
        question="Why can't I play Qf7?",
        alternative={"move": "Qf7"},
    )
    text = ai.ask_explore(tx, body, dry_run=True)["rendered_prompt"]
    assert "The move I am asking about: Qf7. It ends the game: stalemate." in text and "replies" not in text


# --- settings ------------------------------------------------------------------------------------


def test_the_default_ask_prompt_is_opus_thinking_with_room_for_it() -> None:
    prompt = Settings().ai_ask_prompt
    assert prompt.model == "claude-opus-5-5" and prompt.thinking_enabled and prompt.max_tokens == 16000
    assert ai.effective_params(prompt)["thinking"] == {"type": "adaptive"}
    values = Settings()
    values.ai_ask_prompt.thinking_enabled = False
    assert settings.save_errors(values) == ["ai_ask_prompt: claude-opus-5-5 always thinks; turn thinking on"]
    assert settings.schema()["properties"]["ai_ask_prompt"]["type"] == "object"
