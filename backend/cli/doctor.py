"""Install doctor: pre-flight checks for a data dir (EPIC-08 task 5).

Verifies a v2/v3 install is migratable and bootable *before* touching it:
data dir writable, database readable (v2 or v3 shape), config parseable,
backups location available, broker reachable, disk headroom. Read-only,
except a temp-file writability probe it deletes immediately.

Usage:
  python -m backend.cli.doctor --data-dir ./data [--redis-url URL]

Exit codes: 0 healthy (warnings allowed), 1 problems found, 2 bad usage.
Missing-but-creatable things warn; broken things fail.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass()
class Check:
    name: str
    ok: bool
    warning: bool
    detail: str


@dataclass()
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", warning: bool = False) -> None:
        self.checks.append(Check(name, ok, warning, detail))

    @property
    def problems(self) -> int:
        return sum(1 for c in self.checks if not c.ok and not c.warning)

    @property
    def warnings(self) -> int:
        return sum(1 for c in self.checks if c.warning)

    def render(self) -> str:
        lines = []
        for check in self.checks:
            mark = "ok" if check.ok else ("warn" if check.warning else "FAIL")
            lines.append(f"[{mark:4}] {check.name}"
                         + (f": {check.detail}" if check.detail else ""))
        lines.append(
            f"doctor: {self.problems} problem(s), {self.warnings} warning(s)")
        return "\n".join(lines)


def check_data_dir(data_dir: Path, report: Report) -> None:
    if not data_dir.exists():
        report.add("data-dir", False, f"{data_dir} does not exist", warning=True)
        return
    if not data_dir.is_dir():
        report.add("data-dir", False, f"{data_dir} is not a directory")
        return
    try:
        probe = data_dir / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        report.add("data-dir", False, f"not writable: {exc}")
        return
    report.add("data-dir", True, str(data_dir))


def check_database(data_dir: Path, report: Report) -> None:
    db_path = data_dir / "redirect.db"
    if not db_path.exists():
        report.add("database", False, "no redirect.db yet (fresh install?)",
                   warning=True)
        return
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        report.add("database", False, f"unreadable: {exc}")
        return
    if "redirects" in tables:
        report.add("database", True, f"v2 shape ({len(tables)} tables)")
    elif "shortcuts" in tables:
        report.add("database", True, f"v3 shape ({len(tables)} tables)")
    else:
        report.add("database", False,
                   f"unrecognized schema: {sorted(tables) or 'no tables'}")


def check_config(data_dir: Path, report: Report) -> None:
    config_path = data_dir / "redirect.config.json"
    if not config_path.exists():
        report.add("config", False, "no redirect.config.json (defaults will apply)",
                   warning=True)
        return
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.add("config", False, f"unparseable: {exc}")
        return
    if not isinstance(data, dict):
        report.add("config", False, "top level is not an object")
        return
    report.add("config", True, f"{len(data)} top-level keys (values never printed)")


def check_backups_dir(data_dir: Path, report: Report) -> None:
    backups = data_dir / "backups"
    count = 0
    if backups.is_dir():
        count = sum(1 for p in backups.iterdir() if p.is_file())
    report.add("backups", True, f"{count} archive(s) present" if count else
               "directory will be created on first backup")


def check_disk(data_dir: Path, report: Report, minimum_mb: int = 100) -> None:
    try:
        free_mb = shutil.disk_usage(data_dir if data_dir.exists() else ".").free // (1024 * 1024)
    except OSError as exc:
        report.add("disk", False, f"unreadable: {exc}")
        return
    if free_mb < minimum_mb:
        report.add("disk", False, f"only {free_mb} MB free (want {minimum_mb})",
                   warning=True)
    else:
        report.add("disk", True, f"{free_mb} MB free")


def check_redis(report: Report, redis_url: str | None) -> None:
    if not redis_url:
        return
    parsed = urlparse(redis_url)
    host, port = parsed.hostname or "localhost", parsed.port or 6379
    sock = socket.socket()
    sock.settimeout(3)
    try:
        reachable = sock.connect_ex((host, port)) == 0
    except OSError:
        reachable = False
    finally:
        sock.close()
    if reachable:
        report.add("redis", True, f"{host}:{port} reachable")
    else:
        report.add("redis", False, f"{host}:{port} unreachable (cache/broker degraded)",
                   warning=True)


def run_checks(data_dir: Path, redis_url: str | None = None) -> Report:
    report = Report()
    check_data_dir(data_dir, report)
    check_database(data_dir, report)
    check_config(data_dir, report)
    check_backups_dir(data_dir, report)
    check_disk(data_dir, report)
    check_redis(report, redis_url)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-flight checks for a data dir.")
    parser.add_argument("--data-dir", required=True, help="install data directory")
    parser.add_argument("--redis-url", default=None, help="also probe the broker (warn-only)")
    args = parser.parse_args(argv)
    report = run_checks(Path(args.data_dir), args.redis_url)
    print(report.render())
    return 1 if report.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
