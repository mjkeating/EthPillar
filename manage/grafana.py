"""Grafana helpers: ini http_port, provisioning files, and dashboard downloads."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import List, Optional, Union

GRAFANA_INI_PATH = Path("/etc/grafana/grafana.ini")
DEFAULT_GRAFANA_HTTP_PORT = 3000
DEFAULT_GRAFANA_DASHBOARDS = Path("/etc/grafana/provisioning/dashboards")
DEFAULT_DATASOURCES_YML = Path("/etc/grafana/provisioning/datasources/datasources.yml")
_DEFAULT_PROMETHEUS_DATASOURCE = (
    "apiVersion: 1\n"
    "datasources:\n"
    "  - name: Prometheus\n"
    "    type: prometheus\n"
    "    uid: prometheus\n"
    "    url: http://localhost:9090\n"
    "    access: proxy\n"
    "    isDefault: true\n"
)


def read_grafana_ini() -> Optional[str]:
    """Read ``/etc/grafana/grafana.ini`` (directly or via sudo when needed).

    Returns:
        File text, or None when Grafana is not installed / unreadable.
    """
    if GRAFANA_INI_PATH.is_file():
        try:
            return GRAFANA_INI_PATH.read_text(encoding="utf-8")
        except OSError:
            pass
    result = subprocess.run(
        ["sudo", "cat", str(GRAFANA_INI_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        return result.stdout
    return None


def _parse_grafana_http_port_from_ini(content: str) -> Optional[int]:
    """Return active ``http_port`` from ``grafana.ini`` ``[server]`` section.

    Args:
        content: Existing ``grafana.ini`` text.

    Returns:
        Active (uncommented) port, else the commented ``[server]`` default, else None.
    """
    in_server = False
    commented: Optional[int] = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_server = stripped.lower() == "[server]"
            continue
        if not in_server:
            continue
        match = re.match(r"^;?\s*http_port\s*=\s*(\d+)", stripped)
        if not match:
            continue
        port = int(match.group(1))
        if stripped.startswith(";"):
            commented = port
        else:
            return port
    return commented


def read_grafana_http_port(default: int = DEFAULT_GRAFANA_HTTP_PORT) -> int:
    """Return Grafana ``http_port`` from ``grafana.ini``, or *default* when unset.

    Args:
        default: Port to use when the ini is missing or has no ``http_port``.

    Returns:
        Configured Grafana HTTP port.
    """
    content = read_grafana_ini()
    if content is None:
        return default
    port = _parse_grafana_http_port_from_ini(content)
    return port if port is not None else default


def apply_grafana_http_port(port: int) -> bool:
    """Set Grafana ``http_port`` in ``grafana.ini`` and restart grafana-server.

    Args:
        port: TCP port for the Grafana HTTP UI.

    Returns:
        True when the ini was written (or already matched). False when the ini
        is missing (Grafana not installed).
    """
    content = read_grafana_ini()
    if content is None:
        return False

    fd, tmp_name = tempfile.mkstemp(prefix="ethpillar-grafana-", suffix=".ini")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_grafana_ini_with_http_port(content, port))
        subprocess.run(["sudo", "cp", tmp_name, str(GRAFANA_INI_PATH)], check=True)
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
    subprocess.run(
        ["sudo", "systemctl", "try-restart", "grafana-server"],
        check=False,
    )
    return True


def _grafana_ini_with_http_port(content: str, port: int) -> str:
    """Return ``grafana.ini`` content with ``[server] http_port`` set to *port*.

    Args:
        content: Existing ``grafana.ini`` text.
        port: TCP port for Grafana HTTP UI.

    Returns:
        Updated ini text (inserts or replaces ``http_port`` under ``[server]``).
    """
    new_line = f"http_port = {port}\n"
    out: List[str] = []
    in_server = False
    replaced = False
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_server and not replaced:
                out.append(new_line)
                replaced = True
            in_server = stripped.lower() == "[server]"
            out.append(line)
            continue
        if in_server and re.match(r"^;?\s*http_port", stripped):
            out.append(new_line)
            replaced = True
            continue
        out.append(line)
    if in_server and not replaced:
        out.append(new_line)
    return "".join(out)


def finalize_grafana_provisioned_file(path: Path) -> None:
    """Ensure Grafana (user ``grafana``) can read a file-provisioned file.

    Args:
        path: Provisioned file under ``/etc/grafana`` (no-op for other paths).
    """
    if not str(path).startswith("/etc/grafana"):
        return
    subprocess.run(["sudo", "chown", "root:grafana", str(path)], check=False)
    subprocess.run(["sudo", "chmod", "644", str(path)], check=False)


def read_privileged_text(path: Path) -> Optional[str]:
    """Read text from *path*, falling back to ``sudo cat`` on permission errors.

    Args:
        path: File to read.

    Returns:
        File contents, or None when the file is missing or another OSError
        occurs.

    Raises:
        PermissionError: If the direct read is denied and ``sudo cat`` fails.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except PermissionError:
        result = subprocess.run(
            ["sudo", "cat", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
        raise PermissionError(
            f"Permission denied reading {path} (sudo cat failed: "
            f"{(result.stderr or result.stdout or '').strip() or f'exit {result.returncode}'})"
        ) from None
    except OSError:
        return None


def write_privileged_file(path: Path, data: Union[str, bytes]) -> None:
    """Write ``data`` to ``path``, using ``sudo cp`` when /etc is not writable.

    Grafana-provisioned paths also get ``root:grafana`` ownership.

    Args:
        path: Destination file.
        data: Text or bytes to write (text is encoded as UTF-8).
    """
    raw = data.encode("utf-8") if isinstance(data, str) else data
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        finalize_grafana_provisioned_file(path)
        return
    except PermissionError:
        pass

    subprocess.run(["sudo", "mkdir", "-p", str(path.parent)], check=True)
    fd, tmp = tempfile.mkstemp(prefix="ethpillar-grafana-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        subprocess.run(["sudo", "cp", tmp, str(path)], check=True)
        finalize_grafana_provisioned_file(path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def ensure_prometheus_datasource_uid(datasources_yml: Path) -> bool:
    """Ensure Grafana Prometheus datasource uses ``uid: prometheus``.

    Gates on the passed *datasources_yml* path only (parent dir or existing file).
    Does not consult a hardcoded ``/etc/grafana`` override.

    Args:
        datasources_yml: Grafana datasources provisioning file path.

    Returns:
        True if the file was created or modified.
    """
    text = read_privileged_text(datasources_yml)
    if text is None:
        # Create only when the provisioning datasources dir already exists for
        # these paths (monitoring stack present). Avoid host /etc/grafana checks
        # so tests can pass isolated tmp paths.
        if not datasources_yml.parent.is_dir():
            return False
        write_privileged_file(datasources_yml, _DEFAULT_PROMETHEUS_DATASOURCE)
        return True

    if re.search(r"uid:\s*['\"]?prometheus['\"]?", text):
        return False

    updated, count = re.subn(
        r"(name:\s*Prometheus\r?\n\s*type:\s*prometheus\r?\n)",
        r"\1    uid: prometheus\n",
        text,
        count=1,
    )
    if count:
        write_privileged_file(datasources_yml, updated)
        return True
    return False


def download_grafana_dashboard(dest: Path, url: str) -> None:
    """Download a Grafana dashboard JSON to ``dest``.

    Args:
        dest: Local path to write the JSON file.
        url: HTTP URL of the dashboard JSON.

    Raises:
        RuntimeError: When the download returns an empty body.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "ethpillar"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    if not data:
        raise RuntimeError(f"Empty response downloading Grafana dashboard from {url}")
    write_privileged_file(dest, data)
