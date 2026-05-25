import os
import subprocess
from deploy.service_generators import generate_teku_bn_service, generate_teku_vc_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, install_system_directory, ensure_java_available
from client_requirements import validate_version_for_network
from typing import Optional

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Teku release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release
    data = get_github_release("ConsenSys/teku", version_tag)
    tag = data["tag_name"]
    version = tag.lstrip("v")
    filename = f"teku-{version}.tar.gz"
    download_url = f"https://artifacts.consensys.net/public/teku/raw/names/teku.tar.gz/versions/{version}/{filename}"
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_teku(eth_network: str) -> str:
    # Create User and directories
    setup_client_user_and_dir("consensus", "teku")
    setup_client_user_and_dir("validator", "teku_validator")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    teku_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('teku', teku_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Teku")
    # Ensure Java is installed for Teku (best-effort)
    ensure_java_available()

    # Extract to a temporary directory then install and harden
    tmp_dir = f"{DOWNLOAD_DIR}/teku_temp"
    subprocess.run(["rm", "-rf", tmp_dir], check=False)
    subprocess.run(["mkdir", "-p", tmp_dir], check=True)
    subprocess.run(["tar", "xzf", download_path, "-C", tmp_dir, "--strip-components=1"], check=True)
    install_system_directory(tmp_dir, f"{INSTALL_DIR}/teku")
    # Remove the tar file and temp dir
    os.remove(download_path)
    subprocess.run(["rm", "-rf", tmp_dir], check=False)
    return teku_version

def install_teku_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                   cl_rest_port: str, cl_p2p_port: str, cl_max_peer_count: str,
                   fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Teku beacon node service file.

    Args:
        eth_network: Network name.
        checkpoint_sync_url: Checkpoint sync URL.
        jwtsecret_path: Path to JWT secret file.
        cl_rest_port: Consensus client REST port.
        cl_p2p_port: Consensus client P2P port.
        cl_max_peer_count: Consensus client max peer count.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    # Match call in deploy-teku-besu.py (6 positional arguments)
    service_content = generate_teku_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_teku_vc(teku_version: str, eth_network: str, cl_rest_port: str, graffiti: str, bn_addr_flag: str,
                   fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Teku validator client service file.

    Args:
        teku_version: Installed Teku version.
        eth_network: Network name.
        cl_rest_port: Consensus client REST port.
        graffiti: Graffiti string.
        bn_addr_flag: Beacon node address flag.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    service_content = generate_teku_vc_service(
        eth_network, graffiti, bn_addr_flag,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
