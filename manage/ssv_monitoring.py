"""Provision SSV Prometheus scrape and Grafana dashboards.

Used when SSV is installed and EthPillar monitoring (Prometheus/Grafana) is
present — or when monitoring is installed while SSV already exists.

* Go SSV: official Operational dashboard from ``ssvlabs/ssv-stack``.
* Anchor: ``anchor-dash.json`` from ``sigp/anchor`` (stable branch).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

from manage.grafana import (
    DEFAULT_DATASOURCES_YML,
    DEFAULT_GRAFANA_DASHBOARDS,
    download_grafana_dashboard,
    ensure_prometheus_datasource_uid,
    finalize_grafana_provisioned_file,
    read_privileged_text,
    write_privileged_file,
)

SSV_OPERATIONAL_URL = (
    "https://raw.githubusercontent.com/ssvlabs/ssv-stack/"
    "main/ssv-grafana-dashboard/ssv-operational.json"
)
ANCHOR_DASHBOARD_URL = (
    "https://raw.githubusercontent.com/sigp/anchor/stable/"
    "metrics/dashboards/anchor-dash.json"
)
SSV_DASHBOARD_FILENAME = "ssv_operational.json"
ANCHOR_DASHBOARD_FILENAME = "anchor_dash.json"
DEFAULT_PROMETHEUS_YML = Path("/etc/prometheus/prometheus.yml")
_JOB_RE = re.compile(r"job_name:\s*['\"]ssv['\"]")


def ssv_scrape_job(port: int = 15000, pod_label: str = "ssv-node") -> str:
    """Return a Prometheus scrape job targeting SSV metrics on *port*.

    Official SSV dashboards template on a ``pod`` label. Bare Prometheus does
    not set it, so we add a static relabel.

    Args:
        port: SSV ``MetricsAPIPort`` (default 15000).
        pod_label: Value written to the ``pod`` label.

    Returns:
        YAML snippet for ``scrape_configs`` (includes trailing newline).
    """
    return (
        "   - job_name: 'ssv'\n"
        "     metrics_path: /metrics\n"
        "     static_configs:\n"
        f"       - targets: ['127.0.0.1:{int(port)}']\n"
        "     relabel_configs:\n"
        "       - target_label: pod\n"
        f"         replacement: {pod_label}\n"
    )


SSV_SCRAPE_JOB = ssv_scrape_job(15000)


def has_ssv_scrape(text: str) -> bool:
    """Return True if prometheus.yml already defines an SSV scrape job."""
    return bool(_JOB_RE.search(text))


def _ssv_metrics_port() -> int:
    """Read SSV/Anchor metrics port from live config/unit, with client defaults."""
    try:
        from deploy.ssv import default_metrics_port, get_ssv_client, read_ssv_ports

        client = get_ssv_client()
        return int(read_ssv_ports(client=client)["metrics"])
    except Exception:
        try:
            from deploy.ssv import default_metrics_port, get_ssv_client

            return default_metrics_port(get_ssv_client())
        except Exception:
            return 15000


def _ssv_dashboard_for_client(client: Optional[str] = None) -> tuple[str, str]:
    """Return ``(dashboard_url, filename)`` for the installed SSV client."""
    try:
        from deploy.ssv import SSV_CLIENT_ANCHOR, get_ssv_client, normalize_ssv_client

        slug = normalize_ssv_client(client) if client else get_ssv_client()
        if slug == SSV_CLIENT_ANCHOR:
            return ANCHOR_DASHBOARD_URL, ANCHOR_DASHBOARD_FILENAME
    except Exception:
        pass
    return SSV_OPERATIONAL_URL, SSV_DASHBOARD_FILENAME


def ensure_ssv_scrape(prometheus_yml: Path, metrics_port: Optional[int] = None) -> bool:
    """Append or update an SSV scrape job.

    Args:
        prometheus_yml: Prometheus config path.
        metrics_port: Metrics port. When omitted, read from SSV config.

    Returns:
        True if the file was modified; False if unchanged or missing.
    """
    if not prometheus_yml.is_file():
        return False
    port = metrics_port if metrics_port is not None else _ssv_metrics_port()
    job = ssv_scrape_job(port)
    text = read_privileged_text(prometheus_yml)
    if text is None:
        return False
    if has_ssv_scrape(text):
        updated, count = re.subn(
            r"(job_name:\s*['\"]ssv['\"][\s\S]*?targets:\s*\[['\"]?)(?:127\.0\.0\.1|localhost):\d+",
            rf"\g<1>127.0.0.1:{port}",
            text,
            count=1,
        )
        if not count or updated == text:
            return False
        write_privileged_file(prometheus_yml, updated)
        return True
    suffix = "" if text.endswith("\n") else "\n"
    write_privileged_file(prometheus_yml, text + suffix + job)
    return True


def rewrite_ssv_dashboard_pod_variable(dashboard: dict) -> dict:
    """Point the ``pod`` template at scraped SSV series, not OTel-only metrics.

    Upstream SSV Operational uses
    ``label_values(otel_scope_info{otel_scope_name=~"github.com/ssvlabs/.*"}, pod)``.
    Bare-metal EthPillar scrapes ``/metrics`` with a static ``pod`` relabel; many
    nodes never emit ``otel_scope_info``, so the dropdown stays empty and every
    panel filters to nothing. Prefer ``up{job="ssv"}`` (always present when the
    scrape succeeds).
    """
    templating = dashboard.get("templating") or {}
    variables = templating.get("list") or []
    for var in variables:
        if var.get("name") != "pod":
            continue
        new_query = 'label_values(up{job="ssv"}, pod)'
        query = var.get("query")
        if isinstance(query, dict):
            query["query"] = new_query
            var["query"] = query
        else:
            var["query"] = new_query
        var["definition"] = new_query
        var["refresh"] = var.get("refresh") or 2
        var["includeAll"] = True
        if not var.get("allValue"):
            var["allValue"] = ".*"
    return dashboard


def provision_ssv_dashboard(
    dashboards_dir: Path,
    url: Optional[str] = None,
    *,
    filename: Optional[str] = None,
    rewrite_pod: bool = True,
) -> bool:
    """Download an SSV/Anchor dashboard JSON into Grafana provisioning.

    Args:
        dashboards_dir: Grafana file-provisioning directory.
        url: Dashboard JSON URL (defaults to Go SSV Operational).
        filename: Destination basename under *dashboards_dir*.
        rewrite_pod: When True, rewrite the ``pod`` template for bare Prometheus.

    Returns:
        True if the dashboard file was written.
    """
    dest_name = filename or SSV_DASHBOARD_FILENAME
    dest = dashboards_dir / dest_name
    download_grafana_dashboard(dest, url or SSV_OPERATIONAL_URL)
    if rewrite_pod:
        try:
            raw = read_privileged_text(dest)
            if raw is None:
                raise FileNotFoundError(dest)
            data = json.loads(raw)
            rewrite_ssv_dashboard_pod_variable(data)
            write_privileged_file(dest, json.dumps(data, indent=2) + "\n")
            finalize_grafana_provisioned_file(dest)
        except (OSError, json.JSONDecodeError, TypeError, PermissionError) as exc:
            print(
                f"Warning: could not rewrite SSV dashboard pod variable: {exc}",
                file=sys.stderr,
            )
    else:
        finalize_grafana_provisioned_file(dest)
    return True


def provision_ssv_monitoring(
    prometheus_yml: Path = DEFAULT_PROMETHEUS_YML,
    grafana_dashboards: Path = DEFAULT_GRAFANA_DASHBOARDS,
    datasources_yml: Path = DEFAULT_DATASOURCES_YML,
    restart: bool = False,
    dashboard_url: Optional[str] = None,
) -> Dict[str, bool]:
    """Ensure SSV scrape job and client Grafana dashboard when monitoring exists.

    No-ops quietly when Prometheus/Grafana paths are absent. Permission errors
    while writing Grafana files propagate.

    Args:
        prometheus_yml: Prometheus config path.
        grafana_dashboards: Grafana dashboard provisioning directory.
        datasources_yml: Grafana datasources provisioning file.
        restart: Restart prometheus/grafana after changes.
        dashboard_url: Optional override dashboard JSON URL.

    Returns:
        Dict with ``scrape``, ``dashboard``, and ``datasource`` booleans.
    """
    results = {"scrape": False, "dashboard": False, "datasource": False}

    results["scrape"] = ensure_ssv_scrape(prometheus_yml)

    grafana_present = (
        grafana_dashboards.is_dir()
        or datasources_yml.is_file()
        or datasources_yml.parent.is_dir()
    )
    if grafana_present:
        results["datasource"] = ensure_prometheus_datasource_uid(datasources_yml)
        try:
            default_url, default_name = _ssv_dashboard_for_client()
            url = dashboard_url or default_url
            filename = default_name
            if dashboard_url and dashboard_url == ANCHOR_DASHBOARD_URL:
                filename = ANCHOR_DASHBOARD_FILENAME
            elif dashboard_url and dashboard_url != SSV_OPERATIONAL_URL:
                filename = Path(dashboard_url).name or default_name
            # Go Operational needs pod-variable rewrite; Anchor dash is self-contained.
            rewrite_pod = filename == SSV_DASHBOARD_FILENAME
            results["dashboard"] = provision_ssv_dashboard(
                grafana_dashboards,
                url=url,
                filename=filename,
                rewrite_pod=rewrite_pod,
            )
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: SSV Grafana dashboard provision failed: {exc}", file=sys.stderr)

    if restart and results["scrape"]:
        subprocess.run(
            ["sudo", "systemctl", "try-restart", "prometheus"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if restart and (results["datasource"] or results["dashboard"]):
        subprocess.run(
            ["sudo", "systemctl", "try-restart", "grafana-server"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return results


def main(argv: Optional[list] = None) -> int:
    """CLI: ``python -m manage.ssv_monitoring``."""
    parser = argparse.ArgumentParser(description="Provision SSV monitoring")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args(argv)
    results = provision_ssv_monitoring(restart=args.restart)
    print(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
