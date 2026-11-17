"""Asking about a repertoire chapter (core/repertoire/chapter_idea.py): the render, the move tags
and their links, the frozen conversation, the provider's count before every claim, staleness on
every read and after every call, and the routes. No network: both provider endpoints go through an
httpx mock transport that records what it was sent."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import chess
import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from core import ai, schema, settings
from core.constants import CHAPTER_IDEA_MAX_TURNS
from core.prompts import CHAPTER_DEFAULT_QUESTION
from core.repertoire import annotations as ann
from core.repertoire import books, chapter_idea, importing
from core.settings import AiPrompt, Settings
from tests import repertoire_helpers as h
from tests.conftest import _admin_url, with_dbname

Tx = Callable[[], Any]
FIXTURES = Path(__file__).parent / "fixtures"

MAIN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6"]
TWO_KNIGHTS = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "d3"]
QG = ["d4", "d5", "c4"]
INDIAN = ["d4", "Nf6", "c4"]


def _lines(*moves: list[str], start: str = chess.STARTING_FEN) -> chapter_idea.Lines:
    return {
        f"L{i}": {"line_id": 100 + i, "name": f"line {i}", "start_fen": start, "moves": m}
        for i, m in enumerate(moves, start=1)
    }


LINES = _lines(MAIN, TWO_KNIGHTS, QG, INDIAN)


def _fen(moves: list[str], start: str = chess.STARTING_FEN) -> str:
    b = chess.Board(start)
    for m in moves:
        b.push_san(m)
    return b.fen()


# --- numbering and tags (no database) -----------------------------------------------------------


def test_numbering_follows_the_line_s_first_board() -> None:
    black_at_9 = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 5 9"
    assert [chapter_idea.label(chess.STARTING_FEN, k, "x") for k in range(3)] == ["1.x", "1...x", "2.x"]
    assert [chapter_idea.label(black_at_9, k, "x") for k in range(3)] == ["9...x", "10.x", "10...x"]
    assert chapter_idea.ply_of(black_at_9, 10, True) == 1 and chapter_idea.ply_of(black_at_9, 9, False) == 0


TAG_CASES: list[tuple[str, str]] = [
    ("L1 1.e4", "valid"),
    ("L1 3.Bc4", "valid"),
    ("L1 3...Bc5", "valid"),
    ("L1 3... Bc5", "valid"),  # a space after the dots
    ("L1 3.Bc4!?", "valid"),  # annotation glyphs
    ("L1 3...Nf6", "wrong_move"),  # legal, but not what L1 plays
    ("L1 3.Qh5", "invalid"),  # illegal there
    ("L9 1.e4", "invalid"),  # no such line
    ("L1 20.e4", "invalid"),  # past the line's end
    ("L1 1.--", "invalid"),  # the null move
    ("L1 1.0000", "invalid"),
    ("L1 3...Bc5 | 4.b4 Bxb4", "valid"),  # a side line, legal in turn
    ("L1 3...Bc5 | 4.Qxf7", "invalid"),  # illegal after the bar
    ("L1 3...Bc5 | 5.b4", "misnumbered"),  # the number disagrees with the board, ignored
    ("L1 3...Bc5 | 4.b4 Bxb4 Z0", "invalid"),  # the null move after the bar
    ("e4", "invalid"),  # no anchor
]


@pytest.mark.parametrize(("inner", "verdict"), TAG_CASES, ids=[c[0] for c in TAG_CASES])
def test_a_tag_is_checked_against_the_line_it_names(inner: str, verdict: str) -> None:
    assert chapter_idea.check_tag(inner, LINES)[0] == verdict


def test_a_tag_links_the_board_after_its_last_move() -> None:
    _, _, link = chapter_idea.resolve_tag("L1 3.Bc4", LINES)
    assert link == {
        "label": "3.Bc4",
        "line": "L1",
        "line_id": 101,
        "ply": 5,
        "anchor": "3.Bc4",
        "anchor_fen": _fen(MAIN[:5]),
        "branch": [],
        "fen": _fen(MAIN[:5]),
    }
    _, _, side = chapter_idea.resolve_tag("L1 3...Bc5 | 4.b4 Bxb4", LINES)
    assert side is not None and side["ply"] == 6 and side["anchor"] == "3…Bc5" and side["label"] == "4…Bxb4"
    assert side["branch"] == ["b4", "Bxb4"] and side["fen"] == _fen([*MAIN[:6], "b4", "Bxb4"])
    assert side["anchor_fen"] == _fen(MAIN[:6])
    _, _, off = chapter_idea.resolve_tag("L1 3...Bc5 | 5.b4", LINES)
    assert off is not None and off["label"] == "4.b4"  # linked; the label is numbered from the board


def test_an_untagged_numbered_move_is_linked_only_when_every_line_reaches_one_board() -> None:
    both = chapter_idea.resolvable("2.Nf3", LINES)  # L1 and L2 reach the same board
    assert both is not None and both["line"] == "L1" and both["ply"] == 3
    only = chapter_idea.resolvable("3...Nf6", LINES)
    assert only is not None and only["line"] == "L2"
    assert chapter_idea.resolvable("2.c4", LINES) is None  # L3 and L4 reach two boards
    assert chapter_idea.resolvable("Nf3", LINES) is None  # unnumbered
    assert chapter_idea.resolvable("5.Bd2", LINES) is None  # no line plays it


def test_an_answer_becomes_text_and_links_and_a_wrong_tag_is_never_a_link() -> None:
    answer = "**The idea.** After [[L1 3.Bc4]] Black can [[L1 3...Nf6]] or 2.Nf3 again, never 2.c4.\n\nThat's it."
    segments = chapter_idea.resolve(answer, LINES)
    assert [("text" in s) for s in segments] == [True, False, True, False, True]
    assert segments[0] == {"text": "**The idea.** After "}
    assert segments[1]["move"]["label"] == "3.Bc4"
    assert segments[2] == {"text": " Black can 3...Nf6 or "}  # the wrong move shows as text
    assert segments[3]["move"]["label"] == "2.Nf3"  # the fallback
    assert segments[4] == {"text": " again, never 2.c4.\n\nThat's it."}
    report = chapter_idea.tag_report(answer, LINES)
    assert report["tags"] == 2 and report["valid"] == 1 and report["wrong_move"] == 1
    assert report["untagged"] == 2 and report["server_links"] == 1 and report["unlinked_share"] == 0.25


# --- the frozen parameters and the two bodies (no database) -------------------------------------


def _prompt(**kw: Any) -> AiPrompt:
    return AiPrompt(**{**Settings().ai_chapter_prompt.model_dump(), **kw})


THINKING_OFF = {"model": "claude-sonnet-5", "thinking_enabled": False, "temperature": 0.7}


def test_the_default_chapter_prompt_freezes_every_key() -> None:
    p = ai.ConversationParams.of(_prompt())
    stored = p.as_stored()
    assert stored == {
        "model": "claude-opus-5-5",
        "provider": "anthropic",
        "system_prompt": _prompt().system_prompt.strip(),
        "temperature": None,
        "thinking_enabled": True,
        "thinking_budget_tokens": 0,
        "thinking_mode": "adaptive",
        "prefill": "",
        "max_tokens": 16000,
        "thinking": {"type": "adaptive"},
    }
    assert ai.ConversationParams.stored(json.loads(json.dumps(stored))) == p
    assert "the player's own move" in stored["system_prompt"]  # the rule against advising the opponent's move
    # The rules for moves the answers left untagged: a sequence retold move by move, a heading, a plan.
    assert "give every move of it its own tag" in stored["system_prompt"]
    assert "is the tag itself, written once" in stored["system_prompt"]
    assert "read your answer once more for move notation outside" in stored["system_prompt"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d | {"prefill": "Sure"},
        lambda d: {k: v for k, v in d.items() if k != "thinking_mode"},
        lambda d: d | {"stop": "x"},
        lambda d: d | {"max_tokens": "16000"},
        lambda d: d | {"provider": "openai"},
        lambda d: d | {"thinking": {"type": "enabled"}},
    ],
    ids=["prefill", "missing-key", "extra-key", "wrong-type", "provider", "thinking-shape"],
)
def test_a_stored_object_that_does_not_validate_is_refused(mutate: Callable[[dict[str, Any]], Any]) -> None:
    stored = ai.ConversationParams.of(_prompt()).as_stored()
    with pytest.raises(ai.ExplainError) as err:
        ai.ConversationParams.stored(mutate(stored))
    assert err.value.status == 422 and err.value.detail == "conversation_params_invalid"


def test_the_body_and_its_count_projection() -> None:
    p = ai.ConversationParams.of(_prompt(**THINKING_OFF))
    turns = [{"seq": 0, "question": "q0", "answer": "a0"}, {"seq": 1, "question": "q1", "answer": "a1"}]
    body = ai.conversation_body(p, "THE CHAPTER", turns, "q2")
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "THE CHAPTER", "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    assert body["thinking"] == {"type": "disabled"} and body["temperature"] == 0.7 and body["max_tokens"] == 16000
    counted = ai.count_body(body)
    assert set(counted) == set(ai.COUNT_INPUT_FIELDS) & set(body)
    assert all(counted[k] == body[k] for k in counted)
    assert "max_tokens" not in counted and "temperature" not in counted
    # Every key the Messages body can carry is either counted or named as left out.
    assert set(body) <= set(ai.COUNT_INPUT_FIELDS) | set(ai.COUNT_OMITTED_FIELDS)


def test_saving_a_chapter_prompt_that_cannot_hold_a_conversation_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def errors(**kw: Any) -> list[str]:
        return settings.save_errors(Settings(ai_chapter_prompt=_prompt(**kw)))

    assert errors() == []
    assert errors(**THINKING_OFF) == []
    assert errors(**THINKING_OFF, prefill="Sure:") == ["ai_chapter_prompt: the chapter prompt cannot use a prefill"]
    assert errors(model="gpt-5")[0].startswith("ai_chapter_prompt: the chapter prompt needs one of these")
    assert errors(model="claude-unknown-9")[0].startswith("ai_chapter_prompt: the chapter prompt needs one of these")
    monkeypatch.setattr(settings, "AI_CONTEXT_WINDOW_TOKENS", {"claude-opus-5-5": 130_000})
    assert errors(max_tokens=10_000) == []
    assert errors(max_tokens=10_001) == [
        "ai_chapter_prompt: claude-opus-5-5's context window cannot hold the largest request and this max tokens"
    ]


# --- the render --------------------------------------------------------------------------------

TABIYA = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


@pytest.fixture()
def chapter(clean: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """Chapter 1 (white): lines 'b', 'B' and 'a', one switched off, one alternative; notes on a
    shared board (the same text twice), a player's own note, and a note from the other chapter of
    the book on a board L-a passes through. Chapter 2 starts at a set position. Chapter 3 has no
    active line. Chapter 9 is another player's."""
    h.player(clean)
    h.book(clean, 1, "Italian", "white")
    h.chapter(clean, 1, 1, "Giuoco")
    h.chapter(clean, 2, 1, "From the tabiya")
    h.chapter(clean, 3, 1, "Switched off")
    h.line(clean, 1, 1, "b", MAIN)
    h.line(clean, 2, 1, "B", TWO_KNIGHTS)
    h.line(clean, 3, 1, "a", MAIN[:6] + ["d3"])
    h.line(clean, 4, 1, "off", MAIN[:4] + ["d4"], active=False)
    clean.execute("UPDATE repertoire_lines SET is_alternative = TRUE WHERE id = 3")
    fens = h.spine(None, MAIN)
    h.line(clean, 5, 2, "tabiya", ["Bc4", "Bc5", "c3"], fens=importing.spine(TABIYA, ["Bc4", "Bc5", "c3"]))
    h.line(clean, 6, 3, "gone", MAIN, active=False)
    ann.upsert(
        clean, [{"fen": fens[4], "text": "Bc4 eyes @@SANStart@@f7@@SANEnd@@.", "author": "A"}], 1, source="course"
    )
    ann.upsert(
        clean, [{"fen": fens[4], "text": "Bc4 eyes @@SANStart@@f7@@SANEnd@@.", "author": "A"}], 3, source="course"
    )
    ann.upsert(clean, [{"fen": fens[6], "text": "my plan: c3 and d4"}], 1, source="manual")
    ann.upsert(clean, [{"fen": TABIYA, "text": "the tabiya"}], 5, source="course")
    clean.execute("INSERT INTO players (id, chesscom_username) VALUES (2, 'someone')")
    clean.execute("INSERT INTO books (id, player_id, title, color) VALUES (9, 2, 'Theirs', 'white')")
    h.chapter(clean, 9, 9, "Theirs")
    h.line(clean, 9, 9, "Theirs", MAIN)
    clean.commit()
    return clean


