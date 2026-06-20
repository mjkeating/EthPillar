import os
import subprocess
from deploy.common import install_system_binary, write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, BASE_DATA_DIR
from client_requirements import validate_version_for_network
from typing import Tuple, Optional
from deploy.service_generators import form_exec_start, generate_systemd_template

def generate_ethrex_service(eth_network: str, el_p2p_port: str, el_rpc_port: str,
                            el_max_peer_count: str, jwtsecret_path: str,
                            network_override: Optional[str] = None) -> str:
    """Generate Ethrex execution client systemd service file content.

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count
        jwtsecret_path: Path to JWT secret file
        network_override: Optional network flag override (for ephemery custom config)

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--network {eth_network.lower()}'

    _args = [
        f"{INSTALL_DIR}/ethrex",
        f"--datadir {BASE_DATA_DIR}/ethrex",
        _network,
        f"--p2p.port {el_p2p_port}",
        f"--discovery.port {el_p2p_port}",
        "--http.addr 0.0.0.0",
        f"--http.port {el_rpc_port}",
        "--ws.enabled",
        "--ws.addr 0.0.0.0",
        "--ws.port 8546",
        "--metrics",
        "--metrics.addr 0.0.0.0",
        "--metrics.port 6060",
        f"--authrpc.jwtsecret {jwtsecret_path}",
        "--authrpc.addr 0.0.0.0",
        "--authrpc.port 8551",
        "--syncmode snap"
    ]
    
    if el_max_peer_count:
        _args.append(f"--p2p.target-peers {el_max_peer_count}")

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Ethrex Execution Layer Client service for {eth_network.upper()}",
        user="execution",
        exec_start=_exec_start,
        extra_env=['RUST_LOG=info'],
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=None
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Ethrex release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release
    data = get_github_release("lambdaclass/ethrex", version_tag)
    tag = data["tag_name"]
    
    arch_str = "x86_64" if arch_amd64 else "aarch64"
    
    filename = None
    download_url = None
    for asset in data.get("assets", []):
        name = asset["name"]
        if name.startswith("ethrex-linux") and arch_str in name and not name.endswith(".tar.gz") and not name.endswith(".zip"):
            filename = name
            download_url = asset["browser_download_url"]
            break
            
    if not filename:
        raise ValueError(f"Could not find Ethrex download URL for linux-{arch_str} and version {version_tag}")
        
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}

def download_and_install_ethrex(eth_network: str, el_p2p_port: str, el_rpc_port: str,
                                el_max_peer_count: str, jwtsecret_path: str,
                                network_override: Optional[str] = None) -> Tuple[str, str]:
    """Download and install Ethrex binary and service.

    Returns:
        ethrex_version: The version string of the installed Ethrex
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "ethrex")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    ethrex_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('ethrex', ethrex_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Ethrex")

    # Move binary into place and ensure permissions
    dest_path = os.path.join(INSTALL_DIR, "ethrex")
    install_system_binary(download_path, dest_path)

    # Remove the downloaded file if it's still there (install_system_binary moves it, but just in case)
    if os.path.exists(download_path):
        os.remove(download_path)

    # Generate Service File Content
    service_content = generate_ethrex_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, 
        jwtsecret_path, network_override
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return ethrex_version, service_file_path
