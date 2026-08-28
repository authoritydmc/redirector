# Changelog

All notable changes to **Redirector** — your team's `r/` links — are documented here.
We follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).
> **Public & friendly:** What changed for you — not just code.

---

## [Unreleased]

### Added
- Upcoming: more team analytics and Slack integration (tracked in GitHub issues).

---

## [3.1.0] — 2026-08-28

**The `r/` for Teams release — hierarchical, SSO-aware, and ready for your company.**

This release is built from **23 commits on 2026-08-28**, merging PRs #78–#85, #87–#90. Every commit below is an actual `git log` message, grouped for you.

### Added
- **Team shortcuts that just work** — `get_shortcut_for_path()` longest-prefix matcher for `x/abc`, `x/def`, `y/h/k` as distinct links; `sanitize_pattern()` for hierarchical paths (#35, `c4efb61`).
- **One-time setup for you** — `dynamic_shortcut_usage.html` now interactive with per-param descriptions, `localStorage` (`user_param_<pattern>_<name>`), Clear/Go (#33, `c4efb61`).
- **Starter Pack — 14 shortcuts in one click** — `app/utils/default_shortcuts.py` (Essentials: google, github, docs, drive, mail, calendar; Team: eng/docs, hr/handbook; Dynamic: jira/{ticket}, gh/{repo}; User-Dynamic: my-prs/[username]) + `GET /admin/setup` + `POST /api/install-defaults` + dashboard banner when DB empty (#79, `0cf1479`).
- **SSO never cached** — `is_sso_url()` for `accounts.google.com`, `okta.com`, `login.microsoftonline.com` etc., `should_cache_upstream_result()` always false for SSO, `purge_sso_upstream_cache()` on startup, `Cache-Control: no-store` + `X-SSO-Link` (#78, `c4efb61`).
- **Upstream SSO awareness** — `stream_check_upstreams` detects SSO before cache and redirects to original `check_url` + per-upstream `skip_sso_cache` toggle (#78).
- **MFA for admins** — `pyotp` TOTP + `qrcode` QR, backup codes, WebAuthn passkeys (Face ID/Touch ID), `GET /admin/mfa/setup` + `/admin/mfa/verify`, backup codes, `scripts/create-release-tag.ps1` now tests docker first (#78, #86).
- **Changelog you can read in-app** — `GET /changelog` + `GET /api/changelog` (Tax_Scripts style), `VERSION` file + `get_version.py --tag/--bump` (#78, `69dbb89`).
- **System Info overhaul** — `get_system_info()` (Python, platform, Docker, uptime, memory), stats (`total_shortcuts`, `total_hits`), proper semver `compare_semver()` (#85, `6bfccce`).
- **Header redesign** — Rename `URL Shortener/Redirector` → **Redirector — r/ for Teams** (`r/` badge), single-row pill nav with glass blur, mobile hamburger (#84, `a1e7ba0`).
- **Homepage redesign** — 4 stat cards, search by `pattern/target/tags`, tag pills, trending (top 3), quick-create inline, recent, help card (#82, `d302537`).
- **First-run setup wizard** — Public `GET /setup` when `setup_completed=false` + DB empty, set admin password (strength meter, generate), optionally install 6 essentials, auto-login (#82).
- **Redirect page** — Dark `#0f1221` card, pulse-ring icon, destination with favicon + copy, progress bar, 1s countdown (was 3s), `Enter`/`Esc`, QR link (#83, `0a79c3c`).
- **Docker ready for Windows** — `.gitattributes` `eol=lf`, `Dockerfile` `sed CRLF→LF`, `.dockerignore` allows `CHANGELOG.md`, migration chain fixed `f200f245867a` → `20250621b` → `20250828_enterprise` (#80, `33137a3`; #88, `5c4ac03`).
- **QR for every link** — `GET /qr/<pattern>` PNG + `GET /api/qr/<pattern>` base64, `qrcode` + `pillow` (#79).
- **Discovery** — `tags` column, `get_similar_patterns()` + `not_found.html` 404 with 3 suggestions, dashboard search now includes tags (#79).
- **Private & expiring** — `visibility` enum (`public/unlisted/private/team`) + `is_private_visible()`, `expires_at` + `is_expired()` 410, `owner_email` + migration `20250828_enterprise` (#79).
- **Health & version, once a day** — `GET /health`/`/ready` + `/api/metrics` (Prometheus), footer checks GitHub `releases/latest` + raw `VERSION` fallback, cached 24h in `localStorage`, semver compare fixes `3.10.0` > `3.9.0` (#85).

### Changed
- **Versioning** — `app/CONSTANTS.py` now reads `VERSION` file (`3.1.0`) + `git describe` suffix (`3.1.0+5.gabcdef`); `python get_version.py --bump` (#78).
- **Docker** — `auto_redirect_delay` default `3 → 1` for new installs (#83).
- **Dark mode** — `system_info.html` and `dashboard.html` no longer use `text-black` on dark (`dark:text-white` everywhere), header `dark:bg-[#0f1221]/80` with backdrop-blur (#84, `a1e7ba0`; #89, `2aee161`).

### Fixed
- **Security:** `summary.yml` command injection — quoted `"$RESPONSE"` env (`#67`, `c4efb61`).
- **Docker:** CRLF `exec ./entrypoint.sh: no such file`, `CHANGELOG.md` ignored, migration `KeyError: '20250621_add_user_param_table'` and `f200f245867a_init` (#80, #88).
- **MFA:** `mfa_setup.html` extra `{% endif %}` → `TemplateSyntaxError` 500 on `/admin/mfa/setup` (#86).
- **Redirect:** Django `|cut` filter (Jinja `No filter named 'cut'`) + em dash encoding → `TemplateAssertionError` 500 on `/redir-static` (#88).
- **Setup:** Dashboard redirect to `/setup` now bypasses when `TESTING=true` (fixes `Validate` 3 failures, 46 passed).
- **Admin Config:** Rewrote from WIP card (hover-only edits, disabled selects) to production (General/Database/Redis/Upstream, validation, no `Experimental WIP` badge) (#86).
- **Workflows:** `actions/checkout@v4`/`setup-python@v3` → `v5` (Node 20 deprecation), `release.yml` `contents:read` → `write`, `branches: main` → `tags: v*`, version from `VERSION` file (#86).

### Security
- Open redirect & info exposure fixes: `is_safe_url()` / `get_safe_next_url()` for `?next=`, `is_safe_redirect_target()` (only `http/https`, block `javascript:`) for all `redirect()`s, generic error messages for health/metrics/changelog (#78, `8dd52f1`).

---

## [3.0.0] — 2025-06-21

**Upstream caching — fast even when your `go/` is slow.**

### Added
- **Upstream shortcut caching** — `Add cache management, performance optimizations, and admin UI enhancements (#65)` + `feat: add upstream cache management (#64)` — successful upstream lookups cached in SQLite and Redis, `upstream_cache.enabled` toggle.
- **Admin UI for cache** — View, resync one, resync all, purge with confirmations, dark-mode polish.
- **Gunicorn + gevent** — Recommended production run: 4 async workers.

### Changed
- Upstream hits now show same countdown page as local shortcuts.

### Fixed
- Route import and JSON error handling for cache endpoints.

---

## [2.2.0] — 2025-06-21

- `refactor: remove global keyword from get_config_data and save_config_data functions` (`530d0f5`)

---

## [2.0.0] — 2025-06-08

- `config backward comp (#63)` — `49a844b` — backward-compatible config loading
- `Flask migrate (#62)` — `cfe5441` — Alembic migrations `f200f245867a_init` (redirects, upstream_cache, upstream_check_log)

---

## [1.1.0] — 2025-05-23

- Add audit logging (`created_at`, `updated_at`, `created_ip`, `updated_ip`, `access_count`) — `29fd907` and `b117b2b` refactors
- Company-wide install docs in README — `da48998`, `ee30c34`
- Modern Tailwind UI and FontAwesome — `a24f54c` HTML structure

---

## [1.0.0] — 2025-05-20

- Initial Flask app: static (`r/docs`) and dynamic (`r/jira/{ticket}`) shortcuts, SQLite + web UI, Docker + Redis + upstream fallback, admin login, import/export, version page — `a24f54c` and earlier.

---

*Full log: `git log --oneline --all` · Releases: `https://github.com/authoritydmc/redirector/releases` · Version file: `https://raw.githubusercontent.com/authoritydmc/redirector/main/VERSION`*
