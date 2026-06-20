import os
import subprocess
from deploy.common import install_system_binary, write_service_file, get_machine_architecture, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, BASE_DATA_DIR
from client_requirements import validate_version_for_network
from typing import Optional
from deploy.service_generators import form_exec_start, generate_systemd_template

def generate_grandine_bn_service(eth_network: str, sync_url: str, jwtsecret_path: str,
                                 cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                                 fee_parameters: str = '', mev_parameters: str = '',
                                 network_override: Optional[str] = None, is_integrated_vc: bool = False) -> str:
    """Generate Grandine beacon node systemd service file content.

    Args:
        eth_network: Network name
        sync_url: Checkpoint sync URL
        jwtsecret_path: Path to JWT secret file
        cl_rest_port: CL REST port
        cl_p2p_port: CL P2P port
        cl_p2p_port_2: CL secondary P2P port
        cl_max_peer_count: CL max peer count
        fee_parameters: Optional fee recipient parameters
        mev_parameters: Optional MEV relay parameters
        network_override: Optional network flag override
        is_integrated_vc: Whether the validator is integrated

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    _keystore_args = ""
    if is_integrated_vc:
        _keystore_args = f"--keystore-dir={BASE_DATA_DIR}/grandine/validator_keys --keystore-password-dir={BASE_DATA_DIR}/grandine/validator_keys"

    _args = [
        f"{INSTALL_DIR}/grandine",
        _network,
        f"--data-dir={BASE_DATA_DIR}/grandine",
        f"--libp2p-port={cl_p2p_port}",
        f"--discovery-port={cl_p2p_port}",
        f"--quic-port={cl_p2p_port_2}",
        f"--target-peers={cl_max_peer_count}",
        "--http-address=127.0.0.1",
        f"--http-port={cl_rest_port}",
        f"--checkpoint-sync-url={sync_url}",
        "--eth1-rpc-urls=http://127.0.0.1:8551",
        "--metrics",
        "--metrics-address=127.0.0.1",
        "--metrics-port=8008",
        f"--jwt-secret={jwtsecret_path}"
    ]

    if fee_parameters:
        _args.append(fee_parameters.strip())
    if mev_parameters:
        _args.append(mev_parameters.strip())
    if _keystore_args:
        _args.append(_keystore_args.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Grandine Consensus Client service for {eth_network.upper()}",
        user="consensus",
        exec_start=_exec_start,
        extra_env=None,
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=None
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Grandine release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset
    data = get_github_release("grandinetech/grandine", version_tag)
    tag = data["tag_name"]
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("grandine",),
        client_label="Grandine",
    )
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



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

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    gr_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('grandine', gr_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    clean_version = gr_version.lstrip('v')
    dest_filename = f"grandine-{clean_version}-linux-{binary_arch}"

    download_path = f"{DOWNLOAD_DIR}/{dest_filename}"
    download_file(download_url, download_path, "Grandine")

    # Move into place and ensure secure perms/ownership, then create stable symlink
    install_system_binary(download_path, os.path.join(INSTALL_DIR, dest_filename))
    subprocess.run(["sudo", "ln", "-sf", f"{INSTALL_DIR}/{dest_filename}", f"{INSTALL_DIR}/grandine"], check=True)

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
        subprocess.run(["sudo", "mkdir", "-p", "/var/lib/grandine/validator_keys"], check=True)
        subprocess.run(["sudo", "chown", "-R", "consensus:consensus", "/var/lib/grandine/validator_keys"], check=True)
        subprocess.run(["sudo", "chmod", "700", "/var/lib/grandine/validator_keys"], check=True)

    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path
