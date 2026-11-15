"""Tests for deploy/ssv_migrate discovery, YAML parse, and dry-run apply."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deploy.ssv_migrate import (
    classify_unit_name,
    discover_units,
    parse_dkg_config,
    parse_node_config,
    plan_ssv_migration,
    quarantine_unit,
    run_ssv_migration,
    versions_match,
    _config_from_exec,
    _first_token,
    _parse_unit_file,
    _resolve_path,
    _yaml_scalar_or_list,
)


def test_classify_unit_name_variants():
    assert classify_unit_name("ssvnode.service") == "node"
    assert classify_unit_name("ssv_node.service") == "node"
    assert classify_unit_name("SSV.service") == "node"
    assert classify_unit_name("ssvdkg.service") == "dkg"
    assert classify_unit_name("ssv_dkg.service") == "dkg"
    # Bare EthPillar names without body → treat as already-managed.
    assert classify_unit_name("ssv-dkg.service") is None
    assert classify_unit_name("ssv-node.service") is None
    assert classify_unit_name("something.service") is None
    # Legacy unit that reuses EthPillar *names* but different paths → migrate.
    legacy_node = (
        "[Service]\nWorkingDirectory=/home/u/ssv\n"
        "ExecStart=/home/u/ssv/bin/ssvnode start-node --config=/home/u/ssv/config.yaml\n"
    )
    assert classify_unit_name("ssv-node.service", unit_text=legacy_node) == "node"
    ethpillar_node = (
        "[Service]\nWorkingDirectory=/var/lib/ssv\n"
        "ExecStart=/usr/local/bin/ssvnode start-node --config=/var/lib/ssv/config.yaml\n"
    )
    assert classify_unit_name("ssv-node.service", unit_text=ethpillar_node) is None


def test_parse_unit_and_exec_helpers():
    text = """
[Unit]
Description=SSV
[Service]
WorkingDirectory=/home/u/ssv
ExecStart=/home/u/ssv/bin/ssvnode start-node --config=/home/u/ssv/config/config.yaml
"""
    fields = _parse_unit_file(text)
    assert fields["WorkingDirectory"] == "/home/u/ssv"
    assert _first_token(fields["ExecStart"]).endswith("/ssvnode")
    assert _config_from_exec(
        fields["ExecStart"], flags=("--config",)
    ) == "/home/u/ssv/config/config.yaml"
    assert (
        _resolve_path("data/db", workdir="/home/u/ssv", unit_dir="/etc/systemd/system")
        == "/home/u/ssv/data/db"
    )


def test_yaml_multi_endpoint_scalar():
    content = """
eth2:
  BeaconNodeAddr: http://a:5052,http://b:5052,http://c:5052
eth1:
  ETH1Addr: ws://a:8546,ws://b:8546,ws://c:8546
"""
    assert (
        _yaml_scalar_or_list(content, "BeaconNodeAddr")
        == "http://a:5052,http://b:5052,http://c:5052"
    )
    assert _yaml_scalar_or_list(content, "ETH1Addr").count("ws://") == 3


def test_yaml_block_list_joined():
    content = """
eth2:
  BeaconNodeAddr:
    - http://a:5052
    - http://b:5052
    - http://c:5052
"""
    assert _yaml_scalar_or_list(content, "BeaconNodeAddr") == (
        "http://a:5052,http://b:5052,http://c:5052"
    )


def test_parse_node_and_dkg_config_multi():
    node = """
global:
  LogLevel: info
db:
  Path: /home/u/ssv/data/db
ssv:
  Network: hoodi
eth2:
  BeaconNodeAddr: http://1:5052,http://2:5052,http://3:5052
eth1:
  ETH1Addr: ws://1:8546,ws://2:8546,ws://3:8546
p2p:
  TcpPort: 13001
  UdpPort: 12001
  HostAddress: 203.0.113.9
KeyStore:
  PrivateKeyFile: /home/u/ssv/encrypted_private_key.json
  PasswordFile: /home/u/ssv/password
MetricsAPIPort: 15000
SSVAPIPort: 16000
"""
    parsed = parse_node_config(node)
    assert parsed["eth_network"] == "hoodi"
    assert parsed["beacon_node_addr"].count("http://") == 3
    assert parsed["eth1_ws_addr"].count("ws://") == 3
    assert parsed["p2p_external_ip"] == "203.0.113.9"
    assert parsed["db_path"] == "/home/u/ssv/data/db"

    dkg = """
