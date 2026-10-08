# Development Guide (v3 track)

Windows dev machine, Linux CI/Docker — keep scripts POSIX-compatible.
The v2 Flask app still ships; this guide is for the v3 FastAPI + React track
(`v3/epic-01-backend-foundation`, PR #114). Decisions: `docs/adr/`.

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

Notes: v3 tests run isolated from the v2 gevent suite (never mix in one
run); Redis-backed tests skip cleanly without a broker; PG legs need
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
wsl docker run -d --name redirector-pg -e POSTGRES_PASSWORD=redirector-dev-pw -p 5432:5432 postgres:16-alpine
$env:TEST_POSTGRES_URL = "postgresql+asyncpg://postgres:redirector-dev-pw@127.0.0.1:5432/postgres"
```

## Migrations (Alembic, v3 history only)

```sh
alembic -c backend/alembic.ini upgrade head
alembic -c backend/alembic.ini -x url=<URL> upgrade head
# New revision: upgrade a scratch DB first, then autogenerate, then review.
# Never upgrade a v2-stamped database file — import v2 data instead.
```

## M1 dual-serve harness

```sh
docker compose -f docker/compose.m1.yml up -d --build   # :8080, scratch DATA_DIR=... recommended
# Verify from the WSL side (Windows :8080 may be squatted); see docker/README.md.
```

## Load smoke

```sh
sh load_testing/load-smoke.sh   # LOAD_VUS / LOAD_RATE / LOAD_TIME / LOAD_PORT overrides
```

## Legacy v2 (Flask) track

```sh
pip install -r requirements.txt
python app.py                 # or gunicorn via entrypoint.sh / docker-compose.yml
pytest                        # full suite incl. v2 (separate process from v3 runs)
flask db upgrade              # v2 migrations in migrations/
```
