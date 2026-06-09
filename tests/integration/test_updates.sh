#!/bin/bash
# EthPillar Update Scripts Integration Test
# Runs inside the Docker container after the node is deployed.

set -e

cd /ethpillar
source "${ETHPILLAR_ENV_FILE:-/ethpillar/env}"
: "${EL_IP_ADDRESS:=127.0.0.1}"
: "${EL_RPC_PORT:=8545}"
export EL_RPC_ENDPOINT="http://${EL_IP_ADDRESS}:${EL_RPC_PORT}"

function check_binary() {
    local path="$1"
    if [[ ! -f "$path" && ! -d "$path" ]]; then
        echo "❌ Binary not found at expected path: $path"
        return 1
    fi
    echo "✅ Binary verified: $path"
}

function check_service_health() {
    local service="$1"
    echo "  [Integration] Delegating health check for $service to run_inside_docker.py..."
    bash /ethpillar/tests/integration/run_test.sh verify-service-health --service "$service"
}

echo "========================================="
echo " Starting Update Scripts Integration Test"
echo "========================================="

# Test Execution Client Update
if systemctl list-unit-files execution.service > /dev/null 2>&1; then
    echo "Testing execution client update..."
    
    # Capture old PID
    old_pid=$(systemctl show -p MainPID --value execution 2>/dev/null || echo "0")
    
    bash /ethpillar/update_execution.sh --auto
    
    # Verify binary and service after update
    exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/execution.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
    check_binary "$exec_path"
    check_service_health execution
    
    # Verify PID changed
    new_pid=$(systemctl show -p MainPID --value execution 2>/dev/null || echo "0")
    if [[ "$old_pid" != "0" && "$old_pid" == "$new_pid" ]]; then
        echo "❌ Execution service PID did not change ($old_pid). Service was not restarted!"
        exit 1
    fi
    
    echo "✅ Execution client update completed successfully."
else
    echo "No execution client installed. Skipping."
fi

# Test Consensus Client Update
if systemctl list-unit-files consensus.service > /dev/null 2>&1 || systemctl list-unit-files validator.service > /dev/null 2>&1; then
    echo "Testing consensus/validator client update..."
    
    old_cc_pid=$(systemctl show -p MainPID --value consensus 2>/dev/null || echo "0")
    old_vc_pid=$(systemctl show -p MainPID --value validator 2>/dev/null || echo "0")
    
    bash /ethpillar/update_consensus.sh --auto
    
    # Verify binary and service after update
    if systemctl list-unit-files consensus.service > /dev/null 2>&1; then
        exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/consensus.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
        check_binary "$exec_path"
        check_service_health consensus
        
        new_cc_pid=$(systemctl show -p MainPID --value consensus 2>/dev/null || echo "0")
        if [[ "$old_cc_pid" != "0" && "$old_cc_pid" == "$new_cc_pid" ]]; then
            echo "❌ Consensus service PID did not change. Service was not restarted!"
            exit 1
        fi
    fi
    if systemctl list-unit-files validator.service > /dev/null 2>&1; then
        exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/validator.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
        check_binary "$exec_path"
        check_service_health validator
        
        new_vc_pid=$(systemctl show -p MainPID --value validator 2>/dev/null || echo "0")
        if [[ "$old_vc_pid" != "0" && "$old_vc_pid" == "$new_vc_pid" ]]; then
            echo "❌ Validator service PID did not change. Service was not restarted!"
            exit 1
        fi
    fi
    echo "✅ Consensus client update completed successfully."
else
    echo "No consensus client installed. Skipping."
fi

# Test MEV-Boost Update
if systemctl list-unit-files mevboost.service > /dev/null 2>&1; then
    echo "Testing MEV-Boost update..."
    
    old_mev_pid=$(systemctl show -p MainPID --value mevboost 2>/dev/null || echo "0")
    
    bash /ethpillar/update_mevboost.sh --auto
    
    exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/mevboost.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
    check_binary "$exec_path"
    check_service_health mevboost
    
    new_mev_pid=$(systemctl show -p MainPID --value mevboost 2>/dev/null || echo "0")
    if [[ "$old_mev_pid" != "0" && "$old_mev_pid" == "$new_mev_pid" ]]; then
        echo "❌ MEV-Boost service PID did not change. Service was not restarted!"
        exit 1
    fi
    
    echo "✅ MEV-Boost update completed successfully."
else
    echo "No MEV-Boost installed. Skipping."
fi

echo "========================================="
echo " All update scripts ran successfully!"
echo "========================================="