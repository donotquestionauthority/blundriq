"""The code stamp: a digest of every module the review computation depends on.

A stored review generation or snapshot is proof of the current code only while the code
that would compute it now is the code that computed it. The dependency set is not a
list anyone maintains: starting from every module of `core.review`, every `import` and
`from … import` is followed, transitively, to each module under the `core` package it
reaches (the shared SEE implementation in `core.analysis.motifs`, the board helpers, the
eligibility fragments, the constants, the settings model …), and the source of each is
digested. The whole of `core/constants.py` is in whatever the walk finds, so an unprefixed
constant the detector consumes counts; python-chess parses the moves, so its version is in.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib
import importlib.util
import inspect
import pkgutil

import chess

ROOT_PACKAGE = "core"


def _is_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ImportError, ValueError:
        return False


def _imports_of(module_name: str) -> set[str]:
    """The `core.*` modules a module's source imports: `import a.b` names `a.b`; `from a import b`
    names `a` and, when `a.b` is a module rather than an attribute, `a.b` too."""
    source = inspect.getsource(importlib.import_module(module_name))
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            submodules = (f"{node.module}.{alias.name}" for alias in node.names)
            found.update(name for name in submodules if _is_module(name))
    return {n for n in found if n == ROOT_PACKAGE or n.startswith(ROOT_PACKAGE + ".")}


def review_modules() -> list[str]:
    """`core.review` and every module in it, the walk's starting set."""
    import core.review

    return ["core.review", *sorted(f"core.review.{m.name}" for m in pkgutil.iter_modules(core.review.__path__))]


@functools.cache
def dependency_closure() -> tuple[str, ...]:
    """Every `core.*` module reachable by imports from `core.review`, sorted; cycles are
    visited once. `core.constants` is always in it, whether or not an import reaches it."""
    seen: set[str] = set()
    pending = [*review_modules(), f"{ROOT_PACKAGE}.constants"]
    while pending:
        name = pending.pop()
        if name in seen or not _is_module(name):
            continue
        seen.add(name)
        pending.extend(_imports_of(name) - seen)
    return tuple(sorted(seen))


@functools.cache
def code_stamp() -> str:
    """The digest: each module of the closure by name and source, then the chess library's version."""
    digest = hashlib.sha256()
    for name in dependency_closure():
        digest.update(f"{name}\n".encode())
        digest.update(inspect.getsource(importlib.import_module(name)).encode())
    digest.update(f"chess={chess.__version__}".encode())
    return digest.hexdigest()[:16]
