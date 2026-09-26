import os
import sys
import argparse

# Ensure parent directory is in path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from consolemenu import *
from consolemenu.items import *
from dotenv import load_dotenv

import common as common
from orchestrator import (
    VALID_ROLES, resolve_role_flags, get_combo_menu, get_vc_menu,
    get_ec_menu, get_cc_menu, get_vc_options_for_cc, resolve_vc_name, run_install,
    CHARON_VC_LABEL, OBOL_CHARON, is_charon_vc_choice, lodestar_bn_vc_incompatibility_message,
)
import config

common.clear_screen()
valid_networks = ['MAINNET', 'HOODI', 'EPHEMERY', 'HOLESKY', 'SEPOLIA']

load_dotenv(os.getenv("ETHPILLAR_ENV_FILE", "env"))

# Defaults from env
EL_P2P_PORT = os.getenv('EL_P2P_PORT')
EL_P2P_PORT_2 = os.getenv('EL_P2P_PORT_2')
EL_RPC_PORT = os.getenv('EL_RPC_PORT')
EL_MAX_PEER_COUNT = int(os.getenv('EL_MAX_PEER_COUNT', '50'))
CL_P2P_PORT = os.getenv('CL_P2P_PORT')
CL_P2P_PORT_2 = os.getenv('CL_P2P_PORT_2')
CL_REST_PORT = os.getenv('CL_REST_PORT')
CL_MAX_PEER_COUNT = os.getenv('CL_MAX_PEER_COUNT')
CL_IP_ADDRESS = os.getenv('CL_IP_ADDRESS')
JWTSECRET_PATH = os.getenv('JWTSECRET_PATH')
GRAFFITI = os.getenv('GRAFFITI')
FEE_RECIPIENT_ADDRESS = os.getenv('FEE_RECIPIENT_ADDRESS')
MEV_MIN_BID = os.getenv('MEV_MIN_BID')

