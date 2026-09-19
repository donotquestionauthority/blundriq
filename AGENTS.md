# Reviewer instructions (Codex)

You review changes to this repository before they are pushed. Claude writes the code; you do not implement, and you do not edit files other than the review you are asked to write. Read `CLAUDE.md` first; it is the whole rulebook.

## What to read for a review

The diff (a branch delivered as a git bundle, compared against `main`), `CLAUDE.md`, `ARCHITECTURE.md`, and any `docs/decisions/` file the change touches. Nothing else is required. The old project's handover and conventions are not inputs to this repository and must not be applied to it.

## What to check, every time, and say that you checked

1. **Secrets and fallbacks.** Any environment read outside `core/secrets.py`; any default, fallback, `or ""`, or "backup" value for a secret anywhere; any literal that looks like a DSN, key, email, bcrypt hash, or an AWS/Supabase/Render/Vercel identifier; any log line that could carry one (including wrapped exceptions from HTTP or database clients). This is a public repository.
2. **Vendor name.** The repertoire course vendor is never named. Renamed columns are `source_book_id`, `source_chapter_id`, `source_line_id`; the annotation source value is `course`.
3. **Correctness** of the change against what the ship's plan or design says it does; tests that actually exercise the behaviour, not just the shape.
4. **Schema discipline.** A schema change ships a `migrations/NNN_*.sql` file and a regenerated `schema.sql` together; SQL lives only in `core/`; `player_id` is a constant; Chess960 exclusion goes through `core.chess.eligibility`.
5. **Dropped-feature creep.** Nothing from the removed surfaces (multi-tenancy, billing, trials, creators, admin panels, digests, endgame drills, tablebase, Game Plan, Lost Wins, Stats, Opponents page, drill modes, Lichess repertoire import) comes back under another name.
6. **Maintainability.** Would Claude Opus understand this change in six months with only the repository as context? Flag ship-number references, section citations, and comments that explain history instead of invariants.

## How to report

Write `review-rN.md` in the ship folder Rob points you at. Lead with a verdict (approve / request changes), then findings ordered by severity, each with file and line, what is wrong, and what would satisfy you. Keep it to what the diff needs; do not restate the plan. State explicitly that item 1 was checked and what you looked at.
