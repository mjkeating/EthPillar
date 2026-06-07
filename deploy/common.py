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
        print(">> Exception in install_system_binary:")
        traceback.print_exc()
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


def get_java_major_version() -> Optional[int]:
    """Return the major version of the ``java`` on PATH, or None if unavailable.

    Handles both the modern version scheme (``"25.0.1"`` -> 25) and the legacy
    one (``"1.8.0_392"`` -> 8).
    """
    import shutil
    if not shutil.which("java"):
        return None
    try:
        res = subprocess.run(["java", "-version"], capture_output=True, text=True, check=False)
        # The version string is printed on stderr by convention.
        out = (res.stderr or "") + (res.stdout or "")
        m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
        if not m:
            return None
        major = int(m.group(1))
        if major == 1 and m.group(2):  # legacy 1.x scheme: 1.8 -> 8
            major = int(m.group(2))
        return major
    except Exception:
        return None


def _install_java(version: int) -> bool:
    """Install the Ubuntu upstream OpenJDK ``version`` package.

    Returns False (with a clear error) on failure, e.g. when the Ubuntu release
    is too old to provide that JDK. We intentionally do NOT fall back to a
    third-party JDK source; the user should upgrade Ubuntu instead.
    """
    pkg = f"openjdk-{version}-jre-headless"
    print(f">> Attempting apt install of {pkg}")
    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False)
    res = subprocess.run(["sudo", "apt-get", "-y", "install", pkg], check=False)
    if res.returncode == 0:
        print(f">> Installed {pkg}")
        return True
    print(f"""
>> ❌ ERROR: could not install '{pkg}' from the Ubuntu repositories.
>>    Besu requires JDK {version}. If the package was not found, your Ubuntu
>>    release is likely too old to provide it; upgrade Ubuntu (e.g. run
>>    'sudo do-release-upgrade'), then re-run the update.
""")
    return False


def _activate_java(version: int) -> None:
    """Make the installed JDK ``version`` the default ``java`` on PATH.

    The distro package registers an update-alternatives entry, but the active
    selection is not always switched to the newly installed JDK, so set it
    explicitly. JVM clients like Besu invoke ``java`` from PATH.
    """
    # Ubuntu's package installs to /usr/lib/jvm/java-<version>-openjdk-<arch>/.
    candidates = glob.glob(f"/usr/lib/jvm/java-{version}-*/bin/java")
    if not candidates:
        return
    java_bin = sorted(candidates)[0]
    subprocess.run(["sudo", "update-alternatives", "--install",
                    "/usr/bin/java", "java", java_bin, "2500"], check=False)
    subprocess.run(["sudo", "update-alternatives", "--set", "java", java_bin], check=False)
    print(f">> Set default java -> {java_bin}")


def ensure_java_available(min_version: int = 21) -> bool:
    """Ensure a Java runtime of at least ``min_version`` is the default on PATH.

    JVM-based clients ship binaries compiled for a specific Java release (e.g.
    Besu 26.6.0 requires JDK 25). It is not enough for *some* java to be
    present: it must be recent enough, otherwise the client fails to start with
    ``UnsupportedClassVersionError``. This helper checks the active java major
    version and installs/activates a newer JDK when needed.

    Returns True if a suitable Java is available (already or after install),
    False otherwise. Best-effort: installing requires sudo privileges.
    """
    current = get_java_major_version()
    if current is not None and current >= min_version:
        print(f">> Java {current} already available on PATH (>= {min_version})")
        return True

    if current is None:
        print(f">> Java not found on PATH; installing OpenJDK {min_version}")
    else:
        print(f">> Java {current} is older than the required {min_version}; upgrading")

    if not _install_java(min_version):
        return False

    # The package normally makes the new JDK the default automatically (apt's
    # update-alternatives "auto" mode picks the newest). Only force the
    # selection if an older java is still the default (e.g. a manual pin),
    # which also avoids a spurious "link group java is broken" warning.
    new = get_java_major_version()
    if new is None or new < min_version:
        _activate_java(min_version)
        new = get_java_major_version()

    if new is not None and new >= min_version:
        print(f">> Java {new} is now active")
        return True
    print(f">> Warning: active Java is {new}, expected >= {min_version}. "
          f"Select it manually with: sudo update-alternatives --config java")
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


