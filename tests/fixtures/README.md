Fixtures are synthetic. `schema_baseline.sql` (absent until the first migration exists) is the
schema as it was before the earliest pending migration, with a first line `-- baseline_version: N`;
`tests/test_schema.py` loads it, applies migrations, and diffs the result against a fresh install.
