# Load Testing with Locust

This project includes two Locust scripts:
- `load_testing/locustfile.py` — legacy v2 Flask routes (`/edit/…`, `/check-upstreams-ui`, `/admin/…`).
- `load_testing/locustfile_v3.py` — v3 FastAPI backend (`/api/v1/*` + `/{pattern}` hot path).

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
```

Deliberately NOT yet gated: p99-regression-vs-baseline (needs stored
baselines).

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

## v2 script (`locustfile.py`)

## Features Covered
- Static, dynamic, and user-dynamic shortcut creation and redirection
- Upstream check UI and unknown shortcut access
- Upstream cache resync, purge, and logs
- Google and random shortcut flows

## How to Run Load Tests

1. **Install Locust:**
   ```sh
   pip install locust
   ```

2. **Start your Flask app:**
   Make sure your app is running locally (default: http://localhost).

3. **Run Locust:**
   ```sh
   locust -f load_testing/locustfile.py --host=http://localhost
   ```

4. **Open the Locust web UI:**
   Go to [http://localhost:8089](http://localhost:8089) in your browser.

5. **Configure and start the test:**
   - Set the number of users and spawn rate.
   - Click "Start swarming".

## What Gets Tested
- **/edit/<shortcut>**: Create static, dynamic, and user-dynamic shortcuts
- **/<shortcut>**: Redirect to static, dynamic, and user-dynamic targets
- **/check-upstreams-ui/<pattern>**: Upstream check UI
- **/admin/upstream-cache/resync/<upstream>/<pattern>**: Resync cache
- **/admin/upstream-cache/purge/<upstream>**: Purge cache
- **/admin/upstream-logs**: View logs
- **/admin/upstreams**: Add/delete upstreams (if you add tasks)

## Customizing the Test
- Edit `locustfile.py` to add/remove tasks or change request parameters.
- You can simulate more users, different shortcut patterns, or more admin flows as needed.

## Tips
- For best results, run with a clean database or in a test environment.
- Monitor your server's CPU, memory, and response times during the test.
- Use the Locust UI charts to spot bottlenecks or failures.

---

**Example Locust command:**
```sh
locust -f load_testing/locustfile.py --host=http://localhost
```

See `locustfile.py` for all simulated user flows.
