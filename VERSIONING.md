# Versioning for Redirector

## Where the version comes from

1. `VERSION` file at the repo root is the single source of truth (`3.2.0`).
2. `python get_version.py --bump [patch|minor|major]` increments it.
3. `app/CONSTANTS.py:get_semver()` reports `VERSION` plus a git-describe
   suffix on dev builds (`3.2.0+154.gcfae5be`); exactly on a tag it reports
   the base alone.
4. Docker images stamp it at build time: the deploy workflows pass
   `APP_VERSION` (from `VERSION`) and `GIT_COMMIT` (the sha) as build args,
   baked into `REDIRECTOR_APP_VERSION` and the in-image `VERSION` file — so
   an image always reports its own tag, never the build machine's checkout.

## How update checks work

`GET /api/latest-version` compares this install against GitHub
(`app/utils/versioning.py` — the only place that parses or compares
versions, so the footer badge, the system-info badge and the upgrade page
cannot disagree):

* Normalizes every format to `major.minor.patch` first: `v3.2.0` (tags),
  `3.2.0+154.gcfae5be` (dev builds) and `3.2.0` (file/stamp) all compare as
  `3.2.0`.
* Reads `releases/latest`, falling back to the raw `VERSION` file on `main`
  when no releases exist yet.
* Returns `current`, `current_full`, `latest`, `update_available`, `source`
  (`github-release` / `version-file` / `none`), `checked_at` and `error`.
* Fails closed: when GitHub cannot be reached (or rate-limits), the response
  says `success: false` instead of claiming "up to date". "Up to date" is
  only reported when GitHub answered and publishes nothing newer.
* Cached in-process for 24h (per gunicorn worker).

The footer banner (`base.html`) and the system-info badge (`system_info.html`)
render that verdict verbatim — they do no version math of their own. The
banner links to `/upgrade` and `/changelog`, and stays dismissed per
published version once closed.
