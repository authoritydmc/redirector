# [EPIC-02] Frontend: Jinja (32 templates) → React SPA (Vite + TS)

> **Labels:** `epic`, `frontend`, `react` · **Parent:** EPIC-MASTER · **Estimate:** L (3–4 weeks)

## Problem
- 32 Jinja files in `app/templates/` (`dashboard.html`, `admin_config.html`, `check_upstreams_stream.html`, …) with inline JS + Tailwind CDN — no components, no types, no tests.
- Every UI tweak requires backend redeploy; real-time upstream log page uses full-page streaming hacks instead of SSE/websocket.
- No client-side state: pagination/filtering (`/dashboard-shortcuts`) re-renders server-side; QR, metrics-live pages poll naively.

## Proposal
New `frontend/` SPA: **React 18 + Vite + TypeScript + Tailwind + TanStack Query + Zustand + React Router**:

```
frontend/
├── src/
│   ├── routes/          # / (redirect debug), /dashboard, /admin/*, /login
│   ├── features/
│   │   ├── shortcuts/   # table, create/edit drawers, bulk actions
│   │   ├── upstreams/   # config table + live check log (SSE)
│   │   ├── metrics/     # charts (recharts) polling /api/v1/metrics
│   │   └── auth/        # login, MFA (TOTP + WebAuthn), setup wizard
│   ├── components/ui/   # shadcn-style primitives (button, dialog, table)
│   ├── lib/api.ts       # OpenAPI-generated client (orval/openapi-ts)
│   └── e2e/             # Playwright specs
└── vite.config.ts       # proxy /api → :8000 in dev, static build for prod
```

Key decisions:
- **API client generated** from FastAPI OpenAPI (`orval`/`openapi-typescript`) — kills hand-written `fetch` drift.
- **Design tokens**: port existing dark-mode palette; replace CDN Tailwind with compiled Tailwind v4.
- **Pages first**: Dashboard → Create/Edit → Upstreams + live log (SSE showcase) → Admin config → Metrics → MFA/setup wizard → version/system-info.
- Keep `GET /{pattern}` redirect page ultra-light: server-rendered minimal HTML (no React bundle) for speed; React only for app UI.

## Acceptance criteria
- [ ] Feature parity checklist (all 32 templates mapped, signed off) — legacy template served only if `?legacy=1`.
- [ ] Lighthouse ≥ 90 on dashboard; dashboard TTI < 1.5s on broadband.
- [ ] `npm run typecheck`, `vitest`, `playwright test` green in CI.
- [ ] No Jinja in new code; `app/templates/` frozen (bugfixes only) after cutover.

## Phased tasks
- [x] 1. Scaffold Vite+TS+Tailwind+Router+Query; MSW mocks from OpenAPI examples (unblocks UI before backend done).
  (Scaffold done on `v3/epic-01-backend-foundation`: `frontend/` with Vite
  + React 19 + TS strict + Tailwind v4, dev proxy to the v3 API, health
  shell, generated client, vitest smoke, `frontend.yml` CI
  (typecheck+test+build, incl. codegen-freshness gate). MSW mocks
  deliberately skipped — the backend is done, so UI builds against the
  real API. Router/Query/data-table work lands with tasks 2–4.)
- [x] 2. Auth + shell layout (nav, dark mode, toasts).
  (Done: `AuthProvider` (JWT localStorage, MFA-challenge login flow),
  `/login` with TOTP step, protected routes + `RequireAuth`, `Layout`
  (nav, class-based dark mode persisted, sign-out), RTL/jsdom component
  tests (login render/error/success/redirect-guard). Toasts deferred to
  the first feature that needs them — inline form errors cover auth.)
- [ ] 2. Auth + shell layout (nav, dark mode, toasts).
- [ ] 3. Shortcuts datatable (server pagination, filters, bulk delete) + create/edit drawers with Zod validation.
- [ ] 4. Upstream config + live-check SSE view (replaces `check_upstreams_stream.html`).
- [ ] 5. Admin config, Redis/upstream cache, import/export, backup, metrics-live, version page.
- [ ] 6. MFA setup/verify (TOTP QR + WebAuthn), setup wizard, 404/500 routes.
- [ ] 7. Production build served by FastAPI `StaticFiles` (single container) + CDN-friendly hashed assets.

## `gh` snippet
```bash
gh issue create --title "[EPIC-02] Frontend: Jinja → React SPA (Vite + TS)" \
  --label "epic,frontend" --body-file docs/refactor-epics/02-frontend-react.md
```
