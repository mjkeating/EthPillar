import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_lighthouse_bn_service, generate_lighthouse_vc_service
from deploy.common import write_service_file, get_machine_architecture, get_raw_architecture
from client_requirements import validate_version_for_network

def download_lighthouse(eth_network):
    binary_arch = get_raw_architecture()

    # Create User and directories
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "consensus"])
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "validator"])
    # Lighthouse needs /var/lib/lighthouse for both BN and potentially other things
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/lighthouse"])
    subprocess.run(["sudo", "chown", "-R", "consensus:consensus", "/var/lib/lighthouse"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/lighthouse_validator"])
    subprocess.run(["sudo", "chown", "-R", "validator:validator", "/var/lib/lighthouse_validator"])

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
    download_path = f"/tmp/{filename}"

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
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/usr/local/bin"])

    # Remove the tar file
    os.remove(download_path)
    return lh_version

def install_lighthouse_bn(eth_network, checkpoint_sync_url, jwtsecret_path,
                         cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
                         fee_parameters='', mev_parameters=''):
    service_content = generate_lighthouse_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_lighthouse_vc(lh_version, eth_network, cl_rest_port, graffiti, beacon_node_address,
                         fee_parameters='', mev_parameters=''):
    service_content = generate_lighthouse_vc_service(
        eth_network, graffiti, beacon_node_address,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
