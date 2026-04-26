"""
Tests for deploy/service_generators.py

Tier 1 — Pure function tests using golden-string comparisons.
These verify that each generator produces the exact service file content
that the original deploy scripts produced.
"""
import sys
import os
import pytest

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.service_generators import (
    generate_mevboost_service,
    generate_besu_service,
    generate_nethermind_service,
    generate_reth_service,
    generate_erigon_service,
    generate_erigon_standalone_service,
    generate_teku_bn_service,
    generate_teku_vc_service,
    generate_lodestar_bn_service,
    generate_lodestar_vc_service,
    generate_nimbus_bn_service,
    generate_nimbus_vc_service,
    generate_lighthouse_bn_service,
    generate_lighthouse_vc_service,
)
from config import (
    mainnet_relay_options,
    hoodi_relay_options,
    holesky_relay_options,
    sepolia_relay_options,
)

# ──────────────────────────────────────────────
# Default test parameters (matching env defaults)
# ──────────────────────────────────────────────
EL_P2P_PORT = 30303
EL_P2P_PORT_2 = 30304
EL_RPC_PORT = 8545
EL_MAX_PEER_COUNT = 50
CL_P2P_PORT = 9000
CL_P2P_PORT_2 = 9001
CL_REST_PORT = 5052
CL_MAX_PEER_COUNT = 100
CL_IP_ADDRESS = "127.0.0.1"
JWTSECRET_PATH = '"/secrets/jwtsecret"'
GRAFFITI = "🏠🥩🪙🛡️🦓EthPillar"
FEE_RECIPIENT_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"
MEV_MIN_BID = "0.006"
SYNC_URL = "https://beaconstate.ethstaker.cc"


# ═══════════════════════════════════════════════
# MEV-Boost service tests
# ═══════════════════════════════════════════════

class TestMevboostService:
    """Test MEV-Boost service file generation."""

    def test_mainnet_service(self):
        result = generate_mevboost_service("mainnet", MEV_MIN_BID, mainnet_relay_options)
        assert "[Unit]" in result
        assert "Description=MEV-Boost Service for MAINNET" in result
        assert "-mainnet \\" in result
        assert f"-min-bid {MEV_MIN_BID} \\" in result
        assert "-relay-check \\" in result
        # All mainnet relays present
        for relay in mainnet_relay_options:
            assert relay["url"] in result
        # Last relay should NOT have trailing backslash
        lines = result.split('\n')
        relay_lines = [l for l in lines if '-relay ' in l]
        assert not relay_lines[-1].endswith('\\')
        # But other relay lines should
        for rl in relay_lines[:-1]:
            assert rl.endswith('\\')
        assert "WantedBy=multi-user.target" in result

    def test_hoodi_service(self):
        result = generate_mevboost_service("hoodi", MEV_MIN_BID, hoodi_relay_options)
        assert "Description=MEV-Boost Service for HOODI" in result
        assert "-hoodi \\" in result
        for relay in hoodi_relay_options:
            assert relay["url"] in result

    def test_holesky_service(self):
        result = generate_mevboost_service("holesky", MEV_MIN_BID, holesky_relay_options)
        assert "Description=MEV-Boost Service for HOLESKY" in result
        assert "-holesky \\" in result
        for relay in holesky_relay_options:
            assert relay["url"] in result

    def test_sepolia_service(self):
        result = generate_mevboost_service("sepolia", MEV_MIN_BID, sepolia_relay_options)
        assert "Description=MEV-Boost Service for SEPOLIA" in result
        assert "-sepolia \\" in result
        for relay in sepolia_relay_options:
            assert relay["url"] in result

    def test_csm_min_bid(self):
        """CSM uses min_bid=0."""
        result = generate_mevboost_service("mainnet", "0", mainnet_relay_options)
        assert "-min-bid 0 \\" in result

    def test_service_structure(self):
        """Verify overall service file structure."""
        result = generate_mevboost_service("mainnet", MEV_MIN_BID, mainnet_relay_options)
        assert result.startswith("[Unit]")
        assert "[Service]" in result
        assert "[Install]" in result
        assert "User=mevboost" in result
        assert "Group=mevboost" in result
        assert "Type=simple" in result
        assert "Restart=always" in result
        assert "RestartSec=5" in result


