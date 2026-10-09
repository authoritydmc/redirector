# ADR-0001: Strangler-fig migration (dual-serve milestones, no big bang)

- Status: accepted
- Date: 2026-10-08
- Context: the v2 Flask monolith serves real installs (`data/` with db +
  JSON config + backups). A rewrite that strands self-hosters is worse
  than no rewrite. EPIC-MASTER sets the target stack (FastAPI + React +
  async-first).
- Decision: strangler fig in shippable milestones — M1 FastAPI alongside
  Flask (`/api/v1/*` first, proxy in front), M2 React SPA at `/app`,
  M3 redirect hot-path cutover, M4 Flask removal. Every milestone is
  independently shippable and reversible via env flags or image tags,
  with auto pre-migration backups.
- Consequences: prolonged dual code (proxy config, two auth sessions
  during transition), but no flag day; each M-gate is verifiable
  (see EPIC-08). M1 harness: `docker/compose.m1.yml`.
