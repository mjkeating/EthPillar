#!/bin/bash
# Non-interactive RPC bind helper for integration tests.
# Uses production _updateFlagAndRestartService from functions.sh.
set -euo pipefail

cd /ethpillar
source "${ETHPILLAR_ENV_FILE:-/ethpillar/env}"
source /ethpillar/functions.sh

SERVICE="${1:?service name required (execution|consensus)}"
BIND_ADDR="${2:?bind address required (127.0.0.1|0.0.0.0)}"

getClient
if [[ "${EL}" == "Erigon-Caplin" ]]; then
    EL="Erigon"
fi

_service="${SERVICE}"
_file="/etc/systemd/system/${_service}.service"
_value="${BIND_ADDR}"

if [[ "${SERVICE}" == "execution" ]]; then
    case "${EL}" in
        Nethermind ) _flag='--JsonRpc.Host';;
        Besu       ) _flag='--rpc-http-host';;
        Erigon     ) _flag='--http.addr';;
        Geth       ) _flag='--http.addr';;
        Reth       ) _flag='--http.addr';;
        Ethrex     ) _flag='--http.addr';;
        * ) echo "Unsupported execution client for RPC bind: ${EL:-unknown}" >&2; exit 1;;
    esac
elif [[ "${SERVICE}" == "consensus" ]]; then
    case "${CL}" in
        Nimbus     ) _flag='--rest-address';;
        Lodestar   ) _flag='--rest.address';;
        Lighthouse ) _flag='--http-address';;
        Prysm      ) _flag='--http-host';;
        Teku       ) _flag='--rest-api-interface';;
        * ) echo "Unsupported consensus client for RPC bind: ${CL:-unknown}" >&2; exit 1;;
    esac
else
    echo "Unknown service: ${SERVICE}" >&2
    exit 1
fi

_updateFlagAndRestartService
