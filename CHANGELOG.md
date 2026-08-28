# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Changelog UI at `/changelog` with live parsing of `CHANGELOG.md` (version, date, Added/Fixed/Changed sections), API at `/api/changelog`
- Version management via `VERSION` file + `get_version.py` (like Tax_Scripts) — `python get_version.py --bump patch|minor|major`
- Health & readiness probes at `/health` and `/ready` (DB + Redis checks) and Prometheus metrics at `/api/metrics`
- Dashboard search & filter (client-side) for pattern/target/tags

### Changed
- Migrated version logic to read `VERSION` file + git build suffix (`3.1.0+5.gabcdef`) instead of hardcoded `3.0.0`

## [3.1.0] - 2026-08-28
### Added
- **SSO never cached** — `is_sso_url()` detection for `accounts.google.com`, `login.microsoftonline.com`, `okta.com`, etc.; `should_cache_upstream_result()` always returns False for SSO; `purge_sso_upstream_cache()` on startup clears stale SSO entries from DB and Redis; shortcut Redis hydration skipped for SSO targets; `Cache-Control: no-store` headers for SSO redirects (`X-SSO-Link: true`) and security headers middleware (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`)
- **Hierarchical shortcuts** — `get_shortcut_for_path()` longest-prefix matcher enables `x/abc`, `x/def`, `y/h/k` as distinct patterns; `handle_redirect` uses hierarchical resolver with static-exact vs dynamic-prefix semantics; `sanitize_pattern()` normalizes hierarchical paths
- **User-dynamic UX** — `dynamic_shortcut_usage.html` now interactive: shows per-param descriptions, localStorage persistence (`user_param_<pattern>_<name>`), Clear/Go buttons, and multiple-param handling; `create_shortcut.html`/`edit_shortcut.html` dynamically generate `param_desc_<name>` inputs when target contains `[var]`
- **Upstream SSO awareness** — `stream_check_upstreams` detects SSO before cache and redirects to original `check_url` (so browser handles SSO login) instead of caching login page; `admin_upstreams` adds `skip_sso_cache` per-upstream toggle (default True); `resync` and `resync-all` skip SSO caching with warning payload (`sso: true`)
- **Enterprise roadmap** — 9 GitHub issues created for company-wide deployment: SSO/OIDC + RBAC, team namespaces, discovery (search/tags/aliases), analytics, browser/Slack/webhooks, private/team-scoped ACLs, lifecycle (expiry/archival), ops (health/metrics/CLI), QR/multi-links (see #69-#77)

### Fixed
- Security: `/.github/workflows/summary.yml` command injection — `gh issue comment "$ISSUE_NUMBER" --body "$RESPONSE"` with env `RESPONSE` instead of inline `'${{ steps.inference.outputs.response }}'` (reported in #67)
- `cache_upstream_result()` now accepts optional `checked_at` param (fixes 4-arg call in resync handlers)
- Import/creation allows hierarchical patterns with slashes (previously `destructureSubPath` only used first segment)

### Changed
- `get_shortcut()` Redis path purges SSO shortcut entries immediately; `get_cached_upstream_result*` purges SSO upstream cache entries on read
- `app/__init__.py` runs `purge_sso_upstream_cache()` on startup and adds security headers middleware

## [3.0.0] - 2025-06-21
### Added
- Upstream shortcut caching: successful upstream lookups cached in SQLite and Redis (if enabled)
- Configurable upstream cache via `redirect.config.json` (`"upstream_cache": {"enabled": true}`), default enabled
- Admin UI for viewing, resyncing, and purging upstream cache entries
- "Resync All" and "Purge All" actions for upstream cache
- Gunicorn support for production
- Improved error diagnostics for cache and upstream operations

### Changed
- Redirect route uses upstream cache hits with same delay/template logic as local hits
- Upstream and cache admin pages redesigned for mobile + dark mode

### Fixed
- Route import and endpoint registration for upstream cache management
- Robust JSON error handling for all cache/upstream endpoints

## [1.1.0] - 2025-05-23
### Added
- Audit logging: `created_at`, `updated_at`, `created_ip`, `updated_ip` per shortcut
- Access count display on dashboard and edit page
- Company-wide installation docs in README
- Modern Tailwind UI and FontAwesome icons

### Changed
- Config moved to `data/redirect.config.json` with secure auto-generated admin password
- Database path `data/redirects.db` with auto-creation for Docker
- Docker instructions use `rajlabs/redirector` image

### Fixed
- `__version__` import in version route
- Test teardown and cross-platform issues

## [1.0.0] - 2025-05-20
### Added
- Initial Flask URL shortener/redirector with static and dynamic shortcuts, SQLite + web UI
- Docker support, Redis in-memory cache, upstream fallback checks
- Admin login, import/export, version page, and audit logging

