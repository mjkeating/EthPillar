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
# EthPillar currently only supports Grandine as a Consensus Client (Beacon Node).
# Grandine does not support running as a standalone Validator Client that can connect
# to a remote Beacon Node. Its design mandates that the Validator Client is an 
# embedded component within the main `grandine` process, activated by passing
# keystore paths directly to the binary.
# 
# Since EthPillar's architecture strictly relies on separated systemd services 
# (`consensus.service` and `validator.service`) to manage client lifecycles, view 
# split logs, and utilize our `manage_validator_keys.sh` workflows, trying to 
# shoehorn Grandine's embedded validator into our system would break these paradigms.
# 
# Therefore, when users choose Grandine as their Consensus Client, they must 
# pair it with another compatible Validator Client (such as Lighthouse, Teku, or 
# Lodestar) which will communicate with Grandine via its standard Beacon Node API 
# on port 5052.
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

    subprocess.run(["sudo", "mv", download_path, f"{INSTALL_DIR}/{filename}"])
    subprocess.run(["sudo", "chmod", "+x", f"{INSTALL_DIR}/{filename}"])

    return f"v{gr_version}"

def install_grandine_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                         cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                         fee_parameters: str = '', mev_parameters: str = '') -> str:
    service_content = generate_grandine_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path
