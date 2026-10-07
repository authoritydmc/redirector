# Redirector v3 Backend (FastAPI)

Async-first replacement for the Flask app (`app/`), built under
`docs/refactor-epics/01-backend-fastapi.md` (EPIC-01).
Branch: `v3/epic-01-backend-foundation`.

## Layout

```
backend/
├── main.py              # create_app(), lifespan, logging, error handlers
├── core/
│   ├── config.py        # pydantic-settings, REDIRECTOR_* env-first
│   ├── security.py      # admin password check, JWT bearer
│   ├── db.py            # async engine + per-request session Depends
│   ├── cache.py         # Cache protocol + MemoryCache (Redis lands in EPIC-04)
│   └── errors.py        # AppError -> RFC 7807 application/problem+json
├── models/entities.py   # SQLModel tables (clean v3 schema, breaks allowed)
├── modules/             # repository + service + schemas per domain
├── routers/             # thin HTTP layer (validation + status codes only)
├── workers/             # arq broker tasks (upstream resync; EPIC-06)
├── migrations/import_v2.py  # best-effort v2 DB/config import (EPIC-04)
└── requirements.txt     # v3 deps (uvicorn[standard] off-Windows, plain on win32)
```

## Run

```sh
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8123
# docs: http://127.0.0.1:8123/docs   openapi: /openapi.json
```

Scratch SQLite DB with v3 tables:

```sh
python -c "from sqlmodel import SQLModel, create_engine; import backend.models.entities; SQLModel.metadata.create_all(create_engine('sqlite:///./data/v3.db')); print('ok')"
export REDIRECTOR_DATABASE_URL="sqlite+aiosqlite:///./data/v3.db"
export REDIRECTOR_AUTO_REDIRECT_DELAY=0   # instant 302s; default 1 = countdown page
```

## Environment (`REDIRECTOR_*`)

| Variable | Default | Notes |
|---|---|---|
| `REDIRECTOR_DATA_DIR` | `./data` | Readiness probe file lives here |
| `REDIRECTOR_DATABASE_URL` | `sqlite+aiosqlite:///./data/redirect.db` | `asyncpg` URL for Postgres |
| `REDIRECTOR_REDIS_URL` | `redis://localhost:6379/0` | arq broker (`REDIRECTOR_JOB_BACKEND=arq`) + Redis lookup cache (`REDIRECTOR_CACHE_BACKEND=redis`); reserved for EPIC-04 full-cache work |
| `REDIRECTOR_CACHE_BACKEND` | `memory` | Shortcut lookup cache: `memory` (per-process) or `redis` (shared; outages degrade to DB reads) |
| `REDIRECTOR_JOB_BACKEND` | `in-process` | `in-process` (asyncio tasks, no broker) or `arq` (Redis + workers) |
| `REDIRECTOR_LOG_LEVEL` | `INFO` | stdlib logging, `%(asctime)s %(levelname)s [%(name)s]`; per-request access lines on `redirector.access` |
| `REDIRECTOR_AUTO_REDIRECT_DELAY` | `1` | Seconds before redirect; `0` = instant 302 |
| `REDIRECTOR_ADMIN_PASSWORD` | `admin` | Change in production |
| `REDIRECTOR_JWT_SECRET` | insecure dev default | Change in production |
| `REDIRECTOR_JWT_ALGORITHM` | `HS256` | |
| `REDIRECTOR_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | |
| App version | `VERSION` file | Never hardcoded; read at startup |

## Endpoints

| Router | Prefix | Notes |
|---|---|---|
| `health` | `/healthz`, `/health`, `/readyz` | No auth, no DB |
| `shortcuts` | `/api/v1/shortcuts` | CRUD + `POST /bulk-delete` |
| `upstreams` | `/api/v1/upstreams` | CRUD, `/cache` (+ entry purge, resync), `/check-logs`, `/check/stream` (SSE) |
| `resolve` | `/api/v1/resolve`, `/{pattern}` | Hot path; catch-all registered LAST |
| `jobs` | `/api/v1/jobs` | Enqueue (202) + list/status/cancel + `/events` SSE; in-process runner (arq swap later); boot reaps stale rows |
| `auth` | `/api/v1/auth` | `POST /login`, `GET /me`, `/api-keys` issue/list/revoke (JWT-session-only management; MFA: EPIC-05) |
| `config` | `/api/v1/admin/config` | Admin JWT, DB-backed settings |
| `backup` | `/api/v1/admin/backup` | Enqueue/list/download/delete archives (restore staged) |
| `metrics` | `/api/v1/metrics` | `/kpi` (typed schemas), `/live` |
| `qr` | `/qr/{pattern}`, `/api/v1/qr` | PNG bytes or base64 JSON |

## Tests & gates

```sh
# v3 suite (isolated from the v2 Flask/gevent suite — never mix in one run)
pytest tests/test_v3_*.py tests/test_backend_smoke.py tests/test_import_v2.py -v
ruff check backend/
mypy backend/          # strict; narrow carve-outs only for SQLAlchemy expr typing
flake8 backend/ --select=E9,F63,F7,F82
# API contract snapshot (EPIC-03): regenerate after any router/schema change
python scripts/export-openapi.py --check
```

Fixtures use in-memory `sqlite+aiosqlite` + `MemoryCache` via
`dependency_overrides[get_session]` — no Redis/DB required.

## Conventions (learned the hard way)

- **Static routes before dynamic ones.** Starlette matches in registration
  order and `"cache"` fails an `int` converter with 422 instead of falling
  through — `DELETE /cache` was once shadowed by `/{upstream_id}`.
  Same reason `/{pattern}` lives in the last-registered router.
- **No blocking calls in handlers.** CPU/fs work (`qrcode`, readiness probe)
  goes through `asyncio.to_thread`; outbound HTTP is `httpx.AsyncClient`
  with a 3 s budget (`verify` is client-level, never per-request).
- **Routers stay thin.** Rules live in `modules/*/service.py`, persistence in
  `repository.py`; routers only validate, map status codes, and convert to
  response models.

## Background workers (arq)

`POST /api/v1/jobs` runs in-process by default. For durable execution:

```sh
# Redis (local dev via WSL docker; CI provides a redis service):
wsl docker run -d --name redirector-redis -p 6379:6379 redis:8-alpine

export REDIRECTOR_JOB_BACKEND=arq
export REDIRECTOR_REDIS_URL="redis://localhost:6379/0"
uvicorn backend.main:app --port 8123 &   # API enqueues to Redis
arq backend.workers.settings.WorkerSettings  # worker drains the queue
```

Job rows stay the source of truth either way (poll `GET /api/v1/jobs/{id}`,
stream `/events`); only execution moves. Workers skip rows cancelled while
queued. `tests/test_v3_jobs_arq.py` covers the full loop with a burst
worker and skips cleanly without Redis.

## Status / non-goals

- `GET /{pattern}` is shadowed by the Flask proxy until EPIC-08 M3 flips it.
- Dockerfile/compose uvicorn switch lands with EPIC-04/06/08 — the Dockerfile
  still boots gunicorn/gevent for v2.
