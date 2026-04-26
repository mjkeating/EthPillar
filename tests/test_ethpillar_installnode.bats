#!/usr/bin/env bats
#
# tests/test_ethpillar_installnode.bats
#
# Tests for the installNode() function in ethpillar.sh.
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export COMMAND_LOG=$(mktemp)
    export TEST_SYSTEMD_DIR=$(mktemp -d)
    export TEST_AZTEC_DIR=$(mktemp -d)

    # ── Extract ONLY the installNode function ─────────────────────────────────
    sed -n '/^function installNode(){/,/^}/p' ethpillar.sh > installNode_fn.sh
    
    # Patch the paths
    sed -i -e "s|/etc/systemd/system/|$TEST_SYSTEMD_DIR/|g" \
           -e "s|/opt/ethpillar/aztec|$TEST_AZTEC_DIR|g" \
           installNode_fn.sh

    # ── Mock external commands ────────────────────────────────────────────────
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

    whiptail() {
        mock_cmd "whiptail" "$@"
        echo "${WHIPTAIL_OUTPUT:-Solo Staking Node}" >&2
        return "${WHIPTAIL_EXIT_CODE:-0}"
    }
    export -f whiptail

    runScript() {
        mock_cmd "runScript" "$@"
    }
    export -f runScript

    # Source isolated function
    source ./installNode_fn.sh
}

teardown() {
    rm -f "$COMMAND_LOG"
    rm -rf "$TEST_SYSTEMD_DIR"
    rm -rf "$TEST_AZTEC_DIR"
    rm -f installNode_fn.sh
}

@test "installNode: calls runScript for Solo Staking Node" {
    WHIPTAIL_OUTPUT="Solo Staking Node"
    WHIPTAIL_EXIT_CODE=0

    installNode

    run cat "$COMMAND_LOG"
    [[ "$output" == *"runScript"* ]]
    [[ "$output" == *"deploy/install-node.sh"* ]]
    [[ "$output" == *"deploy/deploy-node.py"* ]]
    [[ "$output" == *"Solo Staking Node"* ]]
}

@test "installNode: calls plugin_aztec.sh when Aztec selected" {
    WHIPTAIL_OUTPUT="Aztec L2 Sequencer"
    WHIPTAIL_EXIT_CODE=0

    run installNode

    run cat "$COMMAND_LOG"
    [[ "$output" == *"runScript"* ]]
    [[ "$output" == *"plugins/aztec/plugin_aztec.sh"* ]]
}

@test "installNode: is no-op if services exist" {
    touch "$TEST_SYSTEMD_DIR/consensus.service"

    installNode

    run cat "$COMMAND_LOG"
    [[ "$output" != *"whiptail"* ]]
    [[ "$output" != *"runScript"* ]]
}

@test "installNode: exits on Cancel" {
    WHIPTAIL_EXIT_CODE=1

    installNode

    run cat "$COMMAND_LOG"
    [[ "$output" == *"whiptail"* ]]
    [[ "$output" != *"runScript"* ]]
}
