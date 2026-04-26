#!/usr/bin/env bats
#
# tests/test_install_node.bats
#
# Tests for install-node.sh argument handling and forwarding logic.
# These tests run the ACTUAL script and verify behavior via mocks.
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export COMMAND_LOG=$(mktemp)
    export MOCK_BIN_DIR=$(mktemp -d)

    # ── Robust Mocking System ─────────────────────────────────────────────────
    # This writes calls to the COMMAND_LOG in a predictable format
    mock_template() {
        local cmd="$1"
        shift
        printf "%s" "$cmd" >> "$COMMAND_LOG"
        for arg in "$@"; do
            printf " [%s]" "$arg" >> "$COMMAND_LOG"
        done
        echo >> "$COMMAND_LOG"
    }
    export -f mock_template

    # Helper to create a mock script in our temp bin dir
    create_mock() {
        local name="$1"
        local exit_code="${2:-0}"
        local output="${3:-}"
        cat <<EOF > "$MOCK_BIN_DIR/$name"
#!/bin/bash
mock_template "$name" "\$@"
if [ -n "$output" ]; then echo "$output"; fi
exit $exit_code
EOF
        chmod +x "$MOCK_BIN_DIR/$name"
    }

    # Create mocks for all system commands called by install-node.sh
    create_mock "lscpu" 0 "x86_64"
    create_mock "uname" 0 "Linux"
    create_mock "which" 0 "/usr/bin/python3"
    create_mock "stty" 0 ""
    create_mock "curl" 0 ""
    create_mock "apt-get" 0 ""
    create_mock "git" 0 ""
    create_mock "usermod" 0 ""
    create_mock "mkdir" 0 ""
    create_mock "python3" 0 ""
    create_mock "pip" 0 ""
    
    # Special mock for sudo to pass through to our other mocks
    cat <<'EOF' > "$MOCK_BIN_DIR/sudo"
#!/bin/bash
# If the command is in our mock dir, call it directly
if [ -x "$MOCK_BIN_DIR/$1" ]; then
    shift
    "$MOCK_BIN_DIR/${BASH_ARGV[$((${#BASH_ARGV[@]}-1))]}" "$@"
    # Note: BASH_ARGV logic is tricky, lets just use a simpler check
fi

# Simpler sudo: just remove 'sudo' and run the rest
# But we need to make sure we call our mocks if they exist
cmd=$1
shift
if command -v "$cmd" >/dev/null; then
    "$cmd" "$@"
else
    # Just log that sudo was called for an unknown command
    echo "sudo [$cmd] $@" >> "$COMMAND_LOG"
fi
EOF
    # Actually, a simpler sudo mock is better:
    cat <<EOF > "$MOCK_BIN_DIR/sudo"
#!/bin/bash
"\$@"
EOF
    chmod +x "$MOCK_BIN_DIR/sudo"

    export PATH="$MOCK_BIN_DIR:$PATH"
    
    # Set up the expected ETHPILLAR_DIR for the script
    export ETHPILLAR_DIR="$PWD"
}

teardown() {
    rm -f "$COMMAND_LOG"
    rm -rf "$MOCK_BIN_DIR"
}

# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@test "install-node.sh: rejects invalid filenames" {
    run bash deploy/install-node.sh "wrong.py"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR: Invalid deploy file"* ]]
}

@test "install-node.sh: invokes python3 with correct forwarded arguments" {
    # We use 'true' as 2nd arg to skip the 'wait_for_user' prompt
    run bash deploy/install-node.sh "deploy/deploy-node.py" "true" "--install_config" "Solo Staking Node"
    
    run cat "$COMMAND_LOG"
    # Should check requirements
    [[ "$output" == *"lscpu"* ]]
    # Should install dependencies
    [[ "$output" == *"apt-get <update>"* ]]
    # Check if python3 was called with the correct arguments
    run cat "$COMMAND_LOG"
    [[ "$output" == *"python3"* ]]
    [[ "$output" == *"deploy/deploy-node.py"* ]]
    [[ "$output" == *"<--skip_prompts> <true>"* ]]
    [[ "$output" == *"<--install_config> <Solo Staking Node>"* ]]
}

@test "install-node.sh: skip_prompts is correctly omitted when not provided" {
    run bash deploy/install-node.sh "deploy/deploy-node.py" ""
    
    run cat "$COMMAND_LOG"
    [[ "$output" == *"python3"* ]]
    [[ "$output" == *"deploy/deploy-node.py"* ]]
    [[ "$output" != *"--skip_prompts"* ]]
}

@test "install-node.sh: handles virtual environment setup" {
    run bash deploy/install-node.sh "deploy/deploy-node.py" "true"
    
    run cat "$COMMAND_LOG"
    # Should try to create venv
    [[ "$output" == *"python3 [-m] [venv]"* ]]
}
