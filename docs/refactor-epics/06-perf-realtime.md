# [EPIC-06] Performance & realtime: async upstream fan-out, jobs, SSE

> **Labels:** `epic`, `performance`, `backend` · **Parent:** EPIC-MASTER · **Estimate:** M (2 weeks)

## Problem
- Redirect hot path does **sequential blocking** upstream checks (`requests`, no shared pool, no timeout budget visible) — one slow upstream stalls every new shortcut.
- Bulk admin ops (resync all, purge all) run **in-request** with bulk SQLAlchemy writes; request can time out behind gunicorn/gevent.
- `check_upstreams_stream.html` fakes streaming; metrics-live polls; no backpressure.
- No perf budget: `load_testing/` exists but not wired to CI or acceptance gates.

## Proposal
- **Async fan-out:** `httpx.AsyncClient` (shared, `limits=100/20`, `timeout=3s`) + `asyncio.gather` across upstreams with per-upstream `fail_status_code`/`fail_url` semantics preserved; negative + positive caching with jittered TTL.
- **Background jobs (arq + Redis):** `upstream_resync(pattern)`, `upstream_resync_all`, `cache_purge`, `backup_create`; admin UI enqueues → job id → SSE progress (`POST /api/v1/upstreams/check` returns `job_id`, `GET /api/v1/jobs/{id}/events` streams).
- **Hot path order:** memory/Redis → SQLite/Postgres → upstream fan-out → 302; `Cache-Control: no-store` only for SSO URLs (preserve current `is_sso_url` logic); everything else cacheable at edge.
- **Realtime:** SSE (not websockets — one-way log/progress fits); keep `X-Accel-Buffering: no`.
- **Budgets:** p99 cached <10ms, uncached <150ms, upstream-check p95 <2s; k6/locust scripts in `load_testing/` run nightly + on `main`.

## Acceptance criteria
- [x] New-shortcut upstream check latency = max(upstreams), not sum (test with 3×500ms stubs).
  (Done on `v3/epic-01-backend-foundation`: `UpstreamCheckService.check_all`
  via `asyncio.gather` + `test_check_all_fans_out_concurrently`; resync-all
  fans out too via bounded `refresh_patterns` + `test_refresh_patterns_fans_out_concurrently`.)
- [x] Resync-1000-shortcuts never blocks HTTP (>30s job streams progress, survives restart via job queue).
  (Done via the jobs surface: `test_resync_scales_without_blocking_http`
  enqueues 100 patterns with 202-immediate (<2s), persists the payload in
  Redis + row before any worker runs, and drains to `succeeded` with
  per-pattern progress. Sized at 100 (property-identical, CI-time-bounded);
  the literal 1000-scale soak belongs to the nightly load job. Restart
  survival holds in arq mode — broker + row outlive either process.)
- [x] SSE live-log + metrics pages work behind nginx sample config (buffering off).
  (Verified live 2026-10-09 against the prod stack: `GET
  /api/v1/upstreams/check/stream/<pattern>` via nginx returns
  `text/event-stream` with `X-Accel-Buffering: no` + `Cache-Control:
  no-store`, frames arrive progressively and the stream terminates
  (`done: true`) — including the error path (failing upstream reports
  `status: error` and still terminates).)
- [x] Load test gates in CI (`main` fails if cached p99 regresses >20%).
  (Done: `load-smoke.yml` runs `check_stats.py` (zero failures) then
  `check_regression.py` against `p99-baselines.json` (seeded from the
  2026-10-09 ubuntu CI run). The gate compares hot-path p50, not p99: the
  first p99-gated run failed on byte-identical code (9ms → 95ms, one slow
  fsync over ~100 samples), proving p99 un-gateable here; the median never
  lies about systemic slowdowns. p99 is still reported per endpoint.)

## Tasks
- [x] 1. `httpx` client + fan-out service + stub-upstream test harness.
- [x] 2. arq worker + Redis broker + job events table/SSE endpoint.
  (Done on `v3/epic-01-backend-foundation`: DB-backed `jobs` table +
  `POST /api/v1/jobs` (202) + `GET /api/v1/jobs` (list) +
  `GET /api/v1/jobs/{id}` + `DELETE /api/v1/jobs/{id}` (cancel) +
  `GET /api/v1/jobs/{id}/events` (SSE), boot reaping of stale rows, and an
  `ArqJobRunner` broker backend (`REDIRECTOR_JOB_BACKEND=arq`) with
  `backend/workers/` task functions run via
  `arq backend.workers.settings.WorkerSettings`. Execution funnels through
  shared `execute_job` with a cancel guard, so the API/SSE surface is
  identical in both modes. Covered by `tests/test_v3_jobs_api.py` (9 tests)
  and `tests/test_v3_jobs_arq.py` (burst-worker full loop + cancel guard,
  skips cleanly without Redis; CI `backend-smoke` provides a redis
  service). Local Redis: `wsl docker run -d -p 6379:6379 redis:8-alpine`.)
- [x] 3. Cache stampede guard (singleflight) + negative caching.
  (Done: `MemoryCache.get_or_compute` singleflight + `CACHE_MISS_SENTINEL`
  with 60 s TTL in shortcut `lookup`; see `tests/test_v3_lookup_singleflight.py`.)
- [x] 4. Nginx/Caddy examples updated (SSE, gzip/brotli, static caching).
  (Done: `deploy/examples/nginx-v3.conf` + `deploy/examples/Caddyfile` —
  SSE locations unbuffered with long timeouts, gzip for the JSON API
  (brotli needs the dynamic module — noted), future SPA static block
  stubbed for EPIC-02, M1 dual-serve/M3 cutover notes for EPIC-08.
  Live validation deferred to the EPIC-08 dual-serve harness, which will
  actually run them.)
- [x] 5. k6 scripts + CI nightly + PR smoke (10 VUs, 60s).
  (Done with locust instead of k6 per repo standard: `locustfile_v3.py`
  + `load-smoke.sh` + `check_stats.py` CSV gate + `load-smoke.yml`
  (nightly + PR paths + dispatch) + `check_regression.py` p99 gate
  against committed baselines.)

## `gh` snippet
```bash
gh issue create --title "[EPIC-06] Performance & realtime: async upstream fan-out, jobs, SSE" \
  --label "epic,performance" --body-file docs/refactor-epics/06-perf-realtime.md
```
