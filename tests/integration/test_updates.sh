#!/bin/bash
# EthPillar Update / CLI Integration Test
# Runs inside the Docker container after the node is deployed.
# Exercises the non-interactive ethpillar CLI (upgrade, status, lifecycle).

set -e

cd /ethpillar
source "${ETHPILLAR_ENV_FILE:-/ethpillar/env}"
: "${EL_IP_ADDRESS:=127.0.0.1}"
: "${EL_RPC_PORT:=8545}"
export EL_RPC_ENDPOINT="http://${EL_IP_ADDRESS}:${EL_RPC_PORT}"

ETHPILLAR=(bash /ethpillar/ethpillar.sh)

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

function assert_status_active() {
    echo "Checking ethpillar status (expect active)..."
    "${ETHPILLAR[@]}" status
    echo "✅ ethpillar status: all installed clients active"
}

echo "========================================="
echo " Starting EthPillar CLI Integration Test"
echo "========================================="

echo "Help output (install-aware)..."
"${ETHPILLAR[@]}" --help | head -40

# Test Execution Client Update via CLI
if systemctl list-unit-files execution.service > /dev/null 2>&1; then
    echo "Testing execution client upgrade via CLI..."

    old_pid=$(systemctl show -p MainPID --value execution 2>/dev/null || echo "0")

    "${ETHPILLAR[@]}" upgrade execution

    exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/execution.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
    check_binary "$exec_path"
    check_service_health execution

    new_pid=$(systemctl show -p MainPID --value execution 2>/dev/null || echo "0")
    if [[ "$old_pid" != "0" && "$old_pid" == "$new_pid" ]]; then
        echo "❌ Execution service PID did not change ($old_pid). Service was not restarted!"
        exit 1
    fi

    echo "✅ Execution client upgrade completed successfully."

    echo "Verifying execution client version parsing..."
    python3 /ethpillar/tests/integration/latest_snapshot.py clear
    bash /ethpillar/tests/integration/check_client_versions.sh
else
    echo "No execution client installed. Skipping."
fi

# Test Consensus / Validator Client Update via CLI
# Prefer consensus (BN updater also restarts a separate VC when present).
# Validator-only nodes fall back to the validator target.
if systemctl list-unit-files consensus.service > /dev/null 2>&1 || systemctl list-unit-files validator.service > /dev/null 2>&1; then
    echo "Testing consensus/validator client upgrade via CLI..."

    old_cc_pid=$(systemctl show -p MainPID --value consensus 2>/dev/null || echo "0")
    old_vc_pid=$(systemctl show -p MainPID --value validator 2>/dev/null || echo "0")

    if systemctl list-unit-files consensus.service > /dev/null 2>&1; then
        "${ETHPILLAR[@]}" upgrade consensus
    else
        "${ETHPILLAR[@]}" upgrade validator
    fi

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
    echo "✅ Consensus/validator upgrade completed successfully."

    echo "Verifying consensus/validator client version parsing..."
    python3 /ethpillar/tests/integration/latest_snapshot.py clear
    bash /ethpillar/tests/integration/check_client_versions.sh
else
    echo "No consensus client installed. Skipping."
fi

# Test MEV-Boost Update via CLI
if systemctl list-unit-files mevboost.service > /dev/null 2>&1; then
    echo "Testing MEV-Boost upgrade via CLI..."

    old_mev_pid=$(systemctl show -p MainPID --value mevboost 2>/dev/null || echo "0")

    "${ETHPILLAR[@]}" upgrade mevboost

    exec_path=$(grep -E "^ExecStart=" /etc/systemd/system/mevboost.service | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
    check_binary "$exec_path"
    check_service_health mevboost

    new_mev_pid=$(systemctl show -p MainPID --value mevboost 2>/dev/null || echo "0")
    if [[ "$old_mev_pid" != "0" && "$old_mev_pid" == "$new_mev_pid" ]]; then
        echo "❌ MEV-Boost service PID did not change. Service was not restarted!"
        exit 1
    fi

    echo "✅ MEV-Boost upgrade completed successfully."
else
    echo "No MEV-Boost installed. Skipping."
fi

# Status + lifecycle smoke (CLI start/stop/restart)
if systemctl list-unit-files execution.service > /dev/null 2>&1 \
    || systemctl list-unit-files consensus.service > /dev/null 2>&1; then
    echo "========================================="
    echo " CLI status / lifecycle smoke"
    echo "========================================="

    assert_status_active

    echo "Running check-updates (informational)..."
    set +e
    "${ETHPILLAR[@]}" check-updates
    check_rc=$?
    set -e
    if [[ "$check_rc" -eq 1 ]]; then
        echo "❌ ethpillar check-updates failed with error"
        exit 1
    fi
    echo "✅ ethpillar check-updates completed (exit $check_rc; 0=current 2=updates available)"

    echo "Stopping all clients via CLI..."
    "${ETHPILLAR[@]}" stop all
    if "${ETHPILLAR[@]}" status; then
        echo "❌ ethpillar status succeeded after stop (expected non-zero)"
        exit 1
    fi
    echo "✅ ethpillar status correctly reports inactive after stop"

    echo "Starting all clients via CLI..."
    "${ETHPILLAR[@]}" start all
    # Allow units a moment to enter active
    sleep 2
    assert_status_active

    # Re-verify health after lifecycle
    for svc in execution consensus validator mevboost charon; do
        if systemctl list-unit-files "${svc}.service" > /dev/null 2>&1; then
            check_service_health "$svc"
        fi
    done

    echo "Restarting all clients via CLI..."
    "${ETHPILLAR[@]}" restart all
    sleep 2
    assert_status_active
fi

echo "========================================="
echo " All ethpillar CLI update/lifecycle tests passed!"
echo "========================================="
