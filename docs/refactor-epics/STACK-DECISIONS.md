# Stack decisions & rationale (v3 refactor)

You gave free hand on libs. Below is what I'd actually pick — biased toward **boring, self-hostable, zero-data-loss** over shiny. Every row: chosen → rejected alternatives → why.

## Backend: FastAPI (not Litestar, not Django Ninja, not staying on Flask)

| Option | Verdict |
|---|---|
| **FastAPI + Uvicorn** | ✅ CHOSEN |
| Litestar | Faster benchmarks, cleaner DI — but ~10× smaller community, fewer hiring/StackOverflow answers, plugin churn. Not worth it for a CRUD+redirect service. |
| Django Ninja / Django | Brings full Django ORM/admin baggage; async story weaker; migration from Flask-SQLAlchemy → Django ORM = table/column renames = data-loss risk. |
| Stay on Flask + gevent | Zero migration cost, but keeps `monkey.patch_all()`, sync-only handlers, no OpenAPI, manual validation forever. This is the core bottleneck. |

**Rationale:** team already thinks in Flask route terms; FastAPI is the smallest mental jump with the biggest payoff (Pydantic v2 validation + free OpenAPI + native async). Hiring pool and AI-tooling support are unmatched. Uvicorn replaces gunicorn+gevent with one process model that works identically in dev and prod.

## ORM + migrations: SQLModel + Alembic (clean v3 schema, best-effort v2 import)

> Context update: not yet public → schema breaks are free. Old constraint (byte-perfect
> v2 DB upgrade) is dropped. Optimizing for developer velocity + correct schema instead.

| Option | Verdict |
|---|---|
| **SQLModel + Alembic** | ✅ CHOSEN |
| Raw SQLAlchemy 2.0 async + Alembic | Mature and powerful, but forces two parallel model layers (ORM classes + Pydantic schemas) with manual mapping in every module. For a 4-table CRUD service that's pure boilerplate tax. Still the fallback if SQLModel hits a wall — it's SQLAlchemy under the hood, so escape hatches are trivial. |
| Prisma Python | Best raw DX (typed client, clean migrations), but ships a Rust query-engine binary (heavier Docker, slower CI, binary-compat surprises on alpine/slim), smaller Python community, and a query API nobody on a Flask background knows. Migration story back to mainstream tooling is poor. |
| Tortoise ORM + Aerich / Piccolo | Async-native and pleasant, but niche: fewer answers, fewer plugins, Aerich lags Alembic badly (no real branching, weak autogenerate). Accepting ecosystem risk for no decisive DX win over SQLModel. |
| Raw SQL / query builder | Loses migration tooling entirely. No. |

**Rationale:** SQLModel is one class = table + request/response schemas, maintained by the
FastAPI author, and it *is* SQLAlchemy+Pydantic underneath — so `asyncpg`/`aiosqlite`,
connection pooling, and Alembic autogenerate all keep working, and any exotic query can
drop to raw SQLAlchemy. Because we can break schema, v3 gets a **clean redesign**
instead of inheriting v2's sins: real `DateTime(timezone=True)` columns (today they're
`String` ISO blobs in `model/redirect.py:16-17,23`), `visibility` as a proper Enum,
`tags` as JSON (not comma-separated string), `expires_at` as DateTime (not String).
v2 data gets a **best-effort importer** (`redirector import-v2 --data-dir`), not a
byte-perfect migration — a script that parses the old rows/JSON and inserts into the new
schema, allowed to normalize/drop junk. Alembic stays for v3→v3+ migrations (branching,
downgrades, CI-tested), which is exactly what it's good at.

## Validation/settings: Pydantic v2 + pydantic-settings

Uncontroversial. Replaces the 427-line `Config` god-object and untyped `request.form.get()` chains. Env-first (`REDIRECTOR_*`, `DATABASE_URL`, `REDIS_URL`) with a one-shot JSON importer for backward compat. No alternative (attrs/cattrs/Traitlets) comes close on ecosystem.

## HTTP client: httpx (async) — replaces `requests`

`requests` is sync-only and appears on the redirect hot path (`upstream_routes.py`, `routes.py`, `versioning.py`). `httpx.AsyncClient` gives pooled async fan-out with timeout budgets. (`aiohttp` is equally capable but heavier API for simple GET fan-out; `httpx` mirrors `requests` so the port is mechanical.)

## Background jobs: arq (not Celery, not Dramatiq)

