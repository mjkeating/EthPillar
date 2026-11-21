"""Tests for manage.client_monitoring scrape jobs and dashboard rewrites."""

import json
from pathlib import Path

from manage.client_monitoring import (
    DashboardSpec,
    TemplatePin,
    detect_clients,
    ensure_client_scrapes,
    metrics_port_from_unit,
    provision_client_dashboards,
    remove_obsolete_dashboards,
    rewrite_dashboard,
    scrape_job_yaml,
    upsert_client_scrapes,
)

_BASE_YML = """\
rule_files:
  - alert.rules.yml

global:
  scrape_interval:     15s

scrape_configs:
   - job_name: 'ethereum-metrics-exporter'
     static_configs:
       - targets: ['localhost:9099']
   - job_name: 'node_exporter'
     static_configs:
       - targets: ['localhost:9100']
"""

_GETH_UNIT = """\
[Service]
ExecStart=/usr/local/bin/geth --mainnet --metrics --http --pprof
"""

_LIGHTHOUSE_BN = """\
[Service]
ExecStart=/usr/local/bin/lighthouse bn --metrics --metrics-address=127.0.0.1 --metrics-port=8008
"""

_LIGHTHOUSE_VC = """\
[Service]
ExecStart=/usr/local/bin/lighthouse vc --metrics --metrics-port=8009
"""

_RETH_UNIT = """\
[Service]
ExecStart=/usr/local/bin/reth node --metrics 127.0.0.1:6060
"""

_ERIGON_UNIT = """\
[Service]
ExecStart=/usr/local/bin/erigon --metrics --private.api.addr=127.0.0.1:9091
"""

_NETHERMIND_UNIT = """\
[Service]
ExecStart=/usr/local/bin/nethermind/nethermind --Metrics.Enabled true --Metrics.ExposePort 6060
"""


def test_detect_geth_lighthouse_ports():
    found = detect_clients(_GETH_UNIT, _LIGHTHOUSE_BN, _LIGHTHOUSE_VC)
    jobs = [(client.job_name, port, client.metrics_path, client.instance) for client, port in found]
    assert jobs == [
        ("geth", 6060, "/debug/metrics/prometheus", "execution"),
        ("lighthouse", 8008, "/metrics", "consensus"),
        ("lighthouse_validator", 8009, "/metrics", "validator"),
    ]


def test_reth_and_nethermind_ports_do_not_cross_match():
    reth = detect_clients(_RETH_UNIT, None, None)
    assert reth[0][0].job_name == "reth"
    assert reth[0][1] == 6060
    nethermind = detect_clients(_NETHERMIND_UNIT, None, None)
    assert nethermind[0][0].job_name == "nethermind"
    assert nethermind[0][0].metrics_path == "/metrics"
    assert metrics_port_from_unit(_ERIGON_UNIT, 6060) == 6060


def test_upsert_appends_once_and_replaces_managed_block():
    clients = detect_clients(_GETH_UNIT, _LIGHTHOUSE_BN, None)
    updated, changed = upsert_client_scrapes(_BASE_YML, clients)
    assert changed is True
    assert "job_name: 'geth'" in updated
    assert "metrics_path: /debug/metrics/prometheus" in updated
    assert "instance: execution" in updated
    assert "job_name: 'lighthouse'" in updated
    assert "instance: consensus" in updated
    assert updated.count("job_name: 'geth'") == 1

    again, changed_again = upsert_client_scrapes(updated, clients)
    assert changed_again is False
    assert again.count("BEGIN ethpillar client scrape jobs") == 1

    geth_only = detect_clients(_GETH_UNIT, None, None)
    replaced, changed_replaced = upsert_client_scrapes(updated, geth_only)
    assert changed_replaced is True
    assert "job_name: 'lighthouse'" not in replaced
    assert "job_name: 'ethereum-metrics-exporter'" in replaced


def test_upsert_keeps_hand_edited_job_outside_block():
    custom = _BASE_YML + "   - job_name: 'geth'\n     static_configs:\n       - targets: ['localhost:6061']\n"
    clients = detect_clients(_GETH_UNIT, None, None)
    updated, _changed = upsert_client_scrapes(custom, clients)
    assert updated.count("job_name: 'geth'") == 1
    assert "localhost:6061" in updated
    assert "BEGIN ethpillar client scrape jobs" in updated


def test_ensure_client_scrapes_writes_file(tmp_path: Path):
    yml = tmp_path / "prometheus.yml"
    yml.write_text(_BASE_YML, encoding="utf-8")
    clients = detect_clients(_GETH_UNIT, None, None)
    assert ensure_client_scrapes(yml, clients) is True
    assert ensure_client_scrapes(yml, clients) is False
    assert "localhost:6060" in yml.read_text(encoding="utf-8")


