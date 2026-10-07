# [EPIC-04] Data & cache: SQLModel (clean v3 schema), Postgres-first, Redis abstraction

> **Labels:** `epic`, `backend`, `database`, `caching` · **Parent:** EPIC-MASTER · **Estimate:** M (2–3 weeks)

## Problem
- SQLite-first with runtime WAL/`busy_timeout` hacks (`app/__init__.py:30-56`) because N gevent workers share one file.
- `Config.repair_database_path()` / `resolve_database_uri` (`config.py:144-192`, `utils/paths.py`) — heroic but symptom-fixing: relative-vs-absolute SQLite confusion.
- Redis is a raw global `config.redis_client` with ping-and-disable fallback (`config.py:245-275`); callers branch on `redis_enabled` everywhere; no interface → untestable, no TTL discipline.
- Models in `model/` (`redirect.py`, `upstream_cache.py`, `upstream_check_log.py`, `user_param.py`) are sync; Alembic env assumes Flask (`migrations/env.py`).

## Proposal
- **SQLModel + Alembic, clean v3 schema (breaking allowed — not yet public)**: one class = table + Pydantic schemas (`table=True` models + separate `Create/Update/Read` variants). Async engine (`aiosqlite` / `asyncpg`) + `AsyncSession` per-request via FastAPI `Depends`. Repository layer per module so routes never touch sessions directly.
- **Fix v2's schema sins instead of inheriting them**: `created_at/updated_at` → `DateTime(timezone=True)` (today `String` ISO blobs, `model/redirect.py:16-17`), `visibility` → Enum (`public|unlisted|private|team`), `tags` → JSON list (not comma-separated string), `expires_at` → `DateTime` nullable. Proper indexes (`pattern` unique, `upstream_name`, `expires_at`).
- **Postgres-first in code, SQLite-default in packaging**: same Alembic history boots both; CI matrix tests `sqlite+aiosqlite`, `postgres:16`. MySQL support dropped in v3 (maintenance cost, no users) — revisit on demand.
- **v2 import is best-effort, not byte-perfect**: `redirector import-v2 --data-dir` script parses old rows/JSON into the new schema (normalizes timestamps, splits tags, drops junk). No golden-file DB-compat tests; instead test the importer on a sample v2 fixture.
- **Cache abstraction**:
  ```python
  class Cache(Protocol):
      async def get_shortcut(p) -> Shortcut | None
      async def set_shortcut(p, value, ttl) ...
      async def invalidate(p) ...
  # impls: RedisCache, MemoryCache (dev/test), NoopCache
  ```
  Single `CACHE_TTL_SHORTCUT=300s`, `CACHE_TTL_UPSTREAM=3600s` in settings; stampede protection via `singleflight`/lock on miss.
- **Config split**: secrets (`admin password hash`, `session/JWT secret`, MFA seeds) → env vars / Docker secrets / optional vault; non-secrets stay in DB-backed settings table with admin UI (replaces `redirect.config.json` as source of truth; JSON kept as import/export only).

## Acceptance criteria
- [ ] Fresh boot on SQLite + Postgres from the same Alembic head; v2 sample fixture imports via `import-v2` with row counts verified.
- [ ] All DB access async; no `db.session` globals in request path.
- [ ] Cache hit rate visible in `/metrics`; p99 redirect latency meets master target.
- [ ] `REDIRECTOR_DATA_DIR` + `DATABASE_URL` + `REDIS_URL` documented; JSON config import path tested.

## Tasks
- [x] 1. SQLModel entities (clean v3 schema) + Alembic env (dual-driver `env.py`).
  (Entities + repositories + sqlite-memory tests done on
  `v3/epic-01-backend-foundation`; dual-driver Alembic `env.py` still open.)
- [x] 2. Repository layer + unit tests (sqlite memory).
- [x] 3. `import-v2` script + sample-fixture test.
- [x] 4. Redis `Cache` impl + `MemoryCache`; remove `config.redis_client` globals.
  (Done: `RedisCache` in `backend/core/cache.py` implements the full
  `Cache` protocol incl. distributed singleflight
  (`SET NX PX` lock + token-checked Lua release, `uncacheable` tombstone
  so SSO verdicts release followers at once) with v2-style graceful
  degradation on outages; selected via `REDIRECTOR_CACHE_BACKEND`
  (`memory` default). Covered by `tests/test_v3_cache_redis.py` — 9 tests
  against real Redis, skipping cleanly without it. The v2
  `config.redis_client` global is untouched (Flask runtime); removal lands
  with the EPIC-08 Flask cutover.)
- [x] 5. Settings table (`settings` key→JSON) + migration from `redirect.config.json`.
  (Done on `v3/epic-01-backend-foundation`: `Setting` table + admin
  GET/PATCH API existed; `import-v2` now migrates the curated non-secret
  allowlist — `auto_redirect_delay` (int, clamped 0–10),
  `log_level` (validated), `delete_requires_password` (bool-coerced),
  `upstream_cache.enabled` (dotted key) — with normalization counted.
  Secrets (`admin_password`, `session_secret`, `mfa.*`) are never migrated;
  connection topology (`redis`, `database`, `port`) stays env-owned.
  Idempotent: existing keys win so admin edits stick. Covered by
  `test_import_migrates_config_settings`.)
- [x] 6. Indexes + query plan review on shortcuts/upstream_cache tables.
  (Reviewed on `v3/epic-01-backend-foundation` against seeded SQLite plans:
  both hot-path pattern lookups SEARCH their indexes; check-log filter
  SEARCHes `upstream_name`; jobs newest-first walks the PK backward with no
  TEMP B-TREE sort. Deliberately NO new indexes: remaining SCANs are
  substring-LIKE (unindexable — FTS5 is the future fix, not a b-tree),
  small-table scans, or admin-only sorts; new indexes would tax the
  hottest write path (per-check log upserts) for admin-page reads.
  Regression-pinned by `tests/test_v3_query_plans.py`.)
- [x] 7. Backup/restore covers DB + settings + secrets-manifest (never secrets plaintext).
  (Done on `v3/epic-01-backend-foundation`: zip archives under `data/backups`
  (manifest + per-table JSON, six domain tables; operational rows excluded),
  `POST /api/v1/admin/backup` → `backup_create` job plus list/download/delete,
  and `POST /api/v1/admin/backup/{name}:restore` → `backup_restore` job with
  validate-then-apply (corrupt archives fail before touching data), automatic
  `pre-restore` safety backup, and merge-upsert by natural key (new rows
  insert with DB-assigned ids so sequences can't collide; intruders survive).
  Secrets hygiene test-enforced (hashes only, env secrets named-not-valued,
  plaintext canary absent from archive bytes). Covered by 6 tests in
  `tests/test_v3_backup_api.py`.)

## `gh` snippet
```bash
gh issue create --title "[EPIC-04] Data & cache: async SQLAlchemy, Postgres-first, Redis abstraction" \
  --label "epic,backend" --body-file docs/refactor-epics/04-data-cache.md
```
