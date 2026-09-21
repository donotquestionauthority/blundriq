"""The only place the process reads its environment.

Every secret the application needs is declared here by name. Reading is
strict: a missing variable raises at import of `get()` time with the
variable's name, and the process does not continue. There are no defaults,
no fallbacks, no "backup" values, and nothing here is ever read from a file
or a database row. The lint rule in pyproject.toml bans `os.environ` and
`os.getenv` everywhere else, and tests/test_secrets_policy.py fails if this
file ever contains a string literal on the right-hand side of a read.

Which variables are required depends on the role of the process:

    DATABASE_URL       api, pipeline     Postgres DSN (pooler)
    SESSION_SECRET     api               signs the login cookie
    PASSWORD_HASH      api               bcrypt hash of the one user's password
    ANTHROPIC_API_KEY  api               AI explanations with a claude-* model; read when one is used
    OPENAI_API_KEY     api               AI explanations with an OpenAI model; read when one is used
    RESEND_API_KEY     pipeline          ops alerts
    ALERT_EMAIL        pipeline          where ops alerts go
    ALERT_FROM         pipeline          the sender address (on the Resend-verified domain)
    ORACLE_DATABASE_URL  migrate, tools/oracle   the restored old database (local Postgres)

Local development sets them in the shell (e.g. `set -a; source ~/.blundriq-secrets/blundriq.env`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class MissingSecret(RuntimeError):
    """Raised when a required environment variable is absent or empty."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise MissingSecret(f"required environment variable {name} is not set")
    return value


@dataclass(frozen=True)
class DatabaseSecrets:
    database_url: str


@dataclass(frozen=True)
class ApiSecrets:
    database_url: str
    session_secret: str
    password_hash: str


@dataclass(frozen=True)
class ProviderSecret:
    api_key: str


@dataclass(frozen=True)
class AlertSecrets:
    resend_api_key: str
    alert_email: str  # recipient
    alert_from: str  # sender on the Resend-verified domain


@dataclass(frozen=True)
class OracleSecrets:
    oracle_database_url: str  # the restored old database, for `pipeline migrate` and tools/oracle


def database() -> DatabaseSecrets:
    return DatabaseSecrets(database_url=_require("DATABASE_URL"))


def api() -> ApiSecrets:
    return ApiSecrets(
        database_url=_require("DATABASE_URL"),
        session_secret=_require("SESSION_SECRET"),
        password_hash=_require("PASSWORD_HASH"),
    )


def anthropic() -> ProviderSecret:
    return ProviderSecret(api_key=_require("ANTHROPIC_API_KEY"))


def openai() -> ProviderSecret:
    return ProviderSecret(api_key=_require("OPENAI_API_KEY"))


def alerts() -> AlertSecrets:
    return AlertSecrets(
        resend_api_key=_require("RESEND_API_KEY"),
        alert_email=_require("ALERT_EMAIL"),
        alert_from=_require("ALERT_FROM"),
    )


def oracle() -> OracleSecrets:
    return OracleSecrets(oracle_database_url=_require("ORACLE_DATABASE_URL"))
