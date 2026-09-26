#!/bin/bash
# Backup / restore Redirector on Linux (bare-metal install).
#
#   ./scripts/backup-linux.sh create [label]
#   ./scripts/backup-linux.sh list
#   ./scripts/backup-linux.sh verify   <archive.zip>
#   ./scripts/backup-linux.sh restore  <archive.zip>
#
# This is a thin wrapper: the real logic lives in app/utils/backup.py so the web
# UI, the container entrypoint and this script cannot drift apart. Restores are
# applied immediately here, which is only safe because you are running it against
# a stopped service. If the app is running, stage it instead and restart:
#
#   ./scripts/backup-linux.sh stage <archive.zip>
#   sudo systemctl restart redirector
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -n "${REDIRECTOR_PYTHON:-}" ]; then
  PYTHON="$REDIRECTOR_PYTHON"
elif [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=""
fi

if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import flask' >/dev/null 2>&1; then
  echo "No usable Python interpreter found (flask is not importable)." >&2
  echo "Set one up first:" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  .venv/bin/pip install -r requirements.txt" >&2
  echo "Or point REDIRECTOR_PYTHON at an existing environment." >&2
  exit 1
fi

cmd="${1:-}"
case "$cmd" in
  create) shift; exec "$PYTHON" -m app.utils.backup create --label "${1:-}" ;;
  list)   exec "$PYTHON" -m app.utils.backup list ;;
  verify) shift; exec "$PYTHON" -m app.utils.backup inspect --archive "$1" ;;
  stage)  shift; exec "$PYTHON" -m app.utils.backup stage "$1" ;;
  restore) shift; exec "$PYTHON" -m app.utils.backup restore "$1" ;;
  paths)  exec "$PYTHON" -m app.utils.backup paths ;;
  *)
    cat >&2 <<USAGE
Usage: $0 <command> [argument]

  create [label]     Write a new .zip into the data directory's backups/ folder.
                     Safe to run while the service is up.
  list               List local archives with size, version and row counts.
  verify <archive>   Validate an archive without changing anything.
  stage <archive>    Park an archive to be applied on the next start. Use this
                     when the service is running.
  restore <archive>  Apply an archive NOW. Stop the service first:
                       sudo systemctl stop redirector
                       $0 restore <archive>
                       sudo systemctl start redirector
  paths              Show the data directory, config file and resolved database.

Data directory: $REPO_ROOT/data (override with REDIRECTOR_DATA_DIR)
Full detail: docs/DATA-PERSISTENCE.md
USAGE
    exit 2
    ;;
esac
