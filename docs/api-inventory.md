# API inventory — every v2 (Flask) route, for the `/api/v1` contract design

> EPIC-03 task 1. Source: `app/routes/*.py` (65 `@bp.route` + error handlers).
> **Auth column is best-effort** (README table + spot-checked code) — every row must be
> re-verified by contract tests before the v1 OpenAPI is frozen.
> Legend: `PUB` public · `ADM` admin session · `PWD` admin password or session ·
> `SETUP` first-run/setup-gated · `?` verify during port.

## Redirect hot path (stays UNVERSIONED in v3)

| Method | v2 path | Blueprint | Auth | v1 target |
|---|---|---|---|---|
| GET | `/<path:subpath>` | redirection | PUB | `GET /{pattern}` (same, FastAPI) + `GET /api/v1/resolve?pattern=` debug |
| GET | `/qr/<path:pattern>` | routes | PUB | keep unversioned (hot asset) |
| GET | `/api/qr/<path:pattern>` | routes | PUB | `GET /api/v1/qr?pattern=` (base64 JSON) |

## Shortcuts CRUD (→ `/api/v1/shortcuts`)

| Method | v2 path | Auth | v1 target |
|---|---|---|---|
| GET | `/` (dashboard HTML + create/edit forms) | PUB view / ADM mutate | `GET /api/v1/shortcuts` (list, paginated) |
| GET,POST | `/delete/<path:subpath>` | PWD | `DELETE /api/v1/shortcuts/{pattern}` |
| GET,POST | `/edit/<path:subpath>` | PWD | `PATCH /api/v1/shortcuts/{pattern}` |
| GET,POST | `/edit/` (query-param variant) | PWD | same (query form) |
| POST | `/api/delete-shortcut/<pattern>` | ADM | `POST /api/v1/shortcuts:bulk-delete` (single+bulk) |
| GET | `/dashboard-shortcuts` (paginated JSON) | PUB | folded into list endpoint (`?page=&pageSize=&q=&sort=`) |
| GET | `/api/check-shortcut-exists/<path:pattern>` | PUB | `GET /api/v1/shortcuts/{pattern}/exists` |
| GET | `/api/param-description/<shortcut_pattern>/<param_name>` | PUB? | `GET /api/v1/shortcuts/{pattern}/params/{name}` |
| GET | `/admin/export-redirects` | ADM | `GET /api/v1/admin/export` (file download) |
| GET,POST | `/admin/import-redirects` | ADM | `POST /api/v1/admin/import` (+ `Idempotency-Key`) |
| POST | `/api/install-defaults` | SETUP | `POST /api/v1/admin/install-defaults` (setup-gated) |

## Upstreams (→ `/api/v1/upstreams`, `/api/v1/upstream-cache`)

| Method | v2 path | Auth | v1 target |
|---|---|---|---|
| GET,POST | `/admin/upstreams` | ADM | `GET/POST /api/v1/upstreams`, `PATCH/DELETE /api/v1/upstreams/{id}` |
| GET | `/check-upstreams-ui/<path:pattern>` | ? (create-flow) | React view, no API |
| GET (SSE-ish) | `/stream/check-upstreams/<path:pattern>` | ? (create-flow) | `POST /api/v1/upstreams/check` → SSE `GET /api/v1/jobs/{id}/events` |
| GET | `/admin/upstream-logs` | ADM | `GET /api/v1/upstream-logs` (paginated) |
| POST | `/admin/clear-upstream-logs` | ADM | `DELETE /api/v1/upstream-logs` |
| GET | `/admin/upstream-cache/<upstream>` | ADM | `GET /api/v1/upstream-cache?upstream=` |
| GET,POST | `/admin/upstream-cache/resync/<upstream>/<path:pattern>` | ADM | arq job `upstream_resync` |
| POST | `/admin/upstream-cache/purge-entry/<upstream>/<path:pattern>` | ADM | `DELETE /api/v1/upstream-cache/{upstream}/{pattern}` |
| POST | `/admin/upstream-cache/purge/<upstream>` | ADM | `DELETE /api/v1/upstream-cache?upstream=` |
| POST | `/admin/upstream-cache/resync-all/<upstream>` | ADM | arq job `upstream_resync_all` + SSE progress |

## Auth / MFA / setup (→ `/api/v1/auth`)

