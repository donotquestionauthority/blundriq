"""Default AI explanation prompts. The two Claude defaults use a model that always thinks, and
its thinking shares `max_tokens` with the answer, so both have thinking on and a `max_tokens`
that leaves room for it.

These are content, not knobs, so they live here rather than inline in core/settings.py;
they are still editable on the Preferences page because `ai_prompts` is a settings field
whose defaults come from this module.

A prompt's `text` is a Jinja template over the explanation context (core/ai.py), so one
prompt can branch on it ({% if is_blunder %}). Templates are written on the Preferences page,
so they are compiled and rendered in a sandbox: no attribute or call escapes, no filters or
globals of ours, and a variable the context does not define raises instead of rendering as
nothing. Autoescape is off because the output is a prompt, not HTML, and escaping would
corrupt FENs and SAN.
"""

from __future__ import annotations

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

_ENV = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)


def compile_template(text: str) -> None:
    """Raise `jinja2.TemplateSyntaxError` if `text` is not a valid template. Undefined
    variables cannot be caught here; they are a render-time error."""
    _ENV.from_string(text)


def render(text: str, context: dict[str, object]) -> str:
    """Render a prompt template. Raises a `jinja2.TemplateError` on any problem."""
    return _ENV.from_string(text.strip()).render(**context)


DEFAULT_PROMPTS: dict[str, dict[str, object]] = {
    "a": {
        "label": "Explain This Position",
        "model": "claude-opus-5-5",
        "system_prompt": 'You are a chess coach explaining a specific move to an intermediate club player.\n\nImportant rules:\n-Start your response immediately — no preamble, no introductory sentence. Write in plain prose, EXCEPT for the bold section header(s) requested below. Do not add any other markdown, headers, or section labels.\n-You MUST Start with the answer - do not restate the FEN or say anything like "Let me analyze this position" or any paraphrase of that - NO INTROS\n- Base everything ONLY on the position. Do not invent threats or mention pieces/squares that are not clearly involved.\n- Focus only on what changes immediately after the move.\n- Avoid vague terms like "initiative", "pressure", or "better coordination" unless you tie them to a concrete threat.\n- If a piece is hanging, say what wins it. If there is a tactic, name it directly (fork, pin, attack, etc).',
        "text": 'Position FEN: {{ fen }}\nPlayer color: {{ color }}\n{% if is_blunder %}Move played: {{ move_played }}\nCentipawn loss: {{ cp_loss }}\n\nTask:\n\nIn 1 sentence, explain the exact tactical or concrete reason {{ move_played }} is a mistake in this position. Use Best Line After Blunder: {{ post_blunder_line }} to support your explanation.  Start this section with a bold header "{{ move_played }} was incorrect because"\n\ninsert an empty line in between\n\nThen in 1–2 sentences, explain what {{ best_move }} does better, referencing the continuation best-line:{{ best_line }} if helpful. Start this section with a bold header "{{ best_move }} works because "\n\nBe specific and concrete, and concise. No filler.{% else %}Task:\n\nIn 1–2 sentences, explain why {{ best_move }} is a strong move in this position, referencing the continuation best-line: {{ best_line }} if helpful. Describe only the merits of {{ best_move }} — do not mention or evaluate any other move. Start this section with this exact header wrapped in double asterisks: **{{ best_move }} is the better move because**\n\nBe specific and concrete, and concise. No filler.{% endif %}',
        "temperature": None,
        "thinking_enabled": True,
        "thinking_budget_tokens": 2048,
        "prefill": "",
        "max_tokens": 8192,
    },
    "b": {
        "label": "Explain This Position (plain)",
        "model": "claude-opus-5-5",
        "system_prompt": 'You are a chess coach explaining a specific mistake to an intermediate club player.\n\nImportant rules:\n-Start your response immediately — no preamble, no introductory sentence. Do not use markdown headers, bold text, or section labels unless explicitly asked to. Write in plain prose.\n-You MUST Start with the answer - do not restate the FEN or say anything like "Let me analyze this position" or any paraphrase of that - NO INTROS\n- Base everything ONLY on the position. Do not invent threats or mention pieces/squares that are not clearly involved.\n- Focus only on what changes immediately after the move.\n- Avoid vague terms like "initiative", "pressure", or "better coordination" unless you tie them to a concrete threat.\n- If a piece is hanging, say what wins it. If there is a tactic, name it directly (fork, pin, attack, etc).',
        "text": 'Position FEN: {{ fen }}\nPlayer color: {{ color }}\nMove played: {{ move_played }}\nCentipawn loss: {{ cp_loss }}\n\nTask:\n\nIn 1 sentence, explain the exact tactical or concrete reason {{ move_played }} is a mistake in this position. Use Best Line After Blunder: {{ post_blunder_line }} to support your explanation.  Start this section with a bold header "{{ move_played }} was incorrect because"\n\ninsert an empty line in between\n\nThen in 1–2 sentences, explain what {{ best_move }} does better, referencing the continuation best-line:{{ best_line }} if helpful. Start this section with a bold header "{{ best_move }} works because "\n\nBe specific and concrete, and concise. No filler.',
        "temperature": None,
        "thinking_enabled": True,
        "thinking_budget_tokens": 2048,
        "prefill": "",
        "max_tokens": 8192,
    },
    "c": {
        "label": "openAI",
        "model": "gpt-4o",
        "system_prompt": 'You are a chess coach explaining a specific mistake to an intermediate club player.\n\nImportant rules:\n-Start your response immediately — no preamble, no introductory sentence. Do not use markdown headers, bold text, or section labels unless explicitly asked to. Write in plain prose.\n-You MUST Start with the answer - do not restate the FEN or say anything like "Let me analyze this position" or any paraphrase of that - NO INTROS\n- Base everything ONLY on the position. Do not invent threats or mention pieces/squares that are not clearly involved.\n- Focus only on what changes immediately after the move.\n- Avoid vague terms like "initiative", "pressure", or "better coordination" unless you tie them to a concrete threat.\n- If a piece is hanging, say what wins it. If there is a tactic, name it directly (fork, pin, attack, etc).',
        "text": 'Position FEN: {{ fen }}\nPlayer color: {{ color }}\nMove played: {{ move_played }}\nCentipawn loss: {{ cp_loss }}\n\nTask:\n\nIn 1 sentence, explain the exact tactical or concrete reason {{ move_played }} is a mistake in this position. Use Best Line After Blunder: {{ post_blunder_line }} to support your explanation.  Start this section with a bold header "{{ move_played }} was incorrect because"\n\ninsert an empty line in between\n\nThen in 1–2 sentences, explain what {{ best_move }} does better, referencing the continuation best-line:{{ best_line }} if helpful. Start this section with a bold header "{{ best_move }} works because "\n\nBe specific and concrete, and concise. No filler.',
        "temperature": 0.3,
        "thinking_enabled": False,
        "thinking_budget_tokens": 2048,
        "prefill": "",
    },
}


