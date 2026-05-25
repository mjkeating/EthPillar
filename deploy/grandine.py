import os
import requests
import subprocess
from tqdm import tqdm
from deploy.service_generators import generate_grandine_bn_service
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR, INSTALL_DIR, get_raw_architecture, setup_client_user_and_dir
from client_requirements import validate_version_for_network
import requests

# ==============================================================================
# Grandine Client Support Note:
# 
# EthPillar supports Grandine as both a standalone Consensus Client (Beacon Node)
# and as an integrated Consensus + Validator Client. 
# 
# Grandine does not natively support a "Validator Client Only" mode via standard HTTP 
# Beacon APIs in the same way Lighthouse or Teku do. Instead, the Validator Client 
# is an embedded component within the main `grandine` process, activated by simply 
# passing keystore directory paths to the binary.
# 
# When users select "Grandine (integrated)" as their Validator Client, EthPillar skips 
# generating the standard `validator.service` file. Instead, the keystore flags 
# are appended to `consensus.service`. The EthPillar key management scripts have 
# been adapted to copy the `.json` and `.txt` keystore files directly into 
# `/var/lib/grandine/validator_keys/` and set proper permissions for the `consensus` user.
# ==============================================================================

def download_grandine(eth_network: str) -> str:
    import platform
    machine_arch = platform.machine()
    
    if machine_arch == 'x86_64' or machine_arch == 'amd64':
        binary_arch = 'x64'
    elif machine_arch == 'aarch64' or machine_arch == 'arm64':
        binary_arch = 'arm64'
    else:
        print(f"Error: Unsupported architecture {machine_arch}")
        exit(1)

    # Create User and directories
    setup_client_user_and_dir("consensus", "grandine")

    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/grandinetech/grandine/releases/latest'
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        gr_version = response.json()['tag_name']
    except requests.exceptions.RequestException as e:
        print(f"Error: Unable to fetch latest version info. Try again later. {e}")
        exit(1)

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('grandine', gr_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    # Clean version string for download URL (remove leading 'v' if present)
    clean_version = gr_version.lstrip('v')
    
    # Grandine asset: grandine-2.0.4-linux-x64
    filename = f"grandine-{clean_version}-linux-{binary_arch}"
    download_url = f"https://github.com/grandinetech/grandine/releases/download/{gr_version}/{filename}"

    print(f">> Downloading Grandine > URL: {download_url}")
    download_path = f"{DOWNLOAD_DIR}/{filename}"

    try:
        response = requests.get(download_url, stream=True)
        response.raise_for_status()
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

    subprocess.run(["sudo", "mv", download_path, f"{INSTALL_DIR}/{filename}"], check=True)
    subprocess.run(["sudo", "chmod", "+x", f"{INSTALL_DIR}/{filename}"], check=True)
    # Create a stable symlink so consensus.service can always reference /usr/local/bin/grandine
    subprocess.run(["sudo", "ln", "-sf", f"{INSTALL_DIR}/{filename}", f"{INSTALL_DIR}/grandine"], check=True)

    return gr_version

def install_grandine_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                         cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                         fee_parameters: str = '', mev_parameters: str = '', is_integrated_vc: bool = False) -> str:
    service_content = generate_grandine_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters, is_integrated_vc=is_integrated_vc
    )
    
    if is_integrated_vc:
        subprocess.run(["sudo", "mkdir", "-p", "/var/lib/grandine/validator_keys"])
        subprocess.run(["sudo", "chown", "-R", "consensus:consensus", "/var/lib/grandine/validator_keys"])
        subprocess.run(["sudo", "chmod", "700", "/var/lib/grandine/validator_keys"])

    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path
