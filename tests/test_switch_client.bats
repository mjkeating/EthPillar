#!/usr/bin/env bats

setup() {
    cd "$BATS_TEST_DIRNAME/.."
    export COMMAND_LOG=$(mktemp)

    export SYSTEMD_DIR=$(mktemp -d)
    export BASE_DATA_DIR=$(mktemp -d)

    # Mock 'sudo'
    sudo() {
        echo "sudo $@" >> "$COMMAND_LOG"
    }
    export -f sudo

    # Mock 'whiptail'
    whiptail() {
        echo "whiptail $@" >> "$COMMAND_LOG"
        return $WHIPTAIL_EXIT_CODE
    }
    export -f whiptail
    export WHIPTAIL_EXIT_CODE=0 # Default to user clicking "Yes"

    # Mock 'runScript'
    runScript() {
        echo "runScript $@" >> "$COMMAND_LOG"
    }
    export -f runScript

    # We need to source switch_client.sh without it running its main routine.
    # It calls 'getClient' and 'switchClient $1' at the end but only if BASH_SOURCE matches 0.
    
    # Mock 'getClient' since the real one reads /etc/systemd/system/execution.service
    getClient() { :; }
    export -f getClient
    
    # Mock 'getExecutionDatadir' since Reth uses it
    getExecutionDatadir() { :; }
    export -f getExecutionDatadir

    # Source the scripts
    source ./functions.sh
    source ./switch_client.sh

    > "$COMMAND_LOG"
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
    [[ "$output" == *"runScript deploy/install-node.sh deploy/deploy-node.py --switch_client execution --cc Teku"* ]]
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
    
    # Check deploy script call
    [[ "$output" == *"runScript deploy/install-node.sh deploy/deploy-node.py --switch_client consensus --ec Geth"* ]]
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
