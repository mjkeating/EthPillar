#!/usr/bin/env bats
#
# tests/test_ethpillar_installnode.bats
#
# Tests for the ACTUAL installNode() function in ethpillar.sh.
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export COMMAND_LOG=$(mktemp)
    export MOCK_BIN_DIR=$(mktemp -d)
    export TEST_SYSTEMD_DIR=$(mktemp -d)

    # ── Robust Mocking System ─────────────────────────────────────────────────
    mock_template() {
        local cmd="$1"
        shift
        printf "%s" "$cmd" >> "$COMMAND_LOG"
        for arg in "$@"; do
            printf " <%s>" "$arg" >> "$COMMAND_LOG"
        done
        echo >> "$COMMAND_LOG"
    }
    export -f mock_template

    create_mock() {
        local name="$1"
        cat <<EOF > "$MOCK_BIN_DIR/$name"
#!/bin/bash
mock_template "$name" "\$@"
EOF
        chmod +x "$MOCK_BIN_DIR/$name"
    }

    create_mock "whiptail"
    create_mock "runScript"
    
    export PATH="$MOCK_BIN_DIR:$PATH"

    # ── Prepare ethpillar.sh for testing ──────────────────────────────────────
    sed "s|/etc/systemd/system/|$TEST_SYSTEMD_DIR/|g" ethpillar.sh > ethpillar_test.sh

    # Mock all the high-level functions that run on source
    initializeNetwork() { :; }
    export -f initializeNetwork
    menuMain() { :; }
    export -f menuMain
    getBackTitle() { :; }
    export -f getBackTitle
    source_functions() { :; }
    export -f source_functions
    
    # Source the script
    source ./ethpillar_test.sh || true
}

teardown() {
    rm -f "$COMMAND_LOG"
    rm -rf "$MOCK_BIN_DIR"
    rm -rf "$TEST_SYSTEMD_DIR"
    rm -f ethpillar_test.sh
}

@test "installNode: routes Solo Staking Node selection" {
    whiptail() {
        mock_template "whiptail" "$@"
        echo "Solo Staking Node" >&2
        return 0
    }

    installNode

    run cat "$COMMAND_LOG"
    [[ "$output" == *"runScript <deploy/install-node.sh> <deploy/deploy-node.py> <true> <--install_config> <Solo Staking Node>"* ]]
}

@test "installNode: routes Full Node Only selection" {
    whiptail() {
        mock_template "whiptail" "$@"
        echo "Full Node Only" >&2
        return 0
    }

    installNode

    run cat "$COMMAND_LOG"
    [[ "$output" == *"runScript <deploy/install-node.sh> <deploy/deploy-node.py> <true> <--install_config> <Full Node Only>"* ]]
}

@test "installNode: routes Aztec selection to plugin_aztec.sh" {
    whiptail() {
        mock_template "whiptail" "$@"
        echo "Aztec L2 Sequencer" >&2
        return 0
    }

    run installNode

    run cat "$COMMAND_LOG"
    [[ "$output" == *"runScript <plugins/aztec/plugin_aztec.sh> <-i>"* ]]
}

@test "installNode: is no-op if services exist" {
    touch "$TEST_SYSTEMD_DIR/consensus.service"
    
    installNode

    run cat "$COMMAND_LOG"
    [[ "$output" != *"whiptail"* ]]
    [[ "$output" != *"runScript"* ]]
}

@test "installNode: respects whiptail cancel" {
    whiptail() {
        return 1 # Cancel
    }

    installNode

    run cat "$COMMAND_LOG"
    [[ "$output" != *"runScript"* ]]
}
