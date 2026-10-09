# blundriq

A single-player chess improvement tool. It imports one player's games from Chess.com and Lichess, runs Stockfish over them, and shows the player their recurring blunders, where they leave their opening repertoire, puzzles cut from their own mistakes (with spaced repetition) and from the Lichess puzzle corpus, opponent scouting, and a review of where their openings lose points.

One deployment serves one player behind one password; there are no accounts. To use it for yourself, clone it and run your own copy as below. `ARCHITECTURE.md` is the map of the code; `docs/decisions/` holds the few choices that would surprise you.

## Try it locally

You need Python 3.14, Node 24, Postgres 16 or later (CI runs 17) where you can create databases, and Stockfish 18 on `PATH` (the hourly job pins the same version; analyses are labelled with it).

```
git clone <your fork> blundriq && cd blundriq
python3.14 -m venv .venv && bash tools/install.sh .venv/bin/python dev
(cd ui && npm ci)
createdb blundriq
```

Every environment variable is read in one place, `core/secrets.py`, which lists them all; a missing one stops the process with its name. For the API and the pipeline, put these in a file outside the repository and load it into your shell (`set -a; . ~/.config/blundriq/api.env; set +a`):

```
DATABASE_URL='postgresql:///blundriq'
SESSION_SECRET='<.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))">'
PASSWORD_HASH='<.venv/bin/python -c "import bcrypt, getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())">'
ALLOWED_ORIGINS='http://localhost:5173'
```

Run the generator commands yourself and paste their output between the single quotes. The quotes matter: a bcrypt hash starts with `$2b$`, and unquoted the shell expands it into something else and every login fails. The password you type for `PASSWORD_HASH` is the one the login page asks for. In a host's dashboard (Render, GitHub secrets) enter values without quotes.

Every command below runs from the repository root with the virtualenv's own path (`.venv/bin/pipeline`), so no shell needs the virtualenv activated. If you prefer `. .venv/bin/activate`, plain `pipeline` works in that shell, and only in that shell. Then:

```
.venv/bin/pipeline db init
.venv/bin/pipeline player set --chesscom <your handle> --lichess <your handle>
.venv/bin/pipeline import --all
.venv/bin/pipeline analyze --workers 4
.venv/bin/pipeline generate-puzzles
.venv/bin/pipeline review
.venv/bin/pipeline position-evals --limit 0 --workers 4
.venv/bin/pipeline review-snapshot
.venv/bin/uvicorn api.main:app --reload
```

and in a second shell `cd ui && npm run dev`, then open `http://localhost:5173`. Give either handle or both. `.venv/bin/pipeline --help` lists every step; each is idempotent and safe to rerun. The first `analyze` covers your most recent 1,000 standard games (Stockfish 18 at depth 18) and takes a while: `--limit N` does a slice at a time.

What needs more than your games:

- **Corpus puzzles** (the motif trainer). Download `lichess_db_puzzle.csv.zst` from database.lichess.org (CC0), decompress it, and run `.venv/bin/pipeline import-corpus --csv lichess_db_puzzle.csv`. It keeps a capped sample, so the database stays small. Without it, Practice serves your own puzzles only.
- **Deviations and repertoire puzzles.** Your repertoire comes in as a JSON file: books of chapters of lines, each line a list of SAN moves with optional notes. `tests/fixtures/repertoire_import.json` is a complete example, and the models at the top of `core/repertoire/importing.py` are the schema. Load it with `.venv/bin/pipeline import-repertoire FILE --mode scratch`, then `.venv/bin/pipeline match-repertoire`. `--mode update` only refreshes notes on lines that already exist. Without a repertoire, Deviations is empty and everything else works.

  **A book marked `"complete": true` is a replacement, not an addition.** In `scratch` mode such a book's file is taken as the whole book: lines and chapters already stored but missing from the file are switched off, and the course notes the file no longer carries are deleted (notes you wrote by hand are kept, and lines are never deleted, so their puzzles keep their progress). The worked example sets the flag. To add a few lines to a book you already imported, leave `complete` out (or set it to `false`): the import then only adds. Before re-importing a book that already exists, run the same command with `--dry-run` first: nothing is written, and its counts show what would change (`lines.vanished_deactivated`, `annotations.stale_deleted`).
