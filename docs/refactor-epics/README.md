# Refactor epics — create GitHub issues

Drafts live in this folder. One command per epic (run from repo root, `gh` already authenticated as `authoritydmc`):

```bash
# Master tracking issue
gh issue create --title "[EPIC-MASTER] Sitewide architecture refactor — FastAPI + React + async-first" \
  --label "epic" --body-file docs/refactor-epics/00-master-epic.md

# Child epics
gh issue create --title "[EPIC-01] Backend: Flask → FastAPI (async-first, DI, OpenAPI)" \
  --label "epic" --body-file docs/refactor-epics/01-backend-fastapi.md

gh issue create --title "[EPIC-02] Frontend: Jinja → React SPA (Vite + TS)" \
  --label "epic" --body-file docs/refactor-epics/02-frontend-react.md

gh issue create --title "[EPIC-03] API contract: versioning, pagination, error envelope" \
  --label "epic" --body-file docs/refactor-epics/03-api-contract.md

gh issue create --title "[EPIC-04] Data & cache: async SQLAlchemy, Postgres-first, Redis abstraction" \
  --label "epic" --body-file docs/refactor-epics/04-data-cache.md

gh issue create --title "[EPIC-05] Auth & security: JWT + RBAC + API keys, secrets hygiene" \
  --label "epic" --body-file docs/refactor-epics/05-auth-security.md

gh issue create --title "[EPIC-06] Performance & realtime: async upstream fan-out, jobs, SSE" \
  --label "epic" --body-file docs/refactor-epics/06-perf-realtime.md

gh issue create --title "[EPIC-07] Quality & DevEx: typing, tests, CI/CD, Docker, monorepo" \
  --label "epic" --body-file docs/refactor-epics/07-quality-devex.md

gh issue create --title "[EPIC-08] Migration & rollout: strangler-fig, dual-serve, rollback" \
  --label "epic" --body-file docs/refactor-epics/08-migration-rollout.md
```

After creating, link children to master with `gh issue edit <num> --add-project ...` or tasklists, and apply stack rationale (`STACK-DECISIONS.md`) as a pinned comment on the master issue.

Suggested labels to create first: `epic`, plus per-epic `backend/frontend/security/performance/dx/release/api-design/database/caching/migration/architecture`.
