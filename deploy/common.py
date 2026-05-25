import os
import sys
import re
import platform
import subprocess
import requests
import json
import tarfile
import tempfile
from consolemenu import PromptUtils, Screen
from typing import Optional, List
import glob
import traceback


INSTALL_DIR = "/usr/local/bin"
DOWNLOAD_DIR = "/tmp"
BASE_DATA_DIR = "/var/lib"


def install_system_binary(src_path: str, dest: str) -> str:
    """Move or install a binary and enforce secure perms/ownership.

    Args:
        src_path: Path to the source binary (can be a temp file path or already in INSTALL_DIR).
        dest: Either a destination filename (e.g. 'lodestar') which will be placed
              under `INSTALL_DIR`, or a full destination path (e.g. '/usr/local/bin/lodestar').

    Returns:
        The absolute destination path.
    """
    # Accept either a bare filename or a full path for destination
    if os.path.isabs(dest) or ("/" in dest):
        dest_path = dest
    else:
        dest_path = os.path.join(INSTALL_DIR, dest)
    try:
        # Ensure destination directory exists
        dest_dir = os.path.dirname(dest_path) or INSTALL_DIR
        subprocess.run(["sudo", "mkdir", "-p", dest_dir], check=True)
        # If src and dest are same, skip moving
        if os.path.abspath(src_path) != os.path.abspath(dest_path):
            subprocess.run(["sudo", "mv", src_path, dest_path], check=True)
        # Ensure it's executable and has 755
        subprocess.run(["sudo", "chmod", "+x", dest_path], check=False)
        subprocess.run(["sudo", "chmod", "755", dest_path], check=True)
        # Ensure owned by root:root
        subprocess.run(["sudo", "chown", "root:root", dest_path], check=True)
    except Exception:
        # Best-effort helper: don't raise to avoid breaking installs that partially succeed
        pass
    return dest_path


def install_system_directory(src_dir: str, dest_dir: str, service_user: Optional[str] = None, writable_subdirs: Optional[List[str]] = None) -> str:
    """Move a directory into place under the system path and harden permissions.

    This will move `src_dir` to `dest_dir`, set ownership of the tree to root:root,
    set directory permissions to 755 and regular files to 644 while preserving
    executable bits for files that are already executable. Optionally create
    writable subdirectories and chown them to `service_user`.

    Args:
        src_dir: Source directory to move (local path).
        dest_dir: Full destination directory path.
        service_user: Optional username to own writable subdirs.
        writable_subdirs: Optional list of subdirectory paths (relative to dest_dir)
                          to create and chown to `service_user` (e.g. ['data', 'logs']).

    Returns:
        The absolute destination directory.
    """
    try:
        print(f">> Installing to {dest_dir} from {src_dir}")
        # Ensure parent exists and remove any old install
        parent = os.path.dirname(dest_dir)
        subprocess.run(["sudo", "mkdir", "-p", parent], check=True)
        subprocess.run(["sudo", "rm", "-rf", dest_dir], check=False)

        # Move into place
        subprocess.run(["sudo", "mv", src_dir, dest_dir], check=True)

        # Set ownership to root:root and tighten perms
        subprocess.run(["sudo", "chown", "-R", "root:root", dest_dir], check=True)
        # Ensure directories are accessible
        subprocess.run(["sudo", "find", dest_dir, "-type", "d", "-exec", "chmod", "755", "{}", ";"], check=True)
        # Preserve files that are executable and ensure they remain executable.
        # Then normalize all non-executable regular files to 644. This order
        # prevents clearing execute bits and avoids a race where we would
        # set everything to 644 and then be unable to detect previously
        # executable files.
        subprocess.run(["sudo", "find", dest_dir, "-type", "f", "-perm", "/111", "-exec", "chmod", "755", "{}", ";"], check=False)
        subprocess.run(["sudo", "find", dest_dir, "-type", "f", "!", "-perm", "/111", "-exec", "chmod", "644", "{}", ";"], check=True)

        # Create writable subdirs and assign to service_user if provided
        if writable_subdirs:
            for sub in writable_subdirs:
                full = os.path.join(dest_dir, sub)
                subprocess.run(["sudo", "mkdir", "-p", full], check=True)
                if service_user:
                    subprocess.run(["sudo", "chown", "-R", f"{service_user}:{service_user}", full], check=True)
                    subprocess.run(["sudo", "chmod", "700", full], check=True)
                else:
                    subprocess.run(["sudo", "chmod", "750", full], check=True)
    except Exception:
        print(">> Exception in install_system_directory:")
        traceback.print_exc()
    return dest_dir

