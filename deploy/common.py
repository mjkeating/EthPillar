import os
import re
import sys
import platform
import subprocess
import requests
import json
import tarfile
import tempfile
import random
from consolemenu import *
from consolemenu.items import *

INSTALL_DIR = "/usr/local/bin"
DOWNLOAD_DIR = "/tmp"

def clear_screen():
    if os.name == 'posix':  # Unix-based systems (e.g., Linux, macOS)
        os.system('clear')
    elif os.name == 'nt':   # Windows
        os.system('cls')

def get_machine_architecture():
    machine_arch = platform.machine()
    if machine_arch == "x86_64":
        return "amd64"
    elif machine_arch == "aarch64":
        return "arm64"
    else:
        print(f'Unsupported machine architecture: {machine_arch}')
        exit(1)

def get_raw_architecture():
    return platform.machine()

def get_computer_platform():
    platform_name = platform.system()
    if platform_name == "Linux":
        return platform_name
    else:
        print(f'Unsupported platform: {platform_name}')
        exit(1)

def is_valid_eth_address(address):
    pattern = re.compile("^0x[a-fA-F0-9]{40}$")
    return bool(pattern.match(address))

def validate_beacon_node_address(ip_port):
    pattern = r"^(http|https|ws):\/\/((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(:?\d{1,5})?$"
    return bool(re.match(pattern, ip_port))

VALID_NETWORKS = ['MAINNET', 'HOODI', 'EPHEMERY', 'HOLESKY', 'SEPOLIA']

def network_type(s):
    """Argparse type for case-insensitive network selection."""
    s_upper = s.upper()
    if s_upper not in VALID_NETWORKS:
         import argparse
         raise argparse.ArgumentTypeError(f"Invalid network: {s}. Choose from {VALID_NETWORKS}")
    return s_upper

def setup_ephemery_network(genesis_repository):
    testnet_dir = "/opt/ethpillar/testnet"

    def get_github_release(repo):
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        response = requests.get(url)
        if response.status_code == 200:
            data = json.loads(response.text)
            return data.get('tag_name')
        else:
            return None

    def download_genesis_release(genesis_release):
        # remove old genesis and setup dir
        if os.path.exists(testnet_dir):
            subprocess.run(['sudo', 'rm', '-rf', testnet_dir], check=True)
        subprocess.run(['sudo', 'mkdir', '-p', testnet_dir], check=True)
        subprocess.run(['sudo', 'chmod', '-R', '755', testnet_dir], check=True)

        # get latest genesis
        url = f"https://github.com/{genesis_repository}/releases/download/{genesis_release}/testnet-all.tar.gz"
        print(f">> Downloading {genesis_release} genesis files > URL: {url}")
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            temp_dir = tempfile.mkdtemp()
            with tarfile.open(fileobj=response.raw, mode='r|gz') as tar:
                tar.extractall(f"{temp_dir}")
            os.system(f"sudo mv {temp_dir}/* {testnet_dir}")
            print(f">> Successfully downloaded {genesis_release} genesis files")
        else:
            print("Failed to download genesis release")

    genesis_release = get_github_release(genesis_repository)
    if genesis_release:
        download_genesis_release(genesis_release)
    else:
        print(f"Failed to retrieve genesis release for {genesis_repository}")

def setup_node(jwt_secret_path, validator_only=False):
    if not validator_only:
        # Create JWT directory
        subprocess.run([f'sudo mkdir -p $(dirname {jwt_secret_path})'], shell=True)

        # Generate random hex string and save to file
        rand_hex = subprocess.run(['openssl', 'rand', '-hex', '32'], stdout=subprocess.PIPE)
        subprocess.run([f'sudo tee {jwt_secret_path}'], input=rand_hex.stdout, stdout=subprocess.DEVNULL, shell=True)

    # Update and upgrade packages
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'update'])
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'upgrade'])

    # Autoremove packages
    subprocess.run(['sudo', 'apt', '-y', '-qq' , 'autoremove'])

    # Chrony timesync package
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'install', 'chrony'])

