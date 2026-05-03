import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_lighthouse_bn_service, generate_lighthouse_vc_service
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR, INSTALL_DIR, get_raw_architecture, setup_client_user_and_dir
from client_requirements import validate_version_for_network

def download_lighthouse(eth_network: str) -> str:
    binary_arch = get_raw_architecture()

    # Create User and directories
    setup_client_user_and_dir("consensus", "lighthouse")
    setup_client_user_and_dir("validator", "lighthouse_validator")

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/sigp/lighthouse/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    lh_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('lighthouse', lh_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    assets = response.json()['assets']
    download_url = None
    filename = None
    # Lighthouse asset: lighthouse-v6.0.1-x86_64-unknown-linux-gnu.tar.gz
    for asset in assets:
        if asset['name'].endswith(f'{binary_arch}-unknown-linux-gnu.tar.gz'):
            download_url = asset['browser_download_url']
            filename = asset['name']
            break

    if download_url is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Lighthouse > URL: {download_url}")
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
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}"])

    # Remove the tar file
    os.remove(download_path)
    return lh_version

def install_lighthouse_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                         cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                         fee_parameters: str = '', mev_parameters: str = '') -> str:
    service_content = generate_lighthouse_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_lighthouse_vc(lh_version: str, eth_network: str, cl_rest_port: str, graffiti: str, beacon_node_address: str,
                         fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Lighthouse validator client service file.

    Args:
        lh_version: Installed Lighthouse version.
        eth_network: Network name.
        cl_rest_port: Consensus client REST port.
        graffiti: Graffiti string.
        beacon_node_address: Beacon node address URL.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    service_content = generate_lighthouse_vc_service(
        eth_network, graffiti, beacon_node_address,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
