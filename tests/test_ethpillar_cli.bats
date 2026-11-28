#!/usr/bin/env bats
#
# tests/test_ethpillar_cli.bats
#
# Tests for ethpillar non-interactive CLI (help, status, start/stop/restart,
# targets, logs, update|upgrade, version).
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export ETHPILLAR_VENV="/tmp/ethpillar_bats_cli_venv"
    rm -rf "$ETHPILLAR_VENV"

    export MOCK_BIN_DIR
    MOCK_BIN_DIR=$(mktemp -d)
    export COMMAND_LOG
    COMMAND_LOG=$(mktemp)
    export UPDATE_LOG
    UPDATE_LOG=$(mktemp)
    export TEST_SYSTEMD_DIR
    TEST_SYSTEMD_DIR=$(mktemp -d)
    export SYSTEMCTL_STATE_DIR
    SYSTEMCTL_STATE_DIR=$(mktemp -d)
    export ETHPILLAR_UPDATE_SCRIPT_DIR
    ETHPILLAR_UPDATE_SCRIPT_DIR=$(mktemp -d)

    # Default LATEST tags match the stub binaries in install_mock_clients.
    # Force-reset each test so a "behind" case cannot leak into the next.
    export MOCK_EL_LATEST=v1.30.0
    export MOCK_CL_LATEST=v5.3.0
    export MOCK_CL_COMMIT=
    export MOCK_MEV_LATEST=v1.8.0
    export MOCK_CHARON_LATEST=v1.11.0
    export MOCK_SSV_LATEST=v2.5.1
    export MOCK_SSV_DKG_LATEST=v3.1.1
    export MOCK_EP_REMOTE_VERSION
    MOCK_EP_REMOTE_VERSION=$(grep '^EP_VERSION=' ethpillar.sh | cut -d'"' -f2)

    export EXEC_SERVICE_FILE="$TEST_SYSTEMD_DIR/execution.service"
    export CONSENSUS_SERVICE_FILE="$TEST_SYSTEMD_DIR/consensus.service"
    export VALIDATOR_SERVICE_FILE="$TEST_SYSTEMD_DIR/validator.service"
    export MEVBOOST_SERVICE_FILE="$TEST_SYSTEMD_DIR/mevboost.service"
    export CHARON_SERVICE_FILE="$TEST_SYSTEMD_DIR/charon.service"
    export SSV_NODE_SERVICE_FILE="$TEST_SYSTEMD_DIR/ssv-node.service"
    export SSV_DKG_SERVICE_FILE="$TEST_SYSTEMD_DIR/ssv-dkg.service"
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
    command -p cp "$MOCK_BIN_DIR/venv_python3.template" "\$venv_path/bin/python3"
    command -p chmod +x "\$venv_path/bin/python3"
fi
if [ -n "$stdout" ]; then echo "$stdout"; fi
exit 0
EOF
        chmod +x "$MOCK_BIN_DIR/$name"
    }

    cat > "$MOCK_BIN_DIR/venv_python3.template" <<EOF
#!/bin/bash
echo "python3 \$*" >> "$COMMAND_LOG"
if [[ "\$*" == *release_info* ]]; then
  client=""
  prev=""
  for arg in "\$@"; do
    if [[ "\$prev" == "release_info" ]]; then
      client="\$arg"
      break
    fi
    prev="\$arg"
  done
  case "\${client,,}" in
    nethermind|geth|besu|erigon|reth|ethrex)
      echo "{\"version\":\"\${MOCK_EL_LATEST:-v1.30.0}\",\"commit\":\"\"}"
      ;;
    lighthouse|lodestar|teku|nimbus|prysm|grandine)
      echo "{\"version\":\"\${MOCK_CL_LATEST:-v5.3.0}\",\"commit\":\"\${MOCK_CL_COMMIT:-}\"}"
      ;;
    mevboost)
      echo "{\"version\":\"\${MOCK_MEV_LATEST:-v1.8.0}\",\"commit\":\"\"}"
      ;;
    charon)
      echo "{\"version\":\"\${MOCK_CHARON_LATEST:-v1.11.0}\",\"commit\":\"\"}"
      ;;
    ssv|ssv-node|ssvnode)
      echo "{\"version\":\"\${MOCK_SSV_LATEST:-v2.5.1}\",\"commit\":\"\"}"
      ;;
    ssv-dkg|ssvdkg)
      echo "{\"version\":\"\${MOCK_SSV_DKG_LATEST:-v3.1.1}\",\"commit\":\"\"}"
      ;;
    *)
      echo "{\"version\":\"\${MOCK_LATEST_TAG:-v0.0.1}\",\"commit\":\"\"}"
      ;;
  esac
