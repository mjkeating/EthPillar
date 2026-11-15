#!/usr/bin/env bats
#
# tests/test_ethpillar_version.bats
#
# Tests for ethpillar.sh version / --version (CLI version output).
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export ETHPILLAR_VENV="/tmp/ethpillar_bats_version_venv"
    rm -rf "$ETHPILLAR_VENV"

    export MOCK_BIN_DIR
    MOCK_BIN_DIR=$(mktemp -d)
    export COMMAND_LOG
    COMMAND_LOG=$(mktemp)
    export TEST_SYSTEMD_DIR
    TEST_SYSTEMD_DIR=$(mktemp -d)

    export EXEC_SERVICE_FILE="$TEST_SYSTEMD_DIR/execution.service"
    export CONSENSUS_SERVICE_FILE="$TEST_SYSTEMD_DIR/consensus.service"
    export VALIDATOR_SERVICE_FILE="$TEST_SYSTEMD_DIR/validator.service"
    export MEVBOOST_SERVICE_FILE="$TEST_SYSTEMD_DIR/mevboost.service"
    export CSM_VALIDATOR_SERVICE_FILE="$TEST_SYSTEMD_DIR/csm_nimbusvalidator.service"

    create_mock() {
        local name="$1"
        local stdout="${2:-}"
        cat <<EOF > "$MOCK_BIN_DIR/$name"
#!/bin/bash
echo "$name \$*" >> "$COMMAND_LOG"
if [ "$name" == "python3" ] && [[ "\$*" == *"-m venv"* ]]; then
    venv_path="\${@: -1}"
    command -p mkdir -p "\$venv_path/bin"
    {
        echo '#!/bin/bash'
        echo "echo \"pip \\\$*\" >> \"$COMMAND_LOG\""
        echo 'exit 0'
    } > "\$venv_path/bin/pip"
    command -p chmod +x "\$venv_path/bin/pip"
    {
        echo '#!/bin/bash'
        echo "echo \"python3 \\\$*\" >> \"$COMMAND_LOG\""
        echo 'exit 0'
    } > "\$venv_path/bin/python3"
    command -p chmod +x "\$venv_path/bin/python3"
fi
if [ -n "$stdout" ]; then echo "$stdout"; fi
exit 0
EOF
        chmod +x "$MOCK_BIN_DIR/$name"
    }

    for cmd in apt-get git python3 usermod mkdir stty pip; do
        create_mock "$cmd"
    done
    create_mock "whiptail"

    cat <<EOF > "$MOCK_BIN_DIR/sudo"
#!/bin/bash
export PATH="$MOCK_BIN_DIR:\$PATH"
"\$@"
EOF
    chmod +x "$MOCK_BIN_DIR/sudo"

    cat <<EOF > "$MOCK_BIN_DIR/curl"
#!/bin/bash
echo "curl \$*" >> "$COMMAND_LOG"
echo '{}'
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/curl"

    export PATH="$MOCK_BIN_DIR:$PATH"
}

teardown() {
    rm -rf "$MOCK_BIN_DIR" "$TEST_SYSTEMD_DIR" "${ETHPILLAR_VENV:-/tmp/ethpillar_bats_version_venv}"
    rm -f "$COMMAND_LOG"
}

write_service() {
    local path="$1"
    local description="$2"
    local exec_start="$3"
    cat <<EOF > "$path"
[Unit]
Description=$description

[Service]
ExecStart=$exec_start
EOF
}

write_bin() {
    local path="$1"
    local stdout="$2"
    cat <<EOF > "$path"
#!/bin/bash
echo "$(basename "$path") \$*" >> "$COMMAND_LOG"
echo "$stdout"
exit 0
EOF
    chmod +x "$path"
}

@test "--version: exits 0 and prints default lines when no clients installed" {
    run ./ethpillar.sh --version
    [ "$status" -eq 0 ]
    [[ "$output" == *"Consensus client: Not installed."* ]]
    [[ "$output" == *"Execution client: Not installed."* ]]
    [[ "$output" == *"Validator client: Not installed."* ]]
    [[ "$output" == *"Mev-boost: Not Installed"* ]]
    ep_version=$(grep '^EP_VERSION=' ethpillar.sh | cut -d'"' -f2)
    [[ "$output" == *"EthPillar: $ep_version"* ]]
    ! grep -q whiptail "$COMMAND_LOG"
    ! grep -q curl "$COMMAND_LOG"
}

