"""Asking about one repertoire chapter: the idea of it, with follow-ups that keep the context, and
every move the answer names a link onto the board at exactly that moment of a line.

**The render.** A chapter's active lines (`line_name COLLATE "C"`, then id), labelled L1…Ln and
numbered from each line's own first board, and every note on those lines' boards as the
walk-through projects it (`annotations.project`), once per (placement, text), naming its line and
who wrote it. Every read renders it (for `stale`), so it reads only what it uses: the chapter with
its active lines, then what decides each board's note without the notes' text, the candidate
lines' moves once each, and the text of the notes that win. `context_hash` digests the template
context, not the prompt settings or the question; a chapter with no active lines has the empty
context, whose hash no conversation was ever asked under, so every stored conversation reads as
stale.

**Tags into links.** The model writes every move as `[[L3 10.Bc3]]` (L3 plays exactly that move
at that number) or `[[L3 9...a5 | 10.Nbd2 Be6]]` (the anchor, then moves legal in turn; a number
that disagrees with the board is ignored). Moves are compared parsed, never as strings, and the
null move is refused. A tag that fails either rule becomes the text it would have shown: a wrong
board is never linked. A numbered move outside a tag is linked when every active line playing it
at that number reaches the same board after it (the first such line in label order is opened);
an unnumbered one stays text. The answer is resolved once, when it is stored, against the line
map the model was shown, and a read never parses it again.

**A conversation.** Its first call freezes the rendered chapter, the line map and the effective
parameters; every follow-up is built from those, never from the current lines or settings, so a
follow-up sends the identical cached block. Every question and answer is stored and read back
with no provider call; nothing deletes a conversation but its chapter. A read reports `stale` (the
chapter as it stands now renders a different context) and `full` (the turn cap, or a follow-up
the count refused, whose reason is kept). Every request is counted by the provider before a slot
is claimed and refused over `CHAPTER_IDEA_MAX_INPUT_TOKENS`; no transaction is open during the
count or the call; a failed call gives its slot back; a paid answer the database will not take is
stored again once in a new transaction and otherwise returned marked unsaved.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, LiteralString, cast

import chess
import httpx
import psycopg
from jinja2 import TemplateError
from psycopg import Connection
from psycopg.types.json import Jsonb

from core import ai, prompts
from core.constants import (
    AI_CONTEXT_WINDOW_TOKENS,
    CHAPTER_IDEA_COUNT_MARGIN,
    CHAPTER_IDEA_COUNT_PAD_TOKENS,
    CHAPTER_IDEA_MAX_INPUT_TOKENS,
    CHAPTER_IDEA_MAX_PROMPT_CHARS,
    CHAPTER_IDEA_MAX_TURNS,
    PLAYER_ID,
)
from core.prompts import CHAPTER_DEFAULT_QUESTION
from core.repertoire import annotations
from core.settings import Settings, chapter_prompt_errors
from core.settings import load as load_settings

PROMPT_KEY = "chapter"  # what `ai_calls.prompt_key` records
TIMEOUT_SECONDS = 300.0
# How long a follow-up in flight holds its conversation: past the count's and the call's timeouts,
# so a request that died without clearing it frees the conversation on its own.
ASKING_SECONDS = 3 * TIMEOUT_SECONDS
EMPTY_CONTEXT: dict[str, Any] = {"empty": True}

Transaction = Callable[[], AbstractContextManager[Connection[Any]]]
Lines = dict[str, dict[str, Any]]
_log = logging.getLogger(__name__)

MESSAGES = {
    "no_active_lines": "This chapter has no active lines now.",
    "chapter_too_large": "This chapter is too large to ask about in one go.",
    "conversation_full": "This conversation is as long as it can get. Start over to keep asking.",
    "conversation_params_invalid": "This conversation can't be continued; Start over.",
    "model_retired": "This conversation's model is no longer available; Start over to ask with the current one.",
    "conversation_busy": "A follow-up to this conversation is already being answered.",
}


def refusal(code: str, status: int = 422) -> ai.ExplainError:
    return ai.ExplainError(status, {"code": code, "message": MESSAGES[code]})


# --- move labels ---------------------------------------------------------------------------------


def _start(fen: str) -> tuple[bool, int]:
    fields = fen.split()
    white_first = len(fields) < 2 or fields[1] == "w"
    try:
        first = int(fields[5])
    except IndexError, ValueError:
        first = 1
    return white_first, first


def label(start_fen: str, k: int, san: str) -> str:
    """Ply k of a line starting at `start_fen`, numbered as the prompt and the tags write it."""
    white_first, first = _start(start_fen)
    number = first + (k + (0 if white_first else 1)) // 2
    white = (k % 2 == 0) == white_first
    return f"{number}.{san}" if white else f"{number}...{san}"


def ply_of(start_fen: str, number: int, white: bool) -> int:
    """The index of the move a number names in a line starting at `start_fen`."""
    white_first, first = _start(start_fen)
    return (number - first) * 2 + (0 if white else 1) - (0 if white_first else 1)


def shown(board: chess.Board, move: chess.Move) -> str:
    """A move as the app writes it, numbered from the board it is played on: `10.Bc3`, `10…Be6`."""
    sep = "." if board.turn == chess.WHITE else "…"
    return f"{board.fullmove_number}{sep}{board.san(move)}"


# --- the render ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Render:
    """A chapter as the model is shown it. `context` is None when it has no active lines."""

    chapter_id: int
    chapter_title: str
    book_title: str
    color: str
    context: dict[str, Any] | None
    lines: Lines

    @property
    def context_hash(self) -> str:
        return ai.context_hash(self.context if self.context is not None else EMPTY_CONTEXT)


def render_chapter(conn: Connection[Any], chapter_id: int) -> Render | None:
    """The chapter's context and line map, or None when it is not the player's."""
    rows = conn.execute(
        "SELECT ch.title AS chapter_title, bk.id AS book_id, bk.title AS book_title, bk.color,"
        " rl.id AS line_id, rl.line_name, rl.is_alternative, rl.moves, rl.fen_sequence"
        " FROM chapters ch JOIN books bk ON bk.id = ch.book_id"
        " LEFT JOIN repertoire_lines rl ON rl.chapter_id = ch.id AND rl.active"
        ' WHERE ch.id = %s AND bk.player_id = %s ORDER BY rl.line_name COLLATE "C", rl.id',
        (chapter_id, PLAYER_ID),
    ).fetchall()
    if not rows:
        return None
    head = rows[0]
    chapter_title, book_title, color = str(head["chapter_title"]), str(head["book_title"]), str(head["color"])
    found = [r for r in rows if r["line_id"] is not None]
    if not found:
        return Render(chapter_id, chapter_title, book_title, color, None, {})
    spines: dict[int, set[str]] = {}
    for r in found:
        keys: set[str] = set()
        for f in annotations.strings(r["fen_sequence"]):
            try:
                keys.add(annotations.normalize_fen(f))
            except ValueError:
                continue
        spines[int(r["line_id"])] = keys
    wanted = sorted({key for keys in spines.values() for key in keys})
    # What decides which note a board shows, without the notes' text: the winners' text is read
    # afterwards, and each candidate line's moves once. A candidate's `text` holds its id until then
    # (`annotations.project` copies `text`, never reads it). The three reads are separate statements:
    # a note changed between them gives one render no single state would, which the next read
    # reports as stale.
    meta = conn.execute(
        "SELECT ra.id AS ann_id, ra.fen_norm, ra.source, ra.line_id AS ann_line_id, ra.updated_at,"
        " rl2.chapter_id AS ann_chapter_id"
        " FROM repertoire_annotations ra JOIN repertoire_lines rl2 ON rl2.id = ra.line_id"
        " JOIN chapters ch2 ON ch2.id = rl2.chapter_id"
        " WHERE ra.player_id = %s AND ch2.book_id = %s AND ra.fen_norm = ANY(%s)",
        (PLAYER_ID, head["book_id"], wanted),
    ).fetchall()
    moves_of = {
        int(m["id"]): m["moves"]
        for m in conn.execute(
            "SELECT id, moves FROM repertoire_lines WHERE id = ANY(%s)",
            (sorted({int(c["ann_line_id"]) for c in meta}),),
        ).fetchall()
    }
    by_board: dict[str, list[dict[str, Any]]] = {}
    for c in meta:
        candidate = dict(c) | {"moves": moves_of.get(int(c["ann_line_id"])), "text": int(c["ann_id"])}
        by_board.setdefault(str(c["fen_norm"]), []).append(candidate)

    projected: list[tuple[dict[str, Any], list[str], list[str], list[dict[str, Any]]]] = []
    for r in found:
        line_id = int(r["line_id"])
        fens = annotations.strings(r["fen_sequence"])
        moves = annotations.strings(r["moves"])[: max(len(fens) - 1, 0)]
        if not fens:
            continue
        candidates = [c for key in spines[line_id] for c in by_board.get(key, [])]
        projected.append((r, fens, moves, annotations.project(moves, fens, candidates, line_id, chapter_id)))
    winners = sorted({int(p["annotation"]["text"]) for *_, ps in projected for p in ps if p.get("annotation")})
    text_of = {
        int(t["id"]): str(t["text"] or "")
        for t in conn.execute("SELECT id, text FROM repertoire_annotations WHERE id = ANY(%s)", (winners,)).fetchall()
    }

    lines: Lines = {}
    lines_rows: list[str] = []
    notes_rows: list[str] = []
    seen: set[tuple[str, str]] = set()
    for r, fens, moves, positions in projected:
        line_id = int(r["line_id"])
        start, tag = fens[0], f"L{len(lines) + 1}"
        lines[tag] = {"line_id": line_id, "name": str(r["line_name"]), "start_fen": start, "moves": moves}
        alt = " (alternative)" if r["is_alternative"] else ""
        numbered = " ".join(label(start, k, m) for k, m in enumerate(moves))
        lines_rows.append(f"- {tag} — {r['line_name']}{alt}: {numbered}")
        for k, pos in enumerate(positions):
            note = pos.get("annotation")
            text = text_of.get(int(note["text"]), "") if note else ""
            if not note or not text.strip():
                continue
            prose = ai.note_prose(text)
            about = label(start, k, moves[k]) if k < len(moves) else "the final position"
            key = (str(pos["fen"]).split(" ")[0], prose)
            if key in seen:
                continue
            seen.add(key)
            who = "the player's own note" if note.get("source") == "manual" else "the author"
            notes_rows.append(f"- on {about} ({tag}; {who}): {prose}")
    context = {
        "book_title": book_title,
        "color": color,
        "chapter_title": chapter_title,
        "line_count": len(lines_rows),
        "note_count": len(notes_rows),
        "lines_block": "\n".join(lines_rows),
        "notes_block": "\n".join(notes_rows) or "(no notes in this chapter)",
    }
    return Render(chapter_id, chapter_title, book_title, color, context, lines)


# --- tags into links -----------------------------------------------------------------------------

TAG = re.compile(r"\[\[(.*?)\]\]", re.S)
ANCHOR = re.compile(r"^L(\d+)\s+(\d+)(\.\.\.|\.)\s*(\S+)$")
NUMBERED = re.compile(r"^(\d+)(\.\.\.|\.)\s*(\S+)$")
# Move-like text left outside a tag: a piece move, a capture, castling, a promotion, or a pawn move
# written with a number or "...". A bare square ("e4") is a square, not a move. A move named as a
# plan ("the ...d5 break") or a route ("Bb3-c2") is what the prompt says not to tag.
_PLAN = (
    r"(?![\w])(?!-[a-h])(?!\s+(?:break|push|advance|thrust|lever|idea|plan|pawn|square|structure|setup|system"
    r"|move order)s?\b)"
)
UNTAGGED = re.compile(
    r"(?<![\w\[/-])(?:\d+\.(?:\.\.)?\s?|\.\.\.\s?)?"
    r"(?:O-O-O|O-O|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][1-8]=[QRBN])[+#!?]*"
    + _PLAN
    + r"|(?<![\w\[/-])(?:\d+\.(?:\.\.)?\s?|\.\.\.\s?)[a-h][1-8][+#!?]*"
    + _PLAN
)


def _parse(board: chess.Board, san: str) -> chess.Move | None:
    try:
        move = board.parse_san(san.rstrip("!?"))
    except ValueError:
        return None
    if not move:  # python-chess parses the null move
        return None
    return move


def _replay(line: dict[str, Any], k: int) -> chess.Board | None:
    """The line's board before its move `k`, or None when the stored moves do not play."""
    board = chess.Board(str(line["start_fen"]))
    for san in list(line["moves"])[:k]:
        move = _parse(board, str(san))
        if move is None:
            return None
        board.push(move)
    return board