fi
exit 0
EOF

    for cmd in apt-get apt git python3 usermod mkdir stty pip docker ccze reboot; do
        create_mock "$cmd"
    done
    create_mock "whiptail"

    # apt-get -s upgrade: emit Inst lines when MOCK_APT_UPGRADES=1.
    cat <<EOF > "$MOCK_BIN_DIR/apt-get"
#!/bin/bash
echo "apt-get \$*" >> "$COMMAND_LOG"
if [[ "\$*" == *"-s"* && "\$*" == *"upgrade"* ]]; then
  if [[ "\${MOCK_APT_UPGRADES:-0}" == "1" ]]; then
    echo "Inst libc6 [1.0] (1.1 Ubuntu [amd64])"
  fi
fi
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/apt-get"

    # Default package frontend is apt; tests that prefer nala install a mock.
    cat <<EOF > "$MOCK_BIN_DIR/apt"
#!/bin/bash
echo "apt \$*" >> "$COMMAND_LOG"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/apt"

    export ETHPILLAR_REBOOT_REQUIRED_FILE
    ETHPILLAR_REBOOT_REQUIRED_FILE=$(mktemp -u)
    export MOCK_APT_UPGRADES=0

    # git show origin/main:ethpillar.sh → EP_VERSION for self-upgrade skip.
    cat <<EOF > "$MOCK_BIN_DIR/git"
#!/bin/bash
echo "git \$*" >> "$COMMAND_LOG"
if [[ "\$*" == *show* && "\$*" == *ethpillar.sh* ]]; then
  echo "EP_VERSION=\"\${MOCK_EP_REMOTE_VERSION:-}\""
fi
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/git"

    local _upd
    for _upd in execution consensus validator mevboost charon; do
        cat > "$ETHPILLAR_UPDATE_SCRIPT_DIR/update_${_upd}.sh" <<EOF
#!/bin/bash
echo "update_${_upd}.sh \$*" >> "$UPDATE_LOG"
exit 0
EOF
        chmod +x "$ETHPILLAR_UPDATE_SCRIPT_DIR/update_${_upd}.sh"
    done
    # ssv-node / ssv-dkg / ssv all route to update_ssv.sh
    cat > "$ETHPILLAR_UPDATE_SCRIPT_DIR/update_ssv.sh" <<EOF
#!/bin/bash
echo "update_ssv.sh \$*" >> "$UPDATE_LOG"
exit 0
EOF
    chmod +x "$ETHPILLAR_UPDATE_SCRIPT_DIR/update_ssv.sh"

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

    # journalctl must never follow (-f) in CI. Logs argv so logs-dispatch tests
    # can assert the rolling consolidated unit list.
    cat <<EOF > "$MOCK_BIN_DIR/journalctl"
#!/bin/bash
echo "journalctl \$*" >> "$COMMAND_LOG"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/journalctl"

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
        "${ETHPILLAR_UPDATE_SCRIPT_DIR:-}" \
        "${ETHPILLAR_VENV:-/tmp/ethpillar_bats_cli_venv}"
    rm -f "$COMMAND_LOG" "$UPDATE_LOG" "${ETHPILLAR_REBOOT_REQUIRED_FILE:-}"
}

install_mock_nala() {
    cat <<EOF > "$MOCK_BIN_DIR/nala"
#!/bin/bash
echo "nala \$*" >> "$COMMAND_LOG"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/nala"
}

