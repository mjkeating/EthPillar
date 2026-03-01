"""
Pure functions for generating systemd service file content.

Each function takes configuration parameters and returns the service file
content as a string. These functions have no side effects and are easily
testable.
"""


def generate_mevboost_service(eth_network, mev_min_bid, relay_options):
    """Generate MEV-Boost systemd service file content.

    Args:
        eth_network: Network name (e.g. 'mainnet', 'hoodi')
        mev_min_bid: Minimum bid value string (e.g. '0.006')
        relay_options: List of dicts with 'name' and 'url' keys

    Returns:
        Service file content as a string
    """
    lines = [
        '[Unit]',
        f'Description=MEV-Boost Service for {eth_network.upper()}',
        'Wants=network-online.target',
        'After=network-online.target',
        'Documentation=https://docs.coincashew.com',
        '',
        '[Service]',
        'User=mevboost',
        'Group=mevboost',
        'Type=simple',
        'Restart=always',
        'RestartSec=5',
        'ExecStart=/usr/local/bin/mev-boost \\',
        f'    -{eth_network} \\',
        f'    -min-bid {mev_min_bid} \\',
        '    -relay-check \\',
    ]

    for relay in relay_options:
        relay_line = f'    -relay {relay["url"]} \\'
        lines.append(relay_line)

    # Remove the trailing '\' from the last relay line
    lines[-1] = lines[-1].rstrip(' \\')

    lines.extend([
        '',
        '[Install]',
        'WantedBy=multi-user.target',
    ])
    return '\n'.join(lines)


def generate_besu_service(eth_network, el_p2p_port, el_rpc_port,
                          el_max_peer_count, jwtsecret_path,
                          network_override=None):
    """Generate Besu execution client systemd service file content.

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count
        jwtsecret_path: Path to JWT secret file
        network_override: Optional network flag override (for ephemery custom config)

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    return f'''[Unit]
Description=Besu Execution Layer Client service for {eth_network.upper()}
After=network-online.target
Wants=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=execution
Group=execution
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
Environment="JAVA_OPTS=-Xmx5g"
ExecStart=/usr/local/bin/besu/bin/besu {_network} --p2p-port={el_p2p_port} --rpc-http-port={el_rpc_port} --engine-rpc-port=8551 --max-peers={el_max_peer_count} --metrics-enabled=true --metrics-port=6060 --rpc-http-enabled=true --sync-mode=SNAP --data-storage-format=BONSAI --data-path="/var/lib/besu" --engine-jwt-secret={jwtsecret_path}

[Install]
WantedBy=multi-user.target
'''


def generate_nethermind_service(eth_network, el_p2p_port, el_rpc_port,
                                el_max_peer_count, jwtsecret_path,
                                network_override=None, sync_parameters=''):
    """Generate Nethermind execution client systemd service file content.

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count
        jwtsecret_path: Path to JWT secret file
        network_override: Optional network config override (for ephemery)
        sync_parameters: Optional sync barrier parameters

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--config {eth_network}'

    return f'''[Unit]
Description=Nethermind Execution Layer Client service for {eth_network.upper()}
After=network-online.target
Wants=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=execution
Group=execution
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
WorkingDirectory=/var/lib/nethermind
Environment="DOTNET_BUNDLE_EXTRACT_BASE_DIR=/var/lib/nethermind"
ExecStart=/usr/local/bin/nethermind/nethermind {_network} --datadir="/var/lib/nethermind" --Network.DiscoveryPort {el_p2p_port} --Network.P2PPort {el_p2p_port} --Network.MaxActivePeers {el_max_peer_count} --JsonRpc.Port {el_rpc_port} --Metrics.Enabled true --Metrics.ExposePort 6060 --JsonRpc.JwtSecretFile {jwtsecret_path} --Pruning.Mode=Hybrid --Pruning.FullPruningTrigger=VolumeFreeSpace --Pruning.FullPruningThresholdMb=300000 {sync_parameters}

[Install]
WantedBy=multi-user.target
'''


