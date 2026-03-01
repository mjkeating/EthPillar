import os
import requests
import subprocess
import tarfile
from tqdm import tqdm
from deploy.service_generators import generate_nimbus_bn_service, generate_nimbus_vc_service
from deploy.common import write_service_file, get_machine_architecture
from client_requirements import validate_version_for_network

def download_nimbus(eth_network):
    """Download Nimbus binary.

    Returns:
        nimbus_version: The version string of the downloaded Nimbus
    """
    binary_arch = get_machine_architecture()

    # Change to the home folder
    os.chdir(os.path.expanduser("~"))

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/status-im/nimbus-eth2/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    nimbus_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('nimbus', nimbus_version, eth_network)
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
    print(f">> Downloading Nimbus > URL: {download_url}")

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
    subprocess.run(["tar", "xzf", tar_filename, "--strip-components=1"])

    # Find the extracted binary
    extracted_binary = None
    for item in os.listdir("build"):
        if item == 'nimbus_beacon_node':
            extracted_binary = 'nimbus_beacon_node'
            break

    if extracted_binary is None:
        print("Error: Could not find the nimbus_beacon_node binary.")
        exit(1)

    # Move the binary to /usr/local/bin using sudo
    os.system(f"sudo mv build/nimbus_beacon_node /usr/local/bin")
    os.system(f"sudo mv build/nimbus_validator_client /usr/local/bin")

    # Remove the nimbus.tar.gz file
    os.remove(tar_filename)
    os.system(f"rm -rf build")

    return nimbus_version

def install_nimbus_bn(eth_network, jwtsecret_path, 
                      cl_rest_port, cl_p2p_port, cl_max_peer_count,
                      network_override=None, fee_parameters='', mev_parameters=''):
    """Install Nimbus beacon node service."""
    # Create User and directories
    subprocess.run(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'consensus'])
    subprocess.run(['sudo', 'mkdir', '-p', '/var/lib/nimbus'])
    subprocess.run(['sudo', 'chown', '-R', 'consensus:consensus', '/var/lib/nimbus'])
    subprocess.run(['sudo', 'chmod', '700', '/var/lib/nimbus'])

    service_content = generate_nimbus_bn_service(
        eth_network, jwtsecret_path, cl_rest_port, 
        cl_p2p_port, cl_max_peer_count, network_override, fee_parameters, mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_nimbus_vc(eth_network, graffiti, beacon_node_address,
                      fee_parameters='', mev_parameters=''):
    """Install Nimbus validator client service."""
    # Create User and directories
    subprocess.run(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'validator'])
    subprocess.run(['sudo', 'mkdir', '-p', '/var/lib/nimbus_validator'])
    subprocess.run(['sudo', 'chown', '-R', 'validator:validator', '/var/lib/nimbus_validator'])
    subprocess.run(['sudo', 'chmod', '700', '/var/lib/nimbus_validator'])

    service_content = generate_nimbus_vc_service(
        eth_network, graffiti, beacon_node_address, fee_parameters, mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