def test_the_render_labels_numbers_and_notes(chapter: psycopg.Connection[DictRow]) -> None:
    r = chapter_idea.render_chapter(chapter, 1)
    assert r is not None and r.context is not None
    assert [(t, v["line_id"]) for t, v in r.lines.items()] == [("L1", 2), ("L2", 3), ("L3", 1)]  # "B" < "a" < "b" in C
    assert r.context["lines_block"].splitlines() == [
        "- L1 — B: 1.e4 1...e5 2.Nf3 2...Nc6 3.Bc4 3...Nf6 4.d3",
        "- L2 — a (alternative): 1.e4 1...e5 2.Nf3 2...Nc6 3.Bc4 3...Bc5 4.d3",
        "- L3 — b: 1.e4 1...e5 2.Nf3 2...Nc6 3.Bc4 3...Bc5 4.c3 4...Nf6",
    ]
    assert r.context["notes_block"].splitlines() == [
        "- on 3.Bc4 (L1; the author): Bc4 eyes f7.",  # once per (board, text): L2's and L3's copies dropped
        "- on 4.c3 (L3; the player's own note): my plan: c3 and d4",
    ]
    assert r.context["line_count"] == 3 and r.context["note_count"] == 2
    assert r.context["book_title"] == "Italian" and r.context["color"] == "white"
    tabiya = chapter_idea.render_chapter(chapter, 2)
    assert tabiya is not None and tabiya.context is not None
    assert tabiya.context["lines_block"] == "- L1 — tabiya: 3.Bc4 3...Bc5 4.c3"
    assert tabiya.context["notes_block"] == "- on 3.Bc4 (L1; the author): the tabiya"