def generate_reth_service(eth_network, el_p2p_port, el_p2p_port_2,
                          el_rpc_port, el_max_peer_count, jwtsecret_path,
                          network_override=None, sync_parameters=''):
    """Generate Reth execution client systemd service file content.

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_p2p_port_2: EL secondary P2P port (discv5)
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count (already halved for reth)
        jwtsecret_path: Path to JWT secret file
        network_override: Optional network flag override (for ephemery)
        sync_parameters: Optional sync/prune parameters

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--chain {eth_network}'

    return f'''[Unit]
Description=Reth Execution Layer Client service for {eth_network.upper()}
After=network-online.target
Wants=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=execution
Group=execution
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
Environment=RUST_LOG=info
ExecStart=/usr/local/bin/reth node {_network} --full --datadir=/var/lib/reth --log.file.directory=/var/lib/reth/logs --metrics 127.0.0.1:6060 --port {el_p2p_port} --discovery.port {el_p2p_port} --enable-discv5-discovery --discovery.v5.port {el_p2p_port_2} --max-outbound-peers {el_max_peer_count} --max-inbound-peers {el_max_peer_count} --http --http.port {el_rpc_port} --http.api="rpc,eth,web3,net,debug" --authrpc.jwtsecret {jwtsecret_path} {sync_parameters}

[Install]
WantedBy=multi-user.target
'''


def generate_erigon_service(eth_network, el_p2p_port, el_rpc_port,
                            el_max_peer_count, jwtsecret_path,
                            cl_p2p_port, cl_rest_port, cl_max_peer_count,
                            sync_url,
                            network_override=None, sync_parameters='',
                            mev_parameters=''):
    """Generate Erigon+Caplin integrated execution-consensus systemd service file content.

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count
        jwtsecret_path: Path to JWT secret file
        cl_p2p_port: CL P2P port (for Caplin)
        cl_rest_port: CL REST port (for Caplin)
        cl_max_peer_count: CL max peer count (for Caplin)
        sync_url: Checkpoint sync URL
        network_override: Optional network flag override (for ephemery)
        sync_parameters: Optional sync/prune parameters
        mev_parameters: Optional MEV relay URL parameter

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--chain={eth_network}'

    _caplin = (
        f'--caplin.enable-upnp --caplin.discovery.addr=0.0.0.0 '
        f'--caplin.discovery.port={cl_p2p_port} --caplin.discovery.tcpport={cl_p2p_port} '
        f'--caplin.max-peer-count={cl_max_peer_count} '
        f'--beacon.api.addr=0.0.0.0 --beacon.api.port={cl_rest_port} '
        f'--beacon.api=beacon,validator,builder,config,debug,events,node,lighthouse '
        f'--caplin.checkpoint-sync-url={sync_url}/eth/v2/debug/beacon/states/finalized'
    )

    return f'''[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for {eth_network.upper()}
After=network-online.target
Wants=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=execution
Group=execution
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
ExecStart=/usr/local/bin/erigon --datadir=/var/lib/erigon {_network} --port={el_p2p_port} --torrent.port=42069 --http.port={el_rpc_port} --maxpeers={el_max_peer_count} --http.api=web3,eth,net,engine --metrics --pprof --prune.mode=minimal --authrpc.jwtsecret={jwtsecret_path} {sync_parameters} {_caplin} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''


# ──────────────────────────────────────────────
# Teku consensus client
# ──────────────────────────────────────────────

def generate_teku_bn_service(eth_network, sync_url, jwtsecret_path,
                             cl_rest_port, cl_p2p_port, cl_max_peer_count,
                             fee_parameters='', mev_parameters=''):
    """Generate Teku beacon node systemd service file content."""
    return f'''[Unit]
Description=Teku Beacon Node Consensus Client service for {eth_network.upper()}
Wants=network-online.target
After=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=consensus
Group=consensus
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
Environment=JAVA_OPTS=-Xmx6g
Environment=TEKU_OPTS=-XX:-HeapDumpOnOutOfMemoryError
ExecStart=/usr/local/bin/teku/bin/teku --network={eth_network} --data-path=/var/lib/teku --data-storage-mode=minimal --checkpoint-sync-url={sync_url} --ee-endpoint=http://127.0.0.1:8551 --ee-jwt-secret-file={jwtsecret_path} --rest-api-enabled=true --rest-api-port={cl_rest_port} --p2p-port={cl_p2p_port} --p2p-peer-upper-bound={cl_max_peer_count} --metrics-enabled=true --metrics-port=8008 {fee_parameters} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''


def generate_teku_vc_service(eth_network, graffiti, beacon_node_address,
                             fee_parameters='', mev_parameters=''):
    """Generate Teku validator client systemd service file content."""
    return f'''[Unit]
Description=Teku Validator Client service for {eth_network.upper()}
Wants=network-online.target
After=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=validator
Group=validator
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
LimitNOFILE=65536
ExecStart=/usr/local/bin/teku/bin/teku validator-client --network={eth_network} --data-path=/var/lib/teku_validator --validator-keys=/var/lib/teku_validator/validator_keys:/var/lib/teku_validator/validator_keys --metrics-enabled=true --metrics-port=8009 --validators-graffiti={graffiti} {beacon_node_address} {fee_parameters} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''


# ──────────────────────────────────────────────
# Lodestar consensus client
# ──────────────────────────────────────────────

def generate_lodestar_bn_service(eth_network, sync_url, jwtsecret_path,
                                 cl_rest_port, cl_p2p_port, cl_max_peer_count,
                                 fee_parameters='', mev_parameters='',
                                 network_override=None):
    """Generate Lodestar beacon node systemd service file content."""
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    return f'''[Unit]
Description=Lodestar Consensus Client service for {eth_network.upper()}
Wants=network-online.target
After=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=consensus
Group=consensus
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
WorkingDirectory=/usr/local/bin/lodestar
ExecStart=/usr/local/bin/lodestar/lodestar beacon {_network} --dataDir=/var/lib/lodestar --checkpointSyncUrl={sync_url} --execution.urls=http://127.0.0.1:8551 --jwt-secret={jwtsecret_path} --rest.port={cl_rest_port} --port={cl_p2p_port} --targetPeers={cl_max_peer_count} --metrics=true --metrics.port=8008 {fee_parameters} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''


