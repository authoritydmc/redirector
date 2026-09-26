# [EPIC-04] Data & cache: async SQLAlchemy, Postgres-first, Redis abstraction

> **Labels:** `epic`, `backend`, `database`, `caching` · **Parent:** EPIC-MASTER · **Estimate:** M (2–3 weeks)

## Problem
- SQLite-first with runtime WAL/`busy_timeout` hacks (`app/__init__.py:30-56`) because N gevent workers share one file.
- `Config.repair_database_path()` / `resolve_database_uri` (`config.py:144-192`, `utils/paths.py`) — heroic but symptom-fixing: relative-vs-absolute SQLite confusion.
- Redis is a raw global `config.redis_client` with ping-and-disable fallback (`config.py:245-275`); callers branch on `redis_enabled` everywhere; no interface → untestable, no TTL discipline.
- Models in `model/` (`redirect.py`, `upstream_cache.py`, `upstream_check_log.py`, `user_param.py`) are sync; Alembic env assumes Flask (`migrations/env.py`).

## Proposal
- **Async SQLAlchemy 2.0**: `create_async_engine` + `async_sessionmaker`; repository layer per module (`ShortcutsRepository`, …) so routes never touch `session` directly.
- **Postgres-first in code, SQLite-default in packaging**: same Alembic history boots both; CI matrix tests `sqlite+aiosqlite`, `postgres:16`, (optional) `mysql:8`.
- **Keep table names + columns** so v2 → v3 migration is metadata-only; add missing indexes (`pattern` unique lower, `upstream_name`, `expires_at`) + `updated_at` triggers where useful.
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
- [ ] Same Alembic head boots fresh SQLite + Postgres; v2 `data/redirect.db` upgrades with zero data loss (golden-file test).
- [ ] All DB access async; no `db.session` globals in request path.
- [ ] Cache hit rate visible in `/metrics`; p99 redirect latency meets master target.
- [ ] `REDIRECTOR_DATA_DIR` + `DATABASE_URL` + `REDIS_URL` documented; JSON config import path tested.

## Tasks
- [ ] 1. Async-ify models + Alembic env (dual-driver `env.py`).
- [ ] 2. Repository layer + unit tests (sqlite memory).
- [ ] 3. Redis `Cache` impl + `MemoryCache`; remove `config.redis_client` globals.
- [ ] 4. Settings table (`settings` key→JSON) + migration from `redirect.config.json`.
- [ ] 5. Indexes + query plan review on shortcuts/upstream_cache tables.
- [ ] 6. Backup/restore covers DB + settings + secrets-manifest (never secrets plaintext).

## `gh` snippet
```bash
gh issue create --title "[EPIC-04] Data & cache: async SQLAlchemy, Postgres-first, Redis abstraction" \
  --label "epic,backend" --body-file docs/refactor-epics/04-data-cache.md
```
