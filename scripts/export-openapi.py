#!/usr/bin/env python3
"""Commit the v3 OpenAPI contract snapshot (EPIC-03).

The generated contract is the API source of truth: the React SPA (EPIC-02)
code-generates its client from it, so unreviewed shape changes must fail CI.

Usage:
  python scripts/export-openapi.py          # regenerate docs/openapi.json
  python scripts/export-openapi.py --check  # exit 1 if out of sync (CI gate)

Regenerate after ANY router/schema change (and on every VERSION bump — the
spec carries the app version), and commit the result. Comparison is done on
parsed JSON, so key order and whitespace never cause false diffs; the file
itself is written with sorted keys + 2-space indent for stable diffs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_FILE = ROOT / "docs" / "openapi.json"

sys.path.insert(0, str(ROOT))


def current_spec() -> dict:
    from backend.main import create_app

    return create_app().openapi()


def render(spec: dict) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    spec = current_spec()
    if not SNAPSHOT_FILE.is_file():
        if check_only:
            print(f"{SNAPSHOT_FILE.name} does not exist yet.")
            print("Run: python scripts/export-openapi.py")
            return 1
        SNAPSHOT_FILE.write_text(render(spec), encoding="utf-8")
        print(f"Wrote {SNAPSHOT_FILE.relative_to(ROOT)} "
              f"({len(spec.get('paths', {}))} paths).")
        return 0
    committed = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    if committed == spec:
        print(f"{SNAPSHOT_FILE.relative_to(ROOT)} in sync "
              f"({len(spec.get('paths', {}))} paths).")
        return 0
    if check_only:
        print(f"{SNAPSHOT_FILE.relative_to(ROOT)} is OUT OF SYNC with the routers.")
        print("Run: python scripts/export-openapi.py  (and review the diff — "
              "contract changes need frontend sign-off per EPIC-03)")
        return 1
    SNAPSHOT_FILE.write_text(render(spec), encoding="utf-8")
    print(f"Regenerated {SNAPSHOT_FILE.relative_to(ROOT)} — review the diff before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