def setup_client_user_and_dir(user: str, client_name: str) -> None:
    """Create a system user and data directory with proper permissions.

    Args:
        user: The username to create.
        client_name: The directory name (under BASE_DATA_DIR)
    """
    data_dir = os.path.join(BASE_DATA_DIR, client_name)

    # Check if the user already exists
    user_exists = False
    try:
        with open("/etc/passwd", "r") as f:
            for line in f:
                if line.startswith(f"{user}:"):
                    user_exists = True
                    break
    except Exception:
        # Fallback if /etc/passwd is not directly readable
        res = subprocess.run(["id", "-u", user], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        user_exists = (res.returncode == 0)

    # 1. Ensure the group exists first to prevent useradd from failing if the group exists
    subprocess.run(["sudo", "groupadd", "-f", user], stderr=subprocess.DEVNULL, check=False)

    if not user_exists:
        # 2. Try creating the user with the specified primary group
        res = subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "-g", user, user],
                             stderr=subprocess.DEVNULL, check=False)
        if res.returncode != 0:
            # 3. Fallback to standard useradd if the primary group specification failed
            subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", user], 
                           stderr=subprocess.DEVNULL, check=False)

    # 4. Create the data directory and set correct permissions
    subprocess.run(["sudo", "mkdir", "-p", data_dir], check=True)
    subprocess.run(["sudo", "chown", "-R", f"{user}:{user}", data_dir], check=True)


def download_file(url: str, dest_path: str, label: str = "file") -> None:
    """Download a file with a progress bar and basic error handling.

    Args:
        url: The URL to download from.
        dest_path: Absolute destination file path.
        label: Descriptive label for the download.
    """
    from tqdm import tqdm
    print(f">> Downloading {label} > URL: {url}")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024
        t = tqdm(total=total_size, unit='B', unit_scale=True)

        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(block_size):
                if chunk:
                    t.update(len(chunk))
                    f.write(chunk)
        t.close()
        filename = os.path.basename(dest_path)
        print(f">> Successfully downloaded: {filename}")
    except requests.exceptions.RequestException as e:
        print(f"Error: Unable to download file. Try again later. {e}")
        exit(1)


def ensure_java_available() -> bool:
    """Ensure a Java runtime is available on the system.

    Returns True if Java is available or was installed successfully,
    False otherwise. This helper performs a best-effort install of a
    headless OpenJDK package when run with sufficient privileges.
    """
    import shutil
    # If java is already on PATH, we're done
    if shutil.which("java"):
        print(">> Java runtime already available on PATH")
        return True

    # Try installing a common headless JRE package
    try:
        print(">> Java not found on PATH; attempting to install OpenJDK headless")
        res = subprocess.run(["sudo", "apt-get", "-y", "-qq", "install", "openjdk-21-jre-headless"], check=False)
        if res.returncode == 0:
            print(">> OpenJDK installed")
            return True
        else:
            print(">> Failed to install OpenJDK via apt; return code:", res.returncode)
            return False
    except Exception as e:
        print(">> Exception while attempting to install Java:", e)
        return False


def clear_screen() -> None:
    """Clear the terminal screen based on the operating system."""
    if os.name == 'posix':  # Unix-based systems (e.g., Linux, macOS)
        os.system('clear')
    elif os.name == 'nt':   # Windows
        os.system('cls')


def get_machine_architecture() -> str:
    """Get the machine architecture mapped to amd64 or arm64.

    Returns:
        Mapped architecture string.
    """
    machine_arch = platform.machine()
    if machine_arch == "x86_64":
        return "amd64"
    elif machine_arch == "aarch64":
        return "arm64"
    else:
        print(f'Unsupported machine architecture: {machine_arch}')
        exit(1)


