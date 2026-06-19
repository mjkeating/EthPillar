#!/usr/bin/env bash
# Populate act-smoke cache dirs via Docker-as-root (no real checkpoint sync / downloads).
# Root-owned 0600 files mirror the CI failure mode that plain echo seeding never reproduced.
#
# Uses tests/integration/act-smoke/{cache,checkpoint_cache} only — never touches the real
# integration cache trees used for local test runs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/act-bind-mount.sh
source "${SCRIPT_DIR}/lib/act-bind-mount.sh"
REPO_ROOT="$(act_repo_root)"
cd "${REPO_ROOT}"

ACT_SMOKE_ROOT="${REPO_ROOT}/tests/integration/act-smoke"
ACT_SMOKE_BINARY="${ACT_SMOKE_BINARY_CACHE_DIR:-${ACT_SMOKE_ROOT}/cache}"
ACT_SMOKE_CHECKPOINT="${ACT_SMOKE_CHECKPOINT_CACHE_DIR:-${ACT_SMOKE_ROOT}/checkpoint_cache}"
REPO_BIND="$(host_bind_source "${REPO_ROOT}")"
ACT_SMOKE_SCRIPTS="/ethpillar/tests/integration/act-smoke"

echo "[act-fixture] Building ethpillar-rebuild image..."
docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .

echo "[act-fixture] Resetting act-smoke cache dirs (isolated from integration caches)..."
rm -rf "${ACT_SMOKE_BINARY}" "${ACT_SMOKE_CHECKPOINT}"
mkdir -p "${ACT_SMOKE_BINARY}" "${ACT_SMOKE_CHECKPOINT}"

echo "[act-fixture] Writing fixtures and verifying runner cannot save (one container)..."
if [[ "${ACT:-}" == "true" ]]; then
  timeout 60 docker run --rm \
    -e "ACT_SMOKE_BINARY_CACHE_DIR=${ACT_SMOKE_SCRIPTS}/cache" \
    -e "ACT_SMOKE_CHECKPOINT_CACHE_DIR=${ACT_SMOKE_SCRIPTS}/checkpoint_cache" \
    -v "${REPO_BIND}:/ethpillar" \
    ethpillar-rebuild \
    bash -c "python3 ${ACT_SMOKE_SCRIPTS}/act-cache-fixture.py && bash ${ACT_SMOKE_SCRIPTS}/act-cache-runner-verify-inline.sh deny"
else
  docker run --rm \
    -e "ACT_SMOKE_BINARY_CACHE_DIR=${ACT_SMOKE_SCRIPTS}/cache" \
    -e "ACT_SMOKE_CHECKPOINT_CACHE_DIR=${ACT_SMOKE_SCRIPTS}/checkpoint_cache" \
    -v "${REPO_BIND}:/ethpillar" \
    ethpillar-rebuild \
    python3 "${ACT_SMOKE_SCRIPTS}/act-cache-fixture.py"
  test -f tests/integration/act-smoke/cache/fixture_act_smoke.bin
  test -f tests/integration/act-smoke/checkpoint_cache/manifest.json
  test -f tests/integration/act-smoke/checkpoint_cache/SEPOLIA/entries/fixture_sepolia.body
fi

echo "[act-fixture] Cache fixture ready."
