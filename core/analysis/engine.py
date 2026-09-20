"""Finding and opening Stockfish. One engine per worker process, Threads=1
(N single-threaded engines beat one N-threaded engine for the same CPU)."""

from __future__ import annotations

import shutil
from pathlib import Path

import chess.engine

_CANDIDATES = ("/usr/local/bin/stockfish", "/opt/homebrew/bin/stockfish", "/usr/bin/stockfish", "/usr/games/stockfish")


def find_stockfish() -> str:
    found = shutil.which("stockfish")
    if found:
        return found
    for path in _CANDIDATES:
        if Path(path).exists():
            return path
    raise FileNotFoundError(
        "stockfish not found on PATH (brew install stockfish, or ~/.local/bin/stockfish on the Dell)"
    )


def open_engine(path: str) -> chess.engine.SimpleEngine:
    engine = chess.engine.SimpleEngine.popen_uci(path)
    engine.configure({"Threads": 1})
    return engine
