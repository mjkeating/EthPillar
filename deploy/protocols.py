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
    """Return shape of ``get_release_info``."""

    version: str
    download_urls: List[str]
    filenames: List[str]


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
    **{k: CONSENSUS_BEACON_GENERATORS[k] + CONSENSUS_VALIDATOR_GENERATORS.get(k, [])
       for k in CONSENSUS_BEACON_GENERATORS},
}
