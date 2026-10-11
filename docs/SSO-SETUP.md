# SSO Setup — multi-IdP OIDC logins

Self-hosted Redirector can delegate sign-in to any standards-compliant
OpenID Connect provider — Google Workspace, Microsoft Entra ID, Keycloak,
Authentik, Okta — **several at once**. The local admin password keeps
working; SSO is an additional door, not a replacement.

How it works: the login page shows one button per configured provider →
IdP login (PKCE + `state` + `nonce`) → callback verifies the ID token →
first sight JIT-provisions a user row → group membership decides the role
→ the browser lands back with the **same JWT session** local logins use,
so RBAC, API keys, and audit logging behave identically.

## 1. Configure providers (env)

Non-secret wiring is one JSON document; each secret gets its own env var
(secrets never touch the DB, backups, or logs):

```sh
export REDIRECTOR_OIDC_PROVIDERS='[
  {"id": "google", "display_name": "Google",
   "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
   "client_id": "1234567890-abc.apps.googleusercontent.com",
   "admin_groups": ["redirect-admins"]},
  {"id": "entra", "display_name": "Entra ID",
   "discovery_url": "https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration",
   "client_id": "<application-client-id>",
   "admin_groups": ["sg-redirect-admins"]}
]'
export REDIRECTOR_OIDC_GOOGLE_CLIENT_SECRET='...'
export REDIRECTOR_OIDC_ENTRA_CLIENT_SECRET='...'
# Where the callback sends the browser (fragment-carried token):
# "/app/login" in prod (SPA at /app), "/login" for vite dev.
export REDIRECTOR_SSO_LOGIN_URL='/app/login'
```

Field notes:

- `id`: slug for URLs and the secret env name (`REDIRECTOR_OIDC_<ID>_CLIENT_SECRET`, uppercased).
- `groups_claim` (optional, default `"groups"`): token claim carrying
  membership. The `roles` claim (Entra app roles) and `wids` (Entra
  directory roles) are always checked as well.
- `admin_groups`: **any match grants admin; empty means nobody gets in
  via that provider.** Default deny — SSO never widens access silently.
  Everyone else is stored as `viewer` (auditable, mints nothing).

Restart, then validate without a browser:

```sh
PYTHONPATH=. python -m backend.cli.sso --provider google \
  --login-base-url https://r.example.com
```

Register the callback URL it prints at the IdP
(`https://r.example.com/api/v1/auth/oidc/google/callback`) — redirect-URI
mismatch is failure #1, the doctor catches the rest.

## 2. IdP walkthroughs

### Google Workspace

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs &
   Services → Credentials → **Create Credentials → OAuth client ID** →
   **Web application**.
2. Authorized redirect URI:
   `https://<host>/api/v1/auth/oidc/google/callback`.
3. Copy the client ID + secret into `REDIRECTOR_OIDC_PROVIDERS` /
   `REDIRECTOR_OIDC_GOOGLE_CLIENT_SECRET`.
4. Groups: Google emits Workspace group membership in ID tokens only
   with extra configuration (Admin SDK / custom claims via a proxy) —
   out of the box there is **no group evidence**, so Google logins land
   as `viewer` (`#error=not-authorized`). When group mapping matters,
   prefer Entra ID or Keycloak below; use Google for identity only if
   you additionally gate elsewhere.

### Microsoft Entra ID

1. Entra admin center → Identity → Applications → App registrations →
   **New registration**. Redirect URI type **Web**:
   `https://<host>/api/v1/auth/oidc/entra/callback`.
2. Certificates & secrets → **New client secret** (note expiry in your
   calendar — expired secret is failure #2).
3. Token configuration → **Add groups claim** (Security groups, ID
   tokens) — or **Add app roles** and assign users/groups to them (lands
   in the `roles` claim, checked automatically).
4. API permissions: `openid`, `profile`, `email` (delegated; admin
   consent if required).
5. Discovery URL uses your tenant:
   `https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration`.
   Group overage (user in >~200 groups): Entra omits the claim and points
   at Graph instead — keep admin groups few, or map `roles` (no overage).
6. Set `admin_groups` to the group **object IDs or names** as they appear
   in the claim (sAMAccountName vs object ID bites here — decode one
   token at [jwt.ms](https://jwt.ms) to see exact values).

### Keycloak / Authentik (self-hosted IdP)

Keycloak:

1. Realm → Clients → **Create client**: client ID = Redirector's
   `client_id`, Client authentication **On**, Standard flow **On**.
2. Valid redirect URIs: `https://<host>/api/v1/auth/oidc/keycloak/callback`
   (be exact — trailing slashes matter).
3. Credentials tab → copy the secret.
4. Assign users to a group (e.g. `redirect-admins`); groups land in the
   token via the built-in `groups` mapper (Client scopes → groups).
5. Discovery:
   `https://<keycloak>/realms/<realm>/.well-known/openid-configuration`.

Authentik: Providers → OAuth2/OpenID → New (redirect URI as above),
note client ID/secret; Property mappings → add a `groups` scope mapping;
the outpost/embedded endpoint serves `/.well-known/openid-configuration`.

## 3. Verify

1. `python -m backend.cli.sso --provider <id> --login-base-url https://<host>` → all ok.
2. Open the login page → **Continue with \<Name\>** → sign in at the IdP.
3. Admin lands on `/`; others see “not in an admin group” and an
   `sso.denied` row appears in the auth audit log.
4. `/api/v1/auth/me` with the session shows
   `{"role": "admin", "sub": "sso:<id>:<subject>"}`.

## 4. Troubleshooting

| Symptom | Check |
|---|---|
| `#error=unknown-provider` | Provider id typo, or its secret env var missing (login refuses unsecreted providers). |
| `#error=invalid-state` | Back-button re-submit, 10-min TTL expiry, or two tabs racing (states are one-shot). |
| `#error=bad-discovery` | Server can't reach the IdP (DNS/TLS/outbound); run the doctor from the server. |
| `#error=invalid-token` | Audience mismatch (wrong `client_id`), expired secret, or **clock skew** > 60 s (run NTP). |
| `#error=not-authorized` | Expected for non-members: check group names/IDs against a decoded token; Entra overage → use `roles`. |
| Login button missing | Provider not listed by `GET /api/v1/auth/providers` → secret missing or JSON invalid (server logs the parse error at startup? no — re-run the doctor). |

## 5. Limitations (follow-ups, not bugs)

- Roles beyond `admin` don't exist yet: mapped users are full admins,
  everyone else is `viewer` (stored, mints nothing). Per-user RBAC is next.
- MFA happens **at the IdP** — enforce phishing-resistant auth there;
  local TOTP does not apply to SSO sessions.
- No SAML (only if a tenant mandates it), no SCIM provisioning, no
  Admin-UI provider editor (env + restart for now).
- IdP tokens are never stored; sessions are Redirector JWTs with the
  standard lifetime (`Session lifetime` setting applies).
