"""Gate for the locust CSV report: fail on any request failure or exception.

Usage:  python load_testing/check_stats.py <csv-prefix>

Reads <prefix>_stats.csv (plus _failures.csv / _exceptions.csv when present),
prints a short summary including the resolve-hot-path p99, and exits 1 when
anything failed. Missing report files are also an error (loud, not green).
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main(prefix: str) -> int:
    stats_path = Path(f"{prefix}_stats.csv")
    if not stats_path.is_file():
        print(f"check_stats: missing report {stats_path}", file=sys.stderr)
        return 2
    rows = _read_rows(stats_path)
    body = [r for r in rows if r.get("Name") != "Aggregated"]
    total_requests = sum(int(r.get("Request Count") or 0) for r in body)
    total_failures = sum(int(r.get("Failure Count") or 0) for r in body)

    failures_path = Path(f"{prefix}_failures.csv")
    logged_failures = len(_read_rows(failures_path)) if failures_path.is_file() else 0
    exceptions_path = Path(f"{prefix}_exceptions.csv")
    exceptions = len(_read_rows(exceptions_path)) if exceptions_path.is_file() else 0

    def _p99(name: str) -> str:
        for r in body:
            if r.get("Name") == name:
                return r.get("99%", "?")
        return "n/a"

    print(f"requests={total_requests} failures={total_failures} "
          f"logged_failures={logged_failures} exceptions={exceptions}")
    print(f"p99 /<shortcut>={_p99('/<shortcut>')}ms "
          f"p99 /<unknown-shortcut>={_p99('/<unknown-shortcut>')}ms")
    worst = max(body, key=lambda r: float(r.get("99%", 0) or 0), default=None)
    if worst is not None:
        print(f"slowest endpoint: {worst.get('Name')} p99={worst.get('99%', '?')}ms")

    if total_requests == 0:
        print("check_stats: no requests recorded", file=sys.stderr)
        return 2
    if total_failures or logged_failures or exceptions:
        print("check_stats: FAIL", file=sys.stderr)
        return 1
    print("check_stats: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "load-smoke"))
