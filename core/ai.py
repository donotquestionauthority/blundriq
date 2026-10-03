"""AI explanations, with a cache and a spending cap.

Two subjects share one path. A blunder is named by `(chess_game_id, ply)` and a prompt by key;
a move in a repertoire line is named by `(line_id, ply)` and explained with the one line prompt,
plus the player's optional question. Everything else that reaches the model is read from the
database here, so the only thing the browser can put into a prompt or a cache row is that
question, and it is part of the cache key.

Three rules hold the module together:

* **One context object is both hashed and rendered** (`build_context`). Every value that can
  change the prompt is therefore part of the cache key, and nothing is derived later.
* **One parameter object is hashed, shown and sent** (`effective_params`). The provider's
  mutual-exclusion rules are applied there once, so the cache key, the dry run and the request
  body cannot disagree.
* **Only a call that reached a provider and succeeded is counted** against the hourly and
  daily caps. A cached answer is free; a failed call gives its slot back.

No connection is held while the provider is thinking: `explain` runs a short transaction to
prepare and claim, calls out with none open, and runs another to record the result.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, LiteralString, cast

import httpx
import psycopg
from jinja2 import TemplateError
from psycopg import Connection

from core import prompts, secrets
from core.chess.eligibility import analysable_sql
from core.constants import (
    AI_ADAPTIVE_THINKING_MODELS,
    AI_THINKING_ALWAYS_ON_MODELS,
    AI_THINKING_HEADROOM_TOKENS,
    AI_THINKING_MIN_BUDGET_TOKENS,
    LOCK_AI_BUDGET,
    PLAYER_ID,
)
from core.repertoire import annotations
from core.settings import AiPrompt, Settings, thinking_off_refusal
from core.settings import load as load_settings

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Long enough for a model that thinks first: its thinking shares `max_tokens` with the answer.
TIMEOUT_SECONDS = 180.0
LINE_PROMPT_KEY = "line"  # what `ai_calls.prompt_key` records for a line explanation
QUESTION_MAX_CHARS = 500

Transaction = Callable[[], AbstractContextManager[Connection[Any]]]


class ExplainError(Exception):
    """A refusal the route turns into an HTTP status. `detail` is safe to show: it never
    carries provider text, a key, or a URL."""

    def __init__(self, status: int, detail: str | dict[str, str]) -> None:
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


# --- parameters --------------------------------------------------------------------------


def provider_of(model: str) -> str:
    """The model id is a Preferences field, so the provider is read off the id rather than
    looked up in a list that would need a release per model."""
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gpt-") or re.match(r"^o\d", model):
        return "openai"
    raise ExplainError(400, f"unsupported model: {model}")


def effective_params(prompt: AiPrompt) -> dict[str, Any]:
    """What is actually sent for this prompt, after the provider's rules.

    Anthropic: thinking on forbids a prefill and an explicit temperature. A budget model needs
    room after the budget for the answer; an adaptive model takes no budget at all, and its
    thinking comes out of `max_tokens`. Thinking off must be said out loud to a model that
    thinks by default, or the whole reply is spent thinking and carries no text; a model that
    always thinks cannot be told that, so the combination is refused here, before anything
    reads the cache, claims a slot or calls out.
    OpenAI takes the system prompt, temperature and length; the rest does not exist there,
    so it is not in the hash either and toggling it cannot miss the cache.
    """
    provider = provider_of(prompt.model)
    system = prompt.system_prompt.strip()
    if provider == "openai":
        # The reasoning (o*) models refuse any temperature but their default.
        reasoning = re.match(r"^o\d", prompt.model) is not None
        return {
            "provider": provider,
            "system_prompt": system,
            "temperature": None if reasoning else prompt.temperature,
            "max_tokens": prompt.max_tokens,
        }
    if prompt.model in AI_THINKING_ALWAYS_ON_MODELS and not prompt.thinking_enabled:
        raise ExplainError(422, thinking_off_refusal(prompt.model))
    params: dict[str, Any] = {"provider": provider, "system_prompt": system}
    if prompt.thinking_enabled and prompt.model in AI_ADAPTIVE_THINKING_MODELS:
        params |= {
            "temperature": None,
            "thinking_enabled": True,
            "thinking_budget_tokens": 0,  # not sent, so not in the hash either
            "thinking_mode": "adaptive",
            "prefill": "",
            "max_tokens": prompt.max_tokens,
            "thinking": {"type": "adaptive"},
        }
    elif prompt.thinking_enabled:
        budget = max(prompt.thinking_budget_tokens, AI_THINKING_MIN_BUDGET_TOKENS)
        params |= {
            "temperature": None,
            "thinking_enabled": True,
            "thinking_budget_tokens": budget,
            "prefill": "",
            "max_tokens": max(prompt.max_tokens, budget + AI_THINKING_HEADROOM_TOKENS),
            "thinking": {"type": "enabled", "budget_tokens": budget},
        }
    else:
        params |= {
            "temperature": prompt.temperature,
            "thinking_enabled": False,
            "thinking_budget_tokens": 0,  # not sent, so not in the hash either
            "prefill": prompt.prefill.rstrip(),
            "max_tokens": prompt.max_tokens,
        }
        if prompt.model in AI_ADAPTIVE_THINKING_MODELS:
            params["thinking"] = {"type": "disabled"}
    return params


def prompt_hash(prompt: AiPrompt) -> str:
    """The cache key's prompt half: template, model and effective parameters. `thinking` is
    left out because it is a function of three keys that are in."""
    eff = effective_params(prompt)
    canonical: dict[str, Any] = {
        "text": prompt.text,
        "model": prompt.model,
        "model_provider": eff["provider"],
        "system_prompt": eff["system_prompt"],
        "temperature": eff["temperature"],
        "max_tokens": eff["max_tokens"],
    }
    if eff["provider"] == "anthropic":
        canonical |= {k: eff[k] for k in ("thinking_enabled", "thinking_budget_tokens", "prefill")}
        if "thinking_mode" in eff:  # only on the adaptive path, so every older hash is unchanged
            canonical["thinking_mode"] = eff["thinking_mode"]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def context_hash(context: dict[str, Any]) -> str:
    """The cache key's occurrence half: two blunders at one board with different moves,
    lines or ratings are different explanations."""
    return hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode()).hexdigest()


# --- context -----------------------------------------------------------------------------


def _numbered(line: str, *, white_to_move: bool, fullmove: int) -> str:
    """`Nc6 Bb5 a6` from a black-to-move position at move 12 → `12... Nc6 13. Bb5 a6`."""
    parts: list[str] = []
    white, number = white_to_move, fullmove
    for index, san in enumerate(line.split()):
        if white:
            parts.append(f"{number}. {san}")
        else:
            parts.append(f"{number}... {san}" if index == 0 else san)
            number += 1
        white = not white
    return " ".join(parts)


def _pgn_before(moves: list[str] | None, ply: int, window: int = 5) -> str:
    """The last few moves before the blunder, numbered."""
    if not moves or ply <= 0:
        return ""
    start = max(0, ply - window)
    parts: list[str] = []
    for index in range(start, min(ply, len(moves))):
        if index % 2 == 0:
            parts.append(f"{index // 2 + 1}.")
        parts.append(moves[index])
    return " ".join(parts)


def build_context(row: dict[str, Any], ply: int) -> dict[str, Any]:
    """The render-ready context: every value a string, lines numbered from the position they
    start in, and the halfmove clock zeroed in `fen` so boards that differ only by it share
    a cache row (the move number stays: the numbered lines are derived from it)."""

    def text(value: Any) -> str:
        return "" if value is None else str(value)

    fen = text(row["fen"])
    fields = fen.split()
    white_to_move = len(fields) > 1 and fields[1] == "w"
    try:
        fullmove = int(fields[5])
    except (IndexError, ValueError):
        fullmove = 1
    if len(fields) >= 6:
        fields[4] = "0"
        fen = " ".join(fields[:6])
    return {
        "move_played": text(row["move_played"]),
        "best_move": text(row["best_move"]),
        "best_line": _numbered(text(row["best_line"]), white_to_move=white_to_move, fullmove=fullmove),
        # The refutation starts after the move played: the side flips, and the move number
        # advances if that move was Black's.
        "post_blunder_line": _numbered(
            text(row["post_blunder_line"]),
            white_to_move=not white_to_move,
            fullmove=fullmove if white_to_move else fullmove + 1,
        ),
        "fen": fen,
        "phase": text(row["phase"]),
        "opening_name": text(row["opening_name"]),
        "cp_loss": text(row["centipawn_loss"]),
        "pgn_context": _pgn_before(row["moves"], ply),
        "color": text(row["player_color"]),
        "player_rating": text(row["player_rating"]),
        "classification": text(row["classification"]),
        "eval": "",
        "is_blunder": True,
    }


def _blunder_row(conn: Connection[Any], chess_game_id: int, ply: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            cast(
                LiteralString,
                f"""
            SELECT b.fen, b.move_played, b.best_move, b.best_line, b.post_blunder_line, b.centipawn_loss,
                   b.classification, b.phase, pg.player_color, pg.player_rating, cg.moves, cg.opening_name
            FROM blunders b
            JOIN player_games pg ON pg.player_id = b.player_id AND pg.chess_game_id = b.chess_game_id
            JOIN chess_games cg ON cg.id = b.chess_game_id
            WHERE b.player_id = %s AND b.chess_game_id = %s AND b.ply = %s AND {analysable_sql("cg")}
            ORDER BY b.id LIMIT 1
            """,
            ),
            (PLAYER_ID, chess_game_id, ply),
        )
        row = cur.fetchone()
    return dict(row) if row else None


# --- a move in a repertoire line -------------------------------------------------------------

_BRACKET_OPEN_RE = re.compile(r"@@StartBracket@@\s*")
_BRACKET_CLOSE_RE = re.compile(r"\s*@@EndBracket@@")
_SAN_REF_RE = re.compile(r"@@SANStart@@(.*?)@@SANEnd@@")
_SAN_STRAY_RE = re.compile(r"@@SAN(?:Start|End)@@")
_SPACES_RE = re.compile(r"[ \t]{2,}")


def _note_prose(text: str) -> str:
    """A note's text as the walk-through shows it: move references and brackets unwrapped."""
    out = _BRACKET_CLOSE_RE.sub(")", _BRACKET_OPEN_RE.sub("(", text))
    out = _SAN_STRAY_RE.sub("", _SAN_REF_RE.sub(r"\1", out))
    return _SPACES_RE.sub(" ", out).strip()


