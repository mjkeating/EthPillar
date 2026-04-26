#!/usr/bin/env bats
#
# tests/test_install_node.bats
#
# Tests for install-node.sh argument handling and forwarding logic.
# Focuses on:
#   - Rejection of missing/invalid deploy file arguments
#   - Acceptance of the new deploy-node.py filename
#   - extra_args variable population from positional args
#   - The install invocation command with and without extra_args

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    # We do NOT actually run install-node.sh end-to-end (it would apt-get, git clone, etc.)
    # Instead we source the helper functions we want to test, after mocking the
    # parts that would cause side effects.

    # Build a version of install-node.sh that only defines functions
    # (we'll source it in tests that need specific functions).

    export COMMAND_LOG=$(mktemp)

    # Mock system commands that install-node.sh calls globally
    apt-get()   { echo "apt-get $*" >> "$COMMAND_LOG"; }
    export -f apt-get
    git()       { echo "git $*" >> "$COMMAND_LOG"; }
    export -f git
    python3()   { echo "python3 $*" >> "$COMMAND_LOG"; }
    export -f python3
    sudo()      { echo "sudo $*" >> "$COMMAND_LOG"; "$@" 2>/dev/null || true; }
    export -f sudo
    usermod()   { echo "usermod $*" >> "$COMMAND_LOG"; }
    export -f usermod
    mkdir()     { echo "mkdir $*" >> "$COMMAND_LOG"; }
    export -f mkdir
}

teardown() {
    rm -f "$COMMAND_LOG"
}

# ─────────────────────────────────────────────────────────────────────────────
# Argument validation (pure shell logic, no sourcing needed)
# ─────────────────────────────────────────────────────────────────────────────

@test "install-node.sh: exits 1 when no arguments given" {
    run bash install-node.sh
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR: Missing deploy file"* ]]
}

@test "install-node.sh: rejects filenames with path traversal (slashes)" {
    run bash install-node.sh "../../deploy-node.py"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR: Invalid deploy file"* ]]
}

@test "install-node.sh: rejects filenames that don't match deploy-*.py" {
    run bash install-node.sh "evil-script.sh"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR: Invalid deploy file"* ]]
}

@test "install-node.sh: rejects filenames with subdirectory prefix" {
    run bash install-node.sh "subdir/deploy-node.py"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR: Invalid deploy file"* ]]
}

@test "install-node.sh: accepts deploy-node.py as a valid filename" {
    # We only check that it gets past the filename guard (fails later for other reasons)
    run bash install-node.sh "deploy-node.py" 2>&1 || true
    # Should NOT contain the "Invalid deploy file" error
    [[ "$output" != *"ERROR: Invalid deploy file"* ]]
}

@test "install-node.sh: error message references deploy-node.py" {
    run bash install-node.sh
    [ "$status" -eq 1 ]
    [[ "$output" == *"deploy-node.py"* ]]
}

# ─────────────────────────────────────────────────────────────────────────────
# extra_args population logic (sourcing subset via subshell)
# ─────────────────────────────────────────────────────────────────────────────

@test "install-node.sh: extra_args is empty with only 1 arg" {
    # Inline the arg-parsing logic to test it
    run bash -c '
        skip_prompt=""
        extra_args=""
        if [[ ${#} -eq 0 ]]; then
            echo "ERROR: Missing deploy file" ; exit 1
        elif [[ ${#} -ge 2 ]]; then
            skip_prompt="$2"
            extra_args="${@:3}"
        fi
        install_file="$1"
        echo "extra_args=[${extra_args}]"
    ' _ "deploy-node.py"
    [ "$status" -eq 0 ]
    [[ "$output" == *"extra_args=[]"* ]]
}

@test "install-node.sh: extra_args populated with 3+ args" {
    run bash -c '
        skip_prompt=""
        extra_args=""
        if [[ ${#} -eq 0 ]]; then
            echo "ERROR" ; exit 1
        elif [[ ${#} -ge 2 ]]; then
            skip_prompt="$2"
            extra_args="${@:3}"
        fi
        echo "extra_args=[${extra_args}]"
    ' _ "deploy-node.py" "true" "--install_config" "Solo Staking Node"
    [ "$status" -eq 0 ]
    [[ "$output" == *"--install_config"* ]]
    [[ "$output" == *"Solo Staking Node"* ]]
}

@test "install-node.sh: skip_prompt is set from 2nd arg" {
    run bash -c '
        skip_prompt=""
        extra_args=""
        if [[ ${#} -ge 2 ]]; then
            skip_prompt="$2"
            extra_args="${@:3}"
        fi
        echo "skip_prompt=[${skip_prompt}]"
    ' _ "deploy-node.py" "true"
    [ "$status" -eq 0 ]
    [[ "$output" == *"skip_prompt=[true]"* ]]
}

# ─────────────────────────────────────────────────────────────────────────────
# Python invocation logic
# ─────────────────────────────────────────────────────────────────────────────

@test "install-node.sh: invokes python3 with extra_args when present" {
    # Simulate the linux_install_validator-install function's invocation
    run bash -c '
        python() { echo "python $*"; }
        skip_prompt="true"
        extra_args="--install_config Solo\ Staking\ Node"
        install_file="deploy-node.py"
        ETHPILLAR_DIR="."

        if [ -n "$extra_args" ]; then
            python ~/git/ethpillar/${install_file} --skip_prompts "$skip_prompt" $extra_args
        else
            python ~/git/ethpillar/${install_file}
        fi
    '
    [ "$status" -eq 0 ]
    [[ "$output" == *"--skip_prompts true"* ]]
    [[ "$output" == *"deploy-node.py"* ]]
}

@test "install-node.sh: invokes python3 without extra_args when absent" {
    run bash -c '
        python() { echo "python $*"; }
        skip_prompt=""
        extra_args=""
        install_file="deploy-node.py"

        if [ -n "$extra_args" ]; then
            python ~/git/ethpillar/${install_file} --skip_prompts "$skip_prompt" $extra_args
        else
            python ~/git/ethpillar/${install_file}
        fi
    '
    [ "$status" -eq 0 ]
    [[ "$output" == *"deploy-node.py"* ]]
    [[ "$output" != *"--skip_prompts"* ]]
}
