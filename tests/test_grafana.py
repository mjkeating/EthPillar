"""Tests for manage.grafana ini/port helpers."""

from manage.grafana import (
    _grafana_ini_with_http_port,
    _parse_grafana_http_port_from_ini,
    ensure_prometheus_datasource_uid,
    read_grafana_http_port,
)


def test_grafana_ini_http_port_rewrite():
    content = "[server]\nhttp_port = 3000\ndomain = localhost\n"
    updated = _grafana_ini_with_http_port(content, 3701)
    assert "http_port = 3701" in updated
    assert "http_port = 3000" not in updated


def test_grafana_ini_http_port_rewrite_commented_default():
    content = "[server]\n;http_port = 3000\ndomain = localhost\n"
    updated = _grafana_ini_with_http_port(content, 3701)
    assert "http_port = 3701" in updated
    assert ";http_port = 3000" not in updated


def test_parse_grafana_http_port_from_ini():
    content = "[server]\n;http_port = 3000\nhttp_port = 3701\n"
    assert _parse_grafana_http_port_from_ini(content) == 3701
    assert read_grafana_http_port() == 3000  # no grafana.ini in test env


def test_ensure_prometheus_datasource_uid_inserts(tmp_path):
    ds = tmp_path / "datasources.yml"
    ds.write_text(
        "apiVersion: 1\n"
        "datasources:\n"
        "  - name: Prometheus\n"
        "    type: prometheus\n"
        "    url: http://localhost:9090\n"
        "    access: proxy\n"
        "    isDefault: true\n",
        encoding="utf-8",
    )
    assert ensure_prometheus_datasource_uid(ds) is True
    text = ds.read_text(encoding="utf-8")
    assert "uid: prometheus" in text
    assert "deleteDatasources:" in text
    assert ensure_prometheus_datasource_uid(ds) is False


def test_ensure_prometheus_datasource_uid_creates_when_parent_exists(tmp_path):
    parent = tmp_path / "provisioning" / "datasources"
    parent.mkdir(parents=True)
    ds = parent / "datasources.yml"
    assert ensure_prometheus_datasource_uid(ds) is True
    assert ds.is_file()
    text = ds.read_text(encoding="utf-8")
    assert "uid: prometheus" in text
    assert "deleteDatasources:" in text


def test_ensure_prometheus_datasource_uid_noop_without_parent(tmp_path):
    ds = tmp_path / "missing" / "datasources.yml"
    assert ensure_prometheus_datasource_uid(ds) is False
    assert not ds.exists()
