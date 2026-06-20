import os
import subprocess
from typing import Optional
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, install_system_binary, BASE_DATA_DIR
from client_requirements import validate_version_for_network
from deploy.service_generators import form_exec_start, generate_systemd_template

def generate_lighthouse_bn_service(eth_network: str, sync_url: str, jwtsecret_path: str,
                                   cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str,
                                   cl_max_peer_count: str,
                                   fee_parameters: str = '', mev_parameters: str = '',
                                   network_override: Optional[str] = None) -> str:
    """Generate Lighthouse beacon node systemd service file content.

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

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    _args = [
        f"{INSTALL_DIR}/lighthouse bn",
        _network,
        f"--datadir={BASE_DATA_DIR}/lighthouse",
        "--gui",
        f"--port={cl_p2p_port}",
        f"--quic-port={cl_p2p_port_2}",
        f"--target-peers={cl_max_peer_count}",
        f"--http-port={cl_rest_port}",
        "--staking",
        "--validator-monitor-auto",
        f"--checkpoint-sync-url={sync_url}",
        "--execution-endpoint=http://127.0.0.1:8551",
        "--metrics",
        "--metrics-address=127.0.0.1",
        "--metrics-port=8008",
        f"--execution-jwt={jwtsecret_path}"
    ]
    if mev_parameters:
        _args.append(mev_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Lighthouse Consensus Client service for {eth_network.upper()}",
        user="consensus",
        exec_start=_exec_start,
        extra_env=None,
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=None
    )

def generate_lighthouse_vc_service(eth_network: str, graffiti: str, beacon_node_address: str,
                                   fee_parameters: str = '', mev_parameters: str = '',
                                   network_override: Optional[str] = None) -> str:
    """Generate Lighthouse validator client systemd service file content.

    Args:
        eth_network: Network name
        graffiti: Graffiti string
        beacon_node_address: Beacon node address
        fee_parameters: Optional fee recipient parameters
        mev_parameters: Optional MEV relay parameters
        network_override: Optional network flag override

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    _args = [
        f"{INSTALL_DIR}/lighthouse vc",
        _network,
        f"--datadir={BASE_DATA_DIR}/lighthouse_validator",
        "--http",
        "--metrics",
        "--metrics-address=127.0.0.1",
        "--metrics-port=8009",
        f"--graffiti={graffiti}",
        beacon_node_address
    ]
    if fee_parameters:
        _args.append(fee_parameters.strip())
    if mev_parameters:
        _args.append(mev_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Lighthouse Validator Client service for {eth_network.upper()}",
        user="validator",
        exec_start=_exec_start,
        extra_env=None,
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=65536
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Lighthouse release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset
    data = get_github_release("sigp/lighthouse", version_tag)
    tag = data["tag_name"]
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("lighthouse",),
        client_label="Lighthouse",
    )
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
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}"], check=True)

    # Ensure the binary is owned by root:root with correct permissions
    # (same as other clients — tar extracts with the running user's uid in some envs)
    install_system_binary(f"{INSTALL_DIR}/lighthouse", f"{INSTALL_DIR}/lighthouse")

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
