import os
import subprocess
from typing import List, Dict, Tuple
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, download_file, get_machine_architecture, install_system_binary

def generate_mevboost_service(eth_network: str, mev_min_bid: str, relay_options: List[Dict[str, str]]) -> str:
    """Generate MEV-Boost systemd service file content.

    Args:
        eth_network: Network name (e.g. 'mainnet', 'hoodi')
        mev_min_bid: Minimum bid value string (e.g. '0.006')
        relay_options: List of dicts with 'name' and 'url' keys

    Returns:
        Service file content as a string
    """
    lines = [
        '[Unit]',
        f'Description=MEV-Boost Service for {eth_network.upper()}',
        'After=network-online.target',
        'Wants=network-online.target',
        'Documentation=https://docs.coincashew.com',
        '',
        '[Service]',
        'User=mevboost',
        'Group=mevboost',
        'Type=simple',
        'Restart=always',
        'RestartSec=5',
        f'ExecStart={INSTALL_DIR}/mev-boost \\',
        f'    -{eth_network} \\',
        f'    -min-bid {mev_min_bid} \\',
        '    -relay-check \\',
    ]

    for relay in relay_options:
        relay_line = f'    -relay {relay["url"]} \\'
        lines.append(relay_line)

    # Remove the trailing '\' from the last relay line
    lines[-1] = lines[-1].rstrip(' \\')

    lines.extend([
        '',
        '[Install]',
        'WantedBy=multi-user.target',
    ])
    return '\n'.join(lines)


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get MEV-Boost release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset
    data = get_github_release("flashbots/mev-boost", version_tag)
    tag = data["tag_name"]
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("mev-boost",),
        client_label="MEV-Boost",
    )
    return {"version": tag, "download_urls": [download_url], "filenames": [filename]}



def install_mevboost(eth_network: str, mev_min_bid: str, relay_options: List[Dict[str, str]]) -> Tuple[str, str]:
    """Install MEV-Boost binary and service.

    Returns:
        mevboost_version: The version string of the installed MEV-Boost
        service_file_path: The path to the created service file
    """
    # Step 1: Create mevboost service account
    if subprocess.run(["id", "-u", "mevboost"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "mevboost"], check=True)

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
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", f"{INSTALL_DIR}"], check=True)

    # Ensure binary is moved/configured and follows system best-practices
    install_system_binary(f"{INSTALL_DIR}/mev-boost", os.path.join(INSTALL_DIR, "mev-boost"))

    # Remove the downloaded .tar.gz file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_mevboost_service(eth_network, mev_min_bid, relay_options)
    
    service_file_path = '/etc/systemd/system/mevboost.service'
    write_service_file(service_content, service_file_path, 'mev_boost_temp.service')

    return mevboost_version, service_file_path
