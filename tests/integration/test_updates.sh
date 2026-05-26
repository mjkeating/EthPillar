#!/bin/bash
# EthPillar Update Scripts Integration Test
# Runs inside the Docker container after the node is deployed.

set -e

function check_binary() {
    local path="$1"
    if [[ ! -f "$path" && ! -d "$path" ]]; then
        echo "❌ Binary not found at expected path: $path"
        return 1
    fi
    echo "✅ Binary verified: $path"
}

function check_service_active() {
    local service="$1"
    local state
    state=$(systemctl is-active "$service" 2>/dev/null || true)
    if [[ "$state" != "active" && "$state" != "activating" ]]; then
        echo "❌ Service $service is not active after update (state: $state)"
        return 1
    fi
    echo "✅ Service active: $service"
}

echo "========================================="
echo " Starting Update Scripts Integration Test"
echo "========================================="

# Test Execution Client Update
if systemctl list-unit-files execution.service > /dev/null 2>&1; then
    echo "Testing execution client update..."
    bash /ethpillar/update_execution.sh --auto
    # Verify binary and service after update
    exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/execution.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
    check_binary "$exec_path"
    check_service_active execution
    echo "✅ Execution client update completed successfully."
else
    echo "No execution client installed. Skipping."
fi

# Test Consensus Client Update
if systemctl list-unit-files consensus.service > /dev/null 2>&1 || systemctl list-unit-files validator.service > /dev/null 2>&1; then
    echo "Testing consensus/validator client update..."
    bash /ethpillar/update_consensus.sh --auto
    # Verify binary and service after update
    if systemctl list-unit-files consensus.service > /dev/null 2>&1; then
        exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/consensus.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
        check_binary "$exec_path"
        check_service_active consensus
    fi
    if systemctl list-unit-files validator.service > /dev/null 2>&1; then
        exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/validator.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
        check_binary "$exec_path"
        check_service_active validator
    fi
    echo "✅ Consensus client update completed successfully."
else
    echo "No consensus client installed. Skipping."
fi

# Test MEV-Boost Update
if systemctl list-unit-files mevboost.service > /dev/null 2>&1; then
    echo "Testing MEV-Boost update..."
    bash /ethpillar/update_mevboost.sh --auto
    exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/mevboost.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
    check_binary "$exec_path"
    check_service_active mevboost
    echo "✅ MEV-Boost update completed successfully."
else
    echo "No MEV-Boost installed. Skipping."
fi

echo "========================================="
echo " All update scripts ran successfully!"
echo "========================================="