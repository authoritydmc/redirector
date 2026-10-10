# Upgrading to v3 (EPIC-08 task 5)

Target reader: a self-hoster on v1/v2 with a `data/` directory (database +
`redirect.config.json` + backups). Goal: the FastAPI stack serving the same
redirects with zero manual data surgery. (The M1–M3 dual-serve era is over —
Flask was removed at M4 — so this is now one straight path, not milestones.)

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

## The upgrade

1. **Boot the v3 stack** (fresh `v3.db`; the `migrate` service creates the
   schema — it refuses legacy v2 files on purpose, so never point it at
   `redirect.db`):
   ```sh
   REDIRECTOR_ADMIN_PASSWORD=... REDIRECTOR_JWT_SECRET=... \
     docker compose -f docker/compose.prod.yml up -d --build
   ```
2. **Import v2 data** into the v3 database (idempotent; review row counts):
   ```sh
   docker compose -f docker/compose.prod.yml exec api \
     python -m backend.migrations.import_v2 --data-dir /app/data \
     --database-url sqlite:////app/data/v3.db
   ```
   Curated non-secret settings (`auto_redirect_delay`, `log_level`) carry
   over; upstreams, cache rows, check logs and user params too.
3. **Verify** (checklist below), then point DNS / your TLS proxy at the new
   stack (`web:80`).

## Verify after cutover (`BASE=http://localhost`)

- [ ] `curl $BASE/healthz` → `{"status":"ok"}`; `curl $BASE/` → 200 SPA shell.
- [ ] `curl "$BASE/api/v1/shortcuts?page=1&pageSize=5"` → 200 JSON with your rows.
- [ ] `curl -sI $BASE/<known-shortcut>` → 302 (or the countdown page when the
  delay is > 0).
- [ ] Log in with the **configured** password (not the old v2 one) and open
  the admin surface; re-enroll MFA.
- [ ] Create and resolve one shortcut end-to-end in the SPA.
- [ ] v2 files untouched by the whole process (proven in CI by
  `tests/test_v3_migration_boot.py::test_v3_leaves_v2_files_untouched`).

## Rollback runbook

Restores never touch the pre-migration backup.

| Situation | Rollback |
|---|---|
| v3 misbehaves | `docker compose -f docker/compose.prod.yml down`; v2 files untouched — point DNS/proxy back at the last v2 image tag as before |
| Bad import (wrong counts, missing rows) | Throw away `v3.db`, fix, re-run `import-v2` (idempotent; existing rows skipped) |
| Bad v3 backup restore | Every restore takes an automatic `pre-restore` safety backup first — restore *that* archive to undo |
| Full retreat to v2 | Stop v3 containers, boot the last v2 image tag against the untouched v2 files. v3 never writes the v2 schema (separate DB file), so downgrade is a restart, not a migration |

After any rollback: re-run `backend.cli.doctor` and confirm the redirect
hot path (`GET /<known-shortcut>`) before announcing recovery.
