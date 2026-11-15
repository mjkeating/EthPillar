"""Unit tests for deploy.systemd_units retirement / restore helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deploy.systemd_units import (
    next_bak_path,
    resolve_unit_path,
    restore_retired_unit,
    retire_systemd_unit,
    unit_basename,
    unit_known_to_systemd,
    unit_stem,
    verify_unit_absent,
)


def test_unit_stem_and_basename():
    assert unit_stem("/etc/systemd/system/ssvnode.service") == "ssvnode"
    assert unit_stem("ssv-node") == "ssv-node"
    assert unit_basename("ssvnode") == "ssvnode.service"
    assert unit_basename("ssvnode.service") == "ssvnode.service"
    assert resolve_unit_path("ssvnode", unit_dir="/tmp/u") == "/tmp/u/ssvnode.service"


def test_next_bak_path_rotates(tmp_path: Path):
    unit = tmp_path / "ssvnode.service"
    unit.write_text("x", encoding="utf-8")
    assert next_bak_path(str(unit)) == f"{unit}.bak"
    (tmp_path / "ssvnode.service.bak").write_text("old", encoding="utf-8")
    assert next_bak_path(str(unit)) == f"{unit}.bak.1"
    (tmp_path / "ssvnode.service.bak.1").write_text("old1", encoding="utf-8")
    assert next_bak_path(str(unit)) == f"{unit}.bak.2"


@patch("deploy.systemd_units.subprocess.run")
def test_unit_known_to_systemd_cat_active_list(mock_run):
    # cat succeeds → known
    mock_run.return_value = MagicMock(returncode=0, stdout="[Service]\n")
    assert unit_known_to_systemd("ssvnode.service") is True

    # cat fails, is-active succeeds → known
    def _side_effect(cmd, **kwargs):
        if cmd[:2] == ["systemctl", "cat"]:
            return MagicMock(returncode=1, stdout="")
        if cmd[:3] == ["systemctl", "is-active", "--quiet"]:
            return MagicMock(returncode=0)
        return MagicMock(returncode=1, stdout="")

    mock_run.side_effect = _side_effect
    assert unit_known_to_systemd("ssvnode.service") is True

    # all clear → unknown
    mock_run.side_effect = None
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    assert unit_known_to_systemd("ssvnode.service") is False


@patch("deploy.systemd_units.unit_known_to_systemd", return_value=True)
def test_verify_unit_absent_raises(_mock_known):
    with pytest.raises(RuntimeError, match="verification failed"):
        verify_unit_absent("ssvnode.service")


@patch("deploy.systemd_units.verify_unit_absent")
@patch("deploy.systemd_units.subprocess.run", return_value=MagicMock(returncode=0))
def test_retire_systemd_unit_stop_disable_bak_reload_verify(
    mock_run, mock_verify, tmp_path: Path
):
    unit = tmp_path / "ssvnode.service"
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")

    def _run(cmd, **kwargs):
        # Emulate sudo mv / systemctl
        if len(cmd) >= 3 and cmd[1] == "mv":
            Path(cmd[3]).write_text(Path(cmd[2]).read_text(encoding="utf-8"), encoding="utf-8")
            Path(cmd[2]).unlink()
        return MagicMock(returncode=0)

    mock_run.side_effect = _run
    bak = retire_systemd_unit(str(unit), verify=True, sudo=True)
    assert bak == f"{unit}.bak"
    assert not unit.exists()
    assert Path(bak).exists()

    cmds = [list(c.args[0]) for c in mock_run.call_args_list]
    assert ["sudo", "systemctl", "stop", "ssvnode"] in cmds
    assert ["sudo", "systemctl", "disable", "ssvnode"] in cmds
    assert ["sudo", "systemctl", "daemon-reload"] in cmds
    mock_verify.assert_called_once_with("ssvnode.service")


@patch("deploy.systemd_units.verify_unit_absent")
@patch("deploy.systemd_units.subprocess.run", return_value=MagicMock(returncode=0))
def test_retire_rotates_existing_bak(mock_run, mock_verify, tmp_path: Path):
    unit = tmp_path / "ssvnode.service"
    unit.write_text("new", encoding="utf-8")
    plain_bak = tmp_path / "ssvnode.service.bak"
    plain_bak.write_text("old", encoding="utf-8")

    def _run(cmd, **kwargs):
        if len(cmd) >= 3 and cmd[1] == "mv":
            Path(cmd[3]).write_text(Path(cmd[2]).read_text(encoding="utf-8"), encoding="utf-8")
            Path(cmd[2]).unlink()
        return MagicMock(returncode=0)

    mock_run.side_effect = _run
    bak = retire_systemd_unit(str(unit), verify=True)
    assert bak == f"{unit}.bak"
    assert (tmp_path / "ssvnode.service.bak.1").exists()
    assert Path(bak).read_text(encoding="utf-8") == "new"


@patch("deploy.systemd_units.unit_known_to_systemd", return_value=True)
@patch("deploy.systemd_units.subprocess.run", return_value=MagicMock(returncode=0))
def test_retire_aborts_when_verify_fails(mock_run, _mock_known, tmp_path: Path):
    unit = tmp_path / "ssvnode.service"
    unit.write_text("x", encoding="utf-8")

    def _run(cmd, **kwargs):
        if len(cmd) >= 3 and cmd[1] == "mv":
            Path(cmd[3]).write_text("x", encoding="utf-8")
            Path(cmd[2]).unlink(missing_ok=True)
        return MagicMock(returncode=0)

    mock_run.side_effect = _run
    with pytest.raises(RuntimeError, match="verification failed"):
        retire_systemd_unit(str(unit), verify=True)


@patch("deploy.systemd_units.subprocess.run", return_value=MagicMock(returncode=0))
def test_restore_retired_unit(mock_run, tmp_path: Path):
    bak = tmp_path / "ssvnode.service.bak"
    bak.write_text("[Service]\n", encoding="utf-8")

    def _run(cmd, **kwargs):
        if len(cmd) >= 3 and cmd[1] == "mv":
            Path(cmd[3]).write_text(Path(cmd[2]).read_text(encoding="utf-8"), encoding="utf-8")
            Path(cmd[2]).unlink()
        return MagicMock(returncode=0)

    mock_run.side_effect = _run
    restored = restore_retired_unit(str(bak), enable=True)
    assert restored.endswith("ssvnode.service")
    assert Path(restored).exists()
    cmds = [list(c.args[0]) for c in mock_run.call_args_list]
    assert any(c[:3] == ["sudo", "systemctl", "enable"] for c in cmds)


def test_retire_dry_run(tmp_path: Path):
    unit = tmp_path / "ssvnode.service"
    unit.write_text("x", encoding="utf-8")
    bak = retire_systemd_unit(str(unit), dry_run=True)
    assert bak.endswith(".service.bak")
    assert unit.exists()


def test_ssv_migrate_quarantine_delegates_to_shared_helper(tmp_path: Path):
    """Call-site: migrate quarantine_unit is a thin wrapper around retire."""
    from deploy import ssv_migrate

    unit = tmp_path / "ssvnode.service"
    unit.write_text("x", encoding="utf-8")
    with patch(
        "deploy.ssv_migrate.retire_systemd_unit", return_value=f"{unit}.bak"
    ) as mock_retire:
        out = ssv_migrate.quarantine_unit(str(unit), dry_run=False, verify=True)
    assert out == f"{unit}.bak"
    mock_retire.assert_called_once_with(str(unit), dry_run=False, verify=True)
