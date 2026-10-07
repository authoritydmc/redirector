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
- [ ] Resync-1000-shortcuts never blocks HTTP (>30s job streams progress, survives restart via job queue).
- [ ] SSE live-log + metrics pages work behind nginx sample config (buffering off).
- [ ] Load test gates in CI (`main` fails if cached p99 regresses >20%).
  (In progress: nightly `load-nightly.yml` runs the locust v3 script
  headless and fails on any request failure, with p99s in the CSV
  artifact. Full p99-regression-vs-baseline and PR-triggered runs still open.)

## Tasks
- [x] 1. `httpx` client + fan-out service + stub-upstream test harness.
- [ ] 2. arq worker + Redis broker + job events table/SSE endpoint.
  (In progress on `v3/epic-01-backend-foundation`: DB-backed `jobs` table +
  `POST /api/v1/jobs` (202) + `GET /api/v1/jobs` (list) +
  `GET /api/v1/jobs/{id}` + `DELETE /api/v1/jobs/{id}` (cancel) +
  `GET /api/v1/jobs/{id}/events` (SSE) with an in-process asyncio runner
  for `upstream_resync`, plus boot reaping of stale rows
  (`backend/modules/jobs/`, `backend/routers/jobs.py`,
  `tests/test_v3_jobs_api.py`). Routers depend only on `JobRunner`, so arq +
  Redis lands as a runner swap. Still open: arq worker process, Redis
  broker, restart-surviving execution.)
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
- [ ] 5. k6 scripts + CI nightly + PR smoke (10 VUs, 60s).
  (In progress, locust instead of k6 per repo standard: `locustfile_v3.py`
  + `load-smoke.sh` + `check_stats.py` CSV gate + `load-smoke.yml`
  (nightly + PR paths + dispatch). Still open: baseline-relative p99
  gating.)

## `gh` snippet
```bash
gh issue create --title "[EPIC-06] Performance & realtime: async upstream fan-out, jobs, SSE" \
  --label "epic,performance" --body-file docs/refactor-epics/06-perf-realtime.md
```