# The walk-through's "why does this move matter" prompt (core/ai.py `explain_line`). Its context
# is one repertoire line: the header, the numbered moves, a table with one row per position (the
# board, the move played from it, and the note about that move), the move asked about with the
# boards either side of it, the note directly on it, the note the walk-through was showing, and
# the player's question. Every value is a string; an empty one means "none".
LINE_PROMPT: dict[str, object] = {
    "label": "Explain this move (line)",
    "model": "claude-opus-5-5",
    "system_prompt": (
        "You are a chess coach explaining an opening course author's idea to an intermediate club player"
        " who is studying the line below and does not understand one move.\n\n"
        "Rules:\n"
        "- Start with the answer: no preamble, no restating the position. Plain prose; no headers.\n"
        "- If the player asked a question, answer it first.\n"
        "- Name concrete squares, pieces and threats. The FENs are the authority on where every piece"
        " stands: check them instead of guessing.\n"
        "- Say what goes wrong if the move is not played: the concrete reply or plan it prevents.\n"
        "- Where the author's note is terse, explain what the author means. Never contradict the note"
        " without saying explicitly that you disagree and why.\n"
        "- Keep it to a few short paragraphs."
    ),
    "text": (
        "Course: {{ book_title }} / {{ chapter_title }} / {{ line_name }}\n"
        "I play {{ color }}.\n\n"
        "The whole line: {{ moves_numbered }}\n\n"
        "Position by position. Each row is the board before a move, the move played from it, and the"
        " note about that move:\n"
        "{{ line_table }}\n\n"
        "The move I am asking about: {{ move }}\n"
        "Board before it: {{ fen_before }}\n"
        "Board after it: {{ fen_after }}\n"
        "{% if direct_note %}The note on this move: {{ direct_note }}\n"
        "{% else %}There is no note on this move itself.\n"
        "{% endif %}"
        "{% if sticky_note and sticky_about != move %}"
        "The note I was reading when I got stuck was written about {{ sticky_about }}: {{ sticky_note }}\n"
        "{% endif %}"
        "\n"
        "{% if question %}My question: {{ question }}\n\n"
        "Answer my question first, then explain"
        "{% else %}Explain{% endif %}"
        " why {{ move }} is critical in this line: what it achieves, what it prevents, and what goes"
        " wrong if it is not played."
    ),
    "temperature": None,
    "thinking_enabled": True,
    "thinking_budget_tokens": 2048,
    "prefill": "",
    "max_tokens": 16000,
}


