import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_reth_service
from deploy.common import write_service_file, get_raw_architecture, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir
from client_requirements import validate_version_for_network
from typing import Tuple, Optional

def download_and_install_reth(eth_network: str, el_p2p_port: str, el_p2p_port_2: str,
                                el_rpc_port: str, el_max_peer_count: str, jwtsecret_path: str,
                                network_override: Optional[str] = None, sync_parameters: str = '') -> Tuple[str, str]:
    """Download and install Reth binary and service.

    Returns:
        reth_version: The version string of the installed Reth
        service_file_path: The path to the created service file
    """
    binary_arch = get_raw_architecture()

    # Create User and directories
    setup_client_user_and_dir("execution", "reth")

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/paradigmxyz/reth/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    reth_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('reth', reth_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    assets = response.json()['assets']
    download_url = None
    filename = None
    # Reth asset: reth-v1.2.3-x86_64-unknown-linux-gnu.tar.gz
    for asset in assets:
        if asset['name'].endswith(f'{binary_arch}-unknown-linux-gnu.tar.gz'):
            download_url = asset['browser_download_url']
            filename = asset['name']
            break

    if download_url is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Reth > URL: {download_url}")
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

    # Extract the binary to /usr/local/bin/ using sudo
    # Reth tarball contains just the binary at root
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}"])

    # Find the extracted reth binary and rename it
    subprocess.run(["sudo", "sh", "-c", "mv /usr/local/bin/reth-* /usr/local/bin/reth"])

    # Ensure +x permissions
    subprocess.run(["sudo", "chmod", "a+x", f"{INSTALL_DIR}/reth"])

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_reth_service(
        eth_network, el_p2p_port, el_p2p_port_2, el_rpc_port, 
        el_max_peer_count, jwtsecret_path, network_override, sync_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return reth_version, service_file_path
