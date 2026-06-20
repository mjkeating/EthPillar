#!/usr/bin/env bats

setup() {
    cd "$BATS_TEST_DIRNAME/.."
    export COMMAND_LOG=$(mktemp)

    export SYSTEMD_DIR=$(mktemp -d)
    export BASE_DATA_DIR=$(mktemp -d)
    export EL_RPC_ENDPOINT="http://localhost:8545"
    export CL_REST_ENDPOINT="http://localhost:5052"
    export CL_IP_ADDRESS=127.0.0.1
    export CL_REST_PORT=5052

    # Source the scripts first so we can override their functions
    source ./functions.sh
    source ./switch_client.sh

    # Mock 'sudo'
    sudo() {
        echo "sudo $@" >> "$COMMAND_LOG"
    }
    export -f sudo

    # Mock 'whiptail' (--msgbox always succeeds; yesno uses WHIPTAIL_EXIT_CODE)
    whiptail() {
        echo "whiptail $@" >> "$COMMAND_LOG"
        if [[ "$*" == *"--msgbox"* ]]; then
            return 0
        fi
        return $WHIPTAIL_EXIT_CODE
    }
    export -f whiptail

    # Log vc_service patch calls for separate-VC coordination tests
    python3() {
        if [[ "$*" == *"deploy.vc_service"* ]]; then
            echo "python3 $*" >> "$COMMAND_LOG"
        fi
        command python3 "$@"
    }
    export -f python3
    export WHIPTAIL_EXIT_CODE=0 # Default to user clicking "Yes"

    # Mock 'runScript'
    runScript() {
        echo "runScript $@" >> "$COMMAND_LOG"
    }
    export -f runScript

    # Mock 'getNetwork'
    getNetwork() { NETWORK="Mainnet"; }
    export -f getNetwork
    
    # Mock 'getClient' since the real one reads /etc/systemd/system/execution.service
    getClient() { :; }
    export -f getClient
    
    # Mock 'getExecutionDatadir' since Reth uses it
    getExecutionDatadir() { :; }
    export -f getExecutionDatadir

    > "$COMMAND_LOG"
}

write_prysm_validator_service() {
    cat > "${SYSTEMD_DIR}/validator.service" <<EOF
[Unit]
Description=Prysm Validator Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/prysm-validator \\
    --mainnet \\
    --beacon-rest-api-provider=http://127.0.0.1:5052 \\
    --accept-terms-of-use
EOF
}

teardown() {
    rm -f "$COMMAND_LOG"
    rm -rf "$SYSTEMD_DIR"
    rm -rf "$BASE_DATA_DIR"
}

@test "switchClient execution (Yes to backup, Yes to remove)" {
    export EL="Nethermind"
    export CL="Teku"
    
    # Create fake systemd service
    touch "${SYSTEMD_DIR}/execution.service"
    
    # Create fake datadir
    mkdir -p "${BASE_DATA_DIR}/nethermind"
    
    WHIPTAIL_EXIT_CODE=0 # Yes

    switchClient execution

    run cat "$COMMAND_LOG"
    
    # Check backup
    [[ "$output" == *"sudo cp ${SYSTEMD_DIR}/execution.service ${SYSTEMD_DIR}/execution.service.bak"* ]]
    
    # Check remove datadir
    [[ "$output" == *"sudo rm -rf ${BASE_DATA_DIR}/nethermind"* ]]
    
    # Check stop service
    [[ "$output" == *"sudo systemctl stop execution"* ]]
    
    # Check deploy script call
    [[ "$output" == *"runScript deploy/install-node.sh deploy/deploy-node.py --switch_client execution --cc Teku --network Mainnet"* ]]
}

@test "switchClient consensus (No to backup, No to remove)" {
    export EL="Geth"
    export CL="Lighthouse"
    
    # Create fake systemd service
    touch "${SYSTEMD_DIR}/consensus.service"
    
    # Create fake datadir
    mkdir -p "${BASE_DATA_DIR}/lighthouse"
    
    WHIPTAIL_EXIT_CODE=1 # No

    switchClient consensus

    run cat "$COMMAND_LOG"
    
    # Should NOT backup
    [[ "$output" != *"sudo cp ${SYSTEMD_DIR}/consensus.service"* ]]
    
    # Should NOT remove datadir
    [[ "$output" != *"sudo rm -rf ${BASE_DATA_DIR}/lighthouse"* ]]
    
    # Should still stop service
    [[ "$output" == *"sudo systemctl stop consensus"* ]]
    
    # Check deploy script call (MEV flag included when host has mevboost.service)
    [[ "$output" == *"runScript deploy/install-node.sh deploy/deploy-node.py --switch_client consensus --ec Geth"* ]]
    [[ "$output" == *"--network Mainnet"* ]]
}

@test "switchClient skips backup if service file does not exist" {
    export EL="Nethermind"
    export CL="Teku"
    
    # Do NOT create fake systemd service
    
    # Create fake datadir
    mkdir -p "${BASE_DATA_DIR}/nethermind"
    
    WHIPTAIL_EXIT_CODE=0 # Yes

    switchClient execution

    run cat "$COMMAND_LOG"
    
    # Should not even prompt for backup
    [[ "$output" != *"Backup existing"* ]]
    
    # Should backup NOTHING
    [[ "$output" != *"sudo cp"* ]]
    
    # Should still remove datadir
    [[ "$output" == *"sudo rm -rf ${BASE_DATA_DIR}/nethermind"* ]]
}

