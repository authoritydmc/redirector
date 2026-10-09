"""p99 regression gate for the locust CSV report (EPIC-06 task 5).

Usage:  python load_testing/check_regression.py <csv-prefix> [baselines.json]

Compares per-endpoint p99s in <prefix>_stats.csv against baselines and fails
when an endpoint regresses more than 20% AND more than --floor-ms in absolute
terms. The absolute floor keeps the gate from flaking on fast endpoints
where 20% is sub-millisecond CI jitter (documented deviation from the bare
20% in the epic text).

Exit 0 = within budget, 1 = regression, 2 = missing data.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

DEFAULT_BASELINES = Path(__file__).resolve().parent / "p99-baselines.json"
DEFAULT_FLOOR_MS = 50.0


def _read_p99(prefix: str) -> dict[str, float]:
    stats_path = Path(f"{prefix}_stats.csv")
    if not stats_path.is_file():
        raise FileNotFoundError(f"missing report {stats_path}")
    out: dict[str, float] = {}
    with stats_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("Name") or "").strip()
            if not name or name == "Aggregated":
                continue
            try:
                out[name] = float(row.get("99%") or 0)
            except ValueError:
                continue
    return out


def main(argv: list[str]) -> int:
    prefix = argv[0] if len(argv) > 0 else "load-smoke"
    baselines_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_BASELINES
    try:
        actual = _read_p99(prefix)
    except FileNotFoundError as exc:
        print(f"check_regression: {exc}", file=sys.stderr)
        return 2
    try:
        baselines = json.loads(baselines_path.read_text(encoding="utf-8"))["endpoints"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"check_regression: bad baselines {baselines_path}: {exc}", file=sys.stderr)
        return 2

    failures = 0
    for name, spec in sorted(baselines.items()):
        baseline = float(spec["p99_ms"])
        current = actual.get(name)
        if current is None:
            print(f"check_regression: no samples for {name}", file=sys.stderr)
            return 2
        budget = max(baseline * 1.2, baseline + DEFAULT_FLOOR_MS)
        verdict = "ok" if current <= budget else "REGRESSION"
        print(f"[{verdict}] {name}: p99={current:g}ms baseline={baseline:g}ms budget={budget:g}ms")
        if current > budget:
            failures += 1
    if failures:
        print(f"check_regression: FAIL ({failures} endpoint(s) over budget)", file=sys.stderr)
        return 1
    print("check_regression: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
