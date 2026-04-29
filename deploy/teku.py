import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_teku_bn_service, generate_teku_vc_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir
from client_requirements import validate_version_for_network
from typing import Optional

def download_teku(eth_network: str) -> str:
    """Download and install Teku binary.

    Args:
        eth_network: Network name.

    Returns:
        Installed Teku version.
    """
    # Create User and directories
    setup_client_user_and_dir("consensus", "teku")
    setup_client_user_and_dir("validator", "teku_validator")

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/Consensys/teku/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    teku_version = data['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('teku', teku_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    # Consensys now hosts Teku binaries on their own artifacts server
    v_num = teku_version.lstrip("v")
    download_url = f"https://artifacts.consensys.net/public/teku/raw/names/teku.tar.gz/versions/{v_num}/teku-{v_num}.tar.gz"
    filename = f"teku-{v_num}.tar.gz"

    # Verify the artifacts URL exists, if not fallback to GitHub assets
    try:
        head_check = requests.head(download_url, allow_redirects=True)
        if head_check.status_code != 200:
            download_url = None
    except requests.RequestException:
        download_url = None

    if download_url is None:
        # Fallback to GitHub assets (for older versions or forks)
        assets = data.get('assets', [])
        for asset in assets:
            asset_name = asset['name'].lower()
            if "teku" in asset_name and asset_name.endswith(".tar.gz") and "source" not in asset_name:
                download_url = asset['browser_download_url']
                filename = asset['name']
                break

    if download_url is None:
        print(f"Error: Could not find the download URL for Teku {teku_version} on Consensys Artifacts or GitHub.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Teku > URL: {download_url}")
    download_path = f"{DOWNLOAD_DIR, INSTALL_DIR}/{filename}"

    try:
        # Download the file
        response = requests.get(download_url, stream=True)
        response.raise_for_status()
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

    # Extract the binary to /usr/local/bin/teku using sudo
    subprocess.run(["sudo", "mkdir", "-p", f"{INSTALL_DIR}/teku"])
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}/teku", "--strip-components=1"])

    # Remove the tar file
    os.remove(download_path)
    return teku_version

def install_teku_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                   cl_rest_port: str, cl_p2p_port: str, cl_max_peer_count: str,
                   fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Teku beacon node service file.

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
    # Match call in deploy-teku-besu.py (6 positional arguments)
    service_content = generate_teku_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_teku_vc(teku_version: str, eth_network: str, cl_rest_port: str, graffiti: str, bn_addr_flag: str,
                   fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Teku validator client service file.

    Args:
        teku_version: Installed Teku version.
        eth_network: Network name.
        cl_rest_port: Consensus client REST port.
        graffiti: Graffiti string.
        bn_addr_flag: Beacon node address flag.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    service_content = generate_teku_vc_service(
        eth_network, graffiti, bn_addr_flag,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
