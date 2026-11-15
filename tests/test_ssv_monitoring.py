"""Tests for manage.ssv_monitoring Prometheus scrape + dashboard helpers."""

from pathlib import Path
from unittest.mock import patch

from manage.ssv_monitoring import (
    SSV_SCRAPE_JOB,
    ensure_ssv_scrape,
    has_ssv_scrape,
    provision_ssv_dashboard,
    provision_ssv_monitoring,
    ssv_scrape_job,
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
"""


def test_has_ssv_scrape_detects_job():
    assert has_ssv_scrape(_BASE_YML) is False
    assert has_ssv_scrape(_BASE_YML + SSV_SCRAPE_JOB) is True
    assert has_ssv_scrape('job_name: "ssv"') is True


def test_ssv_scrape_job_includes_pod_relabel():
    job = ssv_scrape_job(15000)
    assert "job_name: 'ssv'" in job
    assert "127.0.0.1:15000" in job
    assert "target_label: pod" in job
    assert "replacement: ssv-node" in job
    assert "metrics_path: /metrics" in job


def test_ensure_ssv_scrape_appends_once(tmp_path: Path):
    yml = tmp_path / "prometheus.yml"
    yml.write_text(_BASE_YML, encoding="utf-8")

    assert ensure_ssv_scrape(yml, metrics_port=15000) is True
    text = yml.read_text(encoding="utf-8")
    assert "job_name: 'ssv'" in text
    assert "127.0.0.1:15000" in text
    assert "target_label: pod" in text

    assert ensure_ssv_scrape(yml, metrics_port=15000) is False
    assert yml.read_text(encoding="utf-8").count("job_name: 'ssv'") == 1


def test_ensure_ssv_scrape_updates_port(tmp_path: Path):
    yml = tmp_path / "prometheus.yml"
    yml.write_text(_BASE_YML + ssv_scrape_job(15000), encoding="utf-8")
    assert ensure_ssv_scrape(yml, metrics_port=15001) is True
    assert "127.0.0.1:15001" in yml.read_text(encoding="utf-8")


def test_ensure_ssv_scrape_missing_file(tmp_path: Path):
    assert ensure_ssv_scrape(tmp_path / "missing.yml") is False


def test_provision_ssv_monitoring_noop_without_prometheus(tmp_path: Path):
    # Use paths whose parents do not exist so Grafana provisioning is skipped.
    missing = tmp_path / "absent" / "missing.yml"
    results = provision_ssv_monitoring(
        prometheus_yml=missing,
        grafana_dashboards=tmp_path / "absent" / "dash",
        datasources_yml=tmp_path / "absent" / "ds.yml",
    )
    assert results == {"scrape": False, "dashboard": False, "datasource": False}


def test_provision_dashboard_writes_file(tmp_path: Path):
    import json

    dash_dir = tmp_path / "dashboards"
    payload = (
        b'{"uid":"ssv","title":"SSV Operational","templating":{"list":['
        b'{"name":"pod","query":"label_values(otel_scope_info,pod)"}]}}'
    )

    def _fake_download(dest: Path, url: str = "") -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)

    with patch("manage.ssv_monitoring.download_grafana_dashboard", side_effect=_fake_download):
        assert provision_ssv_dashboard(dash_dir) is True
        data = json.loads((dash_dir / "ssv_operational.json").read_text(encoding="utf-8"))
        query = data["templating"]["list"][0]["query"]
        if isinstance(query, dict):
            query = query["query"]
        assert query == 'label_values(up{job="ssv"}, pod)'
        assert "otel_scope_info" not in (dash_dir / "ssv_operational.json").read_text(
            encoding="utf-8"
        )


def test_rewrite_ssv_dashboard_pod_variable_dict_query():
    from manage.ssv_monitoring import rewrite_ssv_dashboard_pod_variable

    dash = {
        "templating": {
            "list": [
                {
                    "name": "pod",
                    "query": {
                        "query": 'label_values(otel_scope_info{otel_scope_name=~"github.com/ssvlabs/.*"},pod)',
                        "refId": "PrometheusVariableQueryEditor-VariableQuery",
                    },
                }
            ]
        }
    }
    out = rewrite_ssv_dashboard_pod_variable(dash)
    assert out["templating"]["list"][0]["query"]["query"] == (
        'label_values(up{job="ssv"}, pod)'
    )
