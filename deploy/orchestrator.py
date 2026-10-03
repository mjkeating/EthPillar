import os
import sys
from typing import Dict, List, Optional, Tuple
import deploy.besu as besu
import deploy.nethermind as nethermind
import deploy.reth as reth
import deploy.erigon as erigon
import deploy.geth as geth
import deploy.ethrex as ethrex
import deploy.lighthouse as lighthouse
import deploy.nimbus as nimbus
import deploy.teku as teku
import deploy.lodestar as lodestar
import deploy.grandine as grandine
import deploy.mevboost as mevboost
import deploy.charon as charon
import deploy.prysm as prysm

CHARON_VC_LABEL = "Obol Charon DV"
OBOL_CHARON = "Obol Charon"
OBOL_IMPORT_KEY_SHARES = "Import Obol Charon key shares"
OBOL_GREEN = "\033[32m"
OBOL_RESET = "\033[0m"


def obol_mark() -> str:
    """Green ∞ only (ANSI), for terminal output."""
    return f"{OBOL_GREEN}∞{OBOL_RESET}"

# Charon v1.11+ compatibility matrix: Lodestar BN + these VCs may fail duties (client bug, not Charon).
LODESTAR_BN_INCOMPATIBLE_VCS = frozenset({"Lighthouse", "Nimbus", "Prysm"})

VALID_ROLES = [
    'Solo Staking Node',
    'Full Node Only',
    'Lido CSM Staking Node',
    'Lido CSM Validator Client Only',
    'Validator Client Only',
    'Failover Staking Node',
    'Custom Setup'
]

EXECUTION_CLIENTS = ['Besu', 'Nethermind', 'Reth', 'Erigon', 'Geth', 'Ethrex']
CONSENSUS_CLIENTS = ['Lighthouse', 'Nimbus', 'Teku', 'Lodestar', 'Grandine', 'Prysm']

PREDEFINED_COMBOS = {
    'Nimbus-Nethermind': ('Nethermind', 'Nimbus'),
    'Lodestar-Besu':    ('Besu', 'Lodestar'),
    'Teku-Besu':        ('Besu', 'Teku'),
    'Lighthouse-Reth':  ('Reth', 'Lighthouse'),
    'Caplin-Erigon':    ('Erigon', 'Caplin'),
}

def resolve_role_flags(role: str, network: str) -> Dict[str, bool]:
    """Pure function: resolve role and network to capability flags."""
    flags = {
        "mevboost": False,
        "builder_api": False,
        "validator": False,
        "validator_only": False,
        "node_only": False
    }

    if role == "Solo Staking Node" or role == "Lido CSM Staking Node":
        flags["mevboost"] = True
        flags["builder_api"] = True
        flags["validator"] = True
    elif role == "Full Node Only":
        flags["node_only"] = True
    elif role == "Validator Client Only" or role == "Lido CSM Validator Client Only":
        flags["mevboost"] = False
        flags["validator"] = True
        flags["validator_only"] = True
    elif role == "Failover Staking Node":
        flags["mevboost"] = True
        flags["builder_api"] = True

    return flags

def apply_csm_overrides(role: str, network: str, env_vars: Dict[str, str], current_fee_recipient: str, current_graffiti: str) -> Tuple[str, str, str]:
    """Pure function: apply CSM overrides for fee recipient and graffiti."""
    fee_recipient = current_fee_recipient
    graffiti = current_graffiti
    mev_min_bid = env_vars.get('MEV_MIN_BID', '')

    if role in ["Lido CSM Staking Node", "Lido CSM Validator Client Only"]:
        graffiti = env_vars.get('CSM_GRAFFITI', graffiti)
        mev_min_bid = env_vars.get('CSM_MEV_MIN_BID', mev_min_bid)
        
        if network == "mainnet":
            fee_recipient = env_vars.get('CSM_FEE_RECIPIENT_ADDRESS_MAINNET', fee_recipient)
        elif network == "holesky":
            fee_recipient = env_vars.get('CSM_FEE_RECIPIENT_ADDRESS_HOLESKY', fee_recipient)
        elif network == "hoodi":
            fee_recipient = env_vars.get('CSM_FEE_RECIPIENT_ADDRESS_HOODI', fee_recipient)

    return fee_recipient, graffiti, mev_min_bid

def get_combo_menu() -> List[str]:
    return list(PREDEFINED_COMBOS.keys())

