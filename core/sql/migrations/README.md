# Migrations

Ordered SQL files applied by `pipeline db upgrade` to a database that already exists.
Naming: `NNN_short_description.sql` (three digits, lowercase, underscores). Nothing else
may live here; the loader rejects any other filename.

A migration ships in the same PR as the regenerated `../schema.sql`.
`tests/fixtures/schema_baseline.sql` is the schema before migration 001 and stays so: CI
loads it, applies every migration, and proves the result equals a fresh install. A fresh database never runs migrations; it loads
`schema.sql` and records the highest migration number as its baseline.
