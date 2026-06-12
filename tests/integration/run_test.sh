#!/bin/bash
# Bootstrap integration tests through the same Python dependency path as production.
set -euo pipefail

cd /ethpillar
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
        if curl -fsS "http://127.0.0.1:19595/eth/v1/node/version" >/dev/null 2>&1; then
            echo "[checkpoint] Local cache proxy ready for ${NETWORK}"
            break
        fi
        sleep 0.25
    done
fi

exec python3 /ethpillar/tests/integration/run_inside_docker.py "$@"