def resolve_tag(inner: str, lines: Lines) -> tuple[str, str, dict[str, Any] | None]:
    """`(verdict, why, link)`: valid | misnumbered (linked) | wrong_move | invalid (not linked)."""
    head, _, tail = inner.partition("|")
    m = ANCHOR.match(head.strip())
    if not m:
        return "invalid", "not of the form L<n> <move>", None
    tag = f"L{m.group(1)}"
    line = lines.get(tag)
    if line is None:
        return "invalid", f"no line {tag}", None
    start, moves = str(line["start_fen"]), list(line["moves"])
    k = ply_of(start, int(m.group(2)), m.group(3) == ".")
    if not 0 <= k < len(moves):
        return "invalid", f"{tag} has no move {m.group(2)}{m.group(3)}", None
    board = _replay(line, k)
    if board is None:
        return "invalid", f"{tag} does not play", None
    said, played = _parse(board, m.group(4)), _parse(board, str(moves[k]))
    if said is None:
        return "invalid", f"{m.group(4)} is not a legal move there", None
    if played is None or said != played:
        return "wrong_move", f"{tag} plays {label(start, k, str(moves[k]))}, tag says {m.group(4)}", None
    anchor = shown(board, played)
    board.push(played)
    anchor_fen = board.fen()
    last, verdict, why = anchor, "valid", ""
    branch: list[str] = []
    for token in tail.split():
        n = NUMBERED.match(token)
        san = token
        if n:
            san = n.group(3)
            if int(n.group(1)) != board.fullmove_number or (n.group(2) == ".") != (board.turn == chess.WHITE):
                verdict, why = "misnumbered", f"{token} numbered off"
        elif re.fullmatch(r"\d+\.+", token):
            continue
        move = _parse(board, san)
        if move is None:
            return "invalid", f"after the bar, {token} is not legal", None
        last = shown(board, move)
        branch.append(board.san(move))
        board.push(move)
    link = {
        "label": last,
        "line": tag,
        "line_id": int(line["line_id"]),
        "ply": k + 1,
        "anchor": anchor,
        "anchor_fen": anchor_fen,
        "branch": branch,
        "fen": board.fen(),
    }
    return verdict, why, link


