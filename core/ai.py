"""AI explanations of a blunder, with a cache and a spending cap.

The request names a blunder by `(chess_game_id, ply)` and a prompt by key. Everything that
reaches the model is read from the database here, so nothing the browser sends can shape a
prompt or a cache row.

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
from typing import Any

import httpx
import psycopg
from jinja2 import TemplateError
from psycopg import Connection

from core import prompts, secrets
from core.constants import (
    AI_ADAPTIVE_THINKING_MODELS,
    AI_THINKING_HEADROOM_TOKENS,
    AI_THINKING_MIN_BUDGET_TOKENS,
    LOCK_AI_BUDGET,
    PLAYER_ID,
)
from core.settings import AiPrompt, Settings
from core.settings import load as load_settings

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT_SECONDS = 90.0

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

    Anthropic: thinking on forbids a prefill and an explicit temperature, and needs room
    after the budget for the answer. Thinking off must be said out loud to a model that
    thinks by default, or the whole reply is spent thinking and carries no text.
    OpenAI takes the system prompt, temperature and length; the rest does not exist there,
    so it is not in the hash either and toggling it cannot miss the cache.
    """
    provider = provider_of(prompt.model)
    system = prompt.system_prompt.strip()
    if provider == "openai":
        return {
            "provider": provider,
            "system_prompt": system,
            "temperature": prompt.temperature,
            "max_tokens": prompt.max_tokens,
        }
    params: dict[str, Any] = {"provider": provider, "system_prompt": system}
    if prompt.thinking_enabled:
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
            "thinking_budget_tokens": prompt.thinking_budget_tokens,
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
            """
            SELECT b.fen, b.move_played, b.best_move, b.best_line, b.post_blunder_line, b.centipawn_loss,
                   b.classification, b.phase, pg.player_color, pg.player_rating, cg.moves, cg.opening_name
            FROM blunders b
            JOIN player_games pg ON pg.player_id = b.player_id AND pg.chess_game_id = b.chess_game_id
            JOIN chess_games cg ON cg.id = b.chess_game_id
            WHERE b.player_id = %s AND b.chess_game_id = %s AND b.ply = %s
            ORDER BY b.id LIMIT 1
            """,
            (PLAYER_ID, chess_game_id, ply),
        )
        row = cur.fetchone()
    return dict(row) if row else None


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
            usage = data.get("usage") or {}
            tokens = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
        else:
            text = "".join(str(b["text"]) for b in data["content"] if b.get("type") == "text").strip()
            if text and eff["prefill"]:
                text = eff["prefill"] + text
            usage = data.get("usage") or {}
            tokens = (usage.get("input_tokens"), usage.get("output_tokens"))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ExplainError(502, "AI provider sent a reply that could not be read") from None
    if not text:
        raise ExplainError(502, "AI provider sent a reply with no text (raise max tokens?)")
    return Reply(text, _int(tokens[0]), _int(tokens[1]))


# --- the one entry point -----------------------------------------------------------------


def prompt_labels(settings: Settings) -> list[dict[str, str]]:
    """The prompts a blunder can be explained with, default first."""
    usable = [(k, p) for k, p in sorted(settings.ai_prompts.items()) if p.text.strip() and p.model]
    usable.sort(key=lambda kp: kp[0] != settings.ai_default_prompt)
    return [{"key": k, "label": p.label or k.upper(), "model": p.model} for k, p in usable]


def explain(
    tx: Transaction,
    chess_game_id: int,
    ply: int,
    prompt_key: str,
    *,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Explain the blunder at `(chess_game_id, ply)` with the named prompt.

    `tx` opens a transaction (`core.db.transaction`). Order on a cache miss: render (a broken
    template costs nothing) → claim a slot → call with no transaction open → record the
    tokens and cache the answer, or give the slot back. `dry_run` returns what would be
    sent and touches neither the cache, the caps nor the provider.
    """
    with tx() as conn:
        settings = load_settings(conn)
        prompt = settings.ai_prompts.get(prompt_key)
        if prompt is None or not prompt.text.strip():
            raise ExplainError(404, f"prompt {prompt_key!r} is not configured")
        row = _blunder_row(conn, chess_game_id, ply)
        if row is None:
            raise ExplainError(404, "blunder not found")
        eff = effective_params(prompt)
        context = build_context(row, ply)
        try:
            rendered = prompts.render(prompt.text, context)
        except TemplateError as exc:
            raise ExplainError(500, f"prompt template failed to render ({type(exc).__name__})") from None
        label = prompt.label or prompt_key.upper()
        if dry_run:
            return {"dry_run": True, "model": prompt.model, "prompt_label": label, "rendered_prompt": rendered, **eff}
        p_hash, c_hash = prompt_hash(prompt), context_hash(context)
        cached = _cached(conn, row["fen"], p_hash, c_hash)
        if cached is not None:
            return {"explanation": cached, "cached": True, "model": prompt.model, "prompt_label": label}
        claim_id = _claim(conn, settings, prompt_key, prompt.model)

    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as own:
                reply = call_provider(prompt.model, rendered, eff, own)
        else:
            reply = call_provider(prompt.model, rendered, eff, client)
    except BaseException:
        with tx() as conn:
            conn.execute("DELETE FROM ai_calls WHERE id = %s", (claim_id,))
        raise

    try:
        with tx() as conn:
            _record(conn, claim_id, reply, row["fen"], p_hash, c_hash, prompt.model, label, rendered)
    except psycopg.Error:
        pass  # the answer was paid for; failing to cache it must not lose it
    return {"explanation": reply.text, "cached": False, "model": prompt.model, "prompt_label": label}


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