| Option | Verdict |
|---|---|
| **arq** | ✅ CHOSEN — asyncio-native, Redis-only (you already ship Redis), no beat/flower/scheduler daemons. Job = plain async function. Perfect for resync/purge/backup. |
| Celery | Industry standard but sync-oriented, needs broker+backend+beat, heavy ops for a self-hosted single binary. Overkill. |
| Dramatiq / RQ | Sync or thinner ecosystems; RQ has no native async/scheduling story. |
| SAQ | Credible runner-up (also asyncio+Redis); pick arq on docs/maturity, either is fine — abstraction keeps it swappable. |

## Cache: keep Redis (+ `MemoryCache`/`Noop` via Protocol)

No fork drama needed: Redis 7 API you use (`GET/SET/DEL`, TTLs) is identical on Dragonfly/Valkey — document them as drop-ins, don't code to them. The real fix is the `Cache` Protocol abstraction (singleflight, TTL discipline, fake for tests), not the server.

## Frontend: React 18 + Vite + TypeScript (not Next.js, not Svelte, not HTMX)

| Option | Verdict |
|---|---|
| **React SPA (Vite, TS, Tailwind, TanStack Query, Zustand, React Router)** | ✅ CHOSEN |
| Next.js | SSR buys nothing here (redirect hot path must stay a bare server 302, not a hydrated page). Adds a Node server to every self-host install. Worse ops, no benefit. |
| SvelteKit / Solid / Astro | Lovely DX, smaller hiring pool and component ecosystem (tables, command palettes, WebAuthn helpers). Harder to staff. |
| HTMX / Alpine (keep server-render) | Tempting minimal path — but preserves the exact coupling we're escaping (every UI change = backend deploy, Python-string HTML, zero types). Rejected on architectural grounds. |

**Rationale:** React has the deepest component ecosystem (shadcn primitives, TanStack Table/Query, Recharts) and the largest hiring/contributor pool. Vite keeps dev instant and builds to static files FastAPI can serve from the same container — no extra runtime. API client generated from OpenAPI (`orval`/`openapi-typescript`) + Zod validation kills an entire class of frontend/backend drift bugs. Keep the redirect landing page as server-rendered minimal HTML (no bundle) for speed; React owns only the app UI.

## Auth: JWT (PyJWT) + argon2 (pwdlib) + `webauthn` lib + OIDC SSO (authlib) — no external IdP dependency

Self-hosters won't run Keycloak/Auth0 as a requirement. Short-lived access JWT + rotating httpOnly refresh + per-user TOTP/WebAuthn + scoped API keys covers browser + CLI without new infrastructure. `python-jose` is unmaintained — use `PyJWT` + `pwdlib[argon2]` + `webauthn`.

**Enterprise SSO: OIDC-first via `authlib`, SAML only on demand, SCIM inbound minimal.** Rationale: every modern IdP (Entra ID, Okta, Google, Keycloak) speaks OIDC — one code path covers all of them, with PKCE+nonce and group-claim role mapping. SAML (`python3-saml`) is implemented once behind a flag purely for legacy tenants that mandate it; it is not the recommended path and gets no new features. SCIM inbound (Users + group-push) closes the deprovisioning gap so disabling a user in Entra/Okta actually locks them out here. All SSO funnels into the same internal JWT + RBAC as local login, so nothing downstream branches on "how did this user authenticate". `python3-saml` and SCIM are deliberately the only enterprise-specific libs — everything else stays boring.

## Observability: structlog + OpenTelemetry + Prometheus

`structlog` (JSON logs) → OTel SDK (traces, ready when you want a collector) → `prometheus_client` (keep existing `/metrics` semantics, add cache-hit/job gauges). Grafana stays optional. No SaaS dependency.

## Tooling: uv + ruff + mypy (strict) / pnpm + tsc + eslint + vitest + Playwright + k6

- `uv` over pip: 10–50× faster resolves, lockfile, same `requirements` semantics. Falls back to pip fine.
- `ruff` over flake8/black/isort: one tool, faster, stricter. `mypy --strict` on `backend/` only (ratchet, not big-bang).
- `vitest` (unit) + `Playwright` (e2e vs compose stack) + `k6` (perf budgets in CI). Each covers a gap today's 7-file pytest suite leaves open.

## Containers: multi-stage Docker, uvicorn + nginx-static split

`Dockerfile.api` (uv → slim runtime, non-root, `/healthz` check) + `Dockerfile.web` (node build → nginx static) + compat all-in-one tag. Compose profiles: `dev` (hot reload both), `prod-sqlite` (default), `prod-postgres`, `scale` (N api replicas + lb). Same `/app/data` volume contract — never break the mount.

## The one-line summary

**FastAPI + SQLModel/Alembic (clean v3 schema) + arq + Redis on the backend, React+Vite+TS on the frontend, JWT+RBAC for auth, strangler-fig migration — because with no public installs to protect, velocity and schema correctness beat backward-compat: one model class per entity, async throughout, shippable milestones.**
