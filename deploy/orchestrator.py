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
import deploy.web3signer as web3signer
import deploy.ssv as ssv
import deploy.prysm as prysm
from deploy.common import ephemery_network_override

CHARON_VC_LABEL = "Obol Charon DV"
WEB3SIGNER_VC_LABEL = "Web3Signer"
SSV_VC_LABEL = "SSV Operator"
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

def resolve_role_flags(
    role: str,
    network: str,
    *,
    vc_name: Optional[str] = None,
    cc_name: Optional[str] = None,
    charon: bool = False,
) -> Dict[str, bool]:
    """Resolve a preset role to capability flags, including builder mode.

    Solo and Lido CSM staking nodes use MEV-Boost on networks that are not
    ePBS-enabled yet. On an enabled network they use native ePBS when
    *vc_name* (or integrated Caplin) supports it, and MEV-Boost when it does
    not (Grandine, Charon). Failover has no validator client, so it stays on
    MEV-Boost. CSM validator-only keeps MEV mandatory without a local
    sidecar. Pass *vc_name* once the client is known; without it, a forked
    network stays on MEV-Boost because support cannot be checked.

    Args:
        role: Install role label.
        network: Network slug.
        vc_name: Validator client, when already chosen.
        cc_name: Consensus client, when already chosen.
        charon: True when Obol Charon is part of the install.

    Returns:
        Flag dict. ``epbs`` and ``mevboost`` are mutually exclusive.
        ``builder_api`` is true whenever either builder path is on.
    """
    from deploy.builder_mode import BUILDER_EPBS, BUILDER_MEVBOOST, default_builder_mode

    flags = {
        "mevboost": False,
        "epbs": False,
        "builder_api": False,
        "validator": False,
        "validator_only": False,
        "node_only": False,
    }

    if role == "Solo Staking Node" or role == "Lido CSM Staking Node":
        flags["validator"] = True
    elif role == "Full Node Only":
        flags["node_only"] = True
    elif role == "Validator Client Only" or role == "Lido CSM Validator Client Only":
        flags["validator"] = True
        flags["validator_only"] = True
    elif role == "Failover Staking Node":
        pass

    mode = default_builder_mode(
        role, network, vc_name, cc_name, charon=charon
    )
    if mode == BUILDER_EPBS:
        flags["epbs"] = True
        flags["builder_api"] = True
    elif mode == BUILDER_MEVBOOST:
        if flags["validator_only"]:
            # CSM validator-only: MEV is mandatory, but the sidecar stays on
            # the remote beacon node.
            flags["builder_api"] = True
        else:
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

def get_vc_menu(
    *,
    include_charon: bool = True,
    include_web3signer: bool = False,
    include_ssv: bool = False,
) -> List[str]:
    """Standalone VC menu (VC-only install, or a coupled-signer list).

    Grandine is excluded: it has no standalone VC. Charon and Web3Signer are
    listed as VC choices for UX; selecting either requires a second prompt
    for the real validator client. SSV is a duty-client alternative (no second
    VC). Pass ``include_web3signer`` / ``include_ssv`` on menus that already
    list Charon. Never pass ``include_ssv`` on Charon/Web3Signer second prompts.
    """
    choices = [c for c in CONSENSUS_CLIENTS if c != "Grandine"]
    if include_charon:
        choices.append(CHARON_VC_LABEL)
    if include_web3signer:
        choices.append(WEB3SIGNER_VC_LABEL)
    if include_ssv:
        choices.append(SSV_VC_LABEL)
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
    include_web3signer: bool = False,
    include_ssv: bool = False,
    for_charon_signer: bool = False,
    for_web3signer_vc: bool = False,
) -> List[str]:
    """VC menu after a consensus client has been chosen.

    ``for_charon_signer`` is the second prompt after ``Obol Charon DV``:
    Charon, Web3Signer, SSV, and Grandine (integrated) are omitted.

    ``for_web3signer_vc`` is the second prompt after ``Web3Signer``:
    Charon, Web3Signer, and SSV are omitted. Grandine (integrated) stays available.
    """
    if for_charon_signer or for_web3signer_vc:
        include_charon = False
        include_web3signer = False
        include_ssv = False
    if for_charon_signer:
        if cc_name in ("Caplin", "Caplin (integrated)", "Grandine"):
            return get_vc_menu(
                include_charon=False, include_web3signer=False, include_ssv=False
            )
        return ["Same as CC"] + get_vc_menu(
            include_charon=False, include_web3signer=False, include_ssv=False
        )

    if cc_name == "Caplin" or cc_name == "Caplin (integrated)":
        return get_vc_menu(
            include_charon=include_charon,
            include_web3signer=include_web3signer,
            include_ssv=include_ssv,
        )

    if cc_name == "Grandine":
        # Integrated Grandine cannot sit behind Charon middleware.
        return ["Grandine (integrated)"] + get_vc_menu(
            include_charon=include_charon,
            include_web3signer=include_web3signer,
            include_ssv=include_ssv,
        )

    return ["Same as CC"] + get_vc_menu(
        include_charon=include_charon,
        include_web3signer=include_web3signer,
        include_ssv=include_ssv,
    )

