# v3 error-code catalog (EPIC-03 task 3)

Every API error is an RFC 7807 problem with a machine-readable `code`
(`<domain>:<reason>`). Clients switch on `code`, never on human text.
A test (`test_error_codes_cataloged`) fails when backend code introduces a
code missing here — document first, then implement.

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
| `auth:mfa-enrolled` | 409 | Setup/enable while already enrolled |
| `auth:mfa-not-enrolled` | 400/409 | Verify/regenerate/disable with nothing enrolled |
| `auth:mfa-setup-required` | 409 | Enable before setup staged a seed |
| `auth:required` | 401 | No (or non-Bearer) credentials on a guarded endpoint |

## `backup` — archives (admin)

| Code | HTTP | Meaning |
|---|---|---|
| `backup:invalid-label` | 422 | Label outside `[a-z0-9-]` (max 32) |
| `backup:not-found` | 404 | No archive with that name (also on traversal-shaped names) |

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
