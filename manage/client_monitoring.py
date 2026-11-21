"""Provision Prometheus scrape jobs and Grafana dashboards for installed clients.

EthPillar already enables client metrics (execution :6060, consensus :8008,
validator :8009, Prysm :8080/:8081). Monitoring previously scraped only
ethereum-metrics-exporter and node_exporter, so client dashboards had no series.

Dashboard sources match eth-docker's Grafana provisioner
(https://github.com/ethstaker/eth-docker/blob/main/grafana/provision.sh).
Scrape jobs are written in a marked block inside prometheus.yml so refresh
replaces EthPillar-managed jobs and leaves hand-edited jobs outside the block.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from manage.grafana import (
    DEFAULT_GRAFANA_DASHBOARDS,
    download_grafana_dashboard,
    read_privileged_text,
    remove_privileged_file,
    write_privileged_file,
)

DEFAULT_PROMETHEUS_YML = Path("/etc/prometheus/prometheus.yml")
DEFAULT_EXECUTION_UNIT = Path("/etc/systemd/system/execution.service")
DEFAULT_CONSENSUS_UNIT = Path("/etc/systemd/system/consensus.service")
DEFAULT_VALIDATOR_UNIT = Path("/etc/systemd/system/validator.service")

_BLOCK_BEGIN = "# BEGIN ethpillar client scrape jobs\n"
_BLOCK_END = "# END ethpillar client scrape jobs\n"
_BLOCK_RE = re.compile(
    r"# BEGIN ethpillar client scrape jobs\n.*?# END ethpillar client scrape jobs\n?",
    re.DOTALL,
)
_JOB_RE = re.compile(r"job_name:\s*['\"]([^'\"]+)['\"]")
_PORT_FLAGS = (
    "--metrics-port",
    "--metrics.port",
    "--Metrics.ExposePort",
    "--monitoring-port",
)

_DS_TOKENS = {
    "${DS_PROMETHEUS}",
    "${DS_PROMETHEUS-PROXY}",
    "${datasource}",
    "prometheus_local",
}


@dataclass(frozen=True)
class TemplatePin:
    """Override one Grafana template variable so panels query scraped series.

    Upstream dashboards often ship with another lab's job name or instance
    (Ethrex defaults to ``ethrex L1``; Grandine defaults to ``0.0.0.0:5061``).
    """

    name: str
    current: str = ""
    query: str = ""
    clear_regex: bool = False


@dataclass(frozen=True)
class DashboardSpec:
    """One Grafana dashboard JSON to download for a client."""

    filename: str
    url: str = ""
    grafana_id: Optional[int] = None
    title: str = ""
    uid: str = ""
    instance_label: str = ""
    lodestar_instances: bool = False
    template_pins: Tuple[TemplatePin, ...] = ()
    # Drop label matchers the upstream board requires but stock clients do not emit.
    strip_label_matchers: Tuple[str, ...] = ()
    # Grafana v2 dashboards store a datasource-variable uid, not the classic name.
    prometheus_datasource_uid: str = ""


@dataclass(frozen=True)
class ClientScrape:
    """Prometheus scrape target for one installed client role."""

    key: str
    job_name: str
    default_port: int
    metrics_path: str
    instance: str
    needles: Tuple[str, ...]
    extra_labels: Tuple[Tuple[str, str], ...] = ()
    dashboards: Tuple[DashboardSpec, ...] = field(default_factory=tuple)


def _lh_dashboards() -> Tuple[DashboardSpec, ...]:
    base = "https://raw.githubusercontent.com/sigp/lighthouse-metrics/master/dashboards"
    return (
        DashboardSpec(filename="lighthouse_summary.json", url=f"{base}/Summary.json", title="Lighthouse Summary"),
        DashboardSpec(
            filename="lighthouse_validator_monitor.json",
            url=f"{base}/ValidatorMonitor.json",
            title="Lighthouse Validator Monitor",
        ),
    )


def _lh_vc_dashboard() -> DashboardSpec:
    return DashboardSpec(
        filename="lighthouse_validator_client.json",
        url="https://raw.githubusercontent.com/sigp/lighthouse-metrics/master/dashboards/ValidatorClient.json",
        title="Lighthouse Validator Client",
    )


def _prysm_dashboards() -> Tuple[DashboardSpec, ...]:
    # Official beacon-node board from OffchainLabs/prysm-documentation
    # c0d5c52 (2026-11-09). Grafana 13 v2 resource: the title is spec.title.
    # System and Replay panels filter job="beacon". Pin the commit: the docs
    # site's hashed /docs/assets/files/<name>-<md5>.json URLs change on every
    # rebuild, and the old small-board URL now redirects to HTML.
    return (
        DashboardSpec(
            filename="prysm_beacon_node.json",
            url=(
                "https://raw.githubusercontent.com/OffchainLabs/prysm-documentation/"
                "c0d5c52d9a917f77d46c12ed8d98c2bee9fda078/"
                "static/assets/grafana-dashboards/beacon_node.json"
            ),
            title="Prysm Beacon Node",
        ),
    )


# Boards EthPillar used to download. The file provider loads every JSON in the
# directory, and with disableDeletion false it drops a dashboard only once that
# file is gone. prysm_small.json's URL now returns the offchain.io homepage;
# prysm_big.json is a stale 2020 community board.
_OBSOLETE_DASHBOARD_FILES = (
    "prysm_small.json",
    "prysm_big.json",
)


_EXECUTION_CLIENTS: Tuple[ClientScrape, ...] = (
    ClientScrape(
        key="nethermind",
        job_name="nethermind",
        default_port=6060,
        metrics_path="/metrics",
        instance="execution",
        needles=("nethermind",),
        dashboards=(
            DashboardSpec(
                filename="nethermind_dashboard.json",
                url="https://raw.githubusercontent.com/NethermindEth/metrics-infrastructure/master/grafana/provisioning/dashboards/nethermind.json",
                # Cluster board filters Instance/Network labels and a foreign datasource uid.
                strip_label_matchers=("Instance", "Network"),
                prometheus_datasource_uid="prometheus",
            ),
        ),
    ),
    ClientScrape(
        key="erigon",
        job_name="erigon",
        default_port=6060,
        metrics_path="/debug/metrics/prometheus",
        instance="execution",
        needles=("erigon",),
        dashboards=(
            DashboardSpec(
                filename="erigon_dashboard.json",
                url="https://raw.githubusercontent.com/ledgerwatch/erigon/devel/cmd/prometheus/dashboards/erigon.json",
                title="Erigon Dashboard",
                uid="YbLNLr6Mz",
                template_pins=(
                    TemplatePin(
                        name="instance",
                        current="execution",
                        query='label_values(go_goroutines{job="erigon"},instance)',
                        clear_regex=True,
                    ),
                ),
            ),
        ),
    ),
    ClientScrape(
        key="ethrex",
        job_name="ethrex",
        default_port=6060,
        metrics_path="/metrics",
        instance="execution",
        needles=("ethrex",),
        dashboards=(
            DashboardSpec(
                filename="ethrex_l1_perf.json",
                url="https://raw.githubusercontent.com/lambdaclass/ethrex/refs/heads/main/metrics/provisioning/grafana/dashboards/common_dashboards/ethrex_l1_perf.json",
                # Upstream job default is "ethrex L1", and the instance variable
                # is label_values(eth_con_spec_config_name), which an EL does not export.
                template_pins=(
                    TemplatePin(name="job", current="ethrex"),
                    TemplatePin(
                        name="instance",
                        current="execution",
                        query='label_values(up{job="ethrex"},instance)',
                        clear_regex=True,
                    ),
                ),
            ),
        ),
    ),
    ClientScrape(
        key="besu",
        job_name="besu",
        default_port=6060,
        metrics_path="/metrics",
        instance="execution",
        needles=("besu",),
        dashboards=(
            DashboardSpec(
                filename="besu_dashboard.json",
                grafana_id=10273,
                title="Besu Dashboard",
                # Variable "system" is the Prometheus instance label. Upstream current is empty.
                template_pins=(TemplatePin(name="system", current="execution"),),
            ),
        ),
    ),
    ClientScrape(
        key="reth",
        job_name="reth",
        default_port=6060,
        metrics_path="/metrics",
        instance="execution",
        needles=("reth",),
        dashboards=(
            DashboardSpec(
                filename="reth_dashboard.json",
                url="https://raw.githubusercontent.com/paradigmxyz/reth/main/etc/grafana/dashboards/overview.json",
                title="Reth Dashboard",
                # ${VAR_INSTANCE_LABEL} is the label *name* in {$instance_label="$instance"}.
                instance_label="instance",
                template_pins=(
                    TemplatePin(
                        name="instance",
                        current="execution",
                        query="label_values(reth_info,instance)",
                    ),
                ),
            ),
        ),
    ),
    ClientScrape(
        key="geth",
        job_name="geth",
        default_port=6060,
        metrics_path="/debug/metrics/prometheus",
        instance="execution",
        needles=("geth",),
        dashboards=(
            DashboardSpec(
                filename="geth_dashboard.json",
                url="https://gist.githubusercontent.com/karalabe/e7ca79abdec54755ceae09c08bd090cd/raw/3a400ab90f9402f2233280afd086cb9d6aac2111/dashboard.json",
                title="Geth Dashboard",
            ),
        ),
    ),
)

_CONSENSUS_CLIENTS: Tuple[ClientScrape, ...] = (
    ClientScrape(
        key="prysm-bn",
        # Official System and Replay panels filter job="beacon".
        job_name="beacon",
        default_port=8080,
        metrics_path="/metrics",
        instance="consensus",
        needles=("prysm-beacon-chain", "prysm"),
        dashboards=_prysm_dashboards(),
    ),
    ClientScrape(
        key="lighthouse-bn",
        job_name="lighthouse",
        default_port=8008,
        metrics_path="/metrics",
        instance="consensus",
        needles=("lighthouse",),
        dashboards=_lh_dashboards(),
    ),
    ClientScrape(
        key="lodestar-bn",
        job_name="lodestar",
        default_port=8008,
        metrics_path="/metrics",
        instance="consensus",
        needles=("lodestar",),
        dashboards=(
            DashboardSpec(
                filename="lodestar_summary.json",
                url="https://raw.githubusercontent.com/ChainSafe/lodestar/stable/dashboards/lodestar_summary.json",
                title="Lodestar Dashboard",
                # Constants default to ${VAR_BEACON_JOB}; queries are job=~"$beacon_job|beacon".
                template_pins=(
                    TemplatePin(name="beacon_job", current="lodestar", query="lodestar"),
                    TemplatePin(name="validator_job", current="lodestar_validator", query="lodestar_validator"),
                ),
            ),
        ),
    ),
    ClientScrape(
        key="nimbus-bn",
        job_name="nimbus",
        default_port=8008,
        metrics_path="/metrics",
        instance="consensus",
        needles=("nimbus",),
        extra_labels=(("container", "nimbus"),),
        dashboards=(
            DashboardSpec(
                filename="nimbus_dashboard.json",
                url="https://raw.githubusercontent.com/status-im/nimbus-eth2/master/grafana/beacon_nodes_Grafana_dashboard.json",
                title="Nimbus Dashboard",
                # Every panel filters container="${container}", which Docker adds and systemd does not.
                template_pins=(
                    TemplatePin(
                        name="instance",
                        current="consensus",
                        query='label_values(up{job="nimbus"},instance)',
                    ),
                    TemplatePin(
                        name="container",
                        current="nimbus",
                        query='label_values(up{job="nimbus"},container)',
                    ),
                ),
            ),
        ),
    ),
    ClientScrape(
        key="grandine",
        job_name="grandine",
        default_port=8008,
        metrics_path="/metrics",
        instance="consensus",
        needles=("grandine",),
        dashboards=(
            DashboardSpec(
                filename="grandine_dashboard.json",
                url="https://raw.githubusercontent.com/grandinetech/grandine/refs/heads/master/prometheus_metrics/dashboards/overview.json",
                title="Grandine Dashboard",
                # Upstream opens on instance 0.0.0.0:5061 (Grandine's old default port).
                template_pins=(TemplatePin(name="Instance", current="consensus"),),
            ),
        ),
    ),
    ClientScrape(
        key="teku-bn",
        job_name="teku",
        default_port=8008,
        metrics_path="/metrics",
        instance="consensus",
        needles=("teku",),
        dashboards=(DashboardSpec(filename="teku_overview.json", grafana_id=12199, title="Teku Overview"),),
    ),
)

_VALIDATOR_CLIENTS: Tuple[ClientScrape, ...] = (
    # Official dashboard is beacon-node only; there is no current VC board.
    ClientScrape(
        key="prysm-vc",
        job_name="validator",
        default_port=8081,
        metrics_path="/metrics",
        instance="validator",
        needles=("prysm-validator", "prysm"),
    ),
    ClientScrape(
        key="lighthouse-vc",
        job_name="lighthouse_validator",
        default_port=8009,
        metrics_path="/metrics",
        instance="validator",
        needles=("lighthouse",),
        dashboards=(_lh_vc_dashboard(),),
    ),
    ClientScrape(
        key="lodestar-vc",
        job_name="lodestar_validator",
        default_port=8009,
        metrics_path="/metrics",
        instance="validator",
        needles=("lodestar",),
        dashboards=(
            DashboardSpec(
                filename="lodestar_summary.json",
                url="https://raw.githubusercontent.com/ChainSafe/lodestar/stable/dashboards/lodestar_summary.json",
                title="Lodestar Dashboard",
                template_pins=(
                    TemplatePin(name="beacon_job", current="lodestar", query="lodestar"),
                    TemplatePin(name="validator_job", current="lodestar_validator", query="lodestar_validator"),
                ),
            ),
        ),
    ),
    ClientScrape(
        key="nimbus-vc",
        job_name="nimbus_validator",
        default_port=8009,
        metrics_path="/metrics",
        instance="validator",
        needles=("nimbus",),
    ),
    ClientScrape(
        key="teku-vc",
        job_name="teku_validator",
        default_port=8009,
        metrics_path="/metrics",
        instance="validator",
        needles=("teku",),
    ),
)


def scrape_job_yaml(client: ClientScrape, port: int) -> str:
    """Return one Prometheus scrape job for *client* on *port*.

    Args:
        client: Client scrape definition.
        port: Metrics port to target.

    Returns:
        YAML snippet including a trailing newline.
    """
    yaml = (
        f"   - job_name: '{client.job_name}'\n"
        f"     metrics_path: {client.metrics_path}\n"
        "     static_configs:\n"
        f"       - targets: ['localhost:{int(port)}']\n"
        "         labels:\n"
        f"           instance: {client.instance}\n"
    )
    for key, value in client.extra_labels:
        yaml += f"           {key}: {value}\n"
    return yaml


def metrics_port_from_unit(text: str, default: int) -> int:
    """Return the metrics port declared in a systemd unit, or *default*.

    Only the token immediately after a metrics flag is considered, so unrelated
    ``host:port`` arguments (Erigon ``--private.api.addr``, for example) are ignored.

    Args:
        text: Unit file contents.
        default: Port used when the unit does not set one.

    Returns:
        Parsed port, or *default*.
    """
    tokens = text.split()
    for index, token in enumerate(tokens):
        for flag in _PORT_FLAGS:
            if token == flag and index + 1 < len(tokens) and tokens[index + 1].isdigit():
                return int(tokens[index + 1])
            prefix = flag + "="
            if token.startswith(prefix) and token[len(prefix):].isdigit():
                return int(token[len(prefix):])
        if token == "--metrics" and index + 1 < len(tokens):
            nxt = tokens[index + 1]
            if ":" in nxt and not nxt.startswith("--"):
                port = nxt.rsplit(":", 1)[-1]
                if port.isdigit():
                    return int(port)
        if token.startswith("--metrics=") and ":" in token:
            port = token.rsplit(":", 1)[-1]
            if port.isdigit():
                return int(port)
    return default


def _match_client(text: str, catalog: Sequence[ClientScrape]) -> Optional[ClientScrape]:
    """Return the first catalog entry whose binary needle appears in *text*."""
    lowered = text.lower()
    for client in catalog:
        if any(needle.lower() in lowered for needle in client.needles):
            return client
    return None


def detect_clients(
    execution_text: Optional[str] = None,
    consensus_text: Optional[str] = None,
    validator_text: Optional[str] = None,
) -> List[Tuple[ClientScrape, int]]:
    """Detect installed EL/CL/VC clients from systemd unit contents.

    Args:
        execution_text: ``execution.service`` contents, if present.
        consensus_text: ``consensus.service`` contents, if present.
        validator_text: ``validator.service`` contents, if present.

    Returns:
        ``(client, port)`` pairs in EL, CL, VC order.
    """
    found: List[Tuple[ClientScrape, int]] = []
    pairs = (
        (execution_text, _EXECUTION_CLIENTS),
        (consensus_text, _CONSENSUS_CLIENTS),
        (validator_text, _VALIDATOR_CLIENTS),
    )
    for text, catalog in pairs:
        if not text:
            continue
        client = _match_client(text, catalog)
        if client is None:
            continue
        found.append((client, metrics_port_from_unit(text, client.default_port)))
    return found


def strip_managed_block(text: str) -> str:
    """Remove the EthPillar-managed client scrape block from prometheus.yml text."""
    return _BLOCK_RE.sub("", text)


def existing_job_names(text: str) -> set:
    """Return scrape ``job_name`` values present in *text*."""
    return set(_JOB_RE.findall(text))


def render_managed_block(jobs: Iterable[str]) -> str:
    """Wrap scrape job YAML snippets in the managed-block markers."""
    body = "".join(jobs)
    return _BLOCK_BEGIN + body + _BLOCK_END


def upsert_client_scrapes(text: str, clients: Sequence[Tuple[ClientScrape, int]]) -> Tuple[str, bool]:
    """Insert or replace the managed client scrape block.

    Jobs whose ``job_name`` already exists outside the managed block are left
    alone so a hand-edited scrape target is not duplicated.

    Args:
        text: Full prometheus.yml contents.
        clients: Detected ``(client, port)`` pairs.

    Returns:
        Updated text and whether it differs from *text*.
    """
    stripped = strip_managed_block(text)
    taken = existing_job_names(stripped)
    snippets: List[str] = []
    for client, port in clients:
        if client.job_name in taken:
            continue
        snippets.append(scrape_job_yaml(client, port))
        taken.add(client.job_name)
    block = render_managed_block(snippets)
    if not stripped.endswith("\n"):
        stripped += "\n"
    updated = stripped + block
    return updated, updated != text


def ensure_client_scrapes(
    prometheus_yml: Path,
    clients: Sequence[Tuple[ClientScrape, int]],
) -> bool:
    """Write the managed client scrape block into *prometheus_yml*.

    Args:
        prometheus_yml: Prometheus config path.
        clients: Detected ``(client, port)`` pairs.

    Returns:
        True if the file was modified. False if missing or unchanged.
    """
    if not prometheus_yml.is_file():
        return False
    text = read_privileged_text(prometheus_yml)
    if text is None:
        return False
    updated, changed = upsert_client_scrapes(text, clients)
    if not changed:
        return False
    write_privileged_file(prometheus_yml, updated)
    return True


def rewrite_dashboard(payload: Union[dict, list], spec: DashboardSpec) -> Union[dict, list]:
    """Rewrite datasource placeholders the way eth-docker's provisioner does.

    Args:
        payload: Parsed dashboard JSON.
        spec: Dashboard download spec (title, uid, instance label).

    Returns:
        Transformed JSON structure.
    """

    def walk(node: Union[dict, list, str, int, float, bool, None]) -> Union[dict, list, str, int, float, bool, None]:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value) for value in node]
        if isinstance(node, str):
            if node in _DS_TOKENS:
                return "Prometheus"
            if node == "${VAR_INSTANCE_LABEL}" and spec.instance_label:
                return spec.instance_label
        return node

    rewritten = walk(payload)
    if isinstance(rewritten, dict):
        if spec.title:
            # Grafana v2 resources store the title under spec.title, not at the root.
            api = str(rewritten.get("apiVersion") or "")
            inner = rewritten.get("spec")
            if api.startswith("dashboard.grafana.app") and isinstance(inner, dict):
                inner["title"] = spec.title
            else:
                rewritten["title"] = spec.title
        if spec.uid:
            rewritten["uid"] = spec.uid
        if spec.lodestar_instances:
            templating = rewritten.get("templating")
            items = templating.get("list") if isinstance(templating, dict) else None
            if isinstance(items, list):
                if len(items) > 3 and isinstance(items[3], dict):
                    items[3]["query"] = "consensus"
                if len(items) > 4 and isinstance(items[4], dict):
                    items[4]["query"] = "validator"
        _pin_template_variables(rewritten, spec.template_pins)
        if spec.strip_label_matchers:
            _strip_label_matchers(rewritten, spec.strip_label_matchers)
        if spec.prometheus_datasource_uid:
            _pin_prometheus_datasource_uid(rewritten, spec.prometheus_datasource_uid)
    return rewritten


_MATCHER_RE_CACHE: Dict[str, re.Pattern[str]] = {}


def _strip_label_matchers(node: Union[dict, list], names: Sequence[str]) -> None:
    """Remove ``Label=~"$var"`` matchers that stock clients do not export."""
    if isinstance(node, dict):
        expr = node.get("expr")
        if isinstance(expr, str):
            node["expr"] = _clean_expr_matchers(expr, names)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _strip_label_matchers(value, names)
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                _strip_label_matchers(value, names)


def _clean_expr_matchers(expr: str, names: Sequence[str]) -> str:
    """Drop named matchers and tidy empty selector lists."""
    for name in names:
        pattern = _MATCHER_RE_CACHE.get(name)
        if pattern is None:
            pattern = re.compile(rf',?\s*{re.escape(name)}=~"\$[A-Za-z0-9_]+"')
            _MATCHER_RE_CACHE[name] = pattern
        expr = pattern.sub("", expr)
    expr = expr.replace("{,", "{").replace(",}", "}").replace(",,", ",")
    return expr


def _pin_prometheus_datasource_uid(node: Union[dict, list], uid: str) -> None:
    """Point Grafana v2 Prometheus datasource variables at *uid*."""
    if isinstance(node, dict):
        current = node.get("current")
        if (
            node.get("pluginId") == "prometheus"
            and isinstance(current, dict)
            and current.get("value") not in (None, "", uid)
        ):
            current["value"] = uid
            current["text"] = "Prometheus"
        for value in node.values():
            if isinstance(value, (dict, list)):
                _pin_prometheus_datasource_uid(value, uid)
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                _pin_prometheus_datasource_uid(value, uid)


def _pin_template_variables(dashboard: dict, pins: Sequence[TemplatePin]) -> None:
    """Set template variable defaults so panels match EthPillar scrape labels."""
    if not pins:
        return
    templating = dashboard.get("templating")
    items = templating.get("list") if isinstance(templating, dict) else None
    if not isinstance(items, list):
        return
    by_name = {pin.name: pin for pin in pins}
    for item in items:
        if not isinstance(item, dict):
            continue
        pin = by_name.get(item.get("name"))
        if pin is None:
            continue
        if pin.current:
            selected = [pin.current] if item.get("multi") else pin.current
            item["current"] = {"text": selected, "value": selected, "selected": True}
        if pin.query:
            item["definition"] = pin.query
            query = item.get("query")
            if isinstance(query, dict):
                query["query"] = pin.query
            else:
                item["query"] = pin.query
        if pin.clear_regex:
            item["regex"] = ""


def grafana_download_url(dashboard_id: int) -> str:
    """Resolve the latest Grafana.com revision download URL for *dashboard_id*."""
    api = f"https://grafana.com/api/dashboards/{int(dashboard_id)}"
    req = urllib.request.Request(api, headers={"User-Agent": "ethpillar"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        meta = json.loads(resp.read().decode("utf-8"))
    revision = meta["revision"]
    return f"https://grafana.com/api/dashboards/{int(dashboard_id)}/revisions/{revision}/download"


def dashboards_for(clients: Sequence[Tuple[ClientScrape, int]]) -> List[DashboardSpec]:
    """Unique dashboard specs for the detected clients, first role wins."""
    seen = set()
    specs: List[DashboardSpec] = []
    for client, _port in clients:
        for spec in client.dashboards:
            if spec.filename in seen:
                continue
            seen.add(spec.filename)
            specs.append(spec)
    return specs


def remove_obsolete_dashboards(dashboards_dir: Path, keep: Iterable[str] = ()) -> Tuple[str, ...]:
    """Delete dashboard files EthPillar no longer provisions.

    Args:
        dashboards_dir: Grafana dashboard provisioning directory.
        keep: Filenames to leave in place (boards this run still installs).

    Returns:
        Filenames that were removed, in ``_OBSOLETE_DASHBOARD_FILES`` order.

    Raises:
        PermissionError: When a leftover file cannot be removed.
    """
    kept = set(keep)
    removed: List[str] = []
    for name in _OBSOLETE_DASHBOARD_FILES:
        if name in kept:
            continue
        if remove_privileged_file(dashboards_dir / name):
            removed.append(name)
    return tuple(removed)


def provision_client_dashboards(
    dashboards_dir: Path,
    clients: Sequence[Tuple[ClientScrape, int]],
) -> Tuple[int, Tuple[str, ...]]:
    """Download and rewrite client Grafana dashboards into *dashboards_dir*.

    Also deletes leftover Prysm boards (``prysm_small.json``, ``prysm_big.json``)
    so Grafana stops serving them. File provisioning polls the directory and
    drops a dashboard only after its JSON file is gone.

    Args:
        dashboards_dir: Grafana file-provisioning directory.
        clients: Detected ``(client, port)`` pairs.

    Returns:
        Count of dashboard files written, and obsolete filenames removed.

    Raises:
        PermissionError: When the provisioning directory is not writable.
    """
    specs = dashboards_for(clients)
    written = 0
    for spec in specs:
        url = spec.url or grafana_download_url(spec.grafana_id or 0)
        dest = dashboards_dir / spec.filename
        try:
            download_grafana_dashboard(dest, url)
            raw = read_privileged_text(dest)
            if not raw:
                continue
            payload = json.loads(raw)
            write_privileged_file(
                dest,
                json.dumps(rewrite_dashboard(payload, spec), indent=2) + "\n",
            )
            written += 1
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001 — one bad dashboard must not abort the rest
            print(f"Warning: client dashboard {spec.filename} failed: {exc}", file=sys.stderr)
    removed = remove_obsolete_dashboards(dashboards_dir, keep=(spec.filename for spec in specs))
    for name in removed:
        print(f"Removed obsolete Grafana dashboard {name}", file=sys.stderr)
    return written, removed


def _read_unit(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return read_privileged_text(path)


def provision_client_monitoring(
    prometheus_yml: Path = DEFAULT_PROMETHEUS_YML,
    grafana_dashboards: Path = DEFAULT_GRAFANA_DASHBOARDS,
    execution_unit: Path = DEFAULT_EXECUTION_UNIT,
    consensus_unit: Path = DEFAULT_CONSENSUS_UNIT,
    validator_unit: Path = DEFAULT_VALIDATOR_UNIT,
    restart: bool = False,
) -> Dict[str, object]:
    """Ensure client scrape jobs and dashboards for the installed systemd units.

    No-ops when Prometheus and Grafana paths are absent. Permission errors while
    writing Grafana files propagate.

    Args:
        prometheus_yml: Prometheus config path.
        grafana_dashboards: Grafana dashboard provisioning directory.
        execution_unit: Path to ``execution.service``.
        consensus_unit: Path to ``consensus.service``.
        validator_unit: Path to ``validator.service``.
        restart: Restart prometheus when scrapes change, and grafana when a
            dashboard is written or an obsolete dashboard file is removed.

    Returns:
        Dict with ``scrape`` (bool), ``dashboards`` (int written), ``removed``
        (obsolete dashboard filenames), and ``clients`` (list of job names).
    """
    clients = detect_clients(
        _read_unit(execution_unit),
        _read_unit(consensus_unit),
        _read_unit(validator_unit),
    )
    removed: Tuple[str, ...] = ()
    results: Dict[str, object] = {
        "scrape": False,
        "dashboards": 0,
        "removed": [],
        "clients": [client.job_name for client, _port in clients],
    }
    if prometheus_yml.is_file():
        results["scrape"] = ensure_client_scrapes(prometheus_yml, clients)
    if grafana_dashboards.is_dir() and clients:
        written, removed = provision_client_dashboards(grafana_dashboards, clients)
        results["dashboards"] = written
        results["removed"] = list(removed)

    if restart and results["scrape"]:
        subprocess.run(
            ["sudo", "systemctl", "try-restart", "prometheus"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if restart and (results["dashboards"] or removed):
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
        ``0`` on success.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    provision = sub.add_parser("provision", help="Scrape jobs + dashboards for installed clients")
    provision.add_argument("--prometheus-yml", type=Path, default=DEFAULT_PROMETHEUS_YML)
    provision.add_argument("--grafana-dashboards", type=Path, default=DEFAULT_GRAFANA_DASHBOARDS)
    provision.add_argument("--execution-unit", type=Path, default=DEFAULT_EXECUTION_UNIT)
    provision.add_argument("--consensus-unit", type=Path, default=DEFAULT_CONSENSUS_UNIT)
    provision.add_argument("--validator-unit", type=Path, default=DEFAULT_VALIDATOR_UNIT)
    provision.add_argument("--restart", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "provision":
        results = provision_client_monitoring(
            prometheus_yml=args.prometheus_yml,
            grafana_dashboards=args.grafana_dashboards,
            execution_unit=args.execution_unit,
            consensus_unit=args.consensus_unit,
            validator_unit=args.validator_unit,
            restart=args.restart,
        )
        names = ",".join(results["clients"]) or "none"
        removed = ",".join(results["removed"]) or "none"
        print(
            "client monitoring: "
            f"clients={names} "
            f"scrape_updated={results['scrape']} "
            f"dashboards_written={results['dashboards']} "
            f"dashboards_removed={removed}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