def write_service_file(content, target_path, temp_filename='temp.service'):
    with open(temp_filename, 'w') as f:
        f.write(content)
    os.system(f'sudo cp {temp_filename} {target_path}')
    os.remove(temp_filename)

def finish_install(install_config, eth_network, sync_url, 
                   execution_client, execution_version, execution_service_path,
                   consensus_client, consensus_version, consensus_service_path,
                   mevboost_enabled, mevboost_version, mevboost_service_path,
                   validator_enabled, validator_service_path,
                   validator_only, bn_address, node_only, fee_recipient_address,
                   skip_prompts, cl_rest_port):
    
    # Reload the systemd daemon
    try:
        subprocess.run(['sudo', 'systemctl', 'daemon-reload'], capture_output=True)
    except Exception:
        pass

    print(f'##########################\n')
    print(f'## Installation Summary ##\n')
    print(f'##########################\n')

    print(f'Installation Configuration: \n{install_config}\n')

    if execution_client:
        print(f'{execution_client.capitalize()} Version: \n{execution_version}\n')

    if consensus_client:
        print(f'{consensus_client.capitalize()} Version: \n{consensus_version}\n')

    if mevboost_enabled and not validator_only:
        print(f'Mevboost Version: \n{mevboost_version}\n')

    print(f'Network: {eth_network.upper()}\n')

    if not validator_only:
        print(f'CheckPointSyncURL: {sync_url}\n')

    if validator_only and bn_address:
        print(f'Beacon Node Address: {bn_address}\n')
        # This part seems specific to some scripts but not others? 
        # Actually it's in multiple.
        os.chdir(os.path.expanduser("~/git/ethpillar"))
        os.system(f'cp .env.overrides.example .env.overrides')

    if not node_only:
        print(f'Validator Fee Recipient Address: {fee_recipient_address}\n')

    print(f'Systemd service files created:')
    if not validator_only:
        if consensus_service_path:
            print(f'\n{consensus_service_path}')
        if execution_service_path:
            print(f'{execution_service_path}')
    if validator_enabled:
        if validator_service_path:
            print(f'{validator_service_path}')
    if mevboost_enabled and not validator_only:
        if mevboost_service_path:
            print(f'{mevboost_service_path}')

    if skip_prompts:
        print(f'\nNon-interactive install successful! Skipped prompts.')
        exit(0)

    # Prompt to start services
    if not validator_only:
        message = f"\nInstallation successful!\nSyncing a {consensus_client.capitalize()}/{execution_client.capitalize()} node for validator duties can be as quick as a few hours.\nWould you like to start syncing now?"
        answer = PromptUtils(Screen()).prompt_for_yes_or_no(message)
        if answer:
            services = []
            if execution_service_path:
                services.append('execution')
            if consensus_service_path:
                services.append('consensus')
            if services:
                os.system(f'sudo systemctl start {" ".join(services)} > /dev/null 2>&1')
            if mevboost_enabled:
                os.system(f'sudo systemctl start mevboost > /dev/null 2>&1')

    # Prompt to enable autostart services
    if not skip_prompts:
        answer = PromptUtils(Screen()).prompt_for_yes_or_no(f"\nConfigure node to autostart:\nWould you like this node to autostart when system boots up?")
        if answer:
            if not validator_only:
                services = []
                if execution_service_path:
                    services.append('execution')
                if consensus_service_path:
                    services.append('consensus')
                if services:
                    os.system(f'sudo systemctl enable {" ".join(services)} > /dev/null 2>&1')
            if validator_enabled:
                os.system(f'sudo systemctl enable validator > /dev/null 2>&1')
            if mevboost_enabled and not validator_only:
                os.system(f'sudo systemctl enable mevboost > /dev/null 2>&1')

    if skip_prompts:
        exit(0)
