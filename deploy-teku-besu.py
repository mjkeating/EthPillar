# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
#
# Validator-Install: Standalone Teku BN + Standalone Teku VC + Besu EL + MEVboost
# Quickstart :: Minority Client :: Docker-free

import os
import sys
import argparse
import random
from consolemenu import *
from consolemenu.items import *
from dotenv import load_dotenv

import deploy.common as common
import deploy.mevboost as mevboost
import deploy.besu as besu
import deploy.teku as teku
from config import *

# 1. Boilerplate
common.clear_screen()
valid_networks = ['MAINNET','HOODI','EPHEMERY', 'HOLESKY', 'SEPOLIA']
valid_install_configs = ['Solo Staking Node', 'Full Node Only', 'Lido CSM Staking Node', 'Lido CSM Validator Client Only', 'Validator Client Only', 'Failover Staking Node']

load_dotenv("env")

# 2. Get Configs from Env
EL_P2P_PORT = os.getenv('EL_P2P_PORT')
EL_RPC_PORT = os.getenv('EL_RPC_PORT')
EL_MAX_PEER_COUNT = int(os.getenv('EL_MAX_PEER_COUNT', '50'))
CL_P2P_PORT = os.getenv('CL_P2P_PORT')
CL_REST_PORT = os.getenv('CL_REST_PORT')
CL_MAX_PEER_COUNT = os.getenv('CL_MAX_PEER_COUNT')
CL_IP_ADDRESS = os.getenv('CL_IP_ADDRESS')
JWTSECRET_PATH = os.getenv('JWTSECRET_PATH')
GRAFFITI = os.getenv('GRAFFITI')
FEE_RECIPIENT_ADDRESS = os.getenv('FEE_RECIPIENT_ADDRESS')
MEV_MIN_BID = os.getenv('MEV_MIN_BID')

