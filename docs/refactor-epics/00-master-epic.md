# [EPIC-MASTER] Sitewide Architecture Refactor — FastAPI + React + Async-First

> **Type:** Epic (tracking issue) · **Labels:** `epic`, `architecture`, `breaking-change`
> **Status:** Proposed · **Target:** v3.0

## Vision

Evolve redirector from a Flask monolith (server-rendered Jinja, sync workers, JSON-file config-as-database) into a modern, decoupled stack:

```
┌─────────────┐      ┌──────────────────┐      ┌───────────────┐
│ React SPA   │─────▶│ FastAPI (`/api`) │─────▶│ Postgres/SQLite│
│ Vite + TS   │◀─────│ async, OpenAPI   │◀─────│ + Redis cache  │
└─────────────┘      └──────────────────┘      └───────────────┘
        ▲ redirect (`/<shortcut>`) served by FastAPI, <5ms cached p99
```

**Why now (evidence from current tree):**
- `app/__init__.py:6-9` gevent `monkey.patch_all()` + gunicorn sync workers — fragile, untestable locally, blocks true async.
- `app/routes/upstream_routes.py:5`, `app/routes/routes.py:369`, `app/utils/versioning.py:19` — blocking `requests` calls on the redirect hot path, sequential upstream fan-out.
- `app/config.py` (427 lines) — single god-object; `redirect.config.json` stores `admin_password`, `session_secret`, MFA seeds. No schema validation, no env-first 12-factor config.
- 32 Jinja templates in `app/templates/` + Tailwind via CDN — no component reuse, no type safety, every UI change ships backend.
- 7,231 LOC Python, ~7 test files, no typing/mypy, no API contract (no OpenAPI), ad-hoc JSON shapes per route.
- Auth = Flask `session['admin_logged_in']` + plaintext password compare (`redirection_routes.py:32`, `routes.py:34`) — no RBAC, no API keys, no JWT.

## Non-goals for v3.0
- No multi-tenancy / orgs (defer to v3.x).
- No rewrite of backup/restore format (must stay backward compatible).
- No dropping SQLite (remains default for self-host; Postgres is recommended scale path).

## Child epics (create one issue each, link back here)
1. `[EPIC-01]` Backend: Flask → FastAPI (async, DI, OpenAPI) — `docs/refactor-epics/01-backend-fastapi.md`
2. `[EPIC-02]` Frontend: Jinja → React SPA (Vite + TS) — `docs/refactor-epics/02-frontend-react.md`
3. `[EPIC-03]` API contract: versioning, pagination, error envelope — `docs/refactor-epics/03-api-contract.md`
4. `[EPIC-04]` Data & cache: async SQLAlchemy, Postgres-first, Redis abstraction — `docs/refactor-epics/04-data-cache.md`
5. `[EPIC-05]` Auth & security: JWT + RBAC + API keys, secrets hygiene — `docs/refactor-epics/05-auth-security.md`
6. `[EPIC-06]` Performance & realtime: async upstream fan-out, background jobs, SSE — `docs/refactor-epics/06-perf-realtime.md`
7. `[EPIC-07]` Quality & DevEx: typing, tests, CI/CD, Docker, monorepo — `docs/refactor-epics/07-quality-devex.md`
8. `[EPIC-08]` Migration & rollout: strangler-fig, dual-serve, rollback — `docs/refactor-epics/08-migration-rollout.md`

## Global acceptance criteria
- [x] `GET /<shortcut>` p99 < 10ms cached, < 150ms uncached (measure with `load_testing/`).
  (Measured 2026-10-08: 7.2ms / 6.1ms on container-native Linux —
  see `load_testing/load_testing.md` for the full topology matrix and
  the fsync mechanism note.)
- [x] OpenAPI at `/api/docs` covers 100% of public endpoints; breaking changes only under `/api/v1` → `/api/v2`.
  (Done in practice: the committed `docs/openapi.json` is generated from
  the routers so coverage is complete by construction (36 paths), the
  `--check` gate fails unreviewed drift, and ADR-0003 records the
  versioning rule. Served at `/docs` + `/openapi.json`, not `/api/docs`
  as worded here — same contract, stock FastAPI paths.)
- [x] `data/` (db + config + backups) from v2.x boots on v3.0 with zero manual steps (auto-migrate).
  (Proven by `tests/test_v3_migration_boot.py`: synthetic v2 data dir →
  `import-v2` → boot API on the migrated DB → redirect resolves, with no
  operator steps. Secrets intentionally excluded — admin re-authenticates
  with the configured password.)
- [x] Single-command dev: `docker compose up` and `npm run dev` + `uvicorn` with hot reload.
  (`docker compose -f docker/compose.prod.yml up -d --build` (needs the two
  secrets in env), `npm run dev` in `frontend/`,
  `uvicorn backend.main:app --reload` — each one command; prod stack verified
  live end to end, including the M1/M3 harnesses before their M4 removal.)
- [ ] CI gate: ruff + mypy (strict on backend) + pytest + vitest + Playwright e2e all green.

## Sequencing
```
Phase A (parallelizable): 03 API contract → 01 Backend + 04 Data
Phase B: 02 Frontend (against mocked contract), 05 Auth
Phase C: 06 Perf/realtime, 07 Quality/DevEx hardening
Phase D: 08 Migration, cutover, deprecate Flask
```

## Risks
- Big-bang rewrite stall → mitigate with strangler-fig (EPIC-08): FastAPI serves `/api/v1/*` first while Flask still serves HTML.
- Config migration bricking installs → golden-file tests on real `data/` fixtures from v1/v2.

## How to create these on GitHub
See `docs/refactor-epics/README.md` for one-command `gh issue create` snippets per epic.
