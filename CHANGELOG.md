# Changelog

All notable changes to **Redirector** — your team's `go/` links — are documented here.  
We follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

> **For everyone:** This page highlights what changed for you — faster shortcuts, fewer clicks, and more secure team sharing. Technical details live in GitHub releases.

---

## [Unreleased]

### Added
- **Starter Pack — one click, 14 shortcuts:** New teams see a welcome banner on an empty dashboard. Install curated shortcuts like `google`, `eng/docs`, `hr/handbook`, `jira/{ticket}`, and `my-prs/[username]` in one click, or pick individually at **Admin → Setup**. Perfect for onboarding a new workspace in 30 seconds.
- **Find anything, instantly:** Dashboard search now filters by shortcut name, destination, and tags as you type — plus server-side search for large workspaces (`r/docs` finds `eng/docs` too).
- **QR codes for every link:** Open `r/qr/<pattern>` or call `GET /api/qr/<pattern>` to get a QR for your short link — great for posters, onboarding decks, and meeting rooms.
- **Did you mean?** Typing a missing link now shows up to 3 close matches instead of a blank "create" page. Fewer dead ends, faster discovery.
- **Private & expiring links:** Create unlisted or private shortcuts and set an expiry date. Private links require admin login; expired links show a clear 410 message.

### Fixed
- Faster Docker start on Windows and cleaner first-time database setup.

---

## [3.1.0] — 2026-08-28

**Bring your whole company on `r/` — teams, SSO, and personal shortcuts, done right.**

### Added
- **Team shortcuts that just work:** Create hierarchical links like `r/eng/docs`, `r/eng/runbook`, `r/hr/handbook`, and `r/design/system` as distinct shortcuts. No more collisions between teams — `r/eng/docs` and `r/hr/docs` live happily side by side.
- **One-time setup, remembered for you:** For links that need your info (like `r/my-prs/[username]`), we'll ask once, save it securely in *your browser only* (never on the server), and reuse it next time. Clear it anytime with one click.
- **Smarter handling of company logins (SSO):** Links that require Google, Microsoft, or Okta login are never cached. You'll always land on the real login page — never a stale copy — and we automatically clear any old cached login pages on startup. Includes `Cache-Control: no-store` and `X-SSO-Link` for SSO links.
- **Company-wide security, out of the box:** Added two-factor login for admins — scan a QR with Google Authenticator/Authy/1Password or use a passkey (Face ID, Touch ID, Windows Hello) plus one-time backup codes. Find it under **Admin → MFA / Passkeys**.
- **See what's new, without leaving the app:** A beautiful **Changelog** page at `r/changelog` (and `GET /api/changelog`) shows every update with version, date, and badges — just like your favorite SaaS product.

### Changed
- **Versioning you can trust:** The version now comes from a `VERSION` file (`3.1.0`) plus build info (`3.1.0+5.gabcdef`). Bump it with `python get_version.py --bump minor`. No more guessing what you're running.
- **Stay up to date, once a day:** The footer checks GitHub for updates only once per 24 hours (cached in your browser) and shows a gentle toast when a new version is available — no spam, just nudge when it matters.

### Fixed
- **More secure automation:** Fixed a GitHub Actions workflow that could have allowed untrusted text to run as a shell command. Now uses a safe quoted variable.

**For admins & IT:**
- Health checks at `r/health` / `r/ready` and metrics at `r/api/metrics` for Kubernetes and Prometheus.
- Upstream cache now has a per-upstream “Skip SSO cache” toggle (on by default) and correctly handles `resync` without caching login pages.

---

## [3.0.0] — 2025-06-21

**Faster everywhere — especially for teams using an upstream `go/` service.**

### Added
- **Upstream caching:** Links found on your upstream `go/` service (like `go/ticket`) are now cached in SQLite and Redis — next time, they redirect instantly, even if the upstream is slow.
- **Control your cache:** Turn caching on or off in `data/redirect.config.json` → `upstream_cache.enabled` (on by default).
- **Manage it visually:** New **Admin → Upstream Cache** page to view, resync one, resync all, or purge entries — with confirmations and dark-mode polish.
- **Production ready:** Recommended run is now via Gunicorn + gevent (4 workers, async) — handles many concurrent team clicks without breaking a sweat.

### Changed
- Upstream hits now show the same countdown page as local shortcuts (including delay and stats) — consistent experience everywhere.

### Fixed
- More reliable error messages and JSON responses for all cache actions.

---

## [1.1.0] — 2025-05-23

**Built for companies — not just a personal shortener.**

### Added
- See who used what and when: creation time, last update, and access count on every shortcut.
- Step-by-step company install guide in the README (DNS for `r/`, reverse proxy examples, Docker volumes).

### Changed
- Fresh coat of paint: Tailwind + FontAwesome, fully responsive and dark-mode ready.
- Safer defaults: config lives in `data/redirect.config.json` (auto-created, random admin password shown once), database at `data/redirects.db`.

### Fixed
- More reliable tests on Windows, macOS, and Linux.

---

## [1.0.0] — 2025-05-20

**Hello, world — your team's memorable shortcuts.**

- Create **static** shortcuts (`r/docs` → `https://docs.google.com/...`) and **dynamic** ones (`r/jira/{ticket}` → `https://jira.company.com/{ticket}`) in seconds.
- Self-hosted with Docker (or plain Python), optional Redis for speed, and upstream fallback so you never lose a link.
- Simple admin login, import/export, and a version page that shows your live URLs — all ready for `r/` on your office network.

---

*Questions?* Open an issue on [GitHub](https://github.com/authoritydmc/redirector) or see the `/tutorial` inside the app.
