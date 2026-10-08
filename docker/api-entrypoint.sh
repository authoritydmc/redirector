#!/bin/sh
# v3 API entrypoint: migrate (unless skipped), then serve.
# Mirrors v2's `flask db upgrade` step. SKIP_MIGRATIONS=1 lets the worker
# (and scaled replicas) skip it: a dedicated `migrate` service owns schema
# changes, because concurrent `alembic upgrade` runs race CREATE TYPE on
# Postgres (duplicate pg_type). POSIX sh only; `exec` keeps signals working.
set -eu

if [ "${SKIP_MIGRATIONS:-0}" = "1" ]; then
  echo "==> skipping migrations (SKIP_MIGRATIONS=1)"
else
  echo "==> alembic upgrade head (${REDIRECTOR_DATABASE_URL:-default sqlite})"
  python -m alembic -c backend/alembic.ini upgrade head
fi

echo "==> starting $*"
exec "$@"
