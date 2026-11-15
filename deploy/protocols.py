"""Typing protocols for deploy client modules.

Each client module in ``deploy/`` is a plain Python module (not a class) that
exports role-specific functions.  These protocols document the expected
contract for static checking and contract tests.

Service generator signatures vary by client; role-based protocols capture the
minimum shared surface rather than forcing one unified ``generate_service``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable


class ReleaseInfo(Protocol):
    """Return shape of ``get_release_info``.

    ``commit`` is optional: peeled git tag SHA (or Geth blob filename hash) when
    resolvable. Update menus use it to distinguish same-semver builds (e.g. RC vs GA).
    """

    version: str
    download_urls: List[str]
    filenames: List[str]
    commit: str  # optional at runtime; present when tag/blob commit is known


@runtime_checkable
class ReleaseInfoProvider(Protocol):
    """All client modules must implement release metadata lookup."""

    def get_release_info(self, version_tag: str, arch_amd64: bool) -> Dict[str, Any]: ...


@runtime_checkable
class ExecutionClientModule(Protocol):
    """Execution-layer client module contract."""

    def get_release_info(self, version_tag: str, arch_amd64: bool) -> Dict[str, Any]: ...


@runtime_checkable
class MevBoostClientModule(Protocol):
    """MEV-Boost module contract."""

    def get_release_info(self, version_tag: str, arch_amd64: bool) -> Dict[str, Any]: ...
    def generate_mevboost_service(
        self,
        eth_network: str,
        mev_min_bid: str,
        relay_options: List[Dict[str, str]],
    ) -> str: ...


@runtime_checkable
class CharonClientModule(Protocol):
    """Obol Charon DVT middleware module contract."""

    def get_release_info(self, version_tag: str, arch_amd64: bool) -> Dict[str, Any]: ...
    def generate_charon_service(
        self,
        eth_network: str,
        beacon_node_endpoints: str,
        *,
        builder_api: bool = False,
        p2p_external_ip: str = "",
        validator_api_address: str = "127.0.0.1:3600",
        monitoring_address: str = "127.0.0.1:3620",
        p2p_tcp_address: str = "0.0.0.0:3610",
        feature_set_enable: str = "",
    ) -> str: ...


@runtime_checkable
class ConsensusBeaconClientModule(Protocol):
    """Consensus beacon-node client module contract."""

    def get_release_info(self, version_tag: str, arch_amd64: bool) -> Dict[str, Any]: ...


@runtime_checkable
class ConsensusValidatorClientModule(Protocol):
    """Consensus validator client module contract (shares module with beacon client)."""

    def get_release_info(self, version_tag: str, arch_amd64: bool) -> Dict[str, Any]: ...


# Module name → required service generator function names (beyond get_release_info).
EXECUTION_CLIENT_GENERATORS: Dict[str, List[str]] = {
    "besu": ["generate_besu_service"],
    "nethermind": ["generate_nethermind_service"],
    "reth": ["generate_reth_service"],
    "geth": ["generate_geth_service"],
    "ethrex": ["generate_ethrex_service"],
    "erigon": ["generate_erigon_service", "generate_erigon_standalone_service"],
}

MEVBOOST_GENERATORS: Dict[str, List[str]] = {
    "mevboost": ["generate_mevboost_service"],
}

CHARON_GENERATORS: Dict[str, List[str]] = {
    "charon": ["generate_charon_service"],
}

@runtime_checkable
class Web3SignerClientModule(Protocol):
    """Consensys Web3Signer remote-signer module contract."""

    def get_release_info(self, version_tag: str, arch_amd64: bool) -> Dict[str, Any]: ...
    def generate_web3signer_service(self, eth_network: str) -> str: ...


WEB3SIGNER_GENERATORS: Dict[str, List[str]] = {
    "web3signer": ["generate_web3signer_service"],
}


@runtime_checkable
class SsvClientModule(Protocol):
    """SSV Network operator node + DKG module contract."""

    def get_release_info(self, version_tag: str, arch_amd64: bool) -> Dict[str, Any]: ...
    def generate_ssv_node_service(self, eth_network: str, config_path: str = ...) -> str: ...
    def generate_ssv_dkg_service(self, eth_network: str, config_path: str = ...) -> str: ...


SSV_GENERATORS: Dict[str, List[str]] = {
    "ssv": ["generate_ssv_node_service", "generate_ssv_dkg_service"],
}

CONSENSUS_BEACON_GENERATORS: Dict[str, List[str]] = {
    "lighthouse": ["generate_lighthouse_bn_service"],
    "nimbus": ["generate_nimbus_bn_service"],
    "teku": ["generate_teku_bn_service"],
    "lodestar": ["generate_lodestar_bn_service"],
    "grandine": ["generate_grandine_bn_service"],
    "prysm": ["generate_prysm_bn_service"],
}

CONSENSUS_VALIDATOR_GENERATORS: Dict[str, List[str]] = {
    "lighthouse": ["generate_lighthouse_vc_service"],
    "nimbus": ["generate_nimbus_vc_service"],
    "teku": ["generate_teku_vc_service"],
    "lodestar": ["generate_lodestar_vc_service"],
    "prysm": ["generate_prysm_vc_service"],
}

ALL_CLIENT_MODULES: Dict[str, List[str]] = {
    **{k: v for k, v in EXECUTION_CLIENT_GENERATORS.items()},
    **MEVBOOST_GENERATORS,
    **CHARON_GENERATORS,
    **WEB3SIGNER_GENERATORS,
    **SSV_GENERATORS,
    **{k: CONSENSUS_BEACON_GENERATORS[k] + CONSENSUS_VALIDATOR_GENERATORS.get(k, [])
       for k in CONSENSUS_BEACON_GENERATORS},
}
