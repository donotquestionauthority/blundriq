"""The 'no backup password in code' rule, enforced.

1. core/secrets.py is the only module that reads the environment (ruff also
   enforces this; this test is the belt to its braces and runs even if ruff
   configuration drifts).
2. Inside core/secrets.py, no environment read has a string-literal argument
   and no read has a default: every secret is looked up by a name passed in,
   through `_require`, and a missing one raises.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SECRETS = REPO / "core" / "secrets.py"
ALLOWED_ENV_READERS = {SECRETS, REPO / "tests" / "conftest.py"}


def _env_reads(tree: ast.AST) -> list[ast.AST]:
    """Every os.environ[...] / os.environ.get(...) / os.getenv(...) node."""
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
            found.append(node)
        if isinstance(node, ast.Call):
            target = ast.unparse(node.func)
            if target in {"os.environ.get", "os.getenv"}:
                found.append(node)
    return found


def _python_files() -> list[Path]:
    files: list[Path] = []
    for folder in ("core", "api", "pipeline", "tests"):
        files.extend((REPO / folder).rglob("*.py"))
    return files


def test_only_secrets_module_reads_environment() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if path in ALLOWED_ENV_READERS:
            continue
        tree = ast.parse(path.read_text())
        if _env_reads(tree):
            offenders.append(str(path.relative_to(REPO)))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "dotenv":
                offenders.append(f"{path.relative_to(REPO)} (dotenv)")
    assert not offenders, f"environment reads outside core/secrets.py: {offenders}"


def test_secrets_module_has_no_literals_or_defaults() -> None:
    tree = ast.parse(SECRETS.read_text())
    for node in _env_reads(tree):
        if isinstance(node, ast.Subscript):
            assert not isinstance(node.slice, ast.Constant), "literal key in os.environ[...]"
        elif isinstance(node, ast.Call):
            assert len(node.args) == 1 and not node.keywords, "environment read with a default value"
            assert not isinstance(node.args[0], ast.Constant), "literal key in environment read"
    # No string literal anywhere in the module may look like a secret value.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            assert not any(v.startswith(p) for p in ("postgres://", "postgresql://", "sk-", "re_", "http")), (
                f"suspicious literal in core/secrets.py: {v[:20]}…"
            )
