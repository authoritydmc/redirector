# Threat model (EPIC-05 task 1) — one page

## Assets (what hurts if lost/taken)

| Asset | Impact of compromise |
|---|---|
| Redirect mappings + upstream targets | Phishing (evil targets), broken navigation |
| Admin password / JWT secret / API keys / TOTP seeds | Full admin impersonation |
| Backup archives | Everything above, offline |
| End-user request logs (IPs, patterns) | Privacy breach |
| Availability of `/{pattern}` | Every shared link dies |

## Attackers (who, with what)

- **Credential stuffer**: botnet passwords against `/login` and `/mfa/verify`
  → mitigated by 5/min/IP rate limits (in-memory now, Redis-backed at scale),
  TOTP + backup codes, JWT-only key management.
- **Network observer**: HTTP-sidecar reads tokens/redirects → TLS terminates
  at the proxy (see `deploy/examples/`); `Secure`/`HttpOnly`/`SameSite` cookie
  discipline when the SPA lands (EPIC-02).
- **Malicious upstream**: rogue `base_url` serves phishing targets →
  fail-URL/status checks, SSO-target quarantine (`no-store`, never cached),
  `is_safe_redirect_target` (http/https only) on every serve path.
- **Stolen backup archive**: offline read → archives carry hashes only, env
  secrets named-not-valued in the manifest; encrypt archives at rest (open).
- **Rogue admin / leaked API key**: blast radius bounded by scopes
  (`admin:read` vs `admin:write`), key revocation, `last_used_at` audit;
  roles (owner/admin/editor/viewer) still open.

## Trust boundaries

```
browser ──TLS──▶ proxy ──▶ FastAPI ──┬──▶ SQLite/Postgres (domain rows)
                                     ├──▶ Redis (cache, broker, future: rate limits)
                                     └──▶ upstreams (untrusted third parties)
```

- **Proxy → app**: app trusts `X-Forwarded-For` for rate-limit keys *only*
  when deployed behind the example configs (they set it); direct exposure
  without a proxy misattributes clients — always front with nginx/Caddy.
- **Flask ↔ FastAPI (EPIC-08 dual-serve)**: two codebases share `data/`;
  the v2 JSON config (plaintext secrets) is legacy — v3 never writes it,
  `import-v2` never migrates secrets.
- **IdP (future SSO, EPIC-05)**: IdP compromise → role mapping is the blast
  radius; mapping table is admin-edited, default role `viewer`, SCIM
  deprovision honored.

## Deliberately out of scope

DDoS absorption (CDN's job), WAF tuning, client-device compromise,
malicious operator with host access (game over by definition).
