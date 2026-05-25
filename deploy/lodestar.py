import os
import requests
import subprocess
from tqdm import tqdm
from typing import Tuple, Optional
from deploy.service_generators import generate_lodestar_bn_service, generate_lodestar_vc_service
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir
from client_requirements import validate_version_for_network

def download_lodestar(eth_network: str) -> str:
    """Download and install Lodestar binary.

    Args:
        eth_network: Network name.

    Returns:
        Installed Lodestar version.
    """
    binary_arch = get_machine_architecture() # Use amd64 for Lodestar

    # Create User and directories
    setup_client_user_and_dir("consensus", "lodestar")
    setup_client_user_and_dir("validator", "lodestar_validator")

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/ChainSafe/lodestar/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    lodestar_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('lodestar', lodestar_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    assets = response.json()['assets']
    download_url = None
    filename = None
    # Lodestar asset: lodestar-v1.24.0-linux-amd64.tar.gz
    for asset in assets:
        if asset['name'].endswith(f'linux-{binary_arch}.tar.gz'):
            download_url = asset['browser_download_url']
            filename = asset['name']
            break

    if download_url is None:
        print(f"Error: Could not find the download URL for the latest release (looked for linux-{binary_arch}.tar.gz).")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Lodestar > URL: {download_url}")
    download_path = f"{DOWNLOAD_DIR}/{filename}"

    try:
        # Download the file
        response = requests.get(download_url, stream=True)
        response.raise_for_status()  # Raise an exception for HTTP errors
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024
        t = tqdm(total=total_size, unit='B', unit_scale=True)

        with open(download_path, "wb") as f:
            for chunk in response.iter_content(block_size):
                if chunk:
                    t.update(len(chunk))
                    f.write(chunk)
        t.close()
        print(f">> Successfully downloaded: {filename}")

    except requests.exceptions.RequestException as e:
        print(f"Error: Unable to download file. Try again later. {e}")
        exit(1)

    # The archive usually unpacks a lodestar directory or bare files.
    # We want the binary to end up at /usr/local/bin/lodestar/lodestar
    subprocess.run(["sudo", "mkdir", "-p", f"{INSTALL_DIR}/lodestar"])
    subprocess.run(["sudo", "mkdir", "-p", "/tmp/lodestar_extract"])
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/tmp/lodestar_extract"])
    # Move the lodestar binary correctly
    os.system("if [ -f /tmp/lodestar_extract/lodestar ]; then sudo mv /tmp/lodestar_extract/lodestar /usr/local/bin/lodestar/lodestar; fi")
    os.system("if [ -f /tmp/lodestar_extract/bin/lodestar ]; then sudo mv /tmp/lodestar_extract/bin/lodestar /usr/local/bin/lodestar/lodestar; fi")

    # Remove the tar file and temporary extraction directory
    os.remove(download_path)
    subprocess.run(["sudo", "rm", "-rf", "/tmp/lodestar_extract"])
    return lodestar_version

def install_lodestar_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                       cl_rest_port: str, cl_p2p_port: str, cl_max_peer_count: str,
                       fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Lodestar beacon node service file.

    Args:
        eth_network: Network name.
        checkpoint_sync_url: Checkpoint sync URL.
        jwtsecret_path: Path to JWT secret file.
        cl_rest_port: Consensus client REST port.
        cl_p2p_port: Consensus client P2P port.
        cl_max_peer_count: Consensus client max peer count.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    service_content = generate_lodestar_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_lodestar_vc(lodestar_version: str, eth_network: str, cl_rest_port: str, graffiti: str, bn_addr_flag: str,
                       fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Lodestar validator client service file.

    Args:
        lodestar_version: Installed Lodestar version.
        eth_network: Network name.
        cl_rest_port: Consensus client REST port.
        graffiti: Graffiti string.
        bn_addr_flag: Beacon node address flag.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    service_content = generate_lodestar_vc_service(
        eth_network, graffiti, bn_addr_flag,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
