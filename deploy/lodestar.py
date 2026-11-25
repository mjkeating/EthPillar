from typing import List, Tuple, Optional
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_release_file, get_machine_architecture, BASE_DATA_DIR, extract_and_install, ensure_libatomic
from client_requirements import validate_version_for_network
from deploy.service_generators import form_exec_start, generate_systemd_template

def generate_lodestar_bn_service(eth_network: str, sync_url: str, jwtsecret_path: str,
                                 cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                                 fee_parameters: str = '', mev_parameters: str = '',
                                 network_override: Optional[str] = None) -> str:
    """Generate Lodestar beacon node systemd service file content.

    Args:
        eth_network: Network name
        sync_url: Checkpoint sync URL
        jwtsecret_path: Path to JWT secret file
        cl_rest_port: CL REST port
        cl_p2p_port: CL P2P port
        cl_p2p_port_2: CL QUIC port (UDP)
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
        f"{INSTALL_DIR}/lodestar beacon",
        _network,
        f"--dataDir={BASE_DATA_DIR}/lodestar",
        f"--checkpointSyncUrl={sync_url}",
        "--execution.urls=http://127.0.0.1:8551",
        f"--jwt-secret={jwtsecret_path}",
        f"--rest.port={cl_rest_port}",
        f"--port={cl_p2p_port}",
        f"--quicPort={cl_p2p_port_2}",
        f"--targetPeers={cl_max_peer_count}",
        "--metrics=true",
        "--metrics.port=8008"
    ]
    if fee_parameters:
        _args.append(fee_parameters.strip())
    if mev_parameters:
        _args.append(mev_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Lodestar Consensus Client service for {eth_network.upper()}",
        user="consensus",
        exec_start=_exec_start,
        extra_env=[f'"TMPDIR={BASE_DATA_DIR}/lodestar/tmp"'],
        working_dir=f"{INSTALL_DIR}",
        timeout_stop_sec=900,
        limit_nofile=None
    )

def generate_lodestar_vc_service(eth_network: str, graffiti: str, beacon_node_address: str,
                                 fee_parameters: str = '', extra_parameters: str = '',
                                 network_override: Optional[str] = None,
                                 unit_after: Optional[List[str]] = None) -> str:
    """Generate Lodestar validator client systemd service file content.

    Args:
        eth_network: Network name
        graffiti: Graffiti string
        beacon_node_address: Beacon node address
        fee_parameters: Optional fee recipient parameters
        extra_parameters: Optional extra ExecStart flags (builder/MEV and/or DVT)
        network_override: Optional network flag override
        unit_after: Optional extra systemd ``After=``/``Wants=`` units
            (e.g. ``["charon.service"]`` when running behind Obol Charon).

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--network={eth_network}'

    _args = [
        f"{INSTALL_DIR}/lodestar validator",
        _network,
        f"--dataDir={BASE_DATA_DIR}/lodestar_validator",
        "--metrics=true",
        "--metrics.port=8009",
        f"--graffiti={graffiti}",
        beacon_node_address
    ]
    if fee_parameters:
        _args.append(fee_parameters.strip())
    if extra_parameters:
        _args.append(extra_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Lodestar Validator Client service for {eth_network.upper()}",
        user="validator",
        exec_start=_exec_start,
        extra_env=[f'"TMPDIR={BASE_DATA_DIR}/lodestar_validator/tmp"'],
        working_dir=f"{INSTALL_DIR}",
        timeout_stop_sec=300,
        limit_nofile=65536,
        unit_after=unit_after,
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Lodestar release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', 'filenames', and
        optionally 'commit'.
    """
    from deploy.common import get_github_release, pick_github_release_asset, release_info_from_github
    repo = "ChainSafe/lodestar"
    data = get_github_release(repo, version_tag)
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("lodestar",),
        client_label="Lodestar",
    )
    return release_info_from_github(data, [download_url], [filename])



def download_lodestar(eth_network: str) -> str:
    # Create User and directories
    setup_client_user_and_dir("consensus", "lodestar")
    setup_client_user_and_dir("validator", "lodestar_validator")

    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    lodestar_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('lodestar', lodestar_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    # Lodestar v2 bundles Node.js 26, which needs libatomic.so.1. Abort before
    # downloading or replacing a working binary when apt cannot install it.
    # NOTE: keep in sync with ensureLibatomic in update_consensus.sh,
    # update_validator.sh, and tests/integration/test_rc_vs_latest.sh.
    if not ensure_libatomic():
        print("❌ libatomic1 is required by Lodestar but could not be installed. Aborting Lodestar install.")
        exit(1)

    download_path = download_release_file(info, DOWNLOAD_DIR, "Lodestar")

    # Single binary shared by beacon and validator; each role uses its own data/tmp dir under /var/lib.
    extract_and_install(download_path, "lodestar", f"{INSTALL_DIR}/lodestar", "binary", 0, binary_name="lodestar")
    return lodestar_version

def install_lodestar_bn(eth_network: str, checkpoint_sync_url: str, jwtsecret_path: str,
                       cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                       fee_parameters: str = '', mev_parameters: str = '',
                       network_override: Optional[str] = None) -> str:
    """Generate and write Lodestar beacon node service file.

    Args:
        eth_network: Network name.
        checkpoint_sync_url: Checkpoint sync URL.
        jwtsecret_path: Path to JWT secret file.
        cl_rest_port: Consensus client REST port.
        cl_p2p_port: Consensus client P2P port.
        cl_p2p_port_2: Consensus client QUIC port.
        cl_max_peer_count: Consensus client max peer count.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.

    Returns:
        The path to the created service file.
    """
    service_content = generate_lodestar_bn_service(
        eth_network, checkpoint_sync_url, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters, network_override=network_override
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_lodestar_vc(lodestar_version: str, eth_network: str, cl_rest_port: str, graffiti: str, bn_addr_flag: str,
                       fee_parameters: str = '', extra_parameters: str = '',
                       unit_after: Optional[List[str]] = None,
                       network_override: Optional[str] = None) -> str:
    """Generate and write Lodestar validator client service file.

    Args:
        lodestar_version: Installed Lodestar version.
        eth_network: Network name.
        cl_rest_port: Consensus client REST port.
        graffiti: Graffiti string.
        bn_addr_flag: Beacon node address flag.
        fee_parameters: Optional fee recipient parameters.
        extra_parameters: Optional extra ExecStart flags (builder/MEV and/or DVT).
        unit_after: Optional extra systemd ``After=``/``Wants=`` units.

    Returns:
        The path to the created service file.
    """
    service_content = generate_lodestar_vc_service(
        eth_network, graffiti, bn_addr_flag,
        fee_parameters, extra_parameters, network_override=network_override,
        unit_after=unit_after,
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
