"""
Tests for deploy/orchestrator.py

All tests call real functions from the orchestrator module.
No inline logic is reimplemented here — if an assertion passes,
it is because the production code returned the right value.
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# Silence consolemenu on import (it tries to detect terminal size)
sys.modules["consolemenu"] = MagicMock()
sys.modules["consolemenu.items"] = MagicMock()
sys.modules["consolemenu.format"] = MagicMock()
sys.modules["consolemenu.menu_component"] = MagicMock()
sys.modules["consolemenu.screen"] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.orchestrator import (
    PREDEFINED_COMBOS,
    VALID_ROLES,
    EXECUTION_CLIENTS,
    CONSENSUS_CLIENTS,
    resolve_role_flags,
    apply_csm_overrides,
    get_combo_menu,
    get_vc_menu,
    get_ec_menu,
    get_cc_menu,
    get_vc_options_for_cc,
    resolve_vc_name,
    is_valid_combination,
    run_install,
)


# ─────────────────────────────────────────────────────────────────────────────
# PREDEFINED_COMBOS
# ─────────────────────────────────────────────────────────────────────────────

class TestPredefinedCombos:
    def test_lighthouse_reth_maps_to_correct_ec_cc(self):
        ec, cc = PREDEFINED_COMBOS["Lighthouse-Reth"]
        assert ec == "Reth"
        assert cc == "Lighthouse"

    def test_caplin_erigon_maps_to_erigon_caplin(self):
        ec, cc = PREDEFINED_COMBOS["Caplin-Erigon"]
        assert ec == "Erigon"
        assert cc == "Caplin"

    def test_nimbus_nethermind_maps_correctly(self):
        ec, cc = PREDEFINED_COMBOS["Nimbus-Nethermind"]
        assert ec == "Nethermind"
        assert cc == "Nimbus"

    def test_lodestar_besu_maps_correctly(self):
        ec, cc = PREDEFINED_COMBOS["Lodestar-Besu"]
        assert ec == "Besu"
        assert cc == "Lodestar"

    def test_teku_besu_maps_correctly(self):
        ec, cc = PREDEFINED_COMBOS["Teku-Besu"]
        assert ec == "Besu"
        assert cc == "Teku"

    def test_all_combo_ecs_are_valid(self):
        for name, (ec, cc) in PREDEFINED_COMBOS.items():
            assert ec in EXECUTION_CLIENTS, f"{name}: EC '{ec}' not in EXECUTION_CLIENTS"

    def test_all_combo_ccs_are_valid(self):
        valid_cc = CONSENSUS_CLIENTS + ["Caplin"]
        for name, (ec, cc) in PREDEFINED_COMBOS.items():
            assert cc in valid_cc, f"{name}: CC '{cc}' not in valid CC list"


# ─────────────────────────────────────────────────────────────────────────────
# resolve_role_flags
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRoleFlags:
    def test_solo_staking_enables_mev_and_validator(self):
        flags = resolve_role_flags("Solo Staking Node", "mainnet")
        assert flags["mevboost"] is True
        assert flags["validator"] is True
        assert flags["validator_only"] is False
        assert flags["node_only"] is False

    def test_lido_csm_staking_enables_mev_and_validator(self):
        flags = resolve_role_flags("Lido CSM Staking Node", "mainnet")
        assert flags["mevboost"] is True
        assert flags["validator"] is True
        assert flags["validator_only"] is False
        assert flags["node_only"] is False

    def test_full_node_only_sets_node_only_no_validator_no_mev(self):
        flags = resolve_role_flags("Full Node Only", "mainnet")
        assert flags["node_only"] is True
        assert flags["validator"] is False
        assert flags["mevboost"] is False
        assert flags["validator_only"] is False

    def test_validator_client_only_sets_all_three_flags(self):
        flags = resolve_role_flags("Validator Client Only", "mainnet")
        assert flags["validator_only"] is True
        assert flags["validator"] is True
        assert flags["mevboost"] is True
        assert flags["node_only"] is False

    def test_lido_csm_vc_only_sets_all_three_flags(self):
        flags = resolve_role_flags("Lido CSM Validator Client Only", "mainnet")
        assert flags["validator_only"] is True
        assert flags["validator"] is True
        assert flags["mevboost"] is True

    def test_failover_enables_mev_but_no_validator(self):
        flags = resolve_role_flags("Failover Staking Node", "mainnet")
        assert flags["mevboost"] is True
        assert flags["validator"] is False
        assert flags["validator_only"] is False
        assert flags["node_only"] is False

    def test_custom_returns_all_false_flags(self):
        # Custom Setup role should not pre-set any flags; user configures them
        flags = resolve_role_flags("Custom Setup", "mainnet")
        assert flags["mevboost"] is False
        assert flags["validator"] is False
        assert flags["validator_only"] is False
        assert flags["node_only"] is False

    @pytest.mark.parametrize("network", ["mainnet", "holesky", "hoodi", "sepolia", "ephemery"])
    def test_solo_staking_flags_are_network_independent(self, network):
        flags = resolve_role_flags("Solo Staking Node", network)
        assert flags["mevboost"] is True
        assert flags["validator"] is True

    def test_all_roles_return_complete_flag_dict(self):
        required_keys = {"mevboost", "validator", "validator_only", "node_only"}
        for role in VALID_ROLES:
            flags = resolve_role_flags(role, "mainnet")
            assert required_keys == set(flags.keys()), f"Missing keys for role '{role}'"


# ─────────────────────────────────────────────────────────────────────────────
# apply_csm_overrides
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyCsmOverrides:
    def _env(self, **kwargs):
        defaults = {
            "MEV_MIN_BID": "0.006",
            "CSM_GRAFFITI": "CSMGraffiti",
            "CSM_MEV_MIN_BID": "0",
            "CSM_FEE_RECIPIENT_ADDRESS_MAINNET": "0xCSMmainnet",
            "CSM_FEE_RECIPIENT_ADDRESS_HOLESKY": "0xCSMholesky",
            "CSM_FEE_RECIPIENT_ADDRESS_HOODI": "0xCSMhoodi",
        }
        defaults.update(kwargs)
        return defaults

    def test_non_csm_role_does_not_override_anything(self):
        env = self._env()
        fee, graffiti, mev = apply_csm_overrides(
            "Solo Staking Node", "mainnet", env, "0xOriginal", "MyGraffiti"
        )
        assert fee == "0xOriginal"
        assert graffiti == "MyGraffiti"
        assert mev == "0.006"

    def test_csm_staking_node_overrides_graffiti(self):
        env = self._env()
        _, graffiti, _ = apply_csm_overrides(
            "Lido CSM Staking Node", "mainnet", env, "0xOriginal", "MyGraffiti"
        )
        assert graffiti == "CSMGraffiti"

    def test_csm_staking_node_overrides_mev_min_bid(self):
        env = self._env()
        _, _, mev = apply_csm_overrides(
            "Lido CSM Staking Node", "mainnet", env, "0xOriginal", "MyGraffiti"
        )
        assert mev == "0"

    def test_csm_mainnet_uses_mainnet_fee_recipient(self):
        env = self._env()
        fee, _, _ = apply_csm_overrides(
            "Lido CSM Staking Node", "mainnet", env, "0xOriginal", "MyGraffiti"
        )
        assert fee == "0xCSMmainnet"

    def test_csm_holesky_uses_holesky_fee_recipient(self):
        env = self._env()
        fee, _, _ = apply_csm_overrides(
            "Lido CSM Staking Node", "holesky", env, "0xOriginal", "MyGraffiti"
        )
        assert fee == "0xCSMholesky"

    def test_csm_hoodi_uses_hoodi_fee_recipient(self):
        env = self._env()
        fee, _, _ = apply_csm_overrides(
            "Lido CSM Staking Node", "hoodi", env, "0xOriginal", "MyGraffiti"
        )
        assert fee == "0xCSMhoodi"

    def test_csm_unknown_network_does_not_change_fee_recipient(self):
        env = self._env()
        fee, _, _ = apply_csm_overrides(
            "Lido CSM Staking Node", "sepolia", env, "0xOriginal", "MyGraffiti"
        )
        assert fee == "0xOriginal"

    def test_csm_vc_only_also_applies_overrides(self):
        env = self._env()
        fee, graffiti, mev = apply_csm_overrides(
            "Lido CSM Validator Client Only", "holesky", env, "0xOriginal", "MyGraffiti"
        )
        assert fee == "0xCSMholesky"
        assert graffiti == "CSMGraffiti"
        assert mev == "0"

    def test_missing_csm_env_var_falls_back_to_current_value(self):
        # No CSM keys at all in env
        env = {"MEV_MIN_BID": "0.006"}
        fee, graffiti, mev = apply_csm_overrides(
            "Lido CSM Staking Node", "mainnet", env, "0xOriginal", "MyGraffiti"
        )
        assert fee == "0xOriginal"
        assert graffiti == "MyGraffiti"


# ─────────────────────────────────────────────────────────────────────────────
# Menu helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestMenuHelpers:
    def test_get_combo_menu_returns_all_predefined_combo_names(self):
        menu = get_combo_menu()
        assert set(menu) == set(PREDEFINED_COMBOS.keys())

    def test_get_combo_menu_is_a_list(self):
        assert isinstance(get_combo_menu(), list)

    def test_get_vc_menu_returns_all_four_consensus_clients(self):
        assert set(get_vc_menu()) == {"Lighthouse", "Nimbus", "Teku", "Lodestar"}

    def test_get_vc_menu_does_not_include_caplin(self):
        assert "Caplin" not in get_vc_menu()
        assert "Caplin (integrated)" not in get_vc_menu()

    def test_get_ec_menu_returns_all_four_execution_clients(self):
        assert set(get_ec_menu()) == {"Besu", "Nethermind", "Reth", "Erigon"}

    def test_get_cc_menu_excludes_caplin_for_non_erigon(self):
        for ec in ["Besu", "Nethermind", "Reth"]:
            menu = get_cc_menu(ec)
            assert "Caplin (integrated)" not in menu
            assert "Caplin" not in menu

    def test_get_cc_menu_includes_caplin_integrated_for_erigon(self):
        menu = get_cc_menu("Erigon")
        assert "Caplin (integrated)" in menu

    def test_get_cc_menu_for_erigon_still_includes_standard_clients(self):
        menu = get_cc_menu("Erigon")
        for cc in CONSENSUS_CLIENTS:
            assert cc in menu

    def test_get_vc_options_for_caplin_returns_standard_ccs_only(self):
        for caplin in ["Caplin", "Caplin (integrated)"]:
            opts = get_vc_options_for_cc(caplin)
            assert "Same as CC" not in opts
            assert set(opts) == set(CONSENSUS_CLIENTS)

    def test_get_vc_options_for_standard_cc_includes_same_as_cc(self):
        for cc in CONSENSUS_CLIENTS:
            opts = get_vc_options_for_cc(cc)
            assert opts[0] == "Same as CC"

    def test_get_vc_options_for_standard_cc_includes_all_four_clients(self):
        for cc in CONSENSUS_CLIENTS:
            opts = get_vc_options_for_cc(cc)
            for client in CONSENSUS_CLIENTS:
                assert client in opts


# ─────────────────────────────────────────────────────────────────────────────
# resolve_vc_name
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveVcName:
    @pytest.mark.parametrize("cc", ["Lighthouse", "Nimbus", "Teku", "Lodestar"])
    def test_same_as_cc_returns_the_cc_name(self, cc):
        assert resolve_vc_name(cc, "Same as CC") == cc

    def test_explicit_vc_different_from_cc_is_returned(self):
        assert resolve_vc_name("Teku", "Lighthouse") == "Lighthouse"
        assert resolve_vc_name("Nimbus", "Lodestar") == "Lodestar"

    def test_explicit_vc_same_as_cc_is_returned_directly(self):
        assert resolve_vc_name("Lighthouse", "Lighthouse") == "Lighthouse"


# ─────────────────────────────────────────────────────────────────────────────
# is_valid_combination
# ─────────────────────────────────────────────────────────────────────────────

class TestIsValidCombination:
    @pytest.mark.parametrize("ec,cc", [
        ("Erigon", "Caplin"),
        ("Erigon", "Lighthouse"),
        ("Erigon", "Nimbus"),
        ("Erigon", "Teku"),
        ("Erigon", "Lodestar"),
        ("Besu", "Lighthouse"),
        ("Besu", "Nimbus"),
        ("Besu", "Teku"),
        ("Besu", "Lodestar"),
        ("Reth", "Lighthouse"),
        ("Reth", "Nimbus"),
        ("Nethermind", "Teku"),
        ("Nethermind", "Lodestar"),
    ])
    def test_valid_pairs_return_true(self, ec, cc):
        assert is_valid_combination(ec, cc) is True

    @pytest.mark.parametrize("ec,cc", [
        ("Besu", "Caplin"),
        ("Reth", "Caplin"),
        ("Nethermind", "Caplin"),
        ("Besu", "Caplin (integrated)"),
        ("InvalidEC", "Lighthouse"),
        ("Besu", "InvalidCC"),
        ("", ""),
    ])
    def test_invalid_pairs_return_false(self, ec, cc):
        assert is_valid_combination(ec, cc) is False


# ─────────────────────────────────────────────────────────────────────────────
# run_install — routing logic (mocked system calls)
# ─────────────────────────────────────────────────────────────────────────────

MOCK_PARAMS = {
    'fee_recipient': '0xDEAD',
    'graffiti': 'test',
    'bn_address': '',
    'jwtsecret_path': '/tmp/jwt.hex',
    'sync_url': 'https://sync.example.com',
    'el_p2p_port': 30303,
    'el_p2p_port_2': 30304,
    'el_rpc_port': 8545,
    'el_max_peers': 50,
    'cl_p2p_port': 9000,
    'cl_p2p_port_2': 9001,
    'cl_rest_port': 5052,
    'cl_max_peers': 100,
    'mev_min_bid': '0.006',
    'skip_prompts': 'true',
}

MOCK_ENV = {'CL_IP_ADDRESS': '127.0.0.1', 'MEV_MIN_BID': '0.006'}


def _run(role, ec, cc, vc, flags_override=None):
    """Helper: call run_install with all external calls mocked out."""
    flags = resolve_role_flags(role, "mainnet")
    if flags_override:
        flags.update(flags_override)

    # Patch everything that touches the filesystem or network
    with patch('deploy.common.setup_node'), \
         patch('deploy.common.finish_install'), \
         patch('deploy.mevboost.install_mevboost', return_value=('v1', '/path/mevboost')), \
         patch('deploy.besu.download_and_install_besu', return_value=('v1', '/path/besu')) as mock_besu, \
         patch('deploy.nethermind.download_and_install_nethermind', return_value=('v1', '/path/nethermind')) as mock_nethermind, \
         patch('deploy.reth.download_and_install_reth', return_value=('v1', '/path/reth')) as mock_reth, \
         patch('deploy.erigon.download_and_install_erigon', return_value=('v1', '/path/erigon')) as mock_erigon_integrated, \
         patch('deploy.erigon.download_and_install_erigon_standalone', return_value=('v1', '/path/erigon_standalone')) as mock_erigon_standalone, \
         patch('deploy.lighthouse.download_lighthouse', return_value='v8') as mock_lh_dl, \
         patch('deploy.lighthouse.install_lighthouse_bn', return_value='/path/lighthouse') as mock_lh_bn, \
         patch('deploy.lighthouse.install_lighthouse_vc', return_value='/path/lh_vc') as mock_lh_vc, \
         patch('deploy.nimbus.download_nimbus', return_value='v24') as mock_nb_dl, \
         patch('deploy.nimbus.install_nimbus_bn', return_value='/path/nimbus') as mock_nb_bn, \
         patch('deploy.nimbus.install_nimbus_vc', return_value='/path/nb_vc') as mock_nb_vc, \
         patch('deploy.teku.download_teku', return_value='v24') as mock_tk_dl, \
         patch('deploy.teku.install_teku_bn', return_value='/path/teku') as mock_tk_bn, \
         patch('deploy.teku.install_teku_vc', return_value='/path/tk_vc') as mock_tk_vc, \
         patch('deploy.lodestar.download_lodestar', return_value='v1') as mock_ls_dl, \
         patch('deploy.lodestar.install_lodestar_bn', return_value='/path/lodestar') as mock_ls_bn, \
         patch('deploy.lodestar.install_lodestar_vc', return_value='/path/ls_vc') as mock_ls_vc:

        run_install(role, "mainnet", ec, cc, vc, flags, MOCK_PARAMS.copy(), MOCK_ENV.copy())

        return {
            'besu': mock_besu,
            'nethermind': mock_nethermind,
            'reth': mock_reth,
            'erigon_integrated': mock_erigon_integrated,
            'erigon_standalone': mock_erigon_standalone,
            'lh_bn': mock_lh_bn, 'lh_vc': mock_lh_vc, 'lh_dl': mock_lh_dl,
            'nb_bn': mock_nb_bn, 'nb_vc': mock_nb_vc, 'nb_dl': mock_nb_dl,
            'tk_bn': mock_tk_bn, 'tk_vc': mock_tk_vc, 'tk_dl': mock_tk_dl,
            'ls_bn': mock_ls_bn, 'ls_vc': mock_ls_vc, 'ls_dl': mock_ls_dl,
        }


class TestRunInstallRouting:
    """Verify that run_install calls the correct client install functions."""

    # ── Execution client routing ─────────────────────────────────────────────

    def test_reth_ec_calls_reth_installer(self):
        mocks = _run("Full Node Only", "Reth", "Lighthouse", None, {"node_only": True, "validator": False, "mevboost": False})
        mocks['reth'].assert_called_once()

    def test_besu_ec_calls_besu_installer(self):
        mocks = _run("Full Node Only", "Besu", "Lighthouse", None, {"node_only": True, "validator": False, "mevboost": False})
        mocks['besu'].assert_called_once()

    def test_nethermind_ec_calls_nethermind_installer(self):
        mocks = _run("Full Node Only", "Nethermind", "Lighthouse", None, {"node_only": True, "validator": False, "mevboost": False})
        mocks['nethermind'].assert_called_once()

    def test_erigon_with_caplin_calls_integrated_installer(self):
        mocks = _run("Full Node Only", "Erigon", "Caplin", None, {"node_only": True, "validator": False, "mevboost": False})
        mocks['erigon_integrated'].assert_called_once()
        mocks['erigon_standalone'].assert_not_called()

    def test_erigon_with_lighthouse_calls_standalone_installer(self):
        mocks = _run("Full Node Only", "Erigon", "Lighthouse", None, {"node_only": False, "validator": False, "mevboost": False})
        mocks['erigon_standalone'].assert_called_once()
        mocks['erigon_integrated'].assert_not_called()

    def test_erigon_with_nimbus_calls_standalone_installer(self):
        mocks = _run("Full Node Only", "Erigon", "Nimbus", None, {"node_only": False, "validator": False, "mevboost": False})
        mocks['erigon_standalone'].assert_called_once()
        mocks['erigon_integrated'].assert_not_called()

    def test_erigon_with_teku_calls_standalone_installer(self):
        mocks = _run("Full Node Only", "Erigon", "Teku", None, {"node_only": False, "validator": False, "mevboost": False})
        mocks['erigon_standalone'].assert_called_once()
        mocks['erigon_integrated'].assert_not_called()

    # ── Consensus client routing ─────────────────────────────────────────────

    def test_lighthouse_cc_calls_lighthouse_bn_installer(self):
        mocks = _run("Full Node Only", "Reth", "Lighthouse", None, {"node_only": False, "validator": False, "mevboost": False})
        mocks['lh_bn'].assert_called_once()

    def test_nimbus_cc_calls_nimbus_bn_installer(self):
        mocks = _run("Full Node Only", "Reth", "Nimbus", None, {"node_only": False, "validator": False, "mevboost": False})
        mocks['nb_bn'].assert_called_once()

    def test_teku_cc_calls_teku_bn_installer(self):
        mocks = _run("Full Node Only", "Reth", "Teku", None, {"node_only": False, "validator": False, "mevboost": False})
        mocks['tk_bn'].assert_called_once()

    def test_lodestar_cc_calls_lodestar_bn_installer(self):
        mocks = _run("Full Node Only", "Reth", "Lodestar", None, {"node_only": False, "validator": False, "mevboost": False})
        mocks['ls_bn'].assert_called_once()

    def test_caplin_cc_does_not_call_separate_cc_installer(self):
        mocks = _run("Full Node Only", "Erigon", "Caplin", None, {"node_only": False, "validator": False, "mevboost": False})
        mocks['lh_bn'].assert_not_called()
        mocks['nb_bn'].assert_not_called()
        mocks['tk_bn'].assert_not_called()
        mocks['ls_bn'].assert_not_called()

    # ── Validator client routing ─────────────────────────────────────────────

    def test_lighthouse_vc_calls_lighthouse_vc_installer(self):
        mocks = _run("Solo Staking Node", "Reth", "Lighthouse", "Lighthouse")
        mocks['lh_vc'].assert_called_once()

    def test_nimbus_vc_calls_nimbus_vc_installer(self):
        mocks = _run("Solo Staking Node", "Reth", "Nimbus", "Nimbus")
        mocks['nb_vc'].assert_called_once()

    def test_teku_vc_calls_teku_vc_installer(self):
        mocks = _run("Solo Staking Node", "Reth", "Teku", "Teku")
        mocks['tk_vc'].assert_called_once()

    def test_lodestar_vc_calls_lodestar_vc_installer(self):
        mocks = _run("Solo Staking Node", "Reth", "Lodestar", "Lodestar")
        mocks['ls_vc'].assert_called_once()

    def test_mixed_vc_different_from_cc_calls_both_downloads_and_correct_vc(self):
        # Teku CC + Lighthouse VC — a custom combo
        mocks = _run("Custom Setup", "Reth", "Teku", "Lighthouse", flags_override={"validator": True})
        
        # Verify both downloaders called
        mocks['tk_dl'].assert_called_once()
        mocks['lh_dl'].assert_called_once()
        
        # Verify correct installers called
        mocks['tk_bn'].assert_called_once()
        mocks['lh_vc'].assert_called_once()
        mocks['tk_vc'].assert_not_called()

    def test_full_node_does_not_call_any_vc_installer(self):
        mocks = _run("Full Node Only", "Reth", "Lighthouse", None, {"node_only": True, "validator": False, "mevboost": False})
        mocks['lh_vc'].assert_not_called()
        mocks['nb_vc'].assert_not_called()
        mocks['tk_vc'].assert_not_called()
        mocks['ls_vc'].assert_not_called()

    def test_failover_does_not_call_any_vc_installer(self):
        # Failover: mevboost=True, validator=False
        mocks = _run("Failover Staking Node", "Reth", "Lighthouse", None)
        mocks['lh_vc'].assert_not_called()

    # ── MEV-Boost routing ────────────────────────────────────────────────────

    def test_solo_staking_installs_mevboost(self):
        with patch('deploy.common.setup_node'), \
             patch('deploy.common.finish_install'), \
             patch('deploy.mevboost.install_mevboost', return_value=('v1', '/path')) as mock_mev, \
             patch('deploy.reth.download_and_install_reth', return_value=('v1', '/path')), \
             patch('deploy.lighthouse.download_lighthouse', return_value='v8'), \
             patch('deploy.lighthouse.install_lighthouse_bn', return_value='/path'), \
             patch('deploy.lighthouse.install_lighthouse_vc', return_value='/path'):
            flags = resolve_role_flags("Solo Staking Node", "mainnet")
            run_install("Solo Staking Node", "mainnet", "Reth", "Lighthouse", "Lighthouse", flags, MOCK_PARAMS.copy(), MOCK_ENV.copy())
            mock_mev.assert_called_once()

    def test_full_node_does_not_install_mevboost(self):
        with patch('deploy.common.setup_node'), \
             patch('deploy.common.finish_install'), \
             patch('deploy.mevboost.install_mevboost', return_value=('v1', '/path')) as mock_mev, \
             patch('deploy.reth.download_and_install_reth', return_value=('v1', '/path')), \
             patch('deploy.lighthouse.download_lighthouse', return_value='v8'), \
             patch('deploy.lighthouse.install_lighthouse_bn', return_value='/path'):
            flags = resolve_role_flags("Full Node Only", "mainnet")
            run_install("Full Node Only", "mainnet", "Reth", "Lighthouse", None, flags, MOCK_PARAMS.copy(), MOCK_ENV.copy())
            mock_mev.assert_not_called()
