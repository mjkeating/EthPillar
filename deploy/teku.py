import os
import subprocess
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, install_system_directory, ensure_java_available, BASE_DATA_DIR, extract_and_install
from client_requirements import validate_version_for_network
from typing import Optional
from deploy.service_generators import form_exec_start, generate_systemd_template

def _teku_download_from_release(data: dict) -> tuple[str, str]:
    """Resolve Teku binary URL from GitHub assets or release notes."""
    import re

    from deploy.common import pick_github_release_asset

    assets = data.get("assets", [])
    if assets:
        return pick_github_release_asset(
            assets,
            None,
            name_contains=("teku",),
            prefer_extensions=(".tar.gz", ".zip"),
            client_label="Teku",
        )

    body = data.get("body", "")
    match = re.search(
        r"https://artifacts\.consensys\.net/public/teku/raw/names/teku\.tar\.gz/versions/[^/\s)]+/teku-[^/\s)]+\.tar\.gz",
        body,
    )
    if not match:
        raise ValueError("No Teku download URL found in GitHub release assets or release notes")

    download_url = match.group(0)
    return download_url.rsplit("/", 1)[-1], download_url


def generate_teku_bn_service(eth_network: str, sync_url: str, jwtsecret_path: str,
                             cl_rest_port: str, cl_p2p_port: str, cl_max_peer_count: str,
                             fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate Teku beacon node systemd service file content.

    Args:
        eth_network: Network name
        sync_url: Checkpoint sync URL
        jwtsecret_path: Path to JWT secret file
        cl_rest_port: CL REST port
        cl_p2p_port: CL P2P port
        cl_max_peer_count: CL max peer count
        fee_parameters: Optional fee recipient parameters
        mev_parameters: Optional MEV relay parameters

    Returns:
        Service file content as a string
    """
    _args = [
        f"{INSTALL_DIR}/teku/bin/teku",
        f"--network={eth_network}",
        f"--data-path={BASE_DATA_DIR}/teku",
        "--data-storage-mode=minimal",
        f"--checkpoint-sync-url={sync_url}",
        "--ee-endpoint=http://127.0.0.1:8551",
        f"--ee-jwt-secret-file={jwtsecret_path}",
        "--rest-api-enabled=true",
        f"--rest-api-port={cl_rest_port}",
        f"--p2p-port={cl_p2p_port}",
        f"--p2p-peer-upper-bound={cl_max_peer_count}",
        "--metrics-enabled=true",
        "--metrics-port=8008"
    ]
    if fee_parameters:
        _args.append(fee_parameters.strip())
    if mev_parameters:
        _args.append(mev_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Teku Beacon Node Consensus Client service for {eth_network.upper()}",
        user="consensus",
        exec_start=_exec_start,
        extra_env=['JAVA_OPTS=-Xmx6g', 'TEKU_OPTS=-XX:-HeapDumpOnOutOfMemoryError'],
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=None
    )

def generate_teku_vc_service(eth_network: str, graffiti: str, beacon_node_address: str,
                             fee_parameters: str = '', mev_parameters: str = '') -> str:
    """Generate Teku validator client systemd service file content.

    Args:
        eth_network: Network name
        graffiti: Graffiti string
        beacon_node_address: Beacon node address
        fee_parameters: Optional fee recipient parameters
        mev_parameters: Optional MEV relay parameters

    Returns:
        Service file content as a string
    """
    _args = [
        f"{INSTALL_DIR}/teku/bin/teku validator-client",
        f"--network={eth_network}",
        f"--data-path={BASE_DATA_DIR}/teku_validator",
        f"--validator-keys={BASE_DATA_DIR}/teku_validator/validator_keys:{BASE_DATA_DIR}/teku_validator/validator_keys",
        "--metrics-enabled=true",
        "--metrics-port=8009",
        f"--validators-graffiti={graffiti}",
        beacon_node_address
    ]
    if fee_parameters:
        _args.append(fee_parameters.strip())
    if mev_parameters:
        _args.append(mev_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Teku Validator Client service for {eth_network.upper()}",
        user="validator",
        exec_start=_exec_start,
        extra_env=None,
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=65536
    )


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
    filename, download_url = _teku_download_from_release(data)
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

    # Teku 26.6.0+ is compiled for JDK 25; an older runtime fails to start with
    # UnsupportedClassVersionError. Abort before installing anything if JDK 25
    # is not available (e.g. Ubuntu too old).
    # NOTE: keep this version in sync with the `updateJRE 25` call in update_execution.sh.
    if not ensure_java_available(25):
        print("❌ JDK 25 is required by Teku but could not be installed. Aborting Teku install.")
        exit(1)

    # Extract to canonical temp dir and install directory
    extract_and_install(download_path, "teku", f"{INSTALL_DIR}/teku", "directory", 1)
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
