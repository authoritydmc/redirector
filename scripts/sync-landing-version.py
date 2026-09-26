#!/usr/bin/env python3
"""Keep the GitHub Pages landing page (index.html) in sync with the release version.

Single source of truth: the VERSION file at the repo root.
This script stamps that version into the two badges in index.html:
  - nav badge:        >v3.1.1<   -> >v<VERSION><
  - hero announcement: Version 3.1.1 Released -> Version <VERSION> Released

Usage:
  python scripts/sync-landing-version.py          # stamp in place
  python scripts/sync-landing-version.py --check  # exit 1 if out of sync (CI gate)

The static.yml Pages workflow runs this automatically at deploy time, so the
deployed site can never drift. Run it locally (or let your agent run it) whenever
VERSION, CHANGELOG.md, or user-facing features change, and commit the result so
PRs stay green under validate.yml.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
INDEX_FILE = ROOT / "index.html"

NAV_RE = re.compile(r">v\d+\.\d+\.\d+<")
HERO_RE = re.compile(r"Version \d+\.\d+\.\d+ Released")


def read_version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"VERSION file has unexpected content: {version!r}")
    return version


def synced_text(text: str, version: str) -> str:
    text = NAV_RE.sub(f">v{version}<", text)
    text = HERO_RE.sub(f"Version {version} Released", text)
    return text


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    version = read_version()
    original = INDEX_FILE.read_text(encoding="utf-8")
    updated = synced_text(original, version)
    if original == updated:
        print(f"index.html already in sync (v{version}).")
        return 0
    if check_only:
        print(f"index.html is OUT OF SYNC with VERSION (v{version}).")
        print("Run: python scripts/sync-landing-version.py")
        return 1
    INDEX_FILE.write_text(updated, encoding="utf-8")
    print(f"Stamped index.html to v{version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
