# 002 — Settings are one typed model, one table row, one page

**Decision (2026-09-19).** User-tunable settings are fields on `core/settings.py::Settings`, stored as one JSON row in `settings`, edited on the Preferences page. Not a config file, not env vars, not a key/value table.

**Why.** A config file would make every tweak a commit and a redeploy. The old key/value `app_settings` table plus 27 admin panels let any code path read any key and let settings sprawl. One model means a setting exists only if it is declared with a type, bounds and a description; one row means no per-user resolution; one page means no admin surface to maintain.

**Boundary.** Engineering constants that need a code change anyway (engine depth, corpus theme list, the Chess960 rule) are in `core/constants.py`. Secrets are never settings.