# ═══════════════════════════════════════════════
# Besu service tests
# ═══════════════════════════════════════════════

class TestBesuService:
    """Test Besu execution client service file generation."""

    def test_mainnet_service(self):
        result = generate_besu_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH
        )
        assert "Description=Besu Execution Layer Client service for MAINNET" in result
        assert "--network=mainnet" in result
        assert f"--p2p-port={EL_P2P_PORT}" in result
        assert f"--rpc-http-port={EL_RPC_PORT}" in result
        assert "--engine-rpc-port=8551" in result
        assert f"--max-peers={EL_MAX_PEER_COUNT}" in result
        assert f"--engine-jwt-secret={JWTSECRET_PATH}" in result
        assert "--sync-mode=SNAP" in result
        assert "--data-storage-format=BONSAI" in result
        assert 'Environment="JAVA_OPTS=-Xmx5g"' in result
        assert "User=execution" in result

    def test_ephemery_custom_network(self):
        """Ephemery uses custom genesis file and bootnodes."""
        custom_network = '--genesis-file=/opt/ethpillar/testnet/besu.json --bootnodes=enr1,enr2'
        result = generate_besu_service(
            "ephemery", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT,
            JWTSECRET_PATH, network_override=custom_network
        )
        assert "Description=Besu Execution Layer Client service for EPHEMERY" in result
        assert "--genesis-file=/opt/ethpillar/testnet/besu.json" in result
        assert "--bootnodes=enr1,enr2" in result
        assert "--network=" not in result


# ═══════════════════════════════════════════════
# Nethermind service tests
# ═══════════════════════════════════════════════

class TestNethermindService:
    """Test Nethermind execution client service file generation."""

    def test_mainnet_service(self):
        sync_params = '--Sync.AncientBodiesBarrier=15537394 --Sync.AncientReceiptsBarrier=15537394'
        result = generate_nethermind_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT,
            JWTSECRET_PATH, sync_parameters=sync_params
        )
        assert "Description=Nethermind Execution Layer Client service for MAINNET" in result
        assert "--config mainnet" in result
        assert f"--Network.DiscoveryPort {EL_P2P_PORT}" in result
        assert f"--Network.P2PPort {EL_P2P_PORT}" in result
        assert f"--Network.MaxActivePeers {EL_MAX_PEER_COUNT}" in result
        assert f"--JsonRpc.Port {EL_RPC_PORT}" in result
        assert f"--JsonRpc.JwtSecretFile {JWTSECRET_PATH}" in result
        assert "WorkingDirectory=/var/lib/nethermind" in result
        assert "--Sync.AncientBodiesBarrier=15537394" in result

    def test_sepolia_service(self):
        sync_params = '--Sync.AncientBodiesBarrier=1450408 --Sync.AncientReceiptsBarrier=1450408'
        result = generate_nethermind_service(
            "sepolia", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT,
            JWTSECRET_PATH, sync_parameters=sync_params
        )
        assert "--config sepolia" in result
        assert "--Sync.AncientBodiesBarrier=1450408" in result

    def test_ephemery_custom_network(self):
        custom_network = '--config none.json --Init.ChainSpecPath=/opt/ethpillar/testnet/chainspec.json --Discovery.Bootnodes=enr1,enr2 --JsonRpc.Enabled=true --JsonRpc.EnabledModules=Eth,Subscribe,Trace,TxPool,Web3,Personal,Proof,Net,Parity,Health,Rpc,Debug,Admin --JsonRpc.EngineHost=127.0.0.1 --JsonRpc.EnginePort=8551 --Init.IsMining=false'
        result = generate_nethermind_service(
            "ephemery", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT,
            JWTSECRET_PATH, network_override=custom_network
        )
        assert "--config none.json" in result
        assert "--Init.ChainSpecPath=/opt/ethpillar/testnet/chainspec.json" in result


# ═══════════════════════════════════════════════
# Reth service tests
# ═══════════════════════════════════════════════

