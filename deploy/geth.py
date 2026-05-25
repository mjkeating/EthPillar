import os
import requests
import subprocess
from tqdm import tqdm
from typing import Tuple, Optional
from deploy.service_generators import generate_geth_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir
from client_requirements import validate_version_for_network

def download_and_install_geth(eth_network: str, el_p2p_port: str, el_rpc_port: str, 
                                el_max_peer_count: str, jwtsecret_path: str,
                                network_override: Optional[str] = None) -> Tuple[str, str]:
    """Download and install Geth binary and service.

    Returns:
        geth_version: The version string of the installed Geth
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "geth")

    # Define the downloads page URL
    url = 'https://geth.ethereum.org/downloads'

    # Send a GET request to the page
    response = requests.get(url)
    
    # Check platform and arch
    import platform
    arch = platform.machine().lower()
    if arch == 'x86_64' or arch == 'amd64':
        target_arch = 'amd64'
    else:
        target_arch = 'arm64'
        
    import re
    pattern_url = r"(https://gethstore\.blob\.core\.windows\.net/builds/geth-linux-" + target_arch + r"-([0-9.]+)-[a-f0-9]+\.tar\.gz)"
    url_matches = re.findall(pattern_url, response.text)
    
    if not url_matches:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)
        
    download_url = url_matches[0][0]
    geth_version = "v" + url_matches[0][1]
    filename = download_url.split('/')[-1]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('geth', geth_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)


    # Download the latest release binary
    print(f">> Downloading Geth > URL: {download_url}")
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

    # Extract the binary to /usr/local/bin/geth using sudo
    
    # Extract to a temporary directory in DOWNLOAD_DIR, INSTALL_DIR
    temp_extract_dir = f"{DOWNLOAD_DIR}/geth_temp"
    subprocess.run(["mkdir", "-p", temp_extract_dir])
    subprocess.run(["tar", "xzf", download_path, "-C", temp_extract_dir])
    
    # Find the geth binary and move it
    extracted_dirs = [d for d in os.listdir(temp_extract_dir) if d.startswith("geth-linux")]
    if extracted_dirs:
        geth_bin_path = f"{temp_extract_dir}/{extracted_dirs[0]}/geth"
        subprocess.run(["sudo", "mv", geth_bin_path, f"{INSTALL_DIR}/geth"])
        subprocess.run(["sudo", "chmod", "+x", f"{INSTALL_DIR}/geth"])
        subprocess.run(["sudo", "chown", "execution:execution", f"{INSTALL_DIR}/geth"])
    
    # Cleanup temp directory
    subprocess.run(["rm", "-rf", temp_extract_dir])

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_geth_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, 
        jwtsecret_path, network_override
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return geth_version, service_file_path
