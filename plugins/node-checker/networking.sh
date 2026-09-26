#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: Node-checker networking / port-check helpers (sourced by run.sh).
#
# Approach adapted from ethstaker/eth-docker `port-check` (Apache-2.0): Beacon
# API inbound vs outbound, QUIC multiaddrs, CGNAT honesty, and operator
# guidance that does not leak ENR. EthPillar is systemd/bare-metal — no
# Docker/compose discv5 containers. Active inbound QUIC uses an optional
# aioquic probe adapted from bojanisc/quicmap (Apache-2.0); see
# NOTICE-quicmap.txt and quic_inbound_probe.py. Peer-direction inbound QUIC
# stays complementary.
#
# Made for home and solo stakers 🏠🥩

# Sourced by plugins/node-checker/run.sh (and bats via that entrypoint).
# Do not execute this file directly.
#
# Troubleshoot vs default vs debug (Plugins menu has no CLI flags):
# - Default: PASS/FAIL/WARN for local listen, TCP inbound, active QUIC, and CL peer direction.
#   Paste-safe: no ENR, no operator public IPv4, no QUIC version/ALPN lists
#   (ports and PASS/FAIL/WARN stay).
# - Troubleshoot: extra firewall/NAT/port-forward steps when inbound looks broken.
#   The menu/default path auto-prints that guidance when a useful signal fires
#   (missing UFW allow, TCP checker closed/unreachable, zero/unknown inbound,
#   QUIC expected but not advertised or no inbound QUIC). CLI --troubleshoot or
#   NODE_CHECKER_TROUBLESHOOT=1 still forces the same text even when all-green.
# - Debug (--debug / NODE_CHECKER_DEBUG=1) may print ENR, public IPv4, and probe versions/ALPN.

NODE_CHECKER_TROUBLESHOOT="${NODE_CHECKER_TROUBLESHOOT:-0}"
NODE_CHECKER_DEBUG="${NODE_CHECKER_DEBUG:-0}"
NODE_CHECKER_AUTO_TROUBLESHOOT="${NODE_CHECKER_AUTO_TROUBLESHOOT:-0}"
NODE_CHECKER_TROUBLESHOOT_PRINTED="${NODE_CHECKER_TROUBLESHOOT_PRINTED:-0}"
NODE_CHECKER_TROUBLESHOOT_INBOUND="${NODE_CHECKER_TROUBLESHOOT_INBOUND:-?}"
NODE_CHECKER_TROUBLESHOOT_OUTBOUND="${NODE_CHECKER_TROUBLESHOOT_OUTBOUND:-?}"
TCP_PORT_CHECKER_URL="${TCP_PORT_CHECKER_URL:-https://eth2-client-port-checker.vercel.app/api/checker?ports=}"
# Public IPv4 from the TCP checker (requester_ip). Cached for the QUIC probe.
NODE_CHECKER_PUBLIC_IPV4="${NODE_CHECKER_PUBLIC_IPV4:-}"
# Optional aioquic venv — not the default EthPillar .venv (aioquic is heavy).
NODE_CHECKER_QUIC_VENV="${NODE_CHECKER_QUIC_VENV:-}"
NODE_CHECKER_QUIC_PYTHON="${NODE_CHECKER_QUIC_PYTHON:-}"
NODE_CHECKER_QUIC_TIMEOUT="${NODE_CHECKER_QUIC_TIMEOUT:-5}"
# Default on so Plugins → node-checker just works. Set 0 to skip the one-time pip.
NODE_CHECKER_QUIC_AUTO_INSTALL="${NODE_CHECKER_QUIC_AUTO_INSTALL:-1}"

# Request the troubleshoot block for the menu/default path. Never enables ENR.
node_checker_request_troubleshoot() {
    NODE_CHECKER_AUTO_TROUBLESHOOT=1
}

node_checker_should_print_troubleshoot() {
    [[ "${NODE_CHECKER_TROUBLESHOOT:-0}" -eq 1 || "${NODE_CHECKER_AUTO_TROUBLESHOOT:-0}" -eq 1 ]]
}

# True when --debug / NODE_CHECKER_DEBUG=1 may name ENR or public IPv4.
node_checker_debug_enabled() {
    [[ "${NODE_CHECKER_DEBUG:-0}" -eq 1 ]]
}

# Echo $1 only on the debug path. Default output stays paste-safe (no public IP).
node_checker_debug_ipv4() {
    node_checker_debug_enabled || return 0
    [[ -n "${1:-}" ]] || return 0
    printf '%s' "$1"
}

# Print firewall/NAT/forward guidance at most once. ENR and public IPv4 stay in --debug only.
maybe_print_port_troubleshoot() {
    local inbound="${1:-${NODE_CHECKER_TROUBLESHOOT_INBOUND:-?}}"
    local outbound="${2:-${NODE_CHECKER_TROUBLESHOOT_OUTBOUND:-?}}"
    node_checker_should_print_troubleshoot || return 0
    [[ "${NODE_CHECKER_TROUBLESHOOT_PRINTED:-0}" -eq 1 ]] && return 0
    NODE_CHECKER_TROUBLESHOOT_PRINTED=1
    print_port_troubleshoot_guidance "$inbound" "$outbound"
}

