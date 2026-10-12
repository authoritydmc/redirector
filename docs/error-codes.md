# v3 error-code catalog (EPIC-03 task 3)

Every API error is an RFC 7807 problem with a machine-readable `code`
(`<domain>:<reason>`). Clients switch on `code`, never on human text.
A test (`test_error_codes_cataloged`) fails when backend code introduces a
code missing here — document first, then implement.

## `admin` — configuration

| Code | HTTP | Meaning |
|---|---|---|
| `admin:invalid-setting` | 422 | Settings PATCH value outside the schema (wrong type or range) |

## `auth` — login, tokens, keys, MFA

| Code | HTTP | Meaning |
|---|---|---|
| `auth:bad-credentials` | 401 | Wrong admin password at login |
| `auth:bad-pending-token` | 401 | Expired/invalid MFA pending token, or a full JWT abused as one |
| `auth:bad-totp` | 401 | Wrong TOTP token or spent backup code |
| `auth:forbidden` | 403 | Valid identity without admin role, or key-management via API key |
| `auth:insufficient-scope` | 403 | Valid key lacking the endpoint's scope (vs 401 bad key) |
| `auth:invalid-api-key` | 401 | Unknown, revoked, or mismatched API key |
| `auth:invalid-name` | 422 | Blank API key name at issuance |
| `auth:invalid-token` | 401 | Malformed JWT, or a scoped token used as general credentials |
| `auth:key-not-found` | 404 | No API key with that id |
| `auth:locked-out` | 429 | Too many failed logins from this IP inside the window |
| `auth:rate-limited` | 429 | Over the per-route budget (login, MFA verify: 5/min/IP) |
| `auth:mfa-enrolled` | 409 | Setup/enable while already enrolled |
| `auth:mfa-not-enrolled` | 400/409 | Verify/regenerate/disable with nothing enrolled |
| `auth:mfa-setup-required` | 409 | Enable before setup staged a seed |
| `auth:required` | 401 | No (or non-Bearer) credentials on a guarded endpoint |

## `users` — team accounts

| Code | HTTP | Meaning |
|---|---|---|
| `users:exists` | 409 | Signup with an already-registered email |
| `users:disabled` | 403 | Login on a disabled account |
| `users:not-found` | 404 | No account with that email |
| `users:invalid-email` | 422 | Malformed email address |
| `users:weak-password` | 422 | Password shorter than 8 characters |

## `teams` — teams and membership

| Code | HTTP | Meaning |
|---|---|---|
| `teams:exists` | 409 | Team name already taken |
| `teams:invalid-name` | 422 | Blank team name |
| `teams:invalid-role` | 422 | Member role outside member/admin |
| `teams:not-found` | 404 | No team (or membership) with that id |
| `teams:not-member` | 403 | Team link creation without membership |
| `teams:member-exists` | 409 | Email already on the roster |
| `teams:last-admin` | 409 | Removing the team's final admin |
| `teams:link-needs-team` | 422 | Team-visibility link without a team |
| `teams:links-exist` | 409 | Deleting a team that still holds links |

## `sso` — OIDC logins

| Code | HTTP | Meaning |
|---|---|---|
| `sso:unknown-provider` | 404 | No login-ready provider with that id (undeclared, or secret env var missing) |
| `sso:bad-discovery` | 502 | Discovery document unreachable or malformed |

Browser-flow failures never surface as JSON: the callback 302s to the
login page with a `#error=` fragment instead (`unknown-provider`,
`invalid-state`, `bad-discovery`, `invalid-token`, `bad-claims`,
`not-authorized`) — see `docs/SSO-SETUP.md`.

## `backup` — archives (admin)

| Code | HTTP | Meaning |
|---|---|---|
| `backup:invalid-label` | 422 | Label outside `[a-z0-9-]` (max 32) |
| `backup:not-found` | 404 | No archive with that name (also on traversal-shaped names) |

## `policy` — public action policy (admin curation)

| Code | HTTP | Meaning |
|---|---|---|
| `policy:unknown-action` | 422 | Action outside the known public-action list |

## `jobs` — background jobs

| Code | HTTP | Meaning |
|---|---|---|
| `jobs:already-terminal` | 409 | Cancel arrived after the job finished |
| `jobs:invalid-label` | 422 | Backup label outside `[a-z0-9-]` (max 32) |
| `jobs:not-found` | 404 | No job with that id |

## `upstreams` — upstream config + cache

| Code | HTTP | Meaning |
|---|---|---|
| `upstreams:conflict` | 409 | Name already taken (incl. lost create races) |
| `upstreams:not-found` | 404 | No upstream with that id/name |

## `shortcuts` — shortcut CRUD

| Code | HTTP | Meaning |
|---|---|---|
| `shortcuts:conflict` | 409 | Pattern already exists |
| `shortcuts:invalid-expires-at` | 422 | Unparseable `expires_at` |
| `shortcuts:invalid-pattern` | 422 | Illegal characters in pattern |
| `shortcuts:not-found` | 404 | No shortcut with that pattern |

## `qr` — QR rendering

| Code | HTTP | Meaning |
|---|---|---|
| `qr:generation-failed` | 500 | QR encode failed server-side |

## `resolve` — redirect hot path (outcome-derived, unversioned)

| Code | HTTP | Meaning |
|---|---|---|
| `resolve:forbidden` | 403 | Private/team shortcut, caller not allowed |
| `resolve:gone` | 410 | Expired shortcut |
| `resolve:need_params` | 422 | Dynamic shortcut missing path params |
| `resolve:not_found` | 404 | No match (with ranked suggestions) |
| `resolve:unsafe` | 400 | Non-http(s) redirect target refused |
