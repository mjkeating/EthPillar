#!/usr/bin/env bash
# Run install.sh smoke tests in two isolated Docker containers:
#   1) simulated curl | bash  -> ~/git/ethpillar
#   2) clone to custom path   -> /opt/ethpillar-custom
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${ETHPILLAR_TEST_IMAGE:-ethpillar-rebuild}"

echo "Building ${IMAGE}..."
docker build -t "${IMAGE}" -f "${ROOT}/tests/integration/Dockerfile.test" "${ROOT}"

run_case() {
  local name="$1"
  local script="$2"
  echo ""
  echo "=== Install smoke: ${name} ==="
  docker run --rm \
    -e DEBIAN_FRONTEND=noninteractive \
    -e ETHPILLAR_INSTALL_NONINTERACTIVE=1 \
    -e ETHPILLAR_INSTALL_COPY_FROM=/ethpillar \
    -v "${ROOT}:/ethpillar:ro" \
    "${IMAGE}" \
    bash "${script}"
}

run_case "curl one-liner (simulated)" /ethpillar/tests/integration/install_smoke/run_curl_install.sh
run_case "clone then install (custom path)" /ethpillar/tests/integration/install_smoke/run_clone_install.sh

echo ""
echo "All install smoke tests passed."