# 3. Parse Args
parser = argparse.ArgumentParser(description='Validator Install Options :: CoinCashew.com', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--network", type=str, choices=valid_networks, default="")
parser.add_argument("--jwtsecret", type=str, default=JWTSECRET_PATH)
parser.add_argument("--graffiti", type=str, default=GRAFFITI)
parser.add_argument("--fee_address", type=str, default="")
parser.add_argument("--el_p2p_port", type=int, default=EL_P2P_PORT)
parser.add_argument("--el_rpc_port", type=int, default=EL_RPC_PORT)
parser.add_argument("--el_max_peers", type=int, default=EL_MAX_PEER_COUNT)
parser.add_argument("--cl_p2p_port", type=int, default=CL_P2P_PORT)
parser.add_argument("--cl_rest_port", type=int, default=CL_REST_PORT)
parser.add_argument("--cl_max_peers", type=int, default=CL_MAX_PEER_COUNT)
parser.add_argument("--vc_only_bn_address", type=str)
parser.add_argument("--skip_prompts", type=str, default="")
parser.add_argument("--install_config", type=str, choices=valid_install_configs, default="")
args = parser.parse_args()

# 4. Interactive Prompts
if not args.network and not args.skip_prompts:
    index = SelectionMenu.get_selection(valid_networks, title='Validator Install Quickstart :: CoinCashew.com', subtitle='Installs Besu EL / Teku BN / Teku VC / MEVboost\nSelect Ethereum network:')
    if index == len(valid_networks): exit(0)
    eth_network = valid_networks[index].lower()
else:
    eth_network = args.network.lower()

if not args.install_config and not args.skip_prompts:
    if eth_network == "sepolia":
        install_config = valid_install_configs[1]
    else:
        index = SelectionMenu.get_selection(valid_install_configs, title='Validator Install Quickstart :: CoinCashew.com', subtitle='What type of installation would you like?\nSelect your type:', show_exit_option=False)
        install_config = valid_install_configs[index]
else:
    install_config = args.install_config

# Resolve Role Flags
MEVBOOST_ENABLED = False
VALIDATOR_ENABLED = False
VALIDATOR_ONLY = False
NODE_ONLY = False

if eth_network == "sepolia":
    NODE_ONLY = True
else:
    match install_config:
        case "Solo Staking Node":
            MEVBOOST_ENABLED = True
            VALIDATOR_ENABLED = True
        case "Full Node Only":
            NODE_ONLY = True
        case "Lido CSM Staking Node":
            MEVBOOST_ENABLED = True
            VALIDATOR_ENABLED = True
        case "Lido CSM Validator Client Only":
            MEVBOOST_ENABLED = True
            VALIDATOR_ENABLED = True
            VALIDATOR_ONLY = True
        case "Validator Client Only":
            MEVBOOST_ENABLED = True
            VALIDATOR_ENABLED = True
            VALIDATOR_ONLY = True
        case "Failover Staking Node":
            MEVBOOST_ENABLED = True

# Lido CSM specific logic...
if install_config in ["Lido CSM Staking Node", "Lido CSM Validator Client Only"]:
    GRAFFITI = os.getenv('CSM_GRAFFITI')
    MEV_MIN_BID = os.getenv('CSM_MEV_MIN_BID')
    if eth_network == "mainnet":
        FEE_RECIPIENT_ADDRESS = os.getenv('CSM_FEE_RECIPIENT_ADDRESS_MAINNET')
    elif eth_network == "holesky":
        FEE_RECIPIENT_ADDRESS = os.getenv('CSM_FEE_RECIPIENT_ADDRESS_HOLESKY')
    elif eth_network == "hoodi":
        FEE_RECIPIENT_ADDRESS = os.getenv('CSM_FEE_RECIPIENT_ADDRESS_HOODI')

if eth_network == "ephemery":
    MEVBOOST_ENABLED = False

# Prompt for Fee Recipient if needed
if not NODE_ONLY and not FEE_RECIPIENT_ADDRESS and not args.skip_prompts:
    while True:
        FEE_RECIPIENT_ADDRESS = Screen().input(f'Enter your Ethereum address (aka Fee Recipient Address)\n > ')
        if common.is_valid_eth_address(FEE_RECIPIENT_ADDRESS): break

# Prompt for Beacon Node Address if needed
bn_address = ""
if VALIDATOR_ONLY and not args.vc_only_bn_address and not args.skip_prompts:
    while True:
        bn_address = Screen().input(f'\nEnter your consensus client (beacon node) address.\nExample: http://192.168.1.123:5052\n > ')
        if common.validate_beacon_node_address(bn_address): break
else:
    bn_address = args.vc_only_bn_address

# Sync URLs
sync_urls = globals().get(f"{eth_network}_sync_urls", mainnet_sync_urls)
sync_url = random.choice(sync_urls)[1]

# 5. Execution
common.setup_node(args.jwtsecret, VALIDATOR_ONLY)

if eth_network == "ephemery":
    common.setup_ephemery_network("ephemery-testnet/ephemery-genesis")

mev_ver = ""
mev_path = ""
if MEVBOOST_ENABLED and not VALIDATOR_ONLY:
    mev_ver, mev_path = mevboost.install_mevboost(eth_network, MEV_MIN_BID, globals().get(f"{eth_network}_relay_options", []))

besu_ver = ""
besu_path = ""
if not VALIDATOR_ONLY:
    besu_ver, besu_path = besu.download_and_install_besu(
        eth_network, args.el_p2p_port, args.el_rpc_port, 
        args.el_max_peers, args.jwtsecret
    )

teku_ver = teku.download_teku(eth_network)
cl_path = ""
val_path = ""

if not VALIDATOR_ONLY:
    fee_params = f'--validators-proposer-default-fee-recipient={FEE_RECIPIENT_ADDRESS}'
    mev_params = '--validators-builder-registration-default-enabled=true --builder-endpoint=http://127.0.0.1:18550' if MEVBOOST_ENABLED else ''
    cl_path = teku.install_teku_bn(
        eth_network, sync_url, args.jwtsecret, 
        args.cl_rest_port, args.cl_p2p_port, args.cl_max_peers,
        fee_parameters=fee_params, mev_parameters=mev_params
    )

if VALIDATOR_ENABLED:
    fee_params = f'--validators-proposer-default-fee-recipient={FEE_RECIPIENT_ADDRESS}'
    mev_params = '--validators-builder-registration-default-enabled=true' if MEVBOOST_ENABLED else ''
    bn_addr = f'--beacon-node-api-endpoint={bn_address}' if VALIDATOR_ONLY else f'--beacon-node-api-endpoint=http://{CL_IP_ADDRESS}:{args.cl_rest_port}'
    
    val_path = teku.install_teku_vc(
        eth_network, args.graffiti, bn_addr, fee_params, mev_params
    )

# 6. Finish
common.finish_install(
    install_config, eth_network, sync_url,
    "besu", besu_ver, besu_path,
    "teku", teku_ver, cl_path,
    MEVBOOST_ENABLED, mev_ver, mev_path,
    VALIDATOR_ENABLED, val_path,
    VALIDATOR_ONLY, bn_address, NODE_ONLY, FEE_RECIPIENT_ADDRESS,
    args.skip_prompts, args.cl_rest_port
)
