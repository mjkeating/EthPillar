import os
import sys
import re
import hashlib
import platform
import shutil
import subprocess
import requests
import json
import tarfile
import tempfile
from consolemenu import PromptUtils, Screen
from typing import Callable, Optional, List

from deploy import http_retry
import glob
INSTALL_DIR = "/usr/local/bin"
DOWNLOAD_DIR = "/tmp"
BASE_DATA_DIR = "/var/lib"


def _load_runtime_packages() -> tuple[str, ...]:
    packages_path = os.path.join(os.path.dirname(__file__), 'runtime_packages.txt')
    with open(packages_path, encoding='utf-8') as handle:
        return tuple(
            line.strip()
            for line in handle
            if line.strip() and not line.startswith('#')
        )


# Apt packages installed during node setup (deploy + update scripts + health checks).
NODE_RUNTIME_PACKAGES = _load_runtime_packages()


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


def _remove_file(path: str) -> None:
    """Remove ``path`` if it exists. Ignore races and permission errors."""
    try:
        os.remove(path)
    except OSError:
        pass


def _reject_download(dest_path: str, message: str) -> None:
    """Delete a file that must not be installed, then stop the install."""
    _remove_file(dest_path)
    print(message)
    exit(1)


def _fail_download(message: str) -> None:
    """Stop the install. Does not delete a destination that was never replaced."""
    print(message)
    exit(1)


class ChecksumMismatch(Exception):
    """Downloaded bytes do not match the published checksum. Not retried."""


def github_get(url: str, **kwargs):
    """GET a GitHub URL with :func:`deploy.http_retry.get_with_retry`.

    ``requests.get`` is resolved in this module so tests can patch it.
    Release-asset callers must not put a GitHub API token in ``headers``.
    """
    return http_retry.get_with_retry(requests.get, url, **kwargs)


# Pinned OpenPGP keys for detached signatures. Fingerprints are compared after
# import so a substituted key file cannot satisfy verification.
_SIGNATURE_KEYS = {
    "geth": (
        os.path.join(os.path.dirname(__file__), "keys", "geth-linux-builder.asc"),
        ("FDE5A1A044FA13D2F7ADA019A61A13569BA28146",),
    ),
}


def _ensure_gpg() -> str:
    """Return a ``gpg`` binary, installing Debian/Ubuntu ``gnupg`` when missing."""
    import shutil
    found = shutil.which("gpg")
    if found:
        return found
    subprocess.run(
        ["sudo", "apt-get", "-y", "-qq", "install", "--no-install-recommends", "gnupg"],
        check=False,
    )
    found = shutil.which("gpg")
    if not found:
        print("Error: gpg is required to verify release signatures.")
        exit(1)
    return found


def _gpg_fingerprints(colon_output: str) -> set[str]:
    """Collect fingerprints from ``gpg --with-colons --fingerprint`` output."""
    fingerprints = set()
    for line in colon_output.splitlines():
        if not line.startswith("fpr:"):
            continue
        parts = line.split(":")
        if len(parts) > 9 and parts[9]:
            fingerprints.add(parts[9].upper())
    return fingerprints


def _validsig_fingerprints(status_output: str) -> list[str]:
    """Collect fingerprints from GnuPG ``VALIDSIG`` status lines."""
    fingerprints = []
    for line in status_output.splitlines():
        if not line.startswith("[GNUPG:] VALIDSIG "):
            continue
        parts = line.split()
        if len(parts) >= 3:
            fingerprints.append(parts[2].upper())
    return fingerprints