privKey: /home/u/ssv/encrypted_private_key.json
privKeyPassword: /home/u/ssv/password
operatorID: 42
port: 3030
ethEndpointURL: http://1:8545,http://2:8545,http://3:8545
outputPath: /home/u/ssv-dkg/data
"""
    dparsed = parse_dkg_config(dkg)
    assert dparsed["operator_id"] == "42"
    assert dparsed["eth1_http_addr"].count("http://") == 3
    assert dparsed["dkg_port"] == 3030


def test_versions_match_normalizes_v_prefix():
    assert versions_match("v2.5.1", "2.5.1")
    assert versions_match("v2.5.1", "v2.5.1")
    assert not versions_match("v2.5.0", "v2.5.1")
    assert not versions_match("", "v2.5.1")


def test_eth1_ws_to_http_preserves_ports_and_semicolon_list():
    from deploy.ssv_migrate import _eth1_ws_to_http

    # Scheme only — do not remap :8546→:8545 (non-standard layouts).
    ws = "ws://a:8546;ws://b:8556;wss://c:443"
    assert _eth1_ws_to_http(ws) == "http://a:8546;http://b:8556;https://c:443"


def test_parse_cli_flags_from_run_ssv_dkg_wrapper(tmp_path: Path):
    from deploy.ssv_migrate import _dkg_settings_from_wrapper, _parse_cli_flags

    script = tmp_path / "run_ssv-dkg.sh"
    (tmp_path / "data").mkdir()
    (tmp_path / "ssl").mkdir()
    (tmp_path / "data" / "encrypted_private_key.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "password").write_text("pw", encoding="utf-8")
    (tmp_path / "ssl" / "tls.crt").write_text("cert", encoding="utf-8")
    (tmp_path / "ssl" / "tls.key").write_text("key", encoding="utf-8")
    script.write_text(
        """#!/bin/bash
./bin/ssv-dkg start-operator \\
            --privKey ./data/encrypted_private_key.json  \\
            --privKeyPassword ./data/password \\
            --operatorID 42 \\
            --port 3030 \\
            --outputPath ./operator-config/output \\
            --serverTLSCertPath ./ssl/tls.crt \\
            --serverTLSKeyPath ./ssl/tls.key \\
            --ethEndpointURL http://100.118.113.11:8545
""",
        encoding="utf-8",
    )
    flags = _parse_cli_flags(script.read_text(encoding="utf-8"))
    assert flags["operatorID"] == "42"
    assert flags["ethEndpointURL"] == "http://100.118.113.11:8545"
    settings = _dkg_settings_from_wrapper(str(script), workdir=str(tmp_path))
    assert settings["operator_id"] == "42"
    assert settings["tls_cert_path"].endswith("ssl/tls.crt")
    assert settings["eth1_http_addr"] == "http://100.118.113.11:8545"


def test_relative_db_path_resolves_via_config_dir(tmp_path: Path):
    from deploy.ssv_migrate import _resolve_path

    cfg_dir = tmp_path / "ssv" / "config"
    db = tmp_path / "ssv" / "data" / "db"
    db.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)
    resolved = _resolve_path(
        "./data/db",
        bases=[str(cfg_dir), str(tmp_path / "ssv")],
    )
    assert resolved == str(db)


def test_config_from_script_skips_elf_binary(tmp_path: Path):
    from deploy.ssv_migrate import _config_from_script

    elf = tmp_path / "ssvnode"
    elf.write_bytes(b"\x7fELF" + b"\xb7" * 64)
    assert _config_from_script(str(elf)) == ""


def _write_legacy_stack(tmp_path: Path) -> Path:
    """Create a mini legacy ssvnode + ssvdkg layout under tmp_path."""
    root = tmp_path / "home"
    ssv = root / "ssv"
    dkg = root / "ssv-dkg"
    (ssv / "bin").mkdir(parents=True)
    (ssv / "config").mkdir()
    (ssv / "data" / "db").mkdir(parents=True)
    (dkg / "bin").mkdir(parents=True)
    (dkg / "operator-config").mkdir(parents=True)
    (dkg / "data").mkdir()

    (ssv / "encrypted_private_key.json").write_text('{"key":1}', encoding="utf-8")
    (ssv / "password").write_text("secret-pass", encoding="utf-8")
    (ssv / "data" / "db" / "marker").write_text("db", encoding="utf-8")
    (ssv / "bin" / "ssvnode").write_text("#!/bin/true\n", encoding="utf-8")
    (ssv / "bin" / "ssvnode").chmod(0o755)
    (dkg / "bin" / "ssv-dkg").write_text("#!/bin/true\n", encoding="utf-8")
    (dkg / "bin" / "ssv-dkg").chmod(0o755)

    (ssv / "config" / "config.yaml").write_text(
        f"""
db:
  Path: {ssv / "data" / "db"}
ssv:
  Network: sepolia
