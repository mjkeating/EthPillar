#!/usr/bin/env bats
#
# tests/test_menu_visibility.bats
#
# Helpers that hide inapplicable TUI rows (plot EL times, peer count, eth-duties,
# Beaconcha.in dashboard) when local units are missing.
#
# Run: bats tests/test_menu_visibility.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."
	# shellcheck disable=SC1091
	source ./functions.sh

	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export EXEC_SERVICE_FILE="$TEST_DIR/execution.service"
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export VALIDATOR_SERVICE_FILE="$TEST_DIR/validator.service"
	export CSM_VALIDATOR_SERVICE_FILE="$TEST_DIR/csm_nimbusvalidator.service"
	export SSV_NODE_SERVICE_FILE="$TEST_DIR/ssv-node.service"
	rm -f "$EXEC_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE" \
		"$CSM_VALIDATOR_SERVICE_FILE" "$SSV_NODE_SERVICE_FILE"
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_execution() {
	local name="${1:-Geth}"
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Execution Client for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,}
EOF
}

write_consensus() {
	local name="${1:-Lighthouse}"
	local extra="${2:-}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --network=mainnet ${extra}
EOF
}

write_validator() {
	cat > "$VALIDATOR_SERVICE_FILE" <<'EOF'
[Unit]
Description=Lighthouse Validator Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/lighthouse validator_client
EOF
}

write_ssv() {
	cat > "$SSV_NODE_SERVICE_FILE" <<'EOF'
[Unit]
Description=SSV Operator Node
[Service]
ExecStart=/usr/local/bin/ssvnode start-node --config /var/lib/ssv/config.yaml
EOF
}

write_caplin() {
	cat > "$EXEC_SERVICE_FILE" <<'EOF'
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET
[Service]
ExecStart=/usr/local/bin/erigon --caplin.discovery.port=9000
EOF
}

# ── hasExecutionService ───────────────────────────────────────────────────────

@test "hasExecutionService is true when execution.service exists" {
	write_execution
	run hasExecutionService
	[ "$status" -eq 0 ]
}

@test "hasExecutionService is false when execution.service is missing" {
	run hasExecutionService
	[ "$status" -eq 1 ]
}

# ── hasLocalBeaconStack ───────────────────────────────────────────────────────

@test "hasLocalBeaconStack is true with execution only" {
	write_execution
	run hasLocalBeaconStack
	[ "$status" -eq 0 ]
}

@test "hasLocalBeaconStack is true with consensus only" {
	write_consensus
	run hasLocalBeaconStack
	[ "$status" -eq 0 ]
}

@test "hasLocalBeaconStack is true for Caplin integrated EL" {
	write_caplin
	run hasLocalBeaconStack
	[ "$status" -eq 0 ]
}

@test "hasLocalBeaconStack is false on SSV-only host" {
	write_ssv
	run hasLocalBeaconStack
	[ "$status" -eq 1 ]
}

@test "hasLocalBeaconStack is false on VC-only host" {
	write_validator
	run hasLocalBeaconStack
	[ "$status" -eq 1 ]
}

# ── hasLocalConsensusRpc ──────────────────────────────────────────────────────

@test "hasLocalConsensusRpc is true with consensus.service" {
	write_consensus
	run hasLocalConsensusRpc
	[ "$status" -eq 0 ]
}

@test "hasLocalConsensusRpc is true for Caplin without consensus.service" {
	write_caplin
	run hasLocalConsensusRpc
	[ "$status" -eq 0 ]
}

@test "hasLocalConsensusRpc is false with execution-only non-Caplin" {
	write_execution Geth
	run hasLocalConsensusRpc
	[ "$status" -eq 1 ]
}

# ── hasLocalValidatorTooling ──────────────────────────────────────────────────

@test "hasLocalValidatorTooling is true with separate validator.service" {
	write_validator
	run hasLocalValidatorTooling
	[ "$status" -eq 0 ]
}

@test "hasLocalValidatorTooling is true for integrated Grandine" {
	write_consensus Grandine "--keystore-dir=/var/lib/grandine/validator_keys"
	run hasLocalValidatorTooling
	[ "$status" -eq 0 ]
}

@test "hasLocalValidatorTooling is true with CSM plugin only" {
	touch "$CSM_VALIDATOR_SERVICE_FILE"
	run hasLocalValidatorTooling
	[ "$status" -eq 0 ]
}

@test "hasLocalValidatorTooling is false on SSV-only host" {
	write_ssv
	run hasLocalValidatorTooling
	[ "$status" -eq 1 ]
}

@test "hasLocalValidatorTooling is false on full node without VC" {
	write_execution
	write_consensus
	run hasLocalValidatorTooling
	[ "$status" -eq 1 ]
}

# ── ethpillar.sh wiring ───────────────────────────────────────────────────────

@test "ethpillar gates plot, peer count, and validator toolbox rows" {
	grep -q 'hasExecutionService' ethpillar.sh
	grep -q 'Plot Execution Block Processing Times' ethpillar.sh
	grep -q 'hasLocalBeaconStack' ethpillar.sh
	grep -q 'hasLocalValidatorTooling' ethpillar.sh
	grep -q 'eth-duties: Show upcoming' ethpillar.sh
}