def check_tag(inner: str, lines: Lines) -> tuple[str, str]:
    verdict, why, _ = resolve_tag(inner, lines)
    return verdict, why


def _label_order(lines: Lines) -> list[str]:
    return sorted(lines, key=lambda t: int(t[1:]) if t[1:].isdigit() else 0)


def resolvable(mention: str, lines: Lines) -> dict[str, Any] | None:
    """The link for an untagged numbered mention when every line that plays that move at that
    number reaches the same board after it (the first such line in label order), else None."""
    m = NUMBERED.match(mention.strip())
    if not m:
        return None
    hits: list[tuple[str, dict[str, Any]]] = []
    for tag in _label_order(lines):
        line = lines[tag]
        k = ply_of(str(line["start_fen"]), int(m.group(1)), m.group(2) == ".")
        if not 0 <= k < len(line["moves"]):
            continue
        board = _replay(line, k)
        if board is None:
            continue
        said = _parse(board, m.group(3).rstrip("+#."))
        if said is None or said != _parse(board, str(line["moves"][k])):
            continue
        assert said is not None
        text = shown(board, said)
        board.push(said)
        fen = board.fen()
        link: dict[str, Any] = {
            "label": text,
            "line": tag,
            "line_id": int(line["line_id"]),
            "ply": k + 1,
            "anchor": text,
            "anchor_fen": fen,
            "branch": [],
            "fen": fen,
        }
        hits.append((annotations.normalize_fen(fen), link))
    if not hits or len({b for b, _ in hits}) != 1:
        return None
    return hits[0][1]


