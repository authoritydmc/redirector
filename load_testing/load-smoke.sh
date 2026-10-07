#!/bin/sh
# Load smoke for the v3 FastAPI backend (EPIC-06 task 5).
#
# Boots uvicorn on a scratch SQLite DB, runs the locust v3 script headless,
# then gates on zero request failures via check_stats.py (locust's own exit
# code is not failure-sensitive, so the CSV gate is the verdict).
#
# Knobs (env): LOAD_VUS (default 10), LOAD_RATE (2), LOAD_TIME (60s),
# LOAD_PORT (8123), REPORT_PREFIX (load_testing/report/load-smoke).
# POSIX sh only — CI/Docker are Linux (see AGENTS.md).
set -eu

LOAD_VUS="${LOAD_VUS:-10}"
LOAD_RATE="${LOAD_RATE:-2}"
LOAD_TIME="${LOAD_TIME:-60s}"
LOAD_PORT="${LOAD_PORT:-8123}"
LOAD_HOST="http://127.0.0.1:${LOAD_PORT}"
REPORT_PREFIX="${REPORT_PREFIX:-load_testing/report/load-smoke}"
SCRATCH_DB="${TMPDIR:-/tmp}/redirector-load-smoke-$$.db"

export PYTHONPATH="${PYTHONPATH:-$(pwd)}"
export REDIRECTOR_DATABASE_URL="sqlite+aiosqlite:///${SCRATCH_DB}"
export REDIRECTOR_AUTO_REDIRECT_DELAY=0

rm -f "$SCRATCH_DB"
mkdir -p "$(dirname "$REPORT_PREFIX")"
python3 -c "from sqlmodel import SQLModel, create_engine; import backend.models.entities; SQLModel.metadata.create_all(create_engine('sqlite:///${SCRATCH_DB}')); print('tables ok')"

python3 -m uvicorn backend.main:app --port "$LOAD_PORT" &
SRV_PID=$!
cleanup() { kill $SRV_PID 2>/dev/null || true; rm -f "$SCRATCH_DB"; }
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
    sys.exit("load-smoke: server never became ready")
EOF

python3 -m locust -f load_testing/locustfile_v3.py --host="$LOAD_HOST" \
  --headless -u "$LOAD_VUS" -r "$LOAD_RATE" -t "$LOAD_TIME" \
  --csv="$REPORT_PREFIX" --stop-timeout 10

python3 load_testing/check_stats.py "$REPORT_PREFIX"