write_service() {
    local path="$1"
    local description="${2:-Test Client}"
    local exec_start="${3:-/bin/true}"
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

# Official Lodestar --version plus unpack-path /hex noise that used to steal INSTALLED_COMMIT.
write_lodestar_bin() {
    local path="$1"
    local ver_line="${2:-* Version: v1.48.0/c7dc2b0}"
    cat <<EOF > "$path"
#!/bin/bash
echo "lodestar \$*" >> "$COMMAND_LOG"
printf '%s\n' \\
  "Unpacking Lodestar binary from /tmp/lodestar-v1.48.0-linux-amd64/deadbeef/lodestar" \\
  "$ver_line" \\
  "* by ChainSafe Systems, 2018-2026"
exit 0
EOF
    chmod +x "$path"
}

write_charon_bin() {
    local path="$1"
    local ver="${2:-v1.11.0}"
    cat <<EOF > "$path"
#!/bin/bash
echo "charon \$*" >> "$COMMAND_LOG"
if [[ "\$1" == "version" ]]; then
  echo "$ver"
fi
exit 0
EOF
    chmod +x "$path"
}

write_ssv_bin() {
    local path="$1"
    local ver="${2:-v2.5.1}"
    cat <<EOF > "$path"
#!/bin/bash
echo "$(basename "$path") \$*" >> "$COMMAND_LOG"
echo "$ver"
exit 0
EOF
    chmod +x "$path"
}

# Install stub clients whose versions match the default MOCK_*_LATEST tags.
install_mock_clients() {
    write_bin "$MOCK_BIN_DIR/nethermind" "Nethermind 1.30.0"
    write_bin "$MOCK_BIN_DIR/lighthouse" "Lighthouse v5.3.0"
    write_bin "$MOCK_BIN_DIR/mev-boost" "mev-boost version v1.8.0"
    write_charon_bin "$MOCK_BIN_DIR/charon" "v1.11.0"

    write_service "$EXEC_SERVICE_FILE" "Nethermind Execution Client" "$MOCK_BIN_DIR/nethermind"
    write_service "$CONSENSUS_SERVICE_FILE" "Lighthouse Consensus Client" "$MOCK_BIN_DIR/lighthouse bn"
    write_service "$VALIDATOR_SERVICE_FILE" "Lighthouse Validator Client" "$MOCK_BIN_DIR/lighthouse vc"
    write_service "$MEVBOOST_SERVICE_FILE" "MEV-Boost" "$MOCK_BIN_DIR/mev-boost"
    write_service "$CHARON_SERVICE_FILE" "Charon" "$MOCK_BIN_DIR/charon run"
}

# SSV operator stubs (node + DKG use different repos/versions).
install_mock_ssv() {
    write_ssv_bin "$MOCK_BIN_DIR/ssvnode" "v2.5.1"
    write_ssv_bin "$MOCK_BIN_DIR/ssv-dkg" "v3.1.1"
    write_service "$SSV_NODE_SERVICE_FILE" "SSV Operator Node for MAINNET" "$MOCK_BIN_DIR/ssvnode start-node"
    write_service "$SSV_DKG_SERVICE_FILE" "SSV DKG Operator for MAINNET" "$MOCK_BIN_DIR/ssv-dkg start-operator"
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
    [[ "$output" == *"update"* ]]
    [[ "$output" == *"upgrade"* ]]
    [[ "$output" == *"logs"* ]]
    [[ "$output" == *"version"* ]]
    [[ "$output" == *"--version"* ]]
    [[ "$output" == *"execution"* ]]
    [[ "$output" == *"consensus"* ]]
    [[ "$output" == *"ethpillar"* ]]
    [[ "$output" == *"Not installed:"* ]]
    [[ "$output" == *"validator"* ]]
    [[ "$output" == *"mevboost"* ]]
    [[ "$output" == *"logs|restart|start|status|stop:"* ]]
    [[ "$output" == *"check-updates|update|upgrade:"* ]]
    [[ "$output" == *"system (opt-in"* ]]
    [[ "$output" == *"Reboot the host"* ]]
    [[ "$output" == *"reboot required"* ]]
    [[ "$output" == *"logs [unit"* ]]
    [[ "$output" == *"ethpillar logs execution"* ]]
    [[ "$output" == *"ethpillar update system"* ]]
    [[ "$output" == *"ethpillar restart system"* ]]
    ! [[ "$output" == *$'\n  clients:'* ]]
    ! grep -q whiptail "$COMMAND_LOG"
}

# Primary command tokens in the Commands: block, A–Z by bare name
# (leading dashes stripped so --migrate_cdvn sorts as migrate_cdvn).
cli_help_command_names() {
    awk '
        /^Commands:/ {flag=1; next}
        flag && /^[A-Za-z]/ {exit}
        flag && /^  [^ ]/ {
            line=$0
            sub(/^  /, "", line)
            split(line, a, /[ |]/)
            name=a[1]
            gsub(/^-+/, "", name)
            if (name != "") print name
        }
    '
}

@test "help: Commands list is alphabetical" {
    run ./ethpillar.sh help
    [ "$status" -eq 0 ]
    names=$(cli_help_command_names <<< "$output")
    [ -n "$names" ]
    sorted=$(printf '%s\n' "$names" | LC_ALL=C sort)
    [ "$names" = "$sorted" ]
    [[ "$names" == *"check-updates"* ]]
    [[ "$names" == *"help"* ]]
    [[ "$names" == *"logs"* ]]
    [[ "$names" == *"migrate_cdvn"* ]]
    [[ "$names" == *"start"* ]]
    [[ "$names" == *"status"* ]]
    [[ "$names" == *"update"* ]]
    [[ "$names" == *"upgrade"* ]]
    [[ "$names" == *"version"* ]]
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

@test "logs: help lists the command as TUI Rolling Consolidated Logs" {
    run ./ethpillar.sh help
    [ "$status" -eq 0 ]
    [[ "$output" == *"logs [unit"* ]]
    [[ "$output" == *"View rolling consolidated logs"* ]]
    [[ "$output" == *"TUI Rolling Consolidated Logs"* ]]
    [[ "$output" == *"ethpillar logs"* ]]
    [[ "$output" == *"ethpillar logs execution"* ]]
}

@test "logs: dispatches to view_journal_logs, not view_logs.sh" {
    # Bare `logs` uses show_rolling (hard-coded /etc paths); named units honor
    # *_SERVICE_FILE test overrides via cli.sh.
    write_service "$VALIDATOR_SERVICE_FILE"
    write_service "$CONSENSUS_SERVICE_FILE"
    write_service "$EXEC_SERVICE_FILE"
    write_service "$MEVBOOST_SERVICE_FILE"
    write_service "$CHARON_SERVICE_FILE"
    write_service "$CSM_VALIDATOR_SERVICE_FILE"

    run ./ethpillar.sh logs validator consensus execution mevboost charon csm_nimbusvalidator
    [ "$status" -eq 0 ]
    grep -q "journalctl -o json -u validator -u consensus -u execution -u mevboost -u charon -u csm_nimbusvalidator --no-hostname -f" "$COMMAND_LOG"
    grep -q "journal_format.py" "$COMMAND_LOG"
    ! grep -q "view_logs.sh" "$COMMAND_LOG"
    ! grep -q "tmux" "$COMMAND_LOG"
}

@test "show_rolling_consolidated_logs: Aztec remote-rpc then installed journal units only" {
    awk '
        /^show_rolling_consolidated_logs\(\)/ {flag=1}
        flag {print}
        flag && /^}/ {exit}
    ' logging/journal.sh > "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q '/opt/ethpillar/aztec' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q 'docker compose logs -f --tail=233' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q 'ssv-node' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q 'ssv-dkg' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q 'units+=(-u' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    # -F: [@] is otherwise a BRE character class and misses the literal line.
    grep -Fq 'view_journal_logs "${units[@]}" --no-hostname -f' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q 'show_rolling_consolidated_logs' ethpillar.sh
    grep -q 'show_rolling_consolidated_logs' cli.sh
}

@test "logs: unknown unit name errors cleanly" {
    run ./ethpillar.sh logs leftover
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unknown target: leftover"* ]]
    [[ "$output" == *"Valid targets:"* ]]
    [[ "$output" == *"execution"* ]]
    ! grep -q -- "-u leftover" "$COMMAND_LOG"
    ! grep -q -- "-u validator -u consensus" "$COMMAND_LOG"
    ! grep -q "view_logs.sh" "$COMMAND_LOG"
}

@test "logs: rejects a not-installed unit" {
    write_service "$EXEC_SERVICE_FILE"

    run ./ethpillar.sh logs consensus
    [ "$status" -eq 1 ]
    [[ "$output" == *"not installed"* ]]
    [[ "$output" == *"consensus"* ]]
    ! grep -q -- "-u consensus" "$COMMAND_LOG"
}

@test "logs: one installed unit follows only that unit" {
    write_service "$EXEC_SERVICE_FILE"
    write_service "$CONSENSUS_SERVICE_FILE"

    run ./ethpillar.sh logs execution
    [ "$status" -eq 0 ]
    grep -q "journalctl -o json -u execution --no-hostname -f" "$COMMAND_LOG"
    ! grep -q -- "-u consensus" "$COMMAND_LOG"
    ! grep -q -- "-u validator -u consensus -u execution" "$COMMAND_LOG"
    ! grep -q "docker compose logs" "$COMMAND_LOG"
    ! grep -q "view_logs.sh" "$COMMAND_LOG"
}

@test "logs: multiple units follow in the given order" {
    write_service "$CHARON_SERVICE_FILE"
    write_service "$VALIDATOR_SERVICE_FILE"

    run ./ethpillar.sh logs charon validator
    [ "$status" -eq 0 ]
    grep -q "journalctl -o json -u charon -u validator --no-hostname -f" "$COMMAND_LOG"
    ! grep -q -- "-u validator -u consensus -u execution" "$COMMAND_LOG"
}

@test "logs: mixed-case names and duplicates resolve once" {
    write_service "$EXEC_SERVICE_FILE"

    run ./ethpillar.sh logs Execution execution
    [ "$status" -eq 0 ]
    grep -q "journalctl -o json -u execution --no-hostname -f" "$COMMAND_LOG"
    ! grep -q -- "-u execution -u execution" "$COMMAND_LOG"
}

@test "logs: csm_nimbusvalidator is a valid installed unit" {
    write_service "$CSM_VALIDATOR_SERVICE_FILE"

    run ./ethpillar.sh logs csm_nimbusvalidator
    [ "$status" -eq 0 ]
    grep -q "journalctl -o json -u csm_nimbusvalidator --no-hostname -f" "$COMMAND_LOG"
}

@test "logs: unknown name after a valid unit does not start journalctl" {
    write_service "$EXEC_SERVICE_FILE"

    run ./ethpillar.sh logs execution leftover
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unknown target: leftover"* ]]
    ! grep -q "journalctl -o json -u execution --no-hostname -f" "$COMMAND_LOG"
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

@test "start all: charon before validator; stop all: validator before charon" {
    write_service "$VALIDATOR_SERVICE_FILE"
    write_service "$CHARON_SERVICE_FILE"
    set_unit_state validator active
    set_unit_state charon active

    run ./ethpillar.sh start all
    [ "$status" -eq 0 ]
    start_charon_line=$(grep -n "systemctl start charon" "$COMMAND_LOG" | head -1 | cut -d: -f1)
    start_validator_line=$(grep -n "systemctl start validator" "$COMMAND_LOG" | head -1 | cut -d: -f1)
    [ -n "$start_charon_line" ]
    [ -n "$start_validator_line" ]
    [ "$start_charon_line" -lt "$start_validator_line" ]

    : > "$COMMAND_LOG"
    run ./ethpillar.sh stop all
    [ "$status" -eq 0 ]
    stop_validator_line=$(grep -n "systemctl stop validator" "$COMMAND_LOG" | head -1 | cut -d: -f1)
    stop_charon_line=$(grep -n "systemctl stop charon" "$COMMAND_LOG" | head -1 | cut -d: -f1)
    [ -n "$stop_validator_line" ]
    [ -n "$stop_charon_line" ]
    [ "$stop_validator_line" -lt "$stop_charon_line" ]
}

@test "tui_all_service_order start is CLI start then extras; stop is extras reversed then CLI stop" {
    source ./cli.sh
    mapfile -t start_order < <(tui_all_service_order start)
    mapfile -t stop_order < <(tui_all_service_order stop)
    # CLI_CLIENT_START_ORDER includes ssv-node/ssv-dkg before validator.
    [ "${start_order[0]}" = "execution" ]
    [ "${start_order[1]}" = "consensus" ]
    [ "${start_order[2]}" = "mevboost" ]
    [ "${start_order[3]}" = "charon" ]
    [ "${start_order[4]}" = "web3signer" ]
    [ "${start_order[5]}" = "ssv-node" ]
    [ "${start_order[6]}" = "ssv-dkg" ]
    [ "${start_order[7]}" = "validator" ]
    [ "${start_order[8]}" = "csm_nimbusvalidator" ]
    [ "${start_order[9]}" = "dora" ]
    [ "${stop_order[0]}" = "dora" ]
    [ "${stop_order[1]}" = "csm_nimbusvalidator" ]
    [ "${stop_order[2]}" = "validator" ]
    [ "${stop_order[3]}" = "ssv-dkg" ]
    [ "${stop_order[4]}" = "ssv-node" ]
    [ "${stop_order[5]}" = "web3signer" ]
    [ "${stop_order[6]}" = "charon" ]
    [ "${stop_order[7]}" = "mevboost" ]
    [ "${stop_order[8]}" = "consensus" ]
    [ "${stop_order[9]}" = "execution" ]
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

@test "update: alias of upgrade (same reject for not-installed target)" {
    run ./ethpillar.sh update execution
    [ "$status" -eq 1 ]
    [[ "$output" == *"not installed"* ]]
}

@test "update: alias of upgrade (same skip when already on LATEST)" {
    install_mock_clients
    set_unit_state charon active

    run ./ethpillar.sh update charon
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"charon: already up to date (1.11.0) — skipping"* ]]
    [ ! -s "$UPDATE_LOG" ]
    ! grep -q "systemctl stop" "$COMMAND_LOG"
    ! grep -q "update_charon.sh" "$COMMAND_LOG"
}

@test "update: alias of upgrade (same update script when behind)" {
    install_mock_clients
    set_unit_state charon active
    export MOCK_CHARON_LATEST=v1.12.0

    run ./ethpillar.sh update charon
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "update_charon.sh --auto" "$UPDATE_LOG"
    ! grep -q "skipping" <<< "$output"
}

@test "update and upgrade: same targets and exit codes when all current" {
    install_mock_clients

    run ./ethpillar.sh upgrade all
    upgrade_status=$status
    upgrade_output=$output
    [ "$upgrade_status" -eq 0 ]

    : > "$UPDATE_LOG"
    run ./ethpillar.sh update all
    [ "$status" -eq "$upgrade_status" ]
    [[ "$output" == *"execution: already up to date"* ]]
    [[ "$output" == *"consensus: already up to date"* ]]
    [[ "$output" == *"validator: already up to date"* ]]
    [[ "$output" == *"mevboost: already up to date"* ]]
    [[ "$output" == *"charon: already up to date"* ]]
    [[ "$output" == *"ethpillar: already up to date"* ]]
    [ ! -s "$UPDATE_LOG" ]
    # Both verbs print the same per-target skip lines.
    while IFS= read -r line; do
        [[ "$line" == *": already up to date"* ]] || continue
        [[ "$upgrade_output" == *"$line"* ]]
    done <<< "$output"
}

@test "upgrade: skips charon when already on LATEST (no update script / no stop)" {
    install_mock_clients
    set_unit_state charon active

    run ./ethpillar.sh upgrade charon
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"charon: already up to date (1.11.0) — skipping"* ]]
    [ ! -s "$UPDATE_LOG" ]
    ! grep -q "systemctl stop" "$COMMAND_LOG"
    ! grep -q "update_charon.sh" "$COMMAND_LOG"
}

