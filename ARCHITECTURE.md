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
| `core/schema.py` | Fresh install (`core/sql/schema.sql`) vs upgrade (`core/sql/migrations/`). |
| `core/chess/eligibility.py` | The Chess960 rule, as one SQL predicate and one Python check. |
| `core/chess/board.py`, `openings.py`, `platform.py` | Boards and FEN sequences (Chess960-aware); canonical opening names; termination and time-class vocabularies. |
| `core/ingest/` | Chess.com and Lichess fetch + parse (`chesscom.py`, `lichess.py`), the only `chess_games` writer (`store.py`), the import step (`run.py`). |
| `core/repertoire/matching.py` | Game-vs-line matching and the match step. (Line import: to come.) |
| `core/analysis/` | `game.py` (Stockfish per-game walk and classification), `motifs.py` (tactical-motif and missed-mate tagger), `run.py` (worklist, parallel workers, writes), `engine.py`. |
| `core/housekeeping.py` | Retention outside the analysis window. |
| `core/runs.py`, `core/notify.py` | `pipeline_runs` rows; the one failure email (redacted). |
| `core/games.py` | The Games page's reads. |
| `core/migrate.py` | One-time copy of the old database (`pipeline migrate`). |
| `tools/oracle/` | Diffs of the new pipeline against the old database's rows; see its README. |
| `core/puzzles/` (to come) | Generation, serving, attempts + SRS. |
| `core/scout/` (to come) | Opponent profiles and on-the-fly position stats. |
| `core/review/` (to come) | Review detection (no tablebase rung; see decisions/001). |
| `core/ai.py` (to come) | Explanations with cache. |
| `api/auth.py` | One password, one signed cookie. |
| `api/routes/*` | Thin routes. |
| `pipeline/cli.py` | The `pipeline` command. |
| `ui/` | React app. `Preferences` renders `core/settings.py`'s schema generically. |
| `core/sql/schema.sql`, `core/sql/migrations/` | The database. Every table has a one-line comment saying why it exists. |
| `tests/` | pytest against a scratch Postgres; vitest for the UI. `test_secrets_policy.py` enforces the single-reader rule for secrets. |

## Tables at a glance

Games: `chess_games` (shared, deduplicated by platform id, generated `position_keys` + GIN index), `player_games` (the player's side). Analysis: `blunders`, `player_motif_events`. Repertoire: `books` → `chapters` → `repertoire_lines` (generated `position_keys`/`material_keys`), `repertoire_annotations`, `game_repertoire_results` → `game_result_lines`. Puzzles: `puzzles`, `lichess_puzzles` (corpus sample), `puzzle_attempts` (idempotent by `attempt_id`), `player_puzzle_state` (SRS), `player_puzzle_exposure`, `player_puzzle_skip`, `dismissed_*`. Scout: `opponent_profiles` → `opponent_sources`, `opponent_views`. Review: `review_events`, `review_pool_state`, `review_detection_state`, `learn_commits`. System: `players` (one row), `settings` (one row), `pipeline_runs`, `schema_version`, `ai_explanation_cache`.

The six `bq_*` SQL functions (`core/sql/schema.sql`, top) canonicalise FENs and hash positions; ten generated columns and several GIN and partial unique indexes depend on them. They are why matching is a single indexed query rather than a Python loop.

## Deployment

Render: root directory = repo root, build `pip install .`, start `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, build filter on `api/ core/ pyproject.toml .python-version`; Python version from `.python-version`. Vercel: root `ui/` with the project setting "Ignored Build Step: Automatic" (skips commits that do not touch `ui/`), and `VITE_API_URL` must be set in the Vercel project to the API origin (staging `https://api-personal.blundriq.com`) because the built UI has no `/api` proxy. GitHub Actions: `ci.yml` on push/PR; `pipeline.yml` hourly (`pipeline run --analyze-limit 60`: import → match → analyze → housekeep, Stockfish 18 downloaded from the pinned release), secrets only in that workflow. The Dell runs the same `pipeline` CLI against the same database for bulk work (`pipeline analyze --workers 30`).

## Windows and retention

`analysis_game_limit` (settings) is the one window: the player's most recent N **standard** games (`core.chess.eligibility.window_cte`). Those are matched and analysed; `pipeline housekeep` deletes analysis artefacts and repertoire results outside the window and nulls the bulk JSON (moves, FEN sequence, clocks, per-ply analysis) of games no owner — the player or a scouted opponent — has in-window. Chess960 games never take a window slot: they are history, listed and counted on the Games page (all-variant), never analysed or matched (`core/chess/eligibility.py`, docs/decisions/001 and 003). The metadata row of every game ever imported stays.
