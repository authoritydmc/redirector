# [EPIC-08] Migration & rollout: strangler-fig, dual-serve, rollback

> **Labels:** `epic`, `migration`, `release` · **Parent:** EPIC-MASTER · **Estimate:** M (1–2 weeks + soak)

## Why this epic exists
Big-bang rewrites strand self-hosters. This repo has real `data/` installs (db + JSON config + backups + `.redirector-state.json`). v3 must boot v2 data untouched.

## Strategy: strangler-fig, 4 milestones
```
M1: FastAPI alongside Flask — same container, FastAPI on :8000 serves
    /api/v1/* + /healthz; nginx/Flask proxies unknown paths. Legacy UI intact.
M2: React SPA static build served at /app (FastAPI StaticFiles); Flask
    templates frozen, accessible via ?legacy=1. All new UI hits /api/v1.
M3: Redirect hot path cutover — GET /{pattern} served by FastAPI
    (cache→db→upstream fan-out). Flask route kept as fallback behind flag.
M4: Flask removed. Single uvicorn image. v2 compose file deprecated.
```

- **Feature flags** (env-first, `REDIRECTOR_FF_*`): `FF_API_V1`, `FF_SPA`, `FF_ASYNC_UPSTREAM`, `FF_NEW_AUTH`. Each default-off until M-gate passes.
- **Data compat:** Alembic migrations additive-only until M4; JSON config importer (`redirect.config.json` → settings table + secrets vault) runs idempotently on every boot; golden-file tests on anonymized v1/v2 `data/` snapshots.
- **Rollback:** every milestone is reversible via env flag or image tag (`rajlabs/redirector:v2` ↔ `:v3-M1…M4`); backup auto-created pre-migration (`backup create --label "pre-v3-Mx"`); restore path tested in CI.

## Acceptance criteria
- [ ] M1–M4 each shippable to `main` independently (no long-lived feature branch >2 weeks).
- [ ] Upgrade test: v2.8 `data/` → v3 boots, redirects resolve, admin can log in with old password, MFA intact.
- [ ] Downgrade test: v3 → v2 image boots same `data/` (migrations down-tested or forward-compatible).
- [ ] Deprecation headers on all legacy endpoints (`Deprecation: true`, `Sunset: <date>`, `Link: <successor>`).
- [ ] Rollout doc (`docs/UPGRADE-v3.md`) + per-milestone verification checklist.

## Tasks
- [x] 1. Dual-serve harness (uvicorn :8000 + Flask :5000 + tiny proxy) + compose profile.
  (Done on `v3/epic-01-backend-foundation` as a dev harness:
  `docker/compose.m1.yml` (Flask :5000 + uvicorn API :8123 + nginx :8080
  from `docker/nginx.m1.conf`), `docker/Dockerfile.v3` (dev image; prod
  shape later), `docker/README.md`. Verified live in WSL docker 6/6:
  proxy `/healthz`→FastAPI, `/`→Flask HTML, upstreams list, shortcut
  create + JSON resolve, SSE stream terminating with buffering off.
  Same data volume, separate v3 DB file (no cross-schema interference);
  v3 tables via in-container `alembic upgrade`. Local note: verify from
  the WSL side — port 8080 may be squatted on the Windows host.)
- [ ] 2. Config importer + golden-file fixtures (`tests/fixtures/data-v1/`, `data-v2/`).
- [x] 3. Legacy route inventory → deprecation map (feeds EPIC-03 Sunset headers).
  (Done as `docs/deprecation-map.md`: every machine-consumable legacy
  endpoint mapped to its shipped-or-planned successor, verified against
  the 36 shipped OpenAPI paths; sunset policy proposed (headers from M3,
  6-month sunset post-GA — needs owner sign-off). Header emission itself
  lands with the M3 cutover, feeding EPIC-03 task 6.)
- [ ] 4. Auto pre-migration backup + state stamping (`schema_revision`, `app_version` already exist — extend).
- [ ] 5. `UPGRADE-v3.md` + rollback runbook + `doctor` CLI (`redirector doctor --data-dir`).
- [ ] 6. Soak: nightly M-build against seeded Postgres + Redis, k6 smoke.

## `gh` snippet
```bash
gh issue create --title "[EPIC-08] Migration & rollout: strangler-fig, dual-serve, rollback" \
  --label "epic,release" --body-file docs/refactor-epics/08-migration-rollout.md
```