class TestRethService:
    """Test Reth execution client service file generation."""

    def test_mainnet_service(self):
        # Reth halves peers
        reth_max_peers = max(1, EL_MAX_PEER_COUNT // 2)
        sync_params = '--prune.bodies.pre-merge --prune.receipts.pre-merge'
        result = generate_reth_service(
            "mainnet", EL_P2P_PORT, EL_P2P_PORT_2, EL_RPC_PORT,
            reth_max_peers, JWTSECRET_PATH, sync_parameters=sync_params
        )
        assert "Description=Reth Execution Layer Client service for MAINNET" in result
        assert "--chain mainnet" in result
        assert "--full" in result
        assert f"--port {EL_P2P_PORT}" in result
        assert f"--discovery.port {EL_P2P_PORT}" in result
        assert f"--discovery.v5.port {EL_P2P_PORT_2}" in result
        assert f"--max-outbound-peers {reth_max_peers}" in result
        assert f"--max-inbound-peers {reth_max_peers}" in result
        assert "Environment=RUST_LOG=info" in result
        assert "--prune.bodies.pre-merge" in result

    def test_ephemery_custom_network(self):
        custom_network = '--chain /opt/ethpillar/testnet/genesis.json --bootnodes enr1,enr2'
        result = generate_reth_service(
            "ephemery", EL_P2P_PORT, EL_P2P_PORT_2, EL_RPC_PORT,
            25, JWTSECRET_PATH, network_override=custom_network
        )
        assert "--chain /opt/ethpillar/testnet/genesis.json" in result


# ═══════════════════════════════════════════════
# Erigon service tests
# ═══════════════════════════════════════════════

class TestErigonService:
    """Test Erigon+Caplin integrated service file generation."""

    def test_mainnet_service(self):
        sync_params = '--prune.mode=full --experiment.persist.receipts.v2=false'
        mev_params = '--caplin.mev-relay-url=http://127.0.0.1:18550'
        result = generate_erigon_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT,
            JWTSECRET_PATH, CL_P2P_PORT, CL_REST_PORT, CL_MAX_PEER_COUNT,
            SYNC_URL, sync_parameters=sync_params, mev_parameters=mev_params
        )
        assert "Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET" in result
        assert "--chain=mainnet" in result
        assert f"--port={EL_P2P_PORT}" in result
        assert f"--maxpeers={EL_MAX_PEER_COUNT}" in result
        assert "--prune.mode=minimal" in result
        assert "--caplin.mev-relay-url=http://127.0.0.1:18550" in result
        assert f"--caplin.discovery.port={CL_P2P_PORT}" in result
        assert f"--beacon.api.port={CL_REST_PORT}" in result
        assert f"--caplin.checkpoint-sync-url={SYNC_URL}/eth/v2/debug/beacon/states/finalized" in result

    def test_no_mev(self):
        result = generate_erigon_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT,
            JWTSECRET_PATH, CL_P2P_PORT, CL_REST_PORT, CL_MAX_PEER_COUNT,
            SYNC_URL
        )
        assert "--caplin.mev-relay-url" not in result


# ═══════════════════════════════════════════════
# Erigon Standalone (no Caplin) service tests
# ═══════════════════════════════════════════════

