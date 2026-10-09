"""Hot-path regression gate for the locust CSV report (EPIC-06 task 5).

Usage:  python load_testing/check_regression.py <csv-prefix> [baselines.json]

Compares the resolve hot-path p50 against baselines and fails on regression.
p50 (median) is the verdict because p99 over ~100 samples on shared runners
is jitter-dominated: the first p99-gated run showed 9ms -> 95ms on
byte-identical code (one slow fsync moves p99; the median never lies about
systemic slowdowns). p99 is still REPORTED per endpoint for trend-spotting,
and the zero-failures gate in check_stats.py runs first.

Blocking rule: p50 > baseline * 1.2 AND p50 > baseline + 10ms.
Exit 0 = within budget, 1 = regression, 2 = missing data.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

DEFAULT_BASELINES = Path(__file__).resolve().parent / "p99-baselines.json"
DEFAULT_FLOOR_MS = 10.0


def _read_stats(prefix: str) -> dict[str, dict[str, float]]:
    stats_path = Path(f"{prefix}_stats.csv")
    if not stats_path.is_file():
        raise FileNotFoundError(f"missing report {stats_path}")
    out: dict[str, dict[str, float]] = {}
    with stats_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("Name") or "").strip()
            if not name or name == "Aggregated":
                continue
            try:
                out[name] = {
                    "p50": float(row.get("50%") or 0),
                    "p99": float(row.get("99%") or 0),
                }
            except ValueError:
                continue
    return out


def main(argv: list[str]) -> int:
    prefix = argv[0] if len(argv) > 0 else "load-smoke"
    baselines_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_BASELINES
    try:
        actual = _read_stats(prefix)
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
        baseline_p50 = float(spec["p50_ms"])
        current = actual.get(name)
        if current is None:
            print(f"check_regression: no samples for {name}", file=sys.stderr)
            return 2
        budget = max(baseline_p50 * 1.2, baseline_p50 + DEFAULT_FLOOR_MS)
        verdict = "ok" if current["p50"] <= budget else "REGRESSION"
        print(f"[{verdict}] {name}: p50={current['p50']:g}ms "
              f"baseline={baseline_p50:g}ms budget={budget:g}ms "
              f"(p99={current['p99']:g}ms informational)")
        if current["p50"] > budget:
            failures += 1
    if failures:
        print(f"check_regression: FAIL ({failures} endpoint(s) over budget)", file=sys.stderr)
        return 1
    print("check_regression: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