def is_charon_vc_choice(vc_choice: Optional[str]) -> bool:
    """Return True when the menu/CLI choice is the Charon middleware option."""
    return (vc_choice or "") == CHARON_VC_LABEL

def is_web3signer_vc_choice(vc_choice: Optional[str]) -> bool:
    """Return True when the menu/CLI choice is the Web3Signer option."""
    return (vc_choice or "") == WEB3SIGNER_VC_LABEL

def is_ssv_vc_choice(vc_choice: Optional[str]) -> bool:
    """Return True when the menu/CLI choice is the SSV operator option."""
    return (vc_choice or "") == SSV_VC_LABEL

def get_predefined_vc_mode_options() -> List[str]:
    """Validator mode on Solo, CSM, and Failover installs.

    Same order as the other VC menus: the consensus client, then Charon,
    Web3Signer, then SSV Operator.
    """
    return ["Same as CC", CHARON_VC_LABEL, WEB3SIGNER_VC_LABEL, SSV_VC_LABEL]

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

def _with_signer_params(extra_params: str, vc_name: Optional[str], web3signer_enabled: bool) -> str:
    """Append Web3Signer remote-signer flags for a standalone VC."""
    if not web3signer_enabled or not vc_name:
        return extra_params
    signer = web3signer.signer_flags_for_vc(vc_name)
    if not signer:
        return extra_params
    return f"{extra_params} {signer}".strip() if extra_params else signer


def apply_install_epbs(network: str, relays: List[dict], min_bid: str, fs=None):
    """Write native ePBS relays onto the VC that ``run_install`` just created.

    No ``mevboost.service`` and no beacon-node sidecar. ``min_bid`` is the
    install ``MEV_MIN_BID`` (ETH), forwarded as :class:`manage.epbs.RelaysConfig`.

    Args:
        network: Network slug stored on the relay config.
        relays: Selected relay dicts (``url``) or raw URL strings.
        min_bid: ETH min-bid from the install environment.
        fs: Optional ePBS filesystem (tests). Production reads systemd units.

    Returns:
        The applied migration plan.

    Raises:
        manage.epbs.EpbsError: The client, binary, or relay list cannot be used.
    """
    from manage.epbs import EpbsError, RelaysConfig, apply_fresh_install_relays

    urls: List[str] = []
    for item in relays or []:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
        else:
            url = str(item).strip()
        if url:
            urls.append(url)
    if not urls:
        raise EpbsError(
            f"Native ePBS on {network} needs at least one relay URL. "
            "Refusing to install with an empty builder list."
        )
    return apply_fresh_install_relays(
        RelaysConfig(urls=urls, min_bid=(min_bid or "").strip(), network=network),
        fs=fs,
    )


