import os
import subprocess
from deploy.service_generators import generate_reth_service
from deploy.common import install_system_binary, write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture
from client_requirements import validate_version_for_network
from typing import Tuple, Optional

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Reth release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release
    data = get_github_release("paradigmxyz/reth", version_tag)
    tag = data["tag_name"]
    arch = "x86_64" if arch_amd64 else "aarch64"
    download_url = None
    filename = None
    for asset in data["assets"]:
        if asset["name"].lower().endswith(f"{arch}-unknown-linux-gnu.tar.gz"):
            download_url = asset["browser_download_url"]
            filename = asset["name"]
            break
    if not download_url:
        filename = f"reth-{tag}-{arch}-unknown-linux-gnu.tar.gz"
        download_url = f"https://github.com/paradigmxyz/reth/releases/download/{tag}/{filename}"
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_and_install_reth(eth_network: str, el_p2p_port: str, el_p2p_port_2: str,
                                el_rpc_port: str, el_max_peer_count: str, jwtsecret_path: str,
                                network_override: Optional[str] = None, sync_parameters: str = '') -> Tuple[str, str]:
    """Download and install Reth binary and service.

    Returns:
        reth_version: The version string of the installed Reth
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "reth")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    reth_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('reth', reth_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Reth")

    # Extract the binary to /usr/local/bin/ using sudo
    # Reth tarball contains just the binary at root
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}"])

    # Find the extracted reth binary and rename it
    subprocess.run(["sudo", "sh", "-c", "mv /usr/local/bin/reth-* /usr/local/bin/reth"])

    # Ensure ownership and correct permissions via helper
    install_system_binary(f"{INSTALL_DIR}/reth", os.path.join(INSTALL_DIR, "reth"))

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_reth_service(
        eth_network, el_p2p_port, el_p2p_port_2, el_rpc_port, 
        el_max_peer_count, jwtsecret_path, network_override, sync_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return reth_version, service_file_path
