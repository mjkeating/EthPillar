import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_nethermind_service
from deploy.common import write_service_file, get_machine_architecture
from client_requirements import validate_version_for_network

def download_and_install_nethermind(eth_network, el_p2p_port, el_rpc_port, 
                                     el_max_peer_count, jwtsecret_path,
                                     network_override=None, sync_parameters=''):
    """Download and install Nethermind binary and service.

    Returns:
        nm_version: The version string of the installed Nethermind
        service_file_path: The path to the created service file
    """
    # Nethermind uses linux-x64 for x86_64
    import platform
    machine = platform.machine()
    binary_arch = "x64" if machine == "x86_64" else "arm64" if machine == "aarch64" else machine

    # Create User and directories
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
    subprocess.run(["sudo", "mkdir", "-p", "/var/lib/nethermind"])
    subprocess.run(["sudo", "chown", "-R", "execution:execution", "/var/lib/nethermind"])

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/NethermindEth/nethermind/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    nm_version = response.json()['tag_name']

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('nethermind', nm_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    # Search for the asset
    assets = response.json()['assets']
    download_url = None
    filename = None
    for asset in assets:
        if asset['name'].endswith(f'linux-{binary_arch}.zip'):
             download_url = asset['browser_download_url']
             filename = asset['name']
             break

    if download_url is None:
        print(f"Error: Could not find the download URL for the latest release (looked for linux-{binary_arch}.zip).")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading Nethermind > URL: {download_url}")
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

    # Extract the binary using unzip
    # First install unzip
    subprocess.run(["sudo", "apt-get", "-y", "-qq", "install", "unzip"])
    subprocess.run(["sudo", "mkdir", "-p", "/usr/local/bin/nethermind"])
    subprocess.run(["sudo", "unzip", "-o", download_path, "-d", "/usr/local/bin/nethermind"])

    # Remove the zip file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_nethermind_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, 
        jwtsecret_path, network_override, sync_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return nm_version, service_file_path
