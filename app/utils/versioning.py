"""Version normalization, comparison and update checks in one place.

Three version formats meet here, and every past "Unknown version" bug was a
mismatch between them:

* ``VERSION`` file / Docker stamp: ``3.2.0``
* Dev builds (git describe):      ``3.2.0+154.gcfae5be``
* GitHub tags / releases:         ``v3.2.0``

Everything - the ``/api/latest-version`` endpoint, the footer banner, the
system-info badge, the upgrade page - must compare the same normalized
``major.minor.patch`` triple, or one of them lies.
"""

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

GITHUB_REPO = "authoritydmc/redirector"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RAW_VERSION_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
)
CHECK_TTL_SECONDS = 24 * 3600

_SEMVER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def normalize_version(value) -> str:
    """Reduce any known version format to ``major.minor.patch``.

    ``v3.2.0`` -> ``3.2.0``; ``3.2.0+154.gcfae5be`` -> ``3.2.0``;
    garbage -> ``""``. Never raises.
    """
    if not value or not isinstance(value, str):
        return ""
    match = _SEMVER_RE.search(value.strip())
    if not match:
        return ""
    return ".".join(match.groups())


def parse_semver(value):
    """``"v3.2.0"`` -> ``(3, 2, 0)``; anything else -> ``None``."""
    normalized = normalize_version(value)
    if not normalized:
        return None
    return tuple(int(part) for part in normalized.split("."))


def compare_semver(a, b) -> int:
    """``1`` if ``a`` is newer, ``-1`` if older, ``0`` if equal or unknown.

    Unknown (unparseable either side) compares equal: the caller must not
    claim an update exists based on a version it could not read.
    """
    pa, pb = parse_semver(a), parse_semver(b)
    if not pa or not pb:
        return 0
    return (pa > pb) - (pa < pb)


def _github_latest(timeout: float):
    """Newest published release tag. ``(version, error)``.

    ``error`` is ``""`` on success; ``no-releases`` when GitHub answers but
    publishes nothing; anything else (``unreachable``, ``rate-limited``,
    ``http-*``, ``bad-response``) means nothing could be learned.
    """
    try:
        resp = requests.get(RELEASES_URL, timeout=timeout)
    except Exception as exc:
        logger.warning("Version check: GitHub API unreachable: %s", exc)
        return "", "unreachable"
    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            return "", "bad-response"
        latest = normalize_version(data.get("tag_name") or data.get("name") or "")
        if latest:
            return latest, ""
        return "", "no-releases"
    if resp.status_code == 404:
        return "", "no-releases"
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        logger.warning("Version check: GitHub API rate limit exceeded")
        return "", "rate-limited"
    logger.warning("Version check: GitHub API status %s", resp.status_code)
    return "", f"http-{resp.status_code}"


def _file_fallback(timeout: float):
    """Raw VERSION file on main, for when releases do not exist yet."""
    try:
        resp = requests.get(RAW_VERSION_URL, timeout=timeout)
        if resp.ok:
            latest = normalize_version(resp.text)
            if latest:
                return latest, ""
            return "", "no-fallback"
    except Exception as exc:
        logger.warning("Version check: raw VERSION unreachable: %s", exc)
        return "", "unreachable"
    return "", "no-fallback"


def check_for_updates(current_full: str, timeout: float = 3) -> dict:
    """Compare this install against what GitHub publishes.

    Returns a JSON-serializable dict with a stable shape - the endpoint, the
    footer banner and the system-info badge all read the same fields:

    ``success``, ``current`` (normalized), ``current_full`` (as reported),
    ``latest`` (normalized, ``""`` when unknown), ``update_available``,
    ``source`` (``github-release`` / ``version-file`` / ``none``),
    ``checked_at`` (UTC ISO), and ``error`` (``""`` on success).
    """
    current = normalize_version(current_full)
    latest, error = _github_latest(timeout)
    source = "github-release"
    if not latest and error != "rate-limited":
        # The raw file lives on a different host, so it is worth one try
        # whenever the API did not answer - except while rate-limited.
        latest, error = _file_fallback(timeout)
        source = "version-file" if latest else "none"
    if not latest:
        if error in ("no-releases", "no-fallback"):
            # GitHub answered and publishes nothing to compare against:
            # up-to-date, honestly reported.
            return {
                "success": True,
                "current": current,
                "current_full": current_full,
                "latest": current,
                "update_available": False,
                "source": "none",
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "error": "",
            }
        # Anything else (unreachable, rate-limited, HTTP error, bad payload):
        # fail closed. Never claim "up to date" from ignorance.
        return {
            "success": False,
            "current": current,
            "current_full": current_full,
            "latest": "",
            "update_available": False,
            "source": "none",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "error": error,
        }
    return {
        "success": True,
        "current": current,
        "current_full": current_full,
        "latest": latest,
        "update_available": compare_semver(latest, current) > 0,
        "source": source,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "error": "",
    }
