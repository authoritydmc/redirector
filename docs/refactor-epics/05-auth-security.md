# [EPIC-05] Auth & security: JWT + RBAC + API keys + enterprise SSO, secrets hygiene

> **Labels:** `epic`, `security`, `auth` · **Parent:** EPIC-MASTER · **Estimate:** L (3–4 weeks with SSO)

## Problem (current, with receipts)
- Single shared `admin_password` in plaintext JSON (`config.py:289`, printed to stdout on first boot `config.py:208`).
- Login = `request.form['password'] == admin_pwd` (`routes.py:34`, `redirection_routes.py:32`) — no hashing, no rate limit, no lockout.
- Session = `session['admin_logged_in'] = True` (`routes.py:61`, `mfa_routes.py:159`) + `session_secret` also in same JSON file — steal file = forge sessions.
- MFA seeds + backup codes in same JSON (`mfa` block, `config.py:305-310`); HMAC import signing keyed off admin password (`routes.py:290`).
- Security headers hand-rolled in `after_request` (`__init__.py:156-173`); no CSP; no RBAC (one admin bit); no API keys for automation.

## Proposal
- **Passwords:** `pwdlib`/`passlib` argon2 hashing; migration hashes existing plaintext on first successful login, then redacts JSON.
- **Sessions → JWT:** short-lived access token (15m) + rotating refresh token (httpOnly, SameSite=Lax, Secure in prod). FastAPI `HTTPBearer` + cookie dual transport (cookie for SPA, bearer for API/CLI).
- **MFA:** keep TOTP + WebAuthn/passkeys (`mfa_routes.py` logic ported), but enroll per-user, backup codes hashed; enforce MFA for admin when enabled.
- **RBAC (minimal):** roles `owner > admin > editor > viewer`; shortcuts get `owner_email/visibility` already (`redirection_routes.py:96-98`) — enforce server-side. API keys scoped (`shortcuts:read/write`, `admin:read`).
- **Enterprise SSO (OIDC-first, SAML where required):** pluggable `AuthProvider` interface with `LocalProvider` (password+MFA) + `OIDCProvider` (any OIDC IdP: Entra ID, Okta, Google Workspace, Keycloak) + `SAMLProvider` (one-directional, for legacy enterprise tenants that mandate SAML). Library: `authlib` (OIDC + OAuth2, mature, asyncio-friendly) + `python3-saml` (OneLogin, only for the SAML path). OIDC is the default recommendation — document it first, SAML second.
  - Login flow: `GET /api/v1/auth/providers` → SPA renders "Continue with SSO" buttons alongside local login → `GET /api/v1/auth/oidc/{provider}/login` (PKCE + `state` + `nonce`) → callback exchanges code → upsert user → mint internal JWT (same session format as local login, so all downstream RBAC/API-key logic is identical).
  - **JIT provisioning + IdP role mapping:** on first SSO login, auto-create user; map IdP groups/claims → internal roles via admin-configured mapping table (e.g. Entra `groups: ["sg-redirect-admins"]` → `admin`; `wids` / `roles` claim supported; default role = `viewer`, configurable). Mapping editable in Admin UI, stored in settings table.
  - **SCIM 2.0 (inbound, minimal):** `POST/PATCH/DELETE /api/v1/scim/v2/Users` + `/Groups` with bearer-token auth per IdP tenant, so Entra/Okta can push user lifecycle + group membership (deprovision on IdP disable). Start with Users + group-push mapping; full Groups CRUD only if a design partner needs it.
  - **Multi-tenancy note:** single-tenant per install (self-hosted) — SSO config is per-install, not per-org. Multiple OIDC providers allowed (e.g. Entra + Google) with per-provider role maps. True multi-org is out of scope (see master non-goals).
