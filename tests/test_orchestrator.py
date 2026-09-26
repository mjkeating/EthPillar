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
from contextlib import ExitStack

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.orchestrator import (
    resolve_role_flags, apply_csm_overrides,
    get_combo_menu, get_vc_menu, get_ec_menu, get_cc_menu,
    get_vc_options_for_cc, resolve_vc_name, is_charon_vc_choice,
    run_install, is_valid_combination,
    PREDEFINED_COMBOS, EXECUTION_CLIENTS, CONSENSUS_CLIENTS,
    CHARON_VC_LABEL, _int_param, _with_dvt_params,
    lodestar_bn_vc_incompatibility_message, LODESTAR_BN_INCOMPATIBLE_VCS,
)

# Mock parameters for run_install — keys must match what orchestrator.run_install() reads
MOCK_PARAMS = {
    'jwtsecret_path': '/tmp/jwt',
    'graffiti': 'EthPillar',
    'fee_recipient': '0x123',
    'bn_address': '',
    'sync_url': '',
    'el_p2p_port': '30303',
    'el_p2p_port_2': '30304',
    'el_rpc_port': '8545',
    'el_max_peers': '50',
    'cl_p2p_port': '9000',
    'cl_p2p_port_2': '9001',
    'cl_rest_port': '5052',
    'cl_max_peers': '100',
    'mev_min_bid': '0.006',
    'skip_prompts': 'true',
}
MOCK_ENV = {}

