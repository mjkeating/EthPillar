#!/usr/bin/env python3
"""Transparent tar/unzip wrapper that caches extracted client install trees.

Invoked from ``PATH`` ahead of real ``tar``/``unzip`` inside integration containers.
On cache hit, replays a prior extract; on miss, runs the real command and snapshots
only the archive member paths into ``tests/integration/cache/``.
"""
import hashlib
import os
import subprocess
import sys
from typing import List, Optional

_INTEGRATION_DIR = os.path.dirname(os.path.abspath(__file__))
if _INTEGRATION_DIR not in sys.path:
    sys.path.insert(0, _INTEGRATION_DIR)

from binary_cache_common import default_cache_dir, record_cache_access

CACHE_DIR = default_cache_dir()

TAR_ARCHIVE_SUFFIXES = (".tar.gz", ".tar.xz", ".tar.zst", ".tgz")
ZIP_ARCHIVE_SUFFIX = ".zip"


def get_extracted_cache_key(archive_path: str, dest_dir: str, strip_components: int) -> str:
    """Hash archive prefix, destination, and strip count into a stable cache id."""
    hasher = hashlib.md5()
    with open(archive_path, "rb") as handle:
        # Hash the first 1MB to be fast but reasonably unique.
        hasher.update(handle.read(1024 * 1024))
    hasher.update(dest_dir.encode("utf-8"))
    hasher.update(str(strip_components).encode("utf-8"))
    return hasher.hexdigest()


def parse_strip_components(args: List[str]) -> int:
    """Parse ``--strip-components`` from a tar argument list."""
    for index, arg in enumerate(args):
        if arg == "--strip-components" and index + 1 < len(args):
            return int(args[index + 1])
        if arg.startswith("--strip-components="):
            return int(arg.split("=", 1)[1])
    return 0


def parse_tar_invocation(args: List[str]) -> tuple[Optional[str], Optional[str], int]:
    """Extract archive path, ``-C`` destination, and strip count from tar args."""
    archive_path = None
    dest_dir = None
    strip_components = parse_strip_components(args)

    for index, arg in enumerate(args):
        if arg.endswith(TAR_ARCHIVE_SUFFIXES):
            archive_path = arg
        elif arg == "-C" and index + 1 < len(args):
            dest_dir = args[index + 1]

    return archive_path, dest_dir, strip_components


def parse_unzip_invocation(args: List[str]) -> tuple[Optional[str], Optional[str], int]:
    """Extract archive path and ``-d`` destination from unzip args."""
    archive_path = None
    dest_dir = None

    for index, arg in enumerate(args):
        if arg.endswith(ZIP_ARCHIVE_SUFFIX):
            archive_path = arg
        elif arg == "-d" and index + 1 < len(args):
            dest_dir = args[index + 1]

    return archive_path, dest_dir, 0


