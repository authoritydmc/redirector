# Teams — normal users, team links, membership

Admins run the install; **team accounts** use it. Anyone signs up from the
login page (email + password, PBKDF2-hashed like API secrets); the admin
disables abuse accounts from Admin → Users. No invites, no email
verification in v1 — this is a self-hosted box behind your own network,
not a public SaaS (see Decisions).

## What users can do

- Create links (wherever creation is public, like anyone).
- Edit/delete **their own** links (stamped `owner_email` at creation —
  the stored username; admins override it per link, blank stays blank
  for anonymous creates).
- Found teams (becoming team-admin), manage their rosters, post
  **team-visibility** links that resolve for members only.
- Everything admin-shaped (upstreams, jobs, metrics config, backups,
  keys, settings, user admin) stays 403.

Team links resolve for: site admins, the owner, and team members
(verified per request from live membership rows — removals bite
immediately, no session refresh needed).

## Endpoints

| Method | Path | Who |
|---|---|---|
| POST | `/api/v1/auth/signup` | anyone (5/min) |
| POST | `/api/v1/auth/user/login` | anyone (5/min) |
| GET | `/api/v1/auth/users` | admin JWT |
| POST | `/api/v1/auth/users/{email}/disable\|/enable` | admin JWT |
| POST | `/api/v1/teams` | any JWT session |
| GET | `/api/v1/teams` | own teams (all for admins) |
| GET/DELETE | `/api/v1/teams/{id}` | member / team-admin+site-admin (delete blocked while links reference it) |
| POST | `/api/v1/teams/{id}/members` | team-admin/site-admin, email must belong to a user or SSO identity |
| DELETE | `/api/v1/teams/{id}/members/{email}` | team-admin/site-admin, never the last admin |

User JWTs carry `role: user`, `sub: email`, `scopes: []` — admin routes
keep 403ing on scope, and key/SSO/policy management additionally
requires an admin JWT session, so a leaked user token widens nothing.

## Decisions (explicit)

- **Open signup, not invite-only.** Invites need token plumbing with no
  delivery channel on a self-hosted box; disable-in-admin covers abuse.
- **Local passwords first.** SSO members join teams by matching email;
  per-user WebAuthn/TOTP is deferred (IdP-side MFA covers SSO users).
- **No user API keys.** Automation stays admin-scoped; user tokens are
  browser sessions.
- **No per-link transfer UI yet** — admins edit `owner_email` directly.
- Cache snapshots carry `team_id` (pre-upgrade entries are correctly
  team-less: the column didn't exist, so nothing is misclassified).
