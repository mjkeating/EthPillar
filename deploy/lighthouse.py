import os
import subprocess
from deploy.service_generators import generate_lighthouse_bn_service, generate_lighthouse_vc_service
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file
from client_requirements import validate_version_for_network

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Lighthouse release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release
    data = get_github_release("sigp/lighthouse", version_tag)
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
        filename = f"lighthouse-{tag}-{arch}-unknown-linux-gnu.tar.gz"
        download_url = f"https://github.com/sigp/lighthouse/releases/download/{tag}/{filename}"
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_lighthouse(eth_network: str) -> str:
    # Create User and directories
    setup_client_user_and_dir("consensus", "lighthouse")
    setup_client_user_and_dir("validator", "lighthouse_validator")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    lh_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('lighthouse', lh_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Lighthouse")

    # Extract the binary to /usr/local/bin/ using sudo
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}"])

    # Remove the tar file
    os.remove(download_path)
    return lh_version

def install_lighthouse_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                         cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                         fee_parameters: str = '', mev_parameters: str = '') -> str:
    service_content = generate_lighthouse_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_lighthouse_vc(lh_version: str, eth_network: str, cl_rest_port: str, graffiti: str, beacon_node_address: str,
                         fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Lighthouse validator client service file.

    Args:
        lh_version: Installed Lighthouse version.
        eth_network: Network name.
        cl_rest_port: Consensus client REST port.
        graffiti: Graffiti string.
        beacon_node_address: Beacon node address URL.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    service_content = generate_lighthouse_vc_service(
        eth_network, graffiti, beacon_node_address,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
