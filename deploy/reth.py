import os
import requests
import subprocess
import tarfile
import platform
from tqdm import tqdm
from deploy.service_generators import generate_reth_service
from deploy.common import write_service_file, get_computer_platform
from client_requirements import validate_version_for_network

def download_and_install_reth(eth_network, el_p2p_port, el_p2p_port_2, 
                              el_rpc_port, el_max_peer_count, jwtsecret_path,
                              network_override=None, sync_parameters=''):
    """Download and install Reth binary and service.

    Returns:
        reth_version: The version string of the installed Reth
        service_file_path: The path to the created service file
    """
    platform_arch = get_computer_platform()

    # Create User and directories
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/reth"])
    subprocess.run(["sudo", "chown", "-R", "execution:execution", "/var/lib/reth"])

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

    # Search for the asset
    assets = response.json()['assets']
    download_url = None
    tar_filename = None
    for asset in assets:
        if asset['name'].endswith(f'{platform.machine().lower()}-unknown-{platform_arch.lower()}-gnu.tar.gz') and asset['name'].startswith(f"reth-{reth_version}"):
            download_url = asset['browser_download_url']
            tar_filename = asset['name']
            break

    if download_url is None or tar_filename is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Reth > URL: {download_url}")

    try:
        # Download the file
        response = requests.get(download_url, stream=True)
        response.raise_for_status()  # Raise an exception for HTTP errors
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024
        t = tqdm(total=total_size, unit='B', unit_scale=True)

        # Save the binary to the home folder
        with open(f"{tar_filename}", "wb") as f:
            for chunk in response.iter_content(block_size):
                if chunk:
                    t.update(len(chunk))
                    f.write(chunk)
        t.close()
        print(f">> Successfully downloaded: {tar_filename}")

    except requests.exceptions.RequestException as e:
        print(f"Error: Unable to download file. Try again later. {e}")
        exit(1)

    # Extract the binary to the home folder
    with tarfile.open(tar_filename, "r:gz") as tar:
        tar.extractall()

    # Move the binary to /usr/local/bin using sudo
    os.system(f"sudo mv reth /usr/local/bin")

    # Remove the downloaded .tar.gz file
    os.remove(f"{tar_filename}")

    # Ensure +x permissions, update owner
    subprocess.run(["sudo", "chmod", "a+x", "/usr/local/bin/reth"])
    subprocess.run(["sudo", "chown", "execution:execution", "/usr/local/bin/reth"])

    # Generate Service File Content
    service_content = generate_reth_service(
        eth_network, el_p2p_port, el_p2p_port_2, el_rpc_port, 
        el_max_peer_count, jwtsecret_path, network_override, sync_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return reth_version, service_file_path