def _shown_text(inner: str) -> str:
    """What a tag would have shown, for one that is not linked: its last move as written."""
    head, _, tail = inner.partition("|")
    words = tail.split() or head.split()
    return words[-1] if words else ""


def resolve(answer: str, lines: Lines) -> list[dict[str, Any]]:
    """The answer as segments `{"text"}` or `{"move": link}`; adjacent text is merged."""
    out: list[dict[str, Any]] = []

    def text(s: str) -> None:
        if not s:
            return
        if out and "text" in out[-1]:
            out[-1]["text"] += s
        else:
            out.append({"text": s})

    def plain(s: str) -> None:
        at = 0
        for u in UNTAGGED.finditer(s):
            link = resolvable(u.group(0), lines)
            if link is None:
                continue
            text(s[at : u.start()])
            out.append({"move": link})
            at = u.end()
        text(s[at:])

    at = 0
    for t in TAG.finditer(answer):
        plain(answer[at : t.start()])
        _, _, link = resolve_tag(t.group(1), lines)
        if link is None:
            text(_shown_text(t.group(1)))
        else:
            out.append({"move": link})
        at = t.end()
    plain(answer[at:])
    return out


def tag_report(answer: str, lines: Lines) -> dict[str, Any]:
    """The tag check over one answer: verdict counts, the untagged mentions and how many of them
    the fallback links, and the shares the acceptance bars are stated in."""
    verdicts = {"valid": 0, "misnumbered": 0, "wrong_move": 0, "invalid": 0}
    problems: list[str] = []
    branched = 0
    for inner in TAG.findall(answer):
        v, why = check_tag(inner, lines)
        verdicts[v] += 1
        branched += "|" in inner
        if v != "valid":
            problems.append(f"{v}: [[{inner}]] — {why}")
    untagged = [u.group(0).strip() for u in UNTAGGED.finditer(TAG.sub(" ", answer))]
    linked = [u for u in untagged if resolvable(u, lines) is not None]
    tags = sum(verdicts.values())
    mentions = tags + len(untagged)
    return {
        "tags": tags,
        "branched": branched,
        **verdicts,
        "untagged": len(untagged),
        "valid_share": round((verdicts["valid"] + verdicts["misnumbered"]) / tags, 3) if tags else None,
        "untagged_share": round(len(untagged) / mentions, 3) if mentions else None,
        "server_links": len(linked),
        "unlinked_share": round((len(untagged) - len(linked)) / mentions, 3) if mentions else None,
        "problems": problems,
        "untagged_list": untagged,
    }