class TestErigonStandaloneService:
    """Test Erigon standalone execution client service file generation (without Caplin)."""

    def test_mainnet_service(self):
        result = generate_erigon_standalone_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH
        )
        assert "Description=Erigon Execution Layer Client service for MAINNET" in result
        assert "--chain=mainnet" in result
        assert f"--port={EL_P2P_PORT}" in result
        assert f"--http.port={EL_RPC_PORT}" in result
        assert f"--maxpeers={EL_MAX_PEER_COUNT}" in result
        assert f"--authrpc.jwtsecret={JWTSECRET_PATH}" in result
        assert "--externalcl" in result
        assert "User=execution" in result

    def test_holesky_service(self):
        result = generate_erigon_standalone_service(
            "holesky", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH
        )
        assert "Description=Erigon Execution Layer Client service for HOLESKY" in result
        assert "--chain=holesky" in result
        assert "--externalcl" in result

    def test_no_caplin_flags(self):
        """Standalone service must NOT contain any Caplin-specific flags."""
        result = generate_erigon_standalone_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH
        )
        assert "--caplin.enable-upnp" not in result
        assert "--caplin.discovery.port" not in result
        assert "--beacon.api.port" not in result
        assert "--caplin.checkpoint-sync-url" not in result

    def test_ephemery_network_override(self):
        custom_override = "--chain /opt/ephemery/genesis.json --bootnodes enr1,enr2"
        result = generate_erigon_standalone_service(
            "ephemery", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH,
            network_override=custom_override
        )
        assert "--chain /opt/ephemery/genesis.json" in result
        assert "--chain=ephemery" not in result

    def test_sync_parameters_appended(self):
        sync_params = "--prune.mode=full"
        result = generate_erigon_standalone_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH,
            sync_parameters=sync_params
        )
        assert "--prune.mode=full" in result
        # Also confirm --externalcl still present
        assert "--externalcl" in result

    def test_service_structure(self):
        result = generate_erigon_standalone_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH
        )
        assert result.startswith("[Unit]")
        assert "[Service]" in result
        assert "[Install]" in result
        assert "WantedBy=multi-user.target" in result
        assert "Restart=on-failure" in result
        assert "KillSignal=SIGINT" in result
        assert "TimeoutStopSec=900" in result

    def test_different_from_integrated(self):
        """Standalone description must differ from the integrated Erigon-Caplin description."""
        standalone = generate_erigon_standalone_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT, JWTSECRET_PATH
        )
        integrated = generate_erigon_service(
            "mainnet", EL_P2P_PORT, EL_RPC_PORT, EL_MAX_PEER_COUNT,
            JWTSECRET_PATH, CL_P2P_PORT, CL_REST_PORT, CL_MAX_PEER_COUNT, SYNC_URL
        )
        assert "Erigon Execution Layer Client" in standalone
        assert "Erigon-Caplin Integrated" in integrated


# ═══════════════════════════════════════════════
# Teku service tests
# ═══════════════════════════════════════════════

class TestTekuService:
    """Test Teku BN and VC service file generation."""

    def test_bn_mainnet_with_mev(self):
        fee_params = f'--validators-proposer-default-fee-recipient={FEE_RECIPIENT_ADDRESS}'
        mev_params = '--validators-builder-registration-default-enabled=true --builder-endpoint=http://127.0.0.1:18550'
        result = generate_teku_bn_service(
            "mainnet", SYNC_URL, JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_MAX_PEER_COUNT,
            fee_parameters=fee_params, mev_parameters=mev_params
        )
        assert "Description=Teku Beacon Node Consensus Client service for MAINNET" in result
        assert f"--network=mainnet" in result
        assert f"--checkpoint-sync-url={SYNC_URL}" in result
        assert f"--rest-api-port={CL_REST_PORT}" in result
        assert f"--p2p-port={CL_P2P_PORT}" in result
        assert "--validators-builder-registration-default-enabled=true" in result
        assert "--builder-endpoint=http://127.0.0.1:18550" in result
        assert f"--validators-proposer-default-fee-recipient={FEE_RECIPIENT_ADDRESS}" in result
        assert "Environment=JAVA_OPTS=-Xmx6g" in result
        assert "Environment=TEKU_OPTS=-XX:-HeapDumpOnOutOfMemoryError" in result

    def test_bn_no_mev(self):
        result = generate_teku_bn_service(
            "mainnet", SYNC_URL, JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_MAX_PEER_COUNT
        )
        assert "--builder-endpoint" not in result

    def test_vc_mainnet_with_mev(self):
        fee_params = f'--validators-proposer-default-fee-recipient={FEE_RECIPIENT_ADDRESS}'
        mev_params = '--validators-builder-registration-default-enabled=true'
        bn_addr = f'--beacon-node-api-endpoint=http://{CL_IP_ADDRESS}:{CL_REST_PORT}'
        result = generate_teku_vc_service(
            "mainnet", GRAFFITI, bn_addr,
            fee_parameters=fee_params, mev_parameters=mev_params
        )
        assert "Description=Teku Validator Client service for MAINNET" in result
        assert f"--validators-graffiti={GRAFFITI}" in result
        assert f"--beacon-node-api-endpoint=http://{CL_IP_ADDRESS}:{CL_REST_PORT}" in result
        assert "--validator-keys=/var/lib/teku_validator/validator_keys:/var/lib/teku_validator/validator_keys" in result
        assert "User=validator" in result

    def test_vc_custom_bn_address(self):
        bn_addr = '--beacon-node-api-endpoint=http://192.168.1.123:5052'
        result = generate_teku_vc_service("mainnet", GRAFFITI, bn_addr)
        assert "--beacon-node-api-endpoint=http://192.168.1.123:5052" in result


