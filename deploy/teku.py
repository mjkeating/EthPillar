import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_teku_bn_service, generate_teku_vc_service
from deploy.common import write_service_file, DOWNLOAD_DIR
from client_requirements import validate_version_for_network

def download_teku(eth_network):
    # Create User and directories
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "consensus"])
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "validator"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/teku"])
    subprocess.run(["sudo", "chown", "-R", "consensus:consensus", "/var/lib/teku"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/teku_validator"])
    subprocess.run(["sudo", "chown", "-R", "validator:validator", "/var/lib/teku_validator"])

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/Consensys/teku/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    teku_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('teku', teku_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    assets = response.json()['assets']
    download_url = None
    filename = f'teku-{teku_version}.tar.gz'
    for asset in assets:
        if asset['name'].endswith(filename):
            download_url = asset['browser_download_url']
            break

    if download_url is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Teku > URL: {download_url}")
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

    # Extract the binary to /usr/local/bin/teku using sudo
    subprocess.run(["sudo", "mkdir", "-p", "/usr/local/bin/teku"])
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/usr/local/bin/teku", "--strip-components=1"])

    # Remove the tar file
    os.remove(download_path)
    return teku_version

def install_teku_bn(eth_network, checkpoint_sync_url, jwtsecret_path,
                   cl_rest_port, cl_p2p_port, cl_max_peer_count,
                   fee_parameters='', mev_parameters=''):
    # Match call in deploy-teku-besu.py (6 positional arguments)
    service_content = generate_teku_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_teku_vc(teku_version, eth_network, cl_rest_port, graffiti, bn_addr_flag,
                   fee_parameters='', mev_parameters=''):
    service_content = generate_teku_vc_service(
        eth_network, graffiti, bn_addr_flag,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
