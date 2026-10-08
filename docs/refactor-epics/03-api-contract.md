# [EPIC-03] API contract: versioning, pagination, error envelope

> **Labels:** `epic`, `backend`, `api-design` · **Parent:** EPIC-MASTER · **Estimate:** M (1–2 weeks, but blocks EPIC-01/02)

## Problem
Today's "API" is accidental: `GET /dashboard-shortcuts`, `POST /api/delete-shortcut/<pattern>`, `GET /api/metrics`, `GET /api/qr/<pattern>` — inconsistent verbs, no versioning, no pagination contract, HTML and JSON mixed in same blueprints (`routes/`, `redirection_routes.py`, `upstream_routes.py`, `mfa_routes.py`, `version_routes.py`, `metrics_routes.py`, `backup_routes.py`).

## Proposal
Contract-first under `/api/v1`, designed alongside EPIC-01, consumed by EPIC-02:

| Area | Endpoints |
|---|---|
| Redirect (hot) | `GET /{pattern}` (HTML/302, **unversioned** — permanent), `GET /api/v1/resolve?pattern=` (JSON debug) |
| Shortcuts | `GET/POST /api/v1/shortcuts`, `GET/PATCH/DELETE /api/v1/shortcuts/{pattern}`, `POST /api/v1/shortcuts:bulk-delete` |
| Upstreams | `GET/POST /api/v1/upstreams`, `PATCH/DELETE /api/v1/upstreams/{id}`, `POST /api/v1/upstreams/check` (SSE stream), `GET/DELETE /api/v1/upstream-cache` |
| Auth | `POST /api/v1/auth/login`, `/mfa/verify`, `/mfa/setup`, `POST /api/v1/auth/api-keys` |
| Admin | `GET/PATCH /api/v1/admin/config`, `GET /api/v1/admin/redis`, `POST /api/v1/admin/backup`, import/export |
| Ops | `GET /healthz`, `/readyz`, `GET /api/v1/metrics` (Prometheus text + JSON), `GET /api/v1/version`, `GET /api/v1/qr?pattern=` |

Standards:
- **Envelope:** success `{data, meta:{page,pageSize,total}}`; errors RFC 7807 `{type,title,status,detail,instance,code}`. No bare strings.
- **Pagination:** `?page=&pageSize=&q=&sort=` everywhere list-like (cursor pagination for >10k rows — shortcuts table will grow).
- **Validation:** Pydantic schemas are the contract; OpenAPI published at `/api/docs`; breaking change ⇒ `/api/v2`.
- **Idempotency:** `Idempotency-Key` header on create/import; bulk ops return per-item results.

## Acceptance criteria
- [x] `openapi.json` committed + diff-checked in CI (fail on unreviewed contract change).
  (Done on `v3/epic-01-backend-foundation`: `docs/openapi.json` snapshot +
  `scripts/export-openapi.py` stamp/`--check` gate, enforced in
  `validate.yml` backend-smoke. Regenerate after any router/schema change
  and on every VERSION bump — the spec carries the app version.)
- [ ] Generated TS client (`frontend/src/lib/api.ts`) compiles; no hand-written endpoint strings in React.
- [ ] Legacy endpoints shimmed with `Deprecation: true` header + sunset date, mapped in EPIC-08.
- [ ] Contract tests (schemathesis / schemathesis-style snapshot) green.

## Tasks
- [x] 1. Inventory every current route (method+path+auth+shape) into `docs/api-inventory.md`.
  (Done: 65 routes + 2 error handlers inventoried with v1 targets; auth
  column best-effort, flagged for contract-test re-verification.)
- [ ] 2. Write OpenAPI-first YAML for `/api/v1` (review with frontend before coding).
- [x] 3. Define error codes catalog (`SHORTCUT_CONFLICT_UPSTREAM`, `MFA_REQUIRED`, …).
  (Done as `docs/error-codes.md`: all 30 `domain:reason` codes with HTTP
  meanings, enforced both directions by `tests/test_v3_error_codes.py` —
  new codes fail until documented, documented-but-unemitted codes fail
  until removed.)
- [x] 4. Pagination + filtering spec (incl. `q` semantics: prefix vs substring).
  (Done as `docs/pagination.md`, verified against `list_paged`: `q` is a
  case-insensitive substring over pattern-or-target; two tiers documented
  (full pages for shortcuts, bounded lists elsewhere); cursor graduation
  trigger defined but not built.)
- [x] 5. Auth scheme for API (Bearer JWT + API keys, scopes) — coordinate EPIC-05.
  (Done: transports, `admin:read`/`admin:write` vocabulary, 401-vs-403
  semantics, and the per-route audit table documented in
  `backend/README.md` under Authorization audit.)
- [ ] 6. Deprecation map: old path → new path + Sunset header.

## `gh` snippet
```bash
gh issue create --title "[EPIC-03] API contract: versioning, pagination, error envelope" \
  --label "epic,backend" --body-file docs/refactor-epics/03-api-contract.md
```
