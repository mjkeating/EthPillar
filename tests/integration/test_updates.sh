#!/bin/bash
# EthPillar Update Scripts Integration Test
# Runs inside the Docker container after the node is deployed.

set -e

echo "========================================="
echo " Starting Update Scripts Integration Test"
echo "========================================="

# Test Execution Client Update
if systemctl list-unit-files execution.service > /dev/null 2>&1; then
    echo "Testing execution client update..."
    bash /ethpillar/update_execution.sh --auto
    echo "✅ Execution client update completed successfully."
else
    echo "No execution client installed. Skipping."
fi

# Test Consensus Client Update
if systemctl list-unit-files consensus.service > /dev/null 2>&1 || systemctl list-unit-files validator.service > /dev/null 2>&1; then
    echo "Testing consensus/validator client update..."
    bash /ethpillar/update_consensus.sh --auto
    echo "✅ Consensus client update completed successfully."
else
    echo "No consensus client installed. Skipping."
fi

# Test MEV-Boost Update
if systemctl list-unit-files mevboost.service > /dev/null 2>&1; then
    echo "Testing MEV-Boost update..."
    bash /ethpillar/update_mevboost.sh --auto
    echo "✅ MEV-Boost update completed successfully."
else
    echo "No MEV-Boost installed. Skipping."
fi

echo "========================================="
echo " All update scripts ran successfully!"
echo "========================================="
