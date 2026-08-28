# Upgrade Guide — Redirector

Keep your `r/` links fast, secure, and up to date. This guide covers **all** deployment methods.

> **Current:** `3.1.1` — see [CHANGELOG.md](../CHANGELOG.md) and [Releases](https://github.com/authoritydmc/redirector/releases) for what's new.

---

## 1. Before you upgrade

1. **Backup your data** (config + DB live in `data/`):
   ```sh
   # If you use a bind mount (./data on host)
   cp -r data data.backup.$(date +%Y%m%d)
   # If you use a named volume (recommended)
   docker run --rm -v redirector_data:/data -v $(pwd):/backup alpine tar czf /backup/redirector-data-$(date +%Y%m%d).tgz -C / data
   ```
2. **Check the changelog:** `https://github.com/authoritydmc/redirector/blob/main/CHANGELOG.md` or in-app at `r/changelog` / `http://localhost:80/changelog`
3. **Note your version:** Visit `r/system-info` or call `GET /api/latest-version` / `GET /health`

---

## 2. Docker (recommended)

### Docker Compose (most common)

```sh
# from your redirector folder (where docker-compose.yml lives)
docker compose pull        # pulls rajlabs/redirector:latest if you use the image, or
docker compose build --no-cache  # if you build from source (after git pull)
docker compose up -d       # recreates containers, runs migrations automatically (entrypoint.sh does flask db upgrade)
docker compose ps          # should show redirector Up, redis Up
curl http://localhost/health          # {"status":"ok", "version":"3.1.1+..."}
curl http://localhost/api/changelog | head
```

Your data is kept because `data/` is mounted (`./data:/app/data` or named volume `redirector_data:/app/data`). **Do not delete `data/`**.

### Plain Docker (prebuilt image)

```sh
docker pull rajlabs/redirector:latest
docker rm -f redirector
docker run -d --name redirector --restart unless-stopped -p 80:80 -v $(pwd)/data:/app/data rajlabs/redirector:latest
# or with Redis:
docker run -d --name redirector --restart unless-stopped -p 80:80 -v $(pwd)/data:/app/data -e REDIS_HOST=redis --link redis:redis rajlabs/redirector
```

### How upgrades work inside the container

`entrypoint.sh` on every start:

1. `flask db migrate -m "auto migration"` — no-op if no model changes
2. `flask db upgrade` — applies all pending Alembic migrations (e.g., `20250828_enterprise` for tags/visibility)
3. Purges any stale SSO cache and starts `gunicorn -c gunicorn.conf.py` (gevent, 4 workers)

If `flask db upgrade` fails with `KeyError: '...'` or `multiple heads`, check `migrations/versions/` — ensure `down_revision` chain is linear (`f200f245867a` → `20250621b` → `20250828_enterprise`).

---

## 3. Manual (Python)

```sh
git pull origin main
# or fetch a specific tag
git fetch --tags
git checkout v3.1.1

python -m venv .venv
.venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt  # includes pyotp, qrcode[pil] for MFA/QR
flask db upgrade
python wsgi.py  # or gunicorn -c gunicorn.conf.py wsgi:app
# or: gunicorn -w 4 -k gevent -b 0.0.0.0:80 wsgi:app
```

Visit `http://localhost:80/health` and `http://localhost:80/changelog`.

---

## 4. Verify after upgrade

```sh
# 1. Health
curl http://localhost/health
# {"status":"ok","version":"3.1.1+...","checks":{"database":"up","redis":"disabled|up"}}

# 2. Version check (cached 24h in browser, 24h on server)
curl http://localhost/api/latest-version
# {"success":true,"latest":"v3.1.1","current":"3.1.1+...","update_available":false}

# 3. Changelog
curl http://localhost/api/changelog | jq .changelog | head

# 4. Create a test shortcut
curl -X POST http://localhost/edit/ -d "pattern=health-test&target=https://example.com&type=static" -H "Cookie: $(curl -c - http://localhost/admin-login -d "password=YOUR_ADMIN_PWD" | grep -o 'session=.*')"
# or via UI: r/health-test -> https://example.com
```

In the UI, go to **`r/system-info`** — the top banner will show **“Update available”** if a newer tag exists on GitHub, with buttons to **View changelog** and **Releases**.

---

## 5. Rollback

If something breaks, restore your `data/` backup and restart with the previous image/tag:

```sh
# Docker Compose with previous tag
docker run -d -p 80:80 -v $(pwd)/data:/app/data rajlabs/redirector:3.1.0
# or git checkout previous tag
git checkout v3.1.0
flask db downgrade -1  # if you need to revert the last migration
docker compose up -d
```

Migrations are forward-only; downgrading is supported for the last `enterprise` migration but **always backup first**.

---

## 6. FAQ

**Do I need to recreate the database?** No — `flask db upgrade` handles schema changes (new columns like `tags`, `visibility` are added, not recreated).

**Will my shortcuts stay?** Yes — they live in `data/redirect.db` (SQLite) or your external DB (`postgresql://...` / `mysql://...`). The volume/mount keeps them.

**How do I know if I need to update?** The footer on every page checks `GET /api/latest-version` once per day (cached in `localStorage`). `r/system-info` shows a persistent banner when `latest > current` (proper semver, so `3.10.0` > `3.9.0` correctly).

**What if `r/` stops working after update?** Re-apply the hosts entry: `127.0.0.1 r` (see `r/enable-r-instructions` → live `GET /api/r-status` check).

**Where is the full changelog?** In-app at `r/changelog`, on GitHub at `/releases`, and raw at `https://raw.githubusercontent.com/authoritydmc/redirector/main/CHANGELOG.md`.

---

*Need help?* Open an issue: `https://github.com/authoritydmc/redirector/issues` — include your `r/system-info` (Version, Commit, Python, Docker) and `docker logs redirector`.
