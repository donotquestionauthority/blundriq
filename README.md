# blundriq

A personal chess improvement tool: it imports my games from Chess.com and Lichess, runs Stockfish over them, and shows me my recurring blunders, where I leave my opening repertoire, puzzles cut from my own mistakes (with spaced repetition), opponent scouting, and a game review.

Single user, deliberately small; `ARCHITECTURE.md` is the map. A rebuild of an earlier multi-user version.

## Running it

```
pip install -e ".[dev]"            # Python 3.11+, a Postgres, Stockfish on PATH
pipeline db init                   # fresh database (DATABASE_URL)
pipeline player set --chesscom U --lichess U
pipeline import --all              # first import; later runs are incremental
pipeline match-repertoire          # once repertoire lines are stored (import command to come)
pipeline analyze --workers 4       # Stockfish 18, depth 18, most recent 1000 games
pipeline housekeep
pipeline run                       # the hourly chain (what .github/workflows/pipeline.yml runs)
uvicorn api.main:app               # the API; `cd ui && npm run dev` for the front end
```

Secrets are environment variables read only in `core/secrets.py`; tests need `TEST_DATABASE_URL`.

MIT licensed. Stockfish is GPL-3.0; the in-browser engine under `ui/public/engine/` ships with its licence, notices (`NNUE-NOTICE.txt`, `AUTHORS`, `STOCKFISH-SOURCE.md`) and corresponding source, linked from the Explore view's footer.