def generate_lodestar_vc_service(eth_network, graffiti, beacon_node_address,
                                 fee_parameters='', mev_parameters='',
                                 network_override=None):
    """Generate Lodestar validator client systemd service file content."""
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    return f'''[Unit]
Description=Lodestar Validator Client service for {eth_network.upper()}
Wants=network-online.target
After=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=validator
Group=validator
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=300
LimitNOFILE=65536
ExecStart=/usr/local/bin/lodestar/lodestar validator {_network} --dataDir=/var/lib/lodestar_validator --metrics=true --metrics.port=8009 --graffiti={graffiti} {beacon_node_address} {fee_parameters} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''


# ──────────────────────────────────────────────
# Nimbus consensus client
# ──────────────────────────────────────────────

def generate_nimbus_bn_service(eth_network, jwtsecret_path,
                               cl_rest_port, cl_p2p_port, cl_max_peer_count,
                               fee_parameters='', mev_parameters='',
                               network_override=None):
    """Generate Nimbus beacon node systemd service file content."""
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    return f'''[Unit]
Description=Nimbus Beacon Node Consensus Client service for {eth_network.upper()}
Wants=network-online.target
After=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=consensus
Group=consensus
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
ExecStart=/usr/local/bin/nimbus_beacon_node {_network} --data-dir=/var/lib/nimbus --tcp-port={cl_p2p_port} --udp-port={cl_p2p_port} --max-peers={cl_max_peer_count} --rest-port={cl_rest_port} --enr-auto-update=true --web3-url=http://127.0.0.1:8551 --rest --metrics --metrics-port=8008 --jwt-secret={jwtsecret_path} --non-interactive --status-bar=false --in-process-validators=false {fee_parameters} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''


def generate_nimbus_vc_service(eth_network, graffiti, beacon_node_address,
                               fee_parameters='', mev_parameters=''):
    """Generate Nimbus validator client systemd service file content."""
    return f'''[Unit]
Description=Nimbus Validator Client service for {eth_network.upper()}
Wants=network-online.target
After=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=validator
Group=validator
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
LimitNOFILE=65536
ExecStart=/usr/local/bin/nimbus_validator_client --data-dir=/var/lib/nimbus_validator --metrics --metrics-port=8009 --non-interactive --doppelganger-detection=off --graffiti={graffiti} {beacon_node_address} {fee_parameters} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''


# ──────────────────────────────────────────────
# Lighthouse consensus client
# ──────────────────────────────────────────────

def generate_lighthouse_bn_service(eth_network, sync_url, jwtsecret_path,
                                   cl_rest_port, cl_p2p_port, cl_p2p_port_2,
                                   cl_max_peer_count,
                                   fee_parameters='', mev_parameters='',
                                   network_override=None):
    """Generate Lighthouse beacon node systemd service file content."""
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    return f'''[Unit]
Description=Lighthouse Consensus Client service for {eth_network.upper()}
Wants=network-online.target
After=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=consensus
Group=consensus
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
ExecStart=/usr/local/bin/lighthouse bn {_network} --datadir=/var/lib/lighthouse --gui --port={cl_p2p_port} --quic-port={cl_p2p_port_2} --target-peers={cl_max_peer_count} --http-port={cl_rest_port} --staking --validator-monitor-auto --checkpoint-sync-url={sync_url} --execution-endpoint=http://127.0.0.1:8551 --metrics --metrics-address=127.0.0.1 --metrics-port=8008 --execution-jwt={jwtsecret_path} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''


def generate_lighthouse_vc_service(eth_network, graffiti, beacon_node_address,
                                   fee_parameters='', mev_parameters='',
                                   network_override=None):
    """Generate Lighthouse validator client systemd service file content."""
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    return f'''[Unit]
Description=Lighthouse Validator Client service for {eth_network.upper()}
Wants=network-online.target
After=network-online.target
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User=validator
Group=validator
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
LimitNOFILE=65536
ExecStart=/usr/local/bin/lighthouse vc {_network} --datadir=/var/lib/lighthouse_validator --http --metrics --metrics-address=127.0.0.1 --metrics-port=8009 --graffiti={graffiti} {beacon_node_address} {fee_parameters} {mev_parameters}

[Install]
WantedBy=multi-user.target
'''
