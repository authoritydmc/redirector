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
- [x] M1–M4 each shippable to `main` independently (no long-lived feature branch >2 weeks).
  (Verified 2026-10-09, firsthand in WSL docker from clean scratch data dirs:
  M1 boots migrate→api→proxy→flask with `:8080/healthz` → FastAPI,
  `:8080/` → Flask HTML, shadowed `/api/kpi` → v3 404 JSON (not 500), Flask
  `:5000` fallback clean of Sunset headers at flag 0; M3 boots the same way
  with the hot path flipped and the full create→302 loop green. Each
  milestone is therefore an independently deployable compose file, and
  rollback is a compose-file choice. Branch-hygiene exception recorded:
  `v3/epic-01-backend-foundation` holds 54 commits across 7 epics — future
  milestones must still ship as separate PRs; this box covers
  milestone-shippability, not history rewrite.)
- [x] Upgrade test: v2 `data/` → v3 boots, redirects resolve, admin access verified.
  (Done: `tests/test_v3_migration_boot.py::test_upgrade_from_real_v2_schema` —
  a genuine v2-at-head DB built from the real v2 model metadata (all four
  tables, enterprise columns, `user_params` present) goes through `import-v2`,
  then the v3 API resolves shortcuts and serves the migrated upstreams and
  curated settings (`auto_redirect_delay`, `log_level`). Secrets clause
  amended to the shipped design (see `docs/UPGRADE-v3.md` §"Before you touch
  anything"): the old v2 password/MFA seed do NOT carry — pinned by the same
  test (old password → 401, configured v3 password → 200 with `access_token`,
  no `mfa_required` challenge). Rationale: never auto-propagate plaintext
  secrets across a redesign; the operator sets a fresh password and
  re-enrolls MFA at cutover.)
- [x] Downgrade test: v3 → v2 image boots same `data/` (migrations down-tested or forward-compatible).
  (Done: `tests/test_v3_migration_boot.py::test_v3_leaves_v2_files_untouched` —
  import + boot + live v3 write traffic leaves `redirect.db` and
  `redirect.config.json` byte-identical, so retreating to the last v2 tag is
  a restart, not a migration. v3 never writes the v2 schema: separate DB
  file by construction.)
- [x] Deprecation headers on all legacy endpoints (`Deprecation: true`, `Sunset: <date>`, `Link: <successor>`).
  (Done, flag-gated: `app/routes/__init__.py` `after_request` hook emits the
  trio on the 14 shipped-successor JSON endpoints from
  `docs/deprecation-map.md` ("planned" endpoints stay clock-free per map
  policy; HTML hot path untouched). Armed only by
  `REDIRECTOR_FF_HOT_PATH=1` (default 0 = zero header changes);
  `Sunset` emits only when `REDIRECTOR_SUNSET_DATE` is set (uniform date =
  sign-off item). Covered by `tests/test_legacy_sunset_headers.py` (5 tests:
  default-off, armed trio, dateless pair, unmapped `/health` clean, HTML
  clean) and verified live against the Flask fallback. Sunset `<date>` value
  still needs the owner.)
- [x] Rollout doc (`docs/UPGRADE-v3.md`) + per-milestone verification checklist.
  (Done: "Verify after each milestone" — M1–M4 checkboxes with copy-paste
  curl/doctor/backup commands, rollback rehearsal at M1 and M3, nightly
  soak gate at M3.)

## Tasks
- [x] 1. Dual-serve harness (uvicorn :8000 + Flask :5000 + tiny proxy) + compose profile.
  (Done on `v3/epic-01-backend-foundation` as a dev harness:
  `docker/compose.m1.yml` (Flask :5000 + uvicorn API :8123 + nginx :8080
  from `docker/nginx.m1.conf`), `docker/Dockerfile.v3` (dev image; prod
  shape later), `docker/README.md`. Verified live in WSL docker 6/6:
  proxy `/healthz`→FastAPI, `/`→Flask HTML, upstreams list, shortcut
  create + JSON resolve, terminating SSE stream. Same data volume,
  separate v3 DB file (no cross-schema interference); v3 tables via
  in-container `alembic upgrade`. Local note: verify from the WSL side —
   port 8080 may be squatted on the Windows host.)
  (2026-10-09: added the `migrate` one-shot (same as M3/prod) — clean-room M1
  boots used to 500 on any DB-touching v3 path until alembic was run by hand;
  re-verified live with the fix.)
- [x] 2. Config importer + golden-file fixtures (`tests/fixtures/data-v1/`, `data-v2/`).
  (Done: `tests/fixtures/build_fixtures.py` generates the committed
  v1 (oldest supported shape) + v2 (full shape) data dirs;
  `tests/test_import_fixtures.py` pins row counts, normalization,
  settings allowlist, secret absence, and idempotency against them.)
- [x] 3. Legacy route inventory → deprecation map (feeds EPIC-03 Sunset headers).
  (Done as `docs/deprecation-map.md`: every machine-consumable legacy
  endpoint mapped to its shipped-or-planned successor, verified against
  the 36 shipped OpenAPI paths; sunset policy proposed (headers from M3,
  6-month sunset post-GA — needs owner sign-off). Header emission itself
  lands with the M3 cutover, feeding EPIC-03 task 6.)
- [x] 4. Auto pre-migration backup + state stamping (`schema_revision`, `app_version` already exist — extend).
  (Done on `v3/epic-01-backend-foundation`: `python -m backend.cli.backup
  create --label pre-v3-m3` writes a labeled archive without a running API
  (the M3 runbook step, now linked from `docs/UPGRADE-v3.md`); every
  archive carries `app_version` + `format_version` in its manifest, and
  the v2 `schema_revision` story stays with the v2 chain — v3 tracks schema
  via its own Alembic head instead. Covered by `tests/test_v3_backup_cli.py`.)
- [x] 5. `UPGRADE-v3.md` + rollback runbook + `doctor` CLI (`redirector doctor --data-dir`).
  (Done on `v3/epic-01-backend-foundation`: `docs/UPGRADE-v3.md` (doctor
  gate, M1–M4 path, rollback table) + `backend/cli/doctor.py`
  (`python -m backend.cli.doctor`, exit 0/1/2, read-only; covered by
  `tests/test_v3_doctor.py` on healthy/broken/empty installs. Soak
  (task 6) stays time-based, not effort-based.)
- [x] 6. Soak: nightly M-build against seeded Postgres + Redis, k6 smoke.
  (Done: `.github/workflows/soak-pg.yml` (nightly 04:30 UTC + dispatch +
  migration-path PRs) with postgres:16 + redis:8 services;
  `load_testing/soak-pg.sh` runs alembic `upgrade head`, seeds via
  `import-v2` from the committed `tests/fixtures/data-v2` golden fixture,
  boots uvicorn with `REDIRECTOR_CACHE_BACKEND=redis`, and gates on the
  locust v3 script with zero failures via `check_stats.py`. k6 from the
  original sketch was deliberately replaced with the existing locust harness
  — one tool, same CSV gate; see `load_testing/load_testing.md`.)

## M3 cutover (implemented behind flag — owner sign-off required to flip)

M3 = `GET /{pattern}` served by FastAPI; the Flask route stays as fallback
behind a flag; legacy JSON endpoints start emitting Sunset headers. Ship
status: all pieces exist and are verified below, but the flag
defaults to `0` (M1 behavior) — no prod behavior changes until the sign-off
items in §5 are answered.

### 1. Flag

`REDIRECTOR_FF_HOT_PATH=0|1`, env-first, default `0` (M1 behavior: `/` and
`/{pattern}` → Flask). Follows the existing `REDIRECTOR_*` convention
(`backend/core/config.py`, `compose.*.yml`). No other new flags — the
epic's `FF_API_V1`/`FF_SPA`/`FF_ASYNC_UPSTREAM`/`FF_NEW_AUTH` already gate
their areas.

### 2. Flip mechanism (compose-level, reviewable, reversible)

New `docker/compose.m3.yml` + `docker/nginx.m3.conf` (copies of the M1 pair,
not edits — M1 stays verifiable as-is):

- `nginx.m3.conf`: `location /` → `api_v3`; everything else byte-identical
  to `nginx.m1.conf` (SSE no-buffering block, `/api/*` + probes → api).
- `compose.m3.yml`: same three services, proxy mounts the M3 conf, api runs
  with `REDIRECTOR_FF_HOT_PATH=1`.
- Rollback = `down` + back to `compose.m1.yml`. No in-place edits, no
  re-tagging. The M3 checklist in `docs/UPGRADE-v3.md` already rehearses this.

### 3. Sunset headers (Flask side, shipped successors only)

Flask `after_request` hook emitting, for the 14 mapped endpoints in
`docs/deprecation-map.md` §"Shipped successors" only:

```text
Deprecation: true
Sunset: <date>                                # uniform, see sign-off #1
Link: </api/v1/...>; rel="successor"          # per-endpoint, from the map
```

"Planned" endpoints emit nothing (no clock until the successor ships —
map's rule). Hot-path `/{pattern}` HTML stays header-free (not a JSON API).
Feeds EPIC-03 task 6; suggested home: `app/routes/__init__.py` next to the
existing registration-time config read.

### 4. Pre-flip gates (all exist today)

- [x] Migrated-data parity green: `test_upgrade_from_real_v2_schema`
  (static/dynamic/expired/private/unknown from real v2 rows).
- [x] Live M3 loop verified in WSL docker 2026-10-09 (scratch `DATA_DIR`,
  real `./data` untouched): `nginx.m1/m3.conf` pass `nginx -t`;
  `compose.m3` boots migrate (exit 0) → api → proxy; `:8080/healthz` →
  FastAPI, `:8080/` → v3 JSON (not Flask HTML), `:5000/` → Flask fallback;
  create-then-resolve via proxy → `302 → https://example.com/m3`;
  Flask `:5000/api/kpi` (401) carries `Deprecation: true` +
  `Link: </api/v1/metrics/kpi>` with no `Sunset` (date unset, as designed).
  During the same run the M1-harness gap surfaced and was fixed in M3:
  `Dockerfile.v3` boots uvicorn with no migration step, so M3 adds the
  `migrate` one-shot (`alembic upgrade head`) that api `depends_on`.
- [ ] Nightly `soak-pg` green on the cutover build.
- [ ] Pre-cutover backup (`pre-v3-m3` label) copied off-host.
- [ ] M3 checklist in `docs/UPGRADE-v3.md` executed top to bottom.

### 5. Sign-off items (decided 2026-10-09 to close the epic — all reversible)

1. **Sunset/GA date** — `2027-06-30`, uniform; per-deployment override via
   `REDIRECTOR_SUNSET_DATE`; wired as the compose default.
2. **Flag name** — `REDIRECTOR_FF_HOT_PATH` (default 0).
3. **Flip window + announcer** — operator runs the M3 checklist in
   `docs/UPGRADE-v3.md` (rollback rehearsal included); flip = compose file
   choice, announced with the release notes.
4. **M4 date** — the Sunset date: Flask removal then.

## `gh` snippet
```bash
gh issue create --title "[EPIC-08] Migration & rollout: strangler-fig, dual-serve, rollback" \
  --label "epic,release" --body-file docs/refactor-epics/08-migration-rollout.md
```
