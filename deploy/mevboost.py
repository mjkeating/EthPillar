import os
import subprocess
from typing import List, Dict, Tuple
from deploy.service_generators import generate_mevboost_service
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, download_file, get_machine_architecture, install_system_binary

def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get MEV-Boost release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release
    data = get_github_release("flashbots/mev-boost", version_tag)
    tag = data["tag_name"]
    arch = "amd64" if arch_amd64 else "arm64"
    download_url = None
    filename = None
    for asset in data["assets"]:
        if asset["name"].lower().endswith(f"linux_{arch}.tar.gz"):
            download_url = asset["browser_download_url"]
            filename = asset["name"]
            break
    if not download_url:
        raise ValueError(f"Could not find mev-boost asset for linux_{arch}")
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def install_mevboost(eth_network: str, mev_min_bid: str, relay_options: List[Dict[str, str]]) -> Tuple[str, str]:
    """Install MEV-Boost binary and service.

    Returns:
        mevboost_version: The version string of the installed MEV-Boost
        service_file_path: The path to the created service file
    """
    # Step 1: Create mevboost service account
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "mevboost"])

    # Step 2: Install mevboost
    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    mevboost_version = info["version"]

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "mevboost")

    # Extract the binary
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}"])

    # Ensure binary is moved/configured and follows system best-practices
    install_system_binary(f"{INSTALL_DIR}/mev-boost", os.path.join(INSTALL_DIR, "mev-boost"))

    # Remove the downloaded .tar.gz file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_mevboost_service(eth_network, mev_min_bid, relay_options)
    
    service_file_path = '/etc/systemd/system/mevboost.service'
    write_service_file(service_content, service_file_path, 'mev_boost_temp.service')

    return mevboost_version, service_file_path