@test "upgrade: skips ssv-node and ssv-dkg when already on LATEST" {
    install_mock_ssv

    run ./ethpillar.sh upgrade ssv-node
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"ssv-node: already up to date (2.5.1) — skipping"* ]]
    [ ! -s "$UPDATE_LOG" ]

    run ./ethpillar.sh upgrade ssv-dkg
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"ssv-dkg: already up to date (3.1.1) — skipping"* ]]
    [ ! -s "$UPDATE_LOG" ]
}

@test "upgrade: ssv alias expands to node and dkg; skips when current" {
    install_mock_ssv

    run ./ethpillar.sh upgrade ssv
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"ssv-node: already up to date"* ]]
    [[ "$output" == *"ssv-dkg: already up to date"* ]]
    [ ! -s "$UPDATE_LOG" ]
}

@test "upgrade: calls update_ssv when ssv-node is behind" {
    install_mock_ssv
    export MOCK_SSV_LATEST=v2.6.0

    run ./ethpillar.sh upgrade ssv-node
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "update_ssv.sh --auto" "$UPDATE_LOG"
    ! grep -q "ssv-node: already up to date" <<< "$output"
}

@test "upgrade: calls update_ssv when only ssv-dkg is behind" {
    install_mock_ssv
    export MOCK_SSV_DKG_LATEST=v3.2.0

    run ./ethpillar.sh upgrade ssv-dkg
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "update_ssv.sh --auto" "$UPDATE_LOG"
    ! grep -q "ssv-dkg: already up to date" <<< "$output"
}