| Method | v2 path | Auth | v1 target |
|---|---|---|---|
| GET,POST | `/admin-login` | PUB | `POST /api/v1/auth/login` (JWT + refresh cookie) |
| GET | `/logout` | ADM | `POST /api/v1/auth/logout` (denylist refresh) |
| GET,POST | `/setup` | SETUP | `POST /api/v1/auth/setup` (first-run only) |
| GET | `/admin/setup` | SETUP? | folded into setup status `GET /api/v1/auth/setup-status` |
| GET,POST | `/admin/mfa/setup` | ADM | `POST /api/v1/auth/mfa/setup` |
| GET,POST | `/admin/mfa/verify` | PUB (pending) | `POST /api/v1/auth/mfa/verify` |
| POST | `/admin/mfa/passkey/register` | ADM | `POST /api/v1/auth/mfa/passkeys` |
| POST | `/admin/mfa/passkey/delete` | ADM | `DELETE /api/v1/auth/mfa/passkeys/{id}` |
| POST | `/admin/mfa/backup-codes` | ADM | `POST /api/v1/auth/mfa/backup-codes:regenerate` |
| — | (new: SSO, EPIC-05) | — | `GET /api/v1/auth/providers`, `GET /api/v1/auth/oidc/{p}/login|callback`, SCIM `/api/v1/scim/v2/*`, `POST /api/v1/auth/api-keys` |

## Admin / config / cache (→ `/api/v1/admin`)

| Method | v2 path | Auth | v1 target |
|---|---|---|---|
| GET,POST | `/admin/config` | ADM | `GET/PATCH /api/v1/admin/config` |
| GET | `/admin/redis-cache` | ADM | `GET /api/v1/admin/redis` |
| POST | `/admin/redis-cache/delete` | ADM | `DELETE /api/v1/admin/redis/{key}` |
| GET | `/tutorial` | PUB | React route (static) |
| GET | `/enable-r-instructions` | PUB | React route (static) |
| GET | `/api/r-status` | PUB | `GET /api/v1/r-status` |

## Backup (→ `/api/v1/admin/backup`, arq job)

| Method | v2 path | Auth | v1 target |
|---|---|---|---|
| GET | `/admin/backup` | ADM | React view over `GET /api/v1/admin/backup` |
| GET | `/admin/backup/list` | ADM | `GET /api/v1/admin/backup` |
| POST | `/admin/backup/create` | ADM | `POST /api/v1/admin/backup` (arq `backup_create`) |
| GET | `/admin/backup/download` | ADM | `GET /api/v1/admin/backup/{name}` (file) |
| DELETE | `/admin/backup/<name>` | ADM | `DELETE /api/v1/admin/backup/{name}` |
| POST | `/admin/backup/restore` | ADM | `POST /api/v1/admin/backup/{name}:restore` (staged) |
| POST | `/api/backup/inspect` | ADM? | `GET /api/v1/admin/backup/{name}` (metadata) |
| GET | `/api/data-dir` | ADM? | `GET /api/v1/admin/data-dir` |
| GET | `/api/health/state` | ADM? | folded into `/readyz` details |
| GET | `/admin/backup/state.json` | ADM? | folded into `/readyz` details |
| GET | `/admin/backup/docs/<path:name>` | ADM? | served from React/static docs |

## Ops / meta (→ `/healthz`, `/readyz`, `/api/v1/*`)

| Method | v2 path | Auth | v1 target |
|---|---|---|---|
| GET | `/health` | PUB | `GET /healthz` |
| GET | `/ready` | PUB | `GET /readyz` (+ checks detail) |
| GET | `/api/metrics` | PUB | `GET /api/v1/metrics` (Prometheus text + JSON) |
| GET | `/metrics`, `/metrics/live` | ADM | React views |
| GET | `/api/kpi` | ADM | `GET /api/v1/kpi` |
| GET | `/api/metrics/live` | ADM | `GET /api/v1/metrics/live` |
| GET | `/system-info` (GET,POST) | ADM | React view over `GET /api/v1/version` + config |
| GET | `/changelog`, `/api/changelog` | PUB | React view / `GET /api/v1/changelog` |
| GET | `/upgrade`, `/docs/upgrade`, `/api/upgrade-guide` | PUB | React view / `GET /api/v1/upgrade-guide` |
| GET | `/api/upgrade-info` | PUB? | folded into version endpoint |
| GET | `/api/latest-version` | PUB | `GET /api/v1/version` (current+latest+update_available) |
| — | 404 / 500 handlers | — | RFC 7807 problem envelope, React error routes |

## Counts
- 65 routes + 2 error handlers. HTML-page routes (~20) become React routes (no API);
  JSON/form endpoints (~45) map to ~35 `/api/v1` endpoints (bulk + pagination folding).