@test "version: exits 0 and prints the same default lines as --version" {
    run ./ethpillar.sh version
    [ "$status" -eq 0 ]
    [[ "$output" == *"Consensus client: Not installed."* ]]
    [[ "$output" == *"Execution client: Not installed."* ]]
    [[ "$output" == *"Validator client: Not installed."* ]]
    [[ "$output" == *"Mev-boost: Not Installed"* ]]
    ep_version=$(grep '^EP_VERSION=' ethpillar.sh | cut -d'"' -f2)
    [[ "$output" == *"EthPillar: $ep_version"* ]]
    ! grep -q whiptail "$COMMAND_LOG"
    ! grep -q curl "$COMMAND_LOG"
}

@test "help: documents version as the primary form (and --version as alias)" {
    run ./ethpillar.sh help
    [ "$status" -eq 0 ]
    [[ "$output" == *$'\n  version '* ]]
    [[ "$output" == *"--version"* ]]
}

@test "--version: prints client versions from binaries when services exist" {
    write_bin "$MOCK_BIN_DIR/lighthouse" "Lighthouse v5.3.0"
    write_bin "$MOCK_BIN_DIR/nethermind" "Nethermind v1.30.0+abc"
    write_service "$EXEC_SERVICE_FILE" "Nethermind Execution Client" "$MOCK_BIN_DIR/nethermind"
    write_service "$CONSENSUS_SERVICE_FILE" "Lighthouse Consensus Client" "$MOCK_BIN_DIR/lighthouse bn"
    write_service "$VALIDATOR_SERVICE_FILE" "Lighthouse Validator Client" "$MOCK_BIN_DIR/lighthouse vc"
    touch "$MEVBOOST_SERVICE_FILE"

    cat <<EOF > "$MOCK_BIN_DIR/mev-boost"
#!/bin/bash
echo "mev-boost \$*" >> "$COMMAND_LOG"
echo "mev-boost version v1.8.0"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/mev-boost"

    run ./ethpillar.sh --version
    [ "$status" -eq 0 ]
    [[ "$output" == *"Consensus client: Lighthouse v5.3.0"* ]]
    [[ "$output" == *"Execution client: Nethermind 1.30.0"* ]]
    [[ "$output" == *"Validator client: Lighthouse v5.3.0"* ]]
    [[ "$output" == *"Mev-boost: 1.8.0"* ]]
    ! grep -q whiptail "$COMMAND_LOG"
    ! grep -q curl "$COMMAND_LOG"
}

@test "--version: Erigon-Caplin uses the erigon binary for both clients" {
    write_bin "$MOCK_BIN_DIR/erigon" "erigon version 3.0.12"
    write_service "$EXEC_SERVICE_FILE" "Erigon-Caplin Integrated Execution-Consensus Client" "$MOCK_BIN_DIR/erigon"

    run ./ethpillar.sh --version
    [ "$status" -eq 0 ]
    [[ "$output" == *"Consensus client: Erigon-Caplin 3.0.12"* ]]
    [[ "$output" == *"Execution client: Erigon-Caplin 3.0.12"* ]]
    [[ "$output" == *"Validator client: Not installed."* ]]
    ! grep -q curl "$COMMAND_LOG"
}

@test "--version: prints SSV Operator and SSV DKG when both units exist" {
    export SSV_NODE_SERVICE_FILE="$TEST_SYSTEMD_DIR/ssv-node.service"
    export SSV_DKG_SERVICE_FILE="$TEST_SYSTEMD_DIR/ssv-dkg.service"
    write_bin "$MOCK_BIN_DIR/ssvnode" "Version: v2.5.1"
    write_bin "$MOCK_BIN_DIR/ssv-dkg" "ssv-dkg version v3.1.1"
    write_service "$SSV_NODE_SERVICE_FILE" "SSV Operator Node for MAINNET" "$MOCK_BIN_DIR/ssvnode start-node"
    write_service "$SSV_DKG_SERVICE_FILE" "SSV DKG Operator for MAINNET" "$MOCK_BIN_DIR/ssv-dkg start-operator"

    run ./ethpillar.sh --version
    [ "$status" -eq 0 ]
    [[ "$output" == *"SSV Operator: v2.5.1"* ]]
    [[ "$output" == *"SSV DKG: v3.1.1"* ]]
    ! grep -q curl "$COMMAND_LOG"
}