# ═══════════════════════════════════════════════
# Lodestar service tests
# ═══════════════════════════════════════════════

class TestLodestarService:
    """Test Lodestar BN and VC service file generation."""

    def test_bn_mainnet_with_mev(self):
        fee_params = f'--suggestedFeeRecipient={FEE_RECIPIENT_ADDRESS}'
        mev_params = '--builder --builder.urls http://127.0.0.1:18550'
        result = generate_lodestar_bn_service(
            "mainnet", SYNC_URL, JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_MAX_PEER_COUNT,
            fee_parameters=fee_params, mev_parameters=mev_params
        )
        assert "Description=Lodestar Consensus Client service for MAINNET" in result
        assert f"--network=mainnet" in result
        assert f"--checkpointSyncUrl={SYNC_URL}" in result
        assert f"--rest.port={CL_REST_PORT}" in result
        assert f"--port={CL_P2P_PORT}" in result
        assert "--builder" in result
        assert f"--suggestedFeeRecipient={FEE_RECIPIENT_ADDRESS}" in result
        assert "WorkingDirectory=/usr/local/bin/lodestar" in result

    def test_bn_ephemery(self):
        custom_network = '--paramsFile=/opt/ethpillar/testnet/config.yaml --genesisStateFile=/opt/ethpillar/testnet/genesis.ssz --bootnodes=enr1 --network.connectToDiscv5Bootnodes --ignoreWeakSubjectivityCheck'
        result = generate_lodestar_bn_service(
            "ephemery", SYNC_URL, JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_MAX_PEER_COUNT,
            network_override=custom_network
        )
        assert "--paramsFile=/opt/ethpillar/testnet/config.yaml" in result
        assert "--genesisStateFile=/opt/ethpillar/testnet/genesis.ssz" in result

    def test_vc_mainnet(self):
        fee_params = f'--suggestedFeeRecipient={FEE_RECIPIENT_ADDRESS}'
        mev_params = '--builder'
        bn_addr = f'--beaconNodes=http://{CL_IP_ADDRESS}:{CL_REST_PORT}'
        result = generate_lodestar_vc_service(
            "mainnet", GRAFFITI, bn_addr,
            fee_parameters=fee_params, mev_parameters=mev_params
        )
        assert "Description=Lodestar Validator Client service for MAINNET" in result
        assert f"--graffiti={GRAFFITI}" in result
        assert "TimeoutStopSec=300" in result  # Lodestar VC uses 300, not 900
        assert "--dataDir=/var/lib/lodestar_validator" in result


# ═══════════════════════════════════════════════
# Nimbus service tests
# ═══════════════════════════════════════════════

class TestNimbusService:
    """Test Nimbus BN and VC service file generation."""

    def test_bn_mainnet_with_mev(self):
        fee_params = f'--suggested-fee-recipient={FEE_RECIPIENT_ADDRESS}'
        mev_params = '--payload-builder=true --payload-builder-url=http://127.0.0.1:18550'
        result = generate_nimbus_bn_service(
            "mainnet", JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_MAX_PEER_COUNT,
            fee_parameters=fee_params, mev_parameters=mev_params
        )
        assert "Description=Nimbus Beacon Node Consensus Client service for MAINNET" in result
        assert f"--network=mainnet" in result
        assert f"--tcp-port={CL_P2P_PORT}" in result
        assert f"--udp-port={CL_P2P_PORT}" in result
        assert f"--rest-port={CL_REST_PORT}" in result
        assert "--payload-builder=true" in result
        assert "--in-process-validators=false" in result
        assert "--non-interactive" in result
        assert "--status-bar=false" in result

    def test_bn_ephemery(self):
        custom_network = '--network=/opt/ethpillar/testnet --bootstrap-node=enr1,enr2'
        result = generate_nimbus_bn_service(
            "ephemery", JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_MAX_PEER_COUNT,
            network_override=custom_network
        )
        assert "--network=/opt/ethpillar/testnet" in result
        assert "--bootstrap-node=enr1,enr2" in result

    def test_vc_mainnet(self):
        fee_params = f'--suggested-fee-recipient={FEE_RECIPIENT_ADDRESS}'
        mev_params = '--payload-builder=true'
        bn_addr = f'--beacon-node=http://{CL_IP_ADDRESS}:{CL_REST_PORT}'
        result = generate_nimbus_vc_service(
            "mainnet", GRAFFITI, bn_addr,
            fee_parameters=fee_params, mev_parameters=mev_params
        )
        assert "Description=Nimbus Validator Client service for MAINNET" in result
        assert f"--graffiti={GRAFFITI}" in result
        assert "--doppelganger-detection=off" in result
        assert "--data-dir=/var/lib/nimbus_validator" in result
        assert f"--beacon-node=http://{CL_IP_ADDRESS}:{CL_REST_PORT}" in result


