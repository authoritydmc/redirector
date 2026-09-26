#!/bin/bash
# Redirector container entrypoint.
#
# Responsibilities, in order, so that an image upgrade can never cost data:
#
#   1. make sure the data directory exists and is writable  (fail loudly, not silently)
#   2. apply a restore that was requested through the web UI while the app was running
#   3. refuse to run an OLD image against NEWER data
#   4. snapshot the install, but only when a migration is actually pending
#   5. run pending migrations, with bounded retries instead of hanging forever
#   6. start gunicorn
#
# Everything persistent lives under $REDIRECTOR_DATA_DIR (default /app/data),
# which is the single volume an upgrade has to preserve.

set -euo pipefail

DATA_DIR="${REDIRECTOR_DATA_DIR:-/app/data}"
PENDING_RESTORE="$DATA_DIR/.restore-pending.zip"

# Keep the log readable and stable regardless of the host's locale.
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
# Pin the app the migration CLI must load. Without this, `flask db upgrade` only
# works because Flask happens to auto-detect wsgi.py from the working directory,
# and would target the wrong app (or fail) the moment that changes.
export FLASK_APP="${FLASK_APP:-wsgi:app}"

log()  { echo "[redirector] $*"; }
warn() { echo "[redirector] WARNING: $*" >&2; }
die()  { echo "[redirector] FATAL: $*" >&2; exit 1; }

log "container starting up"

# ---------------------------------------------------------------------------
# 1. Data directory
# ---------------------------------------------------------------------------
mkdir -p "$DATA_DIR" "$DATA_DIR/backups" || die "cannot create $DATA_DIR"

if [ ! -w "$DATA_DIR" ]; then
  die "$DATA_DIR is not writable by uid $(id -u). Fix ownership on the host
       (Linux: sudo chown -R $(id -u):$(id -g) $DATA_DIR) or add ':rw' to the mount."
fi
log "data directory: $DATA_DIR"

# ---------------------------------------------------------------------------
# 2. Deferred restore
# ---------------------------------------------------------------------------
# A restore requested from /admin/backup is parked in the data directory and
# applied here, before migrations, because the running process cannot overwrite
# the database it is serving from.
if [ -f "$PENDING_RESTORE" ]; then
  log "a restore was requested through the admin UI; applying it before start"
  if python -m app.utils.backup apply-pending; then
    log "restore applied"
  else
    die "the staged restore in $PENDING_RESTORE could not be applied.
       Your previous data is intact and still in the archive - see
       $PENDING_RESTORE, and $DATA_DIR/backups/ for a snapshot taken just now.
       Fix the cause and restart, or move that file aside to start normally."
  fi
fi

# ---------------------------------------------------------------------------
# 3 + 4 + 5. Schema drift guard and migrations
# ---------------------------------------------------------------------------
# app.utils.state reads alembic_version straight out of the database and
# compares it with the migrations/versions/ files shipped in this image.
SCHEMA_INFO="$(python -c '
from app.utils.state import schema_status
from app.utils.utils import get_db_uri
s = schema_status(get_db_uri())
print("|".join([s["drift"], s["current"] or "-", s["head"] or "-"]))
' 2>/dev/null)" || SCHEMA_INFO=""

if [ -z "$SCHEMA_INFO" ]; then
  warn "could not read the database schema revision; treating it as unknown"
  DRIFT="unknown"; CURRENT="-"; HEAD="-"
else
  DRIFT="${SCHEMA_INFO%%|*}"
  REST="${SCHEMA_INFO#*|}"
  CURRENT="${REST%%|*}"
  HEAD="${REST#*|}"
  log "database schema: ${CURRENT} -> ${HEAD} (${DRIFT})"
fi

case "$DRIFT" in
  ahead)
    die "the database schema (${CURRENT}) is NEWER than this image supports (${HEAD}).
       This image is older than your data, so it refuses to touch it. Nothing has
       been modified. Either:
         - pull the newer image:  docker compose pull && docker compose up -d
         - or go back to the version your data came from and restore one of its
           snapshots:  ls $DATA_DIR/backups
       Your data is intact."
    ;;
  unknown|unstamped)
    # A database Alembic has no record of. Creating a fresh one is harmless;
    # silently stamping an unrecognised existing one is not, so warn.
    warn "database has no usable Alembic revision stamp (${DRIFT}); migrations will run"
    ;;
esac

if [ "${REDIRECTOR_SKIP_MIGRATIONS:-0}" = "1" ]; then
  warn "REDIRECTOR_SKIP_MIGRATIONS=1 - skipping schema migration (operator override)"
else
  # Snapshot only when there is something to migrate. Backing up on every
  # restart filled the volume with identical copies and trained people to
  # ignore the backup directory. `unknown` is included: we are about to run
  # migrations against a database whose revision we could not read, which is
  # exactly when a snapshot earns its keep.
  case "$DRIFT" in
    pending|unstamped|fresh|unknown)
      log "schema migration pending (${DRIFT}) - snapshotting the current install first"
      if ! python -m app.utils.backup create --kind pre-upgrade --label "rev${CURRENT}"; then
        warn "pre-upgrade snapshot failed; continuing (a manual backup may still exist in $DATA_DIR/backups)"
      fi
      ;;
    *)
      log "schema already current - no snapshot needed"
      ;;
  esac

  # Bounded retries. The old `until flask db upgrade; do sleep 2; done` looped
  # forever: a genuinely broken migration left a container that looked alive but
  # never served traffic, with no clue in the logs about why.
  MIGRATE_ATTEMPTS="${REDIRECTOR_MIGRATE_ATTEMPTS:-5}"
  attempt=1
  until flask db upgrade; do
    if [ "$attempt" -ge "$MIGRATE_ATTEMPTS" ]; then
      echo "[redirector] ---- last migration output above ----" >&2
      die "database migration failed after ${MIGRATE_ATTEMPTS} attempts.
       The database was left untouched by the failed step. To roll back:
         docker compose down
         ls $DATA_DIR/backups
         docker run --rm -v $DATA_DIR:/data <the image you want> \\
           python -m app.utils.backup restore /data/backups/<name>.zip
       Or fix the cause and retry:  docker compose up -d
       Or start without migrating to triage:  REDIRECTOR_SKIP_MIGRATIONS=1 docker compose up -d"
    fi
    warn "migration attempt ${attempt}/${MIGRATE_ATTEMPTS} failed; retrying in 3s"
    attempt=$((attempt + 1))
    sleep 3
  done
  log "migrations applied"
fi

# Record what this install is now running, for /system-info and /api/upgrade-info.
python - <<'PY' || warn "could not record install state"
from app.CONSTANTS import get_semver
from app.config import config
from app.utils import state

status = state.schema_status(config.resolved_database)
state.write_state(config.DATA_DIR, {
    "app_version": get_semver(),
    "schema_revision": status["current"],
    "schema_head": status["head"],
    "data_dir": config.DATA_DIR,
})
PY

log "starting gunicorn"
exec gunicorn -c gunicorn.conf.py "wsgi:app"
