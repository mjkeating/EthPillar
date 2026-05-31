import os
import subprocess
import hashlib
import shutil

CACHE_DIR = "/ethpillar/tests/integration/cache"

def get_extracted_cache_key(archive_path: str, dest_dir: str) -> str:
    """Generate a unique cache key based on the archive hash and destination."""
    hasher = hashlib.md5()
    with open(archive_path, 'rb') as f:
        # Hash the first 1MB to be fast but reasonably unique
        buf = f.read(1024 * 1024)
        hasher.update(buf)
    hasher.update(dest_dir.encode('utf-8'))
    return hasher.hexdigest()

def _cache_is_populated(cache_path):
    """Return True only if the cache directory exists and has at least one entry besides . and .."""
    if not os.path.isdir(cache_path):
        return False
    try:
        return len(os.listdir(cache_path)) > 0
    except PermissionError:
        return False

def intercept_subprocess_run(*args, **kwargs):
    """Intercept subprocess.run to cache tar/unzip operations."""
    import sitecustomize
    original_run = sitecustomize.original_subprocess_run

    cmd = args[0] if args else kwargs.get('args', [])
    if not isinstance(cmd, list):
        return original_run(*args, **kwargs)

    is_sudo_tar = len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "tar" and "x" in cmd[2]
    is_plain_tar = len(cmd) >= 3 and cmd[0] == "tar" and "x" in cmd[1]
    is_tar = is_sudo_tar or is_plain_tar

    is_sudo_unzip = len(cmd) >= 3 and cmd[0] == "sudo" and cmd[1] == "unzip"
    is_plain_unzip = len(cmd) >= 2 and cmd[0] == "unzip"
    is_unzip = is_sudo_unzip or is_plain_unzip

    if not (is_tar or is_unzip):
        return original_run(*args, **kwargs)

    # Find the archive path and destination dir
    archive_path = None
    dest_dir = None

    if is_tar:
        # sudo tar xzf /path/to/file.tar.gz -C /dest/dir
        for i, arg in enumerate(cmd):
            if arg.endswith(".tar.gz") or arg.endswith(".tar.xz"):
                archive_path = arg
            elif arg == "-C" and i + 1 < len(cmd):
                dest_dir = cmd[i + 1]
    elif is_unzip:
        # sudo unzip /path/to/file.zip -d /dest/dir
        for i, arg in enumerate(cmd):
            if arg.endswith(".zip"):
                archive_path = arg
            elif arg == "-d" and i + 1 < len(cmd):
                dest_dir = cmd[i + 1]

    # If we couldn't parse it or the archive doesn't exist, just run normally
    if not archive_path or not dest_dir or not os.path.exists(archive_path):
        return original_run(*args, **kwargs)

    # Blacklist system directories where many binaries share the same folder
    # because cp -a will try to cache the entire directory
    if dest_dir in ["/usr/local/bin", "/usr/bin", "/bin", "/usr/local/bin/", "/usr/bin/", "/bin/"]:
        return original_run(*args, **kwargs)

    # Make sure cache dir exists
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Generate cache key and path
    cache_key = get_extracted_cache_key(archive_path, dest_dir)
    cache_path = os.path.join(CACHE_DIR, f"extracted_{cache_key}")

    # Check if we have a valid (non-empty) cache hit
    if _cache_is_populated(cache_path):
        print(f"[EXTRACT CACHE] Hit for {os.path.basename(archive_path)}")

        # Ensure destination directory exists
        subprocess.run(["sudo", "mkdir", "-p", dest_dir], check=False)

        # Copy from cache to destination using sudo (since destination might require root)
        copy_cmd = ["sudo", "cp", "-a", f"{cache_path}/.", f"{dest_dir}/"]
        cp_result = original_run(copy_cmd, capture_output=kwargs.get('capture_output', False),
                                 text=kwargs.get('text', False), check=kwargs.get('check', False))

        # Verify the destination actually got populated (safety net against poison pills)
        if cp_result.returncode == 0 and os.path.isdir(dest_dir) and len(os.listdir(dest_dir)) > 0:
            # Fix ownership/permissions on the destination after copy, since cached files
            # may belong to the host user. This prevents systemd 203 errors from bad perms.
            subprocess.run(["sudo", "chown", "-R", "root:root", dest_dir], check=False)
            subprocess.run(["sudo", "chmod", "-R", "u+w,go-w", dest_dir], check=False)
            subprocess.run(["sudo", "find", dest_dir, "-type", "f", "-perm", "/111", "-exec", "chmod", "755", "{}", ";"], check=False)
            subprocess.run(["sudo", "find", dest_dir, "-type", "d", "-exec", "chmod", "755", "{}", ";"], check=False)
            # Return a mock CompletedProcess matching the original command
            return subprocess.CompletedProcess(args=cmd, returncode=cp_result.returncode,
                                             stdout=cp_result.stdout, stderr=cp_result.stderr)
        else:
            print(f"[EXTRACT CACHE] Cache copy produced empty destination, falling back to extraction")

    # If cache_path exists but is empty, it's a poison pill from a failed prior run — remove it
    if os.path.exists(cache_path):
        subprocess.run(["sudo", "rm", "-rf", cache_path], check=False)

    # Cache miss - run the original command
    print(f"[EXTRACT CACHE] Miss for {os.path.basename(archive_path)}. Extracting...")
    result = original_run(*args, **kwargs)

    # If extraction succeeded, cache the result
    if result.returncode == 0 and os.path.exists(dest_dir):
        print(f"[EXTRACT CACHE] Caching extracted files...")

        # Create temp dir for the cache to avoid partial copies
        import tempfile
        temp_cache = tempfile.mkdtemp(dir=CACHE_DIR)

        # Copy from destination to temp cache (use sudo in case of permission issues)
        subprocess.run(["sudo", "cp", "-a", f"{dest_dir}/.", f"{temp_cache}/"], check=False)

        # Make sure the cache is readable by us
        subprocess.run(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", temp_cache], check=False)

        # Move to final cache location atomically
        try:
            os.rename(temp_cache, cache_path)
        except OSError:
            # Race: another container already created this cache entry. Discard ours.
            subprocess.run(["sudo", "rm", "-rf", temp_cache], check=False)

    return result
