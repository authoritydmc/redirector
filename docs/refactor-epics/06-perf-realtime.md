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
- [ ] New-shortcut upstream check latency = max(upstreams), not sum (test with 3×500ms stubs).
- [ ] Resync-1000-shortcuts never blocks HTTP (>30s job streams progress, survives restart via job queue).
- [ ] SSE live-log + metrics pages work behind nginx sample config (buffering off).
- [ ] Load test gates in CI (`main` fails if cached p99 regresses >20%).

## Tasks
- [ ] 1. `httpx` client + fan-out service + stub-upstream test harness.
- [ ] 2. arq worker + Redis broker + job events table/SSE endpoint.
- [ ] 3. Cache stampede guard (singleflight) + negative caching.
- [ ] 4. Nginx/Caddy examples updated (SSE, gzip/brotli, static caching).
- [ ] 5. k6 scripts + CI nightly + PR smoke (10 VUs, 60s).

## `gh` snippet
```bash
gh issue create --title "[EPIC-06] Performance & realtime: async upstream fan-out, jobs, SSE" \
  --label "epic,performance" --body-file docs/refactor-epics/06-perf-realtime.md
```