# --- storage -------------------------------------------------------------------------------------


def _conversation(conn: Connection[Any], conversation_id: int, *, for_call: bool = False) -> dict[str, Any] | None:
    """The stored conversation with its turns in order, or None when it is not the player's. Only a
    follow-up reads what it resends (the rendered chapter, each answer's text); a read gets the
    segments the dialog draws."""
    resent = (
        "c.rendered_prompt, c.lines"
        if for_call
        else "NULL AS rendered_prompt, (SELECT jsonb_object_agg(e.key, jsonb_build_object("
        "'line_id', e.value -> 'line_id', 'name', e.value -> 'name')) FROM jsonb_each(c.lines) e) AS lines"
    )
    row = conn.execute(
        cast(
            LiteralString,
            "SELECT c.id, c.chapter_id, c.params, c.prompt_hash, c.context_hash, c.full_reason, c.created_at, "
            + resent
            + " FROM chapter_idea_conversations c JOIN chapters ch ON ch.id = c.chapter_id"
            " JOIN books bk ON bk.id = ch.book_id WHERE c.id = %s AND bk.player_id = %s",
        ),
        (conversation_id, PLAYER_ID),
    ).fetchone()
    if row is None:
        return None
    turns = conn.execute(
        cast(
            LiteralString,
            "SELECT seq, question, "
            + ("answer" if for_call else "segments")
            + ", input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, created_at"
            " FROM chapter_idea_turns WHERE conversation_id = %s ORDER BY seq",
        ),
        (conversation_id,),
    ).fetchall()
    return dict(row) | {"turns": [dict(t) | {"created_at": t["created_at"].astimezone(UTC).isoformat()} for t in turns]}


def _listing(conn: Connection[Any], chapter_id: int) -> list[dict[str, Any]]:
    """Every conversation of the chapter, newest first: id, date, number of turns."""
    rows = conn.execute(
        "SELECT c.id, c.created_at, (SELECT count(*) FROM chapter_idea_turns t WHERE t.conversation_id = c.id) AS turns"
        " FROM chapter_idea_conversations c WHERE c.chapter_id = %s ORDER BY c.created_at DESC, c.id DESC",
        (chapter_id,),
    ).fetchall()
    return [
        {"id": int(r["id"]), "created_at": r["created_at"].astimezone(UTC).isoformat(), "turns": int(r["turns"])}
        for r in rows
    ]


def _existing(conn: Connection[Any], lines: Lines) -> set[int]:
    ids = [int(v["line_id"]) for v in lines.values()]
    return {int(r["id"]) for r in conn.execute("SELECT id FROM repertoire_lines WHERE id = ANY(%s)", (ids,))}


def _blocked(params: Any) -> str | None:
    """Why a stored conversation cannot be continued whatever its size, or None."""
    try:
        p = ai.ConversationParams.stored(params)
    except ai.ExplainError:
        return "conversation_params_invalid"
    try:
        ai.provider_of(p.model)
    except ai.ExplainError:
        return "model_retired"
    return None if p.model in AI_CONTEXT_WINDOW_TOKENS else "model_retired"


def _current_prompt_hash(settings: Settings) -> str | None:
    try:
        return ai.prompt_hash(settings.ai_chapter_prompt)
    except ai.ExplainError:
        return None


