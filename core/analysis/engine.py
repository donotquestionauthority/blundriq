"""Finding and opening Stockfish. One engine per worker process, Threads=1
(N single-threaded engines beat one N-threaded engine for the same CPU).

The engine is checked, not assumed: every result is stamped with STOCKFISH_VERSION, and
a binary of another version would stamp its results with the wrong label (a package
manager upgrade, a `latest` download). `open_engine` reads the engine's own `id name`
and refuses any version but the one the stamp names."""

from __future__ import annotations

import shutil
from pathlib import Path

import chess.engine

from core.constants import STOCKFISH_VERSION
from core.notify import OperatorError

_CANDIDATES = ("/usr/local/bin/stockfish", "/opt/homebrew/bin/stockfish", "/usr/bin/stockfish", "/usr/games/stockfish")


def find_stockfish() -> str:
    found = shutil.which("stockfish")
    if found:
        return found
    for path in _CANDIDATES:
        if Path(path).exists():
            return path
    raise FileNotFoundError(f"stockfish not found on PATH (install Stockfish {STOCKFISH_VERSION} and put it on PATH)")


def engine_version(name: str) -> str | None:
    """The major version an engine's UCI `id name` announces ("Stockfish 19 by ..." -> "19"),
    None when the name is not a Stockfish one."""
    words = name.split()
    if len(words) < 2 or words[0] != "Stockfish":
        return None
    return words[1].split(".")[0]


def check_version(engine: chess.engine.SimpleEngine) -> None:
    """Raise OperatorError unless `engine` is the Stockfish the stamp names."""
    name = str(engine.id.get("name", ""))
    if engine_version(name) != STOCKFISH_VERSION:
        raise OperatorError(
            f"the engine on PATH announces itself as {name or 'an unnamed engine'};"
            f" this version of the pipeline analyses with Stockfish {STOCKFISH_VERSION} only"
        )


def open_engine(path: str) -> chess.engine.SimpleEngine:
    engine = chess.engine.SimpleEngine.popen_uci(path)
    try:
        check_version(engine)
        engine.configure({"Threads": 1})
    except BaseException:
        engine.quit()
        raise
    return engine
