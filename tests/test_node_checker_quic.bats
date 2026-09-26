#!/usr/bin/env bats
#
# tests/test_node_checker_quic.bats
#
# Unit tests for CL QUIC UDP helpers in plugins/node-checker/networking.sh
# (sourced by run.sh). Does not start Ethereum clients.
#
# Run: bats tests/test_node_checker_quic.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."

	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export EXEC_SERVICE_FILE="$TEST_DIR/execution.service"
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export CHARON_SERVICE_FILE="$TEST_DIR/charon.service"
	export ETHPILLAR_ENV_FILE="${PWD}/env"

	# shellcheck disable=SC1091
	source ./plugins/node-checker/run.sh

	total_checks=0
	failed_checks=0
	warning_checks=0
	tcp_check_ports="9000,30303"
	udp_check_ports="9000,30303"
	udp_check_ports_base="9000,30303"
	CL_P2P_PORT="${CL_P2P_PORT:-9000}"
	CL_P2P_PORT_2="${CL_P2P_PORT_2:-9001}"
	NODE_CHECKER_TROUBLESHOOT=0
	NODE_CHECKER_DEBUG=0
	NODE_CHECKER_AUTO_TROUBLESHOOT=0
	NODE_CHECKER_TROUBLESHOOT_PRINTED=0
	NODE_CHECKER_PUBLIC_IPV4=""
	NODE_CHECKER_QUIC_AUTO_INSTALL=0
	unset TEKU_QUIC_IPV6_PORT || true
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_consensus() {
	local name="$1"
	local qport="${2:-${CL_P2P_PORT_2:-9001}}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --quic-port=${qport}
EOF
}

write_consensus_no_quic_flag() {
	local name="$1"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --p2p-port=9000
EOF
}

write_execution() {
	local name="$1"
	local extra="${2:-}"
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Execution Client for MAINNET
[Service]
ExecStart=/usr/local/bin/el ${extra}
EOF
}

write_caplin_execution() {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET
[Service]
ExecStart=/usr/local/bin/erigon --caplin.discovery.port=9000
EOF
}

# ── expected ports / Caplin skip ──────────────────────────────────────────────

@test "expected_cl_quic_udp_ports is 9001 for Lighthouse Nimbus Lodestar Grandine Prysm" {
	for cl in Lighthouse Nimbus Lodestar Grandine Prysm; do
		write_consensus "$cl"
		run expected_cl_quic_udp_ports
		[ "$status" -eq 0 ]
		[ "$output" = "9001" ]
	done
}

@test "expected_cl_quic_udp_ports includes Teku IPv4 9001 and IPv6 9091" {
	write_consensus Teku
	run expected_cl_quic_udp_ports
	[ "$status" -eq 0 ]
	[ "$output" = "9001 9091" ]
}

@test "unknown CL with consensus.service still expects 9001/udp" {
	write_consensus UnknownClient
	run expected_cl_quic_udp_ports
	[ "$status" -eq 0 ]
	[ "$output" = "9001" ]
}

@test "no consensus.service and no Caplin skips QUIC ports" {
	run expected_cl_quic_udp_ports
	[ "$status" -eq 0 ]
	[ -z "$output" ]
	run cl_expects_quic
	[ "$status" -eq 1 ]
}

@test "Erigon-Caplin EL skips QUIC ports" {
	write_caplin_execution
	run is_caplin_node
	[ "$status" -eq 0 ]
	run expected_cl_quic_udp_ports
	[ -z "$output" ]
	run cl_expects_quic
	[ "$status" -eq 1 ]
}

@test "execution.service containing caplin flags is treated as Caplin" {
	write_execution Erigon "--caplin.discovery.port=9000"
	run is_caplin_node
	[ "$status" -eq 0 ]
	run expected_cl_quic_udp_ports
	[ -z "$output" ]
}

@test "Geth plus Lighthouse is not Caplin" {
	write_execution Geth
	write_consensus Lighthouse
	run is_caplin_node
	[ "$status" -eq 1 ]
	run expected_cl_quic_udp_ports
	[ "$output" = "9001" ]
}

@test "CL_P2P_PORT_2 override is used for QUIC UDP when unit has no flag" {
	write_consensus_no_quic_flag Lighthouse
	CL_P2P_PORT_2=19001
	run expected_cl_quic_udp_ports
	[ "$output" = "19001" ]
}