def _github_api_headers() -> dict:
    """Headers for GitHub API requests (optional GITHUB_TOKEN raises rate limits)."""
    import os
    headers = {"User-Agent": "ethpillar"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


_NON_LINUX_ASSET_MARKERS = (
    "osx",
    "darwin",
    "win",
    "windows",
    ".exe",
    ".dmg",
    ".msi",
    ".sha256",
    ".sha512",
    "checksum",
    "checksums",
)

_AMD64_ASSET_MARKERS = (
    "x86_64",
    "amd64",
    "linux-x64",
    "linux_x64",
    "linux-amd64",
    "linux_amd64",
)

_ARM64_ASSET_MARKERS = (
    "aarch64",
    "arm64",
    "linux-arm64",
    "linux_arm64",
)


def _asset_name_excluded(name: str) -> bool:
    """Return True when an asset name looks non-installable or non-Linux.

    Filters checksum sidecars, Windows/macOS builds, and similar artifacts that
    appear on GitHub releases but must never be downloaded as client binaries.
    """
    lowered = name.lower()
    return any(marker in lowered for marker in _NON_LINUX_ASSET_MARKERS)


def _asset_matches_arch(name: str, arch_amd64: bool) -> bool:
    """Return True when *name* matches the requested CPU architecture.

    Accepts common upstream naming variants (``amd64``, ``x86_64``, ``linux-x64``,
    ``arm64``, ``aarch64``, etc.) and rejects assets that clearly target the
    opposite architecture.
    """
    lowered = name.lower()
    want = _AMD64_ASSET_MARKERS if arch_amd64 else _ARM64_ASSET_MARKERS
    reject = _ARM64_ASSET_MARKERS if arch_amd64 else _AMD64_ASSET_MARKERS
    if not any(marker in lowered for marker in want):
        return False
    return not any(marker in lowered for marker in reject)


def _asset_is_linux_candidate(name: str) -> bool:
    """Return True when an asset name plausibly refers to a Linux client binary.

    Most clients embed ``linux`` in the filename. Some (e.g. older Grandine builds)
    publish bare architecture suffixes such as ``grandine-2.0.1-amd64`` instead.
    """
    lowered = name.lower()
    if _asset_name_excluded(name):
        return False
    if "linux" in lowered:
        return True
    return any(marker in lowered for marker in _AMD64_ASSET_MARKERS + _ARM64_ASSET_MARKERS)


def _asset_extension_rank(name: str, prefer_extensions: tuple[str, ...]) -> Optional[int]:
    """Return the preference index of the first matching extension, or None.

    An empty string in *prefer_extensions* matches extensionless bare binaries
    (for example ``grandine-2.0.4-linux-x64``). Lower indices are higher priority.
    """
    lowered = name.lower()
    for index, ext in enumerate(prefer_extensions):
        if ext == "":
            if not any(lowered.endswith(skip) for skip in (".tar.gz", ".zip", ".exe", ".sha256", ".sha512")):
                return index
        elif lowered.endswith(ext):
            return index
    return None


def pick_github_release_asset(
    assets: list,
    arch_amd64: Optional[bool],
    *,
    role_contains: str = "",
    name_contains: tuple[str, ...] = (),
    prefer_extensions: tuple[str, ...] = (".tar.gz", ".zip", ""),
    client_label: str = "release",
) -> tuple[str, str]:
    """Select the best Linux release asset from a GitHub ``release[\"assets\"]`` list.

    EthPillar never constructs download URLs from version tags. Callers fetch the
    release JSON (via :func:`get_github_release`) and pass its ``assets`` array here
    so the returned ``browser_download_url`` is always the URL GitHub published.

    Args:
        assets: GitHub API asset dicts, each with ``name`` and
            ``browser_download_url``.
        arch_amd64: ``True`` for amd64/x86_64 hosts, ``False`` for arm64/aarch64,
            or ``None`` for architecture-neutral archives (e.g. Besu tarballs).
        role_contains: When set, the asset name must contain this substring
            (case-insensitive). Used for multi-binary releases such as Prysm
            (``beacon-chain`` vs ``validator``).
        name_contains: When non-empty, every substring must appear in the asset
            name (case-insensitive).
        prefer_extensions: Filename endings to prefer, highest priority first.
            Use ``\"\"`` to allow extensionless bare binaries.
        client_label: Client name included in :class:`ValueError` messages.

    Returns:
        ``(filename, browser_download_url)`` for the highest-priority matching asset.

    Raises:
        ValueError: If no asset matches the requested filters.
    """
    candidates: list[tuple[int, str, str]] = []
    for asset in assets:
        name = asset.get("name", "")
        url = asset.get("browser_download_url", "")
        if not name or not url:
            continue
        if role_contains and role_contains.lower() not in name.lower():
            continue
        if name_contains and not all(part.lower() in name.lower() for part in name_contains):
            continue
        if arch_amd64 is None:
            if _asset_name_excluded(name):
                continue
        elif not _asset_is_linux_candidate(name) or not _asset_matches_arch(name, arch_amd64):
            continue
        ext_rank = _asset_extension_rank(name, prefer_extensions)
        if ext_rank is None:
            continue
        candidates.append((ext_rank, name, url))

    if not candidates:
        if arch_amd64 is None:
            arch_label = "neutral"
        else:
            arch_label = "amd64" if arch_amd64 else "arm64"
        role = f" matching {role_contains!r}" if role_contains else ""
        raise ValueError(f"No Linux {arch_label} {client_label} asset found{role}")

    candidates.sort(key=lambda item: item[0])
    _, name, url = candidates[0]
    return name, url


def _normalize_release_version_key(tag: str) -> str:
    """Normalize a version tag for loose matching (``v1.11.0`` and ``v1.11`` → ``1.11``)."""
    normalized = tag.strip().lstrip("vV")
    if re.fullmatch(r"\d+\.\d+\.0", normalized):
        normalized = normalized[:-2]
    return normalized.lower()


def _github_release_tag_candidates(version_tag: str) -> list[str]:
    """Build ordered tag strings to try when resolving a GitHub release by tag."""
    tag = version_tag.strip()
    candidates = [tag]
    bare = tag.lstrip("vV")
    if bare != tag:
        candidates.append(bare)
    if re.fullmatch(r"v?\d+\.\d+\.0", tag, re.IGNORECASE):
        major_minor = bare.rsplit(".", 1)[0]
        candidates.extend([f"v{major_minor}", major_minor])
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _fetch_github_release_by_tag(repo: str, tag: str) -> Optional[dict]:
    """Return release JSON for *tag*, or ``None`` when no published release exists."""
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    res = requests.get(url, headers=_github_api_headers(), timeout=30)
    if res.status_code == 404:
        return None
    res.raise_for_status()
    return res.json()


def _find_github_release_by_normalized_tag(repo: str, version_tag: str) -> Optional[dict]:
    """Scan published releases for one whose normalized tag matches *version_tag*."""
    target = _normalize_release_version_key(version_tag)
    page = 1
    while page <= 10:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"
        res = requests.get(url, headers=_github_api_headers(), timeout=30)
        res.raise_for_status()
        releases = res.json()
        if not releases:
            break
        for release in releases:
            if release.get("draft"):
                continue
            if _normalize_release_version_key(release.get("tag_name", "")) == target:
                return release
        page += 1
    return None


def get_github_release(repo: str, version_tag: str) -> dict:
    """Fetch release info from the GitHub API.

    Some repos publish releases under shortened tags (for example ``v1.11``) while
    git also has patch tags such as ``v1.11.0`` with no release assets. When the
    exact tag is missing, this tries common aliases and scans published releases.
    """
    if version_tag.upper() == "LATEST":
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        res = requests.get(url, headers=_github_api_headers(), timeout=30)
        res.raise_for_status()
        return res.json()

    for candidate in _github_release_tag_candidates(version_tag):
        release = _fetch_github_release_by_tag(repo, candidate)
        if release is not None:
            return release

    release = _find_github_release_by_normalized_tag(repo, version_tag)
    if release is not None:
        return release

    url = f"https://api.github.com/repos/{repo}/releases/tags/{version_tag}"
    res = requests.get(url, headers=_github_api_headers(), timeout=30)
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
