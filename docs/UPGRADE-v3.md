# Upgrading to v3 (EPIC-08 task 5)

Target reader: a self-hoster on v1/v2 with a `data/` directory (database +
`redirect.config.json` + backups). Goal: v3 serving the same redirects with
zero manual data surgery.

## Before you touch anything

1. **Back up from v2** (admin UI or CLI) and copy the archive off the host.
2. **Run the doctor** — read-only, safe any time:
   ```sh
   python -m backend.cli.doctor --data-dir ./data
   ```
   Exit 0 means migratable (warnings allowed); exit 1 lists problems to fix
   first. Never upgrade past a failing doctor.
3. Note your admin password and MFA state: **secrets do not migrate**
   (by design — `import-v2` skips `admin_password`, `session_secret`,
   `mfa.*`). You will log in with the configured password after cutover
   and re-enroll MFA.

## The upgrade (strangler-fig milestones)

- **M1** — run both: `docker compose -f docker/compose.m1.yml up -d --build`.
  Flask keeps serving the UI on `/`; `/api/v1/*` already answers from
  FastAPI. Verify: `GET /healthz` → FastAPI, `/` → Flask HTML.
- **Populate v3**: `POST /api/v1/admin/backup` is v3-native; for v2 data run
  the importer against a scratch DB, then point the API at it:
  ```sh
  python -m backend.migrations.import_v2 --data-dir ./data \
      --database-url sqlite:///./data/v3.db
  ```
  Curated non-secret settings carry over; review its printed row counts.
- **M2** — serve the SPA build at `/app`; freeze Flask templates.
- **M3** — flip `/` (and `/{pattern}`) to FastAPI. Legacy JSON routes start
  emitting `Deprecation: true` + `Sunset` + `Link: <successor>` (see
  `docs/deprecation-map.md`). Take the pre-cutover backup first (no API
  needed — safe to run during the maintenance window):
  ```sh
  python -m backend.cli.backup create --label pre-v3-m3 \
      --data-dir ./data --database-url sqlite+aiosqlite:///./data/v3.db
  ```
- **M4** — Flask removed (done). Sunset date: 2027-06-30 (override per
  deployment with `REDIRECTOR_SUNSET_DATE`).

## Verify after each milestone

Do not advance until the current milestone's checklist is green. `BASE` is
the front door: `http://localhost:8080` for the M1 harness
(`docker/compose.m1.yml`), `http://localhost` for the prod stack
(`docker/compose.prod.yml`, web on :80).

### M1 — dual-serve

- [ ] `curl $BASE/healthz` → FastAPI `{"status":"ok"}` (via nginx).
- [ ] `curl $BASE/` → Flask legacy HTML (not the SPA shell).
- [ ] `curl "$BASE/api/v1/shortcuts?page=1&pageSize=5"` → 200 JSON — v3 reads
  its own `v3-m1.db`, never the v2 schema.
- [ ] `python -m backend.cli.doctor --data-dir ./data` → exit 0.
- [ ] v2 files untouched after import + traffic (proven in CI by
  `tests/test_v3_migration_boot.py::test_v3_leaves_v2_files_untouched`).
- [ ] Rollback rehearsal: `docker compose -f docker/compose.m1.yml down` →
  the v2 container answers alone, exactly as before.

### M2 — SPA at /app

- [ ] `curl $BASE/app` → 200 SPA shell; `GET $BASE/?legacy=1` still serves the
  frozen Flask templates.
- [ ] Create and resolve one shortcut end-to-end in the SPA (not the legacy
  UI); devtools shows the calls hitting `/api/v1/*`.
- [ ] New UI issues no requests to legacy Flask JSON routes.

### M3 — redirect hot-path cutover

- [ ] Pre-cutover backup taken (the `pre-v3-m3` command above) and copied off
  the host.
- [ ] `curl -sI $BASE/<known-shortcut>` → 302 (or the countdown page when the
  delay is > 0) served by FastAPI.
- [ ] Legacy JSON routes now emit `Deprecation: true` + `Sunset` + `Link:`
  headers — verify with `curl -sI` (see `docs/deprecation-map.md`).
- [ ] Nightly `soak-pg` job green on the cutover build.
- [ ] Rollback rehearsal: revert the cutover and confirm the Flask fallback
  serves `/<known-shortcut>` again before announcing.

### M4 — Flask removed

- [ ] Single uvicorn image boots; no Flask/v2 service left in the compose
  config, and `?legacy=1` no longer serves anything.
- [ ] Full suite green: `pytest`, `vitest`, Playwright e2e.
- [ ] v2 compose file deprecated; final pre-M4 backup archived off-host.

## Rollback runbook

Every milestone is reversible; restores never touch the pre-migration backup.

| Situation | Rollback |
|---|---|
| M1 misbehaves | `docker compose -f docker/compose.m1.yml down`; v2 container untouched. Point DNS/proxy back at `:5000`/`:80` as before |
| Bad import (wrong counts, missing rows) | Throw away `v3.db`, fix, re-run `import-v2` (idempotent; existing rows skipped) |
| Bad v3 backup restore | Every restore takes an automatic `pre-restore` safety backup first — restore *that* archive to undo |
| Full retreat to v2 | Stop v3 containers, boot the last v2 image tag against the untouched v2 files. v3 never writes the v2 schema (separate DB file), so downgrade is a restart, not a migration |

After any rollback: re-run `backend.cli.doctor` and confirm the redirect
hot path (`GET /<known-shortcut>`) before announcing recovery.
