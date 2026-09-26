# Data & Persistence — how Redirector stores everything, and what must survive an upgrade

This is the reference for the one question that decides whether an upgrade is
boring or destructive: **where does my data live, and what do I copy to keep it?**

---

## 1. The short version

Everything that matters is in **one directory**:

```
data/
├── redirect.db               # SQLite: shortcuts, upstreams, users, counters
├── redirect.config.json      # settings, admin password hash, session secret, MFA
├── redirect.config.json.bak  # previous config, kept automatically
├── .redirector-state.json    # install state: versions, schema revision, timestamps
├── backups/                  # .zip archives written by /admin/backup and the entrypoint
└── .restore-pending.zip      # present only between staging a restore and restarting
```

Inside Docker that directory is mounted at `/app/data`. Outside Docker it is
whatever `REDIRECTOR_DATA_DIR` points at.

**To move or protect an install, copy that one directory.** Nothing else in the
container or the checkout is worth keeping.

---

## 2. What lives where, and why it matters

| File | Contents | Lost if deleted? |
|---|---|---|
| `redirect.db` | All shortcuts (`redirects`), custom params (`user_params`), upstreams, check logs, access counters | **Unrecoverable** unless you have a backup |
| `redirect.config.json` | Admin password, MFA TOTP seeds, session secret, hostnames, rate limits, log settings | You are locked out; MFA must be re-enrolled |
| `.redirector-state.json` | Recorded app version, schema revision, last backup/migration | Regenerated; only affects reporting, not data |
| `backups/*.zip` | Point-in-time copies of all of the above | You lose your rollback options |
| Redis | Response cache only | A slow first request, nothing more |

Redis holds no authoritative data. If you are unsure whether something matters,
it is in the `data/` directory.

---

## 3. The rule for Docker

`/app/data` must be the **same host location on every start**. The default
`docker-compose.yml` uses a bind mount — `./data:/app/data` — for that reason:
it is what existing installs already have, so `git pull` followed by
`docker compose up -d` cannot point the app at an empty volume and look like an
upgrade deleted everything.

### Commands that are always safe

```sh
docker compose pull
docker compose up -d
docker compose restart app
docker compose down
```

`down` stops containers; it does not touch a bind-mounted host directory.

### Commands that destroy data

```sh
docker compose down -v        # -v DELETES named volumes, including data
docker volume rm redirector_data
rm -rf data/                  # or data\ on Windows
```

If you ever run one of these, the data is gone unless a backup archive survives
somewhere else. That is what `backups/` is for — keep an archive off the host.

---

## 4. Using a named volume instead

`docker-compose.volumes.yml` switches `/app/data` to a named volume. That is a
**different storage location**, so switching without copying your data across is
the exact scenario that makes an upgrade look like data loss: the container
starts against an empty volume, generates a new admin password, and your data is
still sitting in `./data` untouched.

On an **existing** install, migrate properly:

```sh
# Linux / macOS
docker compose down
docker volume create redirector_data
docker run --rm \
  -v "$(pwd)/data:/from" \
  -v redirector_data:/to \
  alpine sh -c "cp -a /from/. /to/"
docker compose -f docker-compose.volumes.yml up -d
curl http://localhost/api/health/state    # confirm it sees your data
# once satisfied:
rm -rf data
```

```powershell
# Windows (PowerShell)
docker compose down
docker volume create redirector_data
docker run --rm `
  -v "${PWD}\data:/from" `
  -v redirector_data:/to `
  alpine sh -c "cp -a /from/. /to/"
docker compose -f docker-compose.volumes.yml up -d
curl.exe http://localhost/api/health/state
# once satisfied:
Remove-Item -Recurse -Force data
```

To go the other way (named volume → bind mount) the copy runs the other way
around: mount `redirector_data:/from` and `./data:/to`.

To move to a **different machine**, copy the `data/` directory over. Nothing
inside it refers to the old absolute path — see the next section.

---

## 5. Why the database path in the config is relative

`redirect.config.json` stores:

```json
"database": "sqlite:///redirect.db"
```

Not `sqlite:////home/alice/redirector/data/redirect.db`. That is deliberate.