def _view(conn: Connection[Any], settings: Settings, conv: dict[str, Any], current: Render) -> dict[str, Any]:
    """A conversation as the dialog draws it: never the rendered prompt."""
    lines = cast(Lines, conv["lines"] or {})
    turns: list[dict[str, Any]] = conv["turns"]
    existing = _existing(conn, lines)
    blocked = _blocked(conv["params"])
    full_reason = conv.get("full_reason")
    at_cap = len(turns) >= CHAPTER_IDEA_MAX_TURNS
    params = cast(dict[str, Any], conv["params"]) if isinstance(conv["params"], dict) else {}
    return {
        "id": conv["id"],
        "chapter_id": conv["chapter_id"],
        "created_at": conv["created_at"].astimezone(UTC).isoformat(),
        "model": str(params.get("model") or ""),
        "turns": [{k: t.get(k) for k in _TURN_KEYS} for t in turns],
        "lines": {
            tag: {"line_id": int(v["line_id"]), "name": str(v["name"]), "exists": int(v["line_id"]) in existing}
            for tag, v in lines.items()
        },
        "stale": current.context_hash != conv["context_hash"],
        "stale_reason": None
        if current.context_hash == conv["context_hash"]
        else ("empty" if current.context is None else "changed"),
        "prompt_current": _current_prompt_hash(settings) == conv["prompt_hash"],
        "full": at_cap or full_reason is not None,
        "full_reason": "conversation_full" if (at_cap or full_reason is not None) else None,
        "blocked": blocked,
    }


_TURN_KEYS = (
    "seq",
    "question",
    "segments",
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "created_at",
)


def _chapter_head(current: Render) -> dict[str, Any]:
    return {
        "id": current.chapter_id,
        "title": current.chapter_title,
        "book_title": current.book_title,
        "color": current.color,
        "active_lines": len(current.lines),
    }


def read_chapter(conn: Connection[Any], chapter_id: int) -> dict[str, Any] | None:
    """The chapter, its conversations newest first, and the newest one in full; no provider call."""
    current = render_chapter(conn, chapter_id)
    if current is None:
        return None
    listing = _listing(conn, chapter_id)
    conv = _conversation(conn, listing[0]["id"]) if listing else None
    settings = load_settings(conn)
    return {
        "chapter": _chapter_head(current),
        "conversations": listing,
        "conversation": _view(conn, settings, conv, current) if conv else None,
    }


def read_conversation(conn: Connection[Any], conversation_id: int) -> dict[str, Any] | None:
    """One conversation of a chapter, with the chapter's listing; no provider call."""
    conv = _conversation(conn, conversation_id)
    if conv is None:
        return None
    current = render_chapter(conn, int(conv["chapter_id"]))
    assert current is not None  # the conversation was found through the player's chapter
    settings = load_settings(conn)
    return {
        "chapter": _chapter_head(current),
        "conversations": _listing(conn, int(conv["chapter_id"])),
        "conversation": _view(conn, settings, conv, current),
    }


# --- asking ----------------------------------------------------------------------------------------


def _question(question: str, *, first: bool) -> str:
    q = question.strip()
    if len(q) > ai.QUESTION_MAX_CHARS:
        raise ai.ExplainError(422, f"the question is longer than {ai.QUESTION_MAX_CHARS} characters")
    if not q:
        if not first:
            raise ai.ExplainError(422, "a follow-up needs a question")
        return CHAPTER_DEFAULT_QUESTION
    return q


def check_size(body: dict[str, Any], counted: int, code: str) -> None:
    """Refuse a request whose padded count is over the input cap, or whose cap and output
    reservation (this body's own `max_tokens`) the model's window cannot hold."""
    window = AI_CONTEXT_WINDOW_TOKENS.get(str(body["model"]))
    if window is None:
        raise refusal("model_retired")
    padded = counted * CHAPTER_IDEA_COUNT_MARGIN + CHAPTER_IDEA_COUNT_PAD_TOKENS
    if padded > CHAPTER_IDEA_MAX_INPUT_TOKENS or CHAPTER_IDEA_MAX_INPUT_TOKENS + int(body["max_tokens"]) > window:
        raise refusal(code)


@dataclass(frozen=True)
class _Asked:
    """What one call needs once the preparing transaction has closed."""

    chapter_id: int
    conversation_id: int | None  # None for a first question
    seq: int
    question: str
    body: dict[str, Any]
    params: ai.ConversationParams
    prompt_hash: str
    context_hash: str
    rendered: str
    lines: Lines


