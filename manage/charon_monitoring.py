"""Provision Charon Prometheus scrape and CDVN Grafana dashboards.

Used when Charon is installed and EthPillar monitoring (Prometheus/Grafana) is
present — or when monitoring is installed while Charon already exists. Downloads
the same dashboard JSON bundle as upstream CDVN (Overview, Cluster, Node, Logs).
"""

from __future__ import annotations

import argparse
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
    read_privileged_text,
    write_privileged_file,
)

CHARON_OVERVIEW_URL = (
    "https://raw.githubusercontent.com/ObolNetwork/charon-distributed-validator-node/"
    "main/grafana/dashboards/charon_overview_dashboard.json"
)
CDVN_DASHBOARDS_BASE = (
    "https://raw.githubusercontent.com/ObolNetwork/charon-distributed-validator-node/"
    "main/grafana/dashboards"
)
# Same JSON files CDVN Docker Grafana provisions (Overview, Cluster, Node, Logs).
CDVN_GRAFANA_DASHBOARDS = (
    "charon_overview_dashboard.json",
    "cluster_dashboard.json",
    "node_overview_dashboard.json",
    "logs_dashboard.json",
)
DEFAULT_PROMETHEUS_YML = Path("/etc/prometheus/prometheus.yml")
_JOB_RE = re.compile(r"job_name:\s*['\"]charon['\"]")


def charon_scrape_job(port: int = 3620) -> str:
    """Return a Prometheus scrape job targeting Charon metrics on *port*.

    Args:
        port: Charon ``--monitoring-address`` port (default 3620).

    Returns:
        YAML snippet for ``scrape_configs`` (includes trailing newline).
    """
    return (
        "   - job_name: 'charon'\n"
        "     static_configs:\n"
        f"       - targets: ['localhost:{int(port)}']\n"
    )


CHARON_SCRAPE_JOB = charon_scrape_job(3620)


def has_charon_scrape(text: str) -> bool:
    """Return True if prometheus.yml already defines a Charon scrape job.

    Args:
        text: Full ``prometheus.yml`` contents.

    Returns:
        True when a ``job_name: charon`` entry is present.
    """
    return bool(_JOB_RE.search(text))


def _charon_metrics_port() -> int:
    """Read Charon ``--monitoring-address`` port from charon.service.

    Returns:
        Metrics port from ``charon.service``, or 3620 when the unit is absent.
    """
    try:
        from deploy.charon import parse_monitoring_port

        return parse_monitoring_port()
    except Exception:
        return 3620


