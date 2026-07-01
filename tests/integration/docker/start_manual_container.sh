#!/usr/bin/env bash
# Start a long-lived systemd container for manual TUI / deploy testing.
#
# Keep this script executable in git (100755). On Windows: git add --chmod=+x this file.
set -euo pipefail

IMAGE="${ETHPILLAR_TEST_IMAGE:-ethpillar-test}"
NAME="${ETHPILLAR_MANUAL_CONTAINER:-ep-manual}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
UID_VAL="$(id -u)"
GID_VAL="$(id -g)"

docker rm -f "${NAME}" 2>/dev/null || true

docker run -d --name "${NAME}" \
  --privileged --cgroupns=host \
  --tmpfs /run --tmpfs /run/lock \
  -e "ETHPILLAR_INTEGRATION_UID=${UID_VAL}" \
  -e "ETHPILLAR_INTEGRATION_GID=${GID_VAL}" \
  -e ETHPILLAR_INTEGRATION_USER=epstaker \
  -v "${REPO_ROOT}:/ethpillar" \
  "${IMAGE}"

echo "Waiting for systemd to initialize..."
sleep 3
echo ""
echo "Attach as non-root (recommended):"
echo "  docker exec -it ${NAME} /ethpillar/tests/integration/docker/manual_shell.sh"
echo ""
echo "Remove when finished:"
echo "  docker rm -f ${NAME}"