def get_raw_architecture() -> str:
    """Get the raw machine architecture string."""
    return platform.machine()


def get_computer_platform() -> str:
    """Get the operating system platform name.

    Returns:
        Platform name string (e.g. 'Linux').
    """
    platform_name = platform.system()
    if platform_name == "Linux":
        return platform_name
    else:
        print(f'Unsupported platform: {platform_name}')
        exit(1)


def is_valid_eth_address(address: str) -> bool:
    """Validate if a string is a valid Ethereum address.

    Args:
        address: The address string to validate.

    Returns:
        True if valid, False otherwise.
    """
    pattern = re.compile("^0x[a-fA-F0-9]{40}$")
    return bool(pattern.match(address))


def validate_beacon_node_address(ip_port: str) -> bool:
    """Validate if a string is a valid beacon node address URL.

    Args:
        ip_port: The URL string to validate.

    Returns:
        True if valid, False otherwise.
    """
    pattern = r"^(http|https|ws):\/\/((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(:?\d{1,5})?$"
    return bool(re.match(pattern, ip_port))

VALID_NETWORKS = ['MAINNET', 'HOODI', 'EPHEMERY', 'HOLESKY', 'SEPOLIA']

def network_type(s: str) -> str:
    """Argparse type for case-insensitive network selection.

    Args:
        s: The network name string.

    Returns:
        Uppercase network name.

    Raises:
        argparse.ArgumentTypeError: If the network is invalid.
    """
    s_upper = s.upper()
    if s_upper not in VALID_NETWORKS:
         import argparse
         raise argparse.ArgumentTypeError(f"Invalid network: {s}. Choose from {VALID_NETWORKS}")
    return s_upper


def setup_ephemery_network(genesis_repository: str) -> None:
    """Setup ephemery network by downloading genesis files.

    Args:
        genesis_repository: GitHub repository for genesis files.
    """
    testnet_dir = "/opt/ethpillar/testnet"

    def get_github_release(repo: str) -> Optional[str]:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        response = requests.get(url)
        if response.status_code == 200:
            data = json.loads(response.text)
            return data.get('tag_name')
        else:
            return None

    def download_genesis_release(genesis_release: str) -> None:
        # remove old genesis and setup dir
        if os.path.exists(testnet_dir):
            subprocess.run(['sudo', 'rm', '-rf', testnet_dir], check=True)
        subprocess.run(['sudo', 'mkdir', '-p', testnet_dir], check=True)
        subprocess.run(['sudo', 'chmod', '-R', '755', testnet_dir], check=True)

        # get latest genesis
        url = f"https://github.com/{genesis_repository}/releases/download/{genesis_release}/testnet-all.tar.gz"
        print(f">> Downloading {genesis_release} genesis files > URL: {url}")
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            temp_dir = tempfile.mkdtemp()
            with tarfile.open(fileobj=response.raw, mode='r|gz') as tar:
                tar.extractall(f"{temp_dir}")
            subprocess.run(['sudo', 'sh', '-c', f'mv {temp_dir}/* {testnet_dir}'], check=True)
            print(f">> Successfully downloaded {genesis_release} genesis files")
        else:
            print("Failed to download genesis release")

    genesis_release = get_github_release(genesis_repository)
    if genesis_release:
        download_genesis_release(genesis_release)
    else:
        print(f"Failed to retrieve genesis release for {genesis_repository}")


def setup_node(jwt_secret_path: str, validator_only: bool = False) -> None:
    """Setup node dependencies and JWT secret.

    Args:
        jwt_secret_path: Path to save the JWT secret.
        validator_only: If True, only setup validator-specific parts.
    """
    if not validator_only:
        # Generate JWT secret only if it doesn't exist (don't regenerate on client switch)
        if not os.path.exists(jwt_secret_path):
            # Create JWT directory
            jwt_dir = os.path.dirname(jwt_secret_path)
            subprocess.run(['sudo', 'mkdir', '-p', jwt_dir], check=True)

            # Generate random hex string and save to file
            rand_hex = subprocess.run(['openssl', 'rand', '-hex', '32'], stdout=subprocess.PIPE, check=True)
            subprocess.run(['sudo', 'tee', jwt_secret_path], input=rand_hex.stdout, stdout=subprocess.DEVNULL, check=True)

    # Update and upgrade packages
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'update'])
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'upgrade'])

    # Autoremove packages
    subprocess.run(['sudo', 'apt', '-y', '-qq' , 'autoremove'])

    # Chrony timesync package
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'install', 'chrony'])

