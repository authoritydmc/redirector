# Development Guide

Windows dev machine, Linux CI/Docker — keep scripts POSIX-compatible.
FastAPI + React stack (`backend/`, `frontend/`); decisions: `docs/adr/`.

## Backend (FastAPI)

```sh
python -m venv .venv
.venv/Scripts/Activate.ps1            # Windows; `source .venv/bin/activate` on POSIX
.venv/Scripts/python -m pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8123   # docs: 127.0.0.1:8123/docs
```

Scratch DB + env knobs:

```sh
.venv/Scripts/python -c "from sqlmodel import SQLModel, create_engine; import backend.models.entities; SQLModel.metadata.create_all(create_engine('sqlite:///./data/v3.db'))"
$env:REDIRECTOR_DATABASE_URL = "sqlite+aiosqlite:///./data/v3.db"
$env:REDIRECTOR_AUTO_REDIRECT_DELAY = "0"   # instant 302s
```

Gates (same as CI `validate.yml` → backend-smoke):

```sh
.venv/Scripts/python -m pytest tests/test_v3_*.py tests/test_backend_smoke.py tests/test_import_v2.py
.venv/Scripts/python -m ruff check backend/
.venv/Scripts/python -m mypy backend/
.venv/Scripts/python scripts/export-openapi.py --check   # after router/schema edits
```

Notes: single-process suite; Redis-backed tests skip cleanly without a broker; PG legs need
`TEST_POSTGRES_URL`.

## Frontend (React SPA)

```sh
cd frontend && npm install && npm run dev   # http://localhost:5173, /api proxied to :8123
npm run typecheck && npm test && npm run build
npm run codegen                             # regenerate src/lib/api.ts from docs/openapi.json
```

## Infrastructure (WSL docker)

```sh
wsl docker run -d --name redirector-redis -p 6379:6379 redis:8-alpine
wsl docker run -d --name redirector-pg -e POSTGRES_HOST_AUTH_METHOD=trust -p 5432:5432 postgres:16-alpine
$env:TEST_POSTGRES_URL = "postgresql+asyncpg://postgres@127.0.0.1:5432/postgres"
```

## Migrations (Alembic, v3 history only)

```sh
alembic -c backend/alembic.ini upgrade head
alembic -c backend/alembic.ini -x url=<URL> upgrade head
# New revision: upgrade a scratch DB first, then autogenerate, then review.
# Never upgrade a v2-stamped database file — import v2 data instead.
```

## Production stack

```sh
REDIRECTOR_ADMIN_PASSWORD=... REDIRECTOR_JWT_SECRET=... \
  docker compose -f docker/compose.prod.yml up -d --build   # :80
# Verify from the WSL side (Windows :80 may be squatted); see docker/README.md.
```

## Load smoke

```sh
sh load_testing/load-smoke.sh   # LOAD_VUS / LOAD_RATE / LOAD_TIME / LOAD_PORT overrides
```

## Coming from v2

The Flask app was removed in M4. Migrate data with the one-shot importer
(see [`docs/UPGRADE-v3.md`](docs/UPGRADE-v3.md)), then run the stack above.
The full test suite is v3-only now: `python -m pytest tests/ -v`.