eth2:
  BeaconNodeAddr: http://el1:5052,http://el2:5052,http://el3:5052
eth1:
  ETH1Addr: ws://el1:8546,ws://el2:8546,ws://el3:8546
p2p:
  TcpPort: 13001
  UdpPort: 12001
KeyStore:
  PrivateKeyFile: {ssv / "encrypted_private_key.json"}
  PasswordFile: {ssv / "password"}
MetricsAPIPort: 15000
SSVAPIPort: 16000
""",
        encoding="utf-8",
    )
    (dkg / "operator-config" / "operator.yaml").write_text(
        f"""
privKey: {ssv / "encrypted_private_key.json"}
privKeyPassword: {ssv / "password"}
operatorID: 99
port: 3030
ethEndpointURL: http://el1:8545,http://el2:8545,http://el3:8545
outputPath: {dkg / "data"}
""",
        encoding="utf-8",
    )

    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "ssvnode.service").write_text(
        f"""
[Service]
WorkingDirectory={ssv}
ExecStart={ssv / "bin" / "ssvnode"} start-node --config={ssv / "config" / "config.yaml"}
""",
        encoding="utf-8",
    )
    (unit_dir / "ssvdkg.service").write_text(
        f"""
[Service]
WorkingDirectory={dkg}
ExecStart={dkg / "bin" / "ssv-dkg"} start-operator --configPath={dkg / "operator-config" / "operator.yaml"}
""",
        encoding="utf-8",
    )
    return unit_dir


def test_discover_units(tmp_path: Path):
    unit_dir = _write_legacy_stack(tmp_path)
    node, dkg = discover_units(str(unit_dir))
    assert node is not None and node.unit_name == "ssvnode.service"
    assert dkg is not None and dkg.unit_name == "ssvdkg.service"
    assert node.config_path.endswith("config.yaml")
    assert dkg.config_path.endswith("operator.yaml")


def test_discover_without_config_flags_uses_heuristics(tmp_path: Path):
    """Match home-dir layouts where ExecStart omits --config / uses a wrapper."""
    root = tmp_path / "home"
    ssv = root / "ssv"
    dkg = root / "ssv-dkg"
    (ssv / "bin").mkdir(parents=True)
    (ssv / "config").mkdir()
    (dkg / "bin").mkdir(parents=True)
    (dkg / "operator-config").mkdir(parents=True)
    (ssv / "bin" / "ssvnode").write_text("x", encoding="utf-8")
    (dkg / "bin" / "ssv-dkg").write_text("x", encoding="utf-8")
    (ssv / "config" / "config.yaml").write_text("ssv:\n  Network: mainnet\n", encoding="utf-8")
    (dkg / "operator-config" / "op.yaml").write_text("operatorID: 1\n", encoding="utf-8")
    wrapper = dkg / "run_ssv-dkg.sh"
    wrapper.write_text(
        "#!/bin/bash\nexec ./bin/ssv-dkg start-operator --configPath=./operator-config/op.yaml\n",
        encoding="utf-8",
    )
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "ssvnode.service").write_text(
        f"[Service]\nWorkingDirectory={ssv}\nExecStart={ssv / 'bin' / 'ssvnode'} start-node\n",
        encoding="utf-8",
    )
    (unit_dir / "ssvdkg.service").write_text(
        f"[Service]\nWorkingDirectory={dkg}\nExecStart={wrapper}\n",
        encoding="utf-8",
    )
    node, dkg_u = discover_units(str(unit_dir))
    assert node is not None
    assert node.config_path.endswith("config/config.yaml")
    assert dkg_u is not None
    assert dkg_u.config_path.endswith("op.yaml")
    assert dkg_u.binary_path.endswith("ssv-dkg")


@patch("deploy.ssv_migrate.get_dkg_release_info", return_value={"version": "v2.0.0"})
@patch("deploy.ssv_migrate.get_release_info", return_value={"version": "v2.5.1"})
@patch("deploy.ssv_migrate.probe_binary_version", return_value="v2.5.1")
def test_plan_multi_endpoints(mock_probe, mock_rel, mock_dkg, tmp_path: Path):
    unit_dir = _write_legacy_stack(tmp_path)
    plan = plan_ssv_migration(unit_dir=str(unit_dir))
    assert not plan.errors
    assert plan.beacon_node_addr.count("http://") == 3
    assert plan.eth1_ws_addr.count("ws://") == 3
    assert plan.eth1_http_addr.count("http://") == 3
    assert plan.operator_id == "99"
    assert plan.node_binary is not None
    assert plan.node_binary.action == "copy"
    assert plan.dkg_binary is not None
    summary = plan.summary()
    assert "sepolia" in summary
    # BN/EL appear before unit blocks so whiptail shows them without scrolling.
    assert summary.index("BeaconNode:") < summary.index("Node unit:")
    assert summary.index("ETH1 WS:") < summary.index("Node unit:")


@patch("deploy.ssv_migrate.get_dkg_release_info", return_value={"version": "v2.0.0"})
@patch("deploy.ssv_migrate.get_release_info", return_value={"version": "v2.5.1"})
@patch("deploy.ssv_migrate.probe_binary_version", return_value="v2.5.1")
def test_plan_cli_wrapper_ignores_debug_log(mock_probe, mock_rel, mock_dkg, tmp_path: Path):
    """run_ssv-dkg.sh + operator-config/debug.log must not be treated as YAML config."""
    root = tmp_path / "home"
    ssv = root / "ssv"
    dkg = root / "ssv-dkg"
    (ssv / "bin").mkdir(parents=True)
    (ssv / "config").mkdir()
    (ssv / "data" / "db").mkdir(parents=True)
    (dkg / "bin").mkdir(parents=True)
    (dkg / "operator-config" / "output").mkdir(parents=True)
    (dkg / "data").mkdir()
    (dkg / "ssl").mkdir()
    (ssv / "encrypted_private_key.json").write_text('{"key":1}', encoding="utf-8")
    (ssv / "password").write_text("secret-pass", encoding="utf-8")
    (ssv / "data" / "db" / "marker").write_text("db", encoding="utf-8")
    (ssv / "bin" / "ssvnode").write_text("#!/bin/true\n", encoding="utf-8")
    (ssv / "bin" / "ssvnode").chmod(0o755)
    (dkg / "bin" / "ssv-dkg").write_text("#!/bin/true\n", encoding="utf-8")
    (dkg / "bin" / "ssv-dkg").chmod(0o755)
    (dkg / "ssl" / "tls.crt").write_text("cert", encoding="utf-8")
    (dkg / "ssl" / "tls.key").write_text("key", encoding="utf-8")
    # Upstream DKG layout: log under operator-config/, no operator.yaml.
    (dkg / "operator-config" / "debug.log").write_text(
        '{"level":"info","msg":"started"}\n', encoding="utf-8"
    )
    (ssv / "config" / "config.yaml").write_text(
        f"""
