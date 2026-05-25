import os
import subprocess
from typing import Tuple
from deploy.service_generators import generate_erigon_service, generate_erigon_standalone_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, install_system_binary
from client_requirements import validate_version_for_network

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Erigon release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release
    data = get_github_release("erigontech/erigon", version_tag)
    tag = data["tag_name"]
    arch = "amd64" if arch_amd64 else "arm64"
    download_url = None
    filename = None
    for asset in data["assets"]:
        if asset["name"].lower().endswith(f"linux_{arch}.tar.gz"):
            download_url = asset["browser_download_url"]
            filename = asset["name"]
            break
    if not download_url:
        raise ValueError(f"Could not find Erigon asset for linux_{arch}")
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_and_install_erigon(eth_network: str, el_p2p_port: str, el_rpc_port: str, el_max_peer_count: str, 
                                 jwtsecret_path: str, cl_p2p_port: str, cl_rest_port: str, cl_max_peer_count_cl: str,
                                 checkpoint_sync_url: str, mev_parameters: str = '') -> Tuple[str, str]:
    """Download and install Erigon binary and service.

    Returns:
        erigon_version: The version string of the installed Erigon
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "erigon")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    erigon_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('erigon', erigon_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Erigon")

    # Extract the binary using sudo
    # Erigon tarball typically contains a folder, so we strip one component and extract to /usr/local/bin
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}", "--strip-components=1"])
    # Ensure binary is configured correctly
    install_system_binary(f"{INSTALL_DIR}/erigon", os.path.join(INSTALL_DIR, "erigon"))

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_erigon_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count,
        jwtsecret_path, cl_p2p_port, cl_rest_port, cl_max_peer_count_cl,
        checkpoint_sync_url, mev_parameters=mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return erigon_version, service_file_path


def download_and_install_erigon_standalone(eth_network: str, el_p2p_port: str, el_rpc_port: str, el_max_peer_count: str, 
                                           jwtsecret_path: str) -> Tuple[str, str]:
    """Download and install Erigon binary and service as a standalone execution client.

    Returns:
        erigon_version: The version string of the installed Erigon
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "erigon")

    # Resolve version and download URL using local get_release_info
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    erigon_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('erigon', erigon_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Erigon Standalone")

    # Extract the binary using sudo
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}", "--strip-components=1"])
    subprocess.run(["sudo", "chmod", "a+x", f"{INSTALL_DIR}/erigon"])
    install_system_binary(f"{INSTALL_DIR}/erigon", os.path.join(INSTALL_DIR, "erigon"))

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_erigon_standalone_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, jwtsecret_path
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return erigon_version, service_file_path
