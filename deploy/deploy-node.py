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
    get_ec_menu, get_cc_menu, get_vc_options_for_cc, resolve_vc_name, run_install
)
import config

common.clear_screen()
valid_networks = ['MAINNET', 'HOODI', 'EPHEMERY', 'HOLESKY', 'SEPOLIA']

load_dotenv("env")

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
parser.add_argument("--skip_prompts", type=str, default="")
args = parser.parse_args()

if args.fee_address:
    FEE_RECIPIENT_ADDRESS = args.fee_address

skip_prompts = args.skip_prompts.lower() == 'true'

# 1. Network selection
if not args.network and not skip_prompts:
    index = SelectionMenu.get_selection(valid_networks, title='Validator Install Quickstart', subtitle='Select Ethereum network:')
    if index == len(valid_networks): exit(0)
    eth_network = valid_networks[index].lower()
else:
    eth_network = args.network.lower()

# 2. Role selection
if not args.install_config and not skip_prompts:
    index = SelectionMenu.get_selection(VALID_ROLES, title='Validator Install Quickstart', subtitle='What type of installation would you like?', show_exit_option=False)
    role = VALID_ROLES[index]
else:
    role = args.install_config

flags = resolve_role_flags(role, eth_network)

# 3. Client Selection
ec_name = None
cc_name = None
vc_name = None

if flags['validator_only']:
    # VC Only Path
    if not args.vc and not skip_prompts:
        vc_menu = get_vc_menu()
        index = SelectionMenu.get_selection(vc_menu, title='Validator Client Selection', subtitle='Select your Validator Client:', show_exit_option=False)
        vc_name = vc_menu[index]
    else:
        vc_name = args.vc or args.cc # Fallback to --cc if --vc not passed
elif role == "Custom":
    # Custom Path
    if not skip_prompts:
        # EC
        ec_menu = get_ec_menu()
        index = SelectionMenu.get_selection(ec_menu, title='Custom Setup', subtitle='Step 1: Select your Execution Client', show_exit_option=False)
        ec_name = ec_menu[index]
        # CC
        cc_menu = get_cc_menu(ec_name)
        index = SelectionMenu.get_selection(cc_menu, title='Custom Setup', subtitle='Step 2: Select your Consensus Client', show_exit_option=False)
        cc_name = cc_menu[index]
        
        # VC
        val_prompt = SelectionMenu.get_selection(["Yes", "No"], title='Custom Setup', subtitle='Step 3: Do you want a Validator Client?', show_exit_option=False)
        if val_prompt == 0:
            flags['validator'] = True
            vc_opts = get_vc_options_for_cc(cc_name)
            if len(vc_opts) == 4: # No "Same as CC"
                index = SelectionMenu.get_selection(vc_opts, title='Validator Client', subtitle='Select your Validator Client:', show_exit_option=False)
                vc_name = vc_opts[index]
            else:
                index = SelectionMenu.get_selection(vc_opts, title='Validator Client', subtitle='Use same client as CC?', show_exit_option=False)
                vc_name = resolve_vc_name(cc_name, vc_opts[index])
        else:
            flags['validator'] = False
            vc_name = None

        # MEV
        mev_prompt = SelectionMenu.get_selection(["Yes", "No"], title='Custom Setup', subtitle='Step 4: Do you want MEV-Boost?', show_exit_option=False)
        flags['mevboost'] = (mev_prompt == 0)
        
    else:
        ec_name = args.ec
        cc_name = args.cc
        vc_name = args.vc if args.vc else cc_name if args.with_validator else None
        flags['validator'] = args.with_validator or bool(args.vc)
        flags['mevboost'] = args.with_mevboost

else:
    # Predefined role (Solo/Full/Failover/CSM) -> Combo Menu
    if not args.combo and not args.ec and not skip_prompts:
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
    
    # For predefined roles, VC is usually same as CC if validator is enabled
    if flags['validator']:
        vc_name = cc_name

# 4. Role-specific prompts
beacon_node_address = args.vc_only_bn_address
if flags['validator_only'] and not beacon_node_address and not skip_prompts:
    beacon_node_address = input("What is your beacon node URL? (e.g. http://192.168.1.5:5052): ").strip()
    if not beacon_node_address:
        print("Beacon node address is required for VC-only setup.")
        exit(1)

# Fee recipient prompt for non-CSM roles with validator
if flags['validator'] and not FEE_RECIPIENT_ADDRESS and not skip_prompts:
    if "Lido CSM" not in role:
        FEE_RECIPIENT_ADDRESS = input("What is your fee recipient address? (0x...): ").strip()

# Sync URL
sync_url = ""
if not flags['validator_only'] and not skip_prompts:
    try:
        sync_urls_list = getattr(config, f"{eth_network}_sync_urls", [])
        if sync_urls_list:
            titles = [f"{item[0]} : {item[1]}" for item in sync_urls_list]
            index = SelectionMenu.get_selection(titles, title='Validator Install Quickstart', subtitle='Select a Checkpoint-Sync URL:', show_exit_option=False)
            sync_url = sync_urls_list[index][1]
    except AttributeError:
        pass
else:
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

# 5. Execute Install
run_install(
    role=role, 
    network=eth_network, 
    ec_name=ec_name, 
    cc_name=cc_name, 
    vc_name=vc_name, 
    flags=flags, 
    params=params, 
    env_vars=env_vars
)
