#!/usr/bin/env bats

setup() {
    # Move to the EthPillar directory where the scripts live
    cd "$BATS_TEST_DIRNAME/.."
    
    # Create mock env file if it doesn't exist to prevent 'source ./env' from failing
    if [ ! -f ./env ]; then
        touch ./env
        MOCKED_ENV=true
    fi

    # Create a temporary file to capture mocked command outputs
    export COMMAND_LOG=$(mktemp)

    # Create a temporary mock service file for our tests
    export EXEC_SERVICE_FILE=$(mktemp)

    # Mock 'sudo' to just echo its arguments to our log file
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

    # Mock 'clear'
    clear() { return 0; }
    export -f clear

    # Mock 'promptViewLogs' because promptYesNo calls it, though we mock promptYesNo anyway
    promptViewLogs() { return 0; }
    export -f promptViewLogs

    # We need to source resync_execution.sh without it running its main routine.
    # It calls 'getClient' and 'promptYesNo' at the end.
    # We will mock them to do nothing during source, but we will test resyncClient directly.
    promptYesNo() { :; }
    export -f promptYesNo

    getClient() { :; }
    export -f getClient

    # Source the scripts
    source ./functions.sh
    source ./resync_execution.sh

    # Clear the log since sourcing resync_execution.sh automatically executes promptYesNo
    > "$COMMAND_LOG"
}

teardown() {
    rm -f "$COMMAND_LOG"
    rm -f "$EXEC_SERVICE_FILE"
    if [ "${MOCKED_ENV:-false}" = "true" ]; then
        rm -f ./env
    fi
}

@test "getExecutionDatadir returns default empty string if no config is found" {
    getExecutionDatadir
    [ -z "$DATADIR" ]
}

@test "getExecutionDatadir correctly parses custom datadir from systemd file" {
    cat <<EOF > "$EXEC_SERVICE_FILE"
[Service]
ExecStart=/usr/local/bin/reth node --datadir=/mnt/nvme/reth_db --http
EOF
    getExecutionDatadir
    [ "$DATADIR" = "/mnt/nvme/reth_db" ]
}

@test "getExecutionStaticFiles correctly parses static files from systemd file" {
    cat <<EOF > "$EXEC_SERVICE_FILE"
[Service]
ExecStart=/usr/local/bin/reth node --datadir=/mnt/nvme/reth_db --datadir.static-files=/mnt/hdd/static --http
EOF
    getExecutionStaticFiles
    [ "$STATIC_FILES" = "/mnt/hdd/static" ]
}

@test "resyncClient Reth Mainnet (Snapshot YES) uses custom datadir and static files" {
    # Arrange
    EL="Reth"
    # Mock network function to explicitly set network
    getNetwork() { NETWORK="Mainnet"; } 
    
    cat <<EOF > "$EXEC_SERVICE_FILE"
[Service]
ExecStart=/usr/local/bin/reth node --datadir=/custom/db --datadir.static-files=/custom/static
EOF

    WHIPTAIL_EXIT_CODE=0 # User says Yes to snapshot

    # Act
    resyncClient

    # Assert
    run cat "$COMMAND_LOG"
    
    # It should stop execution
    [[ "$output" == *"sudo systemctl stop execution"* ]]
    
    # It should remove both dirs
    [[ "$output" == *"sudo rm -rf /custom/db/"* ]]
    [[ "$output" == *"sudo rm -rf /custom/static/"* ]]
    
    # It should call download with the correct args
    [[ "$output" == *"sudo reth download --chain=mainnet --datadir=/custom/db --datadir.static-files=/custom/static"* ]]
    
    # It should chown both dirs
    [[ "$output" == *"sudo chown -R execution:execution /custom/db"* ]]
    [[ "$output" == *"sudo chown -R execution:execution /custom/static"* ]]
    
    # It should restart
    [[ "$output" == *"sudo systemctl restart execution"* ]]
}

@test "resyncClient Reth Mainnet (Snapshot NO) performs standard P2P sync" {
    EL="Reth"
    getNetwork() { NETWORK="Mainnet"; } 
    
    cat <<EOF > "$EXEC_SERVICE_FILE"
[Service]
ExecStart=/usr/local/bin/reth node --datadir=/custom/db
EOF

    WHIPTAIL_EXIT_CODE=1 # User says No to snapshot

    resyncClient

    run cat "$COMMAND_LOG"
    
    [[ "$output" == *"sudo systemctl stop execution"* ]]
    [[ "$output" == *"sudo rm -rf /custom/db/"* ]]
    
    # It should NOT call download
    [[ "$output" != *"sudo reth download"* ]]
    
    # It should NOT chown
    [[ "$output" != *"sudo chown"* ]]
    
    [[ "$output" == *"sudo systemctl restart execution"* ]]
}

@test "resyncClient Reth Holesky skips snapshot and performs standard P2P sync" {
    EL="Reth"
    getNetwork() { NETWORK="Holesky"; } 
    
    cat <<EOF > "$EXEC_SERVICE_FILE"
[Service]
ExecStart=/usr/local/bin/reth node --datadir=/custom/holesky
EOF

    # Whiptail exit code doesn't matter, it shouldn't be called
    WHIPTAIL_EXIT_CODE=0 

    resyncClient

    run cat "$COMMAND_LOG"
    
    [[ "$output" == *"sudo systemctl stop execution"* ]]
    [[ "$output" == *"sudo rm -rf /custom/holesky/"* ]]
    
    # It should NOT call download because chain is not mainnet
    [[ "$output" != *"sudo reth download"* ]]
    
    # Whiptail should NOT be called
    [[ "$output" != *"whiptail"* ]]
    
    [[ "$output" == *"sudo systemctl restart execution"* ]]
}