@test "consensus.service --quic-port wins over CL_P2P_PORT_2" {
	write_consensus Lighthouse 19002
	CL_P2P_PORT_2=9001
	run expected_cl_quic_udp_ports
	[ "$output" = "19002" ]
}

# ── udp_check_ports vs expected-4 listen accounting ───────────────────────────

@test "configure_cl_quic_udp_check_ports appends 9001 UDP only for Lighthouse" {
	write_consensus Lighthouse
	tcp_check_ports="9000,30303"
	configure_cl_quic_udp_check_ports
	[ "$udp_check_ports" = "9000,30303,9001" ]
	[ "$tcp_check_ports" = "9000,30303" ]
	[ "${#p2p_ports[@]}" -eq 2 ]
	[ "${p2p_ports[0]}" = "9000" ]
	[ "${p2p_ports[1]}" = "30303" ]
	[ "$ELCL_EXPECTED_LISTEN_COUNT" -eq 4 ]
}

@test "configure_cl_quic_udp_check_ports appends Teku 9001 and 9091 UDP" {
	write_consensus Teku
	configure_cl_quic_udp_check_ports
	[ "$udp_check_ports" = "9000,30303,9001,9091" ]
	[ "$tcp_check_ports" = "9000,30303" ]
	[ "$ELCL_EXPECTED_LISTEN_COUNT" -eq 4 ]
}

@test "configure_cl_quic_udp_check_ports does not add QUIC for Caplin" {
	write_caplin_execution
	configure_cl_quic_udp_check_ports
	[ "$udp_check_ports" = "9000,30303" ]
}

@test "configure_cl_quic_udp_check_ports is idempotent" {
	write_consensus Lighthouse
	configure_cl_quic_udp_check_ports
	configure_cl_quic_udp_check_ports
	[ "$udp_check_ports" = "9000,30303,9001" ]
}

# ── check_cl_quic behavior ────────────────────────────────────────────────────

# check_cl_quic mutates failed_checks/warning_checks; call it in this shell
# (bats `run` uses a subshell and would hide those counters).
check_cl_quic_capture() {
	check_cl_quic > "$TEST_DIR/quic.out" 2>&1
	cat "$TEST_DIR/quic.out"
}

@test "check_cl_quic WARNs for Caplin and does not FAIL missing 9001" {
	write_caplin_execution
	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Glamsterdam"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Caplin has no QUIC by default"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 0 ]
}

@test "check_cl_quic FAILs UFW allow miss and listen miss for Lighthouse" {
	write_consensus Lighthouse
	sudo() { "$@"; }
	ufw() {
		echo "Status: active"
		echo "9000                       ALLOW       Anywhere"
	}
	ss() { echo "tcp LISTEN 0 0 0.0.0.0:9000 0.0.0.0:*"; }
	export -f sudo ufw ss

	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Glamsterdam"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"missing allow rule for CL QUIC 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"CL QUIC port 9001/udp not listening"* ]]
	[ "$failed_checks" -eq 2 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 1 ]
}

@test "check_cl_quic PASSes UFW and listen when 9001/udp is open" {
	write_consensus Lighthouse
	sudo() { "$@"; }
	ufw() {
		echo "Status: active"
		echo "9001/udp                   ALLOW       Anywhere"
	}
	ss() {
		echo "udp UNCONN 0 0 0.0.0.0:9001 0.0.0.0:*"
	}
	export -f sudo ufw ss

	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" == *"UFW allows CL QUIC 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Detected UDP service on CL QUIC port 9001"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 0 ]
}

@test "check_cl_quic skips UFW rule check when firewall is inactive" {
	write_consensus Nimbus
	sudo() { "$@"; }
	ufw() { echo "Status: inactive"; }
	ss() { echo "udp UNCONN 0 0 0.0.0.0:9001 0.0.0.0:*"; }
	export -f sudo ufw ss

	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" != *"missing allow rule"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Detected UDP service on CL QUIC port 9001"* ]]
	[ "$failed_checks" -eq 0 ]
}

