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
| `REDIRECTOR_AUTH_LOCKOUT_MAX_ATTEMPTS` | `10` | Failed logins per IP inside the window before 429 lockout |
| `REDIRECTOR_AUTH_LOCKOUT_WINDOW_MINUTES` | `15` | Lockout counting window |
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
| `auth` | `/api/v1/auth` | `POST /login` (MFA-challenged when enrolled), `GET /me`, `/api-keys` issue/list/revoke (JWT-session-only), `/mfa/*` TOTP enroll/verify (WebAuthn: EPIC-05) |
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

## Authorization audit (EPIC-05 task 3, scopes enforced on the admin surface)

Transports (EPIC-03 task 5: the full auth scheme): `Authorization: Bearer`
carries either a JWT (interactive login, 60 min default, scope `*`) or an
API key (`rk_<prefix>_<secret>`, issued scopes). Short-lived
purpose-scoped tokens exist too (`mfa-pending`: `mfa/verify` only, rejected
as general credentials). Auth failures are 401 (`auth:required`,
`auth:invalid-token`, `auth:invalid-api-key`); authenticated-but-rejected
is 403 (`auth:forbidden`, `auth:insufficient-scope`) — automation can tell
"bad credential" from "narrow key" apart.

JWT sessions carry scope `*` (implies everything). API keys (`rk_*`) carry
the scopes they were issued with; `RequireScopes(...)` denies with 403
`auth:insufficient-scope`. Key management itself stays JWT-only.

| Surface | Requirement |
|---|---|
| `GET /api/v1/admin/config`, `GET /api/v1/admin/backup`, `GET /api/v1/admin/backup/{name}` | `admin:read` |
| `PATCH /api/v1/admin/config`, backup create/restore/delete, `POST/GET/DELETE /api/v1/auth/api-keys` (JWT-only) | `admin:write` (management: JWT session) |
| Shortcuts CRUD, upstreams, jobs, resolve, metrics, QR, `/me` | public for now — full-route enforcement is the RBAC cutover (open) |

## Schema migrations (Alembic, EPIC-04 task 1)

v3 has its own history in `backend/alembic/` (v2's `migrations/` is Flask-bound;
v2 data arrives via `import-v2`, never via upgrade). Async env works off the
same async URLs as the app — no sync drivers needed.

```sh
alembic -c backend/alembic.ini upgrade head                       # migrate
alembic -c backend/alembic.ini -x url=<URL> upgrade head         # ...another DB
# New revision: upgrade a scratch DB to head FIRST (autogenerate diffs
# against a current DB and refuses a behind one with this async env),
# then generate:
alembic -c backend/alembic.ini -x url=<SCRATCH-URL> upgrade head
alembic -c backend/alembic.ini -x url=<SCRATCH-URL> revision --autogenerate -m "what"
```

Rules: always review autogenerated scripts (add the missing
`import sqlmodel.sql.sqltypes` when column types reference it); never run
`upgrade head` against a v2-stamped database file (foreign
`alembic_version` fails loudly by design — import v2 data instead).
Lifespan auto-migrate lands with the EPIC-08 container cutover.
