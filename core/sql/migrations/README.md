# Migrations

Ordered SQL files applied by `pipeline db upgrade` to a database that already exists.
Naming: `NNN_short_description.sql` (three digits, lowercase, underscores). Nothing else
may live here; the loader rejects any other filename.

A migration ships in the same PR as the regenerated `../schema.sql`, and with
`tests/fixtures/schema_baseline.sql` updated to the schema *before* it (that fixture is
how CI proves fresh-install == upgrade). A fresh database never runs migrations; it loads
`schema.sql` and records the highest migration number as its baseline.
