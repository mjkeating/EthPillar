import os
import subprocess
from typing import Tuple, Optional
from deploy.service_generators import generate_besu_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, install_system_directory, ensure_java_available
from client_requirements import validate_version_for_network

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Besu release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset
    data = get_github_release("besu-eth/besu", version_tag)
    tag = data["tag_name"]
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        None,
        name_contains=("besu",),
        prefer_extensions=(".tar.gz", ".zip"),
        client_label="Besu",
    )
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_and_install_besu(eth_network: str, el_p2p_port: str, el_rpc_port: str, 
                                el_max_peer_count: str, jwtsecret_path: str,
                                network_override: Optional[str] = None) -> Tuple[str, str]:
    """Download and install Besu binary and service.

    Returns:
        besu_version: The version string of the installed Besu
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "besu")
    print(f">> Installing dependencies")
    # Besu 26.6.0+ is compiled for JDK 25; an older runtime fails to start with
    # UnsupportedClassVersionError. Abort before installing anything if JDK 25
    # is not available (e.g. Ubuntu too old).
    # NOTE: keep this version in sync with the `updateJRE 25` call in update_execution.sh.
    if not ensure_java_available(25):
        print("❌ JDK 25 is required by Besu but could not be installed. Aborting Besu install.")
        exit(1)
    subprocess.run(["sudo", "apt-get", '-qq', "install", "libjemalloc-dev", "-y"], check=True)

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    besu_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('besu', besu_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Besu")

    # Extract to a temporary dir and install atomically with hardening
    tmp_dir = f"{DOWNLOAD_DIR}/besu_temp"
    subprocess.run(["rm", "-rf", tmp_dir], check=False)
    subprocess.run(["mkdir", "-p", tmp_dir], check=True)
    subprocess.run(["tar", "xzf", download_path, "-C", tmp_dir, "--strip-components=1"], check=True)
    install_system_directory(tmp_dir, f"{INSTALL_DIR}/besu")

    # Remove the tar file and temporary extraction directory
    os.remove(download_path)
    subprocess.run(["rm", "-rf", tmp_dir], check=False)

    # Generate Service File Content
    service_content = generate_besu_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, 
        jwtsecret_path, network_override
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return besu_version, service_file_path