def test_the_two_query_projection_is_the_walk_through_s(chapter: psycopg.Connection[DictRow]) -> None:
    """Each line's notes as `render_chapter` projects them equal `line_with_notes`, line by line."""
    r = chapter_idea.render_chapter(chapter, 1)
    assert r is not None and r.context is not None
    rebuilt: list[str] = []
    seen: set[tuple[str, str]] = set()
    for tag, v in r.lines.items():
        full = ann.line_with_notes(chapter, int(v["line_id"]))
        assert full is not None
        positions = full["positions"]
        assert [p["move"] for p in positions[:-1]] == v["moves"] and positions[0]["fen"] == v["start_fen"]
        for k, pos in enumerate(positions):
            note = pos["annotation"]
            if not note:
                continue
            prose = ai.note_prose(str(note["text"]))
            if (str(pos["fen"]).split(" ")[0], prose) in seen:
                continue
            seen.add((str(pos["fen"]).split(" ")[0], prose))
            who = "the player's own note" if note["source"] == "manual" else "the author"
            rebuilt.append(f"- on {chapter_idea.label(v['start_fen'], k, v['moves'][k])} ({tag}; {who}): {prose}")
    assert r.context["notes_block"].splitlines() == rebuilt


def test_a_board_s_winning_note_is_the_one_whose_text_is_rendered(chapter: psycopg.Connection[DictRow]) -> None:
    """Two notes on one board with different texts: each line renders the text of the note that wins
    for it, the board's notes being chosen before any text is read."""
    fens = h.spine(None, MAIN)
    ann.upsert(chapter, [{"fen": fens[4], "text": "in the Two Knights, Bc4 invites Nf6"}], 2, source="manual")
    chapter.commit()
    r = chapter_idea.render_chapter(chapter, 1)
    assert r is not None and r.context is not None
    assert r.context["notes_block"].splitlines()[:2] == [
        "- on 3.Bc4 (L1; the player's own note): in the Two Knights, Bc4 invites Nf6",
        "- on 3.Bc4 (L2; the author): Bc4 eyes f7.",
    ]


def test_an_emptied_chapter_renders_the_empty_context(chapter: psycopg.Connection[DictRow]) -> None:
    r = chapter_idea.render_chapter(chapter, 3)
    assert r is not None and r.context is None and r.lines == {}
    assert r.context_hash == ai.context_hash({"empty": True})
    assert chapter_idea.render_chapter(chapter, 9) is None  # another player's
    assert chapter_idea.render_chapter(chapter, 99) is None


# --- a fake provider ---------------------------------------------------------------------------