@test "check-updates: reports ssv-dkg against DKG latest (not node tag)" {
    install_mock_ssv
    export MOCK_SSV_DKG_LATEST=v3.2.0

    run ./ethpillar.sh check-updates ssv-dkg
    echo "$output"
    [ "$status" -eq 2 ]
    [[ "$output" == *"ssv-dkg: update available"* ]]
    [[ "$output" == *"3.1.1"* ]]
    [[ "$output" == *"3.2.0"* ]]
}

@test "upgrade: calls update script when charon is behind" {
    install_mock_clients
    set_unit_state charon active
    export MOCK_CHARON_LATEST=v1.12.0

    run ./ethpillar.sh upgrade charon
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "update_charon.sh --auto" "$UPDATE_LOG"
    ! grep -q "skipping" <<< "$output"
}

@test "upgrade: skips every --auto client target when already on LATEST" {
    install_mock_clients
    local t
    for t in execution consensus validator mevboost charon; do
        : > "$UPDATE_LOG"
        : > "$COMMAND_LOG"
        run ./ethpillar.sh upgrade "$t"
        echo "$output"
        [ "$status" -eq 0 ]
        [[ "$output" == *"${t}: already up to date"* ]]
        [[ "$output" == *"skipping"* ]]
        [ ! -s "$UPDATE_LOG" ]
        ! grep -q "systemctl stop" "$COMMAND_LOG"
    done
}

