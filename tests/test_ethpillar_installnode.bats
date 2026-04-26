#!/usr/bin/env bats
#
# tests/test_ethpillar_installnode.bats
#
# Tests for the ACTUAL installNode() function in ethpillar.sh.
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export MOCK_BIN_DIR=$(mktemp -d)
    mkdir -p "$MOCK_BIN_DIR"
    export COMMAND_LOG=$(mktemp)

    create_mock() {
        local name="$1"
        cat <<EOF > "$MOCK_BIN_DIR/$name"
#!/bin/bash
echo "$name \$*" >> "$COMMAND_LOG"
exit 0
EOF
        chmod +x "$MOCK_BIN_DIR/$name"
    }

    create_mock "whiptail"
    create_mock "runScript"
    export PATH="$MOCK_BIN_DIR:$PATH"

    # Patch hardcoded paths
    export TEST_SYSTEMD_DIR=$(mktemp -d)
    sed "s|/etc/systemd/system/|$TEST_SYSTEMD_DIR/|g" ethpillar.sh > ethpillar_testable.sh

    # Mock side-effects
    initializeNetwork() { :; }
    export -f initializeNetwork
    menuMain() { :; }
    export -f menuMain
    getBackTitle() { :; }
    export -f getBackTitle
    source_functions() { :; }
    export -f source_functions
    
    # Extract function
    sed -n '/^function installNode(){/,/^}/p' ethpillar_testable.sh > installNode_fn.sh
    source ./installNode_fn.sh
}

teardown() {
    rm -rf "$MOCK_BIN_DIR"
    rm -f "$COMMAND_LOG"
    rm -rf "$TEST_SYSTEMD_DIR"
    rm -f ethpillar_testable.sh
    rm -f installNode_fn.sh
}

@test "installNode: routes Solo Staking Node selection" {
    # Mock whiptail via script to ensure subshell inheritance
    cat <<EOF > "$MOCK_BIN_DIR/whiptail"
#!/bin/bash
echo "Solo Staking Node" >&2
exit 0
EOF

    installNode
    grep -q "runScript .*Solo Staking Node" "$COMMAND_LOG"
}

@test "installNode: routes Full Node Only selection" {
    cat <<EOF > "$MOCK_BIN_DIR/whiptail"
#!/bin/bash
echo "Full Node Only" >&2
exit 0
EOF

    installNode
    grep -q "runScript .*Full Node Only" "$COMMAND_LOG"
}

@test "installNode: routes Custom Setup selection" {
    cat <<EOF > "$MOCK_BIN_DIR/whiptail"
#!/bin/bash
echo "Custom Setup" >&2
exit 0
EOF

    installNode
    grep -q "runScript .*Custom Setup" "$COMMAND_LOG"
}

@test "installNode: is no-op if services exist" {
    touch "$TEST_SYSTEMD_DIR/consensus.service"
    installNode
    [ ! -s "$COMMAND_LOG" ]
}

@test "installNode: handles whiptail cancellation" {
    cat <<EOF > "$MOCK_BIN_DIR/whiptail"
#!/bin/bash
exit 1
EOF

    run installNode
    [ "$status" -eq 1 ]
}
