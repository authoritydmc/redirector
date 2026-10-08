# ADR-0002: Clean-break v3 schema + best-effort import (no v2 migrations)

- Status: accepted
- Date: 2026-10-08
- Context: v2's schema carries sins worth fixing, not inheriting
  (ISO-string datetimes, comma tags, config-buried upstreams). Alembic
  upgrade scripts from v2 would fossilize them.
- Decision: clean v3 SQLModel schema (breaking allowed pre-release);
  v2 data arrives via a best-effort `import-v2` normalizer, never via
  upgrade scripts. Separate Alembic history (`backend/alembic/`) starts
  at the clean schema.
- Consequences: import must be verified per install (row-count + dry-run
  reporting); no downgrade path v3→v2 schema (forward-compatible JSON
  backups instead). See `backend/migrations/import_v2.py`, EPIC-04.
