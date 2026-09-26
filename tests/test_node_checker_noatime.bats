#!/usr/bin/env bats
#
# tests/test_node_checker_noatime.bats
#
# Unit tests for check_noatime live-mount helpers in plugins/node-checker/run.sh.
# Does not start Ethereum clients. Mocks findmnt OPTIONS and fstab.
#
# Run: bats tests/test_node_checker_noatime.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."

	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export EXEC_SERVICE_FILE="$TEST_DIR/execution.service"
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export NODE_CHECKER_FSTAB="$TEST_DIR/fstab"
	export ETHPILLAR_ENV_FILE="${PWD}/env"

	export EL_DIR="$TEST_DIR/reth"
	export CL_DIR="$TEST_DIR/lodestar"
	mkdir -p "$EL_DIR" "$CL_DIR"

	# Guest fstab without noatime (Proxmox bind-mount case).
	cat > "$NODE_CHECKER_FSTAB" <<'EOF'
# <file system> <mount point> <type> <options> <dump> <pass>
UUID=root / ext4 errors=remount-ro 0 1
EOF

	# shellcheck disable=SC1091
	source ./plugins/node-checker/run.sh

	total_checks=0
	failed_checks=0
	warning_checks=0
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_execution() {
	local flag="${1:---datadir=${EL_DIR}}"
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=Reth Execution Layer Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/reth node \\
    --chain mainnet \\
    ${flag} \\
    --http
EOF
}

write_consensus() {
	local flag="${1:---dataDir=${CL_DIR}}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=Lodestar Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/lodestar beacon \\
    --network=mainnet \\
    ${flag} \\
    --rest.port=5052
EOF
}

# check_noatime mutates failed_checks/warning_checks; call it in this shell.
check_noatime_capture() {
	check_noatime > "$TEST_DIR/noatime.out" 2>&1
	cat "$TEST_DIR/noatime.out"
}

# ── extract / options helpers ─────────────────────────────────────────────────

@test "node_checker_extract_datadir_paths reads common EL/CL flags" {
	write_execution "--datadir=${EL_DIR}"
	write_consensus "--dataDir=${CL_DIR}"
	run node_checker_extract_datadir_paths "$EXEC_SERVICE_FILE"
	[ "$output" = "$EL_DIR" ]
	run node_checker_extract_datadir_paths "$CONSENSUS_SERVICE_FILE"
	[ "$output" = "$CL_DIR" ]
}

@test "node_checker_extract_datadir_paths accepts equals, space, and quotes" {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/nethermind --data-dir="${EL_DIR}" --http
EOF
	run node_checker_extract_datadir_paths "$EXEC_SERVICE_FILE"
	[ "$output" = "$EL_DIR" ]

	cat > "$EXEC_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/besu --data-path='${EL_DIR}' --rpc-http-enabled
EOF
	run node_checker_extract_datadir_paths "$EXEC_SERVICE_FILE"
	[ "$output" = "$EL_DIR" ]

	cat > "$EXEC_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/ethrex --datadir ${EL_DIR} --http
EOF
	run node_checker_extract_datadir_paths "$EXEC_SERVICE_FILE"
	[ "$output" = "$EL_DIR" ]

	cat > "$EXEC_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/nimbus --data-dir=${EL_DIR} --rest
EOF
	run node_checker_extract_datadir_paths "$EXEC_SERVICE_FILE"
	[ "$output" = "$EL_DIR" ]

	cat > "$EXEC_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/teku --data.path=${EL_DIR} --rest-api-enabled
EOF
	run node_checker_extract_datadir_paths "$EXEC_SERVICE_FILE"
	[ "$output" = "$EL_DIR" ]

	cat > "$EXEC_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/client --db-path=${EL_DIR} --base-path=${CL_DIR}
EOF
	run node_checker_extract_datadir_paths "$EXEC_SERVICE_FILE"
	[[ "$output" == *"$EL_DIR"* ]]
	[[ "$output" == *"$CL_DIR"* ]]
}

@test "node_checker_extract_datadir_paths skips --datadir.static-files" {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/reth node --datadir=${EL_DIR} --datadir.static-files=/mnt/hdd/static --http
EOF
	run node_checker_extract_datadir_paths "$EXEC_SERVICE_FILE"
	[ "$output" = "$EL_DIR" ]
}

