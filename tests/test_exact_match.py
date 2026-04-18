"""
Exact-match tests based on original deploy script fragments.

These tests guarantee 100% preservation of the service file content
by comparing the generator output against the literal strings from the
original unrefactored Python files.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.service_generators import (
    generate_mevboost_service,
    generate_besu_service,
)
from config import mainnet_relay_options

def test_besu_exact_match():
    # Simulate variables from original deploy-teku-besu.py
    eth_network = "mainnet"
    EL_P2P_PORT = 30303
    EL_RPC_PORT = 8545
    EL_MAX_PEER_COUNT = 50
    JWTSECRET_PATH = "/secrets/jwtsecret"
    
    # EXACT copied literal string from deploy-teku-besu.py lines 510-529
    expected = f'''[Unit]
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
ExecStart=/usr/local/bin/besu/bin/besu --network={eth_network} --p2p-port={EL_P2P_PORT} --rpc-http-port={EL_RPC_PORT} --engine-rpc-port=8551 --max-peers={EL_MAX_PEER_COUNT} --metrics-enabled=true --metrics-port=6060 --rpc-http-enabled=true --sync-mode=SNAP --data-storage-format=BONSAI --data-path="/var/lib/besu" --engine-jwt-secret={JWTSECRET_PATH}

[Install]
WantedBy=multi-user.target
'''

    actual = generate_besu_service(
        eth_network, EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH
    )
    
    assert actual == expected


def test_mevboost_exact_match():
    # Simulate variables from original deploy-teku-besu.py
    eth_network = "mainnet"
    MEV_MIN_BID = "0.006"
    relay_options = mainnet_relay_options
    
    # EXACT logic from lines 380-417 of deploy-teku-besu.py
    mev_boost_service_file_lines = [
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
    f'    -min-bid {MEV_MIN_BID} \\',
    '    -relay-check \\',
    ]

    for relay in relay_options:
        relay_line = f'    -relay {relay["url"]} \\'
        mev_boost_service_file_lines.append(relay_line)

    mev_boost_service_file_lines[-1] = mev_boost_service_file_lines[-1].rstrip(' \\')

    mev_boost_service_file_lines.extend([
        '',
        '[Install]',
        'WantedBy=multi-user.target',
    ])
    expected = '\n'.join(mev_boost_service_file_lines)

    actual = generate_mevboost_service(eth_network, MEV_MIN_BID, relay_options)
    
    # Expected uses precise logic from original script, verify our pure function
    # matches that legacy string builder perfectly.
    assert actual == expected

