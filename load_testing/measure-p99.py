#!/usr/bin/env python3
"""Redirect p99 measurement vs the master targets (EPIC-04/MASTER p99 boxes).

Drives the resolve hot path directly (no browser, no locust needed):
  - cached:   one shortcut resolved warm, N times  -> p99 must be < 10 ms
  - uncached: N distinct unknown-or-fresh patterns -> p99 must be < 150 ms

Usage (API must already run on the host, e.g. uvicorn WITHOUT --reload):
  python load_testing/measure-p99.py --host http://127.0.0.1:8123 [--samples 2000]

Exit 0 when both targets hold, 1 otherwise. CI does NOT gate on this
(shared runners are too noisy); humans re-run it on performance work.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import urllib.request
from urllib.error import HTTPError


def _get_ms(session_opener, url: str) -> float:
    start = time.perf_counter()
    try:
        with session_opener.open(url, timeout=30) as response:
            response.read()
    except HTTPError as exc:
        exc.read()  # 404s still count: the lookup ran
    return (time.perf_counter() - start) * 1000.0


def _p99(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.99)))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure redirect p99s.")
    parser.add_argument("--host", default="http://127.0.0.1:8123")
    parser.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args()

    opener = urllib.request.build_opener()
    create = urllib.request.Request(
        args.host + "/api/v1/shortcuts",
        data=b'{"pattern": "p99probe", "target": "https://example.com/p99"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(create, timeout=30) as response:
            response.read()
    except HTTPError as exc:
        if exc.code != 409:  # already exists from a previous run: fine
            raise

    # Warm the cache, then measure the cached path.
    _get_ms(opener, args.host + "/p99probe")
    cached = [_get_ms(opener, args.host + "/p99probe") for _ in range(args.samples)]
    # Fresh patterns every time: DB miss + suggestions path (uncached).
    uncached = [_get_ms(opener, args.host + f"/p99-miss-{i}")
                for i in range(args.samples)]

    cached_p99 = _p99(cached)
    uncached_p99 = _p99(uncached)
    print(f"cached   p99={cached_p99:.2f}ms (target <10ms, n={len(cached)})")
    print(f"uncached p99={uncached_p99:.2f}ms (target <150ms, n={len(uncached)})")
    ok = cached_p99 < 10.0 and uncached_p99 < 150.0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