def test_rewrite_dashboard_datasource_and_lodestar():
    spec = DashboardSpec(
        filename="lodestar_summary.json",
        title="Lodestar Dashboard",
        lodestar_instances=True,
        instance_label="execution",
    )
    payload = {
        "title": "old",
        "templating": {"list": [{}, {}, {}, {"query": "job"}, {"query": "job"}]},
        "panels": [{"datasource": "${DS_PROMETHEUS}", "expr": "${VAR_INSTANCE_LABEL}"}],
    }
    rewritten = rewrite_dashboard(payload, spec)
    assert rewritten["title"] == "Lodestar Dashboard"
    assert rewritten["panels"][0]["datasource"] == "Prometheus"
    assert rewritten["panels"][0]["expr"] == "execution"
    assert rewritten["templating"]["list"][3]["query"] == "consensus"
    assert rewritten["templating"]["list"][4]["query"] == "validator"


def test_rewrite_pins_ethrex_job_and_grandine_instance():
    ethrex = rewrite_dashboard(
        {
            "templating": {
                "list": [
                    {
                        "name": "job",
                        "current": {"text": "ethrex L1", "value": "ethrex L1"},
                        "query": {"query": "label_values(up, job)"},
                    },
                    {
                        "multi": True,
                        "name": "instance",
                        "regex": "/^(?<text>[^:]+)/",
                        "definition": "label_values(eth_con_spec_config_name,instance)",
                        "query": {"query": "label_values(eth_con_spec_config_name,instance)"},
                    },
                ]
            }
        },
        DashboardSpec(
            filename="ethrex_l1_perf.json",
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
    )
    job, instance = ethrex["templating"]["list"]
    assert job["current"]["value"] == "ethrex"
    assert instance["current"]["value"] == ["execution"]
    assert instance["query"]["query"] == 'label_values(up{job="ethrex"},instance)'
    assert instance["regex"] == ""

    grandine = rewrite_dashboard(
        {
            "templating": {
                "list": [
                    {
                        "multi": True,
                        "name": "Instance",
                        "current": {"text": ["0.0.0.0:5061"], "value": ["0.0.0.0:5061"]},
                    }
                ]
            }
        },
        DashboardSpec(
            filename="grandine_dashboard.json",
            template_pins=(TemplatePin(name="Instance", current="consensus"),),
        ),
    )
    assert grandine["templating"]["list"][0]["current"]["value"] == ["consensus"]


def test_prysm_jobs_match_upstream_dashboards():
    beacon = detect_clients(None, "ExecStart=/usr/local/bin/prysm-beacon-chain --monitoring-port=8080\n", None)
    validator = detect_clients(None, None, "ExecStart=/usr/local/bin/prysm-validator --monitoring-port=8081\n")
    assert beacon[0][0].job_name == "beacon"
    assert beacon[0][1] == 8080
    board = beacon[0][0].dashboards[0]
    assert board.filename == "prysm_beacon_node.json"
    assert board.title == "Prysm Beacon Node"
    assert "OffchainLabs/prysm-documentation/c0d5c52d9a917f77d46c12ed8d98c2bee9fda078/" in board.url
    assert board.url.endswith("static/assets/grafana-dashboards/beacon_node.json")
    assert validator[0][0].job_name == "validator"
    assert validator[0][1] == 8081
    assert validator[0][0].dashboards == ()

    old = _BASE_YML + (
        "# BEGIN ethpillar client scrape jobs\n"
        "   - job_name: 'beacon node'\n"
        "     metrics_path: /metrics\n"
        "     static_configs:\n"
        "       - targets: ['localhost:8080']\n"
        "         labels:\n"
        "           instance: consensus\n"
        "# END ethpillar client scrape jobs\n"
    )
    updated, changed = upsert_client_scrapes(old, beacon)
    assert changed is True
    assert "job_name: 'beacon node'" not in updated
    assert "job_name: 'beacon'" in updated
    assert "localhost:8080" in updated


def test_nimbus_scrape_adds_container_label():
    found = detect_clients(None, "ExecStart=/usr/local/bin/nimbus_beacon_node --metrics-port=8008\n", None)
    yaml = scrape_job_yaml(found[0][0], found[0][1])
    assert "job_name: 'nimbus'" in yaml
    assert "container: nimbus" in yaml
    assert "instance: consensus" in yaml


def test_rewrite_strips_nethermind_cluster_matchers():
    rewritten = rewrite_dashboard(
        {
            "expr": 'nethermind_sync{Instance=~"$enode",Network=~"$network"}',
            "spec": {
                "variables": [
                    {
                        "pluginId": "prometheus",
                        "current": {"text": "Prometheus", "value": "prometheus_ds"},
                    }
                ]
            },
        },
        DashboardSpec(
            filename="nethermind_dashboard.json",
            strip_label_matchers=("Instance", "Network"),
            prometheus_datasource_uid="prometheus",
        ),
    )
    assert rewritten["expr"] == "nethermind_sync{}"
    assert rewritten["spec"]["variables"][0]["current"]["value"] == "prometheus"


def test_rewrite_dashboard_v2_sets_spec_title():
    rewritten = rewrite_dashboard(
        {
            "apiVersion": "dashboard.grafana.app/v2",
            "kind": "Dashboard",
            "metadata": {"name": "prysm-beacon-node"},
            "spec": {"title": "Beacon node"},
        },
        DashboardSpec(filename="prysm_beacon_node.json", title="Prysm Beacon Node"),
    )
    assert rewritten["spec"]["title"] == "Prysm Beacon Node"
    assert "title" not in rewritten


def test_rewrite_dashboard_classic_title_leaves_spec_alone():
    rewritten = rewrite_dashboard(
        {"title": "old", "spec": {"title": "inner"}},
        DashboardSpec(filename="geth_dashboard.json", title="Geth Dashboard"),
    )
    assert rewritten["title"] == "Geth Dashboard"
    assert rewritten["spec"]["title"] == "inner"


def test_remove_obsolete_prysm_dashboards(tmp_path: Path):
    (tmp_path / "prysm_small.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "prysm_big.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "geth_dashboard.json").write_text("{}\n", encoding="utf-8")
    assert remove_obsolete_dashboards(tmp_path, keep=("prysm_small.json",)) == ("prysm_big.json",)
    assert (tmp_path / "prysm_small.json").is_file()
    assert not (tmp_path / "prysm_big.json").exists()
    assert remove_obsolete_dashboards(tmp_path) == ("prysm_small.json",)
    assert not (tmp_path / "prysm_small.json").exists()
    assert (tmp_path / "geth_dashboard.json").is_file()
    assert remove_obsolete_dashboards(tmp_path) == ()


def test_provision_prysm_dashboard_replaces_obsolete_files(tmp_path: Path, monkeypatch):
    dash = tmp_path / "dashboards"
    dash.mkdir()
    (dash / "prysm_small.json").write_text("{}\n", encoding="utf-8")
    (dash / "prysm_big.json").write_text("{}\n", encoding="utf-8")
    (dash / "geth_dashboard.json").write_text("{}\n", encoding="utf-8")
    seen = {}

    def fake_download(dest: Path, url: str) -> None:
        seen["url"] = url
        dest.write_text(
            json.dumps(
                {
                    "apiVersion": "dashboard.grafana.app/v2",
                    "kind": "Dashboard",
                    "metadata": {"name": "prysm-beacon-node"},
                    "spec": {"title": "Beacon node"},
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr("manage.client_monitoring.download_grafana_dashboard", fake_download)
    clients = detect_clients(None, "ExecStart=/usr/local/bin/prysm-beacon-chain --monitoring-port=8080\n", None)
    written, removed = provision_client_dashboards(dash, clients)
    assert written == 1
    assert removed == ("prysm_small.json", "prysm_big.json")
    assert seen["url"].endswith("static/assets/grafana-dashboards/beacon_node.json")
    payload = json.loads((dash / "prysm_beacon_node.json").read_text(encoding="utf-8"))
    assert payload["spec"]["title"] == "Prysm Beacon Node"
    assert "title" not in payload
    assert not (dash / "prysm_small.json").exists()
    assert not (dash / "prysm_big.json").exists()
    assert (dash / "geth_dashboard.json").is_file()


def test_provision_prysm_validator_only_removes_obsolete_dashboards(tmp_path: Path, monkeypatch):
    dash = tmp_path / "dashboards"
    dash.mkdir()
    (dash / "prysm_small.json").write_text("{}\n", encoding="utf-8")
    (dash / "prysm_big.json").write_text("{}\n", encoding="utf-8")

    def fail_download(dest: Path, url: str) -> None:
        raise AssertionError(f"validator client should not download a dashboard ({url})")

    monkeypatch.setattr("manage.client_monitoring.download_grafana_dashboard", fail_download)
    clients = detect_clients(None, None, "ExecStart=/usr/local/bin/prysm-validator --monitoring-port=8081\n")
    written, removed = provision_client_dashboards(dash, clients)
    assert written == 0
    assert removed == ("prysm_small.json", "prysm_big.json")
    assert list(dash.iterdir()) == []
