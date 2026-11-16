#!/usr/bin/env bats
#
# tests/test_ufw_menu.bats
#
# UFW Firewall menu helpers in functions.sh (EL/CL P2P allow + Charon visibility).
# Does not start Ethereum clients.
#
# Run: bats tests/test_ufw_menu.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."
	# shellcheck disable=SC1091
	source ./functions.sh

	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export EXEC_SERVICE_FILE="$TEST_DIR/execution.service"
	export CHARON_SERVICE_FILE="$TEST_DIR/charon.service"
	export SSV_NODE_SERVICE_FILE="$TEST_DIR/ssv-node.service"
	export COMMAND_LOG="$TEST_DIR/sudo.log"
	CL_P2P_PORT="${CL_P2P_PORT:-9000}"
	CL_P2P_PORT_2="${CL_P2P_PORT_2:-9001}"
	EL_P2P_PORT="${EL_P2P_PORT:-30303}"
	EL_P2P_PORT_2="${EL_P2P_PORT_2:-30304}"
	unset TEKU_QUIC_IPV6_PORT || true

	sudo() {
		echo "sudo $*" >> "$COMMAND_LOG"
	}
	export -f sudo
	: > "$COMMAND_LOG"
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_consensus() {
	local name="$1"
	local qport="${2:-9001}"
	local p2p="${3:-9000}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --port=${p2p} --quic-port=${qport}
EOF
}

write_execution() {
	local name="$1"
	local p2p="${2:-30303}"
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Execution Client for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --port=${p2p}
EOF
}

write_charon() {
	cat > "$CHARON_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/charon run --p2p-tcp-address=0.0.0.0:3610
EOF
}

write_ssv() {
	cat > "$SSV_NODE_SERVICE_FILE" <<EOF
[Unit]
Description=SSV Operator Node
[Service]
ExecStart=/usr/local/bin/ssvnode start-node --config /var/lib/ssv/config.yaml
EOF
}

