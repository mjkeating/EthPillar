import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_nimbus_bn_service, generate_nimbus_vc_service
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR
from client_requirements import validate_version_for_network

def download_nimbus(eth_network):
    binary_arch = get_machine_architecture() # Use amd64 for Nimbus

    # Create User and directories
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "consensus"])
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "validator"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/nimbus"])
    subprocess.run(["sudo", "chown", "-R", "consensus:consensus", "/var/lib/nimbus"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/nimbus_validator"])
    subprocess.run(["sudo", "chown", "-R", "validator:validator", "/var/lib/nimbus_validator"])
    
    # Install dependencies for Nimbus
    print(f">> Installing Nimbus dependencies")
    subprocess.run(["sudo", "apt-get", "-y", "-qq", "install", "libnss3", "libsqlite3-0"])

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

    assets = response.json()['assets']
    download_url = None
    filename = None
    # Nimbus asset: nimbus-eth2_linux-amd64_1.2.3_abcdef.tar.gz
    for asset in assets:
        if f'Linux_{binary_arch}' in asset['name'] and asset['name'].endswith('.tar.gz'):
             download_url = asset['browser_download_url']
             filename = asset['name']
             break

    if download_url is None:
        print(f"Error: Could not find the download URL for the latest release (looked for Linux_{binary_arch}.tar.gz).")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Nimbus > URL: {download_url}")
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
    subprocess.run(["sudo", "mkdir", "-p", "/tmp/nimbus_extract"])
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/tmp/nimbus_extract", "--strip-components=1"])
    
    # Move the actual binaries we need
    subprocess.run(["sudo", "cp", "/tmp/nimbus_extract/build/nimbus_beacon_node", "/usr/local/bin/"])
    subprocess.run(["sudo", "cp", "/tmp/nimbus_extract/build/nimbus_validator_client", "/usr/local/bin/"])

    # Remove the tar file and extract dir
    os.remove(download_path)
    subprocess.run(["sudo", "rm", "-rf", "/tmp/nimbus_extract"])
    return nimbus_version

def install_nimbus_bn(eth_network, jwtsecret_path,
                     cl_rest_port, cl_p2p_port, cl_max_peer_count,
                     fee_parameters='', mev_parameters=''):
    service_content = generate_nimbus_bn_service(
        eth_network, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_nimbus_vc(nimbus_version, eth_network, cl_rest_port, graffiti, bn_addr_flag,
                     fee_parameters='', mev_parameters=''):
    service_content = generate_nimbus_vc_service(
        eth_network, graffiti, bn_addr_flag,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