def list_tar_members(archive_path: str) -> List[str]:
    """List member paths inside a tar archive without extracting."""
    if archive_path.endswith((".tar.gz", ".tgz")):
        cmd = ["/usr/bin/tar", "tzf", archive_path]
    elif archive_path.endswith(".tar.xz"):
        cmd = ["/usr/bin/tar", "tJf", archive_path]
    elif archive_path.endswith(".tar.zst"):
        cmd = ["/usr/bin/tar", "--zstd", "-tf", archive_path]
    else:
        cmd = ["/usr/bin/tar", "tf", archive_path]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def list_zip_members(archive_path: str) -> List[str]:
    """List member paths inside a zip archive without extracting."""
    result = subprocess.run(
        ["/usr/bin/unzip", "-Z1", archive_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def archive_members(cmd_type: str, archive_path: str) -> List[str]:
    """Return member paths for either a tar or zip archive."""
    if cmd_type == "tar":
        return list_tar_members(archive_path)
    return list_zip_members(archive_path)


def stripped_dest_paths(members: List[str], strip_components: int) -> List[str]:
    """Map archive member names to paths relative to the extract destination."""
    paths: List[str] = []
    seen = set()

    for member in members:
        normalized = member.lstrip("./").rstrip("/")
        if not normalized:
            continue

        parts = normalized.split("/")
        if len(parts) <= strip_components:
            continue

        relative = "/".join(parts[strip_components:])
        if relative and relative not in seen:
            seen.add(relative)
            paths.append(relative)

    return paths


def existing_dest_paths(dest_dir: str, relative_paths: List[str]) -> List[str]:
    """Return *relative_paths* that exist under *dest_dir* after extraction."""
    existing: List[str] = []
    for relative in relative_paths:
        if os.path.lexists(os.path.join(dest_dir, relative)):
            existing.append(relative)
    return existing


def write_selective_cache(
    dest_dir: str,
    relative_paths: List[str],
    cache_tar: str,
    cache_key: str,
) -> None:
    """Snapshot extracted *relative_paths* from *dest_dir* into *cache_tar*."""
    temp_tar = os.path.join(CACHE_DIR, f"tmp_{cache_key}.tar")
    result = subprocess.run(
        ["/usr/bin/sudo", "/usr/bin/tar", "cf", temp_tar, "-C", dest_dir, *relative_paths],
        check=False,
    )
    if result.returncode != 0:
        subprocess.run(["/usr/bin/sudo", "/usr/bin/rm", "-f", temp_tar], check=False)
        print(f"[EXTRACT CACHE] Warning: Could not cache extracted files for {dest_dir}")
        return

    # temp_tar is created via sudo tar (root-owned); make it readable before rename.
    subprocess.run(["/usr/bin/sudo", "/usr/bin/chmod", "644", temp_tar], check=False)
    try:
        os.rename(temp_tar, cache_tar)
        # Same root-ownership story as sitecustomize.py — runner must read extracted_*.tar.
        os.chmod(cache_tar, 0o644)
        record_cache_access(cache_tar, CACHE_DIR)
    except OSError:
        subprocess.run(["/usr/bin/sudo", "/usr/bin/rm", "-f", temp_tar], check=False)


def main() -> None:
    """Intercept tar/unzip: replay or populate the extract cache, then exit with tar's code."""
    if len(sys.argv) < 2:
        sys.exit(1)

    cmd_type = sys.argv[1]
    args = sys.argv[2:]
    real_bin = f"/usr/bin/{cmd_type}"
    cmd = [real_bin, *args]

    if cmd_type == "tar":
        archive_path, dest_dir, strip_components = parse_tar_invocation(args)
    elif cmd_type == "unzip":
        archive_path, dest_dir, strip_components = parse_unzip_invocation(args)
    else:
        os.execv(real_bin, cmd)

    if not archive_path or not dest_dir or not os.path.exists(archive_path):
        os.execv(real_bin, cmd)

    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_key = get_extracted_cache_key(archive_path, dest_dir, strip_components)
    cache_tar = os.path.join(CACHE_DIR, f"extracted_{cache_key}.tar")

    if os.path.exists(cache_tar) and os.path.getsize(cache_tar) > 0:
        print(f"[EXTRACT CACHE] Hit for {os.path.basename(archive_path)}")
        record_cache_access(cache_tar, CACHE_DIR)
        os.makedirs(dest_dir, exist_ok=True)
        result = subprocess.run(
            ["/usr/bin/sudo", "/usr/bin/tar", "xf", cache_tar, "-C", dest_dir],
            check=True,
        )
        sys.exit(result.returncode)

    print(f"[EXTRACT CACHE] Miss for {os.path.basename(archive_path)}. Extracting...")
    result = subprocess.run(cmd, check=True)
    if result.returncode != 0 or not os.path.exists(dest_dir):
        sys.exit(result.returncode)

    members = archive_members(cmd_type, archive_path)
    relative_paths = existing_dest_paths(
        dest_dir,
        stripped_dest_paths(members, strip_components),
    )
    if not relative_paths:
        print(
            f"[EXTRACT CACHE] Warning: No archive members found to cache for "
            f"{os.path.basename(archive_path)}"
        )
        sys.exit(result.returncode)

    print(f"[EXTRACT CACHE] Caching {len(relative_paths)} extracted path(s)...")
    write_selective_cache(dest_dir, relative_paths, cache_tar, cache_key)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
