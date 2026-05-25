from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import urllib.error
import urllib.request
from typing import Optional, Sequence

from models import MachineInfo


def _run_command(command: Sequence[str], timeout: float = 2.0) -> Optional[str]:
    """Run a command and return its stdout output.

    Args:
        command: The command and arguments to execute as a sequence.
        timeout: Timeout in seconds for the subprocess execution.

    Returns:
        The command stdout as a string, or ``None`` on failure.
    """

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=timeout)
        return result.stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def get_cpu_model() -> str:
    """Detect the CPU model name on supported platforms.

    Returns:
        A human readable CPU model string, or a fallback message when unavailable.
    """

    if platform.system() not in ("Linux", "Darwin"):
        return "Unknown CPU"
    output = _run_command(["lscpu"], timeout=1.0)
    if not output:
        return "CPU Info Unavailable"
    match = re.search(r"Model name:\s*(.*)", output, re.IGNORECASE)
    return match.group(1).strip() if match else "CPU Info Unavailable"


def get_largest_ssd_model() -> str:
    """Return a description of the largest SSD-like block device on Linux.

    Returns:
        A string describing the device model and size, or a fallback message.
    """

    if platform.system() != "Linux":
        return "Unknown Storage"

    output = _run_command(["lsblk", "-b", "-d", "-J", "-o", "NAME,SIZE,ROTA,MODEL", "--json", "-e", "7"], timeout=3.0)
    if not output:
        return "Storage Info Unavailable"

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return "Storage Info Unavailable"

    largest_ssd = None
    max_size = 0
    for device in data.get("blockdevices", []):
        size = int(device.get("size") or 0)
        is_ssd = str(device.get("rota")) == "0" or str(device.get("name", "")).startswith("nvme")
        if is_ssd and size > max_size:
            max_size = size
            largest_ssd = device

    if not largest_ssd:
        return "No SSD Detected"

    model = str(largest_ssd.get("model") or largest_ssd.get("name") or "Unknown SSD").strip()
    size_gb = max_size / (1024**3)
    size_text = f"{size_gb / 1024:.1f}T" if size_gb >= 1024 else f"{size_gb:.0f}G"
    return f"{model} ({size_text})"


def get_total_ram() -> str:
    """Return total installed RAM as a human readable string on Linux.

    Returns:
        Total installed RAM (GB or TB), or a fallback when unavailable.
    """

    if platform.system() != "Linux":
        return "Unknown RAM"
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    gb = kb / 1024 / 1024
                    return f"{gb / 1024:.1f} TB" if gb >= 1024 else f"{gb:.0f} GB"
    except OSError:
        pass
    return "RAM Info Unavailable"


def detect_execution_rpc(endpoint: str, timeout: float = 2.0) -> Optional[tuple[str, str]]:
    """Query an execution client's JSON-RPC endpoint for client/version info.

    Args:
        endpoint: The HTTP endpoint URL of the execution client's JSON-RPC.
        timeout: Timeout in seconds for the HTTP request.

    Returns:
        A tuple of (client_name, client_version) on success, otherwise ``None``.
    """

    try:
        req_data = json.dumps({"jsonrpc": "2.0", "method": "web3_clientVersion", "params": [], "id": 2}).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError):
        return None

    result = parsed.get("result")
    if not result:
        return None

    match = re.match(r"([A-Za-z0-9_-]+)[/ ]?([^ ]*)", str(result))
    if not match:
        return None
    name = match.group(1)
    version = match.group(2) or "Unknown"
    return name, f"{name}:{version}"


def detect_execution_service_client() -> Optional[str]:
    """Inspect a systemd service file to heuristically detect the execution client.

    The function reads `EXECUTION_SERVICE_FILE` or `/etc/systemd/system/execution.service`.

    Returns:
        The detected client name (e.g. "geth"), or ``None`` if not determinable.
    """

    service_file = os.environ.get("EXECUTION_SERVICE_FILE", "/etc/systemd/system/execution.service")
    try:
        with open(service_file, "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return None

    description = re.search(r"^Description=(.+)$", content, re.MULTILINE)
    if description:
        first_word = description.group(1).strip().split()[0].lower()
        known_names = {"geth", "reth", "nethermind", "besu", "erigon"}
        if first_word in known_names:
            return first_word

    exec_start = re.search(r"^ExecStart=(.+)$", content, re.MULTILINE)
    if exec_start:
        value = exec_start.group(1).lower()
        for name in ("geth", "reth", "nethermind", "besu", "erigon"):
            if name in value:
                return name
    return None


def detect_client_info(endpoint: str) -> tuple[str, str]:
    """Detect execution client name and version, trying RPC then service file.

    Args:
        endpoint: JSON-RPC endpoint to try when detecting client via RPC.

    Returns:
        A tuple (client_name, client_version) with fallbacks when detection fails.
    """

    rpc_info = detect_execution_rpc(endpoint)
    if rpc_info:
        return rpc_info

    service_client = detect_execution_service_client()
    if service_client:
        return service_client, f"{service_client}:Unknown"

    return "unknown", "Client:Unknown"


def build_machine_info(client_version: str) -> MachineInfo:
    """Build a `MachineInfo` object by probing local system information.

    Args:
        client_version: Client version string to include in the returned `MachineInfo`.

    Returns:
        A populated `MachineInfo` instance.
    """

    return MachineInfo(
        client_version=client_version,
        cpu_model=get_cpu_model(),
        storage_model=get_largest_ssd_model(),
        installed_ram=get_total_ram(),
    )