class Fake:
    """Both Anthropic endpoints. `count` decides the token count from the count request; `answers`
    are returned in turn; `on_call` runs while the Messages call is "thinking"."""

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = answers or ["Play [[L1 3.Bc4]] and then 2.Nf3."]
        self.count: Callable[[dict[str, Any]], int] = lambda body: 30_000
        self.count_status = 200
        self.call_status = 200
        self.counts: list[httpx.Request] = []
        self.calls: list[httpx.Request] = []
        self.on_call: Callable[[], None] | None = None
        self.usage: dict[str, Any] = {
            "input_tokens": 120,
            "cache_creation_input_tokens": 29_000,
            "cache_read_input_tokens": 0,
            "output_tokens": 4_000,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/count_tokens"):
            self.counts.append(request)
            if self.count_status != 200:
                return httpx.Response(self.count_status, json={"error": {"type": "rate_limit_error"}})
            return httpx.Response(200, json={"input_tokens": self.count(body)})
        self.calls.append(request)
        if self.on_call is not None:
            self.on_call()
        if self.call_status != 200:
            return httpx.Response(self.call_status, json={"error": {"type": "overloaded_error"}})
        text = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        content = [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": text}]
        return httpx.Response(200, json={"content": content, "stop_reason": "end_turn", "usage": self.usage})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def body(self, i: int = -1) -> dict[str, Any]:
        return json.loads(self.calls[i].content)


@pytest.fixture()
def tx(chapter: psycopg.Connection[DictRow], fresh_db_url: str, monkeypatch: pytest.MonkeyPatch) -> Tx:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    @contextmanager
    def open_tx() -> Generator[psycopg.Connection[DictRow]]:
        with psycopg.Connection[DictRow].connect(fresh_db_url, row_factory=dict_row) as conn:
            yield conn

    return open_tx


def _calls(tx: Tx) -> list[dict[str, Any]]:
    with tx() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ai_calls ORDER BY id").fetchall()]


def _configure(tx: Tx, **kw: Any) -> None:
    with tx() as conn:
        settings.save(conn, Settings(**kw))


def _read(tx: Tx, chapter_id: int = 1) -> dict[str, Any]:
    with tx() as conn:
        out = chapter_idea.read_chapter(conn, chapter_id)
    assert out is not None
    return out


def _conv(tx: Tx, conversation_id: int) -> dict[str, Any]:
    with tx() as conn:
        out = chapter_idea.read_conversation(conn, conversation_id)
    assert out is not None
    return out


def _refused(fn: Callable[[], Any], status: int, code: str | None = None) -> None:
    with pytest.raises(ai.ExplainError) as err:
        fn()
    assert err.value.status == status, err.value.detail
    if code is not None:
        assert isinstance(err.value.detail, dict) and err.value.detail["code"] == code


# --- asking --------------------------------------------------------------------------------------


def test_a_first_question_is_counted_claimed_sent_resolved_and_stored(tx: Tx) -> None:
    fake = Fake()
    out = chapter_idea.ask(tx, 1, "", client=fake.client())
    assert out["saved"] is True and len(fake.counts) == 1 and len(fake.calls) == 1
    sent = fake.body()
    assert json.loads(fake.counts[0].content) == ai.count_body(sent)
    assert fake.counts[0].headers["x-api-key"] == fake.calls[0].headers["x-api-key"] == "k"
    assert fake.counts[0].headers["anthropic-version"] == fake.calls[0].headers["anthropic-version"]
    assert [m["role"] for m in sent["messages"]] == ["user"]
    block = sent["messages"][0]["content"][0]
    assert block["cache_control"] == {"type": "ephemeral"}
    assert block["text"].startswith("Repertoire: Italian (white).\nChapter: Giuoco — 3 lines, 2 notes.")
    assert block["text"].endswith(f"Question: {CHAPTER_DEFAULT_QUESTION}")
    conv = out["conversation"]
    assert conv["stale"] is False and conv["full"] is False and conv["prompt_current"] is True
    assert conv["blocked"] is None and conv["model"] == "claude-opus-5-5"
    [turn] = conv["turns"]
    assert turn["seq"] == 0 and turn["question"] == CHAPTER_DEFAULT_QUESTION
    assert [("move" in s) for s in turn["segments"]] == [False, True, False, True, False]
    assert turn["segments"][1]["move"]["line_id"] == 2 and turn["segments"][3]["move"]["label"] == "2.Nf3"
    assert turn["cache_write_tokens"] == 29_000 and turn["output_tokens"] == 4_000
    assert conv["lines"]["L3"] == {"line_id": 1, "name": "b", "exists": True}
    assert "rendered_prompt" not in json.dumps(out)
    [call] = _calls(tx)
    assert call["prompt_key"] == "chapter" and call["input_tokens"] == 29_120 and call["output_tokens"] == 4_000


def test_every_question_and_answer_is_read_back_without_a_call(tx: Tx) -> None:
    fake = Fake(["first [[L1 1.e4]]", "second"])
    first = chapter_idea.ask(tx, 1, "what?", client=fake.client())
    cid = first["conversation"]["id"]
    chapter_idea.follow_up(tx, cid, "and then?", client=fake.client())
    calls = len(_calls(tx))
    read: dict[str, Any] = {}
    for _ in range(2):  # a reload, then the conversation opened from the listing
        read = _read(tx)
        assert [t["question"] for t in read["conversation"]["turns"]] == ["what?", "and then?"]
        assert read["conversation"]["turns"][0]["segments"] == first["conversation"]["turns"][0]["segments"]
        assert _conv(tx, cid)["conversation"]["turns"] == read["conversation"]["turns"]
    assert len(fake.calls) == 2 and len(fake.counts) == 2 and len(_calls(tx)) == calls
    assert read["conversations"] == [{"id": cid, "created_at": read["conversation"]["created_at"], "turns": 2}]


def test_a_follow_up_resends_the_stored_chapter_and_every_turn(tx: Tx) -> None:
    fake = Fake(["a0", "a1", "a2"])
    cid = chapter_idea.ask(tx, 1, "q0", client=fake.client())["conversation"]["id"]
    chapter_idea.follow_up(tx, cid, "  q1 ", client=fake.client())
    out = chapter_idea.follow_up(tx, cid, "q2", client=fake.client())
    first, last = fake.body(0), fake.body(2)
    assert last["messages"][0] == first["messages"][0]  # the identical cached block
    assert [(m["role"], m["content"]) for m in last["messages"][1:]] == [
        ("assistant", "a0"),
        ("user", "q1"),
        ("assistant", "a1"),
        ("user", "q2"),
    ]
    assert [t["seq"] for t in out["conversation"]["turns"]] == [0, 1, 2]
    _refused(lambda: chapter_idea.follow_up(tx, cid, "   ", client=fake.client()), 422)
    _refused(lambda: chapter_idea.follow_up(tx, cid, "x" * 501, client=fake.client()), 422)
    _refused(lambda: chapter_idea.follow_up(tx, 999, "q", client=fake.client()), 404)
    assert len(fake.calls) == 3


def test_a_failed_call_gives_its_slot_back_and_the_caps_apply(tx: Tx) -> None:
    fake = Fake()
    fake.call_status = 529
    _refused(lambda: chapter_idea.ask(tx, 1, client=fake.client()), 502)
    assert _calls(tx) == []
    _configure(tx, ai_explain_max_per_hour=1)
    with tx() as conn:
        conn.execute("INSERT INTO ai_calls (prompt_key, model) VALUES ('a', 'claude-opus-5-5')")
    fake.call_status = 200
    _refused(lambda: chapter_idea.ask(tx, 1, client=fake.client()), 429)
    assert len(fake.calls) == 1 and len(_calls(tx)) == 1


def _nothing_sent(tx: Tx, fake: Fake, *, counted: bool = False) -> None:
    assert fake.calls == [] and _calls(tx) == []
    assert (len(fake.counts) > 0) is counted


def test_refusals_before_the_count(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake()
    _refused(lambda: chapter_idea.ask(tx, 3, client=fake.client()), 422, "no_active_lines")
    _refused(lambda: chapter_idea.ask(tx, 9, client=fake.client()), 404)
    _refused(lambda: chapter_idea.ask(tx, 1, "x" * 501, client=fake.client()), 422)
    _nothing_sent(tx, fake)
    chapter_idea.ask(tx, 1, "q", client=fake.client())
    rendered = len(json.loads(fake.counts[0].content)["messages"][0]["content"][0]["text"])
    with tx() as conn:
        conn.execute("DELETE FROM ai_calls")
    fake.counts.clear()
    fake.calls.clear()
    monkeypatch.setattr(chapter_idea, "CHAPTER_IDEA_MAX_PROMPT_CHARS", rendered - 1)
    _refused(lambda: chapter_idea.ask(tx, 1, "q", client=fake.client()), 422, "chapter_too_large")
    _nothing_sent(tx, fake)


def test_the_count_bounds_a_first_question_and_a_follow_up(tx: Tx) -> None:
    fake = Fake()
    fake.count = lambda body: 116_667  # 116,667 x 1.02 + 1,000 = 120,000.34 > 120,000
    _refused(lambda: chapter_idea.ask(tx, 1, client=fake.client()), 422, "chapter_too_large")
    _nothing_sent(tx, fake, counted=True)
    fake.count = lambda body: 116_666  # 119,999.32: sent
    cid = chapter_idea.ask(tx, 1, client=fake.client())["conversation"]["id"]
    assert len(fake.calls) == 1
    fake.count = lambda body: 116_667
    _refused(lambda: chapter_idea.follow_up(tx, cid, "more?", client=fake.client()), 422, "conversation_full")
    assert len(fake.calls) == 1 and len(_calls(tx)) == 1
    read = _conv(tx, cid)["conversation"]
    assert read["full"] is True and read["full_reason"] == "conversation_full" and len(read["turns"]) == 1
    fake.count = lambda body: 10  # once refused by the count, the box stays greyed: no count again
    counts = len(fake.counts)
    _refused(lambda: chapter_idea.follow_up(tx, cid, "more?", client=fake.client()), 422, "conversation_full")
    assert len(fake.counts) == counts


def test_the_output_reservation_is_read_from_the_body_that_is_sent(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake()
    monkeypatch.setattr(chapter_idea, "AI_CONTEXT_WINDOW_TOKENS", {"claude-opus-5-5": 120_000 + 15_999})
    _refused(lambda: chapter_idea.ask(tx, 1, client=fake.client()), 422, "chapter_too_large")
    _nothing_sent(tx, fake, counted=True)


def test_a_multibyte_request_is_bounded_by_its_own_count(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake counts one token per byte of the count request: a question and a note of CJK,
    emoji and combining marks cost what they encode to, not what they look like."""
    weird = "定跡は? 🐴♞ é́ " * 20
    with tx() as conn:
        ann.upsert(conn, [{"fen": h.spine(None, MAIN)[2], "text": weird}], 1, source="manual")
    fake = Fake()
    fake.count = lambda body: len(json.dumps(body, ensure_ascii=False).encode())
    probe = chapter_idea.ask(tx, 1, weird[:500], client=fake.client())
    assert weird[:40] in probe["conversation"]["turns"][0]["question"]
    n = fake.count(json.loads(fake.counts[0].content))
    assert n > len(json.dumps(json.loads(fake.counts[0].content), ensure_ascii=False))  # bytes > characters
    with tx() as conn:
        conn.execute("DELETE FROM ai_calls")
    fake.calls.clear()
    limit = math.ceil(n * 1.02 + 1000)
    monkeypatch.setattr(chapter_idea, "CHAPTER_IDEA_MAX_INPUT_TOKENS", limit - 1)
    _refused(lambda: chapter_idea.ask(tx, 1, weird[:500], client=fake.client()), 422, "chapter_too_large")
    assert fake.calls == [] and _calls(tx) == []
    monkeypatch.setattr(chapter_idea, "CHAPTER_IDEA_MAX_INPUT_TOKENS", limit)
    chapter_idea.ask(tx, 1, weird[:500], client=fake.client())
    assert len(fake.calls) == 1


def test_a_count_that_fails_refuses_the_request(tx: Tx) -> None:
    fake = Fake()
    fake.count_status = 429
    _refused(lambda: chapter_idea.ask(tx, 1, client=fake.client()), 503)
    _nothing_sent(tx, fake, counted=True)
    fake.count = lambda body: "many"  # type: ignore[assignment,return-value]
    fake.count_status = 200
    _refused(lambda: chapter_idea.ask(tx, 1, client=fake.client()), 503)
    assert fake.calls == []


def test_the_twelfth_turn_is_sent_and_the_thirteenth_refused(tx: Tx) -> None:
    fake = Fake(["a"])
    cid = chapter_idea.ask(tx, 1, client=fake.client())["conversation"]["id"]
    out: dict[str, Any] = {}
    for i in range(1, CHAPTER_IDEA_MAX_TURNS):
        out = chapter_idea.follow_up(tx, cid, f"q{i}", client=fake.client())
    assert len(out["conversation"]["turns"]) == CHAPTER_IDEA_MAX_TURNS and out["conversation"]["full"] is True
    sent = len(fake.calls)
    _refused(lambda: chapter_idea.follow_up(tx, cid, "q13", client=fake.client()), 422, "conversation_full")
    assert len(fake.calls) == sent and len(_calls(tx)) == sent
    assert _read(tx)["conversation"]["full"] is True


def test_a_turn_without_usage_is_stored_and_does_not_fill_the_conversation(tx: Tx) -> None:
    fake = Fake()
    fake.usage = {}
    cid = chapter_idea.ask(tx, 1, client=fake.client())["conversation"]["id"]
    read = _conv(tx, cid)["conversation"]
    assert read["turns"][0]["input_tokens"] is None and read["full"] is False
    chapter_idea.follow_up(tx, cid, "more?", client=fake.client())
    assert len(fake.calls) == 2


def test_one_follow_up_at_a_time(tx: Tx) -> None:
    """A second follow-up while one is being answered is refused before it is counted or claimed;
    the mark is cleared when the answer is stored, and when the call fails."""
    fake = Fake(["a0", "a1", "a2", "a3"])
    cid = chapter_idea.ask(tx, 1, client=fake.client())["conversation"]["id"]
    second: list[Any] = []

    def again() -> None:
        try:
            chapter_idea.follow_up(tx, cid, "at the same time", client=fake.client())
        except ai.ExplainError as exc:
            second.append(exc)

    fake.on_call = again
    out = chapter_idea.follow_up(tx, cid, "first", client=fake.client())
    assert out["saved"] is True and [t["question"] for t in out["conversation"]["turns"]][1:] == ["first"]
    [refused] = second
    assert refused.status == 409 and isinstance(refused.detail, dict) and refused.detail["code"] == "conversation_busy"
    assert len(fake.counts) == 2 and len(fake.calls) == 2 and len(_calls(tx)) == 2
    fake.on_call = None
    fake.call_status = 529
    _refused(lambda: chapter_idea.follow_up(tx, cid, "fails", client=fake.client()), 502)
    fake.call_status = 200
    assert chapter_idea.follow_up(tx, cid, "after a failure", client=fake.client())["saved"] is True
    with tx() as conn:  # a mark left by a request that died expires on its own
        conn.execute(
            "UPDATE chapter_idea_conversations SET asking_until = now() - interval '1 second' WHERE id = %s", (cid,)
        )
    assert chapter_idea.follow_up(tx, cid, "after an expired mark", client=fake.client())["saved"] is True
    with tx() as conn:
        conn.execute(
            "UPDATE chapter_idea_conversations SET asking_until = now() + interval '1 minute' WHERE id = %s", (cid,)
        )
    counts = len(fake.counts)
    _refused(lambda: chapter_idea.follow_up(tx, cid, "busy", client=fake.client()), 409, "conversation_busy")
    assert len(fake.counts) == counts


def test_provider_errors_are_logged_by_status_and_type_only(tx: Tx, caplog: pytest.LogCaptureFixture) -> None:
    echo = "ECHOED PROMPT TEXT"

    def refusing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": echo}})

    client = httpx.Client(transport=httpx.MockTransport(refusing))
    caplog.set_level("WARNING")
    _refused(lambda: chapter_idea.ask(tx, 1, client=client), 503)
    fake = Fake()
    fake.call_status = 400

    def call_refusing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/count_tokens"):
            return fake.handler(request)
        return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": echo}})

    with pytest.raises(ai.ExplainError) as err:
        chapter_idea.ask(tx, 1, client=httpx.Client(transport=httpx.MockTransport(call_refusing)))
    assert err.value.status == 502 and echo not in str(err.value.detail)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "HTTP 400, invalid_request_error" in logged and echo not in logged


# --- staleness -----------------------------------------------------------------------------------


def test_a_note_edited_after_the_answer_makes_both_reads_stale_and_keeps_the_links(tx: Tx) -> None:
    fake = Fake()
    first = chapter_idea.ask(tx, 1, client=fake.client())
    cid = first["conversation"]["id"]
    assert _read(tx)["conversation"]["stale"] is False
    with tx() as conn:
        ann.upsert(conn, [{"fen": h.spine(None, MAIN)[2], "text": "a new note"}], 1, source="manual")
    for read in (_read(tx)["conversation"], _conv(tx, cid)["conversation"]):
        assert read["stale"] is True and read["stale_reason"] == "changed"
        assert read["turns"][0]["segments"] == first["conversation"]["turns"][0]["segments"]


def test_a_change_while_the_model_answers_makes_that_answer_stale(tx: Tx) -> None:
    fake = Fake()
    sent: list[str] = []

    def edit() -> None:
        sent.append(json.loads(fake.calls[-1].content)["messages"][0]["content"][0]["text"])
        with tx() as conn:
            ann.upsert(conn, [{"fen": h.spine(None, MAIN)[2], "text": f"note {len(sent)}"}], 1, source="manual")

    fake.on_call = edit
    out = chapter_idea.ask(tx, 1, client=fake.client())
    assert out["conversation"]["stale"] is True and _read(tx)["conversation"]["stale"] is True
    cid = out["conversation"]["id"]
    with tx() as conn:
        row = conn.execute("SELECT rendered_prompt, lines FROM chapter_idea_conversations WHERE id = %s", (cid,))
        stored = row.fetchone()
    assert stored is not None and stored["rendered_prompt"] == sent[0] and "note 1" not in sent[0]
    assert set(stored["lines"]) == {"L1", "L2", "L3"}
    follow = chapter_idea.follow_up(tx, cid, "and?", client=fake.client())
    assert follow["conversation"]["stale"] is True and sent[1] == sent[0]


def test_an_unchanged_chapter_is_not_stale(tx: Tx) -> None:
    cid = chapter_idea.ask(tx, 1, client=Fake().client())["conversation"]["id"]
    assert _conv(tx, cid)["conversation"]["stale"] is False


@pytest.mark.parametrize("how", ["deleted", "switched_off", "retired_by_import"])
def test_an_emptied_chapter_keeps_its_conversations(tx: Tx, how: str, tmp_path: Path) -> None:
    fake = Fake(["See [[L3 4.c3]] and [[L1 3...Nf6]].", "follow"])
    doc = json.loads((FIXTURES / "repertoire_import.json").read_text())
    path = tmp_path / "rep.json"
    if how == "retired_by_import":
        path.write_text(json.dumps(doc))
        with tx() as conn:
            for seq in ("books_id_seq", "chapters_id_seq", "repertoire_lines_id_seq"):
                conn.execute("SELECT setval(%s::regclass, 100)", (seq,))  # past the fixture's explicit ids
            importing.import_file(conn, path, mode="scratch", window=100)
            chapter_id = int(
                conn.execute("SELECT id FROM chapters WHERE title = '2) From the tabiya'").fetchone()["id"]
            )  # type: ignore[index]
        fake.answers = ["After [[L1 3.Bc4]] it is quiet.", "follow"]
    else:
        chapter_id = 1
    cid = chapter_idea.ask(tx, chapter_id, client=fake.client())["conversation"]["id"]
    with tx() as conn:
        if how == "deleted":
            conn.execute("DELETE FROM repertoire_lines WHERE chapter_id = 1")
        elif how == "switched_off":
            conn.execute("UPDATE repertoire_lines SET active = FALSE WHERE chapter_id = 1")
        else:
            del doc["books"][0]["chapters"][1]
            path.write_text(json.dumps(doc))
            out = importing.import_file(conn, path, mode="scratch", window=100)
            assert out["chapters"]["deactivated"] == 1
    with tx() as conn:
        book_id = int(conn.execute("SELECT book_id FROM chapters WHERE id = %s", (chapter_id,)).fetchone()["book_id"])  # type: ignore[index]
        [sec] = [c for c in books.sections(conn, book_id) or [] if c["chapter_id"] == chapter_id]
    assert sec["idea_conversations"] == 1
    for read in (_read(tx, chapter_id), _conv(tx, cid)):
        conv = read["conversation"]
        assert read["chapter"]["active_lines"] == 0
        assert conv["stale"] is True and conv["stale_reason"] == "empty" and len(conv["turns"]) == 1
        assert all(v["exists"] is (how != "deleted") for v in conv["lines"].values())
        link = next(s["move"] for s in conv["turns"][0]["segments"] if "move" in s)
        assert link["fen"] and link["anchor_fen"]  # a deleted line's link still has its board
    counts = len(fake.counts)
    _refused(lambda: chapter_idea.ask(tx, chapter_id, client=fake.client()), 422, "no_active_lines")
    assert len(fake.counts) == counts
    out = chapter_idea.follow_up(tx, cid, "still?", client=fake.client())
    assert len(out["conversation"]["turns"]) == 2 and fake.body()["messages"][0] == fake.body(0)["messages"][0]


# --- the frozen conversation ---------------------------------------------------------------------


def test_a_settings_edit_after_the_first_turn_changes_only_new_conversations(tx: Tx) -> None:
    fake = Fake()
    cid = chapter_idea.ask(tx, 1, client=fake.client())["conversation"]["id"]
    first = fake.body(0)
    _configure(
        tx,
        ai_chapter_prompt=_prompt(
            **THINKING_OFF, system_prompt="Be brief.", text="{{ lines_block }} Q: {{ question }}", max_tokens=9000
        ),
    )
    out = chapter_idea.follow_up(tx, cid, "more?", client=fake.client())
    follow = fake.body(1)
    assert {k: follow[k] for k in follow if k != "messages"} == {k: first[k] for k in first if k != "messages"}
    assert follow["messages"][0] == first["messages"][0]
    assert out["conversation"]["prompt_current"] is False and _read(tx)["conversation"]["prompt_current"] is False
    fresh = chapter_idea.ask(tx, 1, client=fake.client())
    new = fake.body(2)
    assert new["model"] == "claude-sonnet-5" and new["system"] == "Be brief." and new["temperature"] == 0.7
    assert new["thinking"] == {"type": "disabled"} and new["max_tokens"] == 9000
    assert new["messages"][0]["content"][0]["text"].endswith(f" Q: {CHAPTER_DEFAULT_QUESTION}")
    assert fresh["conversation"]["prompt_current"] is True
    assert "temperature" not in json.loads(fake.counts[2].content)


def test_a_retired_model_or_a_corrupt_stored_object_continues_nothing(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake()
    cid = chapter_idea.ask(tx, 1, client=fake.client())["conversation"]["id"]
    fake.counts.clear()
    fake.calls.clear()
    with tx() as conn:
        conn.execute("DELETE FROM ai_calls")
    monkeypatch.setattr(chapter_idea, "AI_CONTEXT_WINDOW_TOKENS", {"claude-sonnet-5": 1_000_000})
    _refused(lambda: chapter_idea.follow_up(tx, cid, "q", client=fake.client()), 422, "model_retired")
    assert _read(tx)["conversation"]["blocked"] == "model_retired"
    monkeypatch.undo()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for change in (
        'params || \'{"prefill": "Sure"}\'',
        "params - 'thinking_mode'",
        "params || '{\"seed\": 1}'",
        'params || \'{"max_tokens": "big"}\'',
    ):
        with tx() as conn:
            conn.execute(f"UPDATE chapter_idea_conversations SET params = {change} WHERE id = %s", (cid,))  # type: ignore[arg-type]
        _refused(lambda: chapter_idea.follow_up(tx, cid, "q", client=fake.client()), 422, "conversation_params_invalid")
        assert _read(tx)["conversation"]["blocked"] == "conversation_params_invalid"
        assert len(_conv(tx, cid)["conversation"]["turns"]) == 1
        with tx() as conn:
            conn.execute(
                "UPDATE chapter_idea_conversations SET params = %s WHERE id = %s",
                (Jsonb(ai.ConversationParams.of(_prompt()).as_stored()), cid),
            )
    _nothing_sent(tx, fake)


def test_a_stored_chapter_prompt_that_cannot_converse_costs_nothing(tx: Tx) -> None:
    with tx() as conn:
        bad = Settings().model_dump(mode="json")
        bad["ai_chapter_prompt"] |= {"model": "claude-sonnet-5", "thinking_enabled": False, "prefill": "Sure"}
        conn.execute("INSERT INTO settings (id, data) VALUES (1, %s::jsonb)", (json.dumps(bad),))
    fake = Fake()
    _refused(lambda: chapter_idea.ask(tx, 1, client=fake.client()), 422)
    _nothing_sent(tx, fake)


# --- a paid answer the database will not take -----------------------------------------------------


def test_a_store_that_fails_once_is_tried_again(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    real = chapter_idea._store  # pyright: ignore[reportPrivateUsage]
    failures = [1]

    def flaky(conn: psycopg.Connection[Any], asked: Any, turn: dict[str, Any]) -> int:
        if failures[0]:
            failures[0] -= 1
            raise psycopg.OperationalError("down")
        return real(conn, asked, turn)

    monkeypatch.setattr(chapter_idea, "_store", flaky)
    out = chapter_idea.ask(tx, 1, client=Fake().client())
    assert out["saved"] is True and len(_read(tx)["conversations"]) == 1


def test_a_read_that_fails_after_the_store_never_stores_twice(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    real = chapter_idea.read_conversation
    failures = [1]

    def flaky(conn: psycopg.Connection[Any], conversation_id: int) -> Any:
        if failures[0]:
            failures[0] -= 1
            raise psycopg.OperationalError("down")
        return real(conn, conversation_id)

    monkeypatch.setattr(chapter_idea, "read_conversation", flaky)
    with pytest.raises(psycopg.OperationalError):
        chapter_idea.ask(tx, 1, client=Fake().client())
    read = _read(tx)
    assert len(read["conversations"]) == 1 and len(read["conversation"]["turns"]) == 1


def test_an_answer_that_cannot_be_stored_twice_is_returned_unsaved(tx: Tx, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(conn: psycopg.Connection[Any], asked: Any, turn: dict[str, Any]) -> int:
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(chapter_idea, "_store", down)
    fake = Fake()
    out = chapter_idea.ask(tx, 1, client=fake.client())
    assert out["saved"] is False and out["conversation_id"] is None
    assert out["lines"]["L1"] == {"line_id": 2, "name": "B", "exists": True}
    assert out["unsaved_turn"]["segments"][1]["move"]["label"] == "3.Bc4"
    assert _read(tx)["conversations"] == [] and len(_calls(tx)) == 1 and len(fake.calls) == 1


# --- migration 017 ---------------------------------------------------------------------------------


def test_migration_017_upgrades_a_populated_database(fresh_db_url: str, tmp_path: Path) -> None:
    admin, name = _admin_url(fresh_db_url)
    url = with_dbname(fresh_db_url, f"{name}_v16")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(f"{name}_v16")))
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(f"{name}_v16")))
    v16 = tmp_path / "v16"
    v16.mkdir()
    for n, path in schema.migration_files():
        if n <= 16:
            (v16 / path.name).write_text(path.read_text())
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL((FIXTURES / "schema_baseline.sql").read_text()))  # type: ignore[arg-type]  # repo fixture
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
        c.commit()
        schema.upgrade(c, v16)
        h.player(c)
        h.book(c, 1, "Italian", "white")
        h.chapter(c, 1, 1, "Giuoco")
        h.line(c, 1, 1, "b", MAIN)
        c.commit()
        assert schema.upgrade(c)[0] == 17
        [section] = books.sections(c, 1) or []
        assert section["idea_conversations"] == 0
        out = chapter_idea.read_chapter(c, 1)
        assert out is not None and out["conversation"] is None and out["chapter"]["active_lines"] == 1
        c.execute(
            "INSERT INTO chapter_idea_conversations (chapter_id, params, prompt_hash, context_hash, rendered_prompt, lines)"
            " VALUES (1, '{}', 'p', 'c', 'r', '{}')"
        )
        c.execute("DELETE FROM chapters WHERE id = 1")  # a conversation goes only with its chapter
        assert c.execute("SELECT count(*) AS n FROM chapter_idea_conversations").fetchone() == {"n": 0}


# --- the routes ----------------------------------------------------------------------------------


@pytest.fixture()
def client(app_env: None, tx: Tx, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    from api.main import create_app

    fake = Fake()
    monkeypatch.setattr(chapter_idea, "_with_client", lambda client, fn: fn(fake.client()))
    c = TestClient(create_app())
    assert c.post("/login", json={"password": "correct horse"}).status_code == 200
    c.fake = fake  # type: ignore[attr-defined]
    yield c


def test_the_routes(client: TestClient) -> None:
    assert client.get("/repertoire/chapters/1/idea").json()["conversation"] is None
    assert client.get("/repertoire/chapters/9/idea").status_code == 404
    r = client.post("/repertoire/chapters/1/idea", json={"question": "why?"})
    assert r.status_code == 200 and r.json()["saved"] is True
    cid = r.json()["conversation"]["id"]
    assert client.get(f"/repertoire/chapter-ideas/{cid}").json()["conversation"]["turns"][0]["question"] == "why?"
    assert client.post(f"/repertoire/chapter-ideas/{cid}/followup", json={"question": "more"}).status_code == 200
    r = client.post("/repertoire/chapters/3/idea", json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_active_lines"
    assert client.post("/repertoire/chapters/1/idea", json={"question": "x" * 2001}).status_code == 422
    assert client.get("/repertoire/chapter-ideas/999").status_code == 404
    sections = client.get("/repertoire/1/sections").json()["sections"]
    assert sorted((s["chapter_id"], s["idea_conversations"]) for s in sections) == [(1, 1), (2, 0), (3, 0)]


def test_saving_a_chapter_prompt_with_a_prefill_is_refused_by_the_route(client: TestClient) -> None:
    body = client.get("/settings").json()
    body["ai_chapter_prompt"] |= {"model": "claude-sonnet-5", "thinking_enabled": False, "prefill": "Sure"}
    r = client.put("/settings", json=body)
    assert r.status_code == 422 and "cannot use a prefill" in r.json()["detail"]
