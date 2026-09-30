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
| `REDIRECTOR_REDIS_URL` | `redis://localhost:6379/0` | Reserved for EPIC-04 cache |
| `REDIRECTOR_LOG_LEVEL` | `INFO` | stdlib logging, `%(asctime)s %(levelname)s [%(name)s]` |
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
| `upstreams` | `/api/v1/upstreams` | CRUD, `/cache` + `/cache/resync`, `/check/stream/{pattern}` (SSE) |
| `resolve` | `/api/v1/resolve`, `/{pattern}` | Hot path; catch-all registered LAST |
| `auth` | `/api/v1/auth` | `POST /login`, `GET /me` (MFA/API keys: EPIC-05) |
| `config` | `/api/v1/admin/config` | Admin JWT, DB-backed settings |
| `metrics` | `/api/v1/metrics` | `/kpi` (typed schemas), `/live` |
| `qr` | `/qr/{pattern}`, `/api/v1/qr` | PNG bytes or base64 JSON |

## Tests & gates

```sh
# v3 suite (isolated from the v2 Flask/gevent suite — never mix in one run)
pytest tests/test_v3_*.py tests/test_backend_smoke.py tests/test_import_v2.py -v
ruff check backend/
mypy backend/          # strict; narrow carve-outs only for SQLAlchemy expr typing
flake8 backend/ --select=E9,F63,F7,F82
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

## Status / non-goals

- `GET /{pattern}` is shadowed by the Flask proxy until EPIC-08 M3 flips it.
- `backend/workers/` (arq) + Dockerfile/compose uvicorn switch land with
  EPIC-04/06/08 — the Dockerfile still boots gunicorn/gevent for v2.
