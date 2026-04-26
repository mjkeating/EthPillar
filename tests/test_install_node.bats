#!/usr/bin/env bats
#
# tests/test_install_node.bats
#
# Tests for install-node.sh argument handling and forwarding logic.
# These tests run the ACTUAL script and verify behavior via mocks.
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export COMMAND_LOG="$PWD/tests/mock_calls.log"
    : > "$COMMAND_LOG"

    export MOCK_BIN_DIR="$PWD/tests/mock_bin"
    mkdir -p "$MOCK_BIN_DIR"
    
    # Define HOME and create expected structure
    export HOME="/tmp/test_home"
    mkdir -p "$HOME/.local/bin"

    # Robust mock creator
    create_mock() {
        local name="$1"
        local stdout="${2:-}"
        cat <<EOF > "$MOCK_BIN_DIR/$name"
#!/bin/bash
echo "$name \$*" >> "$COMMAND_LOG"
# If python3 is called to create a venv, we must create the fake pip
if [ "$name" == "python3" ] && [[ "\$*" == *"-m venv"* ]]; then
    mkdir -p "$HOME/.local/bin"
    cat <<PIPOV > "$HOME/.local/bin/pip"
#!/bin/bash
echo "pip \\\$*" >> "$COMMAND_LOG"
exit 0
PIPOV
    chmod +x "$HOME/.local/bin/pip"
fi
if [ -n "$stdout" ]; then echo "$stdout"; fi
exit 0
EOF
        chmod +x "$MOCK_BIN_DIR/$name"
    }

    create_mock "lscpu" "x86_64"
    create_mock "uname" "Linux"
    create_mock "which" "/usr/bin/python3"
    
    for cmd in apt-get git python3 usermod mkdir stty curl pip; do
        create_mock "$cmd"
    done

    # sudo mock
    cat <<EOF > "$MOCK_BIN_DIR/sudo"
#!/bin/bash
export PATH="$MOCK_BIN_DIR:\$PATH"
"\$@"
EOF
    chmod +x "$MOCK_BIN_DIR/sudo"

    export PATH="$MOCK_BIN_DIR:$PATH"
    export USER="testuser"
}

teardown() {
    rm -rf "$MOCK_BIN_DIR"
    rm -f "$COMMAND_LOG"
    rm -rf "/tmp/test_home"
}

@test "install-node.sh: uses default script if none provided" {
    run bash deploy/install-node.sh "true"
    [ "$status" -eq 0 ]
    grep -q "python3 .*deploy-node.py" "$COMMAND_LOG"
}

@test "install-node.sh: invokes python3 with correct arguments" {
    # Test simplified call (no script name, no "true")
    run bash deploy/install-node.sh "--install_config" "Solo Staking Node"
    
    [ "$status" -eq 0 ]
    grep -q "python3 .*deploy-node.py" "$COMMAND_LOG"
    grep -q "Solo Staking Node" "$COMMAND_LOG"
}

@test "install-node.sh: installs dependencies" {
    run bash deploy/install-node.sh "--some-arg"
    [ "$status" -eq 0 ]
    
    grep -q "apt-get update" "$COMMAND_LOG"
    grep -q "apt-get install" "$COMMAND_LOG"
}