parser = argparse.ArgumentParser(description='Node Install Options', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--network", type=str, default="", help="Ethereum network")
parser.add_argument("--install_config", type=str, choices=VALID_ROLES, default="")
parser.add_argument("--combo", type=str, default="")
parser.add_argument("--ec", type=str, default="")
parser.add_argument("--cc", type=str, default="")
parser.add_argument("--vc", type=str, default="")
parser.add_argument("--with_validator", action="store_true", default=False)
parser.add_argument("--with_mevboost", action="store_true", default=False)
parser.add_argument(
    "--with_builder_api",
    action="store_true",
    default=False,
    help="Enable Charon/VC builder flags without installing local MEV-Boost "
    "(e.g. external relays / CDVN BUILDER_API_ENABLED)",
)
parser.add_argument(
    "--with_charon",
    action="store_true",
    default=False,
    help="Install Obol Charon DVT middleware and point the VC at Charon (:3600)",
)
parser.add_argument("--jwtsecret", type=str, default=JWTSECRET_PATH)
parser.add_argument("--graffiti", type=str, default=GRAFFITI)
parser.add_argument("--fee_address", type=str, default="")
parser.add_argument("--el_p2p_port", type=int, default=EL_P2P_PORT)
parser.add_argument("--el_rpc_port", type=int, default=EL_RPC_PORT)
parser.add_argument("--el_max_peers", type=int, default=EL_MAX_PEER_COUNT)
parser.add_argument("--cl_p2p_port", type=int, default=CL_P2P_PORT)
parser.add_argument("--cl_rest_port", type=int, default=CL_REST_PORT)
parser.add_argument("--cl_max_peers", type=int, default=CL_MAX_PEER_COUNT)
parser.add_argument("--vc_only_bn_address", type=str, default="")
parser.add_argument("--switch_client", type=str, choices=["execution", "consensus"], default="")
parser.add_argument("--skip_prompts", type=str, default="")
parser.add_argument(
    "--checkpoint_sync_url",
    type=str,
    default="",
    help="Override checkpoint sync URL (skips menu / default list selection)",
)
args = parser.parse_args()

if args.fee_address:
    FEE_RECIPIENT_ADDRESS = args.fee_address

skip_prompts = args.skip_prompts.lower() == 'true'

# 1. Network selection
if not args.network:
    index = SelectionMenu.get_selection(valid_networks, title='Validator Install Quickstart', subtitle='Select Ethereum network:')
    if index == len(valid_networks): exit(0)
    eth_network = valid_networks[index].lower()
else:
    eth_network = args.network.lower()

if args.switch_client:
    role = f"Switch {args.switch_client.capitalize()} Client"
    flags = {
        "mevboost": args.with_mevboost,
        "builder_api": bool(args.with_mevboost or args.with_builder_api),
        "validator": False,
        "validator_only": False,
        "node_only": False,
        "switch_client": args.switch_client
    }
else:
    # 2. Role selection
    if not args.install_config:
        index = SelectionMenu.get_selection(VALID_ROLES, title='Validator Install Quickstart', subtitle='What type of installation would you like?', show_exit_option=False)
        role = VALID_ROLES[index]
    else:
        role = args.install_config

    flags = resolve_role_flags(role, eth_network)

flags["charon"] = bool(args.with_charon)
if args.with_mevboost:
    flags["mevboost"] = True
# Builder API can be on without a local mevboost.service (external MEV / CDVN).
flags["builder_api"] = bool(
    flags.get("builder_api") or flags.get("mevboost") or args.with_builder_api
)
if args.vc == CHARON_VC_LABEL:
    print(
        f"ERROR: --vc '{CHARON_VC_LABEL}' is not a signer client. "
        "Use --with_charon --vc <Lighthouse|Nimbus|Teku|Lodestar|Prysm>."
    )
    exit(1)
if flags["charon"] and args.vc == "Grandine (integrated)":
    print(f"ERROR: {OBOL_CHARON} is incompatible with Grandine (integrated).")
    exit(1)

# 3. Client Selection
ec_name = None
cc_name = None
vc_name = None

if args.combo:
    from deploy.orchestrator import PREDEFINED_COMBOS
    ec_name, cc_name = PREDEFINED_COMBOS.get(args.combo, (None, None))

if args.switch_client == "execution":
    if not args.ec:
        ec_menu = get_ec_menu()
        index = SelectionMenu.get_selection(ec_menu, title='Switch Execution Client', subtitle='Select your new Execution Client:', show_exit_option=False)
        ec_name = ec_menu[index]
    else:
        ec_name = args.ec
    cc_name = args.cc
elif args.switch_client == "consensus":
    if not args.cc:
        cc_menu = get_cc_menu(args.ec)
        index = SelectionMenu.get_selection(cc_menu, title='Switch Consensus Client', subtitle='Select your new Consensus Client:', show_exit_option=False)
        cc_name = cc_menu[index]
    else:
        cc_name = args.cc
    ec_name = args.ec
elif flags['validator_only']:
    # VC Only Path
    if not args.vc:
        if skip_prompts:
            vc_name = cc_name or args.cc or "Lighthouse"
        else:
            vc_menu = get_vc_menu(include_charon=True)
            index = SelectionMenu.get_selection(vc_menu, title='Validator Client Selection', subtitle='Select your Validator Client:', show_exit_option=False)
            choice = vc_menu[index]
            if is_charon_vc_choice(choice):
                flags["charon"] = True
                signer_menu = get_vc_menu(include_charon=False)
                signer_idx = SelectionMenu.get_selection(
                    signer_menu,
                    title=f'{OBOL_CHARON} Signer',
                    subtitle=f'Select the validator client that will sign behind {OBOL_CHARON}:',
                    show_exit_option=False,
                )
                vc_name = signer_menu[signer_idx]
            else:
                vc_name = choice
    else:
        vc_name = args.vc or args.cc # Fallback to --cc if --vc not passed

    # Remote BN may already run MEV-Boost; enable VC/Charon builder flags without
    # installing a local mevboost.service.
    if args.with_builder_api or args.with_mevboost:
        flags['builder_api'] = True
    elif not skip_prompts:
        builder_prompt = SelectionMenu.get_selection(
            ["Yes", "No"],
            title='Validator Client Only',
            subtitle=(
                'Does the remote beacon node use MEV-Boost / builder API?\n'
                '(Enables VC builder flags; does not install local MEV-Boost)'
            ),
            show_exit_option=False,
        )
        flags['builder_api'] = builder_prompt == 0
    # else: keep flags['builder_api'] from CLI / role defaults (False)
elif role == "Custom Setup":
    # Custom Path
    # EC
    if not args.ec:
        ec_menu = get_ec_menu()
        index = SelectionMenu.get_selection(ec_menu, title='Custom Setup', subtitle='Step 1: Select your Execution Client', show_exit_option=False)
        ec_name = ec_menu[index]
    else:
        ec_name = args.ec
    # CC
    if not args.cc:
        cc_menu = get_cc_menu(ec_name)
        index = SelectionMenu.get_selection(cc_menu, title='Custom Setup', subtitle='Step 2: Select your Consensus Client', show_exit_option=False)
        cc_name = cc_menu[index]
    else:
        cc_name = args.cc
    
    # VC
    # VC
    if not args.vc and not args.with_validator:
        if skip_prompts:
            flags['validator'] = False
            vc_name = None
        else:
            val_prompt = SelectionMenu.get_selection(["Yes", "No"], title='Custom Setup', subtitle='Step 3: Do you want a Validator Client?', show_exit_option=False)
            if val_prompt == 0:
                flags['validator'] = True
                vc_opts = get_vc_options_for_cc(cc_name, include_charon=True)
                subtitle = (
                    'Select your Validator Client:'
                    if vc_opts[0] != "Same as CC"
                    else 'Use same client as CC?'
                )
                index = SelectionMenu.get_selection(
                    vc_opts, title='Validator Client', subtitle=subtitle, show_exit_option=False
                )
                choice = vc_opts[index]
                if is_charon_vc_choice(choice):
                    flags["charon"] = True
                    signer_opts = get_vc_options_for_cc(
                        cc_name, include_charon=False, for_charon_signer=True
                    )
                    signer_idx = SelectionMenu.get_selection(
                        signer_opts,
                        title=f'{OBOL_CHARON} Signer',
                        subtitle=f'Select the validator client that will sign behind {OBOL_CHARON}:',
                        show_exit_option=False,
                    )
                    vc_name = resolve_vc_name(cc_name, signer_opts[signer_idx])
                else:
                    vc_name = resolve_vc_name(cc_name, choice)
            else:
                flags['validator'] = False
                vc_name = None
    else:
        flags['validator'] = True
        vc_name = args.vc if args.vc else cc_name

    # MEV
    if args.with_mevboost:
        flags['mevboost'] = True
    elif not skip_prompts:
        mev_prompt = SelectionMenu.get_selection(["Yes", "No"], title='Custom Setup', subtitle='Step 4: Do you want MEV-Boost?', show_exit_option=False)
        flags['mevboost'] = (mev_prompt == 0)
    else:
        flags['mevboost'] = False
    flags['builder_api'] = bool(flags.get('mevboost') or args.with_builder_api)


else:
    # Predefined role (Solo/Full/Failover/CSM) -> Combo Menu
    if not args.combo and not args.ec:
        combo_menu = get_combo_menu()
        index = SelectionMenu.get_selection(combo_menu, title='Client Configuration', subtitle='Pick your combination:', show_exit_option=False)
        combo_choice = combo_menu[index]
        from deploy.orchestrator import PREDEFINED_COMBOS
        ec_name, cc_name = PREDEFINED_COMBOS[combo_choice]
    else:
        if args.combo:
            from deploy.orchestrator import PREDEFINED_COMBOS
            ec_name, cc_name = PREDEFINED_COMBOS.get(args.combo, (None, None))
        else:
            ec_name = args.ec
            cc_name = args.cc
    
    # For predefined roles, VC is usually same as CC if validator is enabled.
    # Offer Obol Charon DV as an alternate validator mode.
    if flags['validator']:
        if args.vc:
            vc_name = args.vc
        elif skip_prompts or flags.get("charon"):
            vc_name = cc_name
        else:
            mode_opts = ["Same as CC", CHARON_VC_LABEL]
            mode_idx = SelectionMenu.get_selection(
                mode_opts,
                title='Validator Client',
                subtitle=f'Use same client as consensus, or {CHARON_VC_LABEL}?',
                show_exit_option=False,
            )
            if is_charon_vc_choice(mode_opts[mode_idx]):
                flags["charon"] = True
                signer_opts = get_vc_options_for_cc(
                    cc_name, include_charon=False, for_charon_signer=True
                )
                signer_idx = SelectionMenu.get_selection(
                    signer_opts,
                    title=f'{OBOL_CHARON} Signer',
                    subtitle=f'Select the validator client that will sign behind {OBOL_CHARON}:',
                    show_exit_option=False,
                )
                vc_name = resolve_vc_name(cc_name, signer_opts[signer_idx])
            else:
                vc_name = cc_name

# 4. Role-specific prompts
beacon_node_address = args.vc_only_bn_address
if flags['validator_only'] and not beacon_node_address:
    beacon_node_address = input("What is your beacon node URL? (e.g. http://192.168.1.5:5052): ").strip()
    if not beacon_node_address:
        print("Beacon node address is required for VC-only setup.")
        exit(1)

# Fee recipient: prompt if not set.
# Some clients (Nimbus, Teku, Lodestar, Grandine, Prysm) embed the fee recipient at the BN level,
# so we must prompt even when there is no separate validator service.
# An execution-client switch passes --cc only for context; the BN is not reinstalled.
_cc_needs_fee = (
    cc_name in ['Nimbus', 'Teku', 'Lodestar', 'Grandine', 'Prysm']
    and args.switch_client != "execution"
)
_vc_needs_fee = flags['validator'] and vc_name not in ['Grandine (integrated)', None]
if (_cc_needs_fee or _vc_needs_fee) and not FEE_RECIPIENT_ADDRESS:
    if "Lido CSM" not in role:
        if skip_prompts:
            print(
                "ERROR: Fee recipient address is required. Set FEE_RECIPIENT_ADDRESS in "
                "env / .env.overrides, pass --fee_address 0x..., or include it in CDVN "
                "deposit-data.json.",
                file=sys.stderr,
            )
            exit(1)
        FEE_RECIPIENT_ADDRESS = input("What is your fee recipient address? (0x...): ").strip()

# Sync URL
sync_url = args.checkpoint_sync_url or ""
if not sync_url and not flags['validator_only'] and args.switch_client != "execution":
    try:
        sync_urls_list = getattr(config, f"{eth_network}_sync_urls", [])
        if sync_urls_list:
            if skip_prompts:
                # Non-interactive: auto-select the first available sync URL
                sync_url = sync_urls_list[0][1]
            else:
                titles = [f"{item[0]} : {item[1]}" for item in sync_urls_list]
                index = SelectionMenu.get_selection(titles, title='Validator Install Quickstart', subtitle='Select a Checkpoint-Sync URL:', show_exit_option=False)
                sync_url = sync_urls_list[index][1]
    except AttributeError:
        pass
elif not sync_url:
    try:
        sync_urls_list = getattr(config, f"{eth_network}_sync_urls", [])
        if sync_urls_list:
            sync_url = sync_urls_list[0][1]
    except AttributeError:
        pass

# Setup params and environment dicts
params = {
    'fee_recipient': FEE_RECIPIENT_ADDRESS,
    'graffiti': args.graffiti,
    'bn_address': beacon_node_address,
    'jwtsecret_path': args.jwtsecret,
    'sync_url': sync_url,
    'el_p2p_port': args.el_p2p_port,
    'el_p2p_port_2': EL_P2P_PORT_2,
    'el_rpc_port': args.el_rpc_port,
    'el_max_peers': args.el_max_peers,
    'cl_p2p_port': args.cl_p2p_port,
    'cl_p2p_port_2': CL_P2P_PORT_2,
    'cl_rest_port': args.cl_rest_port,
    'cl_max_peers': args.cl_max_peers,
    'mev_min_bid': MEV_MIN_BID,
    'skip_prompts': args.skip_prompts
}

env_vars = dict(os.environ)

# Warn on Lodestar BN + incompatible VC (Charon v1.11+ matrix); interactive can abort.
_bn_vc_warn = lodestar_bn_vc_incompatibility_message(
    cc_name if args.switch_client != "execution" else None,
    vc_name,
)
if _bn_vc_warn and not skip_prompts:
    print(f"\nWARNING: {_bn_vc_warn}\n")
    cont = SelectionMenu.get_selection(
        ["Continue anyway", "Abort install"],
        title="Lodestar BN compatibility",
        subtitle=f"{OBOL_CHARON} v1.11+ marks this BN/VC pair as duties may fail.",
        show_exit_option=False,
    )
    if cont != 0:
        exit(0)

# 5. Execute Install
run_install(
    role=role, 
    network=eth_network, 
    ec_name=ec_name if args.switch_client != "consensus" else None, 
    cc_name=cc_name if args.switch_client != "execution" else None, 
    vc_name=vc_name, 
    flags=flags, 
    params=params, 
    env_vars=env_vars
)
