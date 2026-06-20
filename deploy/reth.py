import os
import subprocess
from deploy.common import install_system_binary, write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, BASE_DATA_DIR
from client_requirements import validate_version_for_network
from typing import Tuple, Optional
from deploy.service_generators import form_exec_start, generate_systemd_template

def generate_reth_service(eth_network: str, el_p2p_port: str, el_p2p_port_2: str,
                          el_rpc_port: str, el_max_peer_count: str, jwtsecret_path: str,
                          network_override: Optional[str] = None, sync_parameters: str = '') -> str:
    """Generate Reth execution client systemd service file content.

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_p2p_port_2: EL secondary P2P port (discv5)
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count (already halved for reth)
        jwtsecret_path: Path to JWT secret file
        network_override: Optional network flag override (for ephemery)
        sync_parameters: Optional sync/prune parameters

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--chain {eth_network}'

    _args = [
        f"{INSTALL_DIR}/reth node",
        _network,
        "--full",
        f"--datadir={BASE_DATA_DIR}/reth",
        f"--log.file.directory={BASE_DATA_DIR}/reth/logs",
        "--metrics 127.0.0.1:6060",
        f"--port {el_p2p_port}",
        f"--discovery.port {el_p2p_port}",
        "--enable-discv5-discovery",
        f"--discovery.v5.port {el_p2p_port_2}",
        f"--max-outbound-peers {el_max_peer_count}",
        f"--max-inbound-peers {el_max_peer_count}",
        "--http",
        f"--http.port {el_rpc_port}",
        "--http.api=\"rpc,eth,web3,net,debug\"",
        f"--authrpc.jwtsecret {jwtsecret_path}"
    ]
    if sync_parameters:
        _args.append(sync_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Reth Execution Layer Client service for {eth_network.upper()}",
        user="execution",
        exec_start=_exec_start,
        extra_env=['RUST_LOG=info'],
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=None
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Reth release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset
    data = get_github_release("paradigmxyz/reth", version_tag)
    tag = data["tag_name"]
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("reth",),
        client_label="Reth",
    )
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_and_install_reth(eth_network: str, el_p2p_port: str, el_p2p_port_2: str,
                                el_rpc_port: str, el_max_peer_count: str, jwtsecret_path: str,
                                network_override: Optional[str] = None, sync_parameters: str = '') -> Tuple[str, str]:
    """Download and install Reth binary and service.

    Returns:
        reth_version: The version string of the installed Reth
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "reth")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    reth_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('reth', reth_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Reth")

    # Extract the binary to /usr/local/bin/ using sudo
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}"], check=True)

    # v2.3+ ships ``reth`` at archive root; older reproducible builds used ``reth-*``.
    dest_path = os.path.join(INSTALL_DIR, "reth")
    find_result = subprocess.run(
        ["sudo", "find", INSTALL_DIR, "-maxdepth", "1", "-type", "f", "-name", "reth*"],
        capture_output=True,
        text=True,
        check=True,
    )
    matches = [line.strip() for line in find_result.stdout.splitlines() if line.strip()]
    if not matches:
        print("Error: Could not find reth binary after extracting archive.")
        exit(1)
    src_path = next((path for path in matches if os.path.basename(path) == "reth"), matches[0])
    if os.path.abspath(src_path) != os.path.abspath(dest_path):
        subprocess.run(["sudo", "mv", src_path, dest_path], check=True)

    install_system_binary(dest_path, dest_path)

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_reth_service(
        eth_network, el_p2p_port, el_p2p_port_2, el_rpc_port, 
        el_max_peer_count, jwtsecret_path, network_override, sync_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return reth_version, service_file_path
