import os
import re
import platform
import subprocess
import requests
import json
import tarfile
import tempfile
from consolemenu import PromptUtils, Screen
from typing import Optional


INSTALL_DIR = "/usr/local/bin"
DOWNLOAD_DIR = "/tmp"
BASE_DATA_DIR = "/var/lib"

def setup_client_user_and_dir(user: str, client_name: str) -> None:
    """Create a system user and data directory with proper permissions.

    Args:
        user: The username to create.
        client_name: The directory name (under BASE_DATA_DIR)
    """
    data_dir = os.path.join(BASE_DATA_DIR, client_name)

    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", user], 
                   stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["sudo", "mkdir", "-p", data_dir], check=True)
    subprocess.run(["sudo", "chown", "-R", f"{user}:{user}", data_dir], check=True)


def clear_screen() -> None:
    """Clear the terminal screen based on the operating system."""
    if os.name == 'posix':  # Unix-based systems (e.g., Linux, macOS)
        os.system('clear')
    elif os.name == 'nt':   # Windows
        os.system('cls')


def get_machine_architecture() -> str:
    """Get the machine architecture mapped to amd64 or arm64.

    Returns:
        Mapped architecture string.
    """
    machine_arch = platform.machine()
    if machine_arch == "x86_64":
        return "amd64"
    elif machine_arch == "aarch64":
        return "arm64"
    else:
        print(f'Unsupported machine architecture: {machine_arch}')
        exit(1)


def get_raw_architecture() -> str:
    """Get the raw machine architecture string."""
    return platform.machine()


def get_computer_platform() -> str:
    """Get the operating system platform name.

    Returns:
        Platform name string (e.g. 'Linux').
    """
    platform_name = platform.system()
    if platform_name == "Linux":
        return platform_name
    else:
        print(f'Unsupported platform: {platform_name}')
        exit(1)


def is_valid_eth_address(address: str) -> bool:
    """Validate if a string is a valid Ethereum address.

    Args:
        address: The address string to validate.

    Returns:
        True if valid, False otherwise.
    """
    pattern = re.compile("^0x[a-fA-F0-9]{40}$")
    return bool(pattern.match(address))


def validate_beacon_node_address(ip_port: str) -> bool:
    """Validate if a string is a valid beacon node address URL.

    Args:
        ip_port: The URL string to validate.

    Returns:
        True if valid, False otherwise.
    """
    pattern = r"^(http|https|ws):\/\/((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(:?\d{1,5})?$"
    return bool(re.match(pattern, ip_port))

VALID_NETWORKS = ['MAINNET', 'HOODI', 'EPHEMERY', 'HOLESKY', 'SEPOLIA']

def network_type(s: str) -> str:
    """Argparse type for case-insensitive network selection.

    Args:
        s: The network name string.

    Returns:
        Uppercase network name.

    Raises:
        argparse.ArgumentTypeError: If the network is invalid.
    """
    s_upper = s.upper()
    if s_upper not in VALID_NETWORKS:
         import argparse
         raise argparse.ArgumentTypeError(f"Invalid network: {s}. Choose from {VALID_NETWORKS}")
    return s_upper


def setup_ephemery_network(genesis_repository: str) -> None:
    """Setup ephemery network by downloading genesis files.

    Args:
        genesis_repository: GitHub repository for genesis files.
    """
    testnet_dir = "/opt/ethpillar/testnet"

    def get_github_release(repo: str) -> Optional[str]:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        response = requests.get(url)
        if response.status_code == 200:
            data = json.loads(response.text)
            return data.get('tag_name')
        else:
            return None

    def download_genesis_release(genesis_release: str) -> None:
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


def setup_node(jwt_secret_path: str, validator_only: bool = False) -> None:
    """Setup node dependencies and JWT secret.

    Args:
        jwt_secret_path: Path to save the JWT secret.
        validator_only: If True, only setup validator-specific parts.
    """
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

def write_service_file(content: str, target_path: str, temp_filename: str = 'temp.service') -> None:
    """Write service content to a target path using a temporary file.

    Args:
        content: The service file content.
        target_path: The final destination path.
        temp_filename: Temporary filename to use.
    """
    # Prepend PID to avoid race conditions when multiple containers share the same mapped volume directory
    actual_temp_filename = f"{os.getpid()}_{temp_filename}"
    with open(actual_temp_filename, 'w') as f:
        f.write(content)
    os.system(f'sudo cp {actual_temp_filename} {target_path}')
    try:
        os.remove(actual_temp_filename)
    except FileNotFoundError:
        pass

def finish_install(install_config: str, eth_network: str, sync_url: str,
                   execution_client: Optional[str], execution_version: Optional[str], execution_service_path: Optional[str],
                   consensus_client: Optional[str], consensus_version: Optional[str], consensus_service_path: Optional[str],
                   mevboost_enabled: bool, mevboost_version: Optional[str], mevboost_service_path: Optional[str],
                   validator_enabled: bool, validator_service_path: Optional[str],
                   validator_only: bool, bn_address: Optional[str], node_only: bool, fee_recipient_address: Optional[str],
                   skip_prompts: bool, cl_rest_port: str,
                   vc_name: str = '', vc_ver: str = '') -> None:
    """Display installation summary and optionally start/enable services.

    Args:
        install_config: Formatted configuration string.
        eth_network: Network name.
        sync_url: Checkpoint sync URL.
        execution_client: Execution client name.
        execution_version: Execution client version.
        execution_service_path: Path to execution service file.
        consensus_client: Consensus client name.
        consensus_version: Consensus client version.
        consensus_service_path: Path to consensus service file.
        mevboost_enabled: Whether MEV-Boost is enabled.
        mevboost_version: MEV-Boost version.
        mevboost_service_path: Path to MEV-Boost service file.
        validator_enabled: Whether validator is enabled.
        validator_service_path: Path to validator service file.
        validator_only: Whether this is a validator-only install.
        bn_address: Beacon node address.
        node_only: Whether this is a node-only install.
        fee_recipient_address: Fee recipient address.
        skip_prompts: Whether to skip interactive prompts.
        cl_rest_port: Consensus client REST port.
    """
    
    # Reload the systemd daemon
    try:
        subprocess.run(['sudo', 'systemctl', 'daemon-reload'], capture_output=True)
    except Exception:
        pass

    print(f'##########################\n')
    print(f'## Installation Summary ##\n')
    print(f'##########################\n')

    print(f'Installation Configuration: \n{install_config}\n')

    if execution_client and execution_version:
        print(f'{execution_client.capitalize()} Version: \n{execution_version}\n')

    if consensus_client and consensus_version:
        print(f'{consensus_client.capitalize()} Version: \n{consensus_version}\n')

    if vc_name and vc_ver and vc_name != consensus_client:
        print(f'{vc_name.capitalize()} (VC) Version: \n{vc_ver}\n')

    if mevboost_enabled and not validator_only:
        print(f'Mevboost Version: \n{mevboost_version}\n')

    print(f'Network: {eth_network.upper()}\n')

    if not validator_only:
        print(f'CheckPointSyncURL: {sync_url}\n')

    if validator_only and bn_address:
        print(f'Beacon Node Address: {bn_address}\n')
        target_dir = os.path.expanduser("~/git/ethpillar")
        if os.path.exists(target_dir):
            os.chdir(target_dir)
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
    if not skip_prompts and not validator_only:
        e_client = (execution_client or "execution").capitalize()
        c_client = (consensus_client or "consensus").capitalize()
        message = f"\nInstallation successful!\nSyncing a {c_client}/{e_client} node for validator duties can be as quick as a few hours.\nWould you like to start syncing now?"
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
