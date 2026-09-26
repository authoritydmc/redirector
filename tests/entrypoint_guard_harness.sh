#!/bin/bash
# Harness: exercise entrypoint.sh's decision logic in isolation.
#
# Not part of the pytest suite, and not a substitute for the real container
# test. It exists because Docker is unavailable on the development machine, and
# the guard is the most important logic in the upgrade path - it is what stops
# an old image from rewriting data written by a newer one.
#
# `python` and `flask` are stubbed, so this checks the *shell* decisions: which
# branch is taken, whether it exits non-zero, and whether the database file is
# left alone. The Python half of the same behaviour (classifying the revision,
# snapshotting, restoring) is covered by tests/test_backup_restore.py.
#
# Usage: bash tests/entrypoint_guard_harness.sh
set -u

T=/tmp/rtest
REPO=/mnt/d/coding/redirector

rm -rf "$T"
mkdir -p "$T/bin" "$T/data"
cp "$REPO/entrypoint.sh" "$T/entrypoint.sh"

# The revision the stubbed python reports as being in the database.
cat > "$T/bin/revision" <<'STUB'
#!/bin/sh
cat "$STUB_REVISION_FILE" 2>/dev/null || echo "-"
STUB

# Emulates the `python -c '... schema_status ...'` call the entrypoint makes to
# learn drift, the current revision and the head. The seed file holds all three
# in that order. Classifying the revision is Python's job and is covered by
# tests/test_backup_restore.py; what is under test here is that the shell acts
# on the classification correctly.
cat > "$T/bin/python" <<'STUB'
#!/bin/sh
case "$1" in
  -c)
    if echo "$2" | grep -q schema_status; then
      cat "$STUB_REVISION_FILE" 2>/dev/null || echo "unknown|-|-"
      exit 0
    fi
    echo "[stub] python $*"
    exit 0
    ;;
  *)
    echo "[stub] python $*"
    exit 0
    ;;
esac
STUB

cat > "$T/bin/flask" <<'STUB'
#!/bin/sh
echo "[stub] flask $*"
exit "${STUB_FLASK_EXIT:-0}"
STUB

cat > "$T/bin/gunicorn" <<'STUB'
#!/bin/sh
echo "[stub] gunicorn $*"
STUB

chmod +x "$T/bin"/*
export STUB_REVISION_FILE="$T/revision"
export PATH="$T/bin:$PATH"
export REDIRECTOR_DATA_DIR="$T/data"
export REDIRECTOR_SKIP_MIGRATIONS=""

seed() { echo "$1" > "$T/revision"; }

# A stand-in for the database so "was it modified?" is answerable without SQLite.
seed_db() { printf 'ORIGINAL-DATA' > "$T/data/redirect.db"; }
fingerprint() { cat "$T/data/redirect.db" 2>/dev/null || echo "<absent>"; }

report() {
  local label="$1" revfile="$2"; shift 2
  echo "================ $label ================"
  seed_db
  local before; before="$(fingerprint)"
  env STUB_REVISION_FILE="$revfile" "$@" bash "$T/entrypoint.sh" 2>&1 \
    | grep -E "^\[redirector\]|^\[stub\]"
  local code=${PIPESTATUS[0]}
  local after; after="$(fingerprint)"
  echo "--> exit code : $code"
  echo "--> database  : $before -> $after  $( [ "$before" = "$after" ] && echo UNCHANGED || echo MODIFIED )"
  echo
}

# seed writes to the path given as $1, the revision as $2. It used to write to a
# fixed name, which silently discarded the caller's redirect and made every case
# below read an empty revision file.
seed() { echo "$2" > "$1"; }

HEAD_REV="20250828_enterprise"

seed "$T/rev_head" "up-to-date|$HEAD_REV|$HEAD_REV"
report "schema at head, migrations skipped" "$T/rev_head" REDIRECTOR_SKIP_MIGRATIONS=1

seed "$T/rev_head" "up-to-date|$HEAD_REV|$HEAD_REV"
report "schema at head (no snapshot, no migration needed)" "$T/rev_head"

seed "$T/rev_old" "pending|f200f245867a|$HEAD_REV"
report "schema behind (expect snapshot + migrate)" "$T/rev_old"

seed "$T/rev_new" "ahead|29991231_future|$HEAD_REV"
report "data NEWER than image (expect FATAL, exit 1, no migration)" "$T/rev_new"

report "no database at all (fresh install, expect snapshot + migrate)" "$T/rev_missing"

seed "$T/rev_old" "pending|f200f245867a|$HEAD_REV"
report "migration keeps failing (expect FATAL after 2 tries)" \
  "$T/rev_old" REDIRECTOR_MIGRATE_ATTEMPTS=2 STUB_FLASK_EXIT=1

echo "================ read-only data dir (expect FATAL, exit 1) ================"
seed "$T/rev_head" "up-to-date|$HEAD_REV|$HEAD_REV"
chmod 555 "$T/data"
env STUB_REVISION_FILE="$T/rev_head" bash "$T/entrypoint.sh" 2>&1 | grep -E "^\[redirector\]|^\[stub\]"
echo "--> exit code : ${PIPESTATUS[0]}"
chmod 755 "$T/data"
echo

echo "================ staged restore is applied before anything else ================"
seed "$T/rev_head" "up-to-date|$HEAD_REV|$HEAD_REV"
printf 'not-a-real-zip' > "$T/data/.restore-pending.zip"
env STUB_REVISION_FILE="$T/rev_head" bash "$T/entrypoint.sh" 2>&1 | grep -E "^\[redirector\]|^\[stub\]"
echo "--> exit code : ${PIPESTATUS[0]}"