db:
  Path: {ssv / "data" / "db"}
ssv:
  Network: mainnet
eth2:
  BeaconNodeAddr: http://100.1.1.1:5052;http://100.1.1.2:5052
eth1:
  ETH1Addr: ws://100.1.1.1:8546;ws://100.1.1.2:8546
KeyStore:
  PrivateKeyFile: {ssv / "encrypted_private_key.json"}
  PasswordFile: {ssv / "password"}
""",
        encoding="utf-8",
    )
    wrapper = dkg / "run_ssv-dkg.sh"
    wrapper.write_text(
        f"""#!/bin/bash
./bin/ssv-dkg start-operator \\
            --privKey ./data/encrypted_private_key.json \\
            --privKeyPassword ./data/password \\
            --operatorID 77 \\
            --port 3030 \\
            --logFilePath ./operator-config/debug.log \\
            --outputPath ./operator-config/output \\
            --serverTLSCertPath ./ssl/tls.crt \\
            --serverTLSKeyPath ./ssl/tls.key \\
            --ethEndpointURL http://100.118.113.11:8545
""",
        encoding="utf-8",
    )
    (dkg / "data" / "encrypted_private_key.json").write_text('{"key":1}', encoding="utf-8")
    (dkg / "data" / "password").write_text("secret-pass", encoding="utf-8")
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "ssvnode.service").write_text(
        f"[Service]\nWorkingDirectory={ssv}\n"
        f"ExecStart={ssv / 'bin' / 'ssvnode'} start-node "
        f"--config={ssv / 'config' / 'config.yaml'}\n",
        encoding="utf-8",
    )
    (unit_dir / "ssvdkg.service").write_text(
        f"[Service]\nWorkingDirectory={dkg}\nExecStart={wrapper}\n",
        encoding="utf-8",
    )

    plan = plan_ssv_migration(unit_dir=str(unit_dir))
    assert not plan.errors
    assert plan.operator_id == "77"
    assert plan.eth1_http_addr == "http://100.118.113.11:8545"
    assert "100.1.1.1:5052" in plan.beacon_node_addr
    assert plan.tls_cert_path.endswith("ssl/tls.crt")
    assert plan.dkg_unit is not None
    assert "debug.log" not in (plan.dkg_unit.config_path or "")
    assert "CLI flags" in (plan.dkg_unit.config_path or "")
    summary = plan.summary()
    assert "BeaconNode:" in summary
    assert "ETH1 HTTP:" in summary
    assert summary.index("BeaconNode:") < summary.index("DKG unit:")


@patch("deploy.ssv_migrate.get_dkg_release_info", return_value={"version": "v2.0.0"})
@patch("deploy.ssv_migrate.get_release_info", return_value={"version": "v2.5.1"})
@patch("deploy.ssv_migrate.probe_binary_version", return_value="v2.4.0")
def test_plan_rebuild_when_older(mock_probe, mock_rel, mock_dkg, tmp_path: Path):
    unit_dir = _write_legacy_stack(tmp_path)
    plan = plan_ssv_migration(unit_dir=str(unit_dir))
    assert plan.node_binary is not None
    assert plan.node_binary.action == "rebuild"
    plan2 = plan_ssv_migration(unit_dir=str(unit_dir), allow_older_binary=True)
    assert plan2.node_binary is not None
    assert plan2.node_binary.action == "copy"


@patch("deploy.systemd_units.subprocess.run")
@patch("deploy.systemd_units.verify_unit_absent")
def test_quarantine_renames_to_bak(mock_verify, mock_run, tmp_path: Path):
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    unit = unit_dir / "ssvnode.service"
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")

    def _run(cmd, **kwargs):
        # Emulate sudo mv for quarantine (shared helper).
        if len(cmd) >= 4 and cmd[1] == "mv":
            Path(cmd[3]).write_text(Path(cmd[2]).read_text(encoding="utf-8"), encoding="utf-8")
            Path(cmd[2]).unlink()
        return MagicMock(returncode=0)

    mock_run.side_effect = _run
    bak = quarantine_unit(str(unit), dry_run=False, verify=True)
    assert bak.endswith(".service.bak")
    assert not unit.exists()
    assert Path(bak).exists()
    mock_verify.assert_called()


@patch("deploy.ssv_migrate.get_dkg_release_info", return_value={"version": "v2.0.0"})
@patch("deploy.ssv_migrate.get_release_info", return_value={"version": "v2.5.1"})
@patch("deploy.ssv_migrate.probe_binary_version", return_value="v2.5.1")
def test_dry_run_does_not_quarantine(
    mock_probe, mock_rel, mock_dkg, tmp_path: Path
):
    unit_dir = _write_legacy_stack(tmp_path)

    def _run(cmd, **kwargs):
        if len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "test":
            flag, target = cmd[2], cmd[3]
            p = Path(target)
            ok = (
                p.is_file()
                if flag == "-f"
                else p.is_dir()
                if flag == "-d"
                else p.exists()
            )
            return MagicMock(returncode=0 if ok else 1, stdout=b"", stderr=b"")
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    with patch("deploy.ssv_migrate.subprocess.run", side_effect=_run), patch(
        "deploy.ssv.subprocess.run", side_effect=_run
    ):
        run_ssv_migration(dry_run=True, unit_dir=str(unit_dir))
    # Dry-run quarantine returns early before systemctl.
    assert (unit_dir / "ssvnode.service").exists()
    assert (unit_dir / "ssvdkg.service").exists()


def test_copy_tree_uses_cp_not_rsync(tmp_path: Path):
    from deploy.ssv_migrate import _copy_tree

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "a").write_text("x", encoding="utf-8")
    calls: list = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        # Emulate sudo mkdir/cp/chown for the test harness.
        if cmd[:2] == ["sudo", "mkdir"]:
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        elif cmd[:2] == ["sudo", "cp"]:
            # sudo cp -a src/. dest/
            import shutil

            shutil.copytree(src, dest, dirs_exist_ok=True)
        return MagicMock(returncode=0)

    with patch("deploy.ssv_migrate.subprocess.run", side_effect=_run):
        _copy_tree(str(src), str(dest))
    assert any(c[:2] == ["sudo", "cp"] for c in calls)
    assert not any("rsync" in c for c in calls)


def test_copy_tree_skips_same_realpath(tmp_path: Path):
    from deploy.ssv_migrate import _copy_tree

    dest = tmp_path / "db"
    dest.mkdir()
    (dest / "a").write_text("x", encoding="utf-8")
    calls: list = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    with patch("deploy.ssv_migrate.subprocess.run", side_effect=_run):
        _copy_tree(str(dest), str(dest))
    assert not any(c[:2] == ["sudo", "cp"] for c in calls)
    assert any("chown" in c for c in calls)


@patch("deploy.ssv_migrate.operator_keystore_present", return_value=True)
def test_already_migrated_short_circuit(mock_ks, tmp_path: Path):
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "ssv-node.service").write_text(
        "[Service]\nWorkingDirectory=/var/lib/ssv\n"
        "ExecStart=/usr/local/bin/ssvnode start-node "
        "--config=/var/lib/ssv/config.yaml\n",
        encoding="utf-8",
    )
    plan = plan_ssv_migration(unit_dir=str(unit_dir))
    assert not plan.errors
    assert any("Already migrated" in w for w in plan.warnings)
    run_ssv_migration(unit_dir=str(unit_dir))  # no-op success


def test_rewrite_legacy_node_config_preserves_extra_keys():
    from deploy.ssv_migrate import rewrite_legacy_node_config_for_ethpillar

    legacy = """
