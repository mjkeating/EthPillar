#!/usr/bin/env bats
#
# tests/test_ethpillar_version.bats
#
# Tests for ethpillar.sh --version (CLI version output).
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
args="\$*"
if [[ "\$args" == *"/eth/v1/node/version"* ]]; then
    echo '{"data":{"version":"Lighthouse/v5.3.0"}}'
elif [[ "\$args" == *"web3_clientVersion"* ]]; then
    echo '{"result":"Nethermind/v1.30.0"}'
else
    echo '{}'
fi
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/curl"

    cat <<'EOF' > "$MOCK_BIN_DIR/jq"
#!/bin/bash
filter="${@: -1}"
input=$(cat)
case "$filter" in
  *'.data.version'*)
    printf '%s' "$input" | grep -o '"version":"[^"]*"' | head -1 | cut -d'"' -f4
    ;;
  *'.result'*)
    if printf '%s' "$input" | grep -q '"result"'; then
      printf '%s' "$input" | grep -o '"result":"[^"]*"' | head -1 | cut -d'"' -f4
    else
      echo "null"
    fi
    ;;
esac
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/jq"

    sed "s|/etc/systemd/system/|$TEST_SYSTEMD_DIR/|g" ethpillar.sh > ethpillar_testable.sh
    chmod +x ethpillar_testable.sh

    export PATH="$MOCK_BIN_DIR:$PATH"
}

teardown() {
    rm -rf "$MOCK_BIN_DIR" "$TEST_SYSTEMD_DIR" "${ETHPILLAR_VENV:-/tmp/ethpillar_bats_version_venv}"
    rm -f "$COMMAND_LOG" ethpillar_testable.sh
}

@test "--version: exits 0 and prints default lines when no clients installed" {
    run ./ethpillar_testable.sh --version
    [ "$status" -eq 0 ]
    [[ "$output" == *"Consensus client: Not installed or still starting up."* ]]
    [[ "$output" == *"Execution client: Not installed or still starting up."* ]]
    [[ "$output" == *"Validator client: Not installed."* ]]
    [[ "$output" == *"Mev-boost: Not Installed"* ]]
    ep_version=$(grep '^EP_VERSION=' ethpillar.sh | cut -d'"' -f2)
    [[ "$output" == *"EthPillar: $ep_version"* ]]
    ! grep -q whiptail "$COMMAND_LOG"
}

@test "--version: prints client versions from RPC when services exist" {
    touch "$TEST_SYSTEMD_DIR/execution.service"
    touch "$TEST_SYSTEMD_DIR/consensus.service"
    touch "$TEST_SYSTEMD_DIR/mevboost.service"

    cat <<EOF > "$MOCK_BIN_DIR/mev-boost"
#!/bin/bash
echo "mev-boost \$*" >> "$COMMAND_LOG"
echo "mev-boost version v1.8.0"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/mev-boost"

    run ./ethpillar_testable.sh --version
    [ "$status" -eq 0 ]
    [[ "$output" == *"Consensus client: Lighthouse/v5.3.0"* ]]
    [[ "$output" == *"Execution client: Nethermind/v1.30.0"* ]]
    [[ "$output" == *"Mev-boost: 1.8.0"* ]]
    [[ "$output" == *"Validator client: Not installed."* ]]
    ! grep -q whiptail "$COMMAND_LOG"
}
