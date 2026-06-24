import os
import subprocess
from typing import Optional, Tuple
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, install_system_binary, BASE_DATA_DIR
from client_requirements import validate_version_for_network
from deploy.service_generators import form_exec_start, generate_systemd_template

def generate_erigon_service(eth_network: str, el_p2p_port: str, el_rpc_port: str,
                            el_max_peer_count: str, jwtsecret_path: str,
                            cl_p2p_port: str, cl_rest_port: str, cl_max_peer_count: str,
                            sync_url: str,
                            network_override: Optional[str] = None, sync_parameters: str = '',
                            mev_parameters: str = '') -> str:
    """Generate Erigon+Caplin integrated execution-consensus systemd service file content.

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count
        jwtsecret_path: Path to JWT secret file
        cl_p2p_port: CL P2P port (for Caplin)
        cl_rest_port: CL REST port (for Caplin)
        cl_max_peer_count: CL max peer count (for Caplin)
        sync_url: Checkpoint sync URL
        network_override: Optional network flag override (for ephemery)
        sync_parameters: Optional sync/prune parameters
        mev_parameters: Optional MEV relay URL parameter

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--chain={eth_network}'

    _args = [
        f"{INSTALL_DIR}/erigon",
        f"--datadir={BASE_DATA_DIR}/erigon",
        _network,
        f"--port={el_p2p_port}",
        "--torrent.port=42069",
        f"--http.port={el_rpc_port}",
        f"--maxpeers={el_max_peer_count}",
        "--http.api=web3,eth,net,engine",
        "--metrics",
        "--pprof",
        "--prune.mode=minimal",
        "--private.api.addr=127.0.0.1:9091",
        f"--authrpc.jwtsecret={jwtsecret_path}"
    ]
    
    if sync_parameters:
        _args.append(sync_parameters.strip())
    
    # Caplin flags
    _args.extend([
        "--caplin.enable-upnp",
        "--caplin.discovery.addr=0.0.0.0",
        f"--caplin.discovery.port={cl_p2p_port}",
        f"--caplin.discovery.tcpport={cl_p2p_port}",
        f"--caplin.max-peer-count={cl_max_peer_count}",
        "--beacon.api.addr=127.0.0.1",
        f"--beacon.api.port={cl_rest_port}",
        "--beacon.api=beacon,validator,builder,config,debug,events,node,lighthouse",
        f"--caplin.checkpoint-sync-url={sync_url}/eth/v2/debug/beacon/states/finalized"
    ])

    if mev_parameters:
        _args.append(mev_parameters.strip())

    _exec_start = form_exec_start(_args)

    caplin_mev = bool(mev_parameters and "caplin.mev-relay-url" in mev_parameters)
    return generate_systemd_template(
        description=f"Erigon-Caplin Integrated Execution-Consensus Client for {eth_network.upper()}",
        user="execution",
        exec_start=_exec_start,
        extra_env=None,
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=None,
        unit_after=["mevboost.service"] if caplin_mev else None,
        unit_requires=["mevboost.service"] if caplin_mev else None,
    )

def generate_erigon_standalone_service(eth_network: str, el_p2p_port: str, el_rpc_port: str,
                                       el_max_peer_count: str, jwtsecret_path: str,
                                       network_override: Optional[str] = None, sync_parameters: str = '') -> str:
    """Generate Erigon execution client standalone systemd service file content (without Caplin).

    Args:
        eth_network: Network name
        el_p2p_port: EL P2P port
        el_rpc_port: EL RPC port
        el_max_peer_count: Max peer count
        jwtsecret_path: Path to JWT secret file
        network_override: Optional network flag override (for ephemery)
        sync_parameters: Optional sync/prune parameters

    Returns:
        Service file content as a string
    """
    if network_override:
        _network = network_override
    else:
        _network = f'--chain={eth_network}'

    _args = [
        f"{INSTALL_DIR}/erigon",
        f"--datadir={BASE_DATA_DIR}/erigon",
        _network,
        f"--port={el_p2p_port}",
        "--torrent.port=42069",
        f"--http.port={el_rpc_port}",
        f"--maxpeers={el_max_peer_count}",
        "--http.api=web3,eth,net,engine",
        "--metrics",
        "--pprof",
        "--prune.mode=minimal",
        "--private.api.addr=127.0.0.1:9091",
        f"--authrpc.jwtsecret={jwtsecret_path}"
    ]
    if sync_parameters:
        _args.append(sync_parameters.strip())
    _args.append("--externalcl")

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Erigon Execution Layer Client service for {eth_network.upper()}",
        user="execution",
        exec_start=_exec_start,
        extra_env=None,
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=None
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Erigon release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset
    data = get_github_release("erigontech/erigon", version_tag)
    tag = data["tag_name"]
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("erigon",),
        client_label="Erigon",
    )
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def download_and_install_erigon(eth_network: str, el_p2p_port: str, el_rpc_port: str, el_max_peer_count: str, 
                                 jwtsecret_path: str, cl_p2p_port: str, cl_rest_port: str, cl_max_peer_count_cl: str,
                                 checkpoint_sync_url: str, mev_parameters: str = '') -> Tuple[str, str]:
    """Download and install Erigon binary and service.

    Returns:
        erigon_version: The version string of the installed Erigon
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "erigon")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    erigon_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('erigon', erigon_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Erigon")

    # Extract the binary using sudo
    # Erigon tarball typically contains a folder, so we strip one component and extract to /usr/local/bin
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}", "--strip-components=1"], check=True)
    # Ensure binary is configured correctly
    install_system_binary(f"{INSTALL_DIR}/erigon", os.path.join(INSTALL_DIR, "erigon"))

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_erigon_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count,
        jwtsecret_path, cl_p2p_port, cl_rest_port, cl_max_peer_count_cl,
        checkpoint_sync_url, mev_parameters=mev_parameters
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return erigon_version, service_file_path


def download_and_install_erigon_standalone(eth_network: str, el_p2p_port: str, el_rpc_port: str, el_max_peer_count: str, 
                                           jwtsecret_path: str) -> Tuple[str, str]:
    """Download and install Erigon binary and service as a standalone execution client.

    Returns:
        erigon_version: The version string of the installed Erigon
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "erigon")

    # Resolve version and download URL using local get_release_info
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    erigon_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('erigon', erigon_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Erigon Standalone")

    # Extract the binary using sudo
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}", "--strip-components=1"], check=True)
    subprocess.run(["sudo", "chmod", "a+x", f"{INSTALL_DIR}/erigon"], check=True)
    install_system_binary(f"{INSTALL_DIR}/erigon", os.path.join(INSTALL_DIR, "erigon"))

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_erigon_standalone_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, jwtsecret_path
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return erigon_version, service_file_path
