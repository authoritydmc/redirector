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
  (Half done: merge is ruleset-blocked without PR + required checks —
  proven when a direct `main` push was rejected. Quarantine policy now
  documented in `DEVELOPMENT.md` (quarantine dir + `[flake]` issue + nightly
  runs, no silent deletes).)
- [ ] Images published `rajlabs/redirector-api:v3`, `rajlabs/redirector-web:v3` + compat `rajlabs/redirector:v3` (all-in-one).
- [x] Coverage + type gates enforced (no `--no-verify` culture).
  (Done: `validate.yml` backend-smoke enforces `--cov-fail-under=80` on
  `backend/modules` + `backend/core` (measured 82% with redis up); ruff +
  mypy strict already gated there. Hot-path-100% sub-target not claimed —
  `shortcuts/service.py` sits at 92%.)

## Tasks
- [x] 1. Monorepo move + import path codemod + `Makefile`/`Taskfile`.
  (Done at M4: `backend/` + `frontend/` + `docker/` + `docs/` separation with
  v2 root files removed; `Makefile` added (`dev/lint/test/e2e/build/smoke`,
  POSIX sh, Git Bash on Windows, LF-pinned via `.gitattributes`).)
- [x] 2. Ruff+mypy+pytest-asyncio baselines (fix or `noqa` with tickets, ratchet to zero).
  (Done on `v3/epic-01-backend-foundation`: `ruff check backend/` clean,
  `mypy backend/` strict clean with the documented SQLAlchemy carve-outs
  (`pyproject.toml`) plus two reasoned side-effect-import `noqa`s,
  pytest green locally and in CI (`validate.yml` gates all three).
  Async style is anyio + `asyncio.run` + sync TestClient instead of
  pytest-asyncio — deliberate: it keeps the v2 gevent suite runnable in
  isolation without plugin conflicts (see `test_backend_smoke.py`).
  Ratchet stands at zero.)
- [x] 3. Vitest+Playwright scaffolding + first 5 critical flows (login, create, redirect, upstream check, backup).
  (Done: `frontend/playwright.config.ts` + self-orchestrated `e2e/` — global-setup boots the real API on a scratch DB (with server-reuse probe), vite dev serves the SPA; 5 flows green locally in ~6s and in `frontend.yml` CI (python + backend deps + chromium). Vitest explicitly scoped away from e2e specs via `vitest.config.ts`.)
- [ ] 4. `ci.yml` rewrite + required checks + CODEOWNERS.
  (Partial: required checks enforced by the `mainProtect` ruleset — direct
  pushes rejected, PR + checks mandatory; `.github/CODEOWNERS` added. A
  single-`ci.yml` consolidation never happened; per-workflow files remain.)
- [x] 5. Dockerfiles + compose profiles (`dev`, `prod-sqlite`, `prod-postgres`, `scale`).
  (Done on `v3/epic-01-backend-foundation`: `docker/Dockerfile.api`
  (non-root, migrates on boot, healthcheck) + `docker/Dockerfile.web`
  (nginx + SPA, SSE-safe proxy) + `docker/compose.prod.yml` (api + web +
  worker + redis, secrets required, single migrator service so concurrent
  CREATE TYPE can't race) + `docker/compose.postgres.yml` overlay.
  Verified live in WSL docker 7/7 on BOTH profiles (SPA, login, CRUD,
  broker-drained backup job, terminating SSE). Dev profile is the M1
  harness; scale documented (needs postgres + redis; rate limits and
  in-process singleflight are per-process). CI image builds stay open
  under task 4.)
- [x] 6. ADRs + DEVELOPMENT.md + contributor quickstart video/gif (nice-to-have).
  (Done on `v3/epic-01-backend-foundation`: `docs/adr/0001-0003`
  (strangler-fig, clean-break schema, snapshot contract) and a v3-first
  `DEVELOPMENT.md` rewrite (backend/frontend/infra/migrations/M1/load).
  Video skipped as stated nice-to-have.)

## `gh` snippet
```bash
gh issue create --title "[EPIC-07] Quality & DevEx: typing, tests, CI/CD, Docker, monorepo" \
  --label "epic,dx" --body-file docs/refactor-epics/07-quality-devex.md
```
