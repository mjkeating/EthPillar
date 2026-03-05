import os
import requests
import subprocess
from tqdm import tqdm
from typing import Tuple
from deploy.service_generators import generate_erigon_service
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR
from client_requirements import validate_version_for_network

def download_and_install_erigon(eth_network: str, el_p2p_port: str, el_rpc_port: str, el_max_peer_count: str, 
                                 jwtsecret_path: str, cl_p2p_port: str, cl_rest_port: str, cl_max_peer_count_cl: str,
                                 checkpoint_sync_url: str, mev_parameters: str = '') -> Tuple[str, str]:
    """Download and install Erigon binary and service.

    Returns:
        erigon_version: The version string of the installed Erigon
        service_file_path: The path to the created service file
    """
    binary_arch = get_machine_architecture()

    # Create User and directories
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/erigon"])
    subprocess.run(["sudo", "chown", "-R", "execution:execution", "/var/lib/erigon"])

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/erigontech/erigon/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    erigon_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('erigon', erigon_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    # Search for the asset
    assets = response.json()['assets']
    download_url = None
    filename = None
    for asset in assets:
        if asset['name'].endswith(f'linux_{binary_arch}.tar.gz'):
             download_url = asset['browser_download_url']
             filename = asset['name']
             break

    if download_url is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Erigon > URL: {download_url}")
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

    # Extract the binary using sudo
    # Erigon tarball typically contains a folder, so we strip one component and extract to /usr/local/bin
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/usr/local/bin", "--strip-components=1"])
    # Ensure it's executable
    subprocess.run(["sudo", "chmod", "a+x", "/usr/local/bin/erigon"])

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_erigon_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count,
        jwtsecret_path, cl_p2p_port, cl_rest_port, cl_max_peer_count_cl,
        checkpoint_sync_url, mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return erigon_version, service_file_path
