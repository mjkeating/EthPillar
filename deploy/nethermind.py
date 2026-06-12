import os
import subprocess
from typing import Tuple, Optional
from deploy.service_generators import generate_nethermind_service
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, install_system_directory
from client_requirements import validate_version_for_network

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Nethermind release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset
    data = get_github_release("NethermindEth/nethermind", version_tag)
    tag = data["tag_name"]
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("nethermind",),
        prefer_extensions=(".zip", ".tar.gz"),
        client_label="Nethermind",
    )
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_and_install_nethermind(eth_network: str, el_p2p_port: str, el_rpc_port: str, 
                                     el_max_peer_count: str, jwtsecret_path: str,
                                     network_override: Optional[str] = None, sync_parameters: str = '') -> Tuple[str, str]:
    """Download and install Nethermind binary and service.

    Returns:
        nm_version: The version string of the installed Nethermind
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "nethermind")
    # Ensure home directory exists for .NET bundle extraction if WorkingDirectory isn't enough
    subprocess.run(["sudo", "mkdir", "-p", "/home/execution"], check=True)
    subprocess.run(["sudo", "chown", "execution:execution", "/home/execution"], check=True)
    
    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    nm_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('nethermind', nm_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Nethermind")

    # Extract to a temporary directory and install atomically
    subprocess.run(["sudo", "apt-get", "-y", "-qq", "install", "unzip"], check=False)
    tmp_dir = f"{DOWNLOAD_DIR}/nethermind_temp"
    subprocess.run(["rm", "-rf", tmp_dir], check=False)
    subprocess.run(["mkdir", "-p", tmp_dir], check=True)
    subprocess.run(["unzip", "-o", download_path, "-d", tmp_dir], check=True)
    install_system_directory(tmp_dir, f"{INSTALL_DIR}/nethermind")

    # Remove the zip file and temporary extraction directory
    os.remove(download_path)
    subprocess.run(["rm", "-rf", tmp_dir], check=False)

    # Generate Service File Content
    service_content = generate_nethermind_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, 
        jwtsecret_path, network_override, sync_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return nm_version, service_file_path
