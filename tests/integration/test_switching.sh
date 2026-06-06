#!/bin/bash
# EthPillar Client Switching Integration Test
# Runs inside the Docker container after the node is deployed.

set -e

cd /ethpillar
source /ethpillar/env
: "${EL_IP_ADDRESS:=127.0.0.1}"
: "${EL_RPC_PORT:=8545}"
export EL_RPC_ENDPOINT="http://${EL_IP_ADDRESS}:${EL_RPC_PORT}"

source /ethpillar/functions.sh
getClient

# Pick new execution client
if [ "$EL" == "Reth" ]; then NEW_EL="Besu"
elif [ "$EL" == "Besu" ]; then NEW_EL="Geth"
elif [ "$EL" == "Geth" ]; then NEW_EL="Nethermind"
elif [ "$EL" == "Nethermind" ]; then NEW_EL="Erigon"
elif [ "$EL" == "Erigon" ]; then NEW_EL="Reth"
else NEW_EL="Besu"; fi

# Pick new consensus client
if [ "$CL" == "Lighthouse" ]; then NEW_CL="Teku"
elif [ "$CL" == "Teku" ]; then NEW_CL="Nimbus"
elif [ "$CL" == "Nimbus" ]; then NEW_CL="Lodestar"
elif [ "$CL" == "Lodestar" ]; then NEW_CL="Prysm"
elif [ "$CL" == "Prysm" ]; then NEW_CL="Grandine"
elif [ "$CL" == "Grandine" ]; then NEW_CL="Caplin"
elif [ "$CL" == "Caplin" ]; then NEW_CL="Lighthouse"
else NEW_CL="Teku"; fi

echo "========================================="
echo " Starting Client Switching Integration Test"
echo "========================================="
echo "Current EL: $EL -> Switching to: $NEW_EL"
echo "Current CL: $CL -> Switching to: $NEW_CL"

echo "Testing execution client switch..."
bash /ethpillar/switch_client.sh execution --auto --target-client "$NEW_EL"

echo "Verifying new execution client health ($NEW_EL)..."
python3 /ethpillar/tests/integration/run_inside_docker.py verify-service-health --service execution

echo "Testing consensus client switch..."
bash /ethpillar/switch_client.sh consensus --auto --target-client "$NEW_CL"

echo "Verifying new consensus client health ($NEW_CL)..."
python3 /ethpillar/tests/integration/run_inside_docker.py verify-service-health --service consensus

echo "========================================="
echo " Verifying post-switch artifacts"
echo "========================================="

# 1. Verify systemd backups
if [ ! -f "/etc/systemd/system/execution.service.bak" ]; then
    echo "❌ execution.service.bak not found. Original EL unit file was not backed up!"
    exit 1
else
    echo "✅ execution.service.bak found."
fi

if [ ! -f "/etc/systemd/system/consensus.service.bak" ]; then
    echo "❌ consensus.service.bak not found. Original CL unit file was not backed up!"
    exit 1
else
    echo "✅ consensus.service.bak found."
fi

# 2. Verify data directories were wiped (convert to lowercase)
OLD_EL_DIR="/var/lib/${EL,,}"
OLD_CL_DIR="/var/lib/${CL,,}"

# Erigon-Caplin shares the erigon dir
if [ "${CL}" == "Caplin" ]; then OLD_CL_DIR="/var/lib/erigon"; fi
if [ "${EL}" == "Erigon" ]; then OLD_EL_DIR="/var/lib/erigon"; fi

if [ -d "$OLD_EL_DIR" ]; then
    echo "❌ Old EL datadir ($OLD_EL_DIR) still exists!"
    exit 1
else
    echo "✅ Old EL datadir ($OLD_EL_DIR) was correctly wiped."
fi

if [ -d "$OLD_CL_DIR" ]; then
    echo "❌ Old CL datadir ($OLD_CL_DIR) still exists!"
    exit 1
else
    echo "✅ Old CL datadir ($OLD_CL_DIR) was correctly wiped."
fi

echo "========================================="
echo " Client Switching completed successfully!"
echo "========================================="