# CL P2P 9000 and EL P2P 30303 (TCP+UDP) are the "expected 4" listen ports.
# CL QUIC UDP (typically 9001; Teku also 9091) is tracked separately so that
# count stays stable — see configure_cl_quic_udp_check_ports / check_cl_quic.
node_checker_init_networking() {
    p2p_ports=("9000" "30303")
    tcp_check_ports="9000,30303"
    udp_check_ports="9000,30303"
    udp_check_ports_base="$udp_check_ports"
    ELCL_EXPECTED_LISTEN_COUNT=4
    charon_p2p_port=""

    if command -v isCharonEnabled >/dev/null 2>&1 && isCharonEnabled; then
        charon_p2p_port="$(getCharonP2pPort)"
        if [[ -n "$charon_p2p_port" ]]; then
            p2p_ports+=("$charon_p2p_port")
            tcp_check_ports="${tcp_check_ports},${charon_p2p_port}"
        fi
    fi
}

node_checker_init_networking

node_checker_exec_service() {
    echo "${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
}

node_checker_consensus_service() {
    echo "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
}

# First word of Description= — same heuristic as getClient / check_consensus_version.
node_checker_unit_client() {
    local path="$1"
    [[ -f "$path" ]] || return 0
    grep "Description=" "$path" 2>/dev/null | awk -F'=' '{print $2}' | awk '{print $1}'
}

# Caplin is integrated into execution.service (EL Erigon-Caplin); no QUIC by default.
is_caplin_node() {
    local exec_svc el cl consensus_svc
    exec_svc="$(node_checker_exec_service)"
    el="$(node_checker_unit_client "$exec_svc")"
    if [[ "$el" == "Erigon-Caplin" || "$el" == "Caplin" ]]; then
        return 0
    fi
    if [[ -f "$exec_svc" ]] && grep -qiE 'caplin' "$exec_svc" 2>/dev/null; then
        return 0
    fi
    consensus_svc="$(node_checker_consensus_service)"
    cl="$(node_checker_unit_client "$consensus_svc")"
    if [[ "$cl" == "Caplin" || "$cl" == "Erigon-Caplin" ]]; then
        return 0
    fi
    return 1
}

node_checker_cl_name() {
    node_checker_unit_client "$(node_checker_consensus_service)"
}

# True when a local consensus.service exists and the CL is not Caplin.
# Known QUIC CLs: Lighthouse, Teku, Nimbus, Lodestar, Grandine, Prysm.
# Unknown CL with consensus.service: still expect 9001/udp.
cl_expects_quic() {
    is_caplin_node && return 1
    local cl
    cl="$(node_checker_cl_name)"
    [[ -n "$cl" ]]
}

# Prints space-separated UDP ports. Empty when QUIC is not expected (Caplin / no CL).
# Delegates to functions.sh so the UFW TUI and node-checker share one resolver.
expected_cl_quic_udp_ports() {
    if declare -F getExpectedClQuicUdpPorts >/dev/null; then
        getExpectedClQuicUdpPorts
        return
    fi
    cl_expects_quic || return 0
    local cl quic_port ipv6_port
    cl="$(node_checker_cl_name)"
    quic_port="${CL_P2P_PORT_2:-9001}"
    if [[ "$cl" == "Teku" ]]; then
        ipv6_port="${TEKU_QUIC_IPV6_PORT:-$(( ${CL_P2P_PORT:-9000} + 91 ))}"
        echo "${quic_port} ${ipv6_port}"
    else
        echo "${quic_port}"
    fi
}

# IPv4 QUIC UDP port (first expected port). Teku's second port is IPv6-only.
cl_quic_ipv4_udp_port() {
    expected_cl_quic_udp_ports | awk '{print $1}'
}

csv_has_item() {
    local csv="$1" item="$2"
    [[ ",${csv}," == *",${item},"* ]]
}

csv_append_unique() {
    local csv="$1" item="$2"
    if [[ -z "$item" ]]; then
        echo "$csv"
        return
    fi
    if csv_has_item "$csv" "$item"; then
        echo "$csv"
        return
    fi
    if [[ -z "$csv" ]]; then
        echo "$item"
    else
        echo "${csv},${item}"
    fi
}

# Append expected CL QUIC UDP ports to udp_check_ports (not TCP, not p2p_ports).
# Resets from udp_check_ports_base so repeated calls stay idempotent.
configure_cl_quic_udp_check_ports() {
    local base port
    base="${udp_check_ports_base:-9000,30303}"
    udp_check_ports="$base"
    for port in $(expected_cl_quic_udp_ports); do
        udp_check_ports="$(csv_append_unique "$udp_check_ports" "$port")"
    done
}

check_cl_quic_ufw_rules() {
    local ufw_status port
    if ! sudo ufw status 2>/dev/null | grep -q "Status: active"; then
        return 0
    fi
    ufw_status="$(sudo ufw status 2>/dev/null)"
    for port in $(expected_cl_quic_udp_ports); do
        total_checks=$((total_checks + 1))
        if echo "$ufw_status" | grep -qE "${port}/udp"; then
            print_check_result "PASS" "UFW allows CL QUIC ${port}/udp"
        else
            print_check_result "FAIL" "UFW is active but missing allow rule for CL QUIC ${port}/udp"
            failed_checks=$((failed_checks + 1))
            node_checker_request_troubleshoot
        fi
    done
}

check_cl_quic_listening() {
    local port pid process
    for port in $(expected_cl_quic_udp_ports); do
        total_checks=$((total_checks + 1))
        if sudo ss -lntu | grep -qE "udp.*:${port}([^0-9]|$)"; then
            print_check_result "PASS" "Detected UDP service on CL QUIC port ${port}"
            if [ "$EUID" -eq 0 ]; then
                pid=$(sudo ss -lntup "sport = :${port}" | awk -Fpid= '/users:/ {print $2}' | cut -d, -f1 | head -1)
                if [ -n "$pid" ]; then
                    process=$(ps -p "$pid" -o comm=)
                    echo -e "${YELLOW}          Process: ${process} (PID ${pid})${NC}"
                fi
            fi
        else
            print_check_result "FAIL" "CL QUIC port ${port}/udp not listening"
            failed_checks=$((failed_checks + 1))
            node_checker_request_troubleshoot
        fi
    done
}