global:
  LogLevel: debug
  LogFilePath: /old/debug.log
db:
  Path: /old/db
ssv:
  Network: hoodi
  CustomFlag: keep-me
KeyStore:
  PrivateKeyFile: /old/key.json
  PasswordFile: /old/password
"""
    out = rewrite_legacy_node_config_for_ethpillar(legacy)
    assert "CustomFlag: keep-me" in out
    assert "LogLevel: debug" in out
    assert "PrivateKeyFile: /var/lib/ssv/encrypted_private_key.json" in out
    assert "Path: /var/lib/ssv/db" in out


def test_config_from_environment_file(tmp_path: Path):
    from deploy.ssv_migrate import _config_from_environment

    env_file = tmp_path / "ssv.env"
    env_file.write_text("SSV_CONFIG=/data/config.yaml\n", encoding="utf-8")
    fields = {"EnvironmentFile": str(env_file)}
    assert _config_from_environment(fields, unit_dir=str(tmp_path)) == "/data/config.yaml"


def test_endpoints_look_local():
    from deploy.ssv_migrate import endpoints_look_local

    assert endpoints_look_local("http://127.0.0.1:5052", "ws://127.0.0.1:8546")
    assert not endpoints_look_local("http://10.0.0.1:5052", "ws://127.0.0.1:8546")


def test_execution_unit_exposes_ws():
    from deploy.ssv_migrate import execution_unit_exposes_ws

    assert execution_unit_exposes_ws("--ws \\\n    --ws.port 8546")
    assert execution_unit_exposes_ws("--rpc-ws-enabled=true")
    assert execution_unit_exposes_ws("--JsonRpc.WebSocketsPort 8546")
    assert execution_unit_exposes_ws("--ws.enabled \\\n")
    assert not execution_unit_exposes_ws("--http --metrics")


def test_local_ethpillar_el_missing_ws(tmp_path: Path):
    from deploy.ssv_migrate import local_ethpillar_el_missing_ws

    unit = tmp_path / "execution.service"
    unit.write_text(
        "[Service]\nUser=execution\nExecStart=/usr/local/bin/besu --rpc-http-enabled=true\n",
        encoding="utf-8",
    )
    err = local_ethpillar_el_missing_ws("ws://127.0.0.1:8546", unit_dir=str(tmp_path))
    assert err is not None
    assert "does not expose WebSocket" in err

    unit.write_text(
        "[Service]\nUser=execution\nExecStart=/usr/local/bin/geth --ws --ws.port 8546\n",
        encoding="utf-8",
    )
    assert local_ethpillar_el_missing_ws("ws://127.0.0.1:8546", unit_dir=str(tmp_path)) is None
    # Remote ETH1: do not require local EL WS
    assert local_ethpillar_el_missing_ws("ws://10.0.0.5:8546", unit_dir=str(tmp_path)) is None


def test_missing_db_is_hard_error_without_allow_empty_db(tmp_path: Path):
    from deploy.ssv_migrate import plan_ssv_migration

    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    home = tmp_path / "ssv"
    (home / "data" / "db").mkdir(parents=True)  # created then removed below
    (home / "data" / "db").rmdir()
    (home / "data").rmdir()
    home.mkdir(exist_ok=True)
    (home / "encrypted_private_key.json").write_text("{}", encoding="utf-8")
    (home / "password").write_text("pw", encoding="utf-8")
    (home / "config.yaml").write_text(
        "eth2:\n  BeaconNodeAddr: http://127.0.0.1:5052\n"
        "eth1:\n  ETH1Addr: ws://127.0.0.1:8546\n"
        "KeyStore:\n  PrivateKeyFile: ./encrypted_private_key.json\n"
        "  PasswordFile: ./password\n"
        "db:\n  Path: ./data/db\n"
        "ssv:\n  Network: hoodi\n",
        encoding="utf-8",
    )
    (unit_dir / "ssv.service").write_text(
        "[Service]\nWorkingDirectory=" + str(home) + "\n"
        "ExecStart=/usr/local/bin/ssvnode start-node --config="
        + str(home / "config.yaml") + "\n",
        encoding="utf-8",
    )
    plan = plan_ssv_migration(unit_dir=str(unit_dir))
    assert any("DB path missing" in e for e in plan.errors)

    plan_ok = plan_ssv_migration(unit_dir=str(unit_dir), allow_empty_db=True)
    assert not any("DB path missing" in e for e in plan_ok.errors)
    assert any("DB path missing" in w for w in plan_ok.warnings)


def test_path_is_file_falls_back_to_sudo_test(monkeypatch):
    from deploy import ssv as ssv_mod

    calls = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr(ssv_mod.os.path, "isfile", lambda p: False)
    monkeypatch.setattr(ssv_mod.subprocess, "run", _run)
    assert ssv_mod.path_is_file("/secret/encrypted_private_key.json") is True
    assert calls and calls[0][:3] == ["sudo", "test", "-f"]


def test_path_is_dir_falls_back_to_sudo_test(monkeypatch):
    from deploy import ssv as ssv_mod

    calls = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr(ssv_mod.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(ssv_mod.subprocess, "run", _run)
    assert ssv_mod.path_is_dir("/secret/db") is True
    assert calls and calls[0][:3] == ["sudo", "test", "-d"]


@patch("deploy.ssv_migrate.quarantine_unit")
@patch("deploy.ssv_migrate.write_service_file")
@patch("deploy.ssv_migrate._sudo_write_text")
@patch("deploy.ssv_migrate.import_operator_keystore_files")
@patch("deploy.ssv_migrate._copy_tree")
@patch("deploy.ssv_migrate._install_dkg_binary")
@patch("deploy.ssv_migrate._install_node_binary", return_value="v9.9.9")
@patch("deploy.ssv_migrate.ensure_ssv_datadir")
@patch("deploy.ssv_migrate.subprocess.run")
def test_same_name_dkg_retired_before_ethpillar_write(
    mock_run,
    mock_ensure,
    mock_node_bin,
    mock_dkg_bin,
    mock_copy,
    mock_import,
    mock_write_text,
    mock_write_unit,
    mock_quarantine,
    tmp_path: Path,
):
    """Bug A: legacy ssv-dkg.service must be bak'd before writing EthPillar unit."""
    from deploy.ssv_migrate import run_ssv_migration

    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    home = tmp_path / "legacy"
    (home / "db").mkdir(parents=True)
    (home / "db" / "x").write_text("1", encoding="utf-8")
    (home / "encrypted_private_key.json").write_text("{}", encoding="utf-8")
    (home / "password").write_text("pw", encoding="utf-8")
    cfg = home / "config.yaml"
    cfg.write_text(
        "eth2:\n  BeaconNodeAddr: http://127.0.0.1:5052\n"
        "eth1:\n  ETH1Addr: ws://127.0.0.1:8546\n"
        "KeyStore:\n  PrivateKeyFile: ./encrypted_private_key.json\n"
        "  PasswordFile: ./password\n"
        "db:\n  Path: ./db\n"
        "ssv:\n  Network: hoodi\n",
        encoding="utf-8",
    )
    # Legacy node under a different name; DKG uses EthPillar's name.
    (unit_dir / "ssv.service").write_text(
        "[Service]\nWorkingDirectory=" + str(home) + "\n"
        f"ExecStart=/opt/legacy/ssvnode start-node --config={cfg}\n",
        encoding="utf-8",
    )
    legacy_dkg = (
        "[Service]\nWorkingDirectory=" + str(home) + "\n"
        "ExecStart=/opt/legacy/ssv-dkg start-operator --configPath=./operator.yaml\n"
    )
    (unit_dir / "ssv-dkg.service").write_text(legacy_dkg, encoding="utf-8")
    (home / "operator.yaml").write_text("operatorID: 1\n", encoding="utf-8")

    order: list[str] = []

    def _quarantine(path, **kwargs):
        order.append(f"quarantine:{os.path.basename(path)}")
        # Emulate retire: move aside so path_is_file becomes false.
        p = Path(path)
        bak = p.with_name(p.name + ".bak")
        p.rename(bak)
        return str(bak)

    def _write_unit(body, dest, tmp_name):
        order.append(f"write:{os.path.basename(dest)}")
        Path(dest).write_text(body, encoding="utf-8")

    mock_quarantine.side_effect = _quarantine
    mock_write_unit.side_effect = _write_unit

    def _run(cmd, **kwargs):
        # sudo test -f/-d must reflect real filesystem (path_is_* shares subprocess).
        if len(cmd) >= 3 and cmd[0] == "sudo" and cmd[1] == "test":
            flag, target = cmd[2], cmd[3]
            ok = Path(target).is_file() if flag == "-f" else Path(target).is_dir() if flag == "-d" else Path(target).exists()
            return MagicMock(returncode=0 if ok else 1, stdout=b"", stderr=b"")
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    mock_run.side_effect = _run

    # Point EthPillar DKG service path at our temp unit_dir via env-less patch.
    with patch("deploy.ssv_migrate.SSV_DKG_SERVICE_PATH", str(unit_dir / "ssv-dkg.service")), \
         patch("deploy.ssv_migrate.SSV_NODE_SERVICE_PATH", str(unit_dir / "ssv-node.service")), \
         patch("deploy.ssv_migrate.SYSTEMD_UNIT_DIR", str(unit_dir)), \
         patch("deploy.ssv.subprocess.run", side_effect=_run):
        run_ssv_migration(unit_dir=str(unit_dir), allow_empty_db=False)

    # Same-named DKG must be quarantined before the EthPillar write.
    assert "quarantine:ssv-dkg.service" in order
    assert "write:ssv-dkg.service" in order
    assert order.index("quarantine:ssv-dkg.service") < order.index("write:ssv-dkg.service")
    # After migrate, live unit must be EthPillar-owned (WorkingDirectory=/var/lib/ssv).
    live = (unit_dir / "ssv-dkg.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=/var/lib/ssv" in live
    bak = (unit_dir / "ssv-dkg.service.bak").read_text(encoding="utf-8")
    assert "/opt/legacy/ssv-dkg" in bak


@patch("deploy.ssv_migrate.restore_retired_unit")
@patch("deploy.ssv_migrate.retire_systemd_unit")
def test_rollback_restores_legacy_same_name_dkg_bak(mock_retire, mock_restore, tmp_path: Path):
    """Bug A2: content-based restore includes legacy ssv-dkg.service.bak."""
    from deploy.ssv_migrate import rollback_ssv_migration

    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    # Live EthPillar DKG unit.
    (unit_dir / "ssv-dkg.service").write_text(
        "[Service]\nWorkingDirectory=/var/lib/ssv\n"
        "ExecStart=/usr/local/bin/ssv-dkg start-operator --configPath=./operator.yaml\n",
        encoding="utf-8",
    )
    # Pre-existing legacy bak from migrate (same name).
    (unit_dir / "ssv-dkg.service.bak").write_text(
        "[Service]\nWorkingDirectory=/home/ssv/ssv\n"
        "ExecStart=/home/ssv/ssv/ssv-dkg start-operator --configPath=./operator.yaml\n",
        encoding="utf-8",
    )
    (unit_dir / "ssv.service.bak").write_text(
        "[Service]\nWorkingDirectory=/home/ssv/ssv\n"
        "ExecStart=/home/ssv/ssv/ssvnode start-node\n",
        encoding="utf-8",
    )

    ethpillar_bak = unit_dir / "ssv-dkg.service.bak.1"

    def _retire(path, **kwargs):
        p = Path(path)
        # Numbered bak because plain .bak exists.
        bak = ethpillar_bak
        p.rename(bak)
        bak.write_text(
            "[Service]\nWorkingDirectory=/var/lib/ssv\n"
            "ExecStart=/usr/local/bin/ssv-dkg start-operator --configPath=./operator.yaml\n",
            encoding="utf-8",
        )
        return str(bak)

    restored: list[str] = []

    def _restore(bak_path, **kwargs):
        restored.append(os.path.basename(bak_path))
        live = str(bak_path).replace(".bak.1", "").replace(".bak", "")
        if live.endswith(".service"):
            Path(live).write_text("restored", encoding="utf-8")
        return live

    mock_retire.side_effect = _retire
    mock_restore.side_effect = _restore

    rollback_ssv_migration(unit_dir=str(unit_dir))
    assert "ssv-dkg.service.bak" in restored
    assert "ssv.service.bak" in restored
    assert "ssv-dkg.service.bak.1" not in restored


@patch("deploy.ssv_migrate.operator_keystore_present", return_value=True)
@patch("deploy.ssv_migrate.repair_missing_ethpillar_dkg", return_value=True)
def test_already_migrated_repairs_missing_dkg(mock_repair, mock_ks, tmp_path: Path):
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "ssv-node.service").write_text(
        "[Service]\nWorkingDirectory=/var/lib/ssv\n"
        "ExecStart=/usr/local/bin/ssvnode start-node "
        "--config=/var/lib/ssv/config.yaml\n",
        encoding="utf-8",
    )
    from deploy.ssv_migrate import run_ssv_migration

    run_ssv_migration(unit_dir=str(unit_dir))
    mock_repair.assert_called_once()
