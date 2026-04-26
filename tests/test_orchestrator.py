import pytest
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.orchestrator import (
    resolve_role_flags, apply_csm_overrides,
    get_combo_menu, get_vc_menu, get_ec_menu, get_cc_menu,
    get_vc_options_for_cc, resolve_vc_name,
    run_install, is_valid_combination
)

class TestResolveRoleFlags:
    @pytest.mark.parametrize("role,expected", [
        ("Solo Staking Node",              {"mevboost": True,  "validator": True,  "validator_only": False, "node_only": False}),
        ("Lido CSM Staking Node",          {"mevboost": True,  "validator": True,  "validator_only": False, "node_only": False}),
        ("Full Node Only",                 {"mevboost": False, "validator": False, "validator_only": False, "node_only": True}),
        ("Validator Client Only",          {"mevboost": True,  "validator": True,  "validator_only": True,  "node_only": False}),
        ("Lido CSM Validator Client Only", {"mevboost": True,  "validator": True,  "validator_only": True,  "node_only": False}),
        ("Failover Staking Node",          {"mevboost": True,  "validator": False, "validator_only": False, "node_only": False}),
    ])
    def test_role_flags(self, role, expected):
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

    def test_non_csm_no_overrides(self):
        env = {"CSM_GRAFFITI": "LidoCSM"}
        fee, graf, mev = apply_csm_overrides("Solo Staking Node", "mainnet", env, "0xdefault", "default")
        assert fee == "0xdefault"
        assert graf == "default"

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

    def test_custom_cc_menu_excludes_caplin_for_non_erigon(self):
        menu = get_cc_menu("Besu")
        assert "Caplin (integrated)" not in menu
        assert "Caplin" not in menu

    def test_vc_options_same_as_cc_is_default(self):
        menu = get_vc_options_for_cc("Lighthouse")
        assert menu[0] == "Same as CC"
        assert "Lighthouse" in menu

    def test_vc_options_no_same_as_cc_when_caplin(self):
        menu = get_vc_options_for_cc("Caplin")
        assert "Same as CC" not in menu
        assert "Lighthouse" in menu

class TestResolveVcName:
    @pytest.mark.parametrize("cc,choice,expected", [
        ("Lighthouse", "Same as CC",  "Lighthouse"),
        ("Nimbus",     "Same as CC",  "Nimbus"),
        ("Lighthouse", "Nimbus",      "Nimbus"),
        ("Reth",       "Teku",        "Teku"),
    ])
    def test_resolve_vc_name(self, cc, choice, expected):
        assert resolve_vc_name(cc, choice) == expected

class TestInstallOrchestration:
    @patch('deploy.besu.download_and_install_besu', return_value=("v1", "path"))
    @patch('deploy.lighthouse.download_lighthouse', return_value="v1")
    @patch('deploy.lighthouse.install_lighthouse_bn', return_value="path")
    @patch('deploy.lighthouse.install_lighthouse_vc', return_value="path")
    @patch('deploy.mevboost.install_mevboost', return_value=("v1", "path"))
    @patch('deploy.common.setup_node')
    @patch('deploy.common.finish_install')
    def test_solo_lighthouse_besu(self, mock_finish, mock_setup, mock_mev, mock_lh_vc, mock_lh_bn, mock_lh_dl, mock_besu):
        flags = resolve_role_flags("Solo Staking Node", "mainnet")
        params = {"cl_rest_port": "5052"}
        run_install(
            role="Solo Staking Node", network="mainnet",
            ec_name="Besu", cc_name="Lighthouse", vc_name="Lighthouse",
            flags=flags, params=params, env_vars={}
        )
        mock_besu.assert_called_once()
        mock_lh_dl.assert_called_once()
        mock_lh_bn.assert_called_once()
        mock_lh_vc.assert_called_once()
        mock_mev.assert_called_once()

    @patch('deploy.nimbus.download_nimbus', return_value="v1")
    @patch('deploy.nimbus.install_nimbus_vc', return_value="path")
    @patch('deploy.besu.download_and_install_besu')
    @patch('deploy.common.setup_node')
    @patch('deploy.common.finish_install')
    def test_vc_only_skips_ec_and_bn(self, mock_finish, mock_setup, mock_besu, mock_nimbus_vc, mock_nimbus_dl):
        flags = resolve_role_flags("Validator Client Only", "mainnet")
        params = {"cl_rest_port": "5052"}
        run_install(
            role="Validator Client Only", network="mainnet",
            ec_name=None, cc_name=None, vc_name="Nimbus",
            flags=flags, params=params, env_vars={}
        )
        mock_besu.assert_not_called()
        mock_nimbus_dl.assert_called_once()
        mock_nimbus_vc.assert_called_once()

    @patch('deploy.nethermind.download_and_install_nethermind', return_value=("v1", "path"))
    @patch('deploy.teku.download_teku', return_value="v1")
    @patch('deploy.teku.install_teku_bn', return_value="path")
    @patch('deploy.teku.install_teku_vc')
    @patch('deploy.common.setup_node')
    @patch('deploy.common.finish_install')
    def test_full_node_skips_vc(self, mock_finish, mock_setup, mock_teku_vc, mock_teku_bn, mock_teku_dl, mock_nethermind):
        flags = resolve_role_flags("Full Node Only", "mainnet")
        params = {"cl_rest_port": "5052"}
        run_install(
            role="Full Node Only", network="mainnet",
            ec_name="Nethermind", cc_name="Teku", vc_name=None,
            flags=flags, params=params, env_vars={}
        )
        mock_teku_vc.assert_not_called()
        mock_nethermind.assert_called_once()
        mock_teku_bn.assert_called_once()