@test "check_cl_quic requires Teku 9001 and 9091 UFW rules" {
	write_consensus Teku
	sudo() { "$@"; }
	ufw() {
		echo "Status: active"
		echo "9001/udp                   ALLOW       Anywhere"
	}
	ss() {
		echo "udp UNCONN 0 0 0.0.0.0:9001 0.0.0.0:*"
		echo "udp UNCONN 0 0 [::]:9091 [::]:*"
	}
	export -f sudo ufw ss

	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" == *"UFW allows CL QUIC 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"missing allow rule for CL QUIC 9091/udp"* ]]
	[ "$failed_checks" -eq 1 ]
}

# ── active inbound QUIC probe (quicmap-style, mocked) ─────────────────────────

check_inbound_quic_probe_capture() {
	check_inbound_quic_probe > "$TEST_DIR/qprobe.out" 2>&1
	cat "$TEST_DIR/qprobe.out"
}

@test "cl_quic_ipv4_udp_port is the first expected port (Teku IPv6 stays off IPv4)" {
	write_consensus Lighthouse
	[ "$(cl_quic_ipv4_udp_port)" = "9001" ]
	write_consensus Teku
	[ "$(cl_quic_ipv4_udp_port)" = "9001" ]
}

@test "node_checker_resolve_public_ipv4 uses the TCP checker requester_ip path" {
	fetch_tcp_port_checker() { echo '{"requester_ip":"203.0.113.50","open_ports":[9000]}'; }
	# Call in this shell so a later cache write is visible ( $() is a subshell ).
	node_checker_resolve_public_ipv4 > "$TEST_DIR/ip.out"
	[ "$(cat "$TEST_DIR/ip.out")" = "203.0.113.50" ]
	NODE_CHECKER_PUBLIC_IPV4="$(cat "$TEST_DIR/ip.out")"
	fetch_tcp_port_checker() { echo '{"requester_ip":"198.51.100.1","open_ports":[]}'; }
	[ "$(node_checker_resolve_public_ipv4)" = "203.0.113.50" ]
}

@test "QUIC auto-install is on by default and 0 disables it" {
	unset NODE_CHECKER_QUIC_AUTO_INSTALL
	run node_checker_quic_auto_install_enabled
	[ "$status" -eq 0 ]
	NODE_CHECKER_QUIC_AUTO_INSTALL=0
	run node_checker_quic_auto_install_enabled
	[ "$status" -eq 1 ]
}

@test "check_inbound_quic_probe auto-installs aioquic once then PASSes" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	NODE_CHECKER_QUIC_AUTO_INSTALL=1
	_quic_py_ready=0
	node_checker_quic_python() {
		if [[ "${_quic_py_ready}" -eq 1 ]]; then
			echo "/mock/python"
			return 0
		fi
		return 1
	}
	maybe_install_quic_probe_deps() {
		_quic_py_ready=1
		return 0
	}
	invoke_quic_inbound_probe() {
		echo '{"ok":true,"reason":"server_versions","host":"203.0.113.50","port":9001,"server_versions":["0x1"],"alpn":["libp2p"]}'
	}

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Installing aioquic once"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *".venv-quic"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Inbound QUIC open on 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"handshake ("* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"0x1"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"libp2p"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"QUIC probe ALPN"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"203.0.113.50"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"QUIC probe target"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"probe tools missing"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 0 ]
}

@test "check_inbound_quic_probe WARNs when aioquic is missing and does not FAIL" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	NODE_CHECKER_QUIC_AUTO_INSTALL=0
	node_checker_quic_python() { return 1; }

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"probe tools missing"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Not a FAIL"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *".venv-quic"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"NODE_CHECKER_QUIC_AUTO_INSTALL=0"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"quicmap"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"Installing aioquic once"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"enr:"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_inbound_quic_probe WARNs when auto-install fails and does not FAIL" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	NODE_CHECKER_QUIC_AUTO_INSTALL=1
	node_checker_quic_python() { return 1; }
	maybe_install_quic_probe_deps() { return 1; }

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Installing aioquic once"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"probe tools missing"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_inbound_quic_probe PASSes when the handshake JSON is ok" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	node_checker_quic_python() { echo "/mock/python"; }
	invoke_quic_inbound_probe() {
		echo '{"ok":true,"reason":"server_versions","host":"203.0.113.50","port":9001,"server_versions":["0x1"],"alpn":["libp2p"]}'
	}

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Inbound QUIC open on 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Internet can complete a QUIC handshake"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"handshake ("* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"0x1"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"libp2p"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"QUIC probe ALPN"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"203.0.113.50"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"QUIC probe target"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"enr:"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 0 ]
}

