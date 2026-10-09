# Redirector

A modern, self-hostable URL shortener and redirector: **FastAPI** backend,
**React** SPA, background workers over Redis, SQLite by default with a
Postgres path for scale. Docker-first, one data directory, zero manual
migration steps.

> **Coming from v2?** The legacy Flask app was removed in M4. Your data
> migrates untouched: [`docs/UPGRADE-v3.md`](docs/UPGRADE-v3.md) (doctor
> gate, one-shot `import-v2`, rollback runbook). Secrets never migrate —
> set a fresh admin password and re-enroll MFA at cutover.

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
  - [Docker Compose (Recommended)](#docker-compose-recommended)
  - [Postgres Variant](#postgres-variant)
  - [Local Dev (Backend + SPA)](#local-dev-backend--spa)
- [Configuration](#configuration)
- [Hostname Setup for r/ Shortcuts](#hostname-setup-for-r-shortcuts)
- [Company-Wide Installation & Team Usage](#company-wide-installation--team-usage)
- [Data & Backups](#data--backups)
- [Reverse Proxy Notes](#reverse-proxy-notes)
- [API Reference](#api-reference)
- [Development & Testing](#development--testing)
- [Project Structure](#project-structure)
- [Version & Credits](#version--credits)
- [License](#license)

---

## Features

- **FastAPI + async SQLAlchemy**: typed routers, OpenAPI at `/docs`, RFC 7807 errors.
- **React SPA** served at `/app` (typechecked, unit + Playwright tested).
- **Redirect hot path** with static / dynamic / user-dynamic shortcuts, countdown or instant 302, access counting.
- **Upstream shortcut fan-out** with cache, resync/purge, SSE check streams and background jobs (in-process or arq/Redis).
- **Auth**: admin password + JWT, TOTP MFA with backup codes, API keys (`rk_*`), IP lockout, audit events.
- **Backups**: labeled `.zip` archives (SQLite online snapshot + manifest) via UI or CLI, staged restores.
- **Migrate-on-boot**: Alembic upgrade runs in a dedicated `migrate` service before the API starts.
- **Nightly soak**: Postgres + Redis smoke with zero-failure gate (see `load_testing/`).

---

## Quick Start

### Docker Compose (Recommended)

```sh
REDIRECTOR_ADMIN_PASSWORD=... REDIRECTOR_JWT_SECRET=... \
  docker compose -f docker/compose.prod.yml up -d --build
```

- Stack: `web` (SPA + proxy, `:80`) → `api` (uvicorn) + `worker` (arq) + `redis`.
- Data lives in `./data` (bind mount — never change where it points; see
  [`docs/DATA-PERSISTENCE.md`](docs/DATA-PERSISTENCE.md)).
- Compose fails fast without both secrets. Visit `http://localhost/app`,
  docs at `http://localhost:80/docs` (proxied), health at `/healthz`.

### Postgres Variant

```sh
REDIRECTOR_ADMIN_PASSWORD=... REDIRECTOR_JWT_SECRET=... POSTGRES_PASSWORD=... \
  docker compose -f docker/compose.prod.yml -f docker/compose.postgres.yml up -d --build
```

Same services, database on managed Postgres. Required for `--scale api=N`
(rate limits and in-process caches are per-process; JWTs and job rows are
already shared-safe).

### Local Dev (Backend + SPA)

```sh
python -m venv .venv
.venv/Scripts/Activate.ps1            # Windows; `source .venv/bin/activate` on POSIX
pip install -r backend/requirements.txt
export REDIRECTOR_DATABASE_URL="sqlite+aiosqlite:///./data/v3.db"
export REDIRECTOR_AUTO_REDIRECT_DELAY=0   # instant 302s
uvicorn backend.main:app --reload --port 8123   # docs: 127.0.0.1:8123/docs
```

```sh
cd frontend && npm install && npm run dev   # http://localhost:5173, /api proxied to :8123
npm run typecheck && npm test && npm run build
```

Full dev guide (env knobs, workers, WSL docker): [`DEVELOPMENT.md`](DEVELOPMENT.md),
[`backend/README.md`](backend/README.md).

---

## Configuration

Everything is env-first (`REDIRECTOR_*`, see
[`backend/README.md`](backend/README.md) for the full table). The knobs most
deployments touch:

| Variable | Default | Notes |
|---|---|---|
| `REDIRECTOR_ADMIN_PASSWORD` | `admin` | **Required in compose; change it** |
| `REDIRECTOR_JWT_SECRET` | insecure dev default | **Required in compose; change it** |
| `REDIRECTOR_DATABASE_URL` | `sqlite+aiosqlite:///./data/redirect.db` | `postgresql+asyncpg://…` for Postgres |
| `REDIRECTOR_REDIS_URL` | `redis://localhost:6379/0` | Broker + shared cache |
| `REDIRECTOR_CACHE_BACKEND` | `memory` | Or `redis` (shared across replicas) |
| `REDIRECTOR_JOB_BACKEND` | `in-process` | Or `arq` (needs the worker) |
| `REDIRECTOR_AUTO_REDIRECT_DELAY` | `1` | Seconds; `0` = instant 302 |
| `REDIRECTOR_DATA_DIR` | `./data` (`/app/data` in Docker) | The one directory that matters |

---

## Hostname Setup for r/ Shortcuts

### Quick Hostname Setup (Recommended)

To use URLs like `http://r/google` on your local machine, add `r` to your hosts file. Use the provided script for your OS (no need for full autostart or Docker restart):

- **Windows:**
  - Run in PowerShell as Administrator:
    ```powershell
    ./scripts/add-r-host-windows.ps1
    ```
- **macOS:**
  - Run in Terminal:
    ```sh
    bash scripts/add-r-host-macos.sh
    ```
- **Linux:**
  - Run in Terminal:
    ```sh
    bash scripts/add-r-host-linux.sh
    ```

Each script will attempt to add `127.0.0.1   r` to your hosts file if you have the necessary privileges, or print instructions if not.

### Manual Hostname Setup

If you prefer to edit your hosts file manually:

- **Windows:**
  1. Open Notepad as Administrator.
  2. Open the file: `C:\Windows\System32\drivers\etc\hosts`
  3. Add this line at the end:
     ```
     127.0.0.1   r
     ```
  4. Save the file. Now you can use `http://r/shortcut` in your browser.

- **Linux/macOS:**
  1. Edit `/etc/hosts` with sudo:
     ```sh
     sudo nano /etc/hosts
     ```
  2. Add this line at the end:
     ```
     127.0.0.1   r
     ```
  3. Save and close. Now you can use `http://r/shortcut` in your browser.

> **Note:** On first run, if you have just set up the `r` hostname (via hosts file or DNS), make sure to access `http://r/` (not just `r/`) in your browser at least once. This ensures your browser recognizes `r` as a valid domain and flushes any old cache or search behavior.

---

## Company-Wide Installation & Team Usage

To make `r/` shortcuts available to your entire team or company:

1. **Deploy the app on a central server** (on-prem or cloud VM/container).
   - Use a static IP or DNS name (e.g., `r.company.com`).
   - The `web` service already terminates on `:80`; put your TLS proxy in front for `:443`.
2. **Configure DNS:**
   - Set up an internal DNS record so `r` (or `r.company.com`) points to the server's IP.
   - Your IT team can add a DNS A record for `r` in your internal DNS system.
   - All users on the network will be able to use `http://r/shortcut` or `http://r.company.com/shortcut`.
3. **(Optional) Use hosts file for small teams:**
   - Each user can add the server's IP and `r` to their hosts file as above.
4. **Secure the admin interface:**
   - Use a strong admin password and JWT secret (compose requires both).
   - Optionally, restrict admin access by IP or VPN.
5. **Share the base URL:**
   - Tell your team to use `http://r/shortcut` for all shared links.

This setup allows everyone in your organization to use simple, memorable shortcuts like `r/google` or `r/docs` from any device on the network.

> **Full walkthrough:** [`docs/COMPANY-DNS-SETUP.md`](docs/COMPANY-DNS-SETUP.md) covers both layers step by step —
> public Cloud DNS records (`r.company.com` on Cloudflare / Route 53 / Azure DNS / Google Cloud DNS / GoDaddy),
> office router / Pi-hole / AD DNS overrides so bare `http://r/` resolves LAN-wide with no per-laptop hosts edits,
> the `company.com` search-domain trick, HTTPS notes (`https://r/` needs a private CA; use `https://r.company.com/` publicly), and a troubleshooting table.

---

## Data & Backups

Everything that matters is in **one directory** (`./data` locally, `/app/data`
in Docker): the database, `backups/*.zip`, and install state. Keep the bind
mount on every start. Full reference (moves, named volumes, what-not-to-touch):
[`docs/DATA-PERSISTENCE.md`](docs/DATA-PERSISTENCE.md). Upgrade + rollback:
[`docs/UPGRADE-v3.md`](docs/UPGRADE-v3.md).

```sh
# Labeled backup (safe on a live database)
docker compose -f docker/compose.prod.yml exec api \
  python -m backend.cli.backup create --label "before change"
```

---

## Reverse Proxy Notes

`web` (nginx) already proxies `/api/*`, probes, the hot path and SSE (with
buffering off) to `api`, and serves the SPA. If you terminate TLS upstream
(Caddy, Traefik, cloud LB), forward to `web:80` and set
`X-Forwarded-Proto`. Sample configs: [`deploy/examples/`](deploy/examples/).

---

## API Reference

Interactive docs at `/docs`; snapshot at [`docs/openapi.json`](docs/openapi.json)
(checked by CI — regenerate via `python scripts/export-openapi.py` after
router changes).

| Router | Prefix | Notes |
|---|---|---|
| `health` | `/healthz`, `/health`, `/readyz` | No auth, no DB |
| `shortcuts` | `/api/v1/shortcuts` | CRUD + `POST /bulk-delete`, `GET /{pattern}` details |
| `upstreams` | `/api/v1/upstreams` | CRUD, `/cache`, `/check-logs`, `/check/stream` (SSE) |
| `resolve` | `/api/v1/resolve`, `/{pattern}` | Debug API + hot path (catch-all, registered last) |
| `jobs` | `/api/v1/jobs` | Enqueue/list/status/cancel + `/events` SSE |
| `auth` | `/api/v1/auth` | `POST /login`, `GET /me`, `/api-keys`, `/mfa/*` |
| `config` | `/api/v1/admin/config` | Admin JWT, DB-backed settings |
| `backup` | `/api/v1/admin/backup` | Enqueue/list/download/delete/restore |
| `metrics` | `/api/v1/metrics` | `/kpi`, `/live` |
| `qr` | `/qr/{pattern}`, `/api/v1/qr` | PNG or base64 JSON |

Auth: `Authorization: Bearer` with a JWT (login) or `rk_*` API key; failures
are 401, scope denials 403 with stable `code`s (see
[`docs/error-codes.md`](docs/error-codes.md)).

---

## Development & Testing

```sh
python -m pytest tests/ -v            # full suite (single process post-M4)
python -m ruff check backend/ && python -m mypy backend/   # strict gates
flake8 backend/ tests/ --select=E9,F63,F7,F82
cd frontend && npm run typecheck && npm test
```

Load testing (`load_testing/`): locust v3 script + headless smoke
(`load-smoke.sh`, zero-failure CSV gate) + nightly Postgres/Redis soak
(`soak-pg.sh`). See [`load_testing/load_testing.md`](load_testing/load_testing.md).

---

## Project Structure

```
redirector/
├── backend/
│   ├── main.py              # create_app(), lifespan, routers, SPA mount
│   ├── core/                # config (REDIRECTOR_*), security/JWT, async DB, cache, errors
│   ├── models/entities.py   # SQLModel tables
│   ├── modules/             # repository + service + schemas per domain
│   ├── routers/             # thin HTTP layer (/api/v1/*, /{pattern} last)
│   ├── workers/             # arq broker tasks
│   ├── migrations/import_v2.py  # one-shot v2 data importer
│   ├── alembic/             # v3 schema history (env-first URLs)
│   ├── cli/                 # backup + doctor CLIs
│   └── requirements.txt
├── frontend/                # React SPA (served at /app)
├── docker/
│   ├── Dockerfile.api       # non-root uvicorn, migrates on boot
│   ├── Dockerfile.web       # nginx: SPA + proxy
│   ├── compose.prod.yml     # web → api + worker + redis (sqlite)
│   └── compose.postgres.yml # overlay: managed Postgres
├── deploy/examples/         # Caddy + nginx samples
├── docs/                    # persistence, upgrades, DNS setup, API inventory
├── load_testing/            # locust scripts, smoke + soak harnesses
├── scripts/                 # r-hostname setup, release tag, version sync, openapi export
├── tests/                   # pytest suite (fixtures, import, API, resolve, migration)
└── VERSION                  # version source of truth (never hardcoded elsewhere)
```

---

## Version & Credits

- The build version lives in [`VERSION`](VERSION) and is served by the API
  (`/api/v1/metrics/live`, `version` field).
- Created by [@authoritydmc](https://github.com/authoritydmc) and contributors.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

> **For local development, setup, and migration instructions, see [`DEVELOPMENT.md`](DEVELOPMENT.md).**

---

## Automated Release Tagging (Windows)

To automate the process of tagging a new release and triggering the GitHub Actions release workflow, use the provided PowerShell script:

### Usage

1. **Ensure your changes are committed and pushed.**
2. Open PowerShell in the project root directory.
3. Run:

```powershell
pwsh scripts/create-release-tag.ps1 -Version v1.2.3
```

- Replace `v1.2.3` with your desired version tag (must start with `v`).
- If you omit `-Version`, the script will prompt you to enter one interactively.
- The script will:
  - Check if the tag exists
  - Create the tag
  - Push it to `origin`
  - Trigger the GitHub Actions release workflow (if configured)

> **Note:** Tagging is required for the GitHub release workflow to succeed. See [VERSIONING.md](VERSIONING.md) for versioning details.

---
