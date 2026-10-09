#!/bin/sh
# Nightly soak for the v3 stack on production-shaped storage (EPIC-08 task 6).
#
# Boots uvicorn against Postgres (+ Redis lookup cache), seeds it with the
# committed v2 golden fixture via import-v2 — so the soak also exercises the
# migration path nightly — then runs the locust v3 script headless and gates
# on zero request failures via check_stats.py (locust's own exit code is not
# failure-sensitive, so the CSV gate is the verdict).
#
# Requires reachable Postgres + Redis. In CI these come from job services;
# locally, either boot the prod overlay:
#   docker compose -f docker/compose.prod.yml -f docker/compose.postgres.yml up -d
# (then point SOAK_PG_URL at it), or run standalone postgres:16 + redis:8.
# Each run targets a fresh database (CI services are ephemeral), so the
# uuid-suffixed rows the locust script accumulates never carry over.
#
# Knobs (env): SOAK_PG_URL, SOAK_REDIS_URL, SOAK_PORT (8124), LOAD_VUS (10),
# LOAD_RATE (2), LOAD_TIME (60s), REPORT_PREFIX, SOAK_SEED_DIR.
# The import step needs a *sync* PG driver (import-v2 maps +asyncpg to
# +psycopg2): `pip install psycopg2-binary` alongside the backend reqs.
# POSIX sh only — CI/Docker are Linux (see AGENTS.md).
set -eu

SOAK_PG_URL="${SOAK_PG_URL:-postgresql+asyncpg://redirector@127.0.0.1:5432/redirector}"
SOAK_REDIS_URL="${SOAK_REDIS_URL:-redis://127.0.0.1:6379/0}"
SOAK_PORT="${SOAK_PORT:-8124}"
LOAD_VUS="${LOAD_VUS:-10}"
LOAD_RATE="${LOAD_RATE:-2}"
LOAD_TIME="${LOAD_TIME:-60s}"
LOAD_HOST="http://127.0.0.1:${SOAK_PORT}"
REPORT_PREFIX="${REPORT_PREFIX:-load_testing/report/soak-pg}"
SOAK_SEED_DIR="${SOAK_SEED_DIR:-tests/fixtures/data-v2}"

export PYTHONPATH="${PYTHONPATH:-$(pwd)}"

# import-v2 speaks sync drivers only: derive the psycopg2 URL from the async one.
SOAK_PG_SYNC_URL="$(printf '%s' "$SOAK_PG_URL" | sed 's/+asyncpg/+psycopg2/')"

mkdir -p "$(dirname "$REPORT_PREFIX")"

echo "soak: migrating Postgres schema..."
python3 -m alembic -c backend/alembic.ini -x "url=${SOAK_PG_URL}" upgrade head

echo "soak: seeding from ${SOAK_SEED_DIR} (import-v2)..."
python3 -m backend.migrations.import_v2 --data-dir "$SOAK_SEED_DIR" \
  --database-url "$SOAK_PG_SYNC_URL"

export REDIRECTOR_DATABASE_URL="$SOAK_PG_URL"
export REDIRECTOR_CACHE_BACKEND=redis
export REDIRECTOR_REDIS_URL="$SOAK_REDIS_URL"
export REDIRECTOR_AUTO_REDIRECT_DELAY=0

python3 -m uvicorn backend.main:app --port "$SOAK_PORT" &
SRV_PID=$!
cleanup() { kill $SRV_PID 2>/dev/null || true; }
trap cleanup EXIT INT TERM

python3 - "$LOAD_HOST" <<'EOF'
import sys, time, urllib.request
host = sys.argv[1]
for _ in range(60):
    try:
        urllib.request.urlopen(host + "/healthz", timeout=2)
        break
    except Exception:
        time.sleep(1)
else:
    sys.exit("soak-pg: server never became ready")
EOF

python3 -m locust -f load_testing/locustfile_v3.py --host="$LOAD_HOST" \
  --headless -u "$LOAD_VUS" -r "$LOAD_RATE" -t "$LOAD_TIME" \
  --csv="$REPORT_PREFIX" --stop-timeout 10

python3 load_testing/check_stats.py "$REPORT_PREFIX"