def _with_client(client: httpx.Client | None, fn: Callable[[httpx.Client], Any]) -> Any:
    if client is not None:
        return fn(client)
    with httpx.Client(timeout=TIMEOUT_SECONDS) as own:
        return fn(own)


def _send(tx: Transaction, asked: _Asked, size_code: str, client: httpx.Client | None) -> ai.ConversationReply:
    """Count (no transaction open), check the size, claim, call (none open), give the slot back on
    a failure. A follow-up the count refuses records why, so the dialog keeps the box greyed."""
    counted = _with_client(client, lambda c: ai.count_tokens(asked.body, c))
    try:
        check_size(asked.body, counted, size_code)
    except ai.ExplainError:
        if asked.conversation_id is not None and size_code == "conversation_full":
            try:
                with tx() as conn:
                    conn.execute(
                        "UPDATE chapter_idea_conversations SET full_reason = 'count' WHERE id = %s",
                        (asked.conversation_id,),
                    )
            except psycopg.Error as exc:  # the refusal stands; the next follow-up is counted again
                _log.warning("chapter idea: the refusal could not be recorded: %s", type(exc).__name__)
        raise
    with tx() as conn:
        claim_id = ai.claim(conn, load_settings(conn), PROMPT_KEY, asked.params.model)
    started = time.perf_counter()
    try:
        reply = cast(ai.ConversationReply, _with_client(client, lambda c: ai.call_conversation(asked.body, c)))
    except BaseException:
        try:
            with tx() as conn:
                ai.release(conn, claim_id)
        except psycopg.Error as exc:  # the call's own error is the one to report
            _log.warning("chapter idea: a slot could not be given back: %s", type(exc).__name__)
        raise
    _log.info(
        "chapter idea: chapter=%d turn=%d in=%s cache_read=%s cache_write=%s out=%s total_ms=%d",
        asked.chapter_id,
        asked.seq,
        reply.input_tokens,
        reply.cache_read_tokens,
        reply.cache_write_tokens,
        reply.output_tokens,
        int((time.perf_counter() - started) * 1000),
    )
    try:
        with tx() as conn:
            ai.record_tokens(conn, claim_id, reply.total_input, reply.output_tokens)
    except psycopg.Error:
        pass  # the cap counts the call either way
    return reply


def _turn(asked: _Asked, reply: ai.ConversationReply, segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "seq": asked.seq,
        "question": asked.question,
        "answer": reply.text,
        "segments": segments,
        "input_tokens": reply.input_tokens,
        "cache_read_tokens": reply.cache_read_tokens,
        "cache_write_tokens": reply.cache_write_tokens,
        "output_tokens": reply.output_tokens,
    }


def _store(conn: Connection[Any], asked: _Asked, turn: dict[str, Any]) -> int:
    """Write the conversation (a first question) and the turn; returns the conversation id."""
    conversation_id = asked.conversation_id
    if conversation_id is None:
        row = conn.execute(
            "INSERT INTO chapter_idea_conversations"
            " (chapter_id, params, prompt_hash, context_hash, rendered_prompt, lines)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                asked.chapter_id,
                Jsonb(asked.params.as_stored()),
                asked.prompt_hash,
                asked.context_hash,
                asked.rendered,
                Jsonb(asked.lines),
            ),
        ).fetchone()
        assert row is not None
        conversation_id = int(row["id"])
    conn.execute(
        "INSERT INTO chapter_idea_turns (conversation_id, seq, question, answer, segments, input_tokens,"
        " cache_read_tokens, cache_write_tokens, output_tokens) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            conversation_id,
            turn["seq"],
            turn["question"],
            turn["answer"],
            Jsonb(turn["segments"]),
            turn["input_tokens"],
            turn["cache_read_tokens"],
            turn["cache_write_tokens"],
            turn["output_tokens"],
        ),
    )
    return conversation_id


