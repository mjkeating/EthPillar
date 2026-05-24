import subprocess
import os
from deploy.service_generators import generate_prysm_bn_service, generate_prysm_vc_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture
from deploy.common import install_system_binary
from client_requirements import validate_version_for_network

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Prysm release version, download URLs, and filenames.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release
    data = get_github_release("prysmaticlabs/prysm", version_tag)
    tag = data["tag_name"]
    arch = "amd64" if arch_amd64 else "arm64"
    
    bn_url = None
    vc_url = None
    for asset in data["assets"]:
        if asset["name"] == f"beacon-chain-{tag}-linux-{arch}":
            bn_url = asset["browser_download_url"]
        elif asset["name"] == f"validator-{tag}-linux-{arch}":
            vc_url = asset["browser_download_url"]
            
    if not bn_url or not vc_url:
        for asset in data["assets"]:
            name = asset["name"]
            if "beacon-chain" in name and arch in name:
                bn_url = asset["browser_download_url"]
            elif "validator" in name and arch in name:
                vc_url = asset["browser_download_url"]
                
    if not bn_url or not vc_url:
        raise ValueError(f"Could not find Prysm assets for linux-{arch}")
        
    return {"version": tag, "download_urls": [bn_url, vc_url], "filenames": [f"beacon-chain-{tag}-linux-{arch}", f"validator-{tag}-linux-{arch}"]}



def download_prysm(eth_network: str) -> str:
    # Create User and directories
    setup_client_user_and_dir("consensus", "prysm")
    setup_client_user_and_dir("validator", "prysm_validator")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    pr_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('prysm', pr_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    bn_download_url = info["download_urls"][0]
    vc_download_url = info["download_urls"][1]
    bn_filename = info["filenames"][0]
    vc_filename = info["filenames"][1]

    # Download the beacon node
    bn_download_path = f"{DOWNLOAD_DIR}/{bn_filename}"
    download_file(bn_download_url, bn_download_path, "Prysm Beacon Node")

    # Download the validator client
    vc_download_path = f"{DOWNLOAD_DIR}/{vc_filename}"
    download_file(vc_download_url, vc_download_path, "Prysm Validator Client")

    # Move/configure the binaries into INSTALL_DIR
    install_system_binary(bn_download_path, os.path.join(INSTALL_DIR, "prysm-beacon-chain"))
    install_system_binary(vc_download_path, os.path.join(INSTALL_DIR, "prysm-validator"))

    return pr_version

def install_prysm_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                     cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                     fee_parameters: str = '', mev_parameters: str = '') -> str:
    service_content = generate_prysm_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_prysm_vc(pr_version: str, eth_network: str, cl_rest_port: str, graffiti: str, beacon_node_address: str,
                     fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate and write Prysm validator client service file."""
    service_content = generate_prysm_vc_service(
        eth_network, graffiti, beacon_node_address,
        fee_parameters, mev_parameters
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