- **Admin setup guide (docs deliverable, part of Done):** `docs/SSO-SETUP.md` with copy-paste walkthroughs per IdP (Entra ID app registration + group claims + SCIM provisioning; Okta app + group push; Google Workspace OIDC; generic Keycloak/Authentik), required redirect URIs (`https://<host>/api/v1/auth/oidc/{provider}/callback`), claim-mapping examples, troubleshooting (clock skew, audience mismatch, group overage), plus a `redirector auth doctor --provider <name>` CLI that validates discovery doc, client secret, callback reachability and prints the exact IdP-side fix.
- **Secrets hygiene:** `session_secret`/JWT keys + MFA seeds out of JSON → env (`JWT_SECRET`, `REDIRECTOR_DATA_DIR/secrets/` with 0600) or Docker secrets; JSON importer warns + redacts.
- **Hardening:** rate-limit login (`slowapi`/middleware, 5/min/IP + account lockout), CSRF for cookie flows, strict CSP (React build hashes), `Referrer-Policy`, HSTS behind proxy, dependency audit (`pip-audit`/`npm audit`) in CI, CodeQL already on.

## Acceptance criteria
- [ ] No plaintext secrets at rest (grep `admin_password` in `data/` finds only hash/manifest).
- [ ] All `/api/v1/*` (except health/login/callback/SCIM) require auth; e2e proves 401→login→MFA→200 and expired-token refresh.
- [ ] API key can drive CLI/import automation without browser session.
- [ ] SSO e2e against a containerized IdP (Keycloak in compose `sso` profile): new user JIT-provisioned with mapped role, group change re-mapped on next login, IdP-disabled user blocked via SCIM DELETE.
- [ ] `docs/SSO-SETUP.md` reviewed by testing each IdP walkthrough verbatim on a fresh install; `auth doctor` catches the 5 most common misconfigurations (bad redirect URI, missing group claim, audience mismatch, expired secret, clock skew).
- [ ] Rate-limit + lockout tests (local + SSO callback abuse); OWASP ZAP baseline no HIGHs on auth flows.
- [ ] Migration: v2 password + MFA seeds carry over; users not locked out.

## Tasks
- [ ] 1. Threat model (1 page): assets, attackers, trust boundaries (add SSO trust boundary: IdP compromise → role mapping).
- [ ] 2. Hash + JWT + refresh rotation + logout denylist (Redis).
- [ ] 3. RBAC middleware + per-route scopes audit.
- [x] 4. API keys (prefix+secret, sha256 store, last-used, revoke UI).
  (Backend done on `v3/epic-01-backend-foundation`: `ApiKey` table
  (`rk_<prefix>_<secret>`, sha256-only storage, `last_used_at`,
  timestamp revocation), issue/list/revoke under `/api/v1/auth/api-keys`
  (JWT-session-only management — a leaked key can't mint siblings),
  verification folded into the shared admin identity so keys work
  wherever admin JWT works; scopes recorded, enforcement deferred to the
  RBAC audit (task 3). Revoke UI waits on the React SPA (EPIC-02).
  Covered by 3 new tests in `tests/test_v3_auth_api.py`.)
- [ ] 5. TOTP/WebAuthn port + backup-code hashing.
- [ ] 6. `AuthProvider` interface + `authlib` OIDC (PKCE, nonce) + role-mapping table + Admin UI for providers/mappings.
- [ ] 7. SAML path (`python3-saml`) behind `FF_SAML`, only if a tenant requires it.
- [ ] 8. SCIM 2.0 inbound (Users + group-push) with per-tenant tokens.
- [ ] 9. `docs/SSO-SETUP.md` (Entra, Okta, Google, Keycloak/Authentik) + `auth doctor` CLI.
- [ ] 10. Secrets migration CLI (`redirector secrets migrate --from-json`).
- [ ] 11. Rate limits, lockout, audit log (`auth_events` table, incl. `sso_login`, `scim_provision`, `role_mapped`).

## `gh` snippet
```bash
gh issue create --title "[EPIC-05] Auth & security: JWT + RBAC + API keys + enterprise SSO, secrets hygiene" \
  --label "epic,security" --body-file docs/refactor-epics/05-auth-security.md
```
