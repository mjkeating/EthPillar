"""
Tests for deploy/orchestrator.py

This file consolidates all orchestration and routing tests.
It verifies that the orchestrator correctly resolves roles into flags
and routes installation calls to the appropriate client modules.
"""
import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.orchestrator import (
    resolve_role_flags, apply_csm_overrides,
    get_combo_menu, get_vc_menu, get_ec_menu, get_cc_menu,
    get_vc_options_for_cc, resolve_vc_name,
    run_install, is_valid_combination,
    PREDEFINED_COMBOS, EXECUTION_CLIENTS, CONSENSUS_CLIENTS
)

# Mock parameters for run_install
MOCK_PARAMS = {
    'jwtsecret': '/tmp/jwt',
    'graffiti': 'EthPillar',
    'fee_address': '0x123',
    'el_p2p_port': '30303',
    'el_rpc_port': '8545',
    'el_max_peers': '50',
    'cl_p2p_port': '9000',
    'cl_rest_port': '5052',
    'cl_max_peers': '100',
    'vc_only_bn_address': 'http://localhost:5052'
}
MOCK_ENV = {}

# ─────────────────────────────────────────────────────────────────────────────
# Role & Flag Resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRoleFlags:
    @pytest.mark.parametrize("role,expected", [
        ("Solo Staking Node",              {"mevboost": True,  "validator": True,  "validator_only": False, "node_only": False}),
        ("Lido CSM Staking Node",          {"mevboost": True,  "validator": True,  "validator_only": False, "node_only": False}),
        ("Full Node Only",                 {"mevboost": False, "validator": False, "validator_only": False, "node_only": True}),
        ("Validator Client Only",          {"mevboost": False, "validator": True,  "validator_only": True,  "node_only": False}),
        ("Lido CSM Validator Client Only", {"mevboost": False, "validator": True,  "validator_only": True,  "node_only": False}),
        ("Failover Staking Node",          {"mevboost": True,  "validator": False, "validator_only": False, "node_only": False}),
    ])
    def test_role_flags(self, role, expected):
        """Verify that roles correctly map to internal installation flags."""
        result = resolve_role_flags(role, "mainnet")
        assert result == expected

    def test_sepolia_no_special_treatment(self):
        result = resolve_role_flags("Solo Staking Node", "sepolia")
        assert result["node_only"] is False

class TestCsmOverrides:
    @pytest.mark.parametrize("network,expected_fee", [
        ("mainnet", "0xmainnet"),
        ("holesky", "0xholesky"),
        ("hoodi",   "0xhoodi"),
    ])
    def test_csm_fee_recipient_per_network(self, network, expected_fee):
        env = {
            "CSM_FEE_RECIPIENT_ADDRESS_MAINNET": "0xmainnet",
            "CSM_FEE_RECIPIENT_ADDRESS_HOLESKY": "0xholesky",
            "CSM_FEE_RECIPIENT_ADDRESS_HOODI": "0xhoodi",
            "CSM_GRAFFITI": "LidoCSM"
        }
        fee, graf, mev = apply_csm_overrides("Lido CSM Staking Node", network, env, "0xdefault", "default")
        assert fee == expected_fee
        assert graf == "LidoCSM"

# ─────────────────────────────────────────────────────────────────────────────
# Menu & Selection Logic
# ─────────────────────────────────────────────────────────────────────────────

class TestClientMenuLogic:
    def test_vc_only_shows_only_vc_brands(self):
        menu = get_vc_menu()
        assert set(menu) == {"Lighthouse", "Nimbus", "Teku", "Lodestar"}

    def test_custom_ec_menu_includes_erigon(self):
        menu = get_ec_menu()
        assert "Erigon" in menu

    def test_custom_cc_menu_includes_caplin_for_erigon(self):
        menu = get_cc_menu("Erigon")
        assert "Caplin (integrated)" in menu

    def test_vc_options_same_as_cc_is_default(self):
        menu = get_vc_options_for_cc("Lighthouse")
        assert menu[0] == "Same as CC"

    def test_vc_options_no_same_as_cc_when_caplin(self):
        menu = get_vc_options_for_cc("Caplin")
        assert "Same as CC" not in menu

class TestPredefinedCombos:
    def test_lighthouse_reth_maps_to_correct_ec_cc(self):
        ec, cc = PREDEFINED_COMBOS["Lighthouse-Reth"]
        assert ec == "Reth"
        assert cc == "Lighthouse"

    def test_all_combo_ecs_are_valid(self):
        for name, (ec, cc) in PREDEFINED_COMBOS.items():
            assert ec in EXECUTION_CLIENTS, f"{name}: EC '{ec}' not in EXECUTION_CLIENTS"

# ─────────────────────────────────────────────────────────────────────────────
# Installation Routing (The Core Orchestrator Logic)
# ─────────────────────────────────────────────────────────────────────────────