def write_service_file(content: str, target_path: str, temp_filename: str = 'temp.service') -> None:
    """Write service content to a target path using a temporary file.

    Args:
        content: The service file content.
        target_path: The final destination path.
        temp_filename: Temporary filename to use.
    """
    # Prepend PID to avoid race conditions when multiple containers share the same mapped volume directory
    # Use /tmp for absolute path to avoid working directory issues
    import tempfile
    temp_dir = tempfile.gettempdir()
    actual_temp_filename = os.path.join(temp_dir, f"{os.getpid()}_{temp_filename}")
    with open(actual_temp_filename, 'w') as f:
        f.write(content)
    subprocess.run(['sudo', 'cp', actual_temp_filename, target_path], check=True)
    try:
        os.remove(actual_temp_filename)
    except FileNotFoundError:
        pass

def finish_install(install_config: str, eth_network: str, sync_url: str,
                   execution_client: Optional[str], execution_version: Optional[str], execution_service_path: Optional[str],
                   consensus_client: Optional[str], consensus_version: Optional[str], consensus_service_path: Optional[str],
                   mevboost_enabled: bool, mevboost_version: Optional[str], mevboost_service_path: Optional[str],
                   validator_enabled: bool, validator_service_path: Optional[str],
                   validator_only: bool, bn_address: Optional[str], node_only: bool, fee_recipient_address: Optional[str],
                   skip_prompts: bool, cl_rest_port: str,
                   vc_name: str = '', vc_ver: str = '') -> None:
    """Display installation summary and optionally start/enable services.

    Args:
        install_config: Formatted configuration string.
        eth_network: Network name.
        sync_url: Checkpoint sync URL.
        execution_client: Execution client name.
        execution_version: Execution client version.
        execution_service_path: Path to execution service file.
        consensus_client: Consensus client name.
        consensus_version: Consensus client version.
        consensus_service_path: Path to consensus service file.
        mevboost_enabled: Whether MEV-Boost is enabled.
        mevboost_version: MEV-Boost version.
        mevboost_service_path: Path to MEV-Boost service file.
        validator_enabled: Whether validator is enabled.
        validator_service_path: Path to validator service file.
        validator_only: Whether this is a validator-only install.
        bn_address: Beacon node address.
        node_only: Whether this is a node-only install.
        fee_recipient_address: Fee recipient address.
        skip_prompts: Whether to skip interactive prompts.
        cl_rest_port: Consensus client REST port.
    """
    
    # Reload the systemd daemon
    try:
        subprocess.run(['sudo', 'systemctl', 'daemon-reload'], capture_output=True)
    except Exception:
        pass

    print(f'##########################\n')
    print(f'## Installation Summary ##\n')
    print(f'##########################\n')

    print(f'Installation Configuration: \n{install_config}\n')

    if execution_client and execution_version:
        print(f'{execution_client.capitalize()} Version: \n{execution_version}\n')

    if consensus_client and consensus_version:
        print(f'{consensus_client.capitalize()} Version: \n{consensus_version}\n')

    if vc_name and vc_ver and vc_name != consensus_client:
        print(f'{vc_name.capitalize()} (VC) Version: \n{vc_ver}\n')

    if mevboost_enabled and not validator_only:
        print(f'Mevboost Version: \n{mevboost_version}\n')

    print(f'Network: {eth_network.upper()}\n')

    if not validator_only:
        print(f'CheckPointSyncURL: {sync_url}\n')

    if validator_only and bn_address:
        print(f'Beacon Node Address: {bn_address}\n')
        target_dir = os.path.expanduser("~/git/ethpillar")
        if os.path.exists(target_dir):
            os.chdir(target_dir)
            subprocess.run(['cp', '.env.overrides.example', '.env.overrides'], check=True)

    if not node_only:
        print(f'Validator Fee Recipient Address: {fee_recipient_address}\n')

    print(f'Systemd service files created:')
    if not validator_only:
        if consensus_service_path:
            print(f'\n{consensus_service_path}')
        if execution_service_path:
            print(f'{execution_service_path}')
    if validator_enabled:
        if validator_service_path:
            print(f'{validator_service_path}')
    if mevboost_enabled and not validator_only:
        if mevboost_service_path:
            print(f'{mevboost_service_path}')

    if skip_prompts:
        print(f'\nNon-interactive install successful! Skipped prompts.')
        exit(0)

    # Prompt to start services
    if not skip_prompts and not validator_only:
        e_client = (execution_client or "execution").capitalize()
        c_client = (consensus_client or "consensus").capitalize()
        message = f"\nInstallation successful!\nSyncing a {c_client}/{e_client} node for validator duties can be as quick as a few hours.\nWould you like to start syncing now?"
        answer = PromptUtils(Screen()).prompt_for_yes_or_no(message)
        if answer:
            services = []
            if execution_service_path:
                services.append('execution')
            if consensus_service_path:
                services.append('consensus')
            if services:
                subprocess.run(['sudo', 'systemctl', 'start'] + services, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if mevboost_enabled:
                subprocess.run(['sudo', 'systemctl', 'start', 'mevboost'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    # Prompt to enable autostart services
    if not skip_prompts:
        answer = PromptUtils(Screen()).prompt_for_yes_or_no(f"\nConfigure node to autostart:\nWould you like this node to autostart when system boots up?")
        if answer:
            if not validator_only:
                services = []
                if execution_service_path:
                    services.append('execution')
                if consensus_service_path:
                    services.append('consensus')
                if services:
                    subprocess.run(['sudo', 'systemctl', 'enable'] + services, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if validator_enabled:
                subprocess.run(['sudo', 'systemctl', 'enable', 'validator'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if mevboost_enabled and not validator_only:
                subprocess.run(['sudo', 'systemctl', 'enable', 'mevboost'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    if skip_prompts:
        exit(0)


def get_github_release(repo: str, version_tag: str) -> dict:
    """Helper function to fetch release info from GitHub API."""
    import requests
    if version_tag.upper() == "LATEST":
        suffix = "latest"
    else:
        suffix = f"tags/{version_tag}"
    url = f"https://api.github.com/repos/{repo}/releases/{suffix}"
    res = requests.get(url)
    res.raise_for_status()
    return res.json()


def get_client_release_info(client: str, version_tag: str = "LATEST") -> dict:
    """Get the correct release version, download URL(s), and filename(s) for a given client.

    Args:
        client: The name of the client (case-insensitive).
        version_tag: 'LATEST' or a specific tag name.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    client = client.lower()
    
    # Normalize client name to module name
    if client in ["mevboost", "mev-boost"]:
        module_name = "mevboost"
    else:
        module_name = client

    import importlib
    try:
        module = importlib.import_module(f"deploy.{module_name}")
    except ImportError:
        raise ValueError(f"Unsupported client: {client}")

    import platform
    raw_arch = platform.machine().lower()
    arch_amd64 = raw_arch in ['x86_64', 'amd64']

    if hasattr(module, "get_release_info"):
        return module.get_release_info(version_tag, arch_amd64)
    else:
        raise ValueError(f"Client module deploy.{module_name} does not implement get_release_info")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="EthPillar Common CLI Utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download", help="Download a file with progress bar")
    download_parser.add_argument("url", type=str, help="URL to download from")
    download_parser.add_argument("dest", type=str, help="Destination file path")
    download_parser.add_argument("label", type=str, nargs="?", default="file", help="Descriptive label")

    info_parser = subparsers.add_parser("release_info", help="Get client release version and download URLs")
    info_parser.add_argument("client", type=str, help="Client name")
    info_parser.add_argument("version_tag", type=str, nargs="?", default="LATEST", help="Version tag or LATEST")

    args = parser.parse_args()
    if args.command == "download":
        download_file(args.url, args.dest, args.label)
    elif args.command == "release_info":
        try:
            info = get_client_release_info(args.client, args.version_tag)
            print(json.dumps(info))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            exit(1)