def ensure_charon_scrape(prometheus_yml: Path, metrics_port: Optional[int] = None) -> bool:
    """Append or update a Charon scrape job.

    Args:
        prometheus_yml: Prometheus config path.
        metrics_port: Charon metrics port. When omitted, read from charon.service.

    Returns:
        True if the file was modified; False if unchanged or missing.
    """
    if not prometheus_yml.is_file():
        return False
    port = metrics_port if metrics_port is not None else _charon_metrics_port()
    job = charon_scrape_job(port)
    text = read_privileged_text(prometheus_yml)
    if text is None:
        return False
    if has_charon_scrape(text):
        updated, count = re.subn(
            r"(job_name:\s*['\"]charon['\"][\s\S]*?targets:\s*\[')localhost:\d+('\])",
            rf"\1localhost:{port}\2",
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


def download_charon_dashboard(dest: Path, url: str) -> None:
    """Download a CDVN/Charon Grafana dashboard JSON to ``dest``.

    Args:
        dest: Local path to write the JSON file.
        url: HTTP URL of the dashboard JSON.

    Raises:
        RuntimeError: When the download returns an empty body.
    """
    download_grafana_dashboard(dest, url)


def download_charon_overview_dashboard(
    dest: Path,
    url: str = CHARON_OVERVIEW_URL,
) -> None:
    """Download Obol Charon Overview dashboard JSON to ``dest``.

    Args:
        dest: Local path to write the JSON file.
        url: Overview dashboard JSON URL (defaults to :data:`CHARON_OVERVIEW_URL`).
    """
    download_charon_dashboard(dest, url)


def provision_cdvn_grafana_dashboards(
    dashboards_dir: Path,
    base_url: str = CDVN_DASHBOARDS_BASE,
    filenames: tuple[str, ...] = CDVN_GRAFANA_DASHBOARDS,
) -> int:
    """Download and write the CDVN Charon Grafana dashboard bundle.

    Args:
        dashboards_dir: Grafana file-provisioning directory.
        base_url: Raw GitHub base URL for CDVN dashboard JSON files.
        filenames: Dashboard basenames to fetch (see :data:`CDVN_GRAFANA_DASHBOARDS`).

    Returns:
        Count of dashboard files written.
    """
    written = 0
    for name in filenames:
        download_charon_dashboard(dashboards_dir / name, f"{base_url.rstrip('/')}/{name}")
        written += 1
    return written


def provision_charon_overview_dashboard(
    dashboards_dir: Path,
    url: str = CHARON_OVERVIEW_URL,
) -> bool:
    """Write Charon Overview into Grafana's file-provisioning directory.

    Args:
        dashboards_dir: Grafana file-provisioning directory.
        url: Overview dashboard JSON URL (defaults to :data:`CHARON_OVERVIEW_URL`).

    Returns:
        True if the dashboard file was written.
    """
    dest = dashboards_dir / "charon_overview_dashboard.json"
    download_charon_overview_dashboard(dest, url=url)
    return True


def provision_charon_dashboards(
    dashboards_dir: Path,
    *,
    all_cdvn: bool = True,
    dashboard_url: str = CHARON_OVERVIEW_URL,
) -> bool:
    """Write Charon Grafana dashboards (full CDVN bundle or Overview-only).

    Args:
        dashboards_dir: Grafana file-provisioning directory.
        all_cdvn: When True, provision :data:`CDVN_GRAFANA_DASHBOARDS`; else Overview only.
        dashboard_url: Overview JSON URL when ``all_cdvn`` is False.

    Returns:
        True if at least one dashboard file was written.
    """
    if all_cdvn:
        return provision_cdvn_grafana_dashboards(dashboards_dir) > 0
    return provision_charon_overview_dashboard(dashboards_dir, url=dashboard_url)


def provision_charon_monitoring(
    prometheus_yml: Path = DEFAULT_PROMETHEUS_YML,
    grafana_dashboards: Path = DEFAULT_GRAFANA_DASHBOARDS,
    datasources_yml: Path = DEFAULT_DATASOURCES_YML,
    restart: bool = False,
    dashboard_url: str = CHARON_OVERVIEW_URL,
    all_cdvn_dashboards: bool = True,
) -> Dict[str, bool]:
    """Ensure Charon scrape job and CDVN Grafana dashboards when monitoring exists.

    No-ops quietly when the passed Prometheus/Grafana paths are absent
    (monitoring not installed yet). Grafana work is gated only on
    *grafana_dashboards* / *datasources_yml* — never on a hardcoded
    ``/etc/grafana`` host path. Permission errors while writing Grafana
    files propagate (they are not soft-skipped).

    Args:
        prometheus_yml: Prometheus config path.
        grafana_dashboards: Grafana dashboard provisioning directory.
        datasources_yml: Grafana datasources provisioning file.
        restart: Restart prometheus/grafana after changes.
        dashboard_url: Overview-only JSON URL (when ``all_cdvn_dashboards`` is False).
        all_cdvn_dashboards: When True, provision the full CDVN dashboard bundle.

    Returns:
        Dict with ``scrape``, ``dashboard``, and ``datasource`` booleans.
    """
    results = {"scrape": False, "dashboard": False, "datasource": False}

    results["scrape"] = ensure_charon_scrape(prometheus_yml)

    # Gate solely on the caller-supplied paths — never treat host /etc/grafana as
    # an override when tests (or callers) pass isolated destinations.
    grafana_present = (
        grafana_dashboards.is_dir()
        or datasources_yml.is_file()
        or datasources_yml.parent.is_dir()
    )
    if grafana_present:
        results["datasource"] = ensure_prometheus_datasource_uid(datasources_yml)
        try:
            results["dashboard"] = provision_charon_dashboards(
                grafana_dashboards,
                all_cdvn=all_cdvn_dashboards,
                dashboard_url=dashboard_url,
            )
        except PermissionError:
            # Installed Grafana with unreadable/unwritable paths must not soft-skip.
            raise
        except Exception as exc:  # noqa: BLE001 — soft-fail download / non-perm errors
            print(f"Warning: Charon Grafana dashboard provision failed: {exc}", file=sys.stderr)

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
    """CLI entry for Bash install hooks.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success. A missing/unknown subcommand makes argparse exit
        with status ``2`` (subcommand is required).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("provision", help="Ensure Charon scrape job + CDVN dashboards (Overview only with --overview-only)")
    p.add_argument("--prometheus-yml", type=Path, default=DEFAULT_PROMETHEUS_YML)
    p.add_argument("--grafana-dashboards", type=Path, default=DEFAULT_GRAFANA_DASHBOARDS)
    p.add_argument("--dashboard-url", default=CHARON_OVERVIEW_URL)
    p.add_argument(
        "--overview-only",
        action="store_true",
        help="Provision Charon Overview only (default: full CDVN dashboard bundle)",
    )
    p.add_argument("--restart", action="store_true", help="try-restart prometheus if scrape changed, grafana-server if datasource/dashboards changed")

    args = parser.parse_args(argv)
    if args.cmd == "provision":
        results = provision_charon_monitoring(
            prometheus_yml=args.prometheus_yml,
            grafana_dashboards=args.grafana_dashboards,
            restart=args.restart,
            dashboard_url=args.dashboard_url,
            all_cdvn_dashboards=not args.overview_only,
        )
        print(
            "charon monitoring: "
            f"scrape_updated={results['scrape']} "
            f"dashboard_written={results['dashboard']} "
            f"datasource_uid={results['datasource']}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
