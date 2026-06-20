#!/usr/bin/env bash
# Widen cache permissions so the non-root CI runner can tar them for actions/cache/save.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/act-bind-mount.sh
source "${SCRIPT_DIR}/lib/act-bind-mount.sh"

REPO_ROOT="$(act_repo_root)"
cd "${REPO_ROOT}"

ACT_SMOKE_ROOT="${REPO_ROOT}/tests/integration/act-smoke"
ACT_SMOKE_BINARY="${ACT_SMOKE_BINARY_CACHE_DIR:-${ACT_SMOKE_ROOT}/cache}"
ACT_SMOKE_CHECKPOINT="${ACT_SMOKE_CHECKPOINT_CACHE_DIR:-${ACT_SMOKE_ROOT}/checkpoint_cache}"
ACT_SMOKE_SCRIPTS="/ethpillar/tests/integration/act-smoke"

if [[ "${ACT:-}" == "true" ]]; then
  REPO_BIND="$(host_bind_source "${REPO_ROOT}")"
  timeout 60 docker run --rm \
    -e "ACT_SMOKE_BINARY_CACHE_DIR=${ACT_SMOKE_SCRIPTS}/cache" \
    -e "ACT_SMOKE_CHECKPOINT_CACHE_DIR=${ACT_SMOKE_SCRIPTS}/checkpoint_cache" \
    -v "${REPO_BIND}:/ethpillar" \
    ethpillar-rebuild \
    bash -c "chmod -R a+rX ${ACT_SMOKE_SCRIPTS}/cache ${ACT_SMOKE_SCRIPTS}/checkpoint_cache && bash ${ACT_SMOKE_SCRIPTS}/act-cache-runner-verify-inline.sh allow"
else
  sudo chmod -R a+rX "${ACT_SMOKE_BINARY}" "${ACT_SMOKE_CHECKPOINT}" 2>/dev/null || true
fi