@test "upgrade: calls update script for each --auto target when behind" {
    install_mock_clients
    export MOCK_EL_LATEST=v1.31.0
    export MOCK_CL_LATEST=v5.4.0
    export MOCK_MEV_LATEST=v1.9.0
    export MOCK_CHARON_LATEST=v1.12.0

    local t
    for t in execution consensus validator mevboost charon; do
        : > "$UPDATE_LOG"
        run ./ethpillar.sh upgrade "$t"
        echo "$output"
        [ "$status" -eq 0 ]
        grep -q "update_${t}.sh --auto" "$UPDATE_LOG"
        ! grep -q "${t}: already up to date" <<< "$output"
    done
}

@test "upgrade all: skips current clients and only updates the one behind" {
    install_mock_clients
    export MOCK_CHARON_LATEST=v1.12.0

    run ./ethpillar.sh upgrade all
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"execution: already up to date"* ]]
    [[ "$output" == *"consensus: already up to date"* ]]
    [[ "$output" == *"validator: already up to date"* ]]
    [[ "$output" == *"mevboost: already up to date"* ]]
    [[ "$output" == *"ethpillar: already up to date"* ]]
    ! grep -q "charon: already up to date" <<< "$output"
    grep -q "update_charon.sh --auto" "$UPDATE_LOG"
    ! grep -q "update_execution.sh" "$UPDATE_LOG"
    ! grep -q "update_consensus.sh" "$UPDATE_LOG"
    ! grep -q "update_validator.sh" "$UPDATE_LOG"
    ! grep -q "update_mevboost.sh" "$UPDATE_LOG"
    ! grep -q "systemctl stop" "$COMMAND_LOG"
}

