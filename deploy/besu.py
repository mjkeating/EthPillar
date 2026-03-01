import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_besu_service
from deploy.common import write_service_file
from client_requirements import validate_version_for_network

def download_and_install_besu(eth_network, el_p2p_port, el_rpc_port, 
                                el_max_peer_count, jwtsecret_path,
                                network_override=None):
    """Download and install Besu binary and service.

    Returns:
        besu_version: The version string of the installed Besu
        service_file_path: The path to the created service file
    """
    # Create User and directories
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/besu"])
    subprocess.run(["sudo", "chown", "-R", "execution:execution", "/var/lib/besu"])
    print(f">> Installing dependencies")
    subprocess.run(["sudo", "apt-get", '-qq', "install", "openjdk-21-jdk", "libjemalloc-dev", "-y"], check=True)

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/hyperledger/besu/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    besu_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('besu', besu_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    assets = response.json()['assets']
    download_url = None
    filename = f'besu-{besu_version}.tar.gz'
    for asset in assets:
        if asset['name'].endswith(filename):
            download_url = asset['browser_download_url']
            break

    if download_url is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Besu > URL: {download_url}")
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

    # Extract the binary to /usr/local/bin/besu using sudo
    subprocess.run(["sudo", "mkdir", "-p", "/usr/local/bin/besu"])
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/usr/local/bin/besu", "--strip-components=1"])

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_besu_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, 
        jwtsecret_path, network_override
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return besu_version, service_file_path
