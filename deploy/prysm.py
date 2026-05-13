import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_prysm_bn_service, generate_prysm_vc_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, get_machine_architecture, setup_client_user_and_dir
from client_requirements import validate_version_for_network

def download_prysm(eth_network: str) -> str:
    binary_arch = get_machine_architecture()

    # Create User and directories
    setup_client_user_and_dir("consensus", "prysm")
    setup_client_user_and_dir("validator", "prysm_validator")

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/prysmaticlabs/prysm/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    pr_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('prysm', pr_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    assets = response.json()['assets']
    bn_download_url = None
    vc_download_url = None
    bn_filename = None
    vc_filename = None
    
    for asset in assets:
        if asset['name'] == f'beacon-chain-{pr_version}-linux-{binary_arch}':
            bn_download_url = asset['browser_download_url']
            bn_filename = asset['name']
        elif asset['name'] == f'validator-{pr_version}-linux-{binary_arch}':
            vc_download_url = asset['browser_download_url']
            vc_filename = asset['name']

    if bn_download_url is None or vc_download_url is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the beacon node
    print(f">> Downloading Prysm Beacon Node > URL: {bn_download_url}")
    bn_download_path = f"{DOWNLOAD_DIR}/{bn_filename}"
    
    try:
        response = requests.get(bn_download_url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024
        t = tqdm(total=total_size, unit='B', unit_scale=True)
        with open(bn_download_path, "wb") as f:
            for chunk in response.iter_content(block_size):
                if chunk:
                    t.update(len(chunk))
                    f.write(chunk)
        t.close()
        print(f">> Successfully downloaded: {bn_filename}")
    except requests.exceptions.RequestException as e:
        print(f"Error: Unable to download file. Try again later. {e}")
        exit(1)

    # Download the validator client
    print(f">> Downloading Prysm Validator Client > URL: {vc_download_url}")
    vc_download_path = f"{DOWNLOAD_DIR}/{vc_filename}"
    
    try:
        response = requests.get(vc_download_url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024
        t = tqdm(total=total_size, unit='B', unit_scale=True)
        with open(vc_download_path, "wb") as f:
            for chunk in response.iter_content(block_size):
                if chunk:
                    t.update(len(chunk))
                    f.write(chunk)
        t.close()
        print(f">> Successfully downloaded: {vc_filename}")
    except requests.exceptions.RequestException as e:
        print(f"Error: Unable to download file. Try again later. {e}")
        exit(1)

    # Move the binary to /usr/local/bin/ using sudo
    subprocess.run(["sudo", "mv", bn_download_path, f"{INSTALL_DIR}/prysm-beacon-chain"])
    subprocess.run(["sudo", "chmod", "+x", f"{INSTALL_DIR}/prysm-beacon-chain"])
    
    subprocess.run(["sudo", "mv", vc_download_path, f"{INSTALL_DIR}/prysm-validator"])
    subprocess.run(["sudo", "chmod", "+x", f"{INSTALL_DIR}/prysm-validator"])

    return pr_version

def install_prysm_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                     cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                     fee_parameters: str = '', mev_parameters: str = '') -> str:
    service_content = generate_prysm_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_prysm_vc(pr_version: str, eth_network: str, cl_rest_port: str, graffiti: str, beacon_node_address: str,
                     fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Prysm validator client service file."""
    service_content = generate_prysm_vc_service(
        eth_network, graffiti, beacon_node_address,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