@test "upgrade consensus: skips Lodestar on official v1.48.0/c7dc2b0 despite extra /hex noise" {
    export MOCK_CL_LATEST=v1.48.0
    export MOCK_CL_COMMIT=c7dc2b0b3b715635fb9b616bf178137ad64f7bba
    write_lodestar_bin "$MOCK_BIN_DIR/lodestar"
    write_service "$CONSENSUS_SERVICE_FILE" "Lodestar Consensus Client" "$MOCK_BIN_DIR/lodestar"

    run ./ethpillar.sh upgrade consensus
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"consensus: already up to date (1.48.0 (c7dc2b0)) — skipping"* ]]
    [ ! -s "$UPDATE_LOG" ]
    ! grep -q "systemctl stop" "$COMMAND_LOG"
    ! grep -q "update_consensus.sh" "$COMMAND_LOG"
}

@test "upgrade consensus: skips Lodestar when Version line embeds EthPillar branch SHA" {
    export MOCK_CL_LATEST=v1.48.0
    export MOCK_CL_COMMIT=c7dc2b0b3b715635fb9b616bf178137ad64f7bba
    write_lodestar_bin "$MOCK_BIN_DIR/lodestar" \
      "* Version: v1.48.0/cursor/cli-upgrade-skip-when-latest-0415/14901a2"
    write_service "$CONSENSUS_SERVICE_FILE" "Lodestar Consensus Client" "$MOCK_BIN_DIR/lodestar"

    run ./ethpillar.sh upgrade consensus
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"consensus: already up to date (1.48.0) — skipping"* ]]
    ! grep -q "14901a2" <<< "$output"
    [ ! -s "$UPDATE_LOG" ]
    ! grep -q "update_consensus.sh" "$COMMAND_LOG"
}

@test "upgrade consensus: calls update script when Lodestar is behind" {
    export MOCK_CL_LATEST=v1.49.0
    export MOCK_CL_COMMIT=aabbccddeeff0011
    write_lodestar_bin "$MOCK_BIN_DIR/lodestar"
    write_service "$CONSENSUS_SERVICE_FILE" "Lodestar Consensus Client" "$MOCK_BIN_DIR/lodestar"

    run ./ethpillar.sh upgrade consensus
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "update_consensus.sh --auto" "$UPDATE_LOG"
    ! grep -q "skipping" <<< "$output"
}

