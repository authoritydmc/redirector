# Load Testing with Locust

- `load_testing/locustfile_v3.py` — FastAPI backend (`/api/v1/*` + `/{pattern}` hot path).

## Measured redirect p99s (EPIC-04/master p99 gates)

`load_testing/measure-p99.py` drives the hot path directly (2000 cached +
2000 uncached samples). Measured 2026-10-08:

| Topology | cached p99 (target <10ms) | uncached p99 (target <150ms) |
|---|---|---|
| Linux, container-native storage | **7.2ms PASS** | **6.1ms PASS** |
| Linux via WSL 9P bind mount | 102ms FAIL | 9.3ms PASS |
| Windows native (NTFS) | 32.6ms FAIL | 33.3ms PASS |

Mechanism: every served redirect commits `access_count` (v2 parity), so
cached latency ≈ one SQLite commit ≈ one fsync. The dev-topology misses
are filesystem artifacts (NTFS / 9P-bridge fsync tax), not code: uncached
reads stay fast everywhere. Known levers if the 7.2ms margin ever thins:
`PRAGMA synchronous=NORMAL` or deferred counting — both trade durability
for latency and are deliberately NOT taken here.

## CI smoke (`load-smoke.sh`, EPIC-06 task 5)

`.github/workflows/load-smoke.yml` runs the locust v3 script headless
(10 VUs, 60s) on every PR touching `backend/` or `load_testing/`, nightly
via cron, and on manual dispatch: it boots uvicorn on a scratch DB and
gates on **zero request failures** via `check_stats.py` (locust's own exit
code is not failure-sensitive, so the CSV gate is the verdict). CSVs land
as the `load-smoke-report` artifact with per-endpoint p99s.

```sh
sh load_testing/load-smoke.sh   # honors LOAD_VUS / LOAD_RATE / LOAD_TIME / LOAD_PORT
python load_testing/check_stats.py load_testing/report/load-smoke
python load_testing/check_regression.py load_testing/report/load-smoke
```

Gates, in order: zero request failures (`check_stats.py`), then hot-path
p99 vs `p99-baselines.json` (`check_regression.py`: fail above 20% *and*
+50ms absolute — the floor keeps sub-10ms endpoints from flaking on
runner jitter; only `/<shortcut>` + `/<unknown-shortcut>` are gated
because upstream/SSE rows track external latency). Re-baseline
deliberately: run the smoke, take the CSV p99s, review the diff, commit.

Deliberately NOT yet gated: p99-regression-vs-baseline (needs stored
baselines).

## Nightly soak on Postgres + Redis (`soak-pg.sh`, EPIC-08 task 6)

`.github/workflows/soak-pg.yml` boots the same API against
production-shaped storage: Postgres (alembic `upgrade head`), seeded with
the committed v2 golden fixture via `import-v2` (so the migration path is
exercised nightly too), with `REDIRECTOR_CACHE_BACKEND=redis`. Then the same
locust v3 script + `check_stats.py` zero-failures gate. Runs nightly
(04:30 UTC), on manual dispatch, and on PRs touching the migration/load
paths. CSVs land as the `soak-pg-report` artifact.

```sh
# needs reachable Postgres + Redis; import-v2 needs a sync PG driver:
pip install psycopg2-binary
SOAK_PG_URL="postgresql+asyncpg://redirector@127.0.0.1:5432/redirector" \
SOAK_REDIS_URL="redis://127.0.0.1:6379/0" \
  sh load_testing/soak-pg.sh
```

Notes:
- Each run targets a fresh database (CI services are ephemeral), so the
  uuid-suffixed rows the script accumulates never carry over.
- k6 was the epic's original sketch; locust is reused instead — one harness
  and the same CSV gate as the sqlite smoke, no new tool to maintain.

## v3 script (`locustfile_v3.py`)

Covers shortcuts CRUD + bulk-delete, the resolve hot path (static, dynamic,
user-dynamic, unknown → 404), upstreams CRUD + cache purge/entry-purge/resync
+ check-logs + SSE check stream, metrics (`/kpi`, `/live`), QR
(`/api/v1/qr`, `/qr/<pattern>`), and ops probes (`/healthz`, `/health`,
`/readyz`). Auth-gated admin/config endpoints are excluded on purpose —
load runs target public/read paths.

Run against a local v3 server:

```sh
# terminal 1: scratch DB with v3 tables, then boot the v3 API
python -c "from sqlmodel import SQLModel, create_engine; import backend.models.entities; SQLModel.metadata.create_all(create_engine('sqlite:///./data/load.db')); print('tables ok')"
export REDIRECTOR_DATABASE_URL="sqlite+aiosqlite:///./data/load.db"
export REDIRECTOR_AUTO_REDIRECT_DELAY=0
uvicorn backend.main:app --port 8123

# terminal 2: headless smoke (4 users, 60s)
locust -f load_testing/locustfile_v3.py --host=http://127.0.0.1:8123 \
  --headless -u 4 -r 4 -t 60s
```

Or open the web UI: `locust -f load_testing/locustfile_v3.py --host=http://127.0.0.1:8123`
then http://localhost:8089.

Notes:
- Use a scratch database (task names are uuid-suffixed and accumulate rows).
- The SSE stream task performs real outbound HTTP to the seeded upstream, so
  stream throughput tracks upstream latency — keep its weight low.
- `GET /{pattern}` returns the countdown page when the redirect delay is > 0;
  that is one HTML response server-side (the wait happens client-side).
- Resolve tasks never follow redirects, so external targets see zero load
  traffic and their status codes can't pollute the results.
