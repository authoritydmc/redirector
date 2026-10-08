# ADR-0003: Committed OpenAPI snapshot as the API contract gate

- Status: accepted
- Date: 2026-10-08
- Context: the v2 "API" is accidental per-route shapes; the React SPA
  needs a stable codegen target, and reviewers need to see contract
  changes in diffs.
- Decision: `docs/openapi.json` is committed and CI-enforced
  (`scripts/export-openapi.py --check`); the TS client generates from it
  (`frontend/src/lib/api.ts`, freshness gated too). Regenerate after any
  router/schema change and on VERSION bumps. No hand-written endpoint
  strings in the SPA.
- Consequences: contract changes are always deliberate two-file diffs
  (spec + client); reviewers sign off on shape changes, not just code.
  Breaking changes still require `/api/v2` (no versioning by accident).
