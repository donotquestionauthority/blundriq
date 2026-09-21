"""AI explanations: parameters, context, cache, caps, providers (core/ai.py). No network:
provider calls go through an httpx mock transport."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from core import ai, settings
from core.constants import PLAYER_ID
from core.settings import AiPrompt, Settings

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 7 12"


def _prompt(**kw: Any) -> AiPrompt:
    return AiPrompt(**{"model": "claude-haiku-4-5", "text": "Explain {{ move_played }} at {{ fen }}", **kw})


# --- parameters and hashes -----------------------------------------------------------------


def test_thinking_forbids_prefill_and_temperature_and_makes_room_for_the_answer() -> None:
    eff = ai.effective_params(_prompt(thinking_enabled=True, thinking_budget_tokens=100, prefill="x", temperature=0.2))
    assert eff["prefill"] == "" and eff["temperature"] is None
    assert eff["thinking"] == {"type": "enabled", "budget_tokens": 1024} and eff["max_tokens"] == 1024 + 256
    body = ai.request_body("claude-haiku-4-5", "hi", eff)
    assert "temperature" not in body and len(body["messages"]) == 1 and body["max_tokens"] == 1280


def test_thinking_off_is_said_explicitly_only_to_a_model_that_thinks_by_default() -> None:
    assert ai.effective_params(_prompt(model="claude-sonnet-5"))["thinking"] == {"type": "disabled"}
    eff = ai.effective_params(_prompt(prefill="**Because ", temperature=0.3, system_prompt="  coach  "))
    assert "thinking" not in eff
    body = ai.request_body("claude-haiku-4-5", "hi", eff)
    assert body["system"] == "coach" and body["temperature"] == 0.3
    assert body["messages"][-1] == {"role": "assistant", "content": "**Because"}  # no trailing whitespace


def test_openai_gets_only_what_it_understands_and_its_hash_ignores_the_rest() -> None:
    a = _prompt(model="gpt-4o", system_prompt="coach")
    b = _prompt(model="gpt-4o", system_prompt="coach", thinking_enabled=True, prefill="x")
    assert ai.prompt_hash(a) == ai.prompt_hash(b)
    body = ai.request_body("gpt-4o", "hi", ai.effective_params(b))
    assert set(body) == {"model", "messages", "max_completion_tokens"}
    assert body["messages"][0] == {"role": "system", "content": "coach"}


@pytest.mark.parametrize(
    "change",
    [
        {"text": "other"},
        {"model": "claude-sonnet-5"},
        {"temperature": 0.5},
        {"max_tokens": 600},
        {"system_prompt": "x"},
        {"prefill": "y"},
        {"thinking_enabled": True},
    ],
)
def test_anything_that_changes_the_request_changes_the_prompt_hash(change: dict[str, Any]) -> None:
    assert ai.prompt_hash(_prompt()) != ai.prompt_hash(_prompt(**change))


def test_a_label_does_not_change_the_hash_and_an_unknown_model_is_refused() -> None:
    assert ai.prompt_hash(_prompt()) == ai.prompt_hash(_prompt(label="renamed"))
    with pytest.raises(ai.ExplainError) as err:
        ai.effective_params(_prompt(model="llama-9"))
    assert err.value.status == 400


# --- context ---------------------------------------------------------------------------------


def _row(**kw: Any) -> dict[str, Any]:
    return {
        "fen": FEN,
        "move_played": "Nf6",
        "best_move": "d6",
        "best_line": "d6 O-O Nf6",
        "post_blunder_line": "Ng5 d5",
        "centipawn_loss": 0,
        "classification": "blunder",
        "phase": None,
        "player_color": "black",
        "player_rating": 1500,
        "moves": ["e4", "e5", "Nf3", "Nc6", "Bc4", "h6", "d3"],
        "opening_name": "Italian",
        **kw,
    }


def test_the_context_is_all_strings_with_lines_numbered_from_where_they_start() -> None:
    ctx = ai.build_context(_row(), ply=7)
    assert ctx["best_line"] == "12... d6 13. O-O Nf6"
    assert ctx["post_blunder_line"] == "13. Ng5 d5"  # after Black's move the number has advanced
    assert ctx["pgn_context"] == "2. Nf3 Nc6 3. Bc4 h6 4. d3"
    assert ctx["cp_loss"] == "0" and ctx["phase"] == "" and ctx["is_blunder"] is True
    assert ctx["fen"].endswith(" b KQkq - 0 12")
    assert all(isinstance(v, str) for k, v in ctx.items() if k != "is_blunder")


def test_boards_that_differ_only_by_the_halfmove_clock_share_a_context() -> None:
    other = FEN.replace(" 7 12", " 3 12")
    assert ai.context_hash(ai.build_context(_row(), 7)) == ai.context_hash(ai.build_context(_row(fen=other), 7))
    assert ai.context_hash(ai.build_context(_row(), 7)) != ai.context_hash(ai.build_context(_row(move_played="a6"), 7))


# --- explain ---------------------------------------------------------------------------------

Tx = Callable[[], Any]


@pytest.fixture()
def tx(clean: psycopg.Connection[DictRow], fresh_db_url: str) -> Tx:
    clean.execute("INSERT INTO players (id, chesscom_username) VALUES (%s, 'me')", (PLAYER_ID,))
    clean.execute(
        "INSERT INTO chess_games (id, platform, platform_game_id, played_at, variant, time_class, moves, opening_name)"
        " VALUES (1, 'lichess', 'g1', now(), 'standard', 'rapid', %s::jsonb, 'Italian')",
        (json.dumps(_row()["moves"]),),
    )
    clean.execute(
        "INSERT INTO player_games (player_id, chess_game_id, player_color, source, player_rating) VALUES (%s, 1, 'black', 'lichess', 1500)",
        (PLAYER_ID,),
    )
    clean.execute(
        "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, best_line, post_blunder_line,"
        " centipawn_loss, classification) VALUES (%s, 1, 7, %s, 'Nf6', 'd6', 'd6 O-O Nf6', 'Ng5 d5', 250, 'blunder')",
        (PLAYER_ID, FEN),
    )
    clean.commit()

    @contextmanager
    def open_tx() -> Generator[psycopg.Connection[DictRow], None, None]:
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as conn:
            yield conn  # commits on a clean exit, rolls back on an exception

    return open_tx


def _configure(tx: Tx, **kw: Any) -> None:
    prompts = {
        "a": _prompt(label="Explain"),
        "o": _prompt(model="gpt-4o"),
        "bad": _prompt(text="{{ nope }}"),
        "empty": _prompt(text=" "),
    }
    with tx() as conn:
        settings.save(conn, Settings(**{"ai_prompts": prompts, **kw}))


def _anthropic(
    text: str = "Because the knight hangs.", status: int = 200, seen: list[httpx.Request] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(
                status, json={"error": {"type": "overloaded_error", "message": "secret-ish text sk-ant-xyz"}}
            )
        if "openai" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": text}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4},
                },
            )
        content = [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": text}]
        return httpx.Response(200, json={"content": content, "usage": {"input_tokens": 11, "output_tokens": 5}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _calls(tx: Tx) -> list[dict[str, Any]]:
    with tx() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ai_calls ORDER BY id").fetchall()]


def test_a_miss_calls_the_provider_once_then_the_cache_answers_for_free(
    tx: Tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-anthropic")
    _configure(tx)
    seen: list[httpx.Request] = []
    first = ai.explain(tx, 1, 7, "a", client=_anthropic(seen=seen))
    assert first == {
        "explanation": "Because the knight hangs.",
        "cached": False,
        "model": "claude-haiku-4-5",
        "prompt_label": "Explain",
    }
    assert seen[0].headers["x-api-key"] == "test-key-anthropic" and seen[0].headers["anthropic-version"]
    assert json.loads(seen[0].content)["messages"][0]["content"].startswith("Explain Nf6 at r1bqkbnr")
    second = ai.explain(tx, 1, 7, "a", client=_anthropic(status=500))  # would fail if it were called
    assert second["cached"] is True and second["explanation"] == first["explanation"]
    (call,) = _calls(tx)
    assert (call["prompt_key"], call["model"], call["input_tokens"], call["output_tokens"]) == (
        "a",
        "claude-haiku-4-5",
        11,
        5,
    )


def test_a_failed_call_gives_its_slot_back_and_says_nothing_the_provider_said(
    tx: Tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-anthropic")
    _configure(tx)
    with pytest.raises(ai.ExplainError) as err:
        ai.explain(tx, 1, 7, "a", client=_anthropic(status=529))
    assert err.value.status == 502 and err.value.detail == "AI provider refused the call (HTTP 529, overloaded_error)"
    assert "sk-ant" not in str(err.value) and _calls(tx) == []

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure for https://api.example/key=abc")

    with pytest.raises(ai.ExplainError) as err:
        ai.explain(tx, 1, 7, "a", client=httpx.Client(transport=httpx.MockTransport(unreachable)))
    assert err.value.detail == "AI provider could not be reached (ConnectError)" and _calls(tx) == []
    with pytest.raises(ai.ExplainError, match="no text"):
        ai.explain(tx, 1, 7, "a", client=_anthropic(text=" "))
    assert _calls(tx) == []


def test_the_caps_count_provider_calls_and_name_the_window(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k2")
    _configure(tx, ai_explain_max_per_hour=1, ai_explain_max_per_day=5)
    ai.explain(tx, 1, 7, "a", client=_anthropic())
    with pytest.raises(ai.ExplainError) as err:
        ai.explain(tx, 1, 7, "o", client=_anthropic())
    assert err.value.status == 429 and isinstance(err.value.detail, dict) and err.value.detail["window"] == "hourly"
    assert ai.explain(tx, 1, 7, "a", client=_anthropic())["cached"] is True  # a cached answer needs no slot
    with tx() as conn:
        conn.execute("UPDATE ai_calls SET called_at = now() - interval '2 hours'")
        conn.execute(
            "INSERT INTO ai_calls (prompt_key, model, called_at) SELECT 'a', 'm', now() - interval '3 hours' FROM generate_series(1, 4)"
        )
    with pytest.raises(ai.ExplainError) as err:
        ai.explain(tx, 1, 7, "o", client=_anthropic())
    assert isinstance(err.value.detail, dict) and err.value.detail["window"] == "daily"
    _configure(tx, ai_explain_max_per_hour=0, ai_explain_max_per_day=0)  # 0 = no cap
    assert ai.explain(tx, 1, 7, "o", client=_anthropic("From OpenAI."))["explanation"] == "From OpenAI."
    assert _calls(tx)[-1]["input_tokens"] == 9


def test_a_missing_key_is_not_configured_and_costs_nothing(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _configure(tx)
    with pytest.raises(ai.ExplainError) as err:
        ai.explain(tx, 1, 7, "a", client=_anthropic())
    assert err.value.status == 503 and "anthropic" in str(err.value.detail) and _calls(tx) == []


def test_refusals_that_never_reach_a_provider(tx: Tx) -> None:
    _configure(tx)
    for args, status in (
        ((1, 7, "zz"), 404),
        ((1, 7, "empty"), 404),
        ((1, 8, "a"), 404),
        ((2, 7, "a"), 404),
        ((1, 7, "bad"), 500),
    ):
        with pytest.raises(ai.ExplainError) as err:
            ai.explain(tx, *args, client=_anthropic())
        assert err.value.status == status
    assert _calls(tx) == []


def test_a_dry_run_shows_what_would_be_sent_and_touches_nothing(tx: Tx) -> None:
    _configure(tx)
    out = ai.explain(tx, 1, 7, "a", dry_run=True, client=_anthropic(status=500))
    assert out["dry_run"] is True and out["provider"] == "anthropic" and out["max_tokens"] == 512
    assert out["rendered_prompt"] == "Explain Nf6 at r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 12"
    assert _calls(tx) == []
    with tx() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ai_explanation_cache").fetchone() == {"n": 0}


def test_the_default_prompts_render_against_a_real_context() -> None:
    from core import prompts

    ctx = ai.build_context(_row(), 7)
    for key, prompt in Settings().ai_prompts.items():
        assert "Nf6" in prompts.render(prompt.text, ctx), key
        ai.effective_params(prompt)


def test_prompt_labels_put_the_default_first_and_skip_unusable_ones() -> None:
    config = Settings(
        ai_prompts={"a": _prompt(label="A"), "b": _prompt(label=""), "c": _prompt(text="")}, ai_default_prompt="b"
    )
    assert ai.prompt_labels(config) == [
        {"key": "b", "label": "B", "model": "claude-haiku-4-5"},
        {"key": "a", "label": "A", "model": "claude-haiku-4-5"},
    ]


def test_templates_are_sandboxed_and_checked_when_saved() -> None:
    from jinja2.exceptions import SecurityError

    from core import prompts

    with pytest.raises(ValueError, match="template syntax"):
        AiPrompt(text="{% if x %}")
    with pytest.raises(SecurityError):
        prompts.render("{{ fen.__class__.__mro__ }}", {"fen": "x"})