def _note_text(note: dict[str, Any] | None) -> str:
    """`"prose" (who wrote it)`, or "" for no note."""
    if not note or not str(note.get("text") or "").strip():
        return ""
    if note.get("source") == "manual":
        who = "the player's own note"
    else:
        who = ", ".join(["the author", *(str(note[k]) for k in ("author", "book_title") if note.get(k))])
    if note.get("from_chapter"):
        who += f"; from the chapter {note['from_chapter']}"
    return f'"{_note_prose(str(note["text"]))}" ({who})'


def line_context(line: dict[str, Any], ply: int, question: str) -> dict[str, Any] | None:
    """The render-ready context for the move arriving at `ply` of `line`
    (`annotations.line_with_notes`), or None when no move arrives there.

    The index contract: `positions[i].move` leaves `positions[i].fen`, and the note at
    `positions[i]` is about that move. So the move asked about is `positions[ply - 1].move`,
    the board before it `positions[ply - 1].fen`, the board after it `positions[ply].fen`, and
    the note directly on it `positions[ply - 1].annotation`. The note the walk-through was
    showing is anchored at the largest annotated index below `ply`, or else at the first one at
    or after it. A note on the final position is about no move and is labelled so."""
    positions: list[dict[str, Any]] = line["positions"]
    last = len(positions) - 1
    if not 1 <= ply <= last:
        return None
    fields = str(positions[0]["fen"]).split()
    white_first = len(fields) > 1 and fields[1] == "w"
    try:
        first_number = int(fields[5])
    except (IndexError, ValueError):
        first_number = 1

    def label(k: int) -> str:
        """The move leaving `positions[k]`, numbered; the final position has none."""
        if k >= last:
            return "the final position"
        number = first_number + (k + (0 if white_first else 1)) // 2
        san = str(positions[k]["move"])
        return f"{number}. {san}" if (k % 2 == 0) == white_first else f"{number}... {san}"

    rows: list[str] = []
    for k, position in enumerate(positions):
        rows.append(f"position {k}: {position['fen']}" + (" (final position)" if k == last else ""))
        if k < last:
            rows.append(f"   move: {label(k)}")
        note = _note_text(position.get("annotation"))
        if note:
            rows.append(f"   note on {label(k)}: {note}")

    annotated = [k for k, position in enumerate(positions) if position.get("annotation") is not None]
    before = [k for k in annotated if k < ply]
    after = [k for k in annotated if k >= ply]
    anchor = before[-1] if before else (after[0] if after else None)
    moves = " ".join(str(positions[k]["move"]) for k in range(last))
    return {
        "book_title": str(line.get("book_title") or ""),
        "chapter_title": str(line.get("chapter_title") or ""),
        "line_name": str(line.get("line_name") or ""),
        "color": str(line.get("color") or ""),
        "moves_numbered": _numbered(moves, white_to_move=white_first, fullmove=first_number),
        "line_table": "\n".join(rows),
        "move": label(ply - 1),
        "fen_before": str(positions[ply - 1]["fen"]),
        "fen_after": str(positions[ply]["fen"]),
        "direct_note": _note_text(positions[ply - 1].get("annotation")),
        "sticky_note": _note_text(positions[anchor].get("annotation")) if anchor is not None else "",
        "sticky_about": label(anchor) if anchor is not None else "",
        "question": question,
    }


