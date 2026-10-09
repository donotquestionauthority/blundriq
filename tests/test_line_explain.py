"""Explaining a move in a repertoire line (core/ai.py `explain_line`): which note belongs to
which move, the cache and caps it shares with blunder explanations, the thinking rules for
models that take only adaptive thinking or always think, and the save-time refusal. No
network: provider calls go through an httpx mock transport."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow, dict_row

from core import ai, settings
from core.repertoire import annotations as ann
from core.settings import AiPrompt, Settings
from tests import repertoire_helpers as h

MAIN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]
FENS = h.spine(None, MAIN)
Tx = Callable[[], Any]

# Prompt hashes of the blunder prompts as they were configured before adaptive thinking had a
# path of its own (a stored settings row still holds them until it is edited), and of
# claude-sonnet-5 with thinking off: their cache rows must stay valid.
STORED_PROMPT_HASHES = {
    "a": "281baa3489d18b8e979ec34085f32796c0b8ac3dcf398843bae1c3bb99017f4d",
    "b": "3fbfa97efc0903b6289d8c74ac96be9043529fed9dbfc3ec14693a76d358f6aa",
    "c": "21dcb170120089a9d1b8a64a1e52762d58361a55164833b7f3c9789fb8bd5d75",
}
SONNET5_OFF_HASH = "47dd03cdc17bcf0905db3fe59b3268dc60218032050b26225bd204a7f03f2965"


def _line_prompt(**kw: Any) -> AiPrompt:
    return AiPrompt(**{**Settings().ai_line_prompt.model_dump(), **kw})


# --- the index contract (no database) --------------------------------------------------------


def _positions(fens: list[str], moves: list[str], notes: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "book_title": "Book",
        "chapter_title": "Chapter",
        "line_name": "Line",
        "color": "white",
        "positions": [
            {"ply": i, "fen": fen, "move": moves[i] if i < len(moves) else None, "annotation": notes.get(i)}
            for i, fen in enumerate(fens)
        ],
    }


def _note(text: str, **kw: Any) -> dict[str, Any]:
    return {"text": text, "source": "course", "author": None, "book_title": None, "from_chapter": None, **kw}


def test_adjacent_notes_each_belong_to_the_move_that_leaves_their_position() -> None:
    line = _positions(FENS, MAIN, {0: _note("root, about e4"), 1: _note("about e5")})
    first = ai.line_context(line, 1, "")
    assert first is not None
    assert (first["move"], first["fen_before"], first["fen_after"]) == ("1. e4", FENS[0], FENS[1])
    assert first["direct_note"] == '"root, about e4" (the author)'
    second = ai.line_context(line, 2, "")
    assert second is not None and second["move"] == "1... e5"
    assert second["direct_note"] == '"about e5" (the author)'  # never the previous move's note
    assert (second["fen_before"], second["fen_after"]) == (FENS[1], FENS[2])
    assert second["sticky_note"] == second["direct_note"] and second["sticky_about"] == "1... e5"
    assert ai.line_context(line, 3, "")["direct_note"] == ""  # type: ignore[index]
    table = first["line_table"].splitlines()
    assert table[:6] == [
        f"position 0: {FENS[0]}",
        "   move: 1. e4",
        '   note on 1. e4: "root, about e4" (the author)',
        f"position 1: {FENS[1]}",
        "   move: 1... e5",
        '   note on 1... e5: "about e5" (the author)',
    ]
    assert first["moves_numbered"] == "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"


def test_a_note_on_the_final_position_is_about_no_move() -> None:
    line = _positions(FENS, MAIN, {6: _note("the plan from here")})
    ctx = ai.line_context(line, 6, "")
    assert ctx is not None and ctx["move"] == "3... Bc5" and ctx["direct_note"] == ""
    assert ctx["line_table"].splitlines()[-2:] == [
        f"position 6: {FENS[6]} (final position)",
        '   note on the final position: "the plan from here" (the author)',
    ]
    assert ctx["sticky_about"] == "the final position"  # the lead-in: no note before ply 6


def test_the_note_on_screen_is_carried_forward_or_leads_in_as_the_walkthrough_shows_it() -> None:
    line = _positions(FENS, MAIN, {3: _note("about Nc6")})
    lead_in = ai.line_context(line, 2, "")
    assert lead_in is not None and (lead_in["sticky_about"], lead_in["direct_note"]) == ("2... Nc6", "")
    carried = ai.line_context(line, 6, "")
    assert carried is not None and carried["sticky_about"] == "2... Nc6" and carried["move"] == "3... Bc5"
    on_it = ai.line_context(line, 4, "")
    assert on_it is not None and on_it["direct_note"] == on_it["sticky_note"] == '"about Nc6" (the author)'
    from core import prompts

    template = Settings().ai_line_prompt.text
    assert "was written about 2... Nc6" in prompts.render(template, carried)
    assert "was written about" not in prompts.render(template, on_it)  # the same note is said once


def test_numbering_follows_the_first_position_and_moves_outside_the_line_are_refused() -> None:
    fens = ["8/8/8/4k3/8/8/4K3/8 b - - 0 7", "x", "y"]
    line = _positions(fens, ["Kd5", "Kd3"], {})
    assert ai.line_context(line, 1, "")["move"] == "7... Kd5"  # type: ignore[index]
    assert ai.line_context(line, 2, "")["move"] == "8. Kd3"  # type: ignore[index]
    assert ai.line_context(line, 1, "")["moves_numbered"] == "7... Kd5 8. Kd3"  # type: ignore[index]
    assert ai.line_context(line, 0, "") is None and ai.line_context(line, 3, "") is None


def test_notes_say_who_wrote_them_and_lose_their_markup() -> None:
    line = _positions(
        FENS,
        MAIN,
        {
            0: _note(
                "Play @@SANStart@@Nf3@@SANEnd@@ @@StartBracket@@ or d4 @@EndBracket@@", author="A", book_title="B"
            ),
            1: {"text": "mine", "source": "manual", "author": None, "book_title": None, "from_chapter": None},
            2: _note("borrowed", from_chapter="Other"),
        },
    )
    ctx = ai.line_context(line, 1, "  why?  ")
    assert ctx is not None and ctx["direct_note"] == '"Play Nf3 (or d4)" (the author, A, B)'
    assert ai.line_context(line, 2, "")["direct_note"] == '"mine" (the player\'s own note)'  # type: ignore[index]
    assert ai.line_context(line, 3, "")["direct_note"] == '"borrowed" (the author; from the chapter Other)'  # type: ignore[index]


# --- thinking parameters (no database) -------------------------------------------------------


def _as_stored(key: str) -> AiPrompt:
    """The blunder prompt as the settings row held it before the Claude buttons moved to Opus."""
    old: dict[str, dict[str, Any]] = {
        "a": {"model": "claude-sonnet-5", "thinking_enabled": False, "max_tokens": 512},
        "b": {"model": "claude-sonnet-4-6", "thinking_enabled": False, "max_tokens": 512, "temperature": 0.3},
        "c": {},
    }
    return Settings().ai_prompts[key].model_copy(update=old[key])


def test_the_stored_blunder_prompts_and_sonnet5_off_keep_their_hashes() -> None:
    for key in ("a", "b", "c"):
        assert ai.prompt_hash(_as_stored(key)) == STORED_PROMPT_HASHES[key], key
    sonnet_off = AiPrompt(model="claude-sonnet-5", text="x")
    assert ai.prompt_hash(sonnet_off) == SONNET5_OFF_HASH
    assert ai.effective_params(sonnet_off)["thinking"] == {"type": "disabled"}


def test_an_adaptive_model_with_thinking_on_is_sent_adaptive_thinking_and_no_budget() -> None:
    for model in ("claude-opus-5-5", "claude-sonnet-5"):
        prompt = AiPrompt(
            model=model, text="x", thinking_enabled=True, thinking_budget_tokens=4000, temperature=0.3, prefill="p"
        )
        eff = ai.effective_params(prompt)
        assert eff["thinking"] == {"type": "adaptive"} and eff["thinking_mode"] == "adaptive"
        body = ai.request_body(model, "hi", eff)
        assert body["thinking"] == {"type": "adaptive"} and body["max_tokens"] == 512
        assert "temperature" not in body and len(body["messages"]) == 1
        # The budget is not sent, so it is not in the hash; turning thinking on is.
        assert ai.prompt_hash(prompt) == ai.prompt_hash(prompt.model_copy(update={"thinking_budget_tokens": 9000}))
    assert ai.prompt_hash(AiPrompt(model="claude-sonnet-5", text="x", thinking_enabled=True)) != SONNET5_OFF_HASH
    budget = ai.effective_params(AiPrompt(model="claude-haiku-4-5", text="x", thinking_enabled=True))
    assert budget["thinking"]["type"] == "enabled" and "thinking_mode" not in budget  # budget models unchanged


def test_a_model_that_always_thinks_is_refused_with_thinking_off_before_anything_else() -> None:
    with pytest.raises(ai.ExplainError) as err:
        ai.effective_params(AiPrompt(model="claude-opus-5-5", text="x", thinking_enabled=False))
    assert err.value.status == 422 and err.value.detail == "claude-opus-5-5 always thinks; turn thinking on"
    with pytest.raises(ai.ExplainError):
        ai.prompt_hash(AiPrompt(model="claude-opus-5-5", text="x"))


def test_the_claude_blunder_buttons_default_to_opus_thinking_and_openai_stays() -> None:
    defaults = Settings().ai_prompts
    for key in ("a", "b"):
        eff = ai.effective_params(defaults[key])
        assert defaults[key].model == "claude-opus-5-5" and eff["thinking"] == {"type": "adaptive"}
        assert eff["max_tokens"] == 8192 and eff["temperature"] is None
    assert defaults["c"] == _as_stored("c") and defaults["c"].model == "gpt-4o"
    assert settings.save_errors(Settings()) == []


def test_the_default_line_prompt_is_opus_thinking_with_room_for_it() -> None:
    prompt = Settings().ai_line_prompt
    assert prompt.model == "claude-opus-5-5" and prompt.thinking_enabled and prompt.max_tokens == 16000
    assert ai.effective_params(prompt)["thinking"] == {"type": "adaptive"}


def test_save_errors_name_every_prompt_that_cannot_run_and_load_still_accepts_it() -> None:
    values = Settings()
    assert settings.save_errors(values) == []
    values.ai_line_prompt.thinking_enabled = False
    values.ai_prompts["b"] = AiPrompt(model="claude-opus-5-5", text="x")
    values.ai_prompts["c"] = AiPrompt(model="claude-sonnet-5", text="x")  # sonnet 5 may switch it off
    assert settings.save_errors(values) == [
        "ai_prompts.b: claude-opus-5-5 always thinks; turn thinking on",
        "ai_line_prompt: claude-opus-5-5 always thinks; turn thinking on",
    ]
    assert Settings.model_validate(values.model_dump(mode="json")) == values  # loading never refuses it


# --- explain_line ----------------------------------------------------------------------------


@pytest.fixture()
def tx(clean: psycopg.Connection[DictRow], fresh_db_url: str) -> Tx:
    h.player(clean)
    h.book(clean, 1, "Italian", "white")
    h.chapter(clean, 1, 1, "Giuoco")
    h.line(clean, 1, 1, "Main", MAIN)
    ann.upsert(clean, [{"fen": FENS[0], "text": "root note", "author": "A"}], 1, source="course")
    ann.upsert(clean, [{"fen": FENS[3], "text": "Nc6 defends e5"}], 1, source="course")
    clean.execute("INSERT INTO players (id, chesscom_username) VALUES (2, 'someone')")
    clean.execute("INSERT INTO books (id, player_id, title, color) VALUES (9, 2, 'Theirs', 'white')")
    h.chapter(clean, 9, 9, "Theirs")
    h.line(clean, 9, 9, "Theirs", MAIN)
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
    text: str = "Because it guards e5.", status: int = 200, seen: list[httpx.Request] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"type": "overloaded_error"}})
        content = [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": text}]
        return httpx.Response(200, json={"content": content, "usage": {"input_tokens": 3000, "output_tokens": 900}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _calls(tx: Tx) -> list[dict[str, Any]]:
    with tx() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ai_calls ORDER BY id").fetchall()]


def test_a_line_explanation_is_cached_by_its_notes_and_its_question(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    seen: list[httpx.Request] = []
    first = ai.explain_line(tx, 1, 4, "", client=_client(seen=seen))
    assert first == {
        "explanation": "Because it guards e5.",
        "cached": False,
        "model": "claude-opus-5-5",
        "prompt_label": "Explain this move (line)",
    }
    sent = json.loads(seen[0].content)
    assert sent["model"] == "claude-opus-5-5" and sent["thinking"] == {"type": "adaptive"}
    assert sent["max_tokens"] == 16000 and "temperature" not in sent
    prompt_text = sent["messages"][0]["content"]
    assert (
        "The move I am asking about: 2... Nc6" in prompt_text
        and 'The note on this move: "Nc6 defends e5"' in prompt_text
    )
    assert ai.explain_line(tx, 1, 4, "   ", client=_client(status=500))["cached"] is True  # blank = empty
    assert [c["prompt_key"] for c in _calls(tx)] == ["line"] and _calls(tx)[0]["output_tokens"] == 900
    # A different question is a different answer.
    assert ai.explain_line(tx, 1, 4, "why not d6?", client=_client("Asked."))["cached"] is False
    # So is the same question after a note on the line changed, anywhere on the line.
    with tx() as conn:
        ann.upsert(conn, [{"fen": FENS[5], "text": "new note"}], 1, source="manual")
    assert ai.explain_line(tx, 1, 4, "", client=_client("Again."))["explanation"] == "Again."
    assert len(_calls(tx)) == 3


def test_a_line_explanation_shares_the_caps_and_gives_a_failed_slot_back(
    tx: Tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx, ai_explain_max_per_hour=1)
    with pytest.raises(ai.ExplainError) as err:
        ai.explain_line(tx, 1, 2, "", client=_client(status=529))
    assert err.value.status == 502 and _calls(tx) == []
    with tx() as conn:  # one blunder explanation this hour fills the shared cap
        conn.execute("INSERT INTO ai_calls (prompt_key, model) VALUES ('a', 'claude-haiku-4-5')")
    with pytest.raises(ai.ExplainError) as err:
        ai.explain_line(tx, 1, 2, "", client=_client())
    assert err.value.status == 429 and isinstance(err.value.detail, dict) and err.value.detail["window"] == "hourly"


def test_refusals_that_never_reach_a_provider(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    seen: list[httpx.Request] = []
    for line_id, ply, question, status in (
        (1, 0, "", 404),  # no move arrives at the start
        (1, 7, "", 404),  # past the end
        (9, 2, "", 404),  # another player's line
        (99, 2, "", 404),
        (1, 2, "x" * 501, 422),
    ):
        for dry in (False, True):
            with pytest.raises(ai.ExplainError) as err:
                ai.explain_line(tx, line_id, ply, question, dry_run=dry, client=_client(seen=seen))
            assert err.value.status == status, (line_id, ply)
    assert ai.explain_line(tx, 1, 2, "x" * 500 + "  ", dry_run=True)["dry_run"] is True
    assert seen == [] and _calls(tx) == []


def test_a_stored_always_thinking_prompt_with_thinking_off_costs_nothing(
    tx: Tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row written before the save-time check (or by hand) still loads, and the refusal comes
    before the cache, the claim and the provider, in both modes."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx, ai_line_prompt=_line_prompt(thinking_enabled=False))
    seen: list[httpx.Request] = []
    for dry in (False, True):
        with pytest.raises(ai.ExplainError) as err:
            ai.explain_line(tx, 1, 2, "", dry_run=dry, client=_client(seen=seen))
        assert err.value.status == 422 and err.value.detail == "claude-opus-5-5 always thinks; turn thinking on"
    assert seen == [] and _calls(tx) == []
    with tx() as conn:
        assert settings.load(conn).ai_line_prompt.thinking_enabled is False
        assert conn.execute("SELECT count(*) AS n FROM ai_explanation_cache").fetchone() == {"n": 0}


def test_the_dry_run_body_is_the_body_that_is_sent(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    dry = ai.explain_line(tx, 1, 6, "why?", dry_run=True)
    seen: list[httpx.Request] = []
    ai.explain_line(tx, 1, 6, "why?", client=_client(seen=seen))
    assert json.loads(seen[0].content) == dry["request_body"]
    assert dry["thinking_mode"] == "adaptive" and "My question: why?" in dry["rendered_prompt"]
    assert "Nc6 defends e5" in dry["rendered_prompt"] and "was written about 2... Nc6" in dry["rendered_prompt"]


# --- the routes ------------------------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, tx: Tx) -> TestClient:
    from api.main import create_app

    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    return c


def test_the_explain_route(client: TestClient) -> None:
    r = client.post("/repertoire/lines/1/explain", json={"ply": 2, "dry_run": True})
    assert r.status_code == 200 and r.json()["request_body"]["model"] == "claude-opus-5-5"
    assert client.post("/repertoire/lines/1/explain", json={"ply": 0}).status_code == 404
    assert client.post("/repertoire/lines/9/explain", json={"ply": 2}).status_code == 404
    assert client.post("/repertoire/lines/1/explain", json={"ply": 2, "question": "x" * 501}).status_code == 422
    assert client.post("/repertoire/lines/1/explain", json={"ply": 2, "question": "x" * 2001}).status_code == 422


def test_saving_a_prompt_that_cannot_run_is_refused_and_the_row_is_unchanged(client: TestClient) -> None:
    before = client.get("/settings").json()
    for field in ("ai_line_prompt", "ai_prompts"):
        body = json.loads(json.dumps(before))
        if field == "ai_line_prompt":
            body["ai_line_prompt"]["thinking_enabled"] = False
        else:
            body["ai_prompts"]["b"]["thinking_enabled"] = False
        r = client.put("/settings", json=body)
        assert r.status_code == 422 and "claude-opus-5-5 always thinks; turn thinking on" in r.json()["detail"]
        assert client.get("/settings").json() == before
    edited = json.loads(json.dumps(before))
    edited["ai_line_prompt"]["max_tokens"] = 20000
    assert client.put("/settings", json=edited).json()["ai_line_prompt"]["max_tokens"] == 20000


def test_a_stored_bad_prompt_does_not_take_the_api_down(client: TestClient, tx: Tx) -> None:
    _configure(tx, ai_line_prompt=_line_prompt(thinking_enabled=False))
    assert client.get("/settings").status_code == 200
    assert client.get("/blunders/prompts").status_code == 200
    r = client.post("/repertoire/lines/1/explain", json={"ply": 2})
    assert r.status_code == 422 and r.json()["detail"] == "claude-opus-5-5 always thinks; turn thinking on"


@pytest.mark.parametrize(
    ("url_part", "payload"),
    [
        ("anthropic", {"content": [{"type": "text", "text": "Because the kn"}], "stop_reason": "max_tokens"}),
        ("openai", {"choices": [{"message": {"content": "Because the kn"}, "finish_reason": "length"}]}),
    ],
)
def test_a_reply_cut_off_at_max_tokens_is_refused(
    url_part: str, payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    model = "claude-opus-5-5" if url_part == "anthropic" else "gpt-4o"
    eff = ai.effective_params(AiPrompt(model=model, text="x", thinking_enabled=True))
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    with pytest.raises(ai.ExplainError) as err:
        ai.call_provider(model, "hi", eff, client)
    assert err.value.status == 502 and "cut off" in str(err.value.detail)


def test_a_cut_off_line_answer_is_neither_cached_nor_counted(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _configure(tx)
    cut = {"content": [{"type": "text", "text": "Because"}], "stop_reason": "max_tokens"}
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=cut)))
    with pytest.raises(ai.ExplainError):
        ai.explain_line(tx, 1, 2, "", client=client)
    assert _calls(tx) == []
    with tx() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ai_explanation_cache").fetchone() == {"n": 0}


def test_one_bad_blunder_prompt_leaves_the_others_working(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    prompts = dict(Settings().ai_prompts)
    prompts["b"] = prompts["b"].model_copy(update={"thinking_enabled": False})  # stored before the check
    _configure(tx, ai_prompts=prompts)
    with tx() as conn:
        h.game(conn, 1, MAIN, color="black")
        conn.execute(
            "INSERT INTO blunders (player_id, chess_game_id, ply, fen, move_played, best_move, classification)"
            " VALUES (1, 1, 3, %s, 'Nc6', 'd6', 'mistake')",
            (FENS[3],),
        )
    assert ai.explain(tx, 1, 3, "a", client=_client("Fine."))["explanation"] == "Fine."
    with pytest.raises(ai.ExplainError) as err:
        ai.explain(tx, 1, 3, "b", client=_client())
    assert err.value.status == 422 and [c["prompt_key"] for c in _calls(tx)] == ["a"]
    with tx() as conn:
        assert ai.prompt_labels(settings.load(conn))[0]["key"] == "a"