- **AI explanations** are optional. Each prompt names its model on the Preferences page, and the key for that provider (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`) is read only when a button is pressed.

`.venv/bin/pipeline run` is the unattended hourly chain. It refuses to start without a way to report a failure (`RESEND_API_KEY`, `ALERT_EMAIL`, `ALERT_FROM`: a Resend account and a sender on a domain verified there), so run the steps by hand until you set those.

## Settings worth checking first

Every tunable lives in one database row, edited on the Preferences page; a fresh database starts from the defaults in `core/settings.py`, which are the original deployment's values. Before the first week of use, check:

- `timezone` (default `America/New_York`): when "today" starts for streaks, daily targets and spaced repetition.
- `time_class_focus` (`rapid_plus`): which games count as evidence for blunders and puzzles. Set `all` if you mostly play blitz.
- `cc0_difficulty_tier` (`very_hard`) and `lichess_rating_offsets`: how hard corpus puzzles are relative to your rating, and how far a Chess.com rating sits below the Lichess scale. The offsets were calibrated on one player's pair of accounts; if you have both, set them from your own gap.
- `daily_puzzle_target` and `daily_game_target` (10 and 1): what Home counts as done.
- `analysis_game_limit` (1,000): how many recent games stay analysed.

## Deploying

The original runs on free tiers: Postgres on Supabase, the API on Render, the UI on Vercel, and the hourly pipeline on GitHub Actions. Any host for each works the same way.

**The UI and the API must share a site.** The login cookie is `SameSite=Lax`, so the browser sends it only when the UI and the API are under the same registrable domain, e.g. `app.example.org` and `api.example.org`, both on HTTPS. Two default host names such as `*.vercel.app` and `*.onrender.com` are different sites: the page loads, but every login fails. Use a domain you control for both.

**Database.** Create the project's Postgres and, with `DATABASE_URL` pointing at it, run `.venv/bin/pipeline db init` once (later versions: `.venv/bin/pipeline db upgrade`). The schema has no row-level security: nothing but the API is meant to reach it, with a password-protected session in front. On Supabase that means turning the Data API off (Project Settings → Data API) before any data goes in. Supabase grants its public API roles access to new tables by default, so with the Data API on, anyone holding the project's public key could read and write every table. Check afterwards that `https://<project-ref>.supabase.co/rest/v1/settings`, called with the project's anon key, no longer answers with data. Use the pooler connection string as `DATABASE_URL`.

**API** (Render or any Python host): build `bash tools/install.sh python runtime`, start `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, with `DATABASE_URL`, `SESSION_SECRET`, `PASSWORD_HASH`, `ALLOWED_ORIGINS=https://app.example.org` and any AI keys set in the host's environment. `ALLOWED_ORIGINS` is the exact origin of your UI (several, comma-separated, if you have more than one); there is no wildcard, and nothing else may call the API with the cookie. Point `api.example.org` at it.

**UI** (Vercel or any static host): build `ui/` with `npm run build`, with `VITE_API_URL=https://api.example.org` set at build time. Point `app.example.org` at it.

**Hourly pipeline**: `.github/workflows/pipeline.yml` runs `pipeline run` every hour. Add `DATABASE_URL`, `RESEND_API_KEY`, `ALERT_EMAIL` and `ALERT_FROM` as repository secrets and enable Actions on your copy (scheduled workflows start disabled on a fork). GitHub also disables a public repository's schedules after 60 days without commits, silently; the job re-enables its own workflow after every run and sends the failure email if it cannot, but GitHub does not promise that this resets the 60 days, so glance at the Actions tab every few weeks. Its console output is public on a public repository, so the code prints counts and error class names only, never messages or handles; keep it that way in anything you add.

## Developing

```
bash tools/hooks/install.sh        # once: gitleaks pre-commit and pre-push hooks (gitleaks >= 8.24)
createdb blundriq_test
export TEST_DATABASE_URL=postgresql:///blundriq_test
.venv/bin/ruff check . && .venv/bin/pyright && .venv/bin/pytest
cd ui && npm run check && npm test -- --run
```

**Dependencies are locked.** `requirements.lock` (the app), `requirements-dev.lock` (plus the `[dev]` tools) and `build.lock` (pip and setuptools) pin every package by version and hash, and `tools/install.sh` installs only from them, with build isolation off so nothing a build needs comes from outside them. Nothing changes underneath a running deployment until someone regenerates a lock. After editing `pyproject.toml` or `build.in`, run `bash tools/locks.sh` (needs [uv](https://docs.astral.sh/uv/) at the version the script names); it re-resolves while keeping every other pin. `bash tools/locks.sh upgrade` moves everything to the newest releases, a deliberate step to take with the full suite. CI runs `bash tools/locks.sh check`, which fails when a lock is not what its command writes (also when a pinned release is later yanked or gains a file; the same command fixes it).

**The test session drops and recreates the database `TEST_DATABASE_URL` names**, and creates and drops siblings named after it. Point it at a throwaway database. The suite refuses to start unless the name carries a `test` or `scratch` word and differs from the database `DATABASE_URL` names, but the name check is a seatbelt, not a reason to aim it at anything you care about.

`pipeline migrate` and `tools/oracle/` moved data from this project's earlier multi-user version and checked the port against it; a new installation never needs them.

MIT licensed. Stockfish is GPL-3.0; the in-browser engine under `ui/public/engine/` ships with its licence, notices (`NNUE-NOTICE.txt`, `AUTHORS`, `STOCKFISH-SOURCE.md`) and corresponding source, linked from the Explore view's footer.