# ─────────────────────────────────────────────────────────────────────────────
# Role & Flag Resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRoleFlags:
    @pytest.mark.parametrize("role,expected", [
        ("Solo Staking Node",              {"mevboost": True,  "builder_api": True,  "validator": True,  "validator_only": False, "node_only": False}),
        ("Lido CSM Staking Node",          {"mevboost": True,  "builder_api": True,  "validator": True,  "validator_only": False, "node_only": False}),
        ("Full Node Only",                 {"mevboost": False, "builder_api": False, "validator": False, "validator_only": False, "node_only": True}),
        ("Validator Client Only",          {"mevboost": False, "builder_api": False, "validator": True,  "validator_only": True,  "node_only": False}),
        ("Lido CSM Validator Client Only", {"mevboost": False, "builder_api": False, "validator": True,  "validator_only": True,  "node_only": False}),
        ("Failover Staking Node",          {"mevboost": True,  "builder_api": True,  "validator": False, "validator_only": False, "node_only": False}),
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

class TestParamParsing:
    @pytest.mark.parametrize("value,expected", [
        (None, 0),
        ("", 0),
        ("30304", 30304),
        (9001, 9001),
    ])
    def test_int_param_defaults_only_for_unset_values(self, value, expected):
        assert _int_param({"port": value}, "port") == expected

# ─────────────────────────────────────────────────────────────────────────────
# Menu & Selection Logic
# ─────────────────────────────────────────────────────────────────────────────

class TestCustomClientMenuLogic:
    def test_vc_only_shows_only_vc_clients(self):
        menu = get_vc_menu(include_charon=False)
        expected = set(CONSENSUS_CLIENTS) - {'Grandine'}
        assert set(menu) == expected

    def test_vc_only_menu_includes_charon_by_default(self):
        menu = get_vc_menu()
        assert menu[-1] == CHARON_VC_LABEL
        assert "Grandine" not in menu

    def test_custom_ec_menu_includes_all_clients(self):
        menu = get_ec_menu()
        assert set(menu) == set(EXECUTION_CLIENTS)

    def test_custom_cc_menu_includes_caplin_for_erigon(self):
        menu = get_cc_menu("Erigon")
        assert "Caplin (integrated)" in menu

    def test_vc_options_same_as_cc_is_default(self):
        menu = get_vc_options_for_cc("Lighthouse")
        assert menu[0] == "Same as CC"
        assert menu[-1] == CHARON_VC_LABEL

    def test_vc_options_no_same_as_cc_when_caplin(self):
        menu = get_vc_options_for_cc("Caplin")
        assert "Same as CC" not in menu
        assert menu[-1] == CHARON_VC_LABEL

    def test_charon_signer_menu_excludes_charon_and_integrated_grandine(self):
        menu = get_vc_options_for_cc("Lighthouse", for_charon_signer=True)
        assert CHARON_VC_LABEL not in menu
        assert "Grandine (integrated)" not in menu
        assert menu[0] == "Same as CC"

    def test_grandine_cc_offers_charon_after_integrated(self):
        menu = get_vc_options_for_cc("Grandine")
        assert menu[0] == "Grandine (integrated)"
        assert menu[-1] == CHARON_VC_LABEL

    def test_is_charon_vc_choice(self):
        assert is_charon_vc_choice(CHARON_VC_LABEL)
        assert not is_charon_vc_choice("Lodestar")

    def test_with_dvt_params_appends_client_specific_flags(self):
        assert _with_dvt_params("--builder", "Lodestar", True) == "--builder --distributed"
        assert _with_dvt_params("", "Lighthouse", True) == "--distributed"
        assert _with_dvt_params("", "Nimbus", True) == "--distributed"
        assert _with_dvt_params("", "Lodestar", True) == "--distributed"
        assert _with_dvt_params("--enable-builder", "Prysm", True) == "--enable-builder --distributed"
        assert (
            _with_dvt_params("--validators-builder-registration-default-enabled=true", "Teku", True)
            == "--validators-builder-registration-default-enabled=true "
            "--Xobol-dvt-integration-enabled=true "
            "--Xvalidator-client-beacon-api-executor-threads=50"
        )
        assert _with_dvt_params("--builder", "Lodestar", False) == "--builder"
        assert _with_dvt_params("", "Grandine (integrated)", True) == ""

    def test_lodestar_bn_vc_incompatibility_message(self):
        msg = lodestar_bn_vc_incompatibility_message("Lodestar", "Lighthouse")
        assert msg is not None
        assert "Lodestar beacon node" in msg
        assert "Lighthouse" in msg
        assert lodestar_bn_vc_incompatibility_message("Lodestar", "Lodestar") is None
        assert lodestar_bn_vc_incompatibility_message("Lighthouse", "Lodestar") is None
        for vc in LODESTAR_BN_INCOMPATIBLE_VCS:
            assert lodestar_bn_vc_incompatibility_message("Lodestar", vc)

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
    
    def _run(self, role, ec, cc, vc=None, flags_override=None, params_override=None):
        flags = resolve_role_flags(role, "mainnet")
        flags.update(flags_override or {})
            
        with ExitStack() as stack:
            r_ec = stack.enter_context(patch('deploy.reth.download_and_install_reth', return_value=("v1", "p")))
            b_ec = stack.enter_context(patch('deploy.besu.download_and_install_besu', return_value=("v1", "p")))
            n_ec = stack.enter_context(patch('deploy.nethermind.download_and_install_nethermind', return_value=("v1", "p")))
            e_ec = stack.enter_context(patch('deploy.erigon.download_and_install_erigon', return_value=("v1", "p")))
            g_ec = stack.enter_context(patch('deploy.geth.download_and_install_geth', return_value=("v1", "p")))
            
            lh_dl = stack.enter_context(patch('deploy.lighthouse.download_lighthouse', return_value="v1"))
            lh_bn = stack.enter_context(patch('deploy.lighthouse.install_lighthouse_bn', return_value="p"))
            lh_vc = stack.enter_context(patch('deploy.lighthouse.install_lighthouse_vc', return_value="p"))
            
            nb_dl = stack.enter_context(patch('deploy.nimbus.download_nimbus', return_value="v1"))
            nb_bn = stack.enter_context(patch('deploy.nimbus.install_nimbus_bn', return_value="p"))
            nb_vc = stack.enter_context(patch('deploy.nimbus.install_nimbus_vc', return_value="p"))
            
            tk_dl = stack.enter_context(patch('deploy.teku.download_teku', return_value="v1"))
            tk_bn = stack.enter_context(patch('deploy.teku.install_teku_bn', return_value="p"))
            tk_vc = stack.enter_context(patch('deploy.teku.install_teku_vc', return_value="p"))
            
            ls_dl = stack.enter_context(patch('deploy.lodestar.download_lodestar', return_value="v1"))
            ls_bn = stack.enter_context(patch('deploy.lodestar.install_lodestar_bn', return_value="p"))
            ls_vc = stack.enter_context(patch('deploy.lodestar.install_lodestar_vc', return_value="p"))
            
            gr_dl = stack.enter_context(patch('deploy.grandine.download_grandine', return_value="v1"))
            gr_bn = stack.enter_context(patch('deploy.grandine.install_grandine_bn', return_value="p"))
            
            pr_dl = stack.enter_context(patch('deploy.prysm.download_prysm', return_value="v1"))
            pr_bn = stack.enter_context(patch('deploy.prysm.install_prysm_bn', return_value="p"))
            pr_vc = stack.enter_context(patch('deploy.prysm.install_prysm_vc', return_value="p"))
            
            mv_dl = stack.enter_context(patch('deploy.mevboost.install_mevboost', return_value=("v1", "p")))
            ch_dl = stack.enter_context(patch('deploy.charon.install_charon', return_value=("v1", "p")))
            
            stack.enter_context(patch('deploy.common.setup_node'))
            stack.enter_context(patch('deploy.common.finish_install'))
            
            params = {**MOCK_PARAMS, **(params_override or {})}
            run_install(role, "mainnet", ec, cc, vc or cc, flags, params, MOCK_ENV.copy())
            
            return {
                'reth': r_ec, 'besu': b_ec, 'nethermind': n_ec, 'erigon': e_ec, 'geth': g_ec,
                'lh_bn': lh_bn, 'lh_vc': lh_vc, 'lh_dl': lh_dl,
                'nb_bn': nb_bn, 'nb_vc': nb_vc, 'nb_dl': nb_dl,
                'tk_bn': tk_bn, 'tk_vc': tk_vc, 'tk_dl': tk_dl,
                'ls_bn': ls_bn, 'ls_vc': ls_vc, 'ls_dl': ls_dl,
                'gr_bn': gr_bn, 'gr_dl': gr_dl,
                'pr_bn': pr_bn, 'pr_vc': pr_vc, 'pr_dl': pr_dl,
                'mev': mv_dl, 'charon': ch_dl,
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

    def test_geth_lighthouse_routing(self):
        mocks = self._run("Solo Staking Node", "Geth", "Lighthouse")
        self._verify_only_called(mocks, ['geth', 'lh_dl', 'lh_bn', 'lh_vc', 'mev'])

    def test_validator_only_skips_ec_and_bn(self):
        mocks = self._run("Validator Client Only", None, None, "Teku")
        # For VC-only, only VC download and VC install should happen
        self._verify_only_called(mocks, ['tk_dl', 'tk_vc'])

    def test_validator_only_builder_api_without_local_mev(self):
        """Remote BN MEV: enable VC builder flags without installing mevboost."""
        mocks = self._run(
            "Validator Client Only",
            None,
            None,
            "Lodestar",
            flags_override={"builder_api": True, "mevboost": False},
        )
        self._verify_only_called(mocks, ['ls_dl', 'ls_vc'])
        extra_params = mocks['ls_vc'].call_args.args[6]
        assert "--builder" in extra_params

    def test_validator_only_charon_builder_api_without_local_mev(self):
        mocks = self._run(
            "Validator Client Only",
            None,
            None,
            "Lodestar",
            flags_override={"builder_api": True, "mevboost": False, "charon": True},
        )
        self._verify_only_called(mocks, ['ls_dl', 'ls_vc', 'charon'])
        assert mocks['charon'].call_args.kwargs.get("builder_api") is True
        assert mocks['mev'].call_count == 0

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

    def test_custom_ec_bn_mev_no_vc(self):
        # Custom setup: Geth EC + Teku BN + MEV (No VC)
        mocks = self._run("Custom Setup", "Geth", "Teku", None, flags_override={"validator": False, "mevboost": True})
        self._verify_only_called(mocks, ['geth', 'tk_dl', 'tk_bn', 'mev'])

    def test_switch_execution_client_only(self):
        # When switching EC, cc_name and vc_name are None, and validator/mevboost flags are False
        mocks = self._run("Switch Execution Client", "Nethermind", None, None, flags_override={"validator": False, "mevboost": False})
        self._verify_only_called(mocks, ['nethermind'])

    def test_switch_consensus_client_only(self):
        # When switching CC, ec_name and vc_name are None, and validator/mevboost flags are False
        mocks = self._run("Switch Consensus Client", None, "Lighthouse", None, flags_override={"validator": False, "mevboost": False})
        self._verify_only_called(mocks, ['lh_dl', 'lh_bn'])
        # Verify NO MEV params are passed to the BN installation
        call_kwargs = mocks['lh_bn'].call_args
        mev_params = call_kwargs.kwargs.get('mev_parameters', '')
        assert mev_params == '', f"Expected no MEV params when mevboost=False, got: {mev_params}"

    def test_switch_consensus_client_with_mevboost(self):
        # When switching CC with mevboost enabled, mevboost.service is preserved
        # and should NOT be reconfigured. The new CC service file MUST include MEV params.
        mocks = self._run("Switch Consensus Client", None, "Lighthouse", None, flags_override={"validator": False, "mevboost": True, "switch_client": "consensus"})
        # Verify only BN is installed (no VC, no mevboost reinstall)
        self._verify_only_called(mocks, ['lh_dl', 'lh_bn'])
        # Verify MEV params are passed to the BN installation
        mev_url = 'http://127.0.0.1:18550'
        mev_params_expected = f'--builder {mev_url}'
        mocks['lh_bn'].assert_called_once()
        call_kwargs = mocks['lh_bn'].call_args
        assert mev_params_expected in call_kwargs.kwargs.get('mev_parameters', ''), \
            f"Expected MEV params '{mev_params_expected}' in lighthouse install, got: {call_kwargs}"

    def test_switch_consensus_client_nimbus_with_mevboost(self):
        # Verify Nimbus gets MEV params when switching with mevboost enabled
        mocks = self._run("Switch Consensus Client", None, "Nimbus", None, flags_override={"validator": False, "mevboost": True, "switch_client": "consensus"})
        self._verify_only_called(mocks, ['nb_dl', 'nb_bn'])
        mev_url = 'http://127.0.0.1:18550'
        mev_params_expected = f'--payload-builder=true --payload-builder-url={mev_url}'
        call_kwargs = mocks['nb_bn'].call_args
        assert mev_params_expected in call_kwargs.kwargs.get('mev_parameters', ''), \
            f"Expected MEV params '{mev_params_expected}' in nimbus install, got: {call_kwargs}"

    def test_switch_consensus_client_nimbus_passes_checkpoint_sync_url(self):
        # EthPillar passes no checkpoint URL flag to the Nimbus unit; the URL must reach
        # install_nimbus_bn so it can run trustedNodeSync instead of syncing from genesis.
        sync_url = 'https://mainnet.checkpoint.sigp.io'
        mocks = self._run("Switch Consensus Client", None, "Nimbus", None,
                          flags_override={"validator": False, "switch_client": "consensus"},
                          params_override={"sync_url": sync_url})
        assert mocks['nb_bn'].call_args.kwargs.get('sync_url') == sync_url

    def test_switch_consensus_client_teku_with_mevboost(self):
        # Verify Teku gets MEV params when switching with mevboost enabled
        mocks = self._run("Switch Consensus Client", None, "Teku", None, flags_override={"validator": False, "mevboost": True, "switch_client": "consensus"})
        self._verify_only_called(mocks, ['tk_dl', 'tk_bn'])
        mev_url = 'http://127.0.0.1:18550'
        mev_params_expected = f'--validators-builder-registration-default-enabled=true --builder-endpoint={mev_url}'
        call_kwargs = mocks['tk_bn'].call_args
        assert mev_params_expected in call_kwargs.kwargs.get('mev_parameters', ''), \
            f"Expected MEV params '{mev_params_expected}' in teku install, got: {call_kwargs}"

    def test_switch_consensus_client_grandine_with_mevboost(self):
        # Verify Grandine gets MEV params when switching with mevboost enabled
        mocks = self._run("Switch Consensus Client", None, "Grandine", None, flags_override={"validator": False, "mevboost": True, "switch_client": "consensus"})
        self._verify_only_called(mocks, ['gr_dl', 'gr_bn'])
        mev_url = 'http://127.0.0.1:18550'
        mev_params_expected = f'--builder-url={mev_url}'
        call_kwargs = mocks['gr_bn'].call_args
        assert mev_params_expected in call_kwargs.kwargs.get('mev_parameters', ''), \
            f"Expected MEV params '{mev_params_expected}' in grandine install, got: {call_kwargs}"

    def test_switch_consensus_client_prysm_with_mevboost(self):
        # Verify Prysm gets MEV params when switching with mevboost enabled
        mocks = self._run("Switch Consensus Client", None, "Prysm", None, flags_override={"validator": False, "mevboost": True, "switch_client": "consensus"})
        self._verify_only_called(mocks, ['pr_dl', 'pr_bn'])
        mev_url = 'http://127.0.0.1:18550'
        mev_params_expected = f'--http-mev-relay={mev_url}'
        call_kwargs = mocks['pr_bn'].call_args
        assert mev_params_expected in call_kwargs.kwargs.get('mev_parameters', ''), \
            f"Expected MEV params '{mev_params_expected}' in prysm install, got: {call_kwargs}"

    def test_custom_grandine_routing(self):
        mocks = self._run("Custom Setup", "Besu", "Grandine", "Lighthouse", flags_override={"validator": True, "mevboost": True})
        self._verify_only_called(mocks, ['besu', 'gr_dl', 'gr_bn', 'lh_dl', 'lh_vc', 'mev'])

    def test_custom_grandine_integrated_routing(self):
        mocks = self._run("Custom Setup", "Besu", "Grandine", "Grandine (integrated)", flags_override={"validator": True, "mevboost": True})
        # Grandine integrated skips VC setup completely and appends flags to BN
        self._verify_only_called(mocks, ['besu', 'gr_dl', 'gr_bn', 'mev'])

    def test_custom_charon_lodestar_routing(self):
        mocks = self._run(
            "Custom Setup", "Geth", "Lighthouse", "Lodestar",
            flags_override={"validator": True, "mevboost": True, "charon": True},
        )
        self._verify_only_called(mocks, ['geth', 'lh_dl', 'lh_bn', 'ls_dl', 'ls_vc', 'mev', 'charon'])
        bn_arg = mocks['ls_vc'].call_args.args[4]
        assert "3600" in bn_arg
        extra_params = mocks['ls_vc'].call_args.args[6]
        assert "--distributed" in extra_params
        assert "slotSkip" not in extra_params
        assert mocks['charon'].call_args.kwargs.get("builder_api") is True

    def test_charon_nimbus_bn_enables_json_requests(self):
        mocks = self._run(
            "Custom Setup", "Geth", "Nimbus", "Nimbus",
            flags_override={"validator": True, "charon": True},
        )
        assert mocks['charon'].call_args.kwargs.get("feature_set_enable") == "json_requests"

    def test_charon_teku_bn_disables_graffiti_append(self):
        mocks = self._run(
            "Custom Setup", "Geth", "Teku", "Teku",
            flags_override={"validator": True, "charon": True},
        )
        fee_params = mocks['tk_bn'].call_args.kwargs.get('fee_parameters', '')
        assert "--validators-graffiti-client-append-format=DISABLED" in fee_params
        extra_params = mocks['tk_vc'].call_args.args[6]
        assert "--Xobol-dvt-integration-enabled=true" in extra_params
        assert "--Xvalidator-client-beacon-api-executor-threads=50" in extra_params

    def test_charon_rejects_grandine_integrated(self):
        with pytest.raises(ValueError, match="incompatible with Grandine"):
            self._run(
                "Custom Setup", "Besu", "Grandine", "Grandine (integrated)",
                flags_override={"validator": True, "charon": True},
            )
