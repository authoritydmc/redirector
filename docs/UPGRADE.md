# Upgrade Guide — Redirector

Every command below is given for **Linux/macOS (sh)**, **Windows (PowerShell)**,
**Docker** and **bare-metal Python**. Copy the block that matches your setup.

> **Current:** `3.1.1` — see [CHANGELOG.md](../CHANGELOG.md) and
> [Releases](https://github.com/authoritydmc/redirector/releases).

---

## 0. The 30-second version

1. **Back up** — `/admin/backup` → *Create backup*, or the CLI command below.
2. **Upgrade** — `docker compose pull && docker compose up -d` (or
   `git pull` + restart for bare metal).
3. **Verify** — `curl http://localhost/api/health/state`.
4. **If it broke** — restore the archive. Do **not** run `flask db downgrade`.

Nothing else is required. Migrations run automatically on start, and a snapshot
is taken automatically first if any migration is pending.

---

## 1. Before you upgrade

### 1a. Back up

**In the UI** (recommended): `http://localhost/admin/backup` → *Create backup*.
The archive lands in `data/backups/` and is downloadable from the same page.

**From the command line** (works whether or not the app is running — the
snapshot uses SQLite's online backup API, so it is safe on a live database):

```sh
# Docker, Linux / macOS
docker compose exec app python -m app.utils.backup create --label "before upgrade"
```

```powershell
# Docker, Windows (PowerShell)
docker compose exec app python -m app.utils.backup create --label "before upgrade"
```

```sh
# Bare metal, Linux / macOS
.venv/bin/python -m app.utils.backup create --label "before upgrade"
```

```powershell
# Bare metal, Windows (PowerShell)
.\.venv\Scripts\python.exe -m app.utils.backup create --label "before upgrade"
```

Copy at least one archive **off the host**. An archive sitting in the same
directory as the data protects you from a bad upgrade, not from a lost disk.

### 1b. Note what you are running

```sh
curl http://localhost/system-info
curl http://localhost/api/data-dir
```

Keep the version string. If you need to roll back, you need the exact tag.

---

## 2. Docker Compose — the common case

### Linux / macOS

```sh
cd /path/to/redirector
docker compose pull                    # prebuilt image
#   ...or, if you build from source:
git pull
docker compose build --no-cache
docker compose up -d
docker compose logs -f --tail=50 app   # watch migrations run
docker compose ps
```

### Windows (PowerShell)

```powershell
cd C:\path\to\redirector
docker compose pull
#   ...or, if you build from source:
git pull
docker compose build --no-cache
docker compose up -d
docker compose logs -f --tail=50 app
docker compose ps
```

### After either

```sh
curl http://localhost/health
curl http://localhost/api/health/state
```

Your data is kept because `./data` is bind-mounted to `/app/data` in
`docker-compose.yml`. **Do not delete `data/`, and do not run
`docker compose down -v`.** The `-v` flag deletes named volumes; see
[DATA-PERSISTENCE.md](DATA-PERSISTENCE.md).

---

## 3. Plain Docker (no compose)

### Linux / macOS

```sh
docker pull rajlabs/redirector:latest
docker rm -f redirector
docker run -d \
  --name redirector \
  --restart unless-stopped \
  -p 80:80 \
  -v "$(pwd)/data:/app/data" \
  -e REDIRECTOR_DATA_DIR=/app/data \
  rajlabs/redirector:latest
```

### Windows (PowerShell)

```powershell
docker pull rajlabs/redirector:latest
docker rm -f redirector
docker run -d --name redirector --restart unless-stopped -p 80:80 `
  -v "${PWD}\data:/app/data" `
  -e REDIRECTOR_DATA_DIR=/app/data `
  rajlabs/redirector:latest
```

The `-v "$(pwd)/data:/app/data"` is the part that matters. Omit it and you get a
brand new empty install with a new admin password — and your old data is still on
disk, unharmed, in the folder you forgot to mount.

With Redis, add `-e REDIS_HOST=redis --link redis:redis`.

---

## 4. Bare metal (venv + gunicorn / wsgi)

Stop the service before migrating, so nothing is writing while the schema moves.

### Linux / macOS

```sh
sudo systemctl stop redirector        # or: pkill -f gunicorn
cd /path/to/redirector
git fetch --tags
git checkout v3.1.1                   # or: git pull origin main

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.utils.backup create --label "before upgrade"
FLASK_APP=wsgi:app .venv/bin/flask db upgrade
sudo systemctl start redirector
```

`FLASK_APP=wsgi:app` is what tells the migration CLI which application to
open. The entrypoint sets it for you; set it yourself when running migrations by
hand, or the command can only find the app if it happens to run from the project
directory.

### Windows (Service)

```powershell
Stop-Service Redirector
cd C:\path\to\redirector
git fetch --tags
git checkout v3.1.1                   # or: git pull origin main

.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.utils.backup create --label "before upgrade"
$env:FLASK_APP = "wsgi:app"
.\.venv\Scripts\flask.exe db upgrade
Start-Service Redirector
```

### Windows (foreground, for testing)

```powershell
.\.venv\Scripts\python.exe wsgi.py
```

### macOS (launchd)

```sh
launchctl stop com.authoritydmc.redirector
cd /path/to/redirector
git fetch --tags && git checkout v3.1.1
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
FLASK_APP=wsgi:app .venv/bin/flask db upgrade
launchctl start com.authoritydmc.redirector
```

---

## 5. What the entrypoint does on every start

`entrypoint.sh` is the part that makes upgrades safe, and it runs in this order
for a reason:

1. **Check the data directory** is present and writable. Fail loudly. A volume
   that silently became read-only is a common cause of "the app is up but my
   redirects are gone" — actually, of *saving* failing, which is worse.
2. **Apply a staged restore**, if one is waiting in `.restore-pending.zip`.
3. **Refuse to run an old image against newer data.** If the database's schema
   revision is not in this build's migration chain, the container exits with an
   explanation rather than downgrading your schema.
4. **Snapshot, but only when a migration is actually pending.** Backing up on
   every restart fills the volume with identical copies and teaches people to
   ignore the backup directory.
5. **Migrate, with bounded retries.** The old loop was
   `until flask db upgrade; do sleep 2; done`, which retried forever: a genuinely
   broken migration left a container that looked alive but never served traffic.
   It now fails after `REDIRECTOR_MIGRATE_ATTEMPTS` (default 5) and prints where
   to find the rollback snapshot.
6. **Start gunicorn.**

Useful overrides:

| Variable | Effect |
|---|---|
| `REDIRECTOR_DATA_DIR` | Where the data directory is. Default `/app/data` in Docker, `./data` otherwise. |
| `REDIRECTOR_MIGRATE_ATTEMPTS` | Migration retry count. Default `5`. |
| `REDIRECTOR_SKIP_MIGRATIONS=1` | Start without migrating. For triage only — the app may not match the schema. |
| `REDIRECTOR_APP_VERSION` | Version reported by `/system-info`. Set at build time by the Dockerfile. |

---

## 6. Verify after upgrading

```sh
curl http://localhost/health              # {"status":"ok","version":"3.1.1+..."}
curl http://localhost/api/health/state    # data dir writable, schema revision, pending migrations
curl http://localhost/api/latest-version  # what is published
```

```powershell
curl.exe http://localhost/health
curl.exe http://localhost/api/health/state
```

In the UI, check **`/system-info`** — the banner shows *Update available* when a
newer tag exists — and **`/admin/backup`** for the schema state and your archive
inventory.

Then confirm a shortcut actually resolves, because that is the thing you care
about:

```sh
curl -sI http://localhost/health-test | head -1   # expect: HTTP/1.1 302
```

---

## 7. Rollback

**Roll back by restoring a backup, not by downgrading the schema.**
`flask db downgrade` inverts a migration and can drop columns that hold your data.
It is not a rollback strategy.

### Step 1 — pick the version

```sh
docker compose logs --tail=80 app
```

Look for the `[redirector] FATAL:` line. It names the specific problem: a
migration that failed, or a schema newer than the image supports.

### Step 2 — start the previous version (data untouched)

```sh
# Docker Compose
docker compose down
sed -i 's|image: rajlabs/redirector:latest|image: rajlabs/redirector:3.1.0|' docker-compose.yml
docker compose up -d
```

```powershell
# Docker Compose, Windows
docker compose down
(Get-Content docker-compose.yml) -replace 'rajlabs/redirector:latest', 'rajlabs/redirector:3.1.0' | Set-Content docker-compose.yml
docker compose up -d
```

If the container refused to start because the data is *newer* than the image, the
previous image is the right one — the refusal is the guard working.

### Step 3 — if the data itself needs restoring

```sh
# Linux / macOS
docker compose down
docker compose run --rm --no-deps app \
  python -m app.utils.backup restore /app/data/backups/<name>.zip
docker compose up -d
```

```powershell
# Windows (PowerShell)
docker compose down
docker compose run --rm --no-deps app `
  python -m app.utils.backup restore /app/data/backups/<name>.zip
docker compose up -d
```

The app is stopped, so the restore is applied immediately. It takes a safety
snapshot of the current state first, so this step is itself reversible.

Then migrations bring the restored database forward to whatever the running
version expects.

---

## 8. If things go wrong

| Symptom | Cause | Fix |
|---|---|---|
| New admin password after upgrade | `/app/data` is not the folder you think — usually a named volume replaced a bind mount | See [DATA-PERSISTENCE.md](DATA-PERSISTENCE.md) §4; your data is usually still in `./data` |
| `FATAL: the database schema (X) is NEWER than this image supports` | Image older than the data | `docker compose pull && docker compose up -d`, or restore a backup taken with the older version |
| `FATAL: database migration failed after 5 attempts` | A migration is broken, or the volume is read-only | `docker compose logs app`; check volume permissions; restore the `pre-upgrade` snapshot from `data/backups/` |
| Container restarts forever, no traffic | A previous version of the entrypoint's infinite retry loop | Upgrade to an image with bounded retries; check the logs for the real error |
| `/r/` stopped working | The hosts entry was lost | `127.0.0.1 r` — see `/enable-r-instructions` and `GET /api/r-status` |
| `no such table: upstream_cache` in the logs | Migrations have not run yet | Check the entrypoint output; run `FLASK_APP=wsgi:app flask db upgrade` manually if the app was started outside the entrypoint |

---

## 9. FAQ

**Do I need to recreate the database?** No. Migrations add columns; they do not
recreate tables.

**Will my shortcuts stay?** Yes, as long as `/app/data` still points at the same
host folder. The database path in the config is stored relative so that moving
the folder between machines works.

**Do I need `flask db migrate`?** No, and you should not run it against a
production install — it autogenerates a migration from whatever drift the local
models have. Upgrades apply existing migrations with `flask db upgrade`.

**Is the admin password in the backup archive?** Yes, hashed, alongside your MFA
seeds. Treat archives as secrets: anyone with one can read the config.

**How do I know an update is available?** Every page checks
`GET /api/latest-version` once a day (cached in `localStorage`); `/system-info`
shows a persistent banner when a newer tag exists.

---

*Need help?* Open an issue at
`https://github.com/authoritydmc/redirector/issues` and include
`/admin/backup/state.json`, the output of `curl http://localhost/api/health/state`,
and `docker compose logs app`.