def run_install(role: str, network: str, ec_name: Optional[str], cc_name: Optional[str], vc_name: Optional[str], flags: Dict[str, bool], params: Dict[str, str], env_vars: Dict[str, str]):
    """Orchestrate the installation by calling the appropriate deploy modules."""
    import deploy.common as common

    if flags.get("charon") and flags.get("web3signer"):
        raise ValueError(
            "Web3Signer and Obol Charon cannot be combined. "
            "Install one signing mode."
        )
    if flags.get("ssv") and flags.get("charon"):
        raise ValueError(
            "SSV Operator and Obol Charon cannot be combined. "
            "Install one DVT mode."
        )
    if flags.get("ssv") and flags.get("web3signer"):
        raise ValueError(
            "SSV Operator and Web3Signer cannot be combined in this install path."
        )
    if flags.get("ssv") and flags.get("validator"):
        # SSV replaces the traditional VC; keep the flag false so finish_install
        # does not create/start validator.service.
        flags["validator"] = False

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

    el_override = ephemery_network_override(ec_name or "", "el") if network == "ephemery" else None
    cl_override = ephemery_network_override(cc_name or "", "bn") if network == "ephemery" else None
    vc_override = ephemery_network_override(vc_name or "", "vc") if network == "ephemery" else None

    if flags.get("epbs") and flags.get("mevboost"):
        from deploy.builder_mode import BuilderModeError
        raise BuilderModeError(
            "Native ePBS does not install mevboost.service. "
            "Choose one builder mode."
        )
    if flags.get("epbs") and flags.get("charon"):
        from deploy.builder_mode import BuilderModeError
        raise BuilderModeError(
            "Obol Charon has no ePBS support yet. Refusing to install native ePBS."
        )

    mev_ver, mev_path = "", ""
    if flags['mevboost'] and not flags['validator_only'] and not flags.get('switch_client'):
        import config
        from deploy.builder_mode import default_relay_selection, is_csm_role

        relay_options = params.get("relays") or getattr(config, f"{network}_relay_options", [])
        if not params.get("relays") and is_csm_role(role):
            relay_options = default_relay_selection(relay_options, csm=True)
        mev_ver, mev_path = mevboost.install_mevboost(network, mev_min_bid, list(relay_options))

    el_ver, el_path = "", ""
    # SSV needs ETH1 over WebSocket. Geth/ethrex already expose localhost WS;
    # Besu/Nethermind/Reth/Erigon only enable it when installing with SSV.
    enable_el_ws = bool(flags.get("ssv"))
    if not flags['validator_only'] and ec_name:
        if ec_name == 'Besu':
            el_ver, el_path = besu.download_and_install_besu(
                network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path,
                network_override=el_override, enable_ws=enable_el_ws,
            )
        elif ec_name == 'Nethermind':
            import config
            sync_params = getattr(config, f"{network}_nethermind_sync_parameters", '')
            el_ver, el_path = nethermind.download_and_install_nethermind(
                network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path,
                network_override=el_override, sync_parameters=sync_params,
                enable_ws=enable_el_ws,
            )
        elif ec_name == 'Reth':
            el_ver, el_path = reth.download_and_install_reth(
                network, el_p2p_port, el_p2p_port_2, el_rpc_port, el_max_peers,
                jwtsecret_path, network_override=el_override, enable_ws=enable_el_ws,
            )
        elif ec_name == 'Erigon':
            if cc_name == 'Caplin' or cc_name == 'Caplin (integrated)':
                mev_params = f'--caplin.mev-relay-url=http://127.0.0.1:18550' if flags['mevboost'] else ''
                # eth-docker #2836: Caplin QUIC is CL_P2P+1 (9001), not native UDP 4001.
                cl_quic_port = str(cl_p2p_port_2) if cl_p2p_port_2 else '9001'
                el_ver, el_path = erigon.download_and_install_erigon(
                    network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path,
                    cl_p2p_port, cl_rest_port, cl_max_peers, sync_url,
                    mev_parameters=mev_params, cl_quic_port=cl_quic_port,
                    network_override=el_override, enable_ws=enable_el_ws,
                )
            else:
                el_ver, el_path = erigon.download_and_install_erigon_standalone(
                    network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path,
                    network_override=el_override, enable_ws=enable_el_ws,
                )
        elif ec_name == 'Geth':
            el_ver, el_path = geth.download_and_install_geth(network, str(el_p2p_port), str(el_rpc_port), str(el_max_peers), jwtsecret_path, network_override=el_override)
        elif ec_name == 'Ethrex':
            el_ver, el_path = ethrex.download_and_install_ethrex(network, str(el_p2p_port), str(el_rpc_port), str(el_max_peers), jwtsecret_path, network_override=el_override)

    cl_ver, cl_path = "", ""
    if not flags['validator_only'] and cc_name and cc_name not in ['Caplin', 'Caplin (integrated)']:
        if cc_name == 'Lighthouse':
            mev_params = f'--builder http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = lighthouse.download_lighthouse(network)
            cl_path = lighthouse.install_lighthouse_bn(network, sync_url, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peers, mev_parameters=mev_params, network_override=cl_override)
        elif cc_name == 'Nimbus':
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            mev_params = '--payload-builder=true --payload-builder-url=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = nimbus.download_nimbus(network)
            cl_path = nimbus.install_nimbus_bn(network, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params, sync_url=sync_url, network_override=cl_override)
        elif cc_name == 'Teku':
            fee_params = f'--validators-proposer-default-fee-recipient={fee_recipient}'
            if flags.get('charon'):
                fee_params = (
                    f'{fee_params} --validators-graffiti-client-append-format=DISABLED'
                )
            mev_params = '--validators-builder-registration-default-enabled=true --builder-endpoint=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = teku.download_teku(network)
            cl_path = teku.install_teku_bn(network, sync_url, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params, network_override=cl_override)
        elif cc_name == 'Lodestar':
            fee_params = f'--suggestedFeeRecipient={fee_recipient}'
            mev_params = '--builder --builder.urls http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = lodestar.download_lodestar(network)
            cl_path = lodestar.install_lodestar_bn(network, sync_url, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params, network_override=cl_override)
        elif cc_name == 'Grandine':
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            mev_params = '--builder-url=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = grandine.download_grandine(network)
            is_integrated_vc = (vc_name == 'Grandine (integrated)' and flags['validator'])
            grandine_signer_url = (
                web3signer.DEFAULT_SIGNER_URL
                if flags.get("web3signer") and is_integrated_vc
                else ""
            )
            cl_path = grandine.install_grandine_bn(network, sync_url, jwtsecret_path, str(cl_rest_port), str(cl_p2p_port), str(cl_p2p_port_2), str(cl_max_peers), fee_parameters=fee_params, mev_parameters=mev_params, is_integrated_vc=is_integrated_vc, network_override=cl_override, web3signer_url=grandine_signer_url)
        elif cc_name == 'Prysm':
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            mev_params = '--http-mev-relay=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = prysm.download_prysm(network)
            cl_path = prysm.install_prysm_bn(network, sync_url, jwtsecret_path, str(cl_rest_port), str(cl_p2p_port), str(cl_p2p_port_2), str(cl_max_peers), fee_parameters=fee_params, mev_parameters=mev_params, network_override=cl_override)

    charon_enabled = bool(flags.get('charon'))
    web3signer_enabled = bool(flags.get('web3signer'))
    ssv_enabled = bool(flags.get('ssv'))
    if charon_enabled and vc_name == "Grandine (integrated)":
        raise ValueError("Obol Charon is incompatible with Grandine (integrated). Select a standalone VC.")
    if charon_enabled and vc_name == CHARON_VC_LABEL:
        raise ValueError("Obol Charon DV requires a signer validator client (e.g. Lodestar).")
    if ssv_enabled and is_ssv_vc_choice(vc_name):
        # Menu label is not a traditional VC name; clear so BN/EL install continues.
        vc_name = None
        flags["validator"] = False

    # Existing Charon on a consensus switch: set/clear json_requests for the new BN.
    if charon_enabled and flags.get('switch_client') == 'consensus':
        try:
            charon.patch_json_requests_feature(
                charon.CHARON_SERVICE_PATH,
                enable=(cc_name == "Nimbus"),
            )
        except FileNotFoundError:
            pass

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
        # Canonical EthPillar name is CHARON_P2P_PORT; CDVN uses CHARON_PORT_P2P_TCP.
        charon_p2p_port = env_vars.get('CHARON_P2P_PORT') or env_vars.get('CHARON_PORT_P2P_TCP') or '3610'
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

    web3signer_ver, web3signer_path = "", ""
    if web3signer_enabled and not flags.get("switch_client"):
        web3signer_ver, web3signer_path = web3signer.install_web3signer(network)

    ssv_ver, ssv_node_path, ssv_dkg_path = "", "", ""
    if ssv_enabled and not flags.get("switch_client"):
        ssv_tcp = int(env_vars.get("SSV_P2P_TCP_PORT") or ssv.DEFAULT_P2P_TCP_PORT)
        ssv_udp = int(env_vars.get("SSV_P2P_UDP_PORT") or ssv.DEFAULT_P2P_UDP_PORT)
        ssv_metrics = int(env_vars.get("SSV_METRICS_PORT") or ssv.DEFAULT_METRICS_PORT)
        ssv_api = int(env_vars.get("SSV_API_PORT") or ssv.DEFAULT_API_PORT)
        ssv_dkg_port = int(env_vars.get("SSV_DKG_PORT") or ssv.DEFAULT_DKG_PORT)
        el_ip = env_vars.get("EL_IP_ADDRESS", "127.0.0.1")
        el_rpc = env_vars.get("EL_RPC_PORT", "8545")
        el_ws = env_vars.get("EL_WS_PORT", "8546")
        eth1_ws = f"ws://{el_ip}:{el_ws}"
        eth1_http = f"http://{el_ip}:{el_rpc}"
        ssv_ver, ssv_node_path, ssv_dkg_path = ssv.install_ssv(
            network,
            upstream_bn,
            eth1_ws_addr=eth1_ws,
            eth1_http_addr=eth1_http,
            p2p_tcp_port=ssv_tcp,
            p2p_udp_port=ssv_udp,
            metrics_port=ssv_metrics,
            api_port=ssv_api,
            dkg_port=ssv_dkg_port,
            p2p_external_ip=env_vars.get("SSV_P2P_EXTERNAL_IP", ""),
            operator_id=env_vars.get("SSV_OPERATOR_ID", ""),
            install_dkg=True,
        )

    val_path = ""
    val_ver = ""
    use_builder = bool(flags.get('builder_api') or flags.get('mevboost'))
    if flags['validator'] and vc_name and not ssv_enabled:
        addr = (
            charon.charon_validator_api_url(
                port=env_vars.get('CHARON_VALIDATOR_API_PORT', '3600')
            )
            if charon_enabled
            else upstream_bn
        )
        # Same-host ordering: start Charon or Web3Signer before the VC.
        vc_unit_after = []
        if charon_enabled:
            vc_unit_after.append("charon.service")
        if web3signer_enabled:
            vc_unit_after.append("web3signer.service")
        vc_unit_after = vc_unit_after or None

        if vc_name == 'Lighthouse':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else lighthouse.download_lighthouse(network)
            val_ver = v_ver
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            extra_params = _with_signer_params(
                _with_dvt_params(
                    '--builder-proposals' if use_builder else '',
                    vc_name,
                    charon_enabled,
                ),
                vc_name,
                web3signer_enabled,
            )
            bn_arg = f'--beacon-nodes={addr}'
            val_path = lighthouse.install_lighthouse_vc(
                v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after, network_override=vc_override,
            )
            if web3signer_enabled:
                web3signer.ensure_lighthouse_definitions_file()
        elif vc_name == 'Nimbus':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else nimbus.download_nimbus(network)
            val_ver = v_ver
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            extra_params = _with_signer_params(
                _with_dvt_params(
                    '--payload-builder=true' if use_builder else '',
                    vc_name,
                    charon_enabled,
                ),
                vc_name,
                web3signer_enabled,
            )
            bn_arg = f'--beacon-node={addr}'
            val_path = nimbus.install_nimbus_vc(
                v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )  # Nimbus VC has no --network flag
        elif vc_name == 'Teku':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else teku.download_teku(network)
            val_ver = v_ver
            fee_params = f'--validators-proposer-default-fee-recipient={fee_recipient}'
            extra_params = _with_signer_params(
                _with_dvt_params(
                    '--validators-builder-registration-default-enabled=true' if use_builder else '',
                    vc_name,
                    charon_enabled,
                ),
                vc_name,
                web3signer_enabled,
            )
            bn_arg = f'--beacon-node-api-endpoint={addr}'
            val_path = teku.install_teku_vc(
                v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after, network_override=vc_override,
            )
        elif vc_name == 'Lodestar':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else lodestar.download_lodestar(network)
            val_ver = v_ver
            fee_params = f'--suggestedFeeRecipient={fee_recipient}'
            extra_params = _with_signer_params(
                _with_dvt_params(
                    '--builder' if use_builder else '',
                    vc_name,
                    charon_enabled,
                ),
                vc_name,
                web3signer_enabled,
            )
            bn_arg = f'--beaconNodes={addr}'
            val_path = lodestar.install_lodestar_vc(
                v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after, network_override=vc_override,
            )
        elif vc_name == 'Prysm':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else prysm.download_prysm(network)
            val_ver = v_ver
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            extra_params = _with_signer_params(
                _with_dvt_params(
                    '--enable-builder' if use_builder else '',
                    vc_name,
                    charon_enabled,
                ),
                vc_name,
                web3signer_enabled,
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
                network_override=vc_override,
            )

    if flags.get("epbs") and not flags.get("switch_client"):
        import config
        from deploy.builder_mode import default_relay_selection, is_csm_role

        relay_options = params.get("relays")
        if not relay_options:
            relay_options = list(getattr(config, f"{network}_relay_options", []) or [])
            if is_csm_role(role):
                relay_options = default_relay_selection(relay_options, csm=True)
        apply_install_epbs(network, list(relay_options or []), mev_min_bid)

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
        web3signer_enabled=web3signer_enabled,
        web3signer_version=web3signer_ver,
        web3signer_service_path=web3signer_path,
        ssv_enabled=ssv_enabled,
        ssv_version=ssv_ver,
        ssv_node_service_path=ssv_node_path,
        ssv_dkg_service_path=ssv_dkg_path,
        builder_mode=str(flags.get("builder_mode") or ""),
        relays=list(params.get("relays") or []),
    )
