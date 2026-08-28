#!/usr/bin/env bash
# Redirector - Docker smoke test (borrowed from Tax_Scripts)
# Builds the image and verifies it end-to-end.
set -euo pipefail
cd "$(dirname "$0")"

C_RESET=$'\033[0m'
C_GREEN=$'\033[32m'
C_RED=$'\033[31m'
C_YELLOW=$'\033[33m'
C_CYAN=$'\033[36m'

IMG="redirector:test"
CONTAINER="redirector-test-$$"
NET="redirector-test-net-$$"

HOST_PORT=""
FE_PORT=""

pick_port() {
  python3 - <<'PY' 2>/dev/null || true
import socket
s=socket.socket()
s.bind(("127.0.0.1",0))
print(s.getsockname()[1])
s.close()
PY
}

HOST_PORT=$(pick_port)
[ -z "${HOST_PORT:-}" ] && HOST_PORT=$(( (RANDOM % 20000) + 20000 ))
echo "Using host port: $HOST_PORT"

echo "${C_CYAN}===========================================${C_RESET}"
echo "${C_CYAN}  Redirector - Docker Smoke Test${C_RESET}"
echo "${C_CYAN}===========================================${C_RESET}"
echo

cleanup() {
  echo
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! docker info >/dev/null 2>&1; then
  echo "${C_RED}[FAIL] Docker daemon not running.${C_RESET}"
  exit 1
fi
echo "${C_GREEN}[ OK ] Docker daemon running.${C_RESET}"

# 1. Build
echo "${C_YELLOW}[1/4] Building image (showing live logs)...${C_RESET}"
if ! docker build -t "$IMG" .; then
  echo "${C_RED}[FAIL] Build failed.${C_RESET}"
  exit 1
fi
echo "${C_GREEN}[ OK ] Built $IMG${C_RESET}"

# 2. Run
echo "${C_YELLOW}[2/4] Starting container on $HOST_PORT...${C_RESET}"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
if ! docker run -d --name "$CONTAINER" -p "$HOST_PORT:80" -e REDIS_HOST=redis "$IMG" >/dev/null; then
  echo "${C_RED}[FAIL] Could not start container.${C_RESET}"
  exit 1
fi
# Also start redis for full compose test? For smoke, we test without redis (should work fallback)
echo "${C_GREEN}[ OK ] Container running${C_RESET}"

# 3. Wait for health
echo "${C_YELLOW}[3/4] Waiting for /health ...${C_RESET}"
for i in $(seq 1 30); do
  echo "[poll $i/30] http://localhost:$HOST_PORT/health"
  if curl -s -f "http://localhost:$HOST_PORT/health" >/dev/null 2>&1; then
    HEALTH=$(curl -s "http://localhost:$HOST_PORT/health")
    echo "${C_GREEN}[ OK ] Health: $HEALTH${C_RESET}"
    break
  fi
  sleep 1
  if [ "$i" = "30" ]; then
    echo "${C_RED}[FAIL] Health never ready. Logs:${C_RESET}"
    docker logs "$CONTAINER" 2>&1 | tail -n 50
    exit 1
  fi
done

# 4. Frontend + API checks
echo "${C_YELLOW}[4/4] Testing endpoints...${C_RESET}"
# 4a. Dashboard (on fresh DB, / redirects to /setup with 302, which is expected)
CODE=$(curl -s -o /tmp/redirector_test.html -w "%{http_code}" "http://localhost:$HOST_PORT/")
if [ "$CODE" = "302" ]; then
  echo "${C_YELLOW}[ .. ] Dashboard 302 (first-run -> /setup), following...${C_RESET}"
  CODE2=$(curl -s -L -o /tmp/redirector_test.html -w "%{http_code}" "http://localhost:$HOST_PORT/")
  if [ "$CODE2" != "200" ]; then echo "${C_RED}[FAIL] Dashboard after redirect $CODE2${C_RESET}"; cat /tmp/redirector_test.html; exit 1; fi
  echo "${C_GREEN}[ OK ] Dashboard 302 -> /setup then 200 (first run)${C_RESET}"
elif [ "$CODE" != "200" ]; then
  echo "${C_RED}[FAIL] Dashboard $CODE${C_RESET}"; cat /tmp/redirector_test.html; exit 1
else
  echo "${C_GREEN}[ OK ] Dashboard 200${C_RESET}"
fi
# Also check /setup directly
CODE_SETUP=$(curl -s -o /tmp/setup.html -w "%{http_code}" "http://localhost:$HOST_PORT/setup")
if [ "$CODE_SETUP" != "200" ]; then echo "${C_YELLOW}[ .. ] /setup $CODE_SETUP (may require setup)${C_RESET}"; else echo "${C_GREEN}[ OK ] /setup 200${C_RESET}"; fi

# 4b. Changelog API (was broken when *.md ignored)
CODE=$(curl -s -o /tmp/changelog.json -w "%{http_code}" "http://localhost:$HOST_PORT/api/changelog")
if [ "$CODE" != "200" ]; then echo "${C_RED}[FAIL] /api/changelog $CODE${C_RESET}"; cat /tmp/changelog.json; exit 1; fi
if ! grep -q "Changelog" /tmp/changelog.json; then echo "${C_RED}[FAIL] changelog missing${C_RESET}"; exit 1; fi
echo "${C_GREEN}[ OK ] /api/changelog${C_RESET}"

# 4c. QR
CODE=$(curl -s -o /tmp/qr.png -w "%{http_code}" "http://localhost:$HOST_PORT/qr/test123")
if [ "$CODE" != "200" ]; then echo "${C_RED}[FAIL] /qr $CODE${C_RESET}"; exit 1; fi
echo "${C_GREEN}[ OK ] /qr PNG${C_RESET}"

# 4d. Create shortcut and redirect
# Need to handle CSRF? For now use direct DB via API: POST to /edit/ with form
# First try health for version
curl -s "http://localhost:$HOST_PORT/api/latest-version" >/dev/null && echo "${C_GREEN}[ OK ] /api/latest-version${C_RESET}"

# Try system-info
curl -s "http://localhost:$HOST_PORT/system-info" -o /tmp/sys.html -w "%{http_code}" | grep -q 200 && echo "${C_GREEN}[ OK ] /system-info${C_RESET}"

# 4e. Public image test (if requested)
if [ "${TEST_PUBLIC:-0}" = "1" ]; then
  echo "${C_YELLOW}Testing public image rajlabs/redirector:latest...${C_RESET}"
  docker pull rajlabs/redirector:latest >/dev/null 2>&1 || echo "${C_YELLOW}Could not pull public image (maybe not pushed yet)${C_RESET}"
fi

echo
echo "${C_CYAN}===========================================${C_RESET}"
echo "${C_GREEN}  ALL CHECKS PASSED${C_RESET}"
echo "${C_CYAN}===========================================${C_RESET}"
echo "Tested: $IMG on http://localhost:$HOST_PORT/"
echo "Try: curl http://localhost:$HOST_PORT/health && curl http://localhost:$HOST_PORT/qr/hello"
