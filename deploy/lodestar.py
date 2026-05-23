import os
import subprocess
from typing import Tuple, Optional
from deploy.service_generators import generate_lodestar_bn_service, generate_lodestar_vc_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture
from client_requirements import validate_version_for_network

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Lodestar release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release
    data = get_github_release("ChainSafe/lodestar", version_tag)
    tag = data["tag_name"]
    arch = "amd64" if arch_amd64 else "arm64"
    download_url = None
    filename = None
    for asset in data["assets"]:
        if asset["name"].lower().endswith(f"linux-{arch}.tar.gz"):
            download_url = asset["browser_download_url"]
            filename = asset["name"]
            break
    if not download_url:
        filename = f"lodestar-{tag}-linux-{arch}.tar.gz"
        download_url = f"https://github.com/ChainSafe/lodestar/releases/download/{tag}/{filename}"
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_lodestar(eth_network: str) -> str:
    # Create User and directories
    setup_client_user_and_dir("consensus", "lodestar")
    setup_client_user_and_dir("validator", "lodestar_validator")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    lodestar_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('lodestar', lodestar_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Lodestar")

    # The archive usually unpacks a lodestar directory or bare files.
    # We want the binary to end up at /usr/local/bin/lodestar
    subprocess.run(["sudo", "mkdir", "-p", "/tmp/lodestar_extract"])
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/tmp/lodestar_extract"])
    # Find the lodestar binary within the extracted archive and move it to the install dir
    result = subprocess.run(["sudo", "find", "/tmp/lodestar_extract", "-type", "f", "-name", "lodestar"], capture_output=True, text=True)
    lodestar_bin = result.stdout.strip().split("\n")[0]
    if lodestar_bin:
        subprocess.run(["sudo", "mv", lodestar_bin, f"{INSTALL_DIR}/lodestar"], check=True)
        subprocess.run(["sudo", "chmod", "+x", f"{INSTALL_DIR}/lodestar"], check=True)

    # Remove the tar file and temporary extraction directory
    os.remove(download_path)
    subprocess.run(["sudo", "rm", "-rf", "/tmp/lodestar_extract"])
    return lodestar_version

def install_lodestar_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                       cl_rest_port: str, cl_p2p_port: str, cl_max_peer_count: str,
                       fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Lodestar beacon node service file.

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
    service_content = generate_lodestar_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_lodestar_vc(lodestar_version: str, eth_network: str, cl_rest_port: str, graffiti: str, bn_addr_flag: str,
                       fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Lodestar validator client service file.

    Args:
        lodestar_version: Installed Lodestar version.
        eth_network: Network name.
        cl_rest_port: Consensus client REST port.
        graffiti: Graffiti string.
        bn_addr_flag: Beacon node address flag.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    service_content = generate_lodestar_vc_service(
        eth_network, graffiti, bn_addr_flag,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
