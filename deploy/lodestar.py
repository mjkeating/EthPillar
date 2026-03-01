import os
import requests
import subprocess
import tarfile
from tqdm import tqdm
from deploy.service_generators import generate_lodestar_bn_service, generate_lodestar_vc_service
from deploy.common import write_service_file, get_machine_architecture
from client_requirements import validate_version_for_network

def download_lodestar(eth_network):
    """Download Lodestar binary.

    Returns:
        lodestar_version: The version string of the downloaded Lodestar
    """
    binary_arch = get_machine_architecture()

    # Change to the home folder
    os.chdir(os.path.expanduser("~"))

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

    # Search for the asset
    assets = response.json()['assets']
    download_url = None
    tar_filename = None
    for asset in assets:
        if asset['name'].endswith(f'linux-{binary_arch}.tar.gz'):
             download_url = asset['browser_download_url']
             tar_filename = asset['name']
             break

    if download_url is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Lodestar > URL: {download_url}")

    try:
        # Download the file
        response = requests.get(download_url, stream=True)
        response.raise_for_status()  # Raise an exception for HTTP errors
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024
        t = tqdm(total=total_size, unit='B', unit_scale=True)

        # Save the binary to the home folder
        with open(tar_filename, "wb") as f:
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
    subprocess.run(["sudo", "mkdir", "-p", "/usr/local/bin/lodestar"])
    subprocess.run(["sudo", "tar", "xzf", tar_filename, "-C", "/usr/local/bin/lodestar", "--strip-components=1"])

    # Remove the lodestar.tar.gz file
    os.remove(tar_filename)

    return lodestar_version

def install_lodestar_bn(eth_network, sync_url, jwtsecret_path, 
                        cl_rest_port, cl_p2p_port, cl_max_peer_count,
                        network_override=None, fee_parameters='', mev_parameters=''):
    """Install Lodestar beacon node service."""
    # Create User and directories
    subprocess.run(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'consensus'])
    subprocess.run(['sudo', 'mkdir', '-p', '/var/lib/lodestar'])
    subprocess.run(['sudo', 'chown', '-R', 'consensus:consensus', '/var/lib/lodestar'])
    subprocess.run(['sudo', 'chmod', '700', '/var/lib/lodestar'])

    service_content = generate_lodestar_bn_service(
        eth_network, sync_url, jwtsecret_path, cl_rest_port, 
        cl_p2p_port, cl_max_peer_count, network_override, fee_parameters, mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_lodestar_vc(eth_network, graffiti, beacon_node_address,
                        fee_parameters='', mev_parameters=''):
    """Install Lodestar validator client service."""
    # Create User and directories
    subprocess.run(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'validator'])
    subprocess.run(['sudo', 'mkdir', '-p', '/var/lib/lodestar_validator'])
    subprocess.run(['sudo', 'chown', '-R', 'validator:validator', '/var/lib/lodestar_validator'])
    subprocess.run(['sudo', 'chmod', '700', '/var/lib/lodestar_validator'])

    service_content = generate_lodestar_vc_service(
        eth_network, graffiti, beacon_node_address, fee_parameters, mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