def get_vc_menu(*, include_charon: bool = True) -> List[str]:
    """Standalone VC menu (VC-only install, or Charon signer list).

    Grandine is excluded: it has no standalone VC. Charon is listed as a VC
    choice for UX; selecting it requires a second prompt for the real signer.
    """
    choices = [c for c in CONSENSUS_CLIENTS if c != "Grandine"]
    if include_charon:
        choices.append(CHARON_VC_LABEL)
    return choices

def get_ec_menu() -> List[str]:
    return EXECUTION_CLIENTS.copy()

def get_cc_menu(ec_name: str) -> List[str]:
    choices = CONSENSUS_CLIENTS.copy()
    if ec_name == 'Erigon':
        choices.append('Caplin (integrated)')
    return choices

def get_vc_options_for_cc(
    cc_name: str,
    *,
    include_charon: bool = True,
    for_charon_signer: bool = False,
) -> List[str]:
    """VC menu after a consensus client has been chosen.

    ``for_charon_signer`` is the second prompt after ``Obol Charon DV``:
    Charon and Grandine (integrated) are omitted.
    """
    if for_charon_signer:
        if cc_name in ("Caplin", "Caplin (integrated)", "Grandine"):
            return get_vc_menu(include_charon=False)
        return ["Same as CC"] + get_vc_menu(include_charon=False)

    if cc_name == "Caplin" or cc_name == "Caplin (integrated)":
        return get_vc_menu(include_charon=include_charon)

    if cc_name == "Grandine":
        # Integrated Grandine cannot sit behind Charon middleware.
        if include_charon:
            return ["Grandine (integrated)"] + get_vc_menu(include_charon=True)
        return ["Grandine (integrated)"] + get_vc_menu(include_charon=False)

    if include_charon:
        return ["Same as CC"] + get_vc_menu(include_charon=True)
    return ["Same as CC"] + get_vc_menu(include_charon=False)

def is_charon_vc_choice(vc_choice: Optional[str]) -> bool:
    """Return True when the menu/CLI choice is the Charon middleware option."""
    return (vc_choice or "") == CHARON_VC_LABEL

def resolve_vc_name(cc_name: str, vc_choice: str) -> str:
    if vc_choice == "Same as CC":
        return cc_name
    return vc_choice

def lodestar_bn_vc_incompatibility_message(
    cc_name: Optional[str],
    vc_name: Optional[str],
) -> Optional[str]:
    """Return a warning when Lodestar BN is paired with a VC Obol marks as 🟠 in Charon v1.11+."""
    if cc_name != "Lodestar" or not vc_name:
        return None
    if vc_name in LODESTAR_BN_INCOMPATIBLE_VCS:
        return (
            f"Lodestar beacon node + {vc_name} validator client is a known incompatible "
            f"combination (Charon v1.11+ matrix: duties may fail). This is a client-side "
            f"issue, not Charon. Prefer Lodestar or Teku VC, or use a different BN "
            f"(Lighthouse, Nimbus, Prysm, Teku, Grandine) until Lodestar ships a fix."
        )
    return None


def _with_dvt_params(extra_params: str, vc_name: Optional[str], charon_enabled: bool) -> str:
    """Append Charon/DVT VC flags when Charon is enabled.

    Per Obol client configuration + CDVN:
      - Lighthouse / Nimbus / Prysm: ``--distributed``
      - Lodestar: ``--distributed`` (disables slot skip by default in Lodestar v1.37+)
      - Teku: ``--Xobol-dvt-integration-enabled=true`` and
        ``--Xvalidator-client-beacon-api-executor-threads=50`` (CDVN PR #480;
        default pool is too small for Charon API load)

    ``extra_params`` holds optional VC ExecStart flags (builder/MEV and/or DVT).
    """
    if not charon_enabled or not vc_name:
        return extra_params
    dvt_by_vc = {
        "Lighthouse": "--distributed",
        "Lodestar": "--distributed",
        "Nimbus": "--distributed",
        "Prysm": "--distributed",
        "Teku": (
            "--Xobol-dvt-integration-enabled=true "
            "--Xvalidator-client-beacon-api-executor-threads=50"
        ),
    }
    dvt = dvt_by_vc.get(vc_name)
    if not dvt:
        return extra_params
    return f"{extra_params} {dvt}".strip() if extra_params else dvt


