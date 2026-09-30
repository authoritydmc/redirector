# [EPIC-07] Quality & DevEx: typing, tests, CI/CD, Docker, monorepo

> **Labels:** `epic`, `dx`, `ci-cd`, `testing` · **Parent:** EPIC-MASTER · **Estimate:** M (2 weeks, ongoing)

## Problem
- No type checking; 7k+ LOC dynamic Python + untyped JS-in-Jinja. `flake8 app/` only lint; no formatter contract.
- ~7 pytest files, no async tests, no frontend tests, no e2e; `load_testing/` manual.
- Docker: single-stage image, gevent+gunicorn, serves HTML+API together; compose lacks profiles (dev vs prod vs scale-to-postgres).
- Repo root mixes concerns (`app.py`, `wsgi.py`, `gunicorn.conf.py`, `index.html`); no `backend/`/`frontend/` separation.

## Proposal: target layout
```
redirector/
├── backend/               # FastAPI (EPIC-01)
├── frontend/              # React SPA (EPIC-02)
├── migrations/            # Alembic (shared)
├── tests/                 # pytest (backend) + contract
├── load_testing/          # k6 (EPIC-06)
├── docker/
│   ├── Dockerfile.api
│   ├── Dockerfile.web    # nginx serving frontend dist
│   └── compose.*.yml
├── docs/ (incl. refactor-epics/, api-inventory.md, ADRs)
└── .github/workflows/     # ci.yml (matrix), e2e.yml, perf.yml, release.yml
```

Toolchain:
- **Backend:** `ruff` (lint+format), `mypy --strict`, `pytest -n auto` + `pytest-asyncio`, coverage ≥80% on `modules/` (hot path 100%).
- **Frontend:** `tsc --noEmit`, `eslint`, `vitest`, `playwright` (chromium+firefox) against compose stack.
- **CI:** `ci.yml` matrix (py3.11/3.12 × sqlite/postgres), contract-diff check, image build + Trivy scan, `pip-audit`/`npm audit`.
- **Docker:** multi-stage (uv/pip → runtime slim; node → nginx); non-root user; `/app/data` volume unchanged; healthcheck → `/healthz`.
- **Docs:** ADRs (`docs/adr/0001-fastapi.md`, `0002-react-spa.md`, …), `DEVELOPMENT.md` refresh (one-command dev), changelog automation (already present via `release.yml` — extend to frontend).

## Acceptance criteria
- [ ] `make dev / lint / test / e2e` (or `task`) works on Win+Linux+macOS for a fresh clone.
- [ ] CI green is required for merge; flaky-test quarantine policy documented.
- [ ] Images published `rajlabs/redirector-api:v3`, `rajlabs/redirector-web:v3` + compat `rajlabs/redirector:v3` (all-in-one).
- [ ] Coverage + type gates enforced (no `--no-verify` culture).

## Tasks
- [ ] 1. Monorepo move + import path codemod + `Makefile`/`Taskfile`.
- [ ] 2. Ruff+mypy+pytest-asyncio baselines (fix or `noqa` with tickets, ratchet to zero).
- [ ] 3. Vitest+Playwright scaffolding + first 5 critical flows (login, create, redirect, upstream check, backup).
- [ ] 4. `ci.yml` rewrite + required checks + CODEOWNERS.
- [ ] 5. Dockerfiles + compose profiles (`dev`, `prod-sqlite`, `prod-postgres`, `scale`).
- [ ] 6. ADRs + DEVELOPMENT.md + contributor quickstart video/gif (nice-to-have).

## `gh` snippet
```bash
gh issue create --title "[EPIC-07] Quality & DevEx: typing, tests, CI/CD, Docker, monorepo" \
  --label "epic,dx" --body-file docs/refactor-epics/07-quality-devex.md
```
