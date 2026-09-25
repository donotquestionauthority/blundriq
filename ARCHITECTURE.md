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
  pipeline generate-puzzles ──► puzzles  (blunder, own_mate, deviation; corpus rows are
        │                            │    materialised at serve time, custom ones by hand)
        │                            │    pipeline import-corpus rebuilds lichess_puzzles
        │                            │
        │                            ▼
        │                 Practice (api) ──► puzzle_attempts, player_puzzle_state (SRS), exposure, skip
        │                 pipeline srs-maintain un-retires mastered puzzles whose pattern recurs
        │
        └─► pipeline review ──► review_events (regenerated, never migrated)

  Blunders (api) reads blunders by board ──► dismissed_blunder_fens; explain ──► ai_explanation_cache, ai_calls;
           create puzzle ──► puzzles (tagged 'custom', which no generator displaces)

  Deviations (api) reads game_repertoire_results by pattern (book, chapter, ply, expected move) ──► seen_deviations;
           each card's board is read back through core/repertoire/read.py (the lines through it, the one move
           they agree on)

  Repertoire (api) reads books › chapters › lines; a toggle flips one flag and rematches the games it can touch
           (core/repertoire/books.py), and switching on is gated: what would disagree with an active line is
           refused (a line) or held back (a chapter's or book's lines) by core/repertoire/conflicts.py, which
           also serves the Conflicts page (positions where two lines disagree, identical lines in different
           chapters, the contested count on the Repertoire page); notes on positions and the line walk-through ──► repertoire_annotations
           (core/repertoire/annotations.py). `pipeline import-repertoire FILE` loads a neutral repertoire file
           (core/repertoire/importing.py; docs/decisions/007) and rematches the window. Two compare surfaces
           read the same lines: Similar positions on every card (core/repertoire/neighbourhood.py: the boards of
           the active repertoire within a placement distance of the card's, same material exactly) and Compare
           similar positions in the solver (core/repertoire/branch_compare.py: what the opponent could have
           played one half-move back, from the repertoire and the player's own blunders).

  Home page reads: due count (Practice eligibility), games today/week, streaks, new blunders — recurring
           boards the Blunders list has never shown (seen_blunder_boards, which that page fills with what it
           rendered; the first look ever records the whole list as known) — one predicate in core/blunders.py,
           shared with the page's NEW chips; new deviation patterns the same way (seen_deviations,
           core/deviations.py); pipeline_runs (the hourly chain only).
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
| `core/repertoire/matching.py` | Game-vs-line matching and the match step; rematching after the repertoire changed; the one lock everything that reads the repertoire to publish, or changes it, holds (docs/decisions/007). |
| `core/repertoire/read.py` | The read side: which effectively-active lines pass through a board (`rep_lines`, by book colour, never defaulted), and the one reduction of many occurrences to a move (`project_ply`, `singular_move`: exact FEN first, canonical moves, fail-closed conflicts). |
| `core/repertoire/annotations.py` | Notes on positions: the unattached note on a board, notes attached to a line, and the walk-through's projection of a book's notes onto a line. |
| `core/repertoire/books.py` | The Repertoire page: books, sections, and switching a book, chapter or line on or off (gated on the way on, rematches what it can touch). |
| `core/repertoire/conflicts.py` | One signature relation (every line's move at its book side's plies, effectiveness computed): the Conflicts page's listing and duplicates, the contested count, and the gate a toggle runs (`importing.decide` over existing rows). |
| `core/repertoire/neighbourhood.py` | Similar positions: material-hash prefilter, exact signature verify on the matched plies only, placement distance, one entry per board with all of its groups (enumerated, never reduced), the cap in boards. |
| `core/repertoire/branch_compare.py` | Branch compare: every opponent option at a puzzle's parent from the repertoire (leg R) and the player's blunders (leg B; a scout leg is reserved and empty), repertoire winning on a board, `current` always present and never capped. |
| `core/repertoire/importing.py` | `pipeline import-repertoire`: the neutral file, identity by source ids, the cohort gate for new lines, replacement for a book the file marks complete. |
| `core/deviations.py` | The Deviations page: patterns (book, chapter, ply, expected move) ranked by distinct games, their games, the repertoire's reading of each board, the seen set. |
| `core/chess/san.py` | SAN normalisation, and move identity that does not depend on notation. |
| `core/chess/mate_acceptance.py` | Forced mate in exactly N: building the acceptance map, and the verdict that reads it. |
| `core/puzzles/lines.py` | Replaying a solution line: its FEN sequence, and whether it ends in mate. |
| `core/puzzles/generate/` | The three generators (`blunder.py`, `missed_mate.py`, `repertoire.py`), who owns a board (`_state.py`), and the two writes they make (`_write.py`). |
| `core/puzzles/corpus.py` | The Lichess CC0 sample: streaming the CSV, loading it, and the index the serve path needs. |
| `core/analysis/` | `game.py` (Stockfish per-game walk and classification), `motifs.py` (tactical-motif and missed-mate tagger), `run.py` (worklist, parallel workers, writes), `engine.py`. |
| `core/housekeeping.py` | Retention outside the analysis window. |
| `core/runs.py`, `core/notify.py` | `pipeline_runs` rows; the one failure email (redacted). |
| `core/games.py` | The Games page's reads. |
| `core/migrate.py` | One-time copy of the old database (`pipeline migrate`). |
| `tools/oracle/` | Diffs of the new pipeline against the old database's rows; see its README. |
| `core/puzzles/visibility.py` | Which puzzles the player may see and attempt, as SQL fragments every reader composes. |
| `core/puzzles/serve.py` | The play queue: batches from the five buckets, pending and skip, the corpus rotation, the browse and trophy lists, the due count. |
| `core/puzzles/srs.py` | The six-level ladder, one attempt's transition, attempt summaries, king demotion (`pipeline srs-maintain`). |
| `core/puzzles/attempts.py` | Grading (line replay or acceptance map) and recording an attempt in one transaction. |
| `core/scout/` (to come) | Opponent profiles and on-the-fly position stats. |
| `core/review/` (to come) | Review detection (no tablebase rung; see decisions/001). |
| `core/blunders.py` | The Blunders page: boards ranked by distinct games and severity, their games, dismissal. |
| `core/ai.py`, `core/prompts.py` | Explaining a blunder: context read from the database, sandboxed prompt templates, provider call over HTTP, cache, hourly and daily caps. |
| `core/puzzles/custom.py` | Creating and retiring a hand-made puzzle. |
| `core/home.py` | The Home page: due count, today's puzzles and games against their targets, streaks, blunders and deviation patterns their lists have never shown, pipeline status. Reads only. |
| `api/auth.py` | One password, one signed cookie. |
| `api/routes/*` | Thin routes. |
| `pipeline/cli.py` | The `pipeline` command. |
| `ui/` | React app. `Preferences` renders `core/settings.py`'s schema generically. |
| `core/sql/schema.sql`, `core/sql/migrations/` | The database. Every table has a one-line comment saying why it exists. |
| `tests/` | pytest against a scratch Postgres; vitest for the UI. `test_secrets_policy.py` enforces the single-reader rule for secrets. |

## Tables at a glance

Games: `chess_games` (shared, deduplicated by platform id, generated `position_keys` + GIN index), `player_games` (the player's side). Analysis: `blunders`, `player_motif_events`. Repertoire: `books` → `chapters` → `repertoire_lines` (generated `position_keys`/`material_keys`; identity in docs/decisions/007), `repertoire_annotations` (attached to a line, or to a bare position), `game_repertoire_results` → `game_result_lines`, `seen_deviations`. Puzzles: `puzzles` (at most one active non-repertoire puzzle per board, one per repertoire line), `lichess_puzzles` (corpus sample), `puzzle_attempts` (idempotent by `attempt_id`), `player_puzzle_state` (SRS), `player_puzzle_exposure`, `player_puzzle_skip`, `dismissed_blunder_fens`. Scout: `opponent_profiles` → `opponent_sources`, `opponent_views`. Review: `review_events`, `review_pool_state`, `review_detection_state`, `learn_commits`. System: `players` (one row), `settings` (one row), `pipeline_runs`, `schema_version`, `ai_explanation_cache`, `ai_calls` (what the AI caps count), `seen_blunder_boards`.

The six `bq_*` SQL functions (`core/sql/schema.sql`, top) canonicalise FENs and hash positions; ten generated columns and several GIN and partial unique indexes depend on them. They are why matching is a single indexed query rather than a Python loop.

## Deployment

Render: root directory = repo root, build `pip install .`, start `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, build filter on `api/ core/ pyproject.toml .python-version`; Python version from `.python-version`. Vercel: root `ui/` with the project setting "Ignored Build Step: Automatic" (skips commits that do not touch `ui/`), and `VITE_API_URL` must be set in the Vercel project to the API origin (staging `https://api-personal.blundriq.com`) because the built UI has no `/api` proxy. GitHub Actions: `ci.yml` on push/PR; `pipeline.yml` hourly (`pipeline run --analyze-limit 60`: import → match → analyze → generate-puzzles → srs-maintain → import-opponents → housekeep, Stockfish 18 downloaded from the pinned release), secrets only in that workflow. The Dell runs the same `pipeline` CLI against the same database for bulk work (`pipeline analyze --workers 30`).

## Windows and retention

`analysis_game_limit` (settings) is the one window: the player's most recent N **standard** games (`core.chess.eligibility.window_cte`). Those are matched and analysed; `pipeline housekeep` deletes analysis artefacts and repertoire results outside the window and nulls the bulk JSON (moves, FEN sequence, clocks, per-ply analysis) of games no owner — the player or a scouted opponent — has in-window. Chess960 games never take a window slot: they are history, listed and counted on the Games page (all-variant), never analysed or matched (`core/chess/eligibility.py`, docs/decisions/001 and 003). Time class works the other way round: a blitz game takes its slot in the window and is then excluded as *evidence* for a blunder or missed-mate puzzle, because it is a recent game that is not what Rob is studying (`time_class_focus`); deviation puzzles ignore time class, since leaving a prepared line is the same mistake at any speed (docs/decisions/005). The two puzzle windows are their own settings — `blunders_default_last_n_games` and `deviations_default_last_n_games` — not the analysis window. The metadata row of every game ever imported stays.