@test "node_checker_options_has_noatime matches only the noatime option" {
	run node_checker_options_has_noatime "rw,noatime"
	[ "$status" -eq 0 ]
	run node_checker_options_has_noatime "rw,relatime"
	[ "$status" -ne 0 ]
	run node_checker_options_has_noatime "rw,nodiratime"
	[ "$status" -ne 0 ]
	run node_checker_options_has_noatime ""
	[ "$status" -ne 0 ]
}

@test "node_checker_resolved_elcl_datadirs skips missing paths" {
	write_execution "--datadir=${TEST_DIR}/does-not-exist"
	write_consensus "--dataDir=${CL_DIR}"
	run node_checker_resolved_elcl_datadirs
	[ "$output" = "$CL_DIR" ]
}

# ── check_noatime behavior ────────────────────────────────────────────────────

@test "check_noatime PASSes live noatime on EL/CL data when fstab has none" {
	write_execution
	write_consensus
	node_checker_findmnt_options() {
		case "$1" in
			"$EL_DIR"|"$CL_DIR") echo "rw,noatime" ;;
			*) echo "rw,relatime" ;;
		esac
	}

	check_noatime_capture
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"[PASS]"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"noatime on EL/CL data: ${EL_DIR}, ${CL_DIR}"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 0 ]
	[ "$total_checks" -eq 1 ]
}

@test "check_noatime FAILs and names the path missing noatime" {
	write_execution
	write_consensus
	node_checker_findmnt_options() {
		case "$1" in
			"$EL_DIR") echo "rw,relatime" ;;
			"$CL_DIR") echo "rw,noatime" ;;
			*) echo "rw,relatime" ;;
		esac
	}

	check_noatime_capture
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"[FAIL]"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"noatime missing on: ${EL_DIR}"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"ok: ${CL_DIR}"* ]]
	[ "$failed_checks" -eq 1 ]
}

@test "check_noatime WARNs when no EL/CL datadir is resolvable" {
	# Service files exist but have no datadir flags (or VC-only).
	cat > "$EXEC_SERVICE_FILE" <<'EOF'
[Service]
ExecStart=/usr/local/bin/reth node --http
EOF
	cat > "$CONSENSUS_SERVICE_FILE" <<'EOF'
[Service]
ExecStart=/usr/local/bin/lodestar beacon --rest.port=5052
EOF

	check_noatime_capture
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"[WARN]"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"noatime not checked"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"validator-only"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"no local EL/CL chaindata"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_noatime WARNs on validator-only node and does not FAIL on fstab" {
	rm -f "$EXEC_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE"
	cat > "$NODE_CHECKER_FSTAB" <<'EOF'
UUID=root / ext4 errors=remount-ro 0 1
EOF

	check_noatime_capture
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"[WARN]"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"noatime not checked"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"no local EL/CL chaindata"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_noatime fstab noatime is not a FAIL when datadir is unresolved" {
	cat > "$NODE_CHECKER_FSTAB" <<'EOF'
UUID=root / ext4 errors=remount-ro,noatime 0 1
EOF
	rm -f "$EXEC_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE"

	check_noatime_capture
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"[WARN]"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"noatime not checked"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"fstab mentions noatime (not used as a gate)"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_noatime WARNs when findmnt OPTIONS are empty for a resolved path" {
	write_execution "--datadir=${EL_DIR}"
	rm -f "$CONSENSUS_SERVICE_FILE"
	node_checker_findmnt_options() { echo ""; }

	check_noatime_capture
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"[WARN]"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" == *"$EL_DIR"* ]]
	[[ "$(cat "$TEST_DIR/noatime.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_noatime does not use /etc/fstab as the sole gate" {
	[ "$(type -t node_checker_resolved_elcl_datadirs)" = "function" ]
	[ "$(type -t node_checker_findmnt_options)" = "function" ]
	grep -q 'node_checker_resolved_elcl_datadirs' plugins/node-checker/run.sh
	grep -q 'findmnt -T' plugins/node-checker/run.sh
	# fstab must never be a FAIL gate (VC-only is WARN).
	! grep -A20 '^node_checker_noatime_unresolved_fallback()' plugins/node-checker/run.sh | grep -q 'print_check_result "FAIL"'
	! grep -A6 '^check_noatime()' plugins/node-checker/run.sh | grep -q 'grep -q "noatime" /etc/fstab'
}
