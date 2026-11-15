import subprocess
from typing import Tuple, Optional
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_release_file, BASE_DATA_DIR, extract_and_install
from client_requirements import validate_version_for_network
from deploy.service_generators import form_exec_start, generate_systemd_template

def generate_nethermind_service(eth_network: str, el_p2p_port: str, el_rpc_port: str,
                                el_max_peer_count: str, jwtsecret_path: str,
                                network_override: Optional[str] = None, sync_parameters: str = '',
                                enable_ws: bool = False) -> str:
    """Generate Nethermind execution client systemd service file content.

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count
        jwtsecret_path: Path to JWT secret file
        network_override: Optional network config override (for ephemery)
        sync_parameters: Optional sync barrier parameters
        enable_ws: When True, expose localhost WebSocket on :8546 (SSV eth1)

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--config {eth_network}'

    _args = [
        f"{INSTALL_DIR}/nethermind/nethermind",
        _network,
        f"--data-dir=\"{BASE_DATA_DIR}/nethermind\"",
        f"--Network.DiscoveryPort {el_p2p_port}",
        f"--Network.P2PPort {el_p2p_port}",
        f"--Network.MaxActivePeers {el_max_peer_count}",
        f"--JsonRpc.Port {el_rpc_port}",
    ]
    if enable_ws:
        _args.extend([
            "--JsonRpc.Host 127.0.0.1",
            "--JsonRpc.WebSocketsPort 8546",
        ])
    _args.extend([
        "--Metrics.Enabled true",
        "--Metrics.ExposePort 6060",
        f"--JsonRpc.JwtSecretFile {jwtsecret_path}",
        "--Pruning.Mode=Hybrid",
        "--Pruning.FullPruningTrigger=VolumeFreeSpace",
        "--Pruning.FullPruningThresholdMb=300000"
    ])
    if sync_parameters:
        _args.append(sync_parameters.strip())
    
    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Nethermind Execution Layer Client service for {eth_network.upper()}",
        user="execution",
        exec_start=_exec_start,
        extra_env=[f'"DOTNET_BUNDLE_EXTRACT_BASE_DIR={BASE_DATA_DIR}/nethermind/bundle-extract"'],
        working_dir=f"{BASE_DATA_DIR}/nethermind",
        timeout_stop_sec=900,
        limit_nofile=None
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Nethermind release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset, release_info_from_github
    repo = "NethermindEth/nethermind"
    data = get_github_release(repo, version_tag)
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("nethermind",),
        prefer_extensions=(".zip", ".tar.gz"),
        client_label="Nethermind",
    )
    return release_info_from_github(data, [download_url], [filename])



def download_and_install_nethermind(eth_network: str, el_p2p_port: str, el_rpc_port: str, 
                                     el_max_peer_count: str, jwtsecret_path: str,
                                     network_override: Optional[str] = None, sync_parameters: str = '',
                                     enable_ws: bool = False) -> Tuple[str, str]:
    """Download and install Nethermind binary and service.

    Args:
        enable_ws: Forwarded to :func:`generate_nethermind_service` (SSV needs localhost WS).

    Returns:
        nm_version: The version string of the installed Nethermind
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "nethermind")
    # Ensure home directory exists for .NET bundle extraction if WorkingDirectory isn't enough
    subprocess.run(["sudo", "mkdir", "-p", "/home/execution"], check=True)
    subprocess.run(["sudo", "chown", "execution:execution", "/home/execution"], check=True)
    
    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    nm_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('nethermind', nm_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_path = download_release_file(info, DOWNLOAD_DIR, "Nethermind")

    subprocess.run(["sudo", "apt-get", "-y", "-qq", "install", "unzip"], check=False)
    extract_and_install(download_path, "nethermind", f"{INSTALL_DIR}/nethermind", "directory", 0)

    # Generate Service File Content
    service_content = generate_nethermind_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, 
        jwtsecret_path, network_override, sync_parameters, enable_ws=enable_ws,
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return nm_version, service_file_path