def _int_param(params: Dict[str, str], key: str, default: int = 0) -> int:
    """Read an integer parameter, treating unset or blank env values as default."""
    value = params.get(key, default)
    if value is None or value == "":
        return default
    return int(value)

def is_valid_combination(ec: str, cc: str) -> bool:
    if ec == 'Erigon' and cc == 'Caplin':
        return True
    if ec == 'Erigon' and cc in CONSENSUS_CLIENTS:
        return True # Erigon standalone
    if ec in ['Besu', 'Nethermind', 'Reth', 'Geth', 'Ethrex'] and cc in CONSENSUS_CLIENTS:
        return True
    return False

def run_install(role: str, network: str, ec_name: Optional[str], cc_name: Optional[str], vc_name: Optional[str], flags: Dict[str, bool], params: Dict[str, str], env_vars: Dict[str, str]):
    """Orchestrate the installation by calling the appropriate deploy modules."""
    import deploy.common as common

    fee_recipient = params.get('fee_recipient', '')
    graffiti = params.get('graffiti', '')
    bn_address = params.get('bn_address', '')
    jwtsecret_path = params.get('jwtsecret_path', '')
    sync_url = params.get('sync_url', '')
    el_p2p_port = _int_param(params, 'el_p2p_port')
    el_p2p_port_2 = _int_param(params, 'el_p2p_port_2')
    el_rpc_port = _int_param(params, 'el_rpc_port')
    el_max_peers = _int_param(params, 'el_max_peers')
    cl_p2p_port = _int_param(params, 'cl_p2p_port')
    cl_p2p_port_2 = _int_param(params, 'cl_p2p_port_2')
    cl_rest_port = _int_param(params, 'cl_rest_port')
    cl_max_peers = _int_param(params, 'cl_max_peers')
    mev_min_bid = params.get('mev_min_bid', '')
    skip_prompts = params.get('skip_prompts', 'false').lower() == 'true'

    bn_vc_warn = lodestar_bn_vc_incompatibility_message(cc_name, vc_name)
    if bn_vc_warn:
        print(f"WARNING: {bn_vc_warn}", file=sys.stderr)

    fee_recipient, graffiti, mev_min_bid = apply_csm_overrides(role, network, env_vars, fee_recipient, graffiti)

    common.setup_node(jwtsecret_path, flags['validator_only'])

    if network == "ephemery":
        common.setup_ephemery_network("ephemery-testnet/ephemery-genesis")

    mev_ver, mev_path = "", ""
    if flags['mevboost'] and not flags['validator_only'] and not flags.get('switch_client'):
        # Need to load config properly or pass it
        import config
        relay_options = getattr(config, f"{network}_relay_options", [])
        mev_ver, mev_path = mevboost.install_mevboost(network, mev_min_bid, relay_options)

    el_ver, el_path = "", ""
    if not flags['validator_only'] and ec_name:
        if ec_name == 'Besu':
            el_ver, el_path = besu.download_and_install_besu(network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path)
        elif ec_name == 'Nethermind':
            import config
            sync_params = getattr(config, f"{network}_nethermind_sync_parameters", '')
            el_ver, el_path = nethermind.download_and_install_nethermind(network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path, sync_parameters=sync_params)
        elif ec_name == 'Reth':
            el_ver, el_path = reth.download_and_install_reth(network, el_p2p_port, el_p2p_port_2, el_rpc_port, el_max_peers, jwtsecret_path)
        elif ec_name == 'Erigon':
            if cc_name == 'Caplin' or cc_name == 'Caplin (integrated)':
                mev_params = f'--caplin.mev-relay-url=http://127.0.0.1:18550' if flags['mevboost'] else ''
                # eth-docker #2836: Caplin QUIC is CL_P2P+1 (9001), not native UDP 4001.
                cl_quic_port = str(cl_p2p_port_2) if cl_p2p_port_2 else '9001'
                el_ver, el_path = erigon.download_and_install_erigon(
                    network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path,
                    cl_p2p_port, cl_rest_port, cl_max_peers, sync_url,
                    mev_parameters=mev_params, cl_quic_port=cl_quic_port,
                )
            else:
                el_ver, el_path = erigon.download_and_install_erigon_standalone(
                    network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path
                )
        elif ec_name == 'Geth':
            el_ver, el_path = geth.download_and_install_geth(network, str(el_p2p_port), str(el_rpc_port), str(el_max_peers), jwtsecret_path)
        elif ec_name == 'Ethrex':
            el_ver, el_path = ethrex.download_and_install_ethrex(network, str(el_p2p_port), str(el_rpc_port), str(el_max_peers), jwtsecret_path)

    cl_ver, cl_path = "", ""
    if not flags['validator_only'] and cc_name and cc_name not in ['Caplin', 'Caplin (integrated)']:
        if cc_name == 'Lighthouse':
            mev_params = f'--builder http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = lighthouse.download_lighthouse(network)
            cl_path = lighthouse.install_lighthouse_bn(network, sync_url, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peers, mev_parameters=mev_params)
        elif cc_name == 'Nimbus':
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            mev_params = '--payload-builder=true --payload-builder-url=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = nimbus.download_nimbus(network)
            cl_path = nimbus.install_nimbus_bn(network, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params, sync_url=sync_url)
        elif cc_name == 'Teku':
            fee_params = f'--validators-proposer-default-fee-recipient={fee_recipient}'
            if flags.get('charon'):
                fee_params = (
                    f'{fee_params} --validators-graffiti-client-append-format=DISABLED'
                )
            mev_params = '--validators-builder-registration-default-enabled=true --builder-endpoint=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = teku.download_teku(network)
            cl_path = teku.install_teku_bn(network, sync_url, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params)
        elif cc_name == 'Lodestar':
            fee_params = f'--suggestedFeeRecipient={fee_recipient}'
            mev_params = '--builder --builder.urls http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = lodestar.download_lodestar(network)
            cl_path = lodestar.install_lodestar_bn(network, sync_url, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params)
        elif cc_name == 'Grandine':
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            mev_params = '--builder-url=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = grandine.download_grandine(network)
            is_integrated_vc = (vc_name == 'Grandine (integrated)' and flags['validator'])
            cl_path = grandine.install_grandine_bn(network, sync_url, jwtsecret_path, str(cl_rest_port), str(cl_p2p_port), str(cl_p2p_port_2), str(cl_max_peers), fee_parameters=fee_params, mev_parameters=mev_params, is_integrated_vc=is_integrated_vc)
        elif cc_name == 'Prysm':
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            mev_params = '--http-mev-relay=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = prysm.download_prysm(network)
            cl_path = prysm.install_prysm_bn(network, sync_url, jwtsecret_path, str(cl_rest_port), str(cl_p2p_port), str(cl_p2p_port_2), str(cl_max_peers), fee_parameters=fee_params, mev_parameters=mev_params)

    charon_enabled = bool(flags.get('charon'))
    if charon_enabled and vc_name == "Grandine (integrated)":
        raise ValueError("Obol Charon is incompatible with Grandine (integrated). Select a standalone VC.")
    if charon_enabled and vc_name == CHARON_VC_LABEL:
        raise ValueError("Obol Charon DV requires a signer validator client (e.g. Lodestar).")

    charon_ver, charon_path = "", ""
    cl_ip = env_vars.get('CL_IP_ADDRESS', '127.0.0.1')
    local_bn_addr = (
        f"http://{cl_ip}:{cl_rest_port}"
        if cc_name != "Caplin" and cc_name != "Caplin (integrated)"
        else f"http://127.0.0.1:{cl_rest_port}"
    )
    upstream_bn = bn_address if flags['validator_only'] else local_bn_addr

    if charon_enabled and not flags.get('switch_client'):
        charon_api_port = env_vars.get('CHARON_VALIDATOR_API_PORT', '3600')
        charon_p2p_port = env_vars.get('CHARON_P2P_PORT', '3610')
        charon_mon_port = env_vars.get('CHARON_MONITORING_PORT', '3620')
        p2p_external_ip = env_vars.get('CHARON_P2P_EXTERNAL_IP', '')
        charon_ver, charon_path = charon.install_charon(
            network,
            upstream_bn,
            builder_api=bool(flags.get('builder_api') or flags['mevboost']),
            p2p_external_ip=p2p_external_ip,
            validator_api_address=f"127.0.0.1:{charon_api_port}",
            monitoring_address=f"127.0.0.1:{charon_mon_port}",
            p2p_tcp_address=f"0.0.0.0:{charon_p2p_port}",
            # Obol: Charon needs JSON beacon APIs when talking to Nimbus BN.
            feature_set_enable="json_requests" if cc_name == "Nimbus" else "",
        )

    val_path = ""
    val_ver = ""
    use_builder = bool(flags.get('builder_api') or flags.get('mevboost'))
    if flags['validator'] and vc_name:
        addr = (
            charon.charon_validator_api_url(
                port=env_vars.get('CHARON_VALIDATOR_API_PORT', '3600')
            )
            if charon_enabled
            else upstream_bn
        )
        # Same-host ordering: start Charon before the VC talks to :3600.
        vc_unit_after = ["charon.service"] if charon_enabled else None

        if vc_name == 'Lighthouse':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else lighthouse.download_lighthouse(network)
            val_ver = v_ver
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            extra_params = _with_dvt_params(
                '--builder-proposals' if use_builder else '',
                vc_name,
                charon_enabled,
            )
            bn_arg = f'--beacon-nodes={addr}'
            val_path = lighthouse.install_lighthouse_vc(
                v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )
        elif vc_name == 'Nimbus':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else nimbus.download_nimbus(network)
            val_ver = v_ver
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            extra_params = _with_dvt_params(
                '--payload-builder=true' if use_builder else '',
                vc_name,
                charon_enabled,
            )
            bn_arg = f'--beacon-node={addr}'
            val_path = nimbus.install_nimbus_vc(
                v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )
        elif vc_name == 'Teku':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else teku.download_teku(network)
            val_ver = v_ver
            fee_params = f'--validators-proposer-default-fee-recipient={fee_recipient}'
            extra_params = _with_dvt_params(
                '--validators-builder-registration-default-enabled=true' if use_builder else '',
                vc_name,
                charon_enabled,
            )
            bn_arg = f'--beacon-node-api-endpoint={addr}'
            val_path = teku.install_teku_vc(
                v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )
        elif vc_name == 'Lodestar':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else lodestar.download_lodestar(network)
            val_ver = v_ver
            fee_params = f'--suggestedFeeRecipient={fee_recipient}'
            extra_params = _with_dvt_params(
                '--builder' if use_builder else '',
                vc_name,
                charon_enabled,
            )
            bn_arg = f'--beaconNodes={addr}'
            val_path = lodestar.install_lodestar_vc(
                v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )
        elif vc_name == 'Prysm':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else prysm.download_prysm(network)
            val_ver = v_ver
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            extra_params = _with_dvt_params(
                '--enable-builder' if use_builder else '',
                vc_name,
                charon_enabled,
            )
            bn_arg = f'--beacon-rest-api-provider={addr}'
            # gRPC beacon endpoint only when the local BN is also Prysm and Charon is off.
            beacon_rpc = "127.0.0.1:4000" if cc_name == "Prysm" and not charon_enabled else None
            val_path = prysm.install_prysm_vc(
                v_ver,
                network,
                str(cl_rest_port),
                graffiti,
                bn_arg,
                fee_params,
                extra_params,
                beacon_rpc_provider=beacon_rpc,
                unit_after=vc_unit_after,
            )

    combo_name = role
    if ec_name and cc_name:
        combo_name = f"{ec_name}-{cc_name}"
    elif vc_name:
        combo_name = vc_name

    ec_name_display = ec_name.lower() if ec_name else ""
    if ec_name == 'Erigon' and (cc_name == 'Caplin' or cc_name == 'Caplin (integrated)'):
        ec_name_display = "erigon-caplin"

    cc_name_display = cc_name.lower() if cc_name and cc_name not in ['Caplin', 'Caplin (integrated)'] else ""
    if vc_name and vc_name != cc_name:
        # If they differ, we'll let the finish_install handle the dual reporting or just use the VC name for the CC slot if CC is empty
        if not cc_name_display:
            cc_name_display = vc_name.lower()
            cl_ver = val_ver

    common.finish_install(
        role, network, sync_url,
        ec_name_display, el_ver, el_path,
        cc_name_display, cl_ver, cl_path,
        flags['mevboost'], mev_ver, mev_path,
        flags['validator'], val_path,
        flags['validator_only'], bn_address, flags['node_only'], fee_recipient,
        skip_prompts=skip_prompts,
        cl_rest_port=str(cl_rest_port),
        vc_name=vc_name, vc_ver=val_ver,
        charon_enabled=charon_enabled,
        charon_version=charon_ver,
        charon_service_path=charon_path,
    )
