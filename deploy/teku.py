import os
import requests
import subprocess
import tarfile
from tqdm import tqdm
from deploy.service_generators import generate_teku_bn_service, generate_teku_vc_service
from deploy.common import write_service_file
from client_requirements import validate_version_for_network

def download_teku(eth_network):
    """Download Teku binary.

    Returns:
        teku_version: The version string of the downloaded Teku
    """
    # Change to the home folder
    os.chdir(os.path.expanduser("~"))

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/ConsenSys/teku/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    teku_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('teku', teku_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = f'https://artifacts.consensys.net/public/teku/raw/names/teku.tar.gz/versions/{teku_version}/teku-{teku_version}.tar.gz'

    # Download the latest release binary
    print(f">> Downloading Teku > URL: {download_url}")

    try:
        # Download the file
        response = requests.get(download_url, stream=True)
        response.raise_for_status()  # Raise an exception for HTTP errors
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024
        t = tqdm(total=total_size, unit='B', unit_scale=True)

        # Save the binary to the home folder
        with open("teku.tar.gz", "wb") as f:
            for chunk in response.iter_content(block_size):
                if chunk:
                    t.update(len(chunk))
                    f.write(chunk)
        t.close()
        print(f">> Successfully downloaded: teku-{teku_version}.tar.gz")

    except requests.exceptions.RequestException as e:
        print(f"Error: Unable to download file. Try again later. {e}")
        exit(1)

    # Extract the binary to the home folder
    with tarfile.open('teku.tar.gz', 'r:gz') as tar:
        tar.extractall()

    # Find the extracted folder
    extracted_folder = None
    for item in os.listdir():
        if item.startswith(f'teku-'):
            extracted_folder = item
            break

    if extracted_folder is None:
        print("Error: Could not find the extracted folder.")
        exit(1)

    # Move the binary to /usr/local/bin using sudo
    os.system(f"sudo mv {extracted_folder} /usr/local/bin/teku")

    # Remove the teku.tar.gz file
    os.remove('teku.tar.gz')

    return teku_version

def install_teku_bn(eth_network, sync_url, jwtsecret_path, 
                    cl_rest_port, cl_p2p_port, cl_max_peer_count,
                    fee_parameters='', mev_parameters=''):
    """Install Teku beacon node service."""
    # Create data paths, service user, assign ownership permissions
    subprocess.run(['sudo', 'mkdir', '-p', '/var/lib/teku'])
    subprocess.run(['sudo', 'chmod', '700', '/var/lib/teku'])
    subprocess.run(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'consensus'])
    subprocess.run(['sudo', 'chown', '-R', 'consensus:consensus', '/var/lib/teku'])

    service_content = generate_teku_bn_service(
        eth_network, sync_url, jwtsecret_path, cl_rest_port, 
        cl_p2p_port, cl_max_peer_count, fee_parameters, mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_teku_vc(eth_network, graffiti, beacon_node_address,
                    fee_parameters='', mev_parameters=''):
    """Install Teku validator client service."""
    # Create data paths, service user, assign ownership permissions
    subprocess.run(['sudo', 'mkdir', '-p', '/var/lib/teku_validator'])
    subprocess.run(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'validator'])
    subprocess.run(['sudo', 'chown', '-R', 'validator:validator', '/var/lib/teku_validator'])
    subprocess.run(['sudo', 'chmod', '700', '/var/lib/teku_validator'])

    service_content = generate_teku_vc_service(
        eth_network, graffiti, beacon_node_address, fee_parameters, mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