check_cl_quic() {
    print_check_result "INFO" "CL QUIC: after Glamsterdam, libp2p MPlex/TCP P2P is deprecated — verify QUIC UDP (typically ${CL_P2P_PORT_2:-9001}/udp)"
    if is_caplin_node; then
        total_checks=$((total_checks + 1))
        print_check_result "WARN" "Caplin has no QUIC by default; skipping ${CL_P2P_PORT_2:-9001}/udp requirement"
        warning_checks=$((warning_checks + 1))
        return 0
    fi
    if ! cl_expects_quic; then
        return 0
    fi
    check_cl_quic_ufw_rules
    check_cl_quic_listening
    print_check_result "INFO" "QUIC listen/UFW is local. An active inbound QUIC probe runs after the public TCP checker; peer-direction inbound QUIC stays complementary."
}

# RFC1918 / loopback / link-local / this-host. Returns 0 when not Internet-routable.
is_private_ipv4() {
    case "$1" in
        10.*|127.*|0.*|169.254.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
        *) return 1 ;;
    esac
}

# Carrier-grade NAT 100.64.0.0/10 — no customer port-forward can work.
is_cgnat_ipv4() {
    case "$1" in
        100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) return 0 ;;
        *) return 1 ;;
    esac
}

# Echo public | private | cgnat | empty
classify_ipv4() {
    local ip="$1"
    if [[ -z "$ip" ]]; then
        echo "empty"
        return
    fi
    if is_cgnat_ipv4 "$ip"; then
        echo "cgnat"
        return
    fi
    if is_private_ipv4 "$ip"; then
        echo "private"
        return
    fi
    echo "public"
}

count_nonempty_lines() {
    local text="$1"
    if [[ -z "$text" ]]; then
        echo 0
        return
    fi
    printf '%s\n' "$text" | grep -c .
}

count_multiaddr_matching() {
    local addresses="$1"
    local pattern="$2"
    if [[ -z "$addresses" ]]; then
        echo 0
        return
    fi
    printf '%s\n' "$addresses" | grep -cE "$pattern" || true
}

# First /ip4/X from newline-separated multiaddrs.
multiaddr_first_ip4() {
    local addresses="$1"
    printf '%s\n' "$addresses" | sed -n 's|.*/ip4/\([^/]*\).*|\1|p' | head -1
}

