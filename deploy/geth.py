import os
import subprocess
from typing import Tuple, Optional
from deploy.service_generators import generate_geth_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, install_system_binary
from client_requirements import validate_version_for_network

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Geth release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    import requests
    import re
    res = requests.get("https://geth.ethereum.org/downloads")
    res.raise_for_status()
    arch = "amd64" if arch_amd64 else "arm64"
    base = rf"https://gethstore\.blob\.core\.windows\.net/builds/geth-linux-{arch}-"

    if version_tag.upper() == "LATEST":
        pattern = rf"({base}([0-9.]+)-[a-f0-9]+\.tar\.gz)"
        matches = re.findall(pattern, res.text)
        if not matches:
            raise ValueError(f"Could not find Geth download URL for linux-{arch} and version {version_tag}")
        download_url, version_num = matches[0]
        version = f"v{version_num}"
    else:
        ver = version_tag.removeprefix("v")
        pattern = base + re.escape(ver) + r"-[a-f0-9]+\.tar\.gz"
        matches = re.findall(pattern, res.text)
        if not matches:
            raise ValueError(f"Could not find Geth download URL for linux-{arch} and version {version_tag}")
        download_url = matches[0]
        version = f"v{ver}"

    filename = download_url.split("/")[-1]
    return {"version": version, "download_urls": [download_url], "filenames": [filename]}



def download_and_install_geth(eth_network: str, el_p2p_port: str, el_rpc_port: str, 
                                el_max_peer_count: str, jwtsecret_path: str,
                                network_override: Optional[str] = None) -> Tuple[str, str]:
    """Download and install Geth binary and service.

    Returns:
        geth_version: The version string of the installed Geth
        service_file_path: The path to the created service file
    """
    # Create User and directories
    setup_client_user_and_dir("execution", "geth")

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    geth_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('geth', geth_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]


    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Geth")

    # Extract the binary to /usr/local/bin/geth using sudo
    
    # Extract to a temporary directory in DOWNLOAD_DIR, INSTALL_DIR
    temp_extract_dir = f"{DOWNLOAD_DIR}/geth_temp"
    subprocess.run(["mkdir", "-p", temp_extract_dir], check=True)
    subprocess.run(["tar", "xzf", download_path, "-C", temp_extract_dir], check=True)

    # Find the geth binary and move it
    extracted_dirs = [d for d in os.listdir(temp_extract_dir) if d.startswith("geth-linux")]
    if not extracted_dirs:
        print("Error: Could not find geth binary after extracting archive.")
        exit(1)
    geth_bin_path = f"{temp_extract_dir}/{extracted_dirs[0]}/geth"
    install_system_binary(geth_bin_path, f"{INSTALL_DIR}/geth")
    
    # Cleanup temp directory
    subprocess.run(["rm", "-rf", temp_extract_dir])

    # Remove the tar file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_geth_service(
        eth_network, el_p2p_port, el_rpc_port, el_max_peer_count, 
        jwtsecret_path, network_override
    )
    
    service_file_path = '/etc/systemd/system/execution.service'
    write_service_file(service_content, service_file_path, 'execution_temp.service')

    return geth_version, service_file_path
