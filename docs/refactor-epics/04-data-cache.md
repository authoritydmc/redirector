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
- [ ] 1. SQLModel entities (clean v3 schema) + Alembic env (dual-driver `env.py`).
- [ ] 2. Repository layer + unit tests (sqlite memory).
- [ ] 3. `import-v2` script + sample-fixture test.
- [ ] 4. Redis `Cache` impl + `MemoryCache`; remove `config.redis_client` globals.
- [ ] 5. Settings table (`settings` key→JSON) + migration from `redirect.config.json`.
- [ ] 6. Indexes + query plan review on shortcuts/upstream_cache tables.
- [ ] 7. Backup/restore covers DB + settings + secrets-manifest (never secrets plaintext).

## `gh` snippet
```bash
gh issue create --title "[EPIC-04] Data & cache: async SQLAlchemy, Postgres-first, Redis abstraction" \
  --label "epic,backend" --body-file docs/refactor-epics/04-data-cache.md
```