# Connected peers in one direction as compact JSON objects (one per line).
# Filter locally: Teku/Grandine ignore Beacon API direction query parameters.
# A peer is dropped only when it says it is not connected.
cl_connected_peers_json() {
    local json="$1"
    local direction="$2"
    jq -c --arg d "$direction" '
        (.data // [])
        | map(select(
            (.direction == $d)
            and (
              (.state // "connected") as $s
              | ($s != "disconnected" and $s != "disconnecting" and $s != "connecting")
            )
          ))
        | .[]
    ' <<< "$json" 2>/dev/null || true
}

cl_peers_report_direction() {
    local json="$1"
    # Empty peer list is a real measurement (inbound=0). Missing direction on
    # present peers means the client does not report which way they were dialed.
    jq -e '
        (.data // []) as $d
        | ($d | length == 0)
          or ([$d[] | select(.direction != null and .direction != "")] | length > 0)
    ' <<< "$json" >/dev/null 2>&1
}

cl_peer_address_lines() {
    local peer_objects="$1"
    if [[ -z "$peer_objects" ]]; then
        return 0
    fi
    printf '%s\n' "$peer_objects" | jq -r '.last_seen_p2p_address // empty' 2>/dev/null || true
}

# Echo: quic4 quic6 tcp4 tcp6
peer_transport_counts() {
    local addresses="$1"
    local quic_total quic_v6 tcp_total tcp_v6
    quic_total="$(count_multiaddr_matching "$addresses" '/quic')"
    quic_v6="$(count_multiaddr_matching "$addresses" '/ip6/.*/quic')"
    tcp_total="$(count_multiaddr_matching "$addresses" '/tcp/')"
    tcp_v6="$(count_multiaddr_matching "$addresses" '/ip6/.*/tcp/')"
    echo "$((quic_total - quic_v6)) ${quic_v6} $((tcp_total - tcp_v6)) ${tcp_v6}"
}

# Echo working | not-working | unknown
inbound_status_kind() {
    local inbound="$1"
    if [[ "$inbound" == "?" ]]; then
        echo "unknown"
    elif [[ "$inbound" =~ ^[0-9]+$ && "$inbound" -gt 0 ]]; then
        echo "working"
    else
        echo "not-working"
    fi
}

cl_identity_peer_id() {
    jq -r '.data.peer_id // empty' <<< "$1" 2>/dev/null || true
}

cl_identity_enr() {
    jq -r '.data.enr // empty' <<< "$1" 2>/dev/null || true
}

cl_identity_p2p_address_lines() {
    jq -r '.data.p2p_addresses[]? // empty' <<< "$1" 2>/dev/null || true
}

cl_identity_discovery_address_lines() {
    jq -r '.data.discovery_addresses[]? // empty' <<< "$1" 2>/dev/null || true
}

tcp_checker_open_port_list() {
    jq -r '.open_ports[]? // empty' <<< "$1" 2>/dev/null || true
}

tcp_checker_requester_ip() {
    jq -r '.requester_ip // empty' <<< "$1" 2>/dev/null || true
}

# Overridable in bats (do not start clients).
node_checker_http_get() {
    curl -m 2 -s "$@"
}

fetch_cl_api() {
    local path="$1"
    node_checker_http_get -X GET "${API_BN_ENDPOINT}${path}" -H "accept: application/json"
}

fetch_el_rpc() {
    local method="$1"
    node_checker_http_get -X POST -H "Content-Type: application/json" \
        --data "{\"jsonrpc\":\"2.0\",\"method\":\"${method}\",\"params\":[],\"id\":1}" \
        "${EL_RPC_ENDPOINT}"
}

fetch_tcp_port_checker() {
    local ports="$1"
    node_checker_http_get "${TCP_PORT_CHECKER_URL}${ports}"
}

print_peer_transport_table() {
    local inbound_addrs="$1"
    local outbound_addrs="$2"
    local iq4 iq6 it4 it6 oq4 oq6 ot4 ot6
    read -r iq4 iq6 it4 it6 <<< "$(peer_transport_counts "$inbound_addrs")"
    read -r oq4 oq6 ot4 ot6 <<< "$(peer_transport_counts "$outbound_addrs")"
    print_check_result "INFO" "Peer transports as the client reports them (last_seen_p2p_address):"
    printf '  %-10s %8s %8s %8s %8s\n' "direction" "QUIC v4" "QUIC v6" "TCP v4" "TCP v6"
    printf '  %-10s %8s %8s %8s %8s\n' "inbound" "$iq4" "$iq6" "$it4" "$it6"
    printf '  %-10s %8s %8s %8s %8s\n' "outbound" "$oq4" "$oq6" "$ot4" "$ot6"
}

# Actionable next steps. Never prints ENR or identity JSON.
print_port_troubleshoot_guidance() {
    local inbound="${1:-?}"
    local outbound="${2:-?}"
    local cl_p2p="${CL_P2P_PORT:-9000}"
    local cl_quic="${CL_P2P_PORT_2:-9001}"
    local el_p2p="${EL_P2P_PORT:-30303}"
    local teku_extra=""

    print_check_result "INFO" "Inbound firewall, NAT, and port-forward tips:"
    echo "  When peers cannot reach this node from the Internet, check the firewall, router port-forwards, and ISP."
    if [[ "$(inbound_status_kind "$inbound")" == "working" ]]; then
        if [[ "${NODE_CHECKER_AUTO_TROUBLESHOOT:-0}" -eq 1 ]]; then
            echo "  Inbound peering looks fine; another port, NAT, or firewall check still looked wrong."
        else
            echo "  Inbound peering looks fine. Review these tips if reachability is still in doubt."
        fi
    elif [[ "$inbound" == "?" ]]; then
        echo "  This consensus client does not report peer direction, so inbound cannot be measured here."
    else
        echo "  No inbound consensus peers yet. A node started in the last few minutes may not have been dialed."
        echo "  Wait, then re-run node-checker before changing firewall rules."
    fi
    if [[ "$outbound" =~ ^[0-9]+$ && "$outbound" -eq 0 && "$inbound" != "?" ]]; then
        echo "  Outbound is also zero — consensus may still be starting, or UDP ${cl_p2p} is blocked outbound too."
    fi
    echo "  Local listen (ss) and UFW allow rules are necessary but not sufficient."
    echo "  Forward UDP ${cl_p2p} (discv5) and UDP ${cl_quic} (QUIC) to this host."
    echo "  Forward TCP+UDP ${el_p2p} for the execution client."
    echo "  The consensus layer needs UDP for transport. A TCP-only forward will not do."
    echo "  No website can test a UDP port — checkers that offer a green result only speak TCP."
    echo "  A green TCP result for ${cl_p2p} or ${el_p2p} proves nothing about QUIC ${cl_quic}/udp."
    echo "  Node-checker auto-installs aioquic into .venv-quic on first probe (NODE_CHECKER_QUIC_AUTO_INSTALL=0 to skip)."
    echo "  Peer-direction inbound QUIC is complementary — it is not a replacement for the active probe."
    echo "  If your public IPv4 is CGNAT (100.64.0.0/10), no IPv4 port-forward can work; use IPv6 or ask the ISP for a public IPv4."
    echo "  UFW (when active) should allow ${cl_p2p}/tcp, ${cl_p2p}/udp, ${cl_quic}/udp, ${el_p2p}/tcp, ${el_p2p}/udp."
    if [[ "$(node_checker_cl_name)" == "Teku" ]]; then
        teku_extra="${TEKU_QUIC_IPV6_PORT:-$(( cl_p2p + 91 ))}"
        echo "  Teku also needs UFW allow ${teku_extra}/udp (IPv6 QUIC)."
    fi
}

# ENR and identity live only here (NODE_CHECKER_DEBUG / --debug).
print_port_debug_diagnostics() {
    local identity_json="$1"
    local peers_json="$2"
    local enr peer_id disc p2p
    print_check_result "INFO" "Debug diagnostics — this names your ENR and addresses. Redact before sharing."
    peer_id="$(cl_identity_peer_id "$identity_json")"
    enr="$(cl_identity_enr "$identity_json")"
    disc="$(cl_identity_discovery_address_lines "$identity_json")"
    p2p="$(cl_identity_p2p_address_lines "$identity_json")"
    echo "  Peer ID: ${peer_id:-none}"
    echo "  ENR: ${enr:-none}"
    echo "  Discovery addresses:"
    if [[ -n "$disc" ]]; then
        printf '    %s\n' "$disc"
    else
        echo "    none"
    fi
    echo "  Libp2p listen addresses:"
    if [[ -n "$p2p" ]]; then
        printf '    %s\n' "$p2p"
    else
        echo "    none"
    fi
    echo "  Raw /eth/v1/node/peers data length: $(printf '%s' "$peers_json" | wc -c) bytes"
}

check_listening_ports() {
    ((total_checks++))
    open_ports=$(sudo ss -tunlp | grep -c -E 'LISTEN|UNCONN')
    if [ "$open_ports" -gt 0 ]; then
        print_check_result "INFO" "Local listening sockets (ss) — not inbound:"
        sudo ss -tunlp | grep -E 'LISTEN|UNCONN'
    else
        print_check_result "WARN" "No listening ports."
        ((warning_checks++))
    fi
}

check_charon_listening_port() {
    [[ -n "$charon_p2p_port" ]] || return 0
    ((total_checks+=1))
    if sudo ss -lnt | grep -qE "tcp.*:${charon_p2p_port}"; then
        print_check_result "PASS" "Detected TCP service on Charon P2P port ${charon_p2p_port}"
        if [ "$EUID" -eq 0 ]; then
            pid=$(sudo ss -lntup "sport = :${charon_p2p_port}" | awk -Fpid= '/users:/ {print $2}' | cut -d, -f1 | head -1)
            if [ -n "$pid" ]; then
                process=$(ps -p "$pid" -o comm=)
                echo -e "${YELLOW}          Process: ${process} (PID ${pid})${NC}"
            fi
        fi
    else
        print_check_result "FAIL" "Charon P2P port ${charon_p2p_port} (TCP) not listening"
        ((failed_checks++))
    fi
}

check_elcl_listening_ports() {
    ((total_checks+=2))
    detected=0
    declare -a p2p_protocols=("tcp" "udp")

    print_check_result "INFO" "Local listen (ss): execution & consensus on 9000 tcp/udp and 30303 tcp/udp — not inbound"
    # Check standard ports for other clients
    for port in "${p2p_ports[@]}"; do
        for proto in "${p2p_protocols[@]}"; do
            if sudo ss -lntu | grep -qE "${proto}.*:${port}"; then
                print_check_result "PASS" "Detected ${proto^^} service on port ${port}"
                ((detected++))
                if [ "$EUID" -eq 0 ]; then
                    pid=$(sudo ss -lntup "sport = :${port}" | awk -Fpid= '/users:/ {print $2}' | cut -d, -f1 | head -1)
                    if [ -n "$pid" ]; then
                        process=$(ps -p "$pid" -o comm=)
                        echo -e "${YELLOW}          Process: ${process} (PID ${pid})${NC}"
                    fi
                else
                    echo -e "${YELLOW}          Run as root to identify process${NC}"
                fi
            fi
        done
    done

    # QUIC UDP (9001/9091) is checked in check_cl_quic, not counted here.
    if [ $detected -gt 0 ]; then
        if [ $detected -eq "$ELCL_EXPECTED_LISTEN_COUNT" ]; then
            print_check_result "PASS" "Found all ${ELCL_EXPECTED_LISTEN_COUNT} expected ports (9000 tcp/udp, 30303 tcp/udp) for execution & consensus services"
        else
            print_check_result "FAIL" "Found ${detected} ports, expected ${ELCL_EXPECTED_LISTEN_COUNT} ports (9000 tcp/udp, 30303 tcp/udp) for execution & consensus services"
            ((failed_checks++))
        fi
    else
        print_check_result "FAIL" "No execution & consensus services detected on expected ports"
        ((failed_checks++))
    fi
    check_charon_listening_port
}

check_open_ports() {
    local tcp_ports udp_ports tcp_json requester named_ip open_list port missing=0

    configure_cl_quic_udp_check_ports
    tcp_ports="$tcp_check_ports"
    udp_ports="$udp_check_ports"

    print_check_result "INFO" "TCP inbound (public checker) vs local listen: a process bound on ss is not proof the Internet can dial you."
    print_check_result "INFO" "UDP inbound cannot be tested by a web checker (they only speak TCP). Expected UDP: ${udp_ports}. An active QUIC probe follows for CL QUIC."

    tcp_json="$(fetch_tcp_port_checker "$tcp_ports")"
    if ! jq -e 'type == "object"' <<< "$tcp_json" >/dev/null 2>&1; then
        total_checks=$((total_checks + 1))
        print_check_result "WARN" "Could not query the public TCP port checker. Local listen still applies."
        warning_checks=$((warning_checks + 1))
        node_checker_request_troubleshoot
        return 0
    fi

    requester="$(tcp_checker_requester_ip "$tcp_json")"
    NODE_CHECKER_PUBLIC_IPV4="$requester"
    if [[ -n "$requester" ]]; then
        named_ip="$(node_checker_debug_ipv4 "$requester")"
        if [[ -n "$named_ip" ]]; then
            print_check_result "INFO" "Public TCP checker sees this host as ${named_ip}"
        fi
        case "$(classify_ipv4 "$requester")" in
            cgnat)
                total_checks=$((total_checks + 1))
                print_check_result "WARN" "Reported address${named_ip:+ ${named_ip}} is CGNAT (100.64.0.0/10). No IPv4 port-forward can work."
                warning_checks=$((warning_checks + 1))
                node_checker_request_troubleshoot
                ;;
            private)
                total_checks=$((total_checks + 1))
                print_check_result "WARN" "Checker reported a non-public IPv4${named_ip:+ (${named_ip})}. Inbound IPv4 peers cannot dial that."
                warning_checks=$((warning_checks + 1))
                node_checker_request_troubleshoot
                ;;
        esac
    fi

    open_list="$(tcp_checker_open_port_list "$tcp_json")"
    for port in ${tcp_ports//,/ }; do
        total_checks=$((total_checks + 1))
        if [[ -n "$open_list" ]] && grep -qx "$port" <<< "$open_list"; then
            print_check_result "PASS" "TCP inbound open on ${port} (Internet can complete a TCP handshake)"
        else
            print_check_result "FAIL" "TCP inbound closed on ${port}. Forward ${port}/tcp on the router and allow it in UFW."
            failed_checks=$((failed_checks + 1))
            missing=1
            node_checker_request_troubleshoot
        fi
    done

    if [[ "$missing" -eq 1 ]]; then
        print_check_result "INFO" "TCP inbound miss is not a UDP/QUIC result. See the inbound firewall, NAT, and port-forward tips after the peer-direction check."
    fi
}

# Echo public IPv4 from the TCP checker path (cached after check_open_ports).
# Always returns 0 so callers under `set -e` can test for an empty address.
node_checker_resolve_public_ipv4() {
    if [[ -n "${NODE_CHECKER_PUBLIC_IPV4:-}" ]]; then
        echo "$NODE_CHECKER_PUBLIC_IPV4"
        return 0
    fi
    local json ports
    ports="${1:-${tcp_check_ports:-9000,30303}}"
    json="$(fetch_tcp_port_checker "$ports")"
    tcp_checker_requester_ip "$json"
}

node_checker_quic_root() {
    echo "${ETHPILLAR_ROOT:-${BASE_DIR:-.}}"
}

node_checker_quic_default_venv() {
    echo "${NODE_CHECKER_QUIC_VENV:-$(node_checker_quic_root)/.venv-quic}"
}

node_checker_quic_probe_script() {
    echo "${NODE_CHECKER_QUIC_PROBE_SCRIPT:-$(node_checker_quic_root)/plugins/node-checker/quic_inbound_probe.py}"
}

# True when python can import aioquic. Echo the interpreter path.
node_checker_quic_python() {
    local py default_venv
    default_venv="$(node_checker_quic_default_venv)"
    local -a candidates=()
    [[ -n "${NODE_CHECKER_QUIC_PYTHON:-}" ]] && candidates+=("${NODE_CHECKER_QUIC_PYTHON}")
    [[ -x "${default_venv}/bin/python3" ]] && candidates+=("${default_venv}/bin/python3")
    [[ -n "${ETHPILLAR_PYTHON:-}" ]] && candidates+=("${ETHPILLAR_PYTHON}")
    candidates+=("python3")
    for py in "${candidates[@]}"; do
        [[ -n "$py" ]] || continue
        if "$py" -c "import aioquic" >/dev/null 2>&1; then
            echo "$py"
            return 0
        fi
    done
    return 1
}

node_checker_quic_auto_install_enabled() {
    [[ "${NODE_CHECKER_QUIC_AUTO_INSTALL:-1}" != "0" ]]
}

quic_probe_install_hint() {
    local root venv req
    root="$(node_checker_quic_root)"
    venv="$(node_checker_quic_default_venv)"
    req="${root}/plugins/node-checker/requirements-quic.txt"
    echo "  Node-checker auto-creates \"${venv}\" and pip-installs aioquic on first probe."
    echo "    python3 -m venv \"${venv}\""
    echo "    \"${venv}/bin/pip\" install -r \"${req}\""
    echo "  To skip auto-install: NODE_CHECKER_QUIC_AUTO_INSTALL=0. Adapted from https://github.com/bojanisc/quicmap (Apache-2.0)."
}

# One-time side venv. Default on; NODE_CHECKER_QUIC_AUTO_INSTALL=0 disables.
maybe_install_quic_probe_deps() {
    node_checker_quic_auto_install_enabled || return 1
    local venv req
    venv="$(node_checker_quic_default_venv)"
    req="$(node_checker_quic_root)/plugins/node-checker/requirements-quic.txt"
    [[ -f "$req" ]] || return 1
    python3 -m venv "$venv" || return 1
    "${venv}/bin/pip" install -q -r "$req" || return 1
    NODE_CHECKER_QUIC_VENV="$venv"
    node_checker_quic_python >/dev/null
}

# True when this IPv4 is assigned to a local interface (no NAT hairpin).
ipv4_is_on_local_interface() {
    local ip="$1"
    [[ -n "$ip" ]] || return 1
    if command -v ip >/dev/null 2>&1; then
        ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -qx "$ip" && return 0
    fi
    if command -v hostname >/dev/null 2>&1; then
        hostname -I 2>/dev/null | tr ' ' '\n' | grep -qx "$ip" && return 0
    fi
    return 1
}

# Overridable in bats. Echo probe JSON on stdout. Return 0 if invoked.
invoke_quic_inbound_probe() {
    local host="$1" port="$2"
    local py script timeout
    py="$(node_checker_quic_python)" || return 1
    script="$(node_checker_quic_probe_script)"
    timeout="${NODE_CHECKER_QUIC_TIMEOUT:-5}"
    [[ -f "$script" ]] || return 1
    "$py" "$script" --host "$host" --port "$port" --timeout "$timeout"
}

# Active inbound QUIC: can the Internet complete a QUIC/libp2p handshake to host:port?
# Missing tools → WARN (never a false FAIL). Peer-direction stays complementary.
check_inbound_quic_probe() {
    local host port json reason versions alpn named_ip version_suffix

    print_check_result "INFO" "Active inbound QUIC (quicmap-style). Local listen is not proof the Internet can complete a QUIC handshake."

    if is_caplin_node || ! cl_expects_quic; then
        return 0
    fi

    host="$(node_checker_resolve_public_ipv4)"
    [[ -n "$host" ]] && NODE_CHECKER_PUBLIC_IPV4="$host"
    if [[ -z "$host" ]]; then
        total_checks=$((total_checks + 1))
        print_check_result "WARN" "Could not resolve public IPv4 via the TCP port checker; skipping active QUIC probe."
        warning_checks=$((warning_checks + 1))
        node_checker_request_troubleshoot
        return 0
    fi

    named_ip="$(node_checker_debug_ipv4 "$host")"
    if [[ -n "$named_ip" ]]; then
        print_check_result "INFO" "QUIC probe target ${named_ip} (same requester_ip path as the public TCP checker)"
    fi

    case "$(classify_ipv4 "$host")" in
        cgnat)
            total_checks=$((total_checks + 1))
            print_check_result "WARN" "Reported address${named_ip:+ ${named_ip}} is CGNAT. Active IPv4 QUIC probe cannot succeed."
            warning_checks=$((warning_checks + 1))
            node_checker_request_troubleshoot
            return 0
            ;;
        private)
            total_checks=$((total_checks + 1))
            print_check_result "WARN" "TCP checker reported a non-public IPv4${named_ip:+ (${named_ip})}. Active inbound QUIC probe skipped."
            warning_checks=$((warning_checks + 1))
            node_checker_request_troubleshoot
            return 0
            ;;
    esac

    port="$(cl_quic_ipv4_udp_port)"
    if [[ -z "$port" ]]; then
        return 0
    fi
    if [[ "$(node_checker_cl_name)" == "Teku" ]]; then
        print_check_result "INFO" "Probing Teku IPv4 QUIC ${port}/udp. IPv6 QUIC is not probed over IPv4."
    fi

    if ! node_checker_quic_python >/dev/null 2>&1; then
        if node_checker_quic_auto_install_enabled; then
            print_check_result "INFO" "Installing aioquic once into $(node_checker_quic_default_venv) (not the default EthPillar venv)."
            maybe_install_quic_probe_deps || true
        fi
    fi
    if ! node_checker_quic_python >/dev/null 2>&1; then
        total_checks=$((total_checks + 1))
        print_check_result "WARN" "Inbound QUIC probe tools missing (aioquic). Not a FAIL — install:"
        quic_probe_install_hint
        warning_checks=$((warning_checks + 1))
        return 0
    fi

    json="$(invoke_quic_inbound_probe "$host" "$port" 2>/dev/null)" || json=""
    if ! jq -e 'type == "object"' <<< "$json" >/dev/null 2>&1; then
        total_checks=$((total_checks + 1))
        print_check_result "WARN" "QUIC probe did not return usable JSON for ${port}/udp. Not a FAIL."
        warning_checks=$((warning_checks + 1))
        return 0
    fi

    reason="$(jq -r '.reason // empty' <<< "$json" 2>/dev/null || true)"
    if [[ "$reason" == "aioquic_missing" ]]; then
        total_checks=$((total_checks + 1))
        print_check_result "WARN" "Inbound QUIC probe tools missing (aioquic). Not a FAIL — install:"
        quic_probe_install_hint
        warning_checks=$((warning_checks + 1))
        return 0
    fi

    versions="$(jq -r '(.server_versions // []) | join(", ")' <<< "$json" 2>/dev/null || true)"
    alpn="$(jq -r '(.alpn // []) | join(", ")' <<< "$json" 2>/dev/null || true)"

    total_checks=$((total_checks + 1))
    if jq -e '.ok == true' <<< "$json" >/dev/null 2>&1; then
        version_suffix=""
        if node_checker_debug_enabled && [[ -n "$versions" ]]; then
            version_suffix=" (${versions})"
        fi
        print_check_result "PASS" "Inbound QUIC open on ${port}/udp — Internet can complete a QUIC handshake${version_suffix}"
        if node_checker_debug_enabled && [[ -n "$alpn" ]]; then
            print_check_result "INFO" "QUIC probe ALPN: ${alpn}"
        fi
    elif ipv4_is_on_local_interface "$host"; then
        print_check_result "FAIL" "Inbound QUIC closed on ${port}/udp. Forward ${port}/udp on the router and allow it in UFW."
        failed_checks=$((failed_checks + 1))
        node_checker_request_troubleshoot
    else
        print_check_result "WARN" "Active QUIC probe got no handshake on ${port}/udp. This host is behind NAT, so a same-host probe can miss a working forward (hairpin). Peer-direction inbound QUIC remains complementary."
        warning_checks=$((warning_checks + 1))
        node_checker_request_troubleshoot
    fi

    if [[ "${NODE_CHECKER_DEBUG:-0}" -eq 1 ]]; then
        print_check_result "INFO" "QUIC probe JSON (no ENR): $(printf '%s' "$json" | tr -d '\n')"
    fi
}