menu_labels() {
	ufwBuildFirewallMenu
	local i
	for (( i = 0; i < ${#UFW_MENU_PAIRS[@]}; i += 2 )); do
		echo "${UFW_MENU_PAIRS[i]}|${UFW_MENU_PAIRS[i+1]}"
	done
}

@test "getExpectedClQuicUdpPorts is 9001 for Lighthouse from unit flag" {
	write_consensus Lighthouse 9001
	run getExpectedClQuicUdpPorts
	[ "$status" -eq 0 ]
	[ "$output" = "9001" ]
}

@test "getExpectedClQuicUdpPorts reads --quic-port from consensus.service" {
	write_consensus Nimbus 19001
	CL_P2P_PORT_2=9001
	run getExpectedClQuicUdpPorts
	[ "$output" = "19001" ]
}

@test "getExpectedClQuicUdpPorts includes Teku IPv4 and IPv6" {
	write_consensus Teku 9001
	run getExpectedClQuicUdpPorts
	[ "$output" = "9001 9091" ]
}

@test "getExpectedClQuicUdpPorts is 9001 for Caplin without an explicit flag" {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET
EOF
	rm -f "$CONSENSUS_SERVICE_FILE"
	run getExpectedClQuicUdpPorts
	[ "$output" = "9001" ]
}

@test "getExpectedClQuicUdpPorts reads Caplin --caplin.discovery.quicport" {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET
[Service]
ExecStart=/usr/local/bin/erigon --caplin.discovery.port=9000 --caplin.discovery.quicport=19001
EOF
	rm -f "$CONSENSUS_SERVICE_FILE"
	CL_P2P_PORT_2=9001
	run getExpectedClQuicUdpPorts
	[ "$output" = "19001" ]
}

@test "getExpectedElP2pPort and getExpectedClP2pPort read unit flags" {
	write_execution Geth 30403
	write_consensus Lighthouse 9001 19000
	[ "$(getExpectedElP2pPort)" = "30403" ]
	[ "$(getExpectedClP2pPort)" = "19000" ]
}

@test "parseUnitFlagPort does not treat --http-port or --quic-port as --port" {
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=Lighthouse Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/lighthouse --http-port=5052 --quic-port=9001 --port=9000
EOF
	[ "$(parseUnitFlagPort "$CONSENSUS_SERVICE_FILE" '--port')" = "9000" ]
	[ "$(getExpectedClP2pPort)" = "9000" ]
}

@test "getExpectedClP2pPort reads Caplin discovery port from execution.service" {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET
[Service]
ExecStart=/usr/local/bin/erigon --port=30303 --caplin.discovery.port=19000
EOF
	rm -f "$CONSENSUS_SERVICE_FILE"
	[ "$(getExpectedClP2pPort)" = "19000" ]
}

@test "ufwAllowExpectedP2pPorts opens EL/CL TCP+UDP and QUIC for Lighthouse" {
	write_execution Reth 30303
	write_consensus Lighthouse 9001 9000
	run ufwAllowExpectedP2pPorts
	[ "$status" -eq 0 ]
	grep -q "ufw allow 30303/tcp" "$COMMAND_LOG"
	grep -q "ufw allow 30303/udp" "$COMMAND_LOG"
	grep -q "ufw allow 9000/tcp" "$COMMAND_LOG"
	grep -q "ufw allow 9000/udp" "$COMMAND_LOG"
	grep -q "ufw allow 9001/udp" "$COMMAND_LOG"
	grep -q "ufw allow 30304/udp" "$COMMAND_LOG"
	[[ "$(describeExpectedP2pUfwRules)" == *"30303/tcp 30303/udp 9000/tcp 9000/udp 30304/udp 9001/udp"* ]]
}

@test "ufwAllowExpectedP2pPorts opens Caplin QUIC UDP 9001 and not Charon" {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET
[Service]
ExecStart=/usr/local/bin/erigon --port=30303 --caplin.discovery.port=9000 --caplin.discovery.quicport=9001
EOF
	rm -f "$CONSENSUS_SERVICE_FILE"
	run ufwAllowExpectedP2pPorts
	grep -q "ufw allow 30303/tcp" "$COMMAND_LOG"
	grep -q "ufw allow 30303/udp" "$COMMAND_LOG"
	grep -q "ufw allow 9000/tcp" "$COMMAND_LOG"
	grep -q "ufw allow 9000/udp" "$COMMAND_LOG"
	grep -q "ufw allow 30304/tcp" "$COMMAND_LOG"
	grep -q "ufw allow 42069/udp" "$COMMAND_LOG"
	grep -q "ufw allow 9001/udp" "$COMMAND_LOG"
	grep -qv "3610" "$COMMAND_LOG"
}

@test "ufwAllowClQuic allows resolved QUIC UDP ports" {
	write_consensus Lighthouse 19001
	run ufwAllowClQuic
	[ "$status" -eq 0 ]
	grep -q "ufw allow 19001/udp" "$COMMAND_LOG"
}

@test "ufwAllowClQuic allows Teku 9001 and 9091" {
	write_consensus Teku 9001
	run ufwAllowClQuic
	grep -q "ufw allow 9001/udp" "$COMMAND_LOG"
	grep -q "ufw allow 9091/udp" "$COMMAND_LOG"
}

@test "UFW menu includes EL/CL P2P and hides Charon when charon.service is missing" {
	write_execution Geth
	write_consensus Lighthouse
	rm -f "$CHARON_SERVICE_FILE" "$SSV_NODE_SERVICE_FILE"
	labels="$(menu_labels)"
	[[ "$labels" == *"EC RPC Node: Allow local network access to RPC port 8545"* ]]
	[[ "$labels" == *"CC RPC Node: Allow local network access to CL REST port"* ]]
	[[ "$labels" == *"EL/CL P2P: Allow TCP/UDP (from execution & consensus, incl. QUIC)"* ]]
	[[ "$labels" != *"CL QUIC: Allow UDP"* ]]
	[[ "$labels" != *"Allow P2P port (from charon.service)"* ]]
	ufwBuildFirewallMenu
	[[ "$(ufwFirewallMenuAction 9)" == "elcl_p2p" ]]
	[[ "$(ufwFirewallMenuAction 10)" == "disable" ]]
}

@test "UFW menu omits EC/CC/ELCL rows on SSV-only host but keeps SSV" {
	rm -f "$EXEC_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE" "$CHARON_SERVICE_FILE"
	write_ssv
	labels="$(menu_labels)"
	[[ "$labels" != *"EC RPC Node"* ]]
	[[ "$labels" != *"CC RPC Node"* ]]
	[[ "$labels" != *"EL/CL P2P"* ]]
	[[ "$labels" == *"SSV: Allow P2P TCP/UDP and DKG"* ]]
	ufwBuildFirewallMenu
	# 1-5 generic, 6 grafana, 7 ssv, 8 disable
	[[ "$(ufwFirewallMenuAction 6)" == "grafana" ]]
	[[ "$(ufwFirewallMenuAction 7)" == "ssv" ]]
	[[ "$(ufwFirewallMenuAction 8)" == "disable" ]]
}

@test "ufwAllowConsensusRest allows the scraped CL port not the 5052 default" {
	cat > "$CONSENSUS_SERVICE_FILE" <<'EOF'
[Unit]
Description=Lighthouse Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/lighthouse bn --http-address=0.0.0.0 --http-port=16052
EOF
	export CL_REST_PORT=5052
	export CL=Lighthouse
	export network_current="192.168.50.0/24"
	run ufwAllowConsensusRest
	[ "$status" -eq 0 ]
	[ "$CL_REST_PORT" = "5052" ]
	grep -q "ufw allow from 192.168.50.0/24 to any port 16052 comment Allow local network to access consensus client RPC port" "$COMMAND_LOG"
	if grep -q "port 5052" "$COMMAND_LOG"; then
		echo "allow used the 5052 fallback" >&2
		cat "$COMMAND_LOG" >&2
		return 1
	fi
}

@test "ufwAllowConsensusRest allows scraped Teku rest-api-port when CL_REST_PORT is 5052" {
	cat > "$CONSENSUS_SERVICE_FILE" <<'EOF'
[Unit]
Description=Teku Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/teku --rest-api-interface=127.0.0.1 --rest-api-port=16099
EOF
	export CL_REST_PORT=5052
	export CL=""
	export network_current="10.0.0.0/24"
	run ufwAllowConsensusRest
	[ "$status" -eq 0 ]
	grep -q "to any port 16099" "$COMMAND_LOG"
	if grep -q "port 5052" "$COMMAND_LOG"; then
		echo "Teku allow used 5052" >&2
		cat "$COMMAND_LOG" >&2
		return 1
	fi
}

@test "ufwAllowConsensusRest allows scraped Nimbus rest-port from the unit description" {
	cat > "$CONSENSUS_SERVICE_FILE" <<'EOF'
[Unit]
Description=Nimbus Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/nimbus_beacon_node --rest-address=127.0.0.1 --rest-port=15052
EOF
	export CL=""
	export CL_REST_PORT=5052
	export network_current="10.1.0.0/24"
	[ "$(consensusRestPortForUfw)" = "15052" ]
	run ufwAllowConsensusRest
	[ "$status" -eq 0 ]
	grep -q "to any port 15052" "$COMMAND_LOG"
	if grep -q "port 5052" "$COMMAND_LOG"; then
		echo "Nimbus allow used 5052" >&2
		cat "$COMMAND_LOG" >&2
		return 1
	fi
}

@test "ufwAllowConsensusRest falls back to 5052 when the consensus unit has no REST port" {
	rm -f "$CONSENSUS_SERVICE_FILE"
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/missing-consensus.service"
	unset CL_REST_PORT
	export network_current="192.168.1.0/24"
	[ "$(consensusRestPortForUfw)" = "5052" ]
	run ufwAllowConsensusRest
	[ "$status" -eq 0 ]
	grep -q "to any port 5052 comment Allow local network to access consensus client RPC port" "$COMMAND_LOG"
}

@test "ufwAllowConsensusRest uses CL_REST_PORT when the unit has no REST port flag" {
	cat > "$CONSENSUS_SERVICE_FILE" <<'EOF'
[Unit]
Description=Lighthouse Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/lighthouse bn --port=9000
EOF
	export CL_REST_PORT=17052
	export network_current="192.168.1.0/24"
	[ "$(consensusRestPortForUfw)" = "17052" ]
	run ufwAllowConsensusRest
	[ "$status" -eq 0 ]
	grep -q "to any port 17052" "$COMMAND_LOG"
	if grep -q "port 5052" "$COMMAND_LOG"; then
		echo "allow ignored CL_REST_PORT" >&2
		cat "$COMMAND_LOG" >&2
		return 1
	fi
}

@test "UFW menu shows Charon P2P only when charon.service exists" {
	write_execution Geth
	write_consensus Lighthouse
	write_charon
	rm -f "$SSV_NODE_SERVICE_FILE"
	labels="$(menu_labels)"
	[[ "$labels" == *"EL/CL P2P: Allow TCP/UDP (from execution & consensus, incl. QUIC)"* ]]
	[[ "$labels" == *"Allow P2P port (from charon.service)"* ]]
	ufwBuildFirewallMenu
	[[ "$(ufwFirewallMenuAction 9)" == "elcl_p2p" ]]
	[[ "$(ufwFirewallMenuAction 10)" == "charon" ]]
	[[ "$(ufwFirewallMenuAction 11)" == "disable" ]]
}