# --- cache and budget --------------------------------------------------------------------


def _cached(conn: Connection[Any], fen: str, p_hash: str, c_hash: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT explanation FROM ai_explanation_cache"
            " WHERE canonical_fen = bq_canonical_fen(%s) || ' 0 1' AND prompt_hash = %s AND context_hash = %s",
            (fen, p_hash, c_hash),
        )
        row = cur.fetchone()
    return str(row["explanation"]) if row else None


def _claim(conn: Connection[Any], settings: Settings, prompt_key: str, model: str) -> int:
    """Take one slot under both caps, or raise 429 naming the cap that is full. The advisory
    lock makes count-then-insert atomic across tabs; it lasts until this transaction ends."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_AI_BUDGET, PLAYER_ID))
        cur.execute(
            "SELECT count(*) FILTER (WHERE called_at > now() - interval '1 hour') AS hour,"
            "       count(*) AS day FROM ai_calls WHERE called_at > now() - interval '1 day'"
        )
        used = cur.fetchone()
        assert used is not None
        per_hour, per_day = settings.ai_explain_max_per_hour, settings.ai_explain_max_per_day
        if per_hour and used["hour"] >= per_hour:
            raise ExplainError(429, {"window": "hourly", "message": f"Limit reached: {per_hour} AI calls per hour."})
        if per_day and used["day"] >= per_day:
            raise ExplainError(429, {"window": "daily", "message": f"Limit reached: {per_day} AI calls per day."})
        cur.execute("INSERT INTO ai_calls (prompt_key, model) VALUES (%s, %s) RETURNING id", (prompt_key, model))
        claimed = cur.fetchone()
        assert claimed is not None
        return int(claimed["id"])


# --- providers ---------------------------------------------------------------------------


def request_body(model: str, rendered: str, eff: dict[str, Any]) -> dict[str, Any]:
    """The exact JSON sent. Optional parameters are left out, not sent as null."""
    if eff["provider"] == "openai":
        messages: list[dict[str, str]] = []
        if eff["system_prompt"]:
            messages.append({"role": "system", "content": eff["system_prompt"]})
        messages.append({"role": "user", "content": rendered})
        body: dict[str, Any] = {"model": model, "messages": messages, "max_completion_tokens": eff["max_tokens"]}
        if eff["temperature"] is not None:
            body["temperature"] = float(eff["temperature"])
        return body
    messages = [{"role": "user", "content": rendered}]
    if eff["prefill"]:
        messages.append({"role": "assistant", "content": eff["prefill"]})
    body = {"model": model, "messages": messages, "max_tokens": eff["max_tokens"]}
    if "thinking" in eff:
        body["thinking"] = eff["thinking"]
    if eff["temperature"] is not None:
        body["temperature"] = float(eff["temperature"])
    if eff["system_prompt"]:
        body["system"] = eff["system_prompt"]
    return body


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int | None
    output_tokens: int | None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _failure(response: httpx.Response) -> ExplainError:
    """Status and the provider's error *type* only: the message can echo the request."""
    kind = ""
    try:
        error = response.json().get("error", {})
        candidate = str(error.get("type") or error.get("code") or "")
        if re.fullmatch(r"[a-z_]{1,40}", candidate):
            kind = f", {candidate}"
    except (ValueError, AttributeError):
        pass
    return ExplainError(502, f"AI provider refused the call (HTTP {response.status_code}{kind})")