def verify_detached_signature(payload_path: str, signature_path: str, key_name: str) -> None:
    """Verify a detached signature with a pinned public key.

    Uses a temporary ``GNUPGHOME`` so keys already on the machine are ignored.
    The imported key's fingerprint and the ``VALIDSIG`` fingerprint must both
    be in the allowlist for ``key_name``.

    Raises:
        ValueError: The key name is unknown, the pinned key does not match its
            fingerprint, or the signature is not valid.
    """
    spec = _SIGNATURE_KEYS.get(key_name)
    if spec is None:
        raise ValueError(f"No pinned signature key named {key_name!r}")
    key_path, allowed_fingerprints = spec
    allowed = {fingerprint.upper() for fingerprint in allowed_fingerprints}
    with open(key_path, encoding="utf-8") as handle:
        armored = handle.read()
    gpg = _ensure_gpg()
    with tempfile.TemporaryDirectory(prefix="ep-gpg-") as home:
        try:
            os.chmod(home, 0o700)
        except OSError:
            pass
        env = os.environ.copy()
        env["GNUPGHOME"] = home
        imported = subprocess.run(
            [gpg, "--batch", "--import"],
            input=armored,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if imported.returncode != 0:
            raise ValueError(f"Could not import the pinned {key_name} key: {imported.stderr.strip()}")
        listed = subprocess.run(
            [gpg, "--batch", "--with-colons", "--fingerprint"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        fingerprints = _gpg_fingerprints(listed.stdout)
        if not fingerprints or not fingerprints <= allowed:
            raise ValueError(
                f"Pinned {key_name} key fingerprint {sorted(fingerprints) or 'missing'} "
                "does not match the allowlist"
            )
        verified = subprocess.run(
            [gpg, "--batch", "--status-fd", "1", "--verify", signature_path, payload_path],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        validsig = _validsig_fingerprints(verified.stdout)
        if verified.returncode != 0 or not validsig or any(item not in allowed for item in validsig):
            detail = (verified.stderr or verified.stdout).strip()
            raise ValueError(f"OpenPGP signature rejected. {detail}")


def stream_download(
    url: str,
    dest_path: str,
    *,
    label: str = "file",
    show_progress: bool = True,
    on_attempt: Optional[Callable[[], None]] = None,
    on_chunk: Optional[Callable[[bytes], None]] = None,
    before_commit: Optional[Callable[[], None]] = None,
) -> None:
    """Download ``url`` to ``dest_path`` with the shared retry policy.

    Delegates to :func:`deploy.http_retry.download_to_path`. ``requests.get`` is
    passed from this module so tests can patch it. ``before_commit`` may raise
    :class:`ChecksumMismatch`; that is not retried.

    Args:
        url: URL to download.
        dest_path: Final file path.
        label: Text included in errors and the progress display.
        show_progress: Draw a byte progress bar when tqdm is used.
        on_attempt: Called at the start of every attempt (reset checksum state).
        on_chunk: Called with each non-empty body chunk of the current attempt.
        before_commit: Called after a complete body is on disk, before rename.

    Raises:
        http_retry.AttemptsExhausted: Every attempt failed with a retryable error.
        requests.RequestException: A non-retryable HTTP or network error.
        ChecksumMismatch: ``before_commit`` rejected the body.
    """
    http_retry.download_to_path(
        url,
        dest_path,
        label=label,
        show_progress=show_progress,
        on_attempt=on_attempt,
        on_chunk=on_chunk,
        before_commit=before_commit,
        get=requests.get,
    )


def download_file(
    url: str,
    dest_path: str,
    label: str = "file",
    expected_sha256: Optional[str] = None,
    expected_md5: Optional[str] = None,
    signature_url: Optional[str] = None,
    signature_key: Optional[str] = None,
) -> None:
    """Download a file with a progress bar and verify a published checksum.

    Transient failures (timeouts, connection errors, HTTP 429, and HTTP 5xx)
    retry inside :func:`stream_download`. A checksum or signature mismatch is
    never retried and never ignored: the partial download is removed and the
    install stops. HTTP 404 and other 4xx also stop on the first attempt.

    Args:
        url: The URL to download from.
        dest_path: Absolute destination file path.
        label: Descriptive label for the download.
        expected_sha256: Lower- or mixed-case hex SHA-256 of the file.
        expected_md5: Lower- or mixed-case hex MD5 of the file.
        signature_url: Detached signature to check after the checksum matches.
        signature_key: Name of a pinned public key in :data:`_SIGNATURE_KEYS`.
    """
    print(f">> Downloading {label} > URL: {url}")
    filename = os.path.basename(dest_path)
    sha256 = hashlib.sha256() if expected_sha256 else None
    md5 = hashlib.md5() if expected_md5 else None

    def _reset_digests() -> None:
        nonlocal sha256, md5
        sha256 = hashlib.sha256() if expected_sha256 else None
        md5 = hashlib.md5() if expected_md5 else None

    def _update_digests(chunk: bytes) -> None:
        if sha256 is not None:
            sha256.update(chunk)
        if md5 is not None:
            md5.update(chunk)

    def _verify_digests() -> None:
        if expected_sha256:
            actual = sha256.hexdigest() if sha256 is not None else ""
            if actual != expected_sha256.lower():
                raise ChecksumMismatch(
                    f"Error: SHA-256 mismatch for {filename}.\n"
                    f"  expected {expected_sha256.lower()}\n"
                    f"  actual   {actual}"
                )
        if expected_md5:
            actual = md5.hexdigest() if md5 is not None else ""
            if actual != expected_md5.lower():
                raise ChecksumMismatch(
                    f"Error: MD5 mismatch for {filename}.\n"
                    f"  expected {expected_md5.lower()}\n"
                    f"  actual   {actual}"
                )

    try:
        stream_download(
            url,
            dest_path,
            label=label,
            on_attempt=_reset_digests,
            on_chunk=_update_digests,
            before_commit=_verify_digests if (expected_sha256 or expected_md5) else None,
        )
    except ChecksumMismatch as exc:
        _fail_download(str(exc))
    except http_retry.AttemptsExhausted as exc:
        _fail_download(
            f"Error: Unable to download {label} after {exc.attempts} attempts. {exc.last}"
        )
    except requests.RequestException as exc:
        _fail_download(f"Error: Unable to download {label}. {exc}")
    except OSError as exc:
        _fail_download(f"Error: Unable to download {label}. {exc}")

    if expected_sha256 or expected_md5:
        algorithms = []
        if expected_sha256:
            algorithms.append("sha256")
        if expected_md5:
            algorithms.append("md5")
        print(f">> Checksum verified ({', '.join(algorithms)}): {filename}")
    if signature_url:
        if not signature_key:
            _reject_download(dest_path, f"Error: no pinned public key for the {filename} signature.")
        sig_path = dest_path + ".asc"
        try:
            sig_response = github_get(signature_url, headers={"User-Agent": http_retry.USER_AGENT})
            sig_response.raise_for_status()
            with open(sig_path, "wb") as handle:
                handle.write(sig_response.content)
            verify_detached_signature(dest_path, sig_path, signature_key)
        except http_retry.AttemptsExhausted as exc:
            _reject_download(
                dest_path,
                f"Error: signature verification failed for {filename} after {exc.attempts} attempts. {exc.last}",
            )
        except (requests.RequestException, ValueError, OSError) as exc:
            _reject_download(dest_path, f"Error: signature verification failed for {filename}. {exc}")
        finally:
            _remove_file(sig_path)
        print(f">> Signature verified: {filename}")
    print(f">> Successfully downloaded: {filename}")


def download_release_file(
    info: dict,
    dest_dir: str,
    label: str,
    index: int = 0,
    dest_name: Optional[str] = None,
) -> str:
    """Download one release asset and verify its published checksum.

    GitHub-hosted clients supply ``info["sha256"]``. Geth supplies ``info["md5"]``
    from the downloads page. Install stops when neither checksum is present.

    Args:
        info: Release-info dict from :func:`get_client_release_info`.
        dest_dir: Directory to write the file into.
        label: Descriptive label for the download.
        index: Which parallel ``download_urls`` / checksum entry to use.
        dest_name: Override the on-disk filename. Checksums cover file bytes,
            so a rename does not change verification.

    Returns:
        Absolute path of the verified download.
    """
    filename = dest_name or info["filenames"][index]
    url = info["download_urls"][index]
    sha_list = info.get("sha256") or []
    md5_list = info.get("md5") or []
    sha256 = sha_list[index] if index < len(sha_list) and sha_list[index] else None
    md5 = md5_list[index] if index < len(md5_list) and md5_list[index] else None
    sig_list = info.get("signature_urls") or []
    signature_url = sig_list[index] if index < len(sig_list) and sig_list[index] else None
    signature_key = info.get("signature_key") or None
    if not sha256 and not md5 and not signature_url:
        print(f"Error: no published checksum for {label} ({filename}). Refusing to install.")
        exit(1)
    dest_path = os.path.join(dest_dir, filename)
    download_file(
        url,
        dest_path,
        label,
        expected_sha256=sha256,
        expected_md5=md5,
        signature_url=signature_url,
        signature_key=signature_key,
    )
    return dest_path


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
>>    This client requires JDK {version}. If the package was not found, your
>>    Ubuntu release is likely too old to provide it; upgrade Ubuntu (e.g. run
>>    'sudo do-release-upgrade'), then re-run the install/update.
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


def ensure_java_available(min_version: int = 25) -> bool:
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


def ensure_jemalloc() -> bool:
    """Install libjemalloc-dev so JVM clients (Besu, Teku) can preload it.

    Besu and Teku start scripts look for the unversioned ``libjemalloc.so``
    name and ``LD_PRELOAD`` it when found. On Debian/Ubuntu that symlink ships
    in the ``-dev`` package (not a newer/unstable jemalloc — it also pulls in
    the runtime ``libjemalloc2``). glibc malloc fragments badly for long-running
    JVM native allocations (RocksDB, Netty); jemalloc keeps RSS much flatter.

    Other EthPillar clients do not auto-load a system jemalloc: Lighthouse,
    Reth, and Grandine compile jemalloc into official Linux binaries; Ethrex
    uses mimalloc; Go/.NET/Node/Nim clients use their own allocators.

    Returns True if apt succeeded, False otherwise. Best-effort: installing
    requires sudo privileges.
    """
    print(">> Installing libjemalloc-dev (jemalloc for JVM memory management)")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    res = subprocess.run(
        ["sudo", "apt-get", "-y", "-qq", "install", "libjemalloc-dev"],
        check=False,
        env=env,
    )
    if res.returncode == 0:
        print(">> libjemalloc-dev installed")
        return True
    print(">> ❌ ERROR: could not install 'libjemalloc-dev' from apt.")
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
        try:
            response = github_get(url, headers=_github_api_headers())
        except (http_retry.AttemptsExhausted, requests.RequestException) as exc:
            print(f"Failed to retrieve genesis release for {repo}: {exc}")
            return None
        if response.status_code == 200:
            data = json.loads(response.text)
            return data.get('tag_name')
        print(f"Failed to retrieve genesis release for {repo}: HTTP {response.status_code}")
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
        temp_dir = tempfile.mkdtemp()
        archive = os.path.join(temp_dir, "testnet-all.tar.gz")
        try:
            stream_download(url, archive, label=f"{genesis_release} genesis files")
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(temp_dir)
            os.remove(archive)
            subprocess.run(['sudo', 'sh', '-c', f'mv {temp_dir}/* {testnet_dir}'], check=True)
            print(f">> Successfully downloaded {genesis_release} genesis files")
        except (http_retry.AttemptsExhausted, requests.RequestException, OSError, tarfile.TarError, EOFError) as exc:
            print(f"Failed to download genesis release: {exc}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    genesis_release = get_github_release(genesis_repository)
    if genesis_release:
        download_genesis_release(genesis_release)
    else:
        print(f"Failed to retrieve genesis release for {genesis_repository}")


EPHEMERY_TESTNET_DIR = "/opt/ethpillar/testnet"
EPHEMERY_CONFIG_YAML = f"{EPHEMERY_TESTNET_DIR}/config.yaml"
EPHEMERY_GENESIS_SSZ = f"{EPHEMERY_TESTNET_DIR}/genesis.ssz"
EPHEMERY_GENESIS_JSON = f"{EPHEMERY_TESTNET_DIR}/genesis.json"
# Nimbus --network value (file). Shared with resync_consensus.sh EPHEMERY_NETWORK_PATH.
EPHEMERY_NIMBUS_NETWORK = EPHEMERY_CONFIG_YAML


def ephemery_chain_id() -> Optional[str]:
    """Read Ephemery chain id from genesis.json or config.yaml when present."""
    if os.path.isfile(EPHEMERY_GENESIS_JSON):
        try:
            with open(EPHEMERY_GENESIS_JSON, encoding="utf-8") as handle:
                data = json.load(handle)
            chain_id = (data.get("config") or {}).get("chainId")
            if chain_id is not None:
                return str(chain_id)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    if os.path.isfile(EPHEMERY_CONFIG_YAML):
        try:
            with open(EPHEMERY_CONFIG_YAML, encoding="utf-8") as handle:
                text = handle.read()
            match = re.search(r"DEPOSIT_CHAIN_ID:\s*(\d+)", text)
            if match:
                return match.group(1)
        except OSError:
            pass
    return None


def ephemery_bootnodes() -> str:
    """Return comma-separated ENRs from the downloaded Ephemery testnet dir."""
    for name in ("bootstrap_nodes.txt", "boot_enr.txt", "bootnodes.txt"):
        path = os.path.join(EPHEMERY_TESTNET_DIR, name)
        if not os.path.isfile(path):
            continue
        nodes: List[str] = []
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        nodes.append(line)
        except OSError:
            continue
        if nodes:
            return ",".join(nodes)
    return ""


def ephemery_network_override(client: str, role: str = "bn") -> Optional[str]:
    """Per-client flags that replace ``--network=ephemery`` (or equivalent).

    Matches the shapes already exercised in ``tests/test_service_generators.py``.
    Bootnode flags are included only when the genesis tarball provided ENRs.

    Args:
        client: Client name (e.g. ``Lodestar``, ``Geth``).
        role: ``bn``, ``vc``, or ``el``.

    Returns:
        Override string, or None when *client* is empty.
    """
    if not client:
        return None
    key = client.split()[0].lower()
    boot = ephemery_bootnodes()
    role = (role or "bn").lower()

    if key == "nimbus":
        return f"--network={EPHEMERY_NIMBUS_NETWORK}"
    if key == "lighthouse":
        flags = f"--testnet-dir={EPHEMERY_TESTNET_DIR}"
        if boot and role != "vc":
            flags = f"{flags} --boot-nodes={boot}"
        return flags
    if key == "lodestar":
        flags = (
            f"--paramsFile={EPHEMERY_CONFIG_YAML} "
            f"--genesisStateFile={EPHEMERY_GENESIS_SSZ} "
            "--network.connectToDiscv5Bootnodes --ignoreWeakSubjectivityCheck"
        )
        if boot:
            flags = f"{flags} --bootnodes={boot}"
        return flags
    if key == "teku":
        flags = f"--network={EPHEMERY_TESTNET_DIR}"
        if boot:
            flags = f"{flags} --p2p-discovery-bootnodes={boot}"
        return flags
    if key == "grandine":
        flags = f"--network-dir={EPHEMERY_TESTNET_DIR}"
        if boot:
            flags = f"{flags} --boot-nodes={boot}"
        return flags
    if key == "prysm":
        return (
            f"--chain-config-file={EPHEMERY_CONFIG_YAML} "
            f"--genesis-state={EPHEMERY_GENESIS_SSZ}"
        )
    if key == "besu":
        genesis = (
            f"{EPHEMERY_TESTNET_DIR}/besu.json"
            if os.path.isfile(f"{EPHEMERY_TESTNET_DIR}/besu.json")
            else EPHEMERY_GENESIS_JSON
        )
        flags = f"--genesis-file={genesis}"
        if boot:
            flags = f"{flags} --bootnodes={boot}"
        return flags
    if key == "geth":
        # Avoid the invalid ``--ephemery`` fallback; chain id lives in genesis.
        chain_id = ephemery_chain_id() or "39438135"
        flags = f"--networkid {chain_id}"
        if boot:
            flags = f"{flags} --bootnodes {boot}"
        return flags
    if key == "ethrex":
        return f"--network {EPHEMERY_GENESIS_JSON}"
    if key == "nethermind":
        chainspec = f"{EPHEMERY_TESTNET_DIR}/chainspec.json"
        flags = f"--config none --Init.ChainSpecPath={chainspec}"
        if boot:
            flags = f"{flags} --Discovery.Bootnodes={boot}"
        return flags
    if key == "reth":
        flags = f"--chain {EPHEMERY_GENESIS_JSON}"
        if boot:
            flags = f"{flags} --bootnodes {boot}"
        return flags
    if key == "erigon":
        flags = f"--chain {EPHEMERY_GENESIS_JSON}"
        if boot:
            flags = f"{flags} --bootnodes {boot}"
        return flags
    return None


def setup_node(jwt_secret_path: str, validator_only: bool = False) -> None:
    """Setup node dependencies and JWT secret.

    Args:
        jwt_secret_path: Path to save the JWT secret.
        validator_only: If True, skip JWT secret creation (packages are still installed).
    """
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'update'], check=True)
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'upgrade'], check=True)
    subprocess.run(['sudo', 'apt', '-y', '-qq', 'autoremove'], check=True)
    subprocess.run(
        ['sudo', 'apt', '-y', '-qq', 'install', '--no-install-recommends', *NODE_RUNTIME_PACKAGES],
        check=True,
    )

    if not validator_only:
        # Generate JWT secret only if it doesn't exist (don't regenerate on client switch)
        if not os.path.exists(jwt_secret_path):
            # Create JWT directory
            jwt_dir = os.path.dirname(jwt_secret_path)
            subprocess.run(['sudo', 'mkdir', '-p', jwt_dir], check=True)

            # Generate random hex string and save to file
            rand_hex = subprocess.run(['openssl', 'rand', '-hex', '32'], stdout=subprocess.PIPE, check=True)
            subprocess.run(['sudo', 'tee', jwt_secret_path], input=rand_hex.stdout, stdout=subprocess.DEVNULL, check=True)

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


def start_units_no_block(units: List[str]) -> None:
    """Queue ``systemctl start`` without waiting for ExecStartPre or active.

    Interactive install must not block on Nimbus ``trustedNodeSync``
    (ExecStartPre can take minutes). Integration health checks still use a
    blocking start with a longer timeout; do not call this from that path.
    """
    if not units:
        return
    subprocess.run(
        ["sudo", "systemctl", "start", "--no-block", *units],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def finish_install(install_config: str, eth_network: str, sync_url: str,
                   execution_client: Optional[str], execution_version: Optional[str], execution_service_path: Optional[str],
                   consensus_client: Optional[str], consensus_version: Optional[str], consensus_service_path: Optional[str],
                   mevboost_enabled: bool, mevboost_version: Optional[str], mevboost_service_path: Optional[str],
                   validator_enabled: bool, validator_service_path: Optional[str],
                   validator_only: bool, bn_address: Optional[str], node_only: bool, fee_recipient_address: Optional[str],
                   skip_prompts: bool, cl_rest_port: str,
                   vc_name: str = '', vc_ver: str = '',
                   charon_enabled: bool = False, charon_version: Optional[str] = None,
                   charon_service_path: Optional[str] = None,
                   web3signer_enabled: bool = False, web3signer_version: Optional[str] = None,
                   web3signer_service_path: Optional[str] = None,
                   builder_mode: str = '',
                   relays: Optional[list] = None) -> None:
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
        charon_enabled: Whether Obol Charon middleware was installed.
        charon_version: Charon version string.
        charon_service_path: Path to charon.service.
        web3signer_enabled: Whether Web3Signer was installed.
        web3signer_version: Web3Signer version string.
        web3signer_service_path: Path to web3signer.service.
        builder_mode: ``mevboost``, ``epbs``, or ``none``. Empty omits the line.
        relays: Selected relay dicts (``name`` and ``url``) for the summary.
    """
    
    # Reload the systemd daemon
    subprocess.run(['sudo', 'systemctl', 'daemon-reload'], capture_output=True, check=True)

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

    _builder_labels = {
        'epbs': 'Native ePBS (relays configured on the validator client)',
        'mevboost': 'MEV-Boost',
        'none': 'None (local blocks)',
    }
    if builder_mode in _builder_labels:
        print(f'Builder mode: {_builder_labels[builder_mode]}\n')
    if relays:
        print('Relays:')
        for relay in relays:
            if isinstance(relay, dict):
                name = relay.get('name') or 'relay'
                url = relay.get('url') or ''
                print(f'  {name}: {url}')
            else:
                print(f'  {relay}')
        print()

    if charon_enabled and charon_version:
        print(f'Charon Version: \n{charon_version}\n')

    if web3signer_enabled and web3signer_version:
        print(f'Web3Signer Version: \n{web3signer_version}\n')

    print(f'Network: {eth_network.upper()}\n')

    if not validator_only:
        print(f'CheckPointSyncURL: {sync_url}\n')

    if validator_only and bn_address:
        print(f'Beacon Node Address: {bn_address}\n')
        target_dir = os.environ.get('BASE_DIR') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    if charon_enabled and charon_service_path:
        print(f'{charon_service_path}')
    if web3signer_enabled and web3signer_service_path:
        print(f'{web3signer_service_path}')

    if charon_enabled:
        from deploy.charon import CHARON_CLUSTER_DIR
        from deploy.orchestrator import obol_mark
        print(f'\n{obol_mark()} Obol Charon next steps:')
        print(f'  1. Copy your existing .charon folder to {CHARON_CLUSTER_DIR}')
        print(f'     EthPillar → {obol_mark()} Obol Charon DV → Import .charon cluster folder')
        print('     (prompts to import key shares into the signer VC when present)')
        print(f'     (or: sudo cp -a /path/to/.charon/. {CHARON_CLUSTER_DIR}/ && sudo chown -R charon:charon {CHARON_CLUSTER_DIR})')
        print('  2. sudo systemctl start charon')
        print(f'  3. If key shares were not imported in step 1: Validator → Import {obol_mark()} Obol Charon key shares')
        print('  4. sudo systemctl start validator')
        try:
            from deploy.charon import parse_p2p_tcp_port

            _charon_p2p = parse_p2p_tcp_port()
        except Exception:
            _charon_p2p = 3610
        print(
            f'  5. Open TCP {_charon_p2p} on your firewall; '
            'set CHARON_P2P_EXTERNAL_IP if peers cannot use relays'
        )
        if validator_only:
            print('  6. Remote beacon node requirements (configure on the BN host):')
            print('     • Nimbus BN: Charon needs --feature-set-enable=json_requests')
            print('       (add to charon.service on this machine if upstream BN is Nimbus)')
            print('     • Teku BN: --validators-graffiti-client-append-format=DISABLED')
        print('     See docs/charon.md for CDVN migration and BN notes.\n')
        # Wire Charon into existing Grafana/Prometheus when monitoring is already installed
        try:
            from manage.charon_monitoring import provision_charon_monitoring
            mon = provision_charon_monitoring(restart=True)
            if mon.get('scrape') or mon.get('dashboard') or mon.get('datasource'):
                print('Charon monitoring: Prometheus scrape and/or Charon Overview dashboard updated.')
                print('  Grafana: http://127.0.0.1:3000/d/charon_overview/')
            elif not os.path.isfile('/etc/prometheus/prometheus.yml'):
                print('Tip: Install Monitoring (Logging & Monitoring) to auto-provision Charon Overview.')
        except PermissionError as exc:
            # Grafana present but /etc/grafana not writable — must not look like a soft skip.
            print(f'ERROR: Charon Grafana/Prometheus provisioning failed (permission denied): {exc}')
            print('  Retry as a user with sudo, or fix ownership under /etc/grafana/provisioning.')
        except Exception as exc:  # noqa: BLE001 — other optional-monitoring failures
            print(f'Note: Charon Grafana/Prometheus provisioning skipped ({exc}).')

    try:
        from manage.client_monitoring import provision_client_monitoring
        mon_clients = provision_client_monitoring(restart=True)
        if mon_clients.get('scrape') or mon_clients.get('dashboards'):
            names = ', '.join(mon_clients.get('clients') or []) or 'installed clients'
            print(f'Client monitoring: Prometheus scrape jobs and Grafana dashboards updated ({names}).')
    except PermissionError as exc:
        print(f'ERROR: Client Grafana/Prometheus provisioning failed (permission denied): {exc}')
        print('  Retry as a user with sudo, or fix ownership under /etc/grafana/provisioning.')
    except Exception as exc:  # noqa: BLE001 — optional monitoring when the stack is absent
        print(f'Note: Client Grafana/Prometheus provisioning skipped ({exc}).')

    if web3signer_enabled:
        from deploy.web3signer import DEFAULT_SIGNER_URL, WEB3SIGNER_KEYSTORE_DIR
        print('\nWeb3Signer next steps:')
        print(f'  Signer URL: {DEFAULT_SIGNER_URL} (localhost only)')
        print(f'  Import new validator keystores into {WEB3SIGNER_KEYSTORE_DIR}')
        print('  EthPillar → Web3Signer → Generate / Import Validator Keys')
        print('  Keys stay in Web3Signer. The validator client does not load them.')
        print('  See docs/web3signer.md for a Hoodi check with Teku and Lighthouse.\n')

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
            if mevboost_enabled:
                services.append('mevboost')
            if charon_enabled:
                from deploy.charon import CHARON_LOCK_FILE
                if os.path.isfile(CHARON_LOCK_FILE):
                    services.append('charon')
            if web3signer_enabled:
                services.append('web3signer')
            # --no-block: Nimbus trustedNodeSync ExecStartPre can block for minutes.
            start_units_no_block(services)
            if services:
                units_flag = " ".join(f"-u {name}" for name in services)
                print("Sync is starting in the background.")
                print(f"  Follow progress: journalctl -f {units_flag}")

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
            if charon_enabled:
                subprocess.run(['sudo', 'systemctl', 'enable', 'charon'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if web3signer_enabled:
                subprocess.run(['sudo', 'systemctl', 'enable', 'web3signer'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

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
    name_excludes: tuple[str, ...] = (),
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
        name_excludes: When non-empty, reject assets whose name contains any of
            these substrings (case-insensitive). Used to skip related binaries
            such as ``op-reth`` when selecting ``reth``.
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
        if name_excludes and any(part.lower() in name.lower() for part in name_excludes):
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
    res = github_get(url, headers=_github_api_headers())
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
        res = github_get(url, headers=_github_api_headers())
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

    When the release tag can be peeled to a commit, the returned dict includes an
    extra ``commit`` key (not part of the raw GitHub payload).
    """
    if version_tag.upper() == "LATEST":
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        res = github_get(url, headers=_github_api_headers())
        res.raise_for_status()
        release = res.json()
    else:
        release = None
        for candidate in _github_release_tag_candidates(version_tag):
            release = _fetch_github_release_by_tag(repo, candidate)
            if release is not None:
                break

        if release is None:
            release = _find_github_release_by_normalized_tag(repo, version_tag)

        if release is None:
            url = f"https://api.github.com/repos/{repo}/releases/tags/{version_tag}"
            res = github_get(url, headers=_github_api_headers())
            res.raise_for_status()
            release = res.json()

    tag = release.get("tag_name")
    if tag:
        commit = get_github_tag_commit(repo, str(tag))
        if commit:
            release["commit"] = commit
    return release


def get_github_tag_commit(repo: str, tag_name: str) -> Optional[str]:
    """Resolve a git tag to its peeled commit SHA via the GitHub API.

    Annotated tags are peeled to the underlying commit. Lightweight tags that
    already point at a commit return that SHA. Returns ``None`` on any failure
    so callers can fall back to semver-only comparison.
    """
    tag = (tag_name or "").strip()
    if not tag:
        return None
    try:
        ref_url = f"https://api.github.com/repos/{repo}/git/refs/tags/{tag}"
        res = github_get(ref_url, headers=_github_api_headers())
        if res.status_code == 404:
            return None
        res.raise_for_status()
        ref = res.json()
        # GitHub may return a list when the path is a prefix of multiple refs.
        if isinstance(ref, list):
            exact = f"refs/tags/{tag}"
            ref = next((item for item in ref if item.get("ref") == exact), None)
            if ref is None:
                return None
        obj = ref.get("object") or {}
        obj_type = obj.get("type")
        obj_sha = obj.get("sha")
        if not obj_sha:
            return None
        if obj_type == "commit":
            return obj_sha
        if obj_type == "tag":
            tag_url = obj.get("url") or f"https://api.github.com/repos/{repo}/git/tags/{obj_sha}"
            tag_res = github_get(tag_url, headers=_github_api_headers())
            tag_res.raise_for_status()
            peeled = (tag_res.json().get("object") or {}).get("sha")
            return peeled or None
        return None
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        print(f"Warning: could not resolve commit for {repo}@{tag}: {exc}", file=sys.stderr)
        return None


def _sha256_from_digest_field(digest: object) -> Optional[str]:
    """Return the hex SHA-256 from a GitHub asset ``digest`` value, if present."""
    text = str(digest or "").strip()
    if not text.lower().startswith("sha256:"):
        return None
    hex_digest = text.split(":", 1)[1].strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", hex_digest):
        return hex_digest
    return None


def _parse_sha256_checksum_text(text: str, filename: str) -> str:
    """Parse a ``.sha256`` sidecar or checksum listing for ``filename``.

    Accepts a bare hex digest or ``sha256sum`` lines (``<digest>  <name>``).
    """
    wanted = filename.lower()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            continue
        if len(parts) == 1 or os.path.basename(parts[-1]).lower() == wanted:
            return parts[0].lower()
    raise ValueError(f"No sha256 line for {filename} in published checksum file")


def _sha256_from_sidecar(url: str, filename: str) -> str:
    """Download a checksum sidecar and return the SHA-256 it publishes.

    Timeouts, connection errors, HTTP 429, and HTTP 5xx are retried. A missing
    sidecar (HTTP 404) or any other 4xx fails immediately. The parsed digest is
    still required; a failed download is never treated as "no checksum".
    """
    try:
        # Release asset URLs reject a GitHub API token (401). User-Agent only.
        response = github_get(url, headers={"User-Agent": "ethpillar"})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"Could not download checksum sidecar for {filename}: {exc}") from exc
    return _parse_sha256_checksum_text(response.text, filename)


def resolve_asset_sha256(github_release: dict, download_url: str, filename: str) -> str:
    """Resolve the SHA-256 EthPillar must match before installing ``filename``.

    Uses the ``digest`` GitHub records on the release asset. When the project
    also publishes ``<filename>.sha256``, that sidecar must contain the same
    digest. When GitHub has no digest (older releases), the sidecar is the
    checksum.
    """
    assets = github_release.get("assets") or []
    match = next(
        (
            asset for asset in assets
            if asset.get("browser_download_url") == download_url or asset.get("name") == filename
        ),
        None,
    )
    if match is None:
        # Some release notes point at an artifact host (Teku on artifacts.consensys.net)
        # and publish the checksum as a sibling ``<file>.sha256``.
        return _sha256_from_sidecar(f"{download_url}.sha256", filename)
    published = _sha256_from_digest_field(match.get("digest"))
    sidecar_name = f"{filename}.sha256"
    sidecar = next((asset for asset in assets if asset.get("name") == sidecar_name), None)
    sidecar_url = (sidecar or {}).get("browser_download_url")
    if published and sidecar_url:
        sidecar_hash = _sha256_from_sidecar(sidecar_url, filename)
        if sidecar_hash != published:
            raise ValueError(
                f"SHA-256 mismatch for {filename}: GitHub asset digest {published} "
                f"does not match {sidecar_name} ({sidecar_hash})"
            )
        return published
    if published:
        return published
    if sidecar_url:
        return _sha256_from_sidecar(sidecar_url, filename)
    raise ValueError(
        f"No sha256 digest published for {filename}. Refusing to install an unverified release asset."
    )


def release_info_from_github(
    github_release: dict,
    download_urls: list[str],
    filenames: list[str],
) -> dict:
    """Build EthPillar release-info from a :func:`get_github_release` payload.

    The returned ``sha256`` list is parallel to ``download_urls``. Each entry is
    the GitHub asset digest, or the project's ``.sha256`` sidecar when the
    digest field is absent.
    """
    if len(download_urls) != len(filenames):
        raise ValueError("download_urls and filenames must be the same length")
    info = {
        "version": github_release["tag_name"],
        "download_urls": download_urls,
        "filenames": filenames,
        "sha256": [
            resolve_asset_sha256(github_release, url, name)
            for url, name in zip(download_urls, filenames)
        ],
    }
    if github_release.get("commit"):
        info["commit"] = github_release["commit"]
    return info


def _detect_runtime_network() -> str:
    """Best-effort network slug for LATEST remapping (env or installed units).

    Order: ``NETWORK`` / ``ETH_NETWORK`` env, then ``Description=`` / ``--network=``
    on consensus then validator systemd units.
    """
    for key in ("NETWORK", "ETH_NETWORK"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw.lower()

    for unit_name in ("consensus", "validator"):
        path = f"/etc/systemd/system/{unit_name}.service"
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("Description="):
                # e.g. "Lighthouse Consensus Client service for SEPOLIA"
                match = re.search(
                    r"\b(mainnet|hoodi|holesky|sepolia|ephemery)\b",
                    line,
                    flags=re.IGNORECASE,
                )
                if match:
                    return match.group(1).lower()
            stripped = line.strip().rstrip("\\").strip()
            if stripped.startswith("--network="):
                return stripped.split("=", 1)[1].strip().lower()
            if stripped.startswith("--network "):
                return stripped.split(None, 1)[1].strip().lower()
    return ""


def get_client_release_info(
    client: str,
    version_tag: str = "LATEST",
    network: Optional[str] = None,
) -> dict:
    """Get the correct release version, download URL(s), and filename(s) for a given client.

    When *version_tag* is ``LATEST`` and the resolved GitHub latest fails the
    network floor (e.g. Sepolia Gloas), a preferred install tag is substituted
    when :func:`client_requirements.preferred_install_tag` returns one.

    Args:
        client: The name of the client (case-insensitive).
        version_tag: 'LATEST' or a specific tag name.
        network: Optional network slug (``sepolia``, …). When omitted, best-effort
            detection uses ``NETWORK`` / ``ETH_NETWORK`` or installed unit files.

    Returns:
        A dictionary with keys ``version``, ``download_urls``, ``filenames``,
        ``sha256`` (GitHub asset digest, parallel to the URLs) or ``md5``
        (Geth downloads page), and optionally ``commit`` (git SHA of the
        release tag when resolvable).
    """
    from client_requirements import preferred_install_tag

    client = client.lower()
    
    # Normalize client name to module name
    if client in ["mevboost", "mev-boost"]:
        module_name = "mevboost"
    elif client in ["charon", "obol", "obol-charon"]:
        module_name = "charon"
    elif client in ["web3signer", "web3-signer"]:
        module_name = "web3signer"
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

    if not hasattr(module, "get_release_info"):
        raise ValueError(f"Client module deploy.{module_name} does not implement get_release_info")

    resolved_tag = version_tag
    info = module.get_release_info(resolved_tag, arch_amd64)

    if str(version_tag).upper() == "LATEST":
        net = (network or _detect_runtime_network() or "").strip().lower()
        preferred = preferred_install_tag(client, net, info.get("version", ""))
        if preferred:
            # stderr: release_info CLI stdout must stay pure JSON for jq callers.
            print(
                f"WARNING: GitHub LATEST {client} {info.get('version')} is not compatible with "
                f"{net}; resolving {preferred} instead",
                file=sys.stderr,
                flush=True,
            )
            info = module.get_release_info(preferred, arch_amd64)

    return info


def extract_and_install(archive_path: str, client_name: str, dest: str, target_type: str, strip_components: int = 0, binary_name: Optional[str] = None) -> None:
    """Extract an archive and install its contents.

    Args:
        archive_path: Path to the archive file (.tar.gz, .zip, etc.)
        client_name: Client name (used to build the temp dir)
        dest: System target path
        target_type: 'binary' or 'directory'
        strip_components: Number of components to strip
        binary_name: Exact or prefix name of binary to find inside (if target_type == 'binary')
    """
    tmp_dir = f"/tmp/{client_name}_extract"
    # Ensure intermediate temp dir exists
    subprocess.run(["sudo", "rm", "-rf", tmp_dir], check=False)
    subprocess.run(["sudo", "mkdir", "-p", tmp_dir], check=True)

    try:
        # Extract archive (tmp_dir is root-owned; extraction must run via sudo).
        if archive_path.endswith(".zip"):
            subprocess.run(["sudo", "unzip", "-o", archive_path, "-d", tmp_dir], check=True)
        else:
            cmd = ["sudo", "tar", "xzf", archive_path, "-C", tmp_dir]
            if strip_components > 0:
                cmd.append(f"--strip-components={strip_components}")
            subprocess.run(cmd, check=True)

        if target_type == "binary":
            name_to_find = binary_name or os.path.basename(dest)
            found_bin = None
            for root, _, files in os.walk(tmp_dir):
                for file in files:
                    if file == name_to_find:
                        found_bin = os.path.join(root, file)
                        break
                if found_bin:
                    break

            if not found_bin:
                for root, _, files in os.walk(tmp_dir):
                    for file in files:
                        if file.startswith(name_to_find):
                            found_bin = os.path.join(root, file)
                            break
                    if found_bin:
                        break

            if not found_bin:
                raise FileNotFoundError(f"Could not find binary '{name_to_find}' inside extracted archive")

            install_system_binary(found_bin, dest)
        elif target_type == "directory":
            install_system_directory(tmp_dir, dest)
        else:
            raise ValueError(f"Unknown target_type: {target_type}")

    finally:
        subprocess.run(["sudo", "rm", "-rf", tmp_dir], check=False)
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except OSError:
                subprocess.run(["sudo", "rm", "-f", archive_path], check=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="EthPillar Common CLI Utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download", help="Download a file with progress bar")
    download_parser.add_argument("url", type=str, help="URL to download from")
    download_parser.add_argument("dest", type=str, help="Destination file path")
    download_parser.add_argument("label", type=str, nargs="?", default="file", help="Descriptive label")
    download_parser.add_argument("--sha256", type=str, default=None, help="Expected SHA-256 hex digest")
    download_parser.add_argument("--md5", type=str, default=None, help="Expected MD5 hex digest")
    download_parser.add_argument("--signature-url", type=str, default=None, help="Detached signature URL")
    download_parser.add_argument("--signature-key", type=str, default=None, help="Pinned key name, such as geth")

    info_parser = subparsers.add_parser("release_info", help="Get client release version and download URLs")
    info_parser.add_argument("client", type=str, help="Client name")
    info_parser.add_argument("version_tag", type=str, nargs="?", default="LATEST", help="Version tag or LATEST")
    info_parser.add_argument(
        "--network",
        type=str,
        default=None,
        help="Network slug for LATEST remapping (e.g. sepolia); defaults to NETWORK env or installed units",
    )

    extract_parser = subparsers.add_parser("extract_and_install", help="Extract and install client files")
    extract_parser.add_argument("archive_path", type=str, help="Path to archive file")
    extract_parser.add_argument("client_name", type=str, help="Client name")
    extract_parser.add_argument("dest", type=str, help="Installation destination")
    extract_parser.add_argument("target_type", type=str, choices=["binary", "directory"], help="Target type: binary or directory")
    extract_parser.add_argument("strip_components", type=int, nargs="?", default=0, help="Number of components to strip")
    extract_parser.add_argument("--binary-name", type=str, default=None, help="Name of binary to search for")

    args = parser.parse_args()
    if args.command == "download":
        download_file(
            args.url,
            args.dest,
            args.label,
            expected_sha256=args.sha256 or None,
            expected_md5=args.md5 or None,
            signature_url=args.signature_url or None,
            signature_key=args.signature_key or None,
        )
    elif args.command == "release_info":
        try:
            info = get_client_release_info(args.client, args.version_tag, network=args.network)
            print(json.dumps(info))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            exit(1)
    elif args.command == "extract_and_install":
        try:
            extract_and_install(
                args.archive_path,
                args.client_name,
                args.dest,
                args.target_type,
                args.strip_components,
                args.binary_name
            )
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            exit(1)