@test "check_inbound_quic_probe FAILs when the probe is closed and the public IP is local" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	node_checker_quic_python() { echo "/mock/python"; }
	invoke_quic_inbound_probe() {
		echo '{"ok":false,"reason":"no_quic_response","host":"203.0.113.50","port":9001,"server_versions":[],"alpn":[]}'
	}
	ipv4_is_on_local_interface() { return 0; }

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Inbound QUIC closed on 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"203.0.113.50"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"enr:"* ]]
	[ "$failed_checks" -eq 1 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 1 ]
}

@test "check_inbound_quic_probe WARNs on NAT hairpin miss instead of false FAIL" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	node_checker_quic_python() { echo "/mock/python"; }
	invoke_quic_inbound_probe() {
		echo '{"ok":false,"reason":"no_quic_response","host":"203.0.113.50","port":9001,"server_versions":[],"alpn":[]}'
	}
	ipv4_is_on_local_interface() { return 1; }

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"behind NAT"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"complementary"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"no handshake on 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"203.0.113.50"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_inbound_quic_probe WARNs when public IPv4 cannot be resolved" {
	write_consensus Lighthouse
	fetch_tcp_port_checker() { echo ''; }
	node_checker_quic_python() { echo "/mock/python"; }

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Could not resolve public IPv4"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_inbound_quic_probe WARNs on private requester IP without naming it" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="192.168.1.20"
	node_checker_quic_python() { echo "/mock/python"; }

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"non-public IPv4"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"192.168.1.20"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_inbound_quic_probe WARNs on unusable probe JSON without naming the host" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	node_checker_quic_python() { echo "/mock/python"; }
	invoke_quic_inbound_probe() { echo 'not-json'; }

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"did not return usable JSON for 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"203.0.113.50"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "node_checker_debug_ipv4 is empty by default and names the IP under --debug" {
	NODE_CHECKER_DEBUG=0
	[ -z "$(node_checker_debug_ipv4 "203.0.113.50")" ]
	NODE_CHECKER_DEBUG=1
	[ "$(node_checker_debug_ipv4 "203.0.113.50")" = "203.0.113.50" ]
}

@test "check_inbound_quic_probe WARNs on CGNAT requester IP" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="100.64.1.8"
	node_checker_quic_python() { echo "/mock/python"; }

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Reported address is CGNAT"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"100.64.1.8"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
}

@test "check_inbound_quic_probe skips Caplin without FAIL" {
	write_caplin_execution
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Active inbound QUIC"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[FAIL]"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"[PASS]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 0 ]
}

@test "check_inbound_quic_probe --debug prints JSON and never an ENR" {
	write_consensus Lighthouse
	NODE_CHECKER_PUBLIC_IPV4="203.0.113.50"
	NODE_CHECKER_DEBUG=1
	node_checker_quic_python() { echo "/mock/python"; }
	invoke_quic_inbound_probe() {
		echo '{"ok":true,"reason":"server_versions","host":"203.0.113.50","port":9001,"server_versions":["0x1"],"alpn":["libp2p"]}'
	}

	check_inbound_quic_probe_capture
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"Inbound QUIC open on 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"handshake (0x1)"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"QUIC probe ALPN: libp2p"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"QUIC probe target 203.0.113.50"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"QUIC probe JSON (no ENR)"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" == *"server_versions"* ]]
	[[ "$(cat "$TEST_DIR/qprobe.out")" != *"enr:-"* ]]
}

@test "check_open_ports caches requester_ip for the QUIC probe" {
	write_consensus Lighthouse
	fetch_tcp_port_checker() { echo '{"requester_ip":"198.51.100.77","open_ports":[9000,30303]}'; }
	check_open_ports > "$TEST_DIR/open.out" 2>&1
	[ "$NODE_CHECKER_PUBLIC_IPV4" = "198.51.100.77" ]
	[[ "$(cat "$TEST_DIR/open.out")" != *"198.51.100.77"* ]]
}