def call_provider(model: str, rendered: str, eff: dict[str, Any], client: httpx.Client) -> Reply:
    body = request_body(model, rendered, eff)
    try:
        if eff["provider"] == "openai":
            headers = {"Authorization": f"Bearer {secrets.openai().api_key}"}
            response = client.post(OPENAI_URL, json=body, headers=headers)
        else:
            headers = {"x-api-key": secrets.anthropic().api_key, "anthropic-version": ANTHROPIC_VERSION}
            response = client.post(ANTHROPIC_URL, json=body, headers=headers)
    except secrets.MissingSecret as exc:
        raise ExplainError(503, f"AI explanations are not configured for {eff['provider']} models") from exc
    except httpx.HTTPError as exc:
        raise ExplainError(502, f"AI provider could not be reached ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise _failure(response)
    try:
        data: dict[str, Any] = response.json()
        usage: dict[str, Any]
        if eff["provider"] == "openai":
            text = str(data["choices"][0]["message"]["content"] or "").strip()
            cut_off = data["choices"][0].get("finish_reason") == "length"
            usage = data.get("usage") or {}
            tokens = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
        else:
            text = "".join(str(b["text"]) for b in data["content"] if b.get("type") == "text").strip()
            if text and eff["prefill"]:
                text = eff["prefill"] + text
            cut_off = data.get("stop_reason") == "max_tokens"
            usage = data.get("usage") or {}
            tokens = (usage.get("input_tokens"), usage.get("output_tokens"))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ExplainError(502, "AI provider sent a reply that could not be read") from None
    if not text:
        raise ExplainError(502, "AI provider sent a reply with no text (raise max tokens?)")
    # A model that thinks spends `max_tokens` on thinking too; a reply cut off there is not an
    # answer, and caching it would serve the fragment for ever.
    if cut_off:
        raise ExplainError(502, "AI provider's reply was cut off at max tokens (raise max tokens)")
    return Reply(text, _int(tokens[0]), _int(tokens[1]))


# --- the shared path ---------------------------------------------------------------------


def prompt_labels(settings: Settings) -> list[dict[str, str]]:
    """The prompts a blunder can be explained with, default first."""
    usable = [(k, p) for k, p in sorted(settings.ai_prompts.items()) if p.text.strip() and p.model]
    usable.sort(key=lambda kp: kp[0] != settings.ai_default_prompt)
    return [{"key": k, "label": p.label or k.upper(), "model": p.model} for k, p in usable]


@dataclass(frozen=True)
class _Prepared:
    """Everything one explanation needs once its transaction has closed."""

    prompt: AiPrompt
    calls_key: str  # what `ai_calls.prompt_key` records
    label: str
    fen: str  # the board the cache row is keyed by
    context: dict[str, Any]
    eff: dict[str, Any]
    rendered: str


def _prepare(prompt: AiPrompt, calls_key: str, label: str, fen: str, context: dict[str, Any]) -> _Prepared:
    """Parameters first (an unsupported model or combination is refused before anything is
    read or claimed), then the template (a broken one costs nothing)."""
    eff = effective_params(prompt)
    try:
        rendered = prompts.render(prompt.text, context)
    except TemplateError as exc:
        raise ExplainError(500, f"prompt template failed to render ({type(exc).__name__})") from None
    return _Prepared(prompt, calls_key, label, fen, context, eff, rendered)


def _run(
    tx: Transaction,
    prepare: Callable[[Connection[Any], Settings], _Prepared],
    *,
    dry_run: bool,
    client: httpx.Client | None,
) -> dict[str, Any]:
    """Order on a cache miss: prepare → claim a slot → call with no transaction open → record
    the tokens and cache the answer, or give the slot back. `dry_run` returns what would be
    sent and touches neither the cache, the caps nor the provider."""
    with tx() as conn:
        settings = load_settings(conn)
        prep = prepare(conn, settings)
        model = prep.prompt.model
        if dry_run:
            return {
                "dry_run": True,
                "model": model,
                "prompt_label": prep.label,
                "rendered_prompt": prep.rendered,
                "request_body": request_body(model, prep.rendered, prep.eff),
                **prep.eff,
            }
        p_hash, c_hash = prompt_hash(prep.prompt), context_hash(prep.context)
        cached = _cached(conn, prep.fen, p_hash, c_hash)
        if cached is not None:
            return {"explanation": cached, "cached": True, "model": model, "prompt_label": prep.label}
        claim_id = _claim(conn, settings, prep.calls_key, model)

    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as own:
                reply = call_provider(model, prep.rendered, prep.eff, own)
        else:
            reply = call_provider(model, prep.rendered, prep.eff, client)
    except BaseException:
        with tx() as conn:
            conn.execute("DELETE FROM ai_calls WHERE id = %s", (claim_id,))
        raise

    try:
        with tx() as conn:
            _record(conn, claim_id, reply, prep.fen, p_hash, c_hash, model, prep.label, prep.rendered)
    except psycopg.Error:
        pass  # the answer was paid for; failing to cache it must not lose it
    return {"explanation": reply.text, "cached": False, "model": model, "prompt_label": prep.label}


# --- the two entry points ----------------------------------------------------------------


def explain(
    tx: Transaction,
    chess_game_id: int,
    ply: int,
    prompt_key: str,
    *,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Explain the blunder at `(chess_game_id, ply)` with the named prompt. `tx` opens a
    transaction (`core.db.transaction`)."""

    def prepare(conn: Connection[Any], settings: Settings) -> _Prepared:
        prompt = settings.ai_prompts.get(prompt_key)
        if prompt is None or not prompt.text.strip():
            raise ExplainError(404, f"prompt {prompt_key!r} is not configured")
        row = _blunder_row(conn, chess_game_id, ply)
        if row is None:
            raise ExplainError(404, "blunder not found")
        label = prompt.label or prompt_key.upper()
        return _prepare(prompt, prompt_key, label, row["fen"], build_context(row, ply))

    return _run(tx, prepare, dry_run=dry_run, client=client)


def explain_line(
    tx: Transaction,
    line_id: int,
    ply: int,
    question: str = "",
    *,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Explain the move arriving at `ply` of the repertoire line `line_id` with
    `ai_line_prompt`, given the whole line and every note on it. `question` is trimmed; an
    empty one asks why the move matters, and is the answer the cache keeps for free."""
    question = question.strip()
    if len(question) > QUESTION_MAX_CHARS:
        raise ExplainError(422, f"the question is longer than {QUESTION_MAX_CHARS} characters")

    def prepare(conn: Connection[Any], settings: Settings) -> _Prepared:
        prompt = settings.ai_line_prompt
        if not prompt.text.strip() or not prompt.model:
            raise ExplainError(404, "the line prompt is not configured")
        line = annotations.line_with_notes(conn, line_id)
        context = line_context(line, ply, question) if line is not None else None
        if line is None or context is None:
            raise ExplainError(404, "no move arrives at that ply of the line")
        label = prompt.label or "Line explanation"
        return _prepare(prompt, LINE_PROMPT_KEY, label, context["fen_after"], context)

    return _run(tx, prepare, dry_run=dry_run, client=client)


def _record(
    conn: Connection[Any],
    claim_id: int,
    reply: Reply,
    fen: str,
    p_hash: str,
    c_hash: str,
    model: str,
    label: str,
    rendered: str,
) -> None:
    conn.execute(
        "UPDATE ai_calls SET input_tokens = %s, output_tokens = %s WHERE id = %s",
        (reply.input_tokens, reply.output_tokens, claim_id),
    )
    conn.execute(
        "INSERT INTO ai_explanation_cache"
        " (fen, prompt_hash, context_hash, model, prompt_label, rendered_prompt, explanation)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (canonical_fen, prompt_hash, context_hash) DO NOTHING",
        (fen, p_hash, c_hash, model, label, rendered, reply.text),
    )
