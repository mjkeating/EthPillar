import os
from typing import Dict, List, Optional, Tuple
import deploy.besu as besu
import deploy.nethermind as nethermind
import deploy.reth as reth
import deploy.erigon as erigon
import deploy.geth as geth
import deploy.lighthouse as lighthouse
import deploy.nimbus as nimbus
import deploy.teku as teku
import deploy.lodestar as lodestar
import deploy.mevboost as mevboost

VALID_ROLES = [
    'Solo Staking Node',
    'Full Node Only',
    'Lido CSM Staking Node',
    'Lido CSM Validator Client Only',
    'Validator Client Only',
    'Failover Staking Node',
    'Custom Setup'
]

EXECUTION_CLIENTS = ['Besu', 'Nethermind', 'Reth', 'Erigon', 'Geth']
CONSENSUS_CLIENTS = ['Lighthouse', 'Nimbus', 'Teku', 'Lodestar']

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
        "validator": False,
        "validator_only": False,
        "node_only": False
    }

    if role == "Solo Staking Node" or role == "Lido CSM Staking Node":
        flags["mevboost"] = True
        flags["validator"] = True
    elif role == "Full Node Only":
        flags["node_only"] = True
    elif role == "Validator Client Only" or role == "Lido CSM Validator Client Only":
        flags["mevboost"] = False
        flags["validator"] = True
        flags["validator_only"] = True
    elif role == "Failover Staking Node":
        flags["mevboost"] = True

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

def get_vc_menu() -> List[str]:
    return CONSENSUS_CLIENTS.copy()

def get_ec_menu() -> List[str]:
    return EXECUTION_CLIENTS.copy()

def get_cc_menu(ec_name: str) -> List[str]:
    choices = CONSENSUS_CLIENTS.copy()
    if ec_name == 'Erigon':
        choices.append('Caplin (integrated)')
    return choices

def get_vc_options_for_cc(cc_name: str) -> List[str]:
    if cc_name == 'Caplin' or cc_name == 'Caplin (integrated)':
        return CONSENSUS_CLIENTS.copy()
    
    return ['Same as CC'] + CONSENSUS_CLIENTS.copy()

def resolve_vc_name(cc_name: str, vc_choice: str) -> str:
    if vc_choice == 'Same as CC':
        return cc_name
    return vc_choice

def is_valid_combination(ec: str, cc: str) -> bool:
    if ec == 'Erigon' and cc == 'Caplin':
        return True
    if ec == 'Erigon' and cc in CONSENSUS_CLIENTS:
        return True # Erigon standalone
    if ec in ['Besu', 'Nethermind', 'Reth', 'Geth'] and cc in CONSENSUS_CLIENTS:
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
    el_p2p_port = int(params.get('el_p2p_port', 0))
    el_p2p_port_2 = int(params.get('el_p2p_port_2', 0))
    el_rpc_port = int(params.get('el_rpc_port', 0))
    el_max_peers = int(params.get('el_max_peers', 0))
    cl_p2p_port = int(params.get('cl_p2p_port', 0))
    cl_p2p_port_2 = int(params.get('cl_p2p_port_2', 0))
    cl_rest_port = int(params.get('cl_rest_port', 0))
    cl_max_peers = int(params.get('cl_max_peers', 0))
    mev_min_bid = params.get('mev_min_bid', '')
    skip_prompts = params.get('skip_prompts', 'false').lower() == 'true'

    fee_recipient, graffiti, mev_min_bid = apply_csm_overrides(role, network, env_vars, fee_recipient, graffiti)

    common.setup_node(jwtsecret_path, flags['validator_only'])

    if network == "ephemery":
        common.setup_ephemery_network("ephemery-testnet/ephemery-genesis")

    mev_ver, mev_path = "", ""
    if flags['mevboost'] and not flags['validator_only']:
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
                el_ver, el_path = erigon.download_and_install_erigon(
                    network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path,
                    cl_p2p_port, cl_rest_port, cl_max_peers, sync_url, mev_parameters=mev_params
                )
            else:
                el_ver, el_path = erigon.download_and_install_erigon_standalone(
                    network, el_p2p_port, el_rpc_port, el_max_peers, jwtsecret_path
                )
        elif ec_name == 'Geth':
            el_ver, el_path = geth.download_and_install_geth(network, str(el_p2p_port), str(el_rpc_port), str(el_max_peers), jwtsecret_path)

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
            cl_path = nimbus.install_nimbus_bn(network, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params)
        elif cc_name == 'Teku':
            fee_params = f'--validators-proposer-default-fee-recipient={fee_recipient}'
            mev_params = '--validators-builder-registration-default-enabled=true --builder-endpoint=http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = teku.download_teku(network)
            cl_path = teku.install_teku_bn(network, sync_url, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params)
        elif cc_name == 'Lodestar':
            fee_params = f'--suggestedFeeRecipient={fee_recipient}'
            mev_params = '--builder --builder.urls http://127.0.0.1:18550' if flags['mevboost'] else ''
            cl_ver = lodestar.download_lodestar(network)
            cl_path = lodestar.install_lodestar_bn(network, sync_url, jwtsecret_path, cl_rest_port, cl_p2p_port, cl_max_peers, fee_parameters=fee_params, mev_parameters=mev_params)

    val_path = ""
    val_ver = ""
    if flags['validator'] and vc_name:
        cl_ip = env_vars.get('CL_IP_ADDRESS', '127.0.0.1')
        local_bn_addr = f"http://{cl_ip}:{cl_rest_port}" if cc_name != 'Caplin' and cc_name != 'Caplin (integrated)' else f"http://127.0.0.1:{cl_rest_port}"
        addr = bn_address if flags['validator_only'] else local_bn_addr

        if vc_name == 'Lighthouse':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else lighthouse.download_lighthouse(network)
            val_ver = v_ver
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            mev_params = '--builder-proposals' if flags['mevboost'] else ''
            bn_arg = f'--beacon-nodes={addr}'
            val_path = lighthouse.install_lighthouse_vc(v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, mev_params)
        elif vc_name == 'Nimbus':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else nimbus.download_nimbus(network)
            val_ver = v_ver
            fee_params = f'--suggested-fee-recipient={fee_recipient}'
            mev_params = '--payload-builder=true' if flags['mevboost'] else ''
            bn_arg = f'--beacon-node={addr}'
            val_path = nimbus.install_nimbus_vc(v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, mev_params)
        elif vc_name == 'Teku':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else teku.download_teku(network)
            val_ver = v_ver
            fee_params = f'--validators-proposer-default-fee-recipient={fee_recipient}'
            mev_params = '--validators-builder-registration-default-enabled=true' if flags['mevboost'] else ''
            bn_arg = f'--beacon-node-api-endpoint={addr}'
            val_path = teku.install_teku_vc(v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, mev_params)
        elif vc_name == 'Lodestar':
            v_ver = cl_ver if vc_name == cc_name and cl_ver else lodestar.download_lodestar(network)
            val_ver = v_ver
            fee_params = f'--suggestedFeeRecipient={fee_recipient}'
            mev_params = '--builder' if flags['mevboost'] else ''
            bn_arg = f'--beaconNodes={addr}'
            val_path = lodestar.install_lodestar_vc(v_ver, network, str(cl_rest_port), graffiti, bn_arg, fee_params, mev_params)

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
        skip_prompts=False,   
        cl_rest_port=str(cl_rest_port),
        vc_name=vc_name, vc_ver=val_ver
    )
