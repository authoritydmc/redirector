# [EPIC-01] Backend: Flask → FastAPI (async-first, DI, OpenAPI)

> **Labels:** `epic`, `backend`, `fastapi` · **Parent:** EPIC-MASTER · **Estimate:** L (3–5 weeks)

## Problem
- Flask sync + `gevent.monkey.patch_all()` (`app/__init__.py:6-9`) — implicit async, breaks debuggers/tests, each gunicorn worker re-resolves everything.
- No dependency injection: routes reach into global `config` singleton and `model.db` directly; untestable without full app boot (`create_app()` connects Redis, stamps state, purges SSO cache).
- No request/response schemas: `request.form.get(...)` everywhere (`redirection_routes.py:57-98`, `upstream_routes.py:23-52`), no Pydantic validation, no OpenAPI docs.
- Blocking I/O on hot paths: `requests` for upstream checks (`upstream_routes.py:5`) and version checks (`utils/versioning.py:19`).

## Proposal
New package `backend/` (or `api/`) built on **FastAPI + Uvicorn**:

```
backend/
├── main.py              # create_app() equivalent, lifespan events
├── core/
│   ├── config.py        # pydantic-settings, env-first (REDIRECTOR_*)
│   ├── security.py      # password hashing, JWT, API keys
│   ├── db.py            # async engine + session Depends
│   ├── cache.py         # Redis abstraction (protocol + noop/fake)
│   └── errors.py        # AppError → RFC 7807 problem envelope
├── modules/
│   ├── shortcuts/       # router + service + schemas + repository
│   ├── upstreams/
│   ├── admin/
│   ├── auth/            # login, MFA, API keys
│   ├── metrics/
│   └── redirects/       # hot path: GET /{pattern}
└── workers/             # arq tasks (upstream resync, purge)
```

Key decisions:
- **Async SQLAlchemy 2.0** (`create_async_engine`), `async_session` per-request via `Depends`. Sync SQLite path kept for single-file installs via `aiosqlite`.
- **Pydantic v2** schemas for every request/response; generate OpenAPI automatically.
- **Lifespan** replaces `app_startup_banner` + `stamp_install_state` spaghetti: discrete startup steps (migrate → seed → warm cache).
- **Delete gevent/gunicorn**; serve with `uvicorn[standard]` (h11+httptools+uvloop). Dockerfile CMD change only.
- Replace `requests` with `httpx.AsyncClient` (shared, pooled, timeout预算 3s default).

## Acceptance criteria (status on `v3/epic-01-backend-foundation`)
- [x] `GET /{pattern}`, CRUD shortcuts, upstream CRUD, auth, metrics all served by FastAPI with OpenAPI coverage.
- [x] Zero `gevent` / `monkey` imports; `grep -r gevent backend/` empty.
- [x] Zero blocking `requests` in request handlers; `httpx` async only.
- [x] `pytest` suite runs without Redis/DB (fake cache + sqlite memory via DI override).
- [x] Ruff + mypy strict pass on `backend/` (narrow carve-outs for SQLAlchemy
  expression typing, documented in `pyproject.toml`; gated in `validate.yml`).

## Phased tasks
- [x] 1. Scaffold `backend/main.py` + health/ready (`/healthz`, `/readyz`) + structured logging.
  (Lifespan migrate → seed → warm cache is EPIC-04.)
- [x] 2. Port `core/config.py` (pydantic-settings) with v2 `redirect.config.json` importer (backward compat).
- [x] 3. SQLModel entities (clean v3 schema — breaks allowed, see EPIC-04) + async engine/session DI.
- [x] 4. Port `redirects` hot path first (highest value), then shortcuts CRUD, then upstreams, then admin/misc.
- [x] 5. Replace `requests` → `httpx` in upstream + version checks.
- [x] 6. Wire `arq` (or `dramatiq`) for resync/purge; remove in-request bulk loops.
  (Done on `v3/epic-01-backend-foundation`: `ArqJobRunner` broker backend
  + `backend/workers/` tasks + jobs API; sync endpoints retained for
  admin-scale use per the parenthetical. Purge still inline — bulk-purge
  graduates to a worker if it outgrows request scope.)
- [ ] 7. Dockerfile + compose update (uvicorn workers, `--loop uvloop`).
  (Blocked on EPIC-08 M3 — the image still boots gunicorn/gevent for v2.)

## Out of scope
- React SPA (EPIC-02); FastAPI initially renders nothing — serve legacy Flask templates via proxy (see EPIC-08).
- Auth model change (EPIC-05) — keep session-compat shim initially.

## `gh` snippet
```bash
gh issue create --title "[EPIC-01] Backend: Flask → FastAPI (async-first, DI, OpenAPI)" \
  --label "epic,backend" --body-file docs/refactor-epics/01-backend-fastapi.md
```
