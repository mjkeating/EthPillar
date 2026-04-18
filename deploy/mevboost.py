import os
import requests
import tarfile
import subprocess
from tqdm import tqdm
from typing import List, Dict, Tuple
from deploy.service_generators import generate_mevboost_service
from deploy.common import write_service_file, get_machine_architecture, DOWNLOAD_DIR, get_computer_platform

def install_mevboost(eth_network: str, mev_min_bid: str, relay_options: List[Dict[str, str]]) -> Tuple[str, str]:
    """Install MEV-Boost binary and service.

    Returns:
        mevboost_version: The version string of the installed MEV-Boost
        service_file_path: The path to the created service file
    """
    binary_arch = get_machine_architecture()
    platform_arch = get_computer_platform()

    # Step 1: Create mevboost service account
    subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "mevboost"])

    # Step 2: Install mevboost
    # Define the Github API endpoint to get the latest release
    url = 'https://api.github.com/repos/flashbots/mev-boost/releases/latest'

    # Send a GET request to the API endpoint
    response = requests.get(url)
    mevboost_version = response.json()['tag_name']

    # Search for the asset with the name that ends in {platform_arch}_{binary_arch}.tar.gz
    assets = response.json()['assets']
    download_url = None
    asset_name = None
    for asset in assets:
        if asset['name'].endswith(f'{platform_arch.lower()}_{binary_arch}.tar.gz'):
            download_url = asset['browser_download_url']
            asset_name = asset['name']
            break

    if download_url is None:
        print("Error: Could not find the download URL for the latest release.")
        exit(1)

    # Download the latest release binary
    print(f">> Downloading mevboost > URL: {download_url}")
    download_path = f"{DOWNLOAD_DIR}/mev-boost.tar.gz"

    try:
        # Download the file
        response = requests.get(download_url, stream=True)
        response.raise_for_status()  # Raise an exception for HTTP errors
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024
        t = tqdm(total=total_size, unit='B', unit_scale=True)

        with open(download_path, "wb") as f:
            for chunk in response.iter_content(block_size):
                if chunk:
                    t.update(len(chunk))
                    f.write(chunk)
        t.close()
        print(f">> Successfully downloaded: mev-boost.tar.gz")

    except requests.exceptions.RequestException as e:
        print(f"Error: Unable to download file. Try again later. {e}")
        exit(1)

    # Extract the binary
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/usr/local/bin"])

    # Ensure +x permissions, update owner
    subprocess.run(["sudo", "chmod", "a+x", "/usr/local/bin/mev-boost"])
    subprocess.run(["sudo", "chown", "mevboost:mevboost", "/usr/local/bin/mev-boost"])

    # Remove the downloaded .tar.gz file
    os.remove(download_path)

    # Generate Service File Content
    service_content = generate_mevboost_service(eth_network, mev_min_bid, relay_options)
    
    service_file_path = '/etc/systemd/system/mevboost.service'
    write_service_file(service_content, service_file_path, 'mev_boost_temp.service')

    return mevboost_version, service_file_path
