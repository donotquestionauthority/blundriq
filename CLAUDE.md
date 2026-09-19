# BlundrIQ Personal

A single-user chess improvement tool. It imports Rob's games from Chess.com and Lichess, runs Stockfish over them, and turns the results into: recurring **blunders**, **deviations** from a declared opening repertoire, generated **puzzles** with spaced repetition (own games + a Lichess corpus), opponent **scouting**, and a game **review**. One user, `player_id = 1`, always.

## Stack and where it runs

Python 3.11, FastAPI, psycopg 3, pydantic 2 (`core/`, `api/`, `pipeline/`) · React + Vite + TypeScript + Tailwind (`ui/`) · Postgres on Supabase (one role, no RLS, Data API disabled) · API on Render (repo root is the service root) · UI on Vercel (root `ui/`) · pipeline on GitHub Actions hourly, Stockfish 18 depth 18 · heavy reprocessing on the Dell with the same `pipeline` CLI.

## Run and test

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # once
bash tools/hooks/install.sh                                  # once: gitleaks (>= 8.24; CI pins 8.24.3) pre-commit + pre-push
export TEST_DATABASE_URL=postgresql://localhost/blundriq_test   # any scratch Postgres
.venv/bin/ruff check . && .venv/bin/pyright && .venv/bin/pytest
cd ui && npm ci && npm run check && npm test                 # UI
.venv/bin/uvicorn api.main:app --reload                      # API (needs the env vars in core/secrets.py)
.venv/bin/pipeline db init | db upgrade | settings show      # database and settings CLI (SQL lives in core/sql/)
```

## Hard rules

1. **Secrets are read in exactly one place, `core/secrets.py`, and fail closed.** No defaults, no fallbacks, no "backup" values, nothing read from a file or a table. Ruff bans `os.environ`/`os.getenv` elsewhere; `tests/test_secrets_policy.py` bans literals inside it.
2. **This repo is public.** No DSNs, keys, emails, AWS/Supabase/Render identifiers, or bcrypt hashes in tracked files or log lines. Log counts and labels, never handles or URLs. The gitleaks hooks are never bypassed (`--no-verify` is not used). The repertoire course vendor is never named here; its tooling lives in a private local repo.
3. **`core/` is the only place SQL lives.** `api/` and `pipeline/` call `core/`. `core/` modules import only what they use and do nothing at import time.
4. **Settings live in the `settings` row via `core/settings.py`** (one typed model, one table row, one Preferences page). Engineering constants live in `core/constants.py`. Neither is ever read from env.
5. **Schema changes** are a numbered file in `core/sql/migrations/` plus a regenerated `core/sql/schema.sql` and an updated `tests/fixtures/schema_baseline.sql`, in the same PR. Fresh install loads `schema.sql`; existing databases apply migrations only. Never both.
6. **`player_id` is always 1.** No multi-user branches, no auth beyond the one password cookie.
7. **Chess960 games are history only**: imported and counted, never analysed, matched, puzzled or reviewed. Every worklist, query and migration uses `core.chess.eligibility`, never its own literal.
8. **Every pipeline step is idempotent** and safe to rerun.
9. **Prefer deleting to abstracting.** A lesson learned becomes a test, a CI rule, or one line here — never a paragraph elsewhere. Documentation grows only by replacement.

## Working agreement

Work on a branch. Hand the branch to Rob as a git bundle in `blundriq_personal/ships/NNN-name/` with the commit SHA and the lint/type/test output; his local Codex reviews it against `main` before anything is pushed. Findings land in that ship folder as `review-rN.md`. Nothing reaches GitHub without Rob pushing it. CI (`.github/workflows/ci.yml`) runs on every push and PR regardless.

Read `ARCHITECTURE.md` for the data flow and module map, `docs/decisions/` for the few choices that would surprise you. There is no other documentation to read.
