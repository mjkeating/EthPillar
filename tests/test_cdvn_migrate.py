"""Tests for deploy/cdvn_migrate planning and deploy argv."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from deploy.cdvn_migrate import (
    DATADIR_MOVES,
    VC_PROFILE_MAP,
    _fee_recipient_from_cdvn,
    _fee_recipient_from_cluster_lock,
    _read_charon_file_text,
    _resolve_fee_recipient,
    detect_docker_compose_status,
    detect_ethpillar_vc_name,
    grafana_port_from_env,
    plan_cdvn_migration,
)

_DEFAULT_TEST_FEE = "0x388C818CA8B9251b393131C08a736A67ccB19297"


def _write_cdvn(tmp_path: Path, env: str, *, with_lock: bool = True, data_dirs: list | None = None) -> Path:
    root = tmp_path / "cdvn"
    root.mkdir()
    (root / ".env").write_text(env, encoding="utf-8")
    charon = root / ".charon"
    charon.mkdir()
    if with_lock:
        lock = {
            "cluster_definition": {
                "validators": [{"fee_recipient_address": _DEFAULT_TEST_FEE}]
            },
            "distributed_validators": [],
        }
        (charon / "cluster-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        keys = charon / "validator_keys"
        keys.mkdir()
        (keys / "keystore-0.json").write_text("{}", encoding="utf-8")
    for rel in data_dirs or []:
        d = root / Path(rel)
        d.mkdir(parents=True, exist_ok=True)
        (d / "marker").write_text("x", encoding="utf-8")
    return root


def _plan(root: Path, tmp_path: Path, **kwargs):
    """Plan against an isolated EthPillar datadir root (no host /var/lib)."""
    return plan_cdvn_migration(
        str(root),
        base_data_dir=str(tmp_path / "ethpillar-varlib"),
        **kwargs,
    )


def test_plan_lodestar_datadir_merges_state(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    base = root / "data" / "lodestar"
    (base / "keystores").mkdir(parents=True)
    (base / "keystores" / "keystore-0.json").write_text("{}", encoding="utf-8")
    (base / "validator-db").mkdir()
    (base / "validator-db" / "db").write_text("x", encoding="utf-8")
    (base / "validator-2026-08-28.log").write_text("log", encoding="utf-8")

    plan = _plan(root, tmp_path)
    move = next(m for m in plan.datadir_moves if m.relative_src == "data/lodestar")
    assert move.will_move
    assert "auto-sync to Lodestar" in plan.summary()


def test_plan_vc_teku_logs_only_skips_datadir_move(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=rp-external\n"
        "CL=rp-external\n"
        "VC=vc-teku\n"
        "MEV=rp-external\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://100.116.116.75:5052\n",
    )
    logs = root / "data" / "vc-teku" / "logs"
    logs.mkdir(parents=True)
    (logs / "teku.log").write_text("x", encoding="utf-8")

    plan = _plan(root, tmp_path)
    vc_move = next(m for m in plan.datadir_moves if m.relative_src == "data/vc-teku")
    assert not vc_move.will_move
    assert "only logs present" in vc_move.skip_reason
    assert plan.has_keyshares is True
    assert "auto-sync to Teku" in plan.summary()


def test_plan_symlink_charon(tmp_path: Path):
    real = tmp_path / "node2"
    real.mkdir()
    fee = _DEFAULT_TEST_FEE
    (real / "cluster-lock.json").write_text(
        json.dumps(
            {
                "cluster_definition": {
                    "validators": [{"fee_recipient_address": fee}],
                },
                "distributed_validators": [],
            }
        ),
        encoding="utf-8",
    )
    keys = real / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")

    root = tmp_path / "cdvn"
    root.mkdir()
    (root / ".env").write_text(
        "NETWORK=mainnet\n"
        "EL=rp-external\n"
        "CL=rp-external\n"
        "VC=vc-teku\n"
        "MEV=rp-external\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://100.116.116.75:5052\n",
        encoding="utf-8",
    )
    (root / ".charon").symlink_to(real, target_is_directory=True)

    plan = _plan(root, tmp_path)
    assert plan.charon_is_symlink is True
    assert plan.charon_dir == str(real.resolve())
    summary = plan.summary()
    assert "Charon cluster overlay" in summary
    assert "COPY" in summary
    assert str(real.resolve()) in summary


def test_plan_validator_only_external_bn(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n"
        "BUILDER_API_ENABLED=true\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://192.168.1.50:5052\n",
    )
    plan = _plan(root, tmp_path)
    assert plan.role == "Validator Client Only"
    assert plan.network == "mainnet"
    assert plan.ec_name is None
    assert plan.cc_name is None
    assert plan.vc_name == "Lodestar"
    assert plan.with_charon is True
    assert plan.with_mevboost is False
    assert plan.with_builder_api is True
    assert plan.bn_address == "http://192.168.1.50:5052"
    argv = plan.deploy_argv()
    assert "--skip_prompts" in argv
    assert argv[argv.index("--skip_prompts") + 1] == "true"
    assert "--install_config" in argv
    assert "Validator Client Only" in argv
    assert "--with_charon" in argv
    assert "--with_builder_api" in argv
    assert "--with_mevboost" not in argv
    assert "--vc_only_bn_address" in argv
    assert "http://192.168.1.50:5052" in argv


def test_fee_recipient_from_cluster_lock(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    fee = "0x388C818CA8B9251b393131C08a736A67ccB19297"
    lock = {
        "cluster_definition": {
            "validators": [{"fee_recipient_address": fee, "withdrawal_address": fee}]
        },
        "distributed_validators": [],
    }
    (root / ".charon" / "cluster-lock.json").write_text(
        __import__("json").dumps(lock),
        encoding="utf-8",
    )
    plan = _plan(root, tmp_path)
    assert plan.fee_recipient.lower() == fee.lower()
    argv = plan.deploy_argv()
    assert argv[argv.index("--fee_address") + 1].lower() == fee.lower()


def test_fee_recipient_ignores_withdrawal_credentials(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
        with_lock=False,
    )
    charon = root / ".charon"
    charon.mkdir(exist_ok=True)
    fee = "0x388C818CA8B9251b393131C08a736A67ccB19297"
    creds = "0x01" + "0" * 22 + fee[2:].lower()
    (charon / "deposit-data.json").write_text(
        f'[{{"withdrawal_credentials": "{creds}", "withdrawal_address": "{fee}"}}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Fee recipient address is required"):
        _plan(root, tmp_path)


def test_fee_recipient_from_root_owned_cluster_lock(tmp_path: Path, monkeypatch):
    root = tmp_path / "cdvn"
    root.mkdir()
    (root / ".env").write_text(
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-nimbus\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
        encoding="utf-8",
    )
    charon = root / ".charon"
    charon.mkdir()
    keys = charon / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")
    fee = "0x1234567890123456789012345678901234567890"
    lock_text = json.dumps(
        {
            "cluster_definition": {
                "validators": [{"fee_recipient_address": fee}],
            },
            "distributed_validators": [],
        }
    )
    lock_path = charon / "cluster-lock.json"
    lock_path.write_text(lock_text, encoding="utf-8")
    lock_path.chmod(0o600)

    real_access = os.access

    def _deny_read(path, mode):
        if os.path.abspath(str(path)) == os.path.abspath(str(lock_path)) and mode == os.R_OK:
            return False
        return real_access(path, mode)

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(cmd) >= 3 and cmd[0] == "sudo" and cmd[1] == "cat" and cmd[2] == str(lock_path):
            return subprocess.CompletedProcess(cmd, 0, stdout=lock_text)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("deploy.cdvn_migrate.os.access", _deny_read)
    monkeypatch.setattr("deploy.cdvn_migrate.subprocess.run", _fake_run)

    env = {"NETWORK": "mainnet"}
    assert _fee_recipient_from_cluster_lock(str(charon)) == fee
    assert _resolve_fee_recipient(env, str(charon)) == fee
    assert any(cmd[:3] == ["sudo", "cat", str(lock_path)] for cmd in calls)

    plan = _plan(root, tmp_path)
    assert plan.fee_recipient == fee


def test_plan_vc_lighthouse(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lighthouse\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    plan = _plan(root, tmp_path)
    assert plan.vc_name == "Lighthouse"
    assert "--vc" in plan.deploy_argv()
    assert plan.deploy_argv()[plan.deploy_argv().index("--vc") + 1] == "Lighthouse"


def test_plan_full_stack_with_local_mev(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=hoodi\n"
        "EL=el-nethermind\n"
        "CL=cl-lighthouse\n"
        "VC=vc-lodestar\n"
        "MEV=mev-mevboost\n"
        "BUILDER_API_ENABLED=true\n",
        data_dirs=["data/nethermind", "data/lighthouse", "data/lodestar"],
    )
    plan = _plan(root, tmp_path)
    assert plan.role == "Custom Setup"
    assert plan.network == "hoodi"
    assert plan.ec_name == "Nethermind"
    assert plan.cc_name == "Lighthouse"
    assert plan.vc_name == "Lodestar"
    assert plan.with_mevboost is True
    assert plan.with_builder_api is True
    argv = plan.deploy_argv()
    assert "--ec" in argv and "Nethermind" in argv
    assert "--cc" in argv and "Lighthouse" in argv
    assert "--with_mevboost" in argv
    movable = [m for m in plan.datadir_moves if m.will_move]
    assert {m.relative_src for m in movable} >= {
        "data/nethermind",
        "data/lighthouse",
        "data/lodestar",
    }


def test_plan_rewrites_docker_bn(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=sepolia\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-teku\n"
        "MEV=mev-none\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://lighthouse:5052\n",
    )
    plan = _plan(root, tmp_path)
    assert plan.vc_name == "Teku"
    assert plan.bn_address == "http://127.0.0.1:5052"
    assert any("Rewrote" in w or "lighthouse" in w for w in plan.warnings)


def test_plan_unknown_el_treated_as_external(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-geth\n"
        "CL=cl-lighthouse\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n",
    )
    with pytest.raises(ValueError, match="unsupported"):
        _plan(root, tmp_path)


def test_plan_custom_external_profiles_vc_only(tmp_path: Path):
    """Unmapped EL/CL/MEV profiles → external stack (e.g. rp-external)."""
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=rp-external\n"
        "CL=rp-external\n"
        "VC=vc-teku\n"
        "MEV=rp-external\n"
        "BUILDER_API_ENABLED=true\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://100.116.116.75:5052\n"
        "MONITORING_PORT_GRAFANA=3701\n",
    )
    plan = _plan(root, tmp_path)
    assert plan.role == "Validator Client Only"
    assert plan.ec_name is None
    assert plan.cc_name is None
    assert plan.vc_name == "Teku"
    assert plan.with_mevboost is False
    assert plan.with_builder_api is True
    assert plan.grafana_port == 3701
    assert plan.bn_address == "http://100.116.116.75:5052"
    assert any("rp-external" in w and "EL" in w for w in plan.warnings)
    assert any("rp-external" in w and "CL" in w for w in plan.warnings)
    assert any("rp-external" in w and "MEV" in w for w in plan.warnings)


def test_plan_cl_without_el_fails(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-lighthouse\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    with pytest.raises(ValueError, match="unsupported"):
        _plan(root, tmp_path)


def test_plan_orphan_data_warning(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://10.0.0.2:5052\n",
        data_dirs=["data/lighthouse"],
    )
    plan = _plan(root, tmp_path)
    assert any("data/lighthouse" in w and "cl-none" in w for w in plan.warnings)
    assert not any(m.relative_src == "data/lighthouse" and m.will_move for m in plan.datadir_moves)


def test_plan_lodestar_bn_incompatible_vc_warns(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-nethermind\n"
        "CL=cl-lodestar\n"
        "VC=vc-prysm\n"
        "MEV=mev-mevboost\n",
    )
    plan = _plan(root, tmp_path)
    assert plan.cc_name == "Lodestar"
    assert plan.vc_name == "Prysm"
    assert any("Lodestar beacon node" in w and "Prysm" in w for w in plan.warnings)


def test_datadir_map_covers_stock_clients():
    assert "data/nethermind" in DATADIR_MOVES
    assert "data/reth" in DATADIR_MOVES
    assert DATADIR_MOVES["data/lodestar"][0] == "lodestar_validator"
    assert DATADIR_MOVES["data/vc-lighthouse"][0] == "lighthouse_validator"
    assert VC_PROFILE_MAP["vc-lighthouse"] == "Lighthouse"


def test_plan_docker_absent_cli_assumes_stopped(tmp_path: Path, monkeypatch):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr("deploy.cdvn_migrate.shutil.which", lambda _name: None)
    plan = _plan(root, tmp_path)
    assert plan.docker_running is False
    assert not plan.docker_check_error
    assert any("Docker CLI not found" in w for w in plan.warnings)


def test_detect_docker_compose_unknown_without_cli(tmp_path: Path, monkeypatch):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr("deploy.cdvn_migrate.shutil.which", lambda _name: None)
    running, err = detect_docker_compose_status(str(compose), str(tmp_path))
    assert running is False
    assert err == ""


def test_detect_ethpillar_vc_name(tmp_path: Path):
    svc = tmp_path / "validator.service"
    svc.write_text(
        "[Unit]\nDescription=Nimbus Validator Client service for MAINNET\n",
        encoding="utf-8",
    )
    assert detect_ethpillar_vc_name(str(svc)) == "Nimbus"


def test_run_migration_fresh_resets_before_overlay(tmp_path, monkeypatch):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    reset_calls: list[str] = []

    def fake_reset(plan):
        reset_calls.append(plan.root)

    monkeypatch.setattr("deploy.cdvn_migrate.run_deploy", lambda *a, **k: 0)
    monkeypatch.setattr("deploy.cdvn_migrate.apply_datadir_moves", lambda *a, **k: [])
    monkeypatch.setattr(
        "deploy.cdvn_migrate._apply_charon_cluster_overlay",
        lambda plan, **k: None,
    )
    monkeypatch.setattr("deploy.cdvn_migrate.import_cdvn_env_to_service", lambda *a, **k: None)
    monkeypatch.setattr(
        "deploy.cdvn_migrate.sync_charon_keyshares_to_vc",
        lambda *a, **k: {"status": "copied", "count": 1, "dest": "/var/lib/lodestar_validator"},
    )
    monkeypatch.setattr("deploy.cdvn_migrate.reset_cdvn_migration_state", fake_reset)
    monkeypatch.setattr("deploy.cdvn_migrate.enable_migrated_units", lambda _plan: None)

    from deploy.cdvn_migrate import run_migration

    run_migration(str(root), skip_deploy=True, apply_moves=[], fresh=True)
    assert reset_calls == [str(root)]


def test_run_migration_applies_charon_overlay_with_empty_moves(tmp_path, monkeypatch):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    overlay_calls: list[str] = []

    def fake_overlay(plan, *, skip=False, force=False):
        overlay_calls.append(plan.root)

    monkeypatch.setattr("deploy.cdvn_migrate.run_deploy", lambda *a, **k: 0)
    monkeypatch.setattr("deploy.cdvn_migrate.apply_datadir_moves", lambda *a, **k: [])
    monkeypatch.setattr("deploy.cdvn_migrate._apply_charon_cluster_overlay", fake_overlay)
    monkeypatch.setattr("deploy.cdvn_migrate.import_cdvn_env_to_service", lambda *a, **k: None)
    monkeypatch.setattr(
        "deploy.cdvn_migrate.sync_charon_keyshares_to_vc",
        lambda *a, **k: {"status": "skipped", "reason": "destination already has keystores (/var/lib/lodestar_validator/keystores)"},
    )
    monkeypatch.setattr("deploy.cdvn_migrate.enable_migrated_units", lambda _plan: None)

    from deploy.cdvn_migrate import run_migration

    run_migration(str(root), skip_deploy=True, apply_moves=[])
    assert overlay_calls == [str(root)]


def test_runtime_path_exists_uses_sudo_for_root_owned_file(tmp_path, monkeypatch):
    target = tmp_path / "cluster-lock.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("deploy.charon.os.path.isfile", lambda _path: False)

    def fake_run(args, **kwargs):
        rc = 0 if args[:3] == ["sudo", "test", "-f"] else 1
        class _Result:
            returncode = rc

        return _Result()

    monkeypatch.setattr("deploy.charon.subprocess.run", fake_run)
    from deploy.charon import path_exists

    assert path_exists(str(target)) is True


def test_grafana_port_from_env():
    assert grafana_port_from_env({"MONITORING_PORT_GRAFANA": "3701"}) == 3701
    assert grafana_port_from_env({}) is None
    assert grafana_port_from_env({"MONITORING_PORT_GRAFANA": "0"}) is None


def test_apply_cdvn_monitoring_from_env_reads_effective_port(monkeypatch, tmp_path):
    from deploy.cdvn_migrate import apply_cdvn_monitoring_from_env

    env = tmp_path / ".env"
    env.write_text("MONITORING_PORT_GRAFANA=3701\n", encoding="utf-8")
    monkeypatch.setattr(
        "deploy.cdvn_migrate.apply_grafana_http_port",
        lambda port: port == 3701,
    )
    monkeypatch.setattr(
        "deploy.cdvn_migrate.read_grafana_ini",
        lambda: "[server]\nhttp_port = 3000\n",
    )
    monkeypatch.setattr(
        "deploy.cdvn_migrate.read_grafana_http_port",
        lambda default=3000: 3000,
    )
    assert apply_cdvn_monitoring_from_env(str(env)) == 3000


# ── _dest_has_data (fail closed) / main error handling ─────────────────────────

def test_dest_has_data_missing_and_readable_dirs(tmp_path):
    from deploy.cdvn_migrate import _dest_has_data

    assert _dest_has_data(str(tmp_path / "missing")) is False
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _dest_has_data(str(empty)) is False
    (empty / "db").mkdir()
    assert _dest_has_data(str(empty)) is True


@pytest.mark.parametrize(
    "returncode, stdout, expected",
    [
        (1, "", True),            # sudo find fails -> cannot list -> occupied (fail closed)
        (0, "/dest/db\n", True),  # root-only dir with content
        (0, "", False),           # root-only dir, really empty
    ],
)
def test_dest_has_data_unreadable_dir(monkeypatch, returncode, stdout, expected):
    import deploy.cdvn_migrate as cm

    def deny(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(cm, "path_exists", lambda path, directory=False: True)
    monkeypatch.setattr(cm.os, "listdir", deny)
    monkeypatch.setattr(
        cm.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], returncode, stdout=stdout, stderr=""),
    )
    assert cm._dest_has_data("/dest") is expected


def test_main_reports_failed_sudo_step_as_error(monkeypatch, capsys):
    import deploy.cdvn_migrate as cm

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, ["sudo", "mv", "a", "b"])

    monkeypatch.setattr(cm, "run_migration", boom)
    assert cm.main(["run", "--path", "/nonexistent"]) == 1
    assert "ERROR:" in capsys.readouterr().err
