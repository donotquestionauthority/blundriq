# Architecture

## Data flow

```
Chess.com / Lichess APIs
        │  pipeline import        (hourly, GitHub Actions; on demand from the Dell)
        ▼
  chess_games + player_games ──► pipeline analyze (Stockfish 18, depth 18; standard games only)
        │                               │
        │                               ▼
        │                        blunders, player_motif_events
        │
        ├─► pipeline match-repertoire ──► game_repertoire_results, game_result_lines
        │        (books / chapters / repertoire_lines come from `pipeline import-repertoire <file>`)
        │
        ├─► pipeline import-opponents ──► opponent_views  (Scout matches on chess_games.position_keys)
        │
        ▼
  pipeline generate-puzzles ──► puzzles  (sources: blunder, deviation, own_mate, lichess_cc0, scout, custom)
        │                            │
        │                            ▼
        │                 Practice (api) ──► puzzle_attempts, player_puzzle_state (SRS), exposure, skip
        │
        └─► pipeline review ──► review_events (regenerated, never migrated)

  Home page reads: due count (Practice eligibility), games today/week, streaks, since-last-visit, pipeline_runs.
```

Everything above the API line is the `pipeline` CLI (`pipeline/cli.py`), one subcommand per step, each idempotent. Everything below is FastAPI routes in `api/routes/`, which are thin: they parse the request, call a function in `core/`, and return its result.

## Module map

| Path | Owns |
|---|---|
| `core/secrets.py` | The only environment reads. Fails closed. |
| `core/settings.py` | The `Settings` model (every tunable), `load`/`save` of the single `settings` row, JSON schema for the Preferences page. |
| `core/constants.py` | Engineering constants: `PLAYER_ID`, engine depth, analysable variants, puzzle sources, corpus themes. |
| `core/db.py` | Connection helper and pool. One role. |
| `core/schema.py` | Fresh install (`schema.sql`) vs upgrade (`migrations/`). |
| `core/chess/eligibility.py` | The Chess960 rule, as one SQL predicate and one Python check. |
| `core/chess/` (later) | FEN identity, openings, time control, termination. |
| `core/analysis/` (phase 2) | Stockfish analysis, motif detection, mate acceptance. |
| `core/repertoire/` (phase 2) | Line storage, matching, the neutral import. |
| `core/puzzles/` (phase 3) | Generation, serving, attempts + SRS. |
| `core/scout/` (phase 6) | Opponent profiles and on-the-fly position stats. |
| `core/review/` (phase 8) | Review detection (no tablebase rung; see decisions/001). |
| `core/ai.py` (phase 4) | Explanations with cache. |
| `core/notify.py` (phase 2) | Ops email. |
| `api/auth.py` | One password, one signed cookie. |
| `api/routes/*` | Thin routes. |
| `pipeline/cli.py` | The `pipeline` command. |
| `ui/` | React app. `Preferences` renders `core/settings.py`'s schema generically. |
| `schema.sql`, `migrations/` | The database. Every table has a one-line comment saying why it exists. |
| `tests/` | pytest against a scratch Postgres; vitest for the UI. `test_secrets_policy.py` enforces rule 1. |

## Tables at a glance

Games: `chess_games` (shared, deduplicated by platform id, generated `position_keys` + GIN index), `player_games` (the player's side). Analysis: `blunders`, `player_motif_events`. Repertoire: `books` → `chapters` → `repertoire_lines` (generated `position_keys`/`material_keys`), `repertoire_annotations`, `game_repertoire_results` → `game_result_lines`. Puzzles: `puzzles`, `lichess_puzzles` (corpus sample), `puzzle_attempts` (idempotent by `attempt_id`), `player_puzzle_state` (SRS), `player_puzzle_exposure`, `player_puzzle_skip`, `dismissed_*`. Scout: `opponent_profiles` → `opponent_sources`, `opponent_views`. Review: `review_events`, `review_pool_state`, `review_detection_state`, `learn_commits`. System: `players` (one row), `settings` (one row), `pipeline_runs`, `schema_version`, `ai_explanation_cache`.

The six `bq_*` SQL functions (`schema.sql`, top) canonicalise FENs and hash positions; ten generated columns and several GIN and partial unique indexes depend on them. They are why matching is a single indexed query rather than a Python loop.

## Deployment

Render: root directory = repo root, build `pip install .`, start `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, build filter on `api/ core/ pyproject.toml schema.sql`. Vercel: root `ui/`, ignored-build step skips commits that do not touch `ui/`. GitHub Actions: `ci.yml` on push/PR; `pipeline.yml` hourly (added in phase 2), secrets only in that workflow. The Dell runs the same `pipeline` CLI against the same database for bulk work.