SQLAlchemy anchors a relative SQLite path to the **current working directory**,
not to the data directory. A config containing an absolute path breaks the moment
the checkout moves, the container is remounted elsewhere, or you switch between
Docker and a bare-metal run — and the failure is nasty, because SQLite happily
creates a **new, empty** database at the new location while your real one sits
untouched in the old place. The app looks like it lost everything.

So the stored value is a portable name, and `app/utils/paths.py` resolves it at
startup:

1. If the stored path exists, use it.
2. If it points somewhere that no longer exists, look for a file of the same name
   in the data directory and **rewrite the config to the portable form**.
3. If the file is genuinely absent, point at the data directory and let SQLite
   create it — which is correct for a fresh install.

Step 2 is what makes moving `data/` between machines work. Check what it decided:

```sh
curl http://localhost/api/data-dir
```

```json
{
  "success": true,
  "data_dir": "/app/data",
  "database_file": "/app/data/redirect.db",
  "backups_dir": "/app/data/backups",
  "backup_count": 3,
  "restore_pending": false,
  "in_docker": true
}
```

External engines (`postgresql://…`, `mysql+pymysql://…`) are passed through
untouched — those have no path to re-anchor, and they are not covered by the
built-in backup tool.

---

## 6. Configuration writes are atomic

Every write to `redirect.config.json` goes through `Config.save()`, which writes
to a temporary file in the same directory, `fsync`s it, and renames it over the
target. The previous copy is kept as `redirect.config.json.bak`.

This matters because a half-written config loses the admin password, and an
interrupted write during an upgrade is exactly when that would happen. The same
pattern protects the SQLite snapshot and the state file.

---

## 7. Backups

`/admin/backup` writes a single `.zip` per snapshot containing:

- `redirect.db` — taken with SQLite's online backup API, so it is consistent
  even while the app is serving traffic and writing (WAL included)
- `redirect.config.json` — your admin password, MFA seeds, settings
- `state.json` — what wrote it, and which schema revision it was at
- `manifest.json` — checksums, row counts, version, creation time
- `RESTORE.txt` — the restore commands for every platform

From the command line, with the app stopped:

```sh
# Linux / macOS
docker compose exec app python -m app.utils.backup create --label "before 3.2"

# Windows (PowerShell)
docker compose exec app python -m app.utils.backup create --label "before 3.2"

docker compose exec app python -m app.utils.backup list
docker compose exec app python -m app.utils.backup inspect --archive /app/data/backups/<name>.zip
```

Restores are **staged and applied on the next start**. A running process cannot
safely replace the database it is serving from — on Windows the file is locked
outright, and elsewhere it would swap the file out from under live connections.
The web UI validates the archive, parks it as `data/.restore-pending.zip`, and
the entrypoint applies it before migrations on the next start.

An archive is refused, not guessed at, when it is:

- not a valid zip, or missing its manifest
- failing a per-file checksum
- written by a **newer schema** than this build understands

An archive from an **older** schema is accepted: migrations bring it forward
afterwards.

---

## 8. Health checks

```sh
curl http://localhost/api/health/state
```

Reports whether the data directory is writable, the current and head schema
revision, whether migrations are pending, and when the last backup was taken.
Worth wiring into whatever alerting you already have — the common upgrade
failure is a volume that quietly became read-only, and this is what catches it.

---

## 9. Files you should not touch

| File | Why |
|---|---|
| `data/redirect.db-wal`, `data/redirect.db-shm` | SQLite write-ahead log. Deleting them while the app runs corrupts the database. Stop the app first if you must. |
| `data/.restore-pending.zip` | Removing it cancels a staged restore. That is the way to abort one. |
| `.redirector-state.json` | Safe to delete, but the "ahead of this build" guard then has to infer the schema from the database instead. |

---

*See also [UPGRADE.md](UPGRADE.md) for the upgrade and rollback procedures, and
`https://github.com/authoritydmc/redirector/issues` if something here does not
match your deployment.*
