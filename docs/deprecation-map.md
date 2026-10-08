# Legacy → v1 deprecation map (EPIC-08 task 3, feeds EPIC-03 task 6)

Source: `docs/api-inventory.md` (65 v2 routes). Only machine-consumable
(JSON/form) endpoints are mapped — HTML pages become React routes and die
with Flask at M4. Successor status was verified against `docs/openapi.json`
(36 shipped paths); anything else is **planned** and carries no sunset
clock until it ships.

## Sunset policy (proposed — needs owner sign-off)

- From M3, every legacy JSON response carries
  `Deprecation: true`, `Sunset: <date>`, and
  `Link: <<successor>>; rel="successor"`.
- Sunset date: 6 months after v3.0 GA, uniform across endpoints.
- Removal happens at M4 (Flask gone); clients must migrate by Sunset.

## Shipped successors (clock can start at M3)

| Legacy | Successor |
|---|---|
| `GET /dashboard-shortcuts` | `GET /api/v1/shortcuts` (paginated) |
| `POST /api/delete-shortcut/<pattern>` | `POST /api/v1/shortcuts/bulk-delete` |
| `GET /api/check-shortcut-exists/<pattern>` | `GET /api/v1/shortcuts/{pattern}` (200 vs 404) |
| `GET,POST /admin/upstreams` | `GET/POST /api/v1/upstreams`, `PATCH/DELETE /api/v1/upstreams/{id}` |
| `GET /stream/check-upstreams/<pattern>` | `GET /api/v1/upstreams/check/stream/{pattern}` (SSE) |
| `GET /admin/upstream-logs`, `POST /admin/clear-upstream-logs` | `GET/DELETE /api/v1/upstreams/check-logs` |
| `GET /admin/upstream-cache/<upstream>` + purge/resync variants | `GET/DELETE /api/v1/upstreams/cache`, `POST .../cache/resync`, per-entry purge, jobs API |
| `GET,POST /admin-login` | `POST /api/v1/auth/login` (+ MFA challenge flow) |
| `/admin/mfa/*` (TOTP paths) | `POST /api/v1/auth/mfa/*` (passkeys still planned) |
| `GET,POST /admin/config` | `GET/PATCH /api/v1/admin/config` |
| `GET /api/metrics`, `/metrics*`, `/api/kpi`, `/metrics/live` | `GET /api/v1/metrics/kpi`, `GET /api/v1/metrics/live` |
| `GET /api/health/state`, `/ready` | `GET /readyz` |
| `/admin/backup*` (list/create/download/delete/restore) | `GET/POST/DELETE /api/v1/admin/backup*` (+ jobs) |
| `GET /api/qr/<pattern>` | `GET /api/v1/qr?pattern=` |

## Planned (no clock yet)

`GET /api/param-description/*`, `/admin/export-redirects`,
`/admin/import-redirects`, `/api/install-defaults`, `/setup*`,
`/admin/redis-cache*`, `/api/r-status`, `/system-info`,
`/api/changelog`, `/upgrade*`, `/api/upgrade-info`, `/api/latest-version`,
`/api/backup/inspect`, `/api/data-dir`, `/admin/logout` (JWT logout =
client discards token; server denylist open), MFA passkeys.
