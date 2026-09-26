"""Portable filesystem layout for Redirector.

Everything the app persists lives under a single *data directory* so that a
backup is one archive and an upgrade never has to guess where state went.

Data directory resolution order:
  1. ``REDIRECTOR_DATA_DIR`` environment variable (used by Docker, systemd,
     Task Scheduler, launchd, and tests).
  2. ``<project root>/data`` (default for source checkouts).

Portability contract
--------------------
The ``database`` value persisted in ``redirect.config.json`` is always stored
in a *portable* form (``sqlite:///<name>``, interpreted relative to the data
directory) whenever the database file physically lives inside the data
directory. That is the only thing that makes ``data/`` copyable between
machines, operating systems and mount points without editing the config.

Older releases wrote an **absolute** host path (e.g.
``sqlite:///D:\\projects\\redirector\\data\\redirect.db``). Those values break
silently the moment the project is cloned, moved, or given a different mount
point: the app would create a brand new empty database and appear to have
"lost" every shortcut. :func:`resolve_database_uri` detects that situation,
repairs it, and heals the stored value back to the portable form.
"""

from __future__ import annotations

import os
import re

SQLITE_PREFIX = "sqlite:///"

# Windows drive-letter or UNC path, e.g. D:\data or \\server\share
_WINDOWS_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})")

ENV_DATA_DIR = "REDIRECTOR_DATA_DIR"
DEFAULT_DATA_DIR_NAME = "data"
DEFAULT_DB_FILENAME = "redirect.db"

# Canonical, portable relative name. Two levels of slashes are consumed by the
# "sqlite://" scheme, so "sqlite:///redirect.db" means "<data dir>/redirect.db".
PORTABLE_DB_SUFFIX = DEFAULT_DB_FILENAME


def project_root() -> str:
    """Absolute path to the repository / installation root."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_data_dir() -> str:
    """Return the absolute data directory, creating it if necessary."""
    raw = os.environ.get(ENV_DATA_DIR, "").strip()
    if raw:
        data_dir = os.path.abspath(os.path.expanduser(raw))
    else:
        data_dir = os.path.join(project_root(), DEFAULT_DATA_DIR_NAME)
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        # Report the real problem later with a writable check rather than here,
        # so the caller can produce an actionable error message.
        pass
    return data_dir


def is_windows_absolute(path: str) -> bool:
    """True for ``C:\\x``, ``C:/x`` and UNC paths, even when running on POSIX."""
    return bool(_WINDOWS_ABS_RE.match(path or ""))


def is_sqlite_uri(value: str) -> bool:
    return bool(value) and str(value).strip().lower().startswith(SQLITE_PREFIX)


def sqlite_path_from_uri(uri: str) -> str:
    """Extract the raw filesystem path from a SQLite URI.

    ``sqlite:///redirect.db`` -> ``redirect.db`` (relative)
    ``sqlite:////srv/x.db``  -> ``/srv/x.db``   (absolute, POSIX)
    ``sqlite:///D:\\x\\y.db``  -> ``D:\\x\\y.db`` (absolute, Windows)
    """
    raw = str(uri).strip()[len(SQLITE_PREFIX):]
    # A single leading slash was already consumed by the "sqlite://" authority
    # marker, so "sqlite:////srv" yields "/srv".
    return raw


def is_absolute_sqlite_path(path: str) -> bool:
    return os.path.isabs(path) or is_windows_absolute(path)


def uri_from_sqlite_path(path: str) -> str:
    """Build a SQLite URI for an absolute filesystem path."""
    return SQLITE_PREFIX + os.path.abspath(path)


def portable_uri_for_path(path: str, data_dir: str) -> str:
    """Return the URI we *want to persist* for ``path``.

    Files inside the data directory collapse to ``sqlite:///<name>`` so the
    config stays valid on any host. Files elsewhere keep an absolute URI,
    because a bare name would be ambiguous.
    """
    abs_path = os.path.abspath(path)
    abs_data = os.path.abspath(data_dir)
    try:
        if os.path.dirname(abs_path) == abs_data:
            return SQLITE_PREFIX + os.path.basename(abs_path)
    except (ValueError, OSError):
        pass
    return uri_from_sqlite_path(abs_path)


def _candidate_paths(raw: str, data_dir: str) -> list[str]:
    """Every location the stored path might mean, most specific first."""
    root = project_root()
    if is_absolute_sqlite_path(raw):
        candidates = [raw]
        # Heal absolute paths that no longer exist: the folder was moved,
        # cloned to another machine, or the mount point changed.
        candidates.append(os.path.join(data_dir, os.path.basename(raw)))
        candidates.append(os.path.join(root, raw.lstrip("/\\")))
    else:
        # Relative URIs are anchored to the data directory first, then to the
        # project root, which covers "sqlite:///data/redirect.db" written by
        # hand-edited configs.
        candidates = [os.path.join(data_dir, raw)]
        candidates.append(os.path.join(root, raw))
        candidates.append(os.path.join(data_dir, os.path.basename(raw)))
    return [c for c in candidates if c]


def _exists(path: str) -> bool:
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def resolve_database_uri(stored: str | None, data_dir: str) -> tuple[str, str | None]:
    """Resolve the configured database URI into one this host can actually use.

    Returns ``(uri, heal_from)``:

    * ``uri`` is what the app should connect to.
    * ``heal_from`` is the stale value that had to be repaired, or ``None``.

    Callers should persist the portable form of the resolved path whenever it
    differs from what was stored, and only warn about ``heal_from`` when it is
    set - a relative path with no file yet is a fresh install, not damage.
    """
    default_uri = SQLITE_PREFIX + PORTABLE_DB_SUFFIX

    if not stored or not str(stored).strip():
        return default_uri, None

    if not is_sqlite_uri(stored):
        # Postgres / MySQL URIs are host-independent, pass through untouched.
        return stored, None

    raw = sqlite_path_from_uri(stored)
    if raw in ("", ":memory:") or raw.startswith("file:"):
        # In-memory and URI-mode databases are used by tests only.
        return stored, None

    candidates = _candidate_paths(raw, data_dir)
    for index, candidate in enumerate(candidates):
        if _exists(candidate):
            # index 0 is the path exactly as stored; anything later means the
            # stored value pointed somewhere that no longer exists and we are
            # re-anchoring. The caller must persist that, so report it.
            return uri_from_sqlite_path(candidate), (None if index == 0 else stored)

    # Nothing matched.
    if is_absolute_sqlite_path(raw):
        # An absolute path pointing nowhere on this host is the dangerous case
        # from the portability contract: SQLite would create a fresh empty
        # database and the install would look wiped. Re-anchor into the data
        # directory and tell the caller to persist it.
        fallback = os.path.join(data_dir, os.path.basename(raw))
        return uri_from_sqlite_path(fallback), stored

    # A relative path with no file yet is not damaged - it is a fresh install,
    # and the value is already in the portable form. Reporting it as "healed"
    # made every command on a new install print a scary re-anchoring warning.
    return uri_from_sqlite_path(candidates[0]), None


def relative_to_data(path: str, data_dir: str) -> str:
    """Human-friendly display of ``path`` relative to the data directory."""
    try:
        return os.path.relpath(os.path.abspath(path), os.path.abspath(data_dir))
    except ValueError:
        return os.path.abspath(path)