# The "ask about this position" prompt (core/ai.py `ask_review`, `ask_explore`). Its context is
# one board and the engine's verdict on it (the stored analysis on Review, the in-browser engine
# in Explore), what the game says about the board when there is one, the line explored from the
# seed when there is one, the move the question names with the engine's reply to it, and the
# question. Every value is a string, empty meaning "none", except the two agreement flags, which
# are booleans: the engine's final choice can differ from the head of the last line it scored.
ASK_PROMPT: dict[str, object] = {
    "label": "Ask about this position",
    "model": "claude-opus-5-5",
    "system_prompt": (
        "You are a chess coach answering an intermediate club player's question about one position."
        " An engine's evaluation, best move and principal variation for this position are given; they"
        " are the authority on what is best, and the FEN is the authority on where every piece stands —"
        " check it rather than guessing.\n\n"
        "Rules:\n"
        "- Start with the answer: no preamble, no restating the position. Plain prose; no headers.\n"
        "- Answer the question asked. If it is about a move other than the engine's, say concretely what"
        " goes wrong after it — the engine's reply to that move is given when the player named one, or"
        " the fact that it ends the game; use that — and what the engine's own line gains instead: the"
        " threat, the piece won, the square taken, the plan prevented.\n"
        "- Name squares and pieces. Do not invent threats, and do not mention pieces or squares that are"
        " not involved.\n"
        '- Avoid "initiative", "pressure" or "coordination" unless tied to a concrete threat in the line'
        " given.\n"
        "- If the engine's line is too short to settle the question, say so rather than guessing beyond"
        " it.\n"
        "- Keep it to a few short paragraphs."
    ),
    "text": (
        "Position (FEN): {{ fen }}\n"
        "I play {{ color }}; {{ side_to_move }} to move.\n"
        "{% if opening_name %}Opening: {{ opening_name }}\n{% endif %}"
        "{% if moves_before %}The moves before this position: {{ moves_before }}\n{% endif %}"
        "{% if explored_moves %}From the position I was reviewing ({{ seed_fen }}) I have played out:"
        " {{ explored_moves }}\n{% endif %}"
        "{% if move_played %}In the game, {{ played_by }} played {{ move_played }} here"
        "{% if classification %} ({{ classification }}, {{ cp_loss }} centipawns lost){% endif %}.\n{% endif %}"
        "{% if book_move %}My repertoire's move here is {{ book_move }}.\n{% endif %}"
        "\n"
        "Engine ({{ engine_source }}): evaluation {{ eval }}; best move {{ best_move }}"
        "{% if line_agrees %}; line {{ best_line }}"
        "{% else %}. The last line it scored, at that evaluation, was {{ best_line }}; it finalised on"
        " {{ best_move }}.{% endif %}\n"
        "{% if alt_outcome %}\nThe move I am asking about: {{ alt_move }}. It ends the game: {{ alt_outcome }}.\n"
        "{% elif alt_move %}\nThe move I am asking about: {{ alt_move }}. After it the engine (in-browser"
        " Stockfish at depth {{ alt_depth }}) gives {{ alt_eval }}"
        "{% if alt_line_agrees %} and replies {{ alt_line }}"
        "{% else %}; the last line it scored was {{ alt_line }} and it finalised on the reply {{ alt_best }}"
        "{% endif %}\n{% endif %}"
        "\nMy question: {{ question }}"
    ),
    "temperature": None,
    "thinking_enabled": True,
    "thinking_budget_tokens": 2048,
    "prefill": "",
    "max_tokens": 16000,
}
