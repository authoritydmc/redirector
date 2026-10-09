# AGENTS.md — instructions for AI coding agents working in this repo

## Stack at a glance
- Stack: FastAPI + SQLAlchemy (async) + Alembic (`backend/`, `backend/alembic/`), React SPA (`frontend/`). Uvicorn + arq in Docker (`docker/compose.prod.yml`).
- Landing page: root `index.html` (deployed to GitHub Pages via `.github/workflows/static.yml`).
- Version source of truth: `VERSION` file at repo root. Never hardcode versions elsewhere.
- Tests: `python -m pytest tests/ -v` (single process). Backend gates: `ruff check backend/`, `mypy backend/`. Lint: `flake8 backend/ tests/`.
- Windows dev machine, but CI/Docker are Linux — keep scripts POSIX-compatible (`scripts/`).

## RULE: landing page stays in sync with the release cycle (always)

`index.html` carries version badges (nav `vX.Y.Z`, hero `Version X.Y.Z Released`).
They MUST match the `VERSION` file on every commit. Enforcement is automated, but
agents must still do the sync — CI fails otherwise.

- **After ANY of these, run the sync and commit the result:**
  - bumping `VERSION` (`python get_version.py --bump [patch|minor|major]`)
  - editing `CHANGELOG.md`
  - adding/changing user-facing features, screenshots in `assets/img/`, or quick-start instructions
- **Command:** `python scripts/sync-landing-version.py` (stamps `VERSION` into `index.html`)
- **Verify:** `python scripts/sync-landing-version.py --check` (exit 0 = in sync; this is what `validate.yml` runs)
- Safety net: `static.yml` re-stamps at Pages deploy time and triggers on `VERSION` changes, so the live site self-heals even if a PR slips through — but do not rely on it; keep the committed file in sync.

## Release checklist (follow in order)
1. `python get_version.py --bump <patch|minor|major>` → verify `VERSION`.
2. Add a `CHANGELOG.md` entry under a new `## [X.Y.Z] - YYYY-MM-DD` heading (Keep a Changelog).
3. `python scripts/sync-landing-version.py` → confirm `index.html` badges updated.
4. Run `python -m pytest tests/ -v` and `python scripts/sync-landing-version.py --check`.
5. Commit everything together, then tag: `pwsh scripts/create-release-tag.ps1 -Version vX.Y.Z`.
6. Tag push fires `release.yml` (GitHub Release); the `main` push fires `static.yml` (Pages redeploy with stamped version).

## Other conventions
- `data/` is runtime state (db, config, backups) — never commit its contents.
- Config changes go through `app/config.py` atomic writer; never write `redirect.config.json` by hand in code.
- One canonical version comparer only: `app/utils/versioning.py`. No version math in templates.
- Docs live in `docs/`; architecture refactor epics in `docs/refactor-epics/` (with `gh` snippets per epic).
