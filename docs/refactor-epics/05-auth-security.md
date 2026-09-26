# [EPIC-05] Auth & security: JWT + RBAC + API keys, secrets hygiene

> **Labels:** `epic`, `security`, `auth` · **Parent:** EPIC-MASTER · **Estimate:** M (2 weeks)

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
- **Secrets hygiene:** `session_secret`/JWT keys + MFA seeds out of JSON → env (`JWT_SECRET`, `REDIRECTOR_DATA_DIR/secrets/` with 0600) or Docker secrets; JSON importer warns + redacts.
- **Hardening:** rate-limit login (`slowapi`/middleware, 5/min/IP + account lockout), CSRF for cookie flows, strict CSP (React build hashes), `Referrer-Policy`, HSTS behind proxy, dependency audit (`pip-audit`/`npm audit`) in CI, CodeQL already on.

## Acceptance criteria
- [ ] No plaintext secrets at rest (grep `admin_password` in `data/` finds only hash/manifest).
- [ ] All `/api/v1/*` (except health/login) require auth; e2e proves 401→login→MFA→200 and expired-token refresh.
- [ ] API key can drive CLI/import automation without browser session.
- [ ] Rate-limit + lockout tests; OWASP ZAP baseline no HIGHs on auth flows.
- [ ] Migration: v2 password + MFA seeds carry over; users not locked out.

## Tasks
- [ ] 1. Threat model (1 page): assets, attackers, trust boundaries.
- [ ] 2. Hash + JWT + refresh rotation + logout denylist (Redis).
- [ ] 3. RBAC middleware + per-route scopes audit.
- [ ] 4. API keys (prefix+secret, sha256 store, last-used, revoke UI).
- [ ] 5. TOTP/WebAuthn port + backup-code hashing.
- [ ] 6. Secrets migration CLI (`redirector secrets migrate --from-json`).
- [ ] 7. Rate limits, lockout, audit log (`auth_events` table).

## `gh` snippet
```bash
gh issue create --title "[EPIC-05] Auth & security: JWT + RBAC + API keys, secrets hygiene" \
  --label "epic,security" --body-file docs/refactor-epics/05-auth-security.md
```
