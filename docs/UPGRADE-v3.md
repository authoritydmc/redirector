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
  `docs/deprecation-map.md`).
- **M4** — remove Flask. Sunset date: 6 months after v3.0 GA (proposed).

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