check_peer_count() {
    local identity_json peers_json peer_count_json el_json
    local cl_connected el_connected
    local inbound outbound inbound_addrs outbound_addrs
    local in_json out_json peer_id disc first_ip kind quic_in=0
    local peers_listed=0

    identity_json="$(fetch_cl_api /eth/v1/node/identity)"
    peers_json="$(fetch_cl_api /eth/v1/node/peers)"
    peer_count_json="$(fetch_cl_api /eth/v1/node/peer_count)"
    el_json="$(fetch_el_rpc net_peerCount)"

    cl_connected="$(jq -r '.data.connected // empty' <<< "$peer_count_json" 2>/dev/null || true)"
    el_connected="$(jq -r '.result // empty' <<< "$el_json" 2>/dev/null | awk '{printf "%d\n", $1}')"

    print_check_result "INFO" "Peer direction (Beacon API). Complementary to the active QUIC probe — inbound peers also prove the Internet can dial you."

    inbound="?"
    outbound="?"
    inbound_addrs=""
    outbound_addrs=""
    if ! jq -e '.data' <<< "$peers_json" >/dev/null 2>&1; then
        total_checks=$((total_checks + 1))
        print_check_result "FAIL" "Unable to list consensus peers. Is consensus.service running and REST reachable at ${API_BN_ENDPOINT}?"
        failed_checks=$((failed_checks + 1))
        node_checker_request_troubleshoot
    elif ! cl_peers_report_direction "$peers_json"; then
        inbound="?"
        outbound="?"
        peers_listed=1
    else
        in_json="$(cl_connected_peers_json "$peers_json" inbound)"
        out_json="$(cl_connected_peers_json "$peers_json" outbound)"
        inbound="$(count_nonempty_lines "$in_json")"
        outbound="$(count_nonempty_lines "$out_json")"
        inbound_addrs="$(cl_peer_address_lines "$in_json")"
        outbound_addrs="$(cl_peer_address_lines "$out_json")"
        peers_listed=1
    fi

    peer_id="$(cl_identity_peer_id "$identity_json")"
    if [[ -n "$peer_id" ]]; then
        print_check_result "INFO" "CL peer ID: ${peer_id}"
    fi

    disc="$(cl_identity_discovery_address_lines "$identity_json")"
    first_ip="$(multiaddr_first_ip4 "$disc")"
    kind="$(classify_ipv4 "$first_ip")"
    case "$kind" in
        public)
            print_check_result "PASS" "CL discovery advertises a public IPv4 (port-forwards can work)"
            ;;
        cgnat)
            total_checks=$((total_checks + 1))
            print_check_result "WARN" "CL discovery advertises a CGNAT IPv4. IPv6 or a public IPv4 from the ISP is required."
            warning_checks=$((warning_checks + 1))
            node_checker_request_troubleshoot
            ;;
        private)
            total_checks=$((total_checks + 1))
            print_check_result "WARN" "CL discovery advertises a private IPv4. Peers cannot dial that address."
            warning_checks=$((warning_checks + 1))
            node_checker_request_troubleshoot
            ;;
        empty)
            print_check_result "INFO" "CL discovery has no IPv4 address yet (node may still be learning its external address)."
            ;;
    esac

    if printf '%s\n' "$disc" | grep -q '/quic'; then
        print_check_result "INFO" "CL discovery addresses include QUIC"
    elif cl_expects_quic; then
        print_check_result "INFO" "CL discovery addresses do not list QUIC yet (listen/UFW still required)"
        node_checker_request_troubleshoot
    fi

    if [[ "$peers_listed" -eq 1 ]]; then
        total_checks=$((total_checks + 1))
        case "$(inbound_status_kind "$inbound")" in
            working)
                print_check_result "PASS" "Inbound working — ${inbound} peer(s) dialed this consensus client (you dialed ${outbound})"
                ;;
            unknown)
                print_check_result "WARN" "CL does not report peer direction, so inbound cannot be measured."
                warning_checks=$((warning_checks + 1))
                node_checker_request_troubleshoot
                ;;
            *)
                if [[ "$outbound" =~ ^[0-9]+$ && "$outbound" -gt 0 ]]; then
                    print_check_result "WARN" "No inbound CL peers (you dialed ${outbound}). Local listen can still pass while the Internet cannot reach you."
                    warning_checks=$((warning_checks + 1))
                else
                    print_check_result "FAIL" "Consensus client has no peers. It may still be starting, or outbound UDP ${CL_P2P_PORT:-9000} is blocked too."
                    failed_checks=$((failed_checks + 1))
                fi
                node_checker_request_troubleshoot
                ;;
        esac
    fi

    if [[ "$inbound" != "?" ]]; then
        print_peer_transport_table "$inbound_addrs" "$outbound_addrs"
        quic_in="$(count_multiaddr_matching "$inbound_addrs" '/quic')"
        if cl_expects_quic; then
            total_checks=$((total_checks + 1))
            if [[ "$quic_in" -gt 0 ]]; then
                print_check_result "PASS" "Inbound QUIC present — ${quic_in} inbound peer address(es) use QUIC"
            elif [[ "$inbound" =~ ^[0-9]+$ && "$inbound" -gt 0 ]]; then
                print_check_result "WARN" "Peers dialed you over TCP only. After Glamsterdam, QUIC UDP (typically ${CL_P2P_PORT_2:-9001}/udp) must be forwarded and allowed."
                warning_checks=$((warning_checks + 1))
                node_checker_request_troubleshoot
            else
                print_check_result "INFO" "No inbound QUIC peers yet. Forward and allow UDP ${CL_P2P_PORT_2:-9001} (and discv5 UDP ${CL_P2P_PORT:-9000})."
                node_checker_request_troubleshoot
            fi
        fi
    fi

    total_checks=$((total_checks + 1))
    if [[ -n "$el_connected" && "$el_connected" -gt 0 ]]; then
        print_check_result "PASS" "Execution layer connected peers: ${el_connected}"
    else
        print_check_result "FAIL" "Execution layer connected peers: ${el_connected:-0}. Check execution.service and ${EL_P2P_PORT:-30303} TCP/UDP."
        failed_checks=$((failed_checks + 1))
    fi

    if [[ -n "$cl_connected" ]]; then
        print_check_result "INFO" "Consensus layer connected peers (API count): ${cl_connected}"
    fi

    NODE_CHECKER_TROUBLESHOOT_INBOUND="$inbound"
    NODE_CHECKER_TROUBLESHOOT_OUTBOUND="$outbound"
    maybe_print_port_troubleshoot "$inbound" "$outbound"

    if [[ "$NODE_CHECKER_DEBUG" -eq 1 ]]; then
        print_port_debug_diagnostics "$identity_json" "$peers_json"
    fi
}