class TestRunInstallRouting:
    """
    Verifies that run_install correctly routes calls to client-specific modules.
    We mock all client modules and verify that only the selected ones are called.
    """
    
    def _run(self, role, ec, cc, vc=None, flags_override=None):
        flags = resolve_role_flags(role, "mainnet")
        if flags_override:
            flags.update(flags_override)
            
        with patch('deploy.reth.download_and_install_reth', return_value=("v1", "p")) as r_ec, \
             patch('deploy.besu.download_and_install_besu', return_value=("v1", "p")) as b_ec, \
             patch('deploy.nethermind.download_and_install_nethermind', return_value=("v1", "p")) as n_ec, \
             patch('deploy.erigon.download_and_install_erigon', return_value=("v1", "p")) as e_ec, \
             patch('deploy.lighthouse.download_lighthouse', return_value="v1") as lh_dl, \
             patch('deploy.lighthouse.install_lighthouse_bn', return_value="p") as lh_bn, \
             patch('deploy.lighthouse.install_lighthouse_vc', return_value="p") as lh_vc, \
             patch('deploy.nimbus.download_nimbus', return_value="v1") as nb_dl, \
             patch('deploy.nimbus.install_nimbus_bn', return_value="p") as nb_bn, \
             patch('deploy.nimbus.install_nimbus_vc', return_value="p") as nb_vc, \
             patch('deploy.teku.download_teku', return_value="v1") as tk_dl, \
             patch('deploy.teku.install_teku_bn', return_value="p") as tk_bn, \
             patch('deploy.teku.install_teku_vc', return_value="p") as tk_vc, \
             patch('deploy.lodestar.download_lodestar', return_value="v1") as ls_dl, \
             patch('deploy.lodestar.install_lodestar_bn', return_value="p") as ls_bn, \
             patch('deploy.lodestar.install_lodestar_vc', return_value="p") as ls_vc, \
             patch('deploy.mevboost.install_mevboost', return_value=("v1", "p")) as mv_dl, \
             patch('deploy.common.setup_node'), \
             patch('deploy.common.finish_install'):
            
            run_install(role, "mainnet", ec, cc, vc or cc, flags, MOCK_PARAMS.copy(), MOCK_ENV.copy())
            
            return {
                'reth': r_ec, 'besu': b_ec, 'nethermind': n_ec, 'erigon': e_ec,
                'lh_bn': lh_bn, 'lh_vc': lh_vc, 'lh_dl': lh_dl,
                'nb_bn': nb_bn, 'nb_vc': nb_vc, 'nb_dl': nb_dl,
                'tk_bn': tk_bn, 'tk_vc': tk_vc, 'tk_dl': tk_dl,
                'ls_bn': ls_bn, 'ls_vc': ls_vc, 'ls_dl': ls_dl,
                'mev': mv_dl
            }
    def _verify_only_called(self, mocks, expected_keys):
        """
        Helper to verify that ONLY the specified mocks were called once,
        and every other mock was NOT called.
        """
        for key, mock in mocks.items():
            if key in expected_keys:
                mock.assert_called_once()
            else:
                mock.assert_not_called()

    def test_reth_lighthouse_routing(self):
        mocks = self._run("Solo Staking Node", "Reth", "Lighthouse")
        self._verify_only_called(mocks, ['reth', 'lh_dl', 'lh_bn', 'lh_vc', 'mev'])

    def test_validator_only_skips_ec_and_bn(self):
        mocks = self._run("Validator Client Only", None, None, "Teku")
        # For VC-only, only VC download and VC install should happen
        self._verify_only_called(mocks, ['tk_dl', 'tk_vc'])

    def test_failover_skips_vc(self):
        mocks = self._run("Failover Staking Node", "Nethermind", "Lodestar")
        # Failover = EC + BN + MEV (No VC)
        self._verify_only_called(mocks, ['nethermind', 'ls_dl', 'ls_bn', 'mev'])

    def test_custom_mixed_cc_vc_calls_both_downloads(self):
        # Lodestar CC + Nimbus VC
        mocks = self._run("Custom Setup", "Reth", "Lodestar", "Nimbus", flags_override={"validator": True})
        # Verify Reth EC + Lodestar BN + Nimbus VC + both CC/VC downloads
        self._verify_only_called(mocks, ['reth', 'ls_dl', 'nb_dl', 'ls_bn', 'nb_vc'])

    def test_custom_same_cc_vc_calls_download_once(self):
        # Teku CC + Teku VC
        mocks = self._run("Custom Setup", "Besu", "Teku", "Teku", flags_override={"validator": True})
        # Note: tk_dl should only be called ONCE (assert_called_once is in _verify_only_called)
        self._verify_only_called(mocks, ['besu', 'tk_dl', 'tk_bn', 'tk_vc'])

    def test_custom_no_validator_skips_vc_entirely(self):
        mocks = self._run("Custom Setup", "Nethermind", "Lighthouse", None, flags_override={"validator": False})
        self._verify_only_called(mocks, ['nethermind', 'lh_dl', 'lh_bn'])

    def test_custom_no_mev_skips_mev_install(self):
        mocks = self._run("Custom Setup", "Reth", "Nimbus", "Nimbus", flags_override={"validator": True, "mevboost": False})
        self._verify_only_called(mocks, ['reth', 'nb_dl', 'nb_bn', 'nb_vc'])

    def test_custom_erigon_caplin_routing(self):
        mocks = self._run("Custom Setup", "Erigon", "Caplin", "Lighthouse", flags_override={"validator": True})
        # Erigon + Caplin + Lighthouse VC
        # Caplin is integrated, so no separate CC download/install should happen
        self._verify_only_called(mocks, ['erigon', 'lh_dl', 'lh_vc'])