def _finish(tx: Transaction, asked: _Asked, reply: ai.ConversationReply) -> dict[str, Any]:
    """Resolve the answer against the line map that was sent, store it (once more in a new
    transaction if the first attempt fails) and return the stored conversation, its staleness
    taken from the chapter as it stands after the answer. Two failed stores return the answer
    once, marked unsaved. A store is never repeated once it has committed: a read that fails after
    it is an error, and the answer is there when the chapter is opened again."""
    turn = _turn(asked, reply, resolve(reply.text, asked.lines))
    stored: int | None = None
    for _ in range(2):
        try:
            with tx() as conn:
                stored = _store(conn, asked, turn)
            break
        except psycopg.Error as exc:
            _log.warning("chapter idea: the answer could not be stored: %s", type(exc).__name__)
    if stored is None:
        return {
            "saved": False,
            "unsaved_turn": {k: turn.get(k) for k in _TURN_KEYS} | {"created_at": datetime.now(UTC).isoformat()},
            "conversation_id": asked.conversation_id,
            "lines": {
                t: {"line_id": int(v["line_id"]), "name": str(v["name"]), "exists": True}
                for t, v in asked.lines.items()
            },
        }
    with tx() as conn:
        out = read_conversation(conn, stored)
    assert out is not None
    return {"saved": True, **out}


def ask(tx: Transaction, chapter_id: int, question: str = "", *, client: httpx.Client | None = None) -> dict[str, Any]:
    """The first question of a new conversation about the chapter."""
    q = _question(question, first=True)
    with tx() as conn:
        settings = load_settings(conn)
        current = render_chapter(conn, chapter_id)
        if current is None:
            raise ai.ExplainError(404, "chapter not found")
        prompt = settings.ai_chapter_prompt
        if not prompt.text.strip() or not prompt.model:
            raise ai.ExplainError(404, "the chapter prompt is not configured")
        errors = chapter_prompt_errors(prompt)
        if errors:
            raise ai.ExplainError(422, "; ".join(errors))
        params = ai.ConversationParams.of(prompt)
        if current.context is None:
            raise refusal("no_active_lines")
        try:
            rendered = prompts.render(prompt.text, {**current.context, "question": q})
        except TemplateError as exc:
            raise ai.ExplainError(500, f"prompt template failed to render ({type(exc).__name__})") from None
        if len(rendered) > CHAPTER_IDEA_MAX_PROMPT_CHARS:
            raise refusal("chapter_too_large")
        asked = _Asked(
            chapter_id=chapter_id,
            conversation_id=None,
            seq=0,
            question=q,
            body=ai.conversation_body(params, rendered, [], None),
            params=params,
            prompt_hash=ai.prompt_hash(prompt),
            context_hash=current.context_hash,
            rendered=rendered,
            lines=current.lines,
        )
    reply = _send(tx, asked, "chapter_too_large", client)
    return _finish(tx, asked, reply)


def follow_up(
    tx: Transaction, conversation_id: int, question: str, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """The next question of a stored conversation, built from what it stored. One follow-up at a
    time: the conversation is marked as asking before the count and cleared when the answer is
    stored or the request fails, so a second one is refused before it costs anything."""
    with tx() as conn:
        conv = _conversation(conn, conversation_id, for_call=True)
        if conv is None:
            raise ai.ExplainError(404, "conversation not found")
        turns = list(conv["turns"])
        if len(turns) >= CHAPTER_IDEA_MAX_TURNS or conv["full_reason"] is not None:
            raise refusal("conversation_full")
        q = _question(question, first=False)
        blocked = _blocked(conv["params"])
        if blocked is not None:
            raise refusal(blocked)
        params = ai.ConversationParams.stored(conv["params"])
        marked = conn.execute(
            "UPDATE chapter_idea_conversations SET asking_until = now() + make_interval(secs => %s)"
            " WHERE id = %s AND (asking_until IS NULL OR asking_until < now()) RETURNING id",
            (ASKING_SECONDS, conversation_id),
        ).fetchone()
        if marked is None:
            raise refusal("conversation_busy", 409)
        asked = _Asked(
            chapter_id=int(conv["chapter_id"]),
            conversation_id=conversation_id,
            seq=len(turns),
            question=q,
            body=ai.conversation_body(params, str(conv["rendered_prompt"]), turns, q),
            params=params,
            prompt_hash=str(conv["prompt_hash"]),
            context_hash=str(conv["context_hash"]),
            rendered=str(conv["rendered_prompt"]),
            lines=cast(Lines, conv["lines"]),
        )
    try:
        reply = _send(tx, asked, "conversation_full", client)
        return _finish(tx, asked, reply)
    finally:
        try:
            with tx() as conn:
                conn.execute(
                    "UPDATE chapter_idea_conversations SET asking_until = NULL WHERE id = %s", (conversation_id,)
                )
        except psycopg.Error as exc:  # the mark expires on its own
            _log.warning("chapter idea: the asking mark could not be cleared: %s", type(exc).__name__)
