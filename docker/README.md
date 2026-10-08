# Docker layouts (EPIC-07/08)

## M1 dual-serve harness (`compose.m1.yml`)

One port (8080), two backends: nginx routes `/api/v1/*` (+ probes, docs,
QR) to the v3 FastAPI container and everything else to the v2 Flask
container. See the header comment in `compose.m1.yml` for commands and
safety properties.

- `Dockerfile.v3` — dev-only v3 API image (production shape lands later).
- `nginx.m1.conf` — pre-M3 routing; M3 flips `/` to `api_v3`.
- Verify routing after `up`: `/healthz` → FastAPI `{"status":"ok"}`,
  `/` → Flask HTML, `/api/v1/upstreams/check/stream/<pattern>` streams
  `text/event-stream` with buffering off.
