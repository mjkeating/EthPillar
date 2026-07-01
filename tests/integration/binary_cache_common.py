"""Binary download and extract cache access tracking + pruning for integration tests.

During a matrix run, cache hits and new writes append basenames to
``.accessed_this_run.log`` under the cache directory. After the run,
``prune_unaccessed_binary_cache`` removes ``*.bin`` and ``extracted_*.tar``
entries that were not touched — dropping old client releases no longer exercised
by the current test matrix.
"""

from __future__ import annotations

import fnmatch
import os
from typing import Iterable

ACCESS_LOG_NAME = ".accessed_this_run.log"
SKIP_PRUNE_ENV = "ETHPILLAR_SKIP_BINARY_CACHE_PRUNE"

# Basenames matching these globs may be pruned when not accessed this run.
PRUNABLE_GLOBS = ("*.bin", "extracted_*.tar", "tmp_*.tar", "*.txt")

# Never delete housekeeping / in-flight files.
PROTECTED_NAMES = frozenset(
    {
        ACCESS_LOG_NAME,
        ".gitkeep",
    }
)


def default_cache_dir() -> str:
    """Host or in-container path to the integration binary cache."""
    override = os.environ.get("ETHPILLAR_BINARY_CACHE_DIR")
    if override:
        return override
    integration_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(integration_dir, "cache")


def access_log_path(cache_dir: str | None = None) -> str:
    return os.path.join(cache_dir or default_cache_dir(), ACCESS_LOG_NAME)


def _sudo_unlink(path: str) -> None:
    """Remove *path* with sudo when the integration user cannot unlink root-owned cache files."""
    import subprocess

    subprocess.run(["sudo", "rm", "-f", path], check=False)


def _ensure_cache_dir_writable(directory: str) -> None:
    """Ensure the integration user can create files under *directory* on bind mounts.

    Prior root-owned runs (or host-side cache reset) can leave the cache directory
    itself root-owned; removing the access log is not enough without fixing the dir.
    """
    import subprocess

    try:
        os.chmod(directory, 0o777)
    except (PermissionError, OSError):
        subprocess.run(["sudo", "chmod", "a+rwX", directory], check=False)


def _append_access_log_line(log_path: str, line: str) -> None:
    """Append one line to the access log, using sudo when the dir is not writable."""
    import shlex
    import subprocess

    directory = os.path.dirname(log_path)
    script = (
        f"mkdir -p {shlex.quote(directory)} && "
        f"printf %s {shlex.quote(line)} >> {shlex.quote(log_path)} && "
        f"chmod 644 {shlex.quote(log_path)}"
    )
    subprocess.run(["sudo", "bash", "-c", script], check=True)


def ensure_binary_cache_dir_writable(cache_dir: str | None = None) -> None:
    """Ensure the cache directory exists and is writable without resetting the access log.

    Called at the start of each integration test container so non-root deploys can
    append to ``.accessed_this_run.log``. Only ``prepare_binary_cache_dir`` should
    reset the log (once per matrix run).
    """
    directory = cache_dir or default_cache_dir()
    os.makedirs(directory, exist_ok=True)
    _ensure_cache_dir_writable(directory)


def prepare_binary_cache_dir(cache_dir: str | None = None) -> None:
    """Reset the access log and make the cache directory writable for a new matrix run."""
    directory = cache_dir or default_cache_dir()
    os.makedirs(directory, exist_ok=True)
    reset_access_log(directory)
    _ensure_cache_dir_writable(directory)


def reset_access_log(cache_dir: str | None = None) -> None:
    """Start a fresh access log for the current integration run."""
    directory = cache_dir or default_cache_dir()
    os.makedirs(directory, exist_ok=True)
    path = access_log_path(directory)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except PermissionError:
        _sudo_unlink(path)


def record_cache_access(filename: str, cache_dir: str | None = None) -> None:
    """Record that *filename* (basename only) was read or written this run."""
    basename = os.path.basename(filename)
    if not basename or basename in PROTECTED_NAMES:
        return
    directory = cache_dir or default_cache_dir()
    os.makedirs(directory, exist_ok=True)
    log_path = access_log_path(directory)
    line = f"{basename}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line)
    except PermissionError:
        _ensure_cache_dir_writable(directory)
        try:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except PermissionError:
            _sudo_unlink(log_path)
            _append_access_log_line(log_path, line)
    try:
        os.chmod(log_path, 0o644)
    except OSError:
        pass


def load_accessed_basenames(cache_dir: str | None = None) -> set[str]:
    path = access_log_path(cache_dir)
    if not os.path.isfile(path):
        return set()
    accessed: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            name = line.strip()
            if name:
                accessed.add(name)
    return accessed


def _is_prunable(basename: str) -> bool:
    if basename in PROTECTED_NAMES or basename.startswith("."):
        return False
    return any(fnmatch.fnmatch(basename, pattern) for pattern in PRUNABLE_GLOBS)


def prune_unaccessed_binary_cache(
    cache_dir: str | None = None,
    *,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """Delete cache files not recorded in this run's access log.

    Returns ``(deleted_count, deleted_basenames)``. Skips pruning when the log
    is empty (no cache use this run) or when ``ETHPILLAR_SKIP_BINARY_CACHE_PRUNE``
    is set.
    """
    if os.environ.get(SKIP_PRUNE_ENV) == "1":
        print(f"[CACHE] Prune skipped ({SKIP_PRUNE_ENV}=1)")
        return 0, []

    directory = cache_dir or default_cache_dir()
    if not os.path.isdir(directory):
        return 0, []

    accessed = load_accessed_basenames(directory)
    if not accessed:
        print("[CACHE] Prune skipped (no cache accesses recorded this run)")
        return 0, []

    deleted: list[str] = []
    for entry in sorted(os.listdir(directory)):
        if entry in accessed or not _is_prunable(entry):
            continue
        path = os.path.join(directory, entry)
        if not os.path.isfile(path):
            continue
        if dry_run:
            deleted.append(entry)
            continue
        try:
            os.remove(path)
            deleted.append(entry)
        except PermissionError:
            _sudo_unlink(path)
            if not os.path.isfile(path):
                deleted.append(entry)
        except OSError as exc:
            print(f"[CACHE] Prune: could not remove {entry}: {exc}")

    if deleted:
        action = "Would remove" if dry_run else "Removed"
        print(f"[CACHE] {action} {len(deleted)} unaccessed cache file(s)")
        for name in deleted[:20]:
            print(f"[CACHE]   - {name}")
        if len(deleted) > 20:
            print(f"[CACHE]   ... and {len(deleted) - 20} more")
    else:
        print(f"[CACHE] Prune: all {len(accessed)} accessed cache file(s) retained")

    return len(deleted), deleted
