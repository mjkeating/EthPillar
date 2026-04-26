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

    # Mock system commands
    mock_cmd() {
        local cmd="$1"
        shift
        printf "%s" "$cmd" >> "$COMMAND_LOG"
        for arg in "$@"; do
            printf " <%s>" "$arg" >> "$COMMAND_LOG"
        done
        echo >> "$COMMAND_LOG"
    }
    export -f mock_cmd
    
    # Export mocks
    apt-get()   { mock_cmd "apt-get" "$@"; }
    export -f apt-get
    git()       { mock_cmd "git" "$@"; }
    export -f git
    python3()   { 
        # Handle -m venv call to not break script
        if [[ "$*" == *"-m venv"* ]]; then
            mock_cmd "python3" "$@"
            return 0
        fi
        mock_cmd "python3" "$@"
    }
    export -f python3
    sudo()      { 
        if declare -f "$1" > /dev/null; then
            "$@"
        else
            mock_cmd "sudo" "$@"
        fi
    }
    export -f sudo
    usermod()   { mock_cmd "usermod" "$@"; }
    export -f usermod
    mkdir()     { mock_cmd "mkdir" "$@"; }
    export -f mkdir
    lscpu()     { echo "x86_64"; }
    export -f lscpu
    uname()     { echo "Linux"; }
    export -f uname
    which()     { return 0; }
    export -f which
    
    # Create pip mock and add to PATH
    echo '#!/bin/bash' > "$MOCK_BIN_DIR/pip"
    echo 'echo "pip $*" >> "'"$COMMAND_LOG"'"' >> "$MOCK_BIN_DIR/pip"
    chmod +x "$MOCK_BIN_DIR/pip"
    export PATH="$MOCK_BIN_DIR:$PATH"
    
    # Create a fake ~/.local/bin/pip to satisfy the script
    # We'll use a symlink to our mock
    LOCAL_BIN="$HOME/.local/bin"
    mkdir -p "$LOCAL_BIN" || true
    ln -sf "$MOCK_BIN_DIR/pip" "$LOCAL_BIN/pip" || true
}

teardown() {
    rm -f "$COMMAND_LOG"
    rm -rf "$MOCK_BIN_DIR"
}

@test "install-node.sh: exits 1 when no arguments given" {
    run bash deploy/install-node.sh
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR: Missing deploy file"* ]]
}

@test "install-node.sh: rejects filenames with path traversal" {
    run bash deploy/install-node.sh "../../deploy/deploy-node.py"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR: Invalid deploy file"* ]]
}

@test "install-node.sh: invokes python3 with correct arguments" {
    run bash deploy/install-node.sh "deploy/deploy-node.py" "true" "--install_config" "Solo Staking Node"
    
    run cat "$COMMAND_LOG"
    [[ "$output" == *"python3"* ]]
    [[ "$output" == *"deploy/deploy-node.py"* ]]
    [[ "$output" == *"--skip_prompts"* ]]
    [[ "$output" == *"true"* ]]
    [[ "$output" == *"--install_config"* ]]
    [[ "$output" == *"Solo Staking Node"* ]]
}

@test "install-node.sh: installs dependencies" {
    run bash deploy/install-node.sh "deploy/deploy-node.py" "true"
    
    run cat "$COMMAND_LOG"
    [[ "$output" == *"apt-get"* ]]
    [[ "$output" == *"git"* ]]
}