@test "switchClient skips remove if datadir does not exist" {
    export EL="Nethermind"
    export CL="Teku"
    
    # Create fake systemd service
    touch "${SYSTEMD_DIR}/execution.service"
    
    # Do NOT create fake datadir
    
    WHIPTAIL_EXIT_CODE=0 # Yes

    switchClient execution

    run cat "$COMMAND_LOG"
    
    # Should backup
    [[ "$output" == *"sudo cp ${SYSTEMD_DIR}/execution.service ${SYSTEMD_DIR}/execution.service.bak"* ]]
    
    # Should not even prompt for remove
    [[ "$output" != *"Remove existing client data"* ]]
    
    # Should remove NOTHING
    [[ "$output" != *"sudo rm -rf"* ]]
}

@test "switchClient execution with Reth datadir fallback" {
    export EL="Reth"
    export CL="Lighthouse"
    
    # We do not define DATADIR so getExecutionDatadir leaves it empty, fallback triggers
    mkdir -p "${BASE_DATA_DIR}/reth"
    
    WHIPTAIL_EXIT_CODE=0 # Yes

    switchClient execution

    run cat "$COMMAND_LOG"
    
    # Check remove datadir matches fallback
    [[ "$output" == *"sudo rm -rf ${BASE_DATA_DIR}/reth"* ]]
}

@test "switchClient execution with systemd scraping fallback" {
    export EL="Nethermind"
    export CL="Teku"
    
    # Mock getNetwork to return Network Syncing
    getNetwork() { NETWORK="Network Syncing"; }
    
    # Create fake systemd service with network in Description
    echo "Description=Nethermind Execution Layer Client service for HOLESKY" > "${SYSTEMD_DIR}/execution.service"
    
    WHIPTAIL_EXIT_CODE=0 # Yes

    switchClient execution

    run cat "$COMMAND_LOG"
    
    # Check deploy script call has HOLESKY
    [[ "$output" == *"runScript deploy/install-node.sh deploy/deploy-node.py --switch_client execution --cc Teku --network HOLESKY"* ]]
}

@test "switchClient consensus removes Grandine datadir" {
    export EL="Geth"
    export CL="Grandine"
    
    # Create fake systemd service
    touch "${SYSTEMD_DIR}/consensus.service"
    
    # Create fake datadir
    mkdir -p "${BASE_DATA_DIR}/grandine"
    
    WHIPTAIL_EXIT_CODE=0 # Yes

    switchClient consensus

    run cat "$COMMAND_LOG"
    
    # Check remove datadir
    [[ "$output" == *"sudo rm -rf ${BASE_DATA_DIR}/grandine"* ]]
}

@test "switchClient consensus with separate VC stops validator before consensus switch" {
    export EL="Geth"
    export CL="Prysm"

    touch "${SYSTEMD_DIR}/consensus.service"
    write_prysm_validator_service

    WHIPTAIL_EXIT_CODE=1 # No backup/remove prompts

    switchClient consensus

    run cat "$COMMAND_LOG"

    [[ "$output" == *"sudo systemctl stop validator"* ]]
    [[ "$output" == *"sudo systemctl stop consensus"* ]]

    validator_stop_line=$(grep -n "sudo systemctl stop validator" <<< "$output" | head -1 | cut -d: -f1)
    consensus_stop_line=$(grep -n "sudo systemctl stop consensus" <<< "$output" | head -1 | cut -d: -f1)
    [ "$validator_stop_line" -lt "$consensus_stop_line" ]
}

@test "switchClient consensus with separate VC patches beacon endpoint and restarts validator" {
    export EL="Geth"
    export CL="Prysm"

    touch "${SYSTEMD_DIR}/consensus.service"
    write_prysm_validator_service

    WHIPTAIL_EXIT_CODE=1

    switchClient consensus

    run cat "$COMMAND_LOG"
    [[ "$output" == *"deploy.vc_service patch"* ]]
    [[ "$output" == *"sudo systemctl daemon-reload"* ]]
    [[ "$output" == *"sudo systemctl start validator"* ]]

    grep -q "beacon-rest-api-provider=http://127.0.0.1:5052" "${SYSTEMD_DIR}/validator.service"
}

@test "switchClient consensus auto with separate VC starts consensus after patch" {
    export EL="Geth"
    export CL="Prysm"

    touch "${SYSTEMD_DIR}/consensus.service"
    write_prysm_validator_service

    switchClient consensus --auto --target-client Lighthouse

    run cat "$COMMAND_LOG"
    [[ "$output" == *"runScript deploy/install-node.sh deploy/deploy-node.py --switch_client consensus --ec Geth"* ]]
    [[ "$output" == *"--cc Lighthouse"* ]]
    [[ "$output" == *"deploy.vc_service patch"* ]]
    [[ "$output" == *"sudo systemctl start consensus"* ]]
    [[ "$output" == *"sudo systemctl start validator"* ]]
}

@test "switchClient consensus without validator does not coordinate VC lifecycle" {
    export EL="Geth"
    export CL="Lighthouse"

    touch "${SYSTEMD_DIR}/consensus.service"

    WHIPTAIL_EXIT_CODE=1

    switchClient consensus

    run cat "$COMMAND_LOG"
    [[ "$output" != *"sudo systemctl stop validator"* ]]
    [[ "$output" != *"deploy.vc_service patch"* ]]
    [[ "$output" != *"sudo systemctl start validator"* ]]
}
