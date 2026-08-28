#!/usr/bin/env bash
# Redirector - Docker image build (test-gated, Tax_Scripts style)
# Usage: ./build_docker_image.sh [--prod] [--yes] [custom-tag]
set -euo pipefail
cd "$(dirname "$0")"

C_RESET=$'\033[0m'
C_GREEN=$'\033[32m'
C_RED=$'\033[31m'
C_YELLOW=$'\033[33m'
C_CYAN=$'\033[36m'

REGISTRY="${RAJLABS_REGISTRY:-rajlabs}"
IMAGE_NAME="${RAJLABS_IMAGE:-redirector}"
IMG="$REGISTRY/$IMAGE_NAME"
TEST_IMG="redirector:test"

PROD=0
YES=0
CUSTOM_TAG=""
for arg in "$@"; do
  case "$arg" in
    --prod) PROD=1 ;;
    --yes) YES=1 ;;
    -*) echo "Unknown option: $arg" >&2; exit 2 ;;
    *) CUSTOM_TAG="$arg" ;;
  esac
done

VERSION_TAG=$(python3 get_version.py --tag 2>/dev/null || cat VERSION 2>/dev/null | tr -d '[:space:]' || echo "test")
DATE_TAG=$(date +%Y.%m.%d)

echo "${C_CYAN}===========================================${C_RESET}"
echo "${C_CYAN}  Redirector - Docker Image Build${C_RESET}"
echo "${C_CYAN}===========================================${C_RESET}"
echo "  Registry: $REGISTRY"
echo "  Image:    $IMG"
echo "  Version:  $VERSION_TAG"
echo "  Mode:     $([ "$PROD" = 1 ] && echo PROD || echo dev)"
echo

echo "${C_YELLOW}[1/3] Running smoke test...${C_RESET}"
if ! ./test_docker.sh; then
  echo "${C_RED}[FAIL] Smoke test failed - images will NOT be tagged.${C_RESET}"
  exit 1
fi
echo "${C_GREEN}[ OK ] Smoke test passed.${C_RESET}"
echo

echo "${C_YELLOW}[2/3] Tagging tested image...${C_RESET}"
docker tag "$TEST_IMG" "$IMG:latest"
docker tag "$TEST_IMG" "$IMG:$DATE_TAG"
docker tag "$TEST_IMG" "$IMG:$VERSION_TAG"
[ -n "$CUSTOM_TAG" ] && docker tag "$TEST_IMG" "$IMG:$CUSTOM_TAG"
docker images "$IMG" --format "  {{.Repository}}:{{.Tag}} ({{.Size}})"
echo "${C_GREEN}[ OK ] Tagged.${C_RESET}"
echo

if [ "$PROD" = 1 ]; then
  if [ "$YES" = 0 ]; then
    echo "${C_YELLOW}Push $IMG to registry? [y/N]${C_RESET}"
    read -r confirm
    case "$confirm" in y|Y|yes|YES) ;; *) echo "Skipping push."; exit 0 ;; esac
  fi
  echo "${C_YELLOW}[3/3] Pushing...${C_RESET}"
  docker push "$IMG:latest"
  docker push "$IMG:$VERSION_TAG"
  [ -n "$CUSTOM_TAG" ] && docker push "$IMG:$CUSTOM_TAG"
  echo "${C_GREEN}[ OK ] Pushed.${C_RESET}"
else
  echo "${C_YELLOW}[3/3] Dev mode - not pushing. Re-run with --prod to publish.${C_RESET}"
fi

echo
echo "${C_CYAN}===========================================${C_RESET}"
echo "${C_GREEN}  BUILD COMPLETE${C_RESET}"
echo "${C_CYAN}===========================================${C_RESET}"
echo "Run: docker run -d -p 80:80 --name redirector $IMG:latest"
echo "Tags: $IMG:latest, $IMG:$VERSION_TAG"
