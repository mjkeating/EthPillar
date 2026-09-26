"""Tests for deploy/charon.py beacon-endpoint patching and CDVN .env import."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.charon import (
    generate_charon_service,
    import_cdvn_env_to_service,
    parse_dotenv,
    parse_monitoring_port,
    parse_p2p_tcp_port,
    patch_beacon_endpoints,
    plan_cdvn_env_import,
    rewrite_docker_url,
    scrape_beacon_endpoints,
)

CHARON_UNIT = generate_charon_service("mainnet", "http://127.0.0.1:5052", builder_api=True)


def test_scrape_beacon_endpoints():
    assert scrape_beacon_endpoints(CHARON_UNIT) == "http://127.0.0.1:5052"


def test_patch_beacon_endpoints_updates_url(tmp_path):
    service_path = tmp_path / "charon.service"
    service_path.write_text(CHARON_UNIT, encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://192.168.1.20:5052")
    updated = service_path.read_text(encoding="utf-8")
    assert "--beacon-node-endpoints=http://192.168.1.20:5052" in updated
    assert "http://127.0.0.1:5052" not in updated
    assert "http://192.168.1.20:5052/eth/v1/node/version" in updated
    assert "--builder-api" in updated


def test_patch_beacon_endpoints_adds_wait_to_legacy_unit(tmp_path):
    """Older units without ExecStartPre get a BN wait when endpoints are patched."""
    legacy = "\n".join(
        line
        for line in CHARON_UNIT.splitlines()
        if not line.startswith("ExecStartPre=") and not line.startswith("TimeoutStartSec=")
    )
    service_path = tmp_path / "charon.service"
    service_path.write_text(legacy + "\n", encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://10.0.0.5:5052")
    updated = service_path.read_text(encoding="utf-8")
    assert "ExecStartPre=/bin/bash -c" in updated
    assert "http://10.0.0.5:5052/eth/v1/node/version" in updated
    assert "TimeoutStartSec=infinity" in updated


def test_patch_beacon_endpoints_no_change(tmp_path):
    service_path = tmp_path / "charon.service"
    service_path.write_text(CHARON_UNIT, encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://127.0.0.1:5052") is False


def test_patch_beacon_endpoints_missing_file():
    with pytest.raises(FileNotFoundError):
        patch_beacon_endpoints("/tmp/does-not-exist-charon.service", "http://127.0.0.1:5052")


def test_parse_dotenv_strips_comments_and_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "NETWORK=mainnet\n"
        "CHARON_BEACON_NODE_ENDPOINTS=\"http://lighthouse:5052\"\n"
        "CHARON_LOG_LEVEL=info # trailing\n"
        "BUILDER_API_ENABLED=true\n",
        encoding="utf-8",
    )
    parsed = parse_dotenv(str(env_file))
    assert parsed["NETWORK"] == "mainnet"
    assert parsed["CHARON_BEACON_NODE_ENDPOINTS"] == "http://lighthouse:5052"
    assert parsed["CHARON_LOG_LEVEL"] == "info"
    assert parsed["BUILDER_API_ENABLED"] == "true"


def test_rewrite_docker_url():
    url, warn = rewrite_docker_url("http://lighthouse:5052")
    assert url == "http://127.0.0.1:5052"
    assert warn is not None
    url2, warn2 = rewrite_docker_url("http://192.168.1.8:5052")
    assert url2 == "http://192.168.1.8:5052"
    assert warn2 is None


def test_parse_p2p_tcp_port_from_service_content():
    unit = generate_charon_service(
        "mainnet",
        "http://127.0.0.1:5052",
        p2p_tcp_address="0.0.0.0:3812",
    )
    assert parse_p2p_tcp_port(unit) == 3812
    assert parse_p2p_tcp_port("") == 3610
    assert parse_p2p_tcp_port("[Service]\nExecStart=charon run", default=3610) == 3610


def test_parse_monitoring_port_from_service_content():
    unit = generate_charon_service(
        "mainnet",
        "http://127.0.0.1:5052",
        monitoring_address="127.0.0.1:3700",
    )
    assert parse_monitoring_port(unit) == 3700
    assert parse_monitoring_port("[Service]\nExecStart=charon run") == 3620


def test_plan_cdvn_env_import_maps_core_flags():
    plan = plan_cdvn_env_import(
        {
            "NETWORK": "hoodi",
            "CHARON_BEACON_NODE_ENDPOINTS": "http://host.docker.internal:5052",
            "BUILDER_API_ENABLED": "true",
            "CHARON_PORT_P2P_TCP": "3610",
            "CHARON_VALIDATOR_API_ADDRESS": "0.0.0.0:3600",
            "CHARON_MONITORING_ADDRESS": "0.0.0.0:3620",
            "CHARON_P2P_RELAYS": "https://0.relay.obol.tech",
            "CHARON_LOG_LEVEL": "debug",
            "CHARON_LOKI_ADDRESSES": "http://loki:3100",
            "CHARON_FEATURE_SET_ENABLE": "json_requests",
        }
    )
    assert plan.network == "hoodi"
    assert plan.beacon_node_endpoints == "http://127.0.0.1:5052"
    assert plan.builder_api is True
    assert plan.p2p_tcp_address == "0.0.0.0:3610"
    assert plan.validator_api_address == "127.0.0.1:3600"
    assert plan.monitoring_address == "127.0.0.1:3620"
    assert plan.feature_set_enable == "json_requests"
    assert "--p2p-relays=https://0.relay.obol.tech" in plan.extra_args
    assert "--log-level=debug" in plan.extra_args
    assert any(r.kind == "skipped" and "LOKI" in r.env_line for r in plan.rows)

    unit = plan.service_content()
    assert "--beacon-node-endpoints=http://127.0.0.1:5052" in unit
    assert "--builder-api" in unit
    assert "--feature-set-enable=json_requests" in unit
    assert "--log-level=debug" in unit


def test_import_preview_panes_are_line_aligned(tmp_path):
    from deploy.charon import write_import_preview_panes

    plan = plan_cdvn_env_import(
        {
            "NETWORK": "mainnet",
            "CHARON_BEACON_NODE_ENDPOINTS": "http://lighthouse:5052",
            "BUILDER_API_ENABLED": "true",
        }
    )
    left, right = write_import_preview_panes(plan, str(tmp_path))
    left_lines = (tmp_path / "01_cdvn.env").read_text(encoding="utf-8").splitlines()
    right_lines = (tmp_path / "02_systemd.flags").read_text(encoding="utf-8").splitlines()
    assert left == str(tmp_path / "01_cdvn.env")
    assert right == str(tmp_path / "02_systemd.flags")
    assert len(left_lines) == len(right_lines)
    assert any("CHARON_BEACON_NODE_ENDPOINTS=" in line for line in left_lines)
    assert any("--beacon-node-endpoints=http://127.0.0.1:5052" in line for line in right_lines)


def test_import_cdvn_env_apply(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "NETWORK=sepolia\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://192.168.1.50:5052\n"
        "BUILDER_API_ENABLED=false\n",
        encoding="utf-8",
    )
    service_path = tmp_path / "charon.service"
    plan = import_cdvn_env_to_service(
        str(env_file),
        service_path=str(service_path),
        apply=True,
    )
    assert plan.network == "sepolia"
    assert service_path.is_file()
    content = service_path.read_text(encoding="utf-8")
    assert "SEPOLIA" in content
    assert "--beacon-node-endpoints=http://192.168.1.50:5052" in content
    assert "--builder-api" not in content


def test_plan_derives_bn_from_cl_when_unset():
    plan = plan_cdvn_env_import({"CL": "cl-lighthouse", "NETWORK": "mainnet"})
    assert plan.beacon_node_endpoints == "http://127.0.0.1:5052"
    assert any("derived from CL" in w for w in plan.warnings)


def test_resolve_cdvn_checkout_directory(tmp_path):
    from deploy.charon import resolve_cdvn_checkout

    root = tmp_path / "cdvn"
    root.mkdir()
    (root / ".env").write_text("NETWORK=mainnet\n", encoding="utf-8")
    charon = root / ".charon"
    charon.mkdir()
    (charon / "cluster-lock.json").write_text("{}", encoding="utf-8")
    keys = charon / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    info = resolve_cdvn_checkout(str(root))
    assert info["root"] == str(root)
    assert info["env_path"] == str(root / ".env")
    assert info["charon_dir"] == str(charon)
    assert info["has_lock"] is True
    assert info["has_keyshares"] is True
    assert info["compose_file"] == str(root / "docker-compose.yml")


def test_resolve_cdvn_checkout_from_env_file(tmp_path):
    from deploy.charon import resolve_cdvn_checkout

    root = tmp_path / "cdvn"
    root.mkdir()
    env = root / ".env"
    env.write_text("NETWORK=hoodi\n", encoding="utf-8")
    info = resolve_cdvn_checkout(str(env))
    assert info["root"] == str(root)
    assert info["env_path"] == str(env)
    assert info["charon_dir"] is None


def test_resolve_cdvn_checkout_follows_charon_symlink(tmp_path):
    from deploy.charon import resolve_cdvn_checkout

    real = tmp_path / "node2"
    real.mkdir()
    (real / "cluster-lock.json").write_text("{}", encoding="utf-8")
    keys = real / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")

    root = tmp_path / "cdvn"
    root.mkdir()
    (root / ".env").write_text("NETWORK=mainnet\n", encoding="utf-8")
    (root / ".charon").symlink_to(real, target_is_directory=True)

    info = resolve_cdvn_checkout(str(root))
    assert info["charon_is_symlink"] is True
    assert info["charon_dir"] == str(real.resolve())
    assert info["has_lock"] is True
    assert info["has_keyshares"] is True


def test_sync_charon_keyshares_lodestar_import(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import sync_charon_keyshares_to_vc

    keys = tmp_path / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")
    (keys / "keystore-0.txt").write_text("password\n", encoding="utf-8")
    dest = tmp_path / "lodestar_validator"
    monkeypatch.setattr(charon_mod, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Lodestar": str(dest)},
    )
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "find":
            names = ""
            if "validator_keys" in cmd[2] or "charon-key-import" in cmd[2]:
                names = "keystore-0.json\nkeystore-0.txt\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=names)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(charon_mod.subprocess, "run", _fake_run)

    result = sync_charon_keyshares_to_vc("Lodestar", keys_dir=str(keys))
    assert result["status"] == "copied"
    assert result["method"] == "import"
    assert any("lodestar" in str(c) and "validator" in str(c) and "import" in str(c) for c in calls)
    assert any("--passphraseFile=" in str(c) for c in calls)


def test_sync_charon_keyshares_lodestar_per_keystore_passphrases(tmp_path, monkeypatch):
    """Charon DKG uses keystore-N.txt pairs; Lodestar must not reuse keystore-0.txt for all keys."""
    from deploy import charon as charon_mod
    from deploy.charon import sync_charon_keyshares_to_vc

    keys = tmp_path / "validator_keys"
    keys.mkdir()
    for idx in range(3):
        (keys / f"keystore-{idx}.json").write_text("{}", encoding="utf-8")
        (keys / f"keystore-{idx}.txt").write_text(f"password-{idx}\n", encoding="utf-8")
    dest = tmp_path / "lodestar_validator"
    monkeypatch.setattr(charon_mod, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Lodestar": str(dest)},
    )
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "find":
            names = ""
            if "validator_keys" in cmd[2] or "charon-key-import" in cmd[2]:
                names = "\n".join(
                    sorted(
                        name
                        for name in os.listdir(keys)
                        if name.startswith("keystore-")
                    )
                )
                names += "\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=names)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(charon_mod.subprocess, "run", _fake_run)

    result = sync_charon_keyshares_to_vc("Lodestar", keys_dir=str(keys))
    assert result["status"] == "copied"
    import_calls = [
        c
        for c in calls
        if "lodestar" in str(c) and "validator" in str(c) and "import" in str(c)
    ]
    assert len(import_calls) == 3
    assert any("keystore-0.txt" in str(c) for c in import_calls)
    assert any("keystore-1.txt" in str(c) for c in import_calls)
    assert any("keystore-2.txt" in str(c) for c in import_calls)


def test_sync_charon_keyshares_lighthouse_uses_password_file(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import sync_charon_keyshares_to_vc

    keys = tmp_path / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")
    (keys / "keystore-0.txt").write_text("password\n", encoding="utf-8")
    dest = tmp_path / "lighthouse_validator"
    monkeypatch.setattr(charon_mod, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Lighthouse": str(dest)},
    )
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "find":
            names = ""
            if "validator_keys" in cmd[2] or "charon-key-import" in cmd[2]:
                names = "keystore-0.json\nkeystore-0.txt\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=names)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(charon_mod.subprocess, "run", _fake_run)
    result = sync_charon_keyshares_to_vc("Lighthouse", keys_dir=str(keys))
    assert result["status"] == "copied"
    assert any("--password-file=" in str(c) for c in calls)


def test_sync_charon_keyshares_nimbus_uses_layout_import(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import sync_charon_keyshares_to_vc

    keys = tmp_path / "validator_keys"
    keys.mkdir()
    keystore = {
        "crypto": {},
        "pubkey": "0x88a471158d618a8f9997dcb2cc1921411392d82d00e339ccf912fd9335bd42f97c9de046280d9d5f681a8e73a7d3baad",
    }
    (keys / "keystore-0.json").write_text(json.dumps(keystore), encoding="utf-8")
    (keys / "keystore-0.txt").write_text("password\n", encoding="utf-8")
    dest = tmp_path / "nimbus_validator"
    monkeypatch.setattr(charon_mod, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Nimbus": str(dest)},
    )
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "find":
            names = ""
            if "validator_keys" in cmd[2] or "charon-key-import" in cmd[2]:
                names = "keystore-0.json\nkeystore-0.txt\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=names)
        if len(cmd) >= 3 and cmd[0] == "sudo" and cmd[1] == "cat":
            target = cmd[2]
            if target.endswith("keystore-0.json"):
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(keystore))
            if target.endswith("keystore-0.txt"):
                return subprocess.CompletedProcess(cmd, 0, stdout="password\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(charon_mod.subprocess, "run", _fake_run)
    monkeypatch.setattr(charon_mod, "path_exists", lambda p, directory=False: True)
    result = sync_charon_keyshares_to_vc("Nimbus", keys_dir=str(keys))
    assert result["status"] == "copied"
    joined = " ".join(" ".join(str(part) for part in call) for call in calls)
    pubkey = "0x88a471158d618a8f9997dcb2cc1921411392d82d00e339ccf912fd9335bd42f97c9de046280d9d5f681a8e73a7d3baad"
    assert "validators" in joined and "secrets" in joined and pubkey in joined
    assert not any("nimbus_beacon_node" in str(c) for c in calls)


def test_sync_charon_keyshares_prysm_per_keystore_passphrases(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import sync_charon_keyshares_to_vc

    keys = tmp_path / "validator_keys"
    keys.mkdir()
    for idx in range(2):
        (keys / f"keystore-{idx}.json").write_text("{}", encoding="utf-8")
        (keys / f"keystore-{idx}.txt").write_text(f"password-{idx}\n", encoding="utf-8")
    dest = tmp_path / "prysm_validator"
    monkeypatch.setattr(charon_mod, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Prysm": str(dest)},
    )
    monkeypatch.setattr(charon_mod, "_prysm_wallet_exists", lambda _p: False)
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "test":
            flag, target = cmd[2], cmd[3]
            if flag == "-d":
                ok = os.path.isdir(target)
            else:
                ok = os.path.isfile(target)
            return subprocess.CompletedProcess(cmd, 0 if ok else 1)
        if len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "find":
            names = ""
            if "validator_keys" in cmd[2] or "charon-key-import" in cmd[2]:
                names = "\n".join(
                    sorted(
                        name
                        for name in os.listdir(keys)
                        if name.startswith("keystore-")
                    )
                )
                names += "\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=names)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(charon_mod.subprocess, "run", _fake_run)

    result = sync_charon_keyshares_to_vc("Prysm", keys_dir=str(keys))
    assert result["status"] == "copied"
    import_calls = [c for c in calls if "prysm-validator" in str(c) and "import" in str(c)]
    assert len(import_calls) == 2
    assert any("keystore-0.txt" in str(c) for c in import_calls)
    assert any("keystore-1.txt" in str(c) for c in import_calls)


def test_sync_charon_keyshares_teku_copies_per_keystore_passphrases(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import sync_charon_keyshares_to_vc

    keys = tmp_path / "validator_keys"
    keys.mkdir()
    for idx in range(2):
        (keys / f"keystore-{idx}.json").write_text('{"crypto":{}}', encoding="utf-8")
        (keys / f"keystore-{idx}.txt").write_text(f"password-{idx}\n", encoding="utf-8")
    dest = tmp_path / "teku_validator" / "validator_keys"
    monkeypatch.setattr(charon_mod, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        charon_mod,
        "VC_COPY_KEY_DIRS",
        {"Teku": str(dest)},
    )
    monkeypatch.setattr(charon_mod, "_chown_vc_tree", lambda *_a, **_k: None)

    def _fake_run(cmd, **kwargs):
        if len(cmd) >= 3 and cmd[0] == "sudo" and cmd[1] == "mkdir":
            os.makedirs(cmd[-1], exist_ok=True)
        elif len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "cp":
            import shutil

            shutil.copy2(cmd[3], cmd[4])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(charon_mod.subprocess, "run", _fake_run)

    result = sync_charon_keyshares_to_vc("Teku", keys_dir=str(keys))
    assert result["status"] == "copied"
    assert result["count"] == 2
    assert (dest / "keystore-0.txt").read_text(encoding="utf-8") == "password-0\n"
    assert (dest / "keystore-1.txt").read_text(encoding="utf-8") == "password-1\n"


def test_vc_already_has_keys_after_lodestar_merge(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import _vc_already_has_keys

    base = tmp_path / "lodestar_validator"
    (base / "keystores").mkdir(parents=True)
    (base / "keystores" / "keystore-0.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Lodestar": str(base)},
    )
    assert _vc_already_has_keys("Lodestar") is True


def test_vc_already_has_keys_ignores_lodestar_validator_db(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import _vc_already_has_keys

    base = tmp_path / "lodestar_validator"
    (base / "validator-db").mkdir(parents=True)
    (base / "validator-db" / "db").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Lodestar": str(base)},
    )
    assert _vc_already_has_keys("Lodestar") is False


def test_vc_already_has_keys_ignores_empty_lighthouse_datadir(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import _vc_already_has_keys

    base = tmp_path / "lighthouse_validator"
    (base / "validators").mkdir(parents=True)
    (base / "junk.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Lighthouse": str(base)},
    )
    assert _vc_already_has_keys("Lighthouse") is False


def test_import_cdvn_env_preserves_existing_beacon_endpoints(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "NETWORK=mainnet\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://lighthouse:5052\n",
        encoding="utf-8",
    )
    unit = tmp_path / "charon.service"
    unit.write_text(
        generate_charon_service("mainnet", "http://127.0.0.1:5053"),
        encoding="utf-8",
    )
    plan = import_cdvn_env_to_service(
        str(env),
        service_path=str(unit),
        apply=True,
        preserve_beacon_endpoints=True,
    )
    assert plan.beacon_node_endpoints == "http://127.0.0.1:5053"
    assert "5053" in unit.read_text(encoding="utf-8")


def test_sync_charon_keyshares_to_teku(tmp_path, monkeypatch):
    from deploy import charon as charon_mod
    from deploy.charon import sync_charon_keyshares_to_vc

    keys = tmp_path / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text('{"crypto":{}}', encoding="utf-8")
    (keys / "keystore-0.txt").write_text("password\n", encoding="utf-8")
    dest = tmp_path / "teku_validator" / "validator_keys"
    monkeypatch.setattr(charon_mod, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        charon_mod,
        "VC_COPY_KEY_DIRS",
        {"Teku": str(dest)},
    )
    monkeypatch.setattr(charon_mod, "_chown_vc_tree", lambda *_a, **_k: None)

    def _fake_run(cmd, **kwargs):
        if len(cmd) >= 3 and cmd[0] == "sudo" and cmd[1] == "mkdir":
            os.makedirs(cmd[-1], exist_ok=True)
        elif len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "cp":
            import shutil

            shutil.copy2(cmd[3], cmd[4])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(charon_mod.subprocess, "run", _fake_run)

    result = sync_charon_keyshares_to_vc("Teku", keys_dir=str(keys))
    assert result["status"] == "copied"
    assert result["count"] == 1
    assert result["dest"] == str(dest)
    assert (dest / "keystore-0.json").is_file()
    assert (dest / "keystore-0.txt").is_file()


def test_sync_charon_keyshares_root_owned_source(tmp_path, monkeypatch):
    """Charon validator_keys is charon:charon mode 700 — sync must use sudo."""
    from deploy import charon as charon_mod
    from deploy.charon import sync_charon_keyshares_to_vc

    keys = tmp_path / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")
    (keys / "keystore-0.txt").write_text("password\n", encoding="utf-8")
    dest = tmp_path / "lodestar_validator"
    monkeypatch.setattr(charon_mod, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        charon_mod,
        "VC_IMPORT_DATA_DIRS",
        {"Lodestar": str(dest)},
    )
    real_isdir = charon_mod.os.path.isdir
    real_listdir = charon_mod.os.listdir

    def _isdir(path):
        if os.path.abspath(path) == os.path.abspath(str(keys)):
            return False
        return real_isdir(path)

    def _listdir(path):
        if os.path.abspath(path) == os.path.abspath(str(keys)):
            raise PermissionError(path)
        return real_listdir(path)

    monkeypatch.setattr(charon_mod.os.path, "isdir", _isdir)
    monkeypatch.setattr(charon_mod.os, "listdir", _listdir)

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["sudo", "test", "-d"]:
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[:4] == ["sudo", "find", str(keys), "-mindepth"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="keystore-0.json\nkeystore-0.txt\n"
            )
        if len(cmd) >= 4 and cmd[0] == "sudo" and cmd[1] == "find":
            return subprocess.CompletedProcess(cmd, 0, stdout="keystore-0.txt\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(charon_mod.subprocess, "run", _fake_run)

    result = sync_charon_keyshares_to_vc("Lodestar", keys_dir=str(keys))
    assert result["status"] == "copied"
    assert any(cmd[:3] == ["sudo", "test", "-d"] for cmd in calls)
    assert any("lodestar" in str(c) and "import" in str(c) for c in calls)


def test_copy_charon_cluster_and_skip(tmp_path):
    from deploy.charon import copy_charon_cluster

    src = tmp_path / "src" / ".charon"
    src.mkdir(parents=True)
    (src / "cluster-lock.json").write_text('{"cluster":1}', encoding="utf-8")
    (src / "validator_keys").mkdir()
    (src / "validator_keys" / "keystore-0.json").write_text("{}", encoding="utf-8")

    dest = tmp_path / "dest" / ".charon"
    result = copy_charon_cluster(str(src), str(dest))
    assert result["status"] == "copied"
    assert (dest / "cluster-lock.json").is_file()
    assert (dest / "validator_keys" / "keystore-0.json").is_file()

    skipped = copy_charon_cluster(str(src), str(dest), force=False)
    assert skipped["status"] == "skipped"

    (src / "cluster-lock.json").write_text('{"cluster":2}', encoding="utf-8")
    forced = copy_charon_cluster(str(src), str(dest), force=True)
    assert forced["status"] == "copied"
    assert (dest / "cluster-lock.json").read_text(encoding="utf-8") == '{"cluster":2}'


def test_list_distributed_validator_pubkeys_from_cluster_lock(tmp_path: Path):
    from deploy.charon import list_distributed_validator_pubkeys

    cluster = tmp_path / ".charon"
    cluster.mkdir()
    pk = "0x88A471158D618A8F9997DCB2CC1921411392D82D00E339CCF912FD9335BD42F97C9DE046280D9D5F681A8E73A7D3BAAD"
    (cluster / "cluster-lock.json").write_text(
        '{"distributed_validators": [{"distributed_public_key": "' + pk + '"}]}',
        encoding="utf-8",
    )
    pubkeys = list_distributed_validator_pubkeys(str(cluster))
    assert pubkeys == [pk.lower()]


def test_list_distributed_validator_pubkeys_empty_when_missing(tmp_path: Path):
    from deploy.charon import list_distributed_validator_pubkeys

    assert list_distributed_validator_pubkeys(str(tmp_path / "nope")) == []


def test_lookup_validator_indices_batch(monkeypatch):
    from deploy.charon import lookup_validator_indices
    from urllib.request import Request
    import urllib.request as ur

    pk1 = "0x" + "11" * 48
    pk2 = "0x" + "22" * 48
    payload = {
        "data": [
            {"index": "101", "validator": {"pubkey": pk1}},
            {"index": "202", "validator": {"pubkey": pk2.upper()}},
        ]
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(req, timeout=0):  # noqa: ARG001
        assert isinstance(req, Request)
        assert "validators?" in req.full_url
        return _Resp()

    monkeypatch.setattr(ur, "urlopen", fake_urlopen)
    result = lookup_validator_indices([pk1, pk2], "http://127.0.0.1:5052")
    assert result["found"] == 2
    assert result["indices"][pk1] == "101"
    assert result["indices"][pk2] == "202"


def test_lookup_validator_indices_connection_error(monkeypatch):
    from deploy.charon import lookup_validator_indices
    import urllib.error
    import urllib.request as ur

    def boom(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ur, "urlopen", boom)
    result = lookup_validator_indices(["0x" + "aa" * 48], "http://127.0.0.1:5052")
    assert result["found"] == 0
    assert "error" in result


def test_patch_beacon_endpoints_adds_timeout_without_timeoutstopsec(tmp_path):
    """Hand-edited units may lack TimeoutStopSec=; TimeoutStartSec must still be added."""
    legacy = "\n".join(
        line
        for line in CHARON_UNIT.splitlines()
        if not line.startswith(("ExecStartPre=", "TimeoutStartSec=", "TimeoutStopSec="))
    )
    service_path = tmp_path / "charon.service"
    service_path.write_text(legacy + "\n", encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://10.0.0.5:5052")
    updated = service_path.read_text(encoding="utf-8")
    assert "[Service]\nTimeoutStartSec=infinity" in updated
    assert updated.count("TimeoutStartSec=") == 1


def test_patch_beacon_endpoints_keeps_existing_timeout(tmp_path):
    unit = CHARON_UNIT.replace("TimeoutStartSec=infinity", "TimeoutStartSec=600")
    assert "TimeoutStartSec=600" in unit
    service_path = tmp_path / "charon.service"
    service_path.write_text(unit, encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://10.0.0.5:5052")
    updated = service_path.read_text(encoding="utf-8")
    assert "TimeoutStartSec=600" in updated
    assert "TimeoutStartSec=infinity" not in updated
