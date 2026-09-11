#!/usr/bin/env bats
#
# tests/test_ethpillar_cli.bats
#
# Tests for ethpillar non-interactive CLI (help, status, start/stop/restart, targets).
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export ETHPILLAR_VENV="/tmp/ethpillar_bats_cli_venv"
    rm -rf "$ETHPILLAR_VENV"

    export MOCK_BIN_DIR
    MOCK_BIN_DIR=$(mktemp -d)
    export COMMAND_LOG
    COMMAND_LOG=$(mktemp)
    export TEST_SYSTEMD_DIR
    TEST_SYSTEMD_DIR=$(mktemp -d)
    export SYSTEMCTL_STATE_DIR
    SYSTEMCTL_STATE_DIR=$(mktemp -d)

    export EXEC_SERVICE_FILE="$TEST_SYSTEMD_DIR/execution.service"
    export CONSENSUS_SERVICE_FILE="$TEST_SYSTEMD_DIR/consensus.service"
    export VALIDATOR_SERVICE_FILE="$TEST_SYSTEMD_DIR/validator.service"
    export MEVBOOST_SERVICE_FILE="$TEST_SYSTEMD_DIR/mevboost.service"
    export CHARON_SERVICE_FILE="$TEST_SYSTEMD_DIR/charon.service"
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

    # Mock systemctl: tracks ActiveState per unit via files in SYSTEMCTL_STATE_DIR
    cat <<EOF > "$MOCK_BIN_DIR/systemctl"
#!/bin/bash
echo "systemctl \$*" >> "$COMMAND_LOG"
cmd="\$1"
unit="\$2"
state_file="$SYSTEMCTL_STATE_DIR/\$unit"
case "\$cmd" in
  is-active)
    if [[ -f "\$state_file" ]]; then
      cat "\$state_file"
    else
      echo "inactive"
    fi
    # systemctl is-active exits 0 only when active
    [[ "\$(cat "\$state_file" 2>/dev/null)" == "active" ]]
    exit \$?
    ;;
  start)
    echo "active" > "\$state_file"
    exit 0
    ;;
  stop)
    echo "inactive" > "\$state_file"
    exit 0
    ;;
  restart)
    echo "active" > "\$state_file"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
    chmod +x "$MOCK_BIN_DIR/systemctl"

    export PATH="$MOCK_BIN_DIR:$PATH"
}

teardown() {
    rm -rf "$MOCK_BIN_DIR" "$TEST_SYSTEMD_DIR" "$SYSTEMCTL_STATE_DIR" \
        "${ETHPILLAR_VENV:-/tmp/ethpillar_bats_cli_venv}"
    rm -f "$COMMAND_LOG"
}

write_service() {
    local path="$1"
    local description="${2:-Test Client}"
    cat <<EOF > "$path"
[Unit]
Description=$description

[Service]
ExecStart=/bin/true
EOF
}

set_unit_state() {
    local unit="$1"
    local state="$2"
    echo "$state" > "$SYSTEMCTL_STATE_DIR/$unit"
}

@test "--help: lists commands and install-aware targets" {
    write_service "$EXEC_SERVICE_FILE" "Nethermind Execution Client"
    write_service "$CONSENSUS_SERVICE_FILE" "Lighthouse Consensus Client"

    run ./ethpillar.sh --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"status"* ]]
    [[ "$output" == *"check-updates"* ]]
    [[ "$output" == *"upgrade"* ]]
    [[ "$output" == *"execution"* ]]
    [[ "$output" == *"consensus"* ]]
    [[ "$output" == *"ethpillar"* ]]
    [[ "$output" == *"Not installed:"* ]]
    [[ "$output" == *"validator"* ]]
    [[ "$output" == *"mevboost"* ]]
    ! grep -q whiptail "$COMMAND_LOG"
}

@test "help: same as --help" {
    run ./ethpillar.sh help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage: ethpillar"* ]]
}

@test "unknown command: exits 1 with hint" {
    run ./ethpillar.sh not-a-real-command
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unknown command"* ]]
    [[ "$output" == *"--help"* ]]
}

@test "status: no clients installed exits 0" {
    run ./ethpillar.sh status
    [ "$status" -eq 0 ]
    [[ "$output" == *"No clients installed"* ]]
}

@test "status: reports active and exits 0 when all active" {
    write_service "$EXEC_SERVICE_FILE"
    write_service "$CONSENSUS_SERVICE_FILE"
    set_unit_state execution active
    set_unit_state consensus active

    run ./ethpillar.sh status
    [ "$status" -eq 0 ]
    [[ "$output" == *"execution"* ]]
    [[ "$output" == *"active"* ]]
    [[ "$output" == *"consensus"* ]]
}

@test "status: exits 1 when a unit is inactive" {
    write_service "$EXEC_SERVICE_FILE"
    set_unit_state execution inactive

    run ./ethpillar.sh status
    [ "$status" -eq 1 ]
    [[ "$output" == *"inactive"* ]]
}

@test "status --json: emits JSON map" {
    write_service "$EXEC_SERVICE_FILE"
    set_unit_state execution active

    run ./ethpillar.sh status --json
    [ "$status" -eq 0 ]
    [[ "$output" == *'"execution":"active"'* ]]
}

@test "start/stop/restart: invoke systemctl for installed targets only" {
    write_service "$EXEC_SERVICE_FILE"
    write_service "$MEVBOOST_SERVICE_FILE"
    set_unit_state execution active
    set_unit_state mevboost active

    run ./ethpillar.sh stop all
    [ "$status" -eq 0 ]
    grep -q "systemctl stop execution" "$COMMAND_LOG"
    grep -q "systemctl stop mevboost" "$COMMAND_LOG"
    ! grep -q "systemctl stop consensus" "$COMMAND_LOG"

    : > "$COMMAND_LOG"
    run ./ethpillar.sh start execution
    [ "$status" -eq 0 ]
    grep -q "systemctl start execution" "$COMMAND_LOG"
    ! grep -q "systemctl start mevboost" "$COMMAND_LOG"

    : > "$COMMAND_LOG"
    run ./ethpillar.sh restart mevboost
    [ "$status" -eq 0 ]
    grep -q "systemctl restart mevboost" "$COMMAND_LOG"
}

@test "start: rejects unknown or not-installed target" {
    write_service "$EXEC_SERVICE_FILE"

    run ./ethpillar.sh start charon
    [ "$status" -eq 1 ]
    [[ "$output" == *"not installed"* ]]

    run ./ethpillar.sh start bob
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unknown target"* ]]
}

@test "upgrade: rejects not-installed client target" {
    run ./ethpillar.sh upgrade execution
    [ "$status" -eq 1 ]
    [[ "$output" == *"not installed"* ]]
}