@test "upgrade ethpillar: skips when EP_VERSION matches remote" {
    run ./ethpillar.sh upgrade ethpillar
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"ethpillar: already up to date"* ]]
    [[ "$output" == *"skipping"* ]]
    ! grep -q "git checkout" "$COMMAND_LOG"
    ! grep -q "git reset" "$COMMAND_LOG"
    ! grep -q "git clean" "$COMMAND_LOG"
}

@test "upgrade ethpillar: runs self-update when remote EP_VERSION differs" {
    export MOCK_EP_REMOTE_VERSION="9.9.9"

    run ./ethpillar.sh upgrade ethpillar
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Updating EthPillar"* ]]
    ! grep -q "skipping" <<< "$output"
    grep -q "git checkout" "$COMMAND_LOG"
}

@test "upgrade system: uses apt when nala is absent; skips upgrade when current" {
    run ./ethpillar.sh upgrade system
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "apt update" "$COMMAND_LOG"
    grep -q "apt autoremove -y" "$COMMAND_LOG"
    ! grep -q "apt upgrade" "$COMMAND_LOG"
    [[ "$output" == *"already up to date"* ]]
    ! grep -q "nala" "$COMMAND_LOG"
}

@test "update system: alias prefers nala when installed and upgrades when pending" {
    install_mock_nala
    export MOCK_APT_UPGRADES=1

    run ./ethpillar.sh update system
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "nala update" "$COMMAND_LOG"
    grep -q "nala upgrade -y" "$COMMAND_LOG"
    grep -q "nala autoremove -y" "$COMMAND_LOG"
    ! grep -q "apt update" "$COMMAND_LOG"
    [[ "$output" == *"upgrade complete"* ]]
}

@test "upgrade system: exits 2 and prints reboot message when reboot-required" {
    export MOCK_APT_UPGRADES=1
    touch "$ETHPILLAR_REBOOT_REQUIRED_FILE"

    run ./ethpillar.sh upgrade system
    echo "$output"
    [ "$status" -eq 2 ]
    [[ "$output" == *"Reboot required to complete update"* ]]
    grep -q "apt upgrade -y" "$COMMAND_LOG"
}

@test "upgrade all: resolve targets excludes system" {
    write_service "$EXEC_SERVICE_FILE"
    write_service "$CONSENSUS_SERVICE_FILE"
    source ./cli.sh

    run cli_resolve_targets upgrade all
    [ "$status" -eq 0 ]
    [[ "$output" == *"execution"* ]]
    [[ "$output" == *"consensus"* ]]
    [[ "$output" == *"ethpillar"* ]]
    ! [[ "$output" == *"system"* ]]

    run cli_resolve_targets upgrade system
    [ "$status" -eq 0 ]
    [[ "$output" == "system" ]]
}

@test "check-updates system: exits 2 when packages are pending" {
    export MOCK_APT_UPGRADES=1

    run ./ethpillar.sh check-updates system
    echo "$output"
    [ "$status" -eq 2 ]
    [[ "$output" == *"system: update(s) available"* ]]
    grep -q "apt update" "$COMMAND_LOG"
}

@test "check-updates system: exits 0 when up to date" {
    run ./ethpillar.sh check-updates system
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"system: up to date"* ]]
}

@test "restart system: reboots the host" {
    run ./ethpillar.sh restart system
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"restart: system"* ]]
    grep -q "reboot now" "$COMMAND_LOG"
    ! grep -q "systemctl restart" "$COMMAND_LOG"
}

@test "restart all: does not reboot the host" {
    write_service "$EXEC_SERVICE_FILE"
    set_unit_state execution active

    run ./ethpillar.sh restart all
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "systemctl restart execution" "$COMMAND_LOG"
    ! grep -q "reboot" "$COMMAND_LOG"
}

@test "start system / stop system: rejected" {
    run ./ethpillar.sh start system
    [ "$status" -eq 1 ]
    [[ "$output" == *"only valid for restart"* ]]
    ! grep -q "reboot" "$COMMAND_LOG"

    run ./ethpillar.sh stop system
    [ "$status" -eq 1 ]
    [[ "$output" == *"only valid for restart"* ]]
    ! grep -q "reboot" "$COMMAND_LOG"
}
