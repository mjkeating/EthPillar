#!/bin/bash
# Bootstrap integration tests through the same Python dependency path as production.
set -euo pipefail

cd /ethpillar

# docker exec starts as root; drop to a sudo-capable test user (matches production installs).
if [[ "$(id -u)" -eq 0 && "${ETHPILLAR_INTEGRATION_PRIVS_DROPPED:-}" != "1" ]]; then
  # shellcheck source=docker/setup_integration_user.sh
  source tests/integration/docker/setup_integration_user.sh
  ensure_integration_user
  export ETHPILLAR_INTEGRATION_PRIVS_DROPPED=1
  export ETHPILLAR_VENV="${ETHPILLAR_VENV:-/tmp/ethpillar-integration-venv}"
  export HOME="$(getent passwd "${INTEGRATION_USER}" | cut -d: -f6)"
  PYTHONPATH="/ethpillar/tests/integration" python3 -c "from binary_cache_common import ensure_binary_cache_dir_writable; ensure_binary_cache_dir_writable()"
  echo "[integration] Dropping root; running tests as ${INTEGRATION_USER} (uid=${INTEGRATION_UID})"
  exec runuser -u "${INTEGRATION_USER}" -p -- bash "$0" "$@"
fi

# shellcheck source=../../functions.sh
source functions.sh

CHECKPOINT_PROXY_PID=""
cleanup_checkpoint_proxy() {
    if [[ -n "${CHECKPOINT_PROXY_PID}" ]]; then
        kill "${CHECKPOINT_PROXY_PID}" 2>/dev/null || true
    fi
}
trap cleanup_checkpoint_proxy EXIT

NETWORK=""
prev=""
for arg in "$@"; do
    if [[ "${prev}" == "--network" ]]; then
        NETWORK="${arg^^}"
    fi
    prev="${arg}"
done

if [[ -f /ethpillar/tests/integration/checkpoint_cache/manifest.json && -n "${NETWORK}" ]]; then
    export ENABLE_CHECKPOINT_CACHE=1
    python3 /ethpillar/tests/integration/checkpoint_proxy.py --network "${NETWORK}" &
    CHECKPOINT_PROXY_PID=$!
    for _ in $(seq 1 40); do
        if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:19595/eth/v1/node/version', timeout=1)" 2>/dev/null; then
            echo "[checkpoint] Local cache proxy ready for ${NETWORK}"
            break
        fi
        sleep 0.25
    done
fi

exec python3 /ethpillar/tests/integration/run_inside_docker.py "$@"
