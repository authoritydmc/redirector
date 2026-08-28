"""Auto-version helper for redirector.

Computes a version string from the repo's VERSION file plus git state so every
build gets a unique, human-readable version like:

    3.1.0                         (release)
    3.1.0+5.gabcdef               (dev build 5 commits ahead)
    3.1.0+build.14.abc1234        (full UI display)

Usage:
    python get_version.py            -> full UI version (3.1.0+5.gabcdef)
    python get_version.py --tag      -> docker-safe tag (3.1.0-b5-gabcdef)
    python get_version.py --version  -> base version only (3.1.0)
    python get_version.py --build    -> build suffix only (5.gabcdef)
    python get_version.py --bump [patch|minor|major] -> increment VERSION file
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(ROOT, "VERSION")


def _git(*args):
    try:
        out = subprocess.run(
            ["git"] + list(args),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def base_version() -> str:
    if os.path.isfile(VERSION_FILE):
        with open(VERSION_FILE, encoding="utf-8") as f:
            version = f.read().strip()
        if version:
            return version
    return "3.1.0"


def bump_version(part: str = "patch") -> str:
    curr = base_version()
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", curr)
    if m:
        major, minor, patch, extra = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        if part == "major":
            major += 1
            minor = 0
            patch = 0
        elif part == "minor":
            minor += 1
            patch = 0
        else:
            patch += 1
        new_version = f"{major}.{minor}.{patch}{extra}"
    else:
        new_version = "3.1.1"
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(new_version + "\n")
    return new_version


def build_suffix() -> str:
    commit_count = _git("rev-list", "--count", "HEAD") or os.environ.get("GIT_COMMIT_COUNT", "0")
    short_sha = _git("rev-parse", "--short", "HEAD") or os.environ.get("GIT_COMMIT", "prod")
    # Try git describe for more precise count since tag
    desc = _git("describe", "--tags", "--long", "--match", "v*")
    if desc:
        m = re.match(r"v?(\d+\.\d+\.\d+)-(\d+)-g([0-9a-f]+)", desc)
        if m:
            _, commits, githash = m.groups()
            return f"{commits}.g{githash}"
    return f"{commit_count}.{short_sha}"


def ui_version() -> str:
    bv = base_version()
    suffix = build_suffix()
    # If suffix is 0.g<hash> meaning exactly at tag, just return base
    if suffix.startswith("0.g"):
        return bv
    return f"{bv}+{suffix}"


def docker_tag() -> str:
    bv = base_version()
    suffix = build_suffix()
    if suffix.startswith("0.g"):
        return bv
    count, sha = suffix.split(".", 1) if "." in suffix else (suffix, "prod")
    # sha already has g prefix
    return f"{bv}-b{count}-{sha.lstrip('g')}"

def main():
    args = sys.argv[1:]
    mode = args[0] if args else "full"
    if mode == "--tag":
        print(docker_tag())
    elif mode == "--version":
        print(base_version())
    elif mode == "--build":
        print(build_suffix())
    elif mode in ("--bump", "--auto-bump"):
        part = "patch"
        if len(args) > 1 and args[1] in ("patch", "minor", "major"):
            part = args[1]
        new_ver = bump_version(part)
        print(f"Bumped version ({part}) -> {new_ver}")
    else:
        print(ui_version())

if __name__ == "__main__":
    main()
