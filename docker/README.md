# Docker layouts

## Production stack (`compose.prod.yml`)

web (SPA + proxy, `:80`) → api (uvicorn, `Dockerfile.api`, non-root,
migrates on boot) + worker (arq) + redis. SQLite by default:

```sh
REDIRECTOR_ADMIN_PASSWORD=... REDIRECTOR_JWT_SECRET=... \
  docker compose -f docker/compose.prod.yml up -d --build
```

Postgres variant (merge the overlay — same services, new database):

```sh
REDIRECTOR_ADMIN_PASSWORD=... REDIRECTOR_JWT_SECRET=... \
  docker compose -f docker/compose.prod.yml -f docker/compose.postgres.yml up -d --build
```

Secrets are never baked in: compose fails fast without both password
variables. Scale-out (`--scale api=N`) requires postgres + redis (rate
limits and in-process singleflight are per-process; JWTs and job rows
are already shared-safe).

`DATA_DIR` must be persistent and writable by the container user: `/tmp`
is wiped on reboot (and docker recreates it root-owned, which the
non-root api image cannot write — every DB call then 500s). Use `./data`
(the default) or a directory under your home.