# ═══════════════════════════════════════════════
# Lighthouse service tests
# ═══════════════════════════════════════════════

class TestLighthouseService:
    """Test Lighthouse BN and VC service file generation."""

    def test_bn_mainnet_with_mev(self):
        mev_params = '--builder http://127.0.0.1:18550'
        result = generate_lighthouse_bn_service(
            "mainnet", SYNC_URL, JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_P2P_PORT_2, CL_MAX_PEER_COUNT,
            mev_parameters=mev_params
        )
        assert "Description=Lighthouse Consensus Client service for MAINNET" in result
        assert "--network=mainnet" in result
        assert f"--port={CL_P2P_PORT}" in result
        assert f"--quic-port={CL_P2P_PORT_2}" in result
        assert f"--http-port={CL_REST_PORT}" in result
        assert f"--checkpoint-sync-url={SYNC_URL}" in result
        assert "--staking" in result
        assert "--validator-monitor-auto" in result
        assert "--gui" in result
        assert "--builder http://127.0.0.1:18550" in result

    def test_bn_no_mev(self):
        result = generate_lighthouse_bn_service(
            "mainnet", SYNC_URL, JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_P2P_PORT_2, CL_MAX_PEER_COUNT
        )
        assert "--builder" not in result

    def test_bn_ephemery(self):
        custom_network = '--testnet-dir=/opt/ethpillar/testnet --boot-nodes=enr1,enr2'
        result = generate_lighthouse_bn_service(
            "ephemery", SYNC_URL, JWTSECRET_PATH,
            CL_REST_PORT, CL_P2P_PORT, CL_P2P_PORT_2, CL_MAX_PEER_COUNT,
            network_override=custom_network
        )
        assert "--testnet-dir=/opt/ethpillar/testnet" in result

    def test_vc_mainnet_with_mev(self):
        fee_params = f'--suggested-fee-recipient={FEE_RECIPIENT_ADDRESS}'
        mev_params = '--builder-proposals'
        bn_addr = f'--beacon-nodes=http://{CL_IP_ADDRESS}:{CL_REST_PORT}'
        result = generate_lighthouse_vc_service(
            "mainnet", GRAFFITI, bn_addr,
            fee_parameters=fee_params, mev_parameters=mev_params
        )
        assert "Description=Lighthouse Validator Client service for MAINNET" in result
        assert f"--graffiti={GRAFFITI}" in result
        assert "--builder-proposals" in result
        assert "--datadir=/var/lib/lighthouse_validator" in result
        assert "--http" in result
        assert f"--beacon-nodes=http://{CL_IP_ADDRESS}:{CL_REST_PORT}" in result

    def test_vc_custom_bn_address(self):
        bn_addr = '--beacon-nodes=http://192.168.1.123:5052'
        result = generate_lighthouse_vc_service("mainnet", GRAFFITI, bn_addr)
        assert "--beacon-nodes=http://192.168.1.123:5052" in result

    def test_vc_ephemery(self):
        bn_addr = f'--beacon-nodes=http://{CL_IP_ADDRESS}:{CL_REST_PORT}'
        result = generate_lighthouse_vc_service(
            "ephemery", GRAFFITI, bn_addr,
            network_override='--testnet-dir=/opt/ethpillar/testnet'
        )
        assert "--testnet-dir=/opt/ethpillar/testnet" in result
        assert "--network=" not in result
