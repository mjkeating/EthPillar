"""Unit tests for deploy.ssv config/units and orchestrator SSV gates."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.orchestrator import (
    SSV_VC_LABEL,
    get_vc_menu,
    get_vc_options_for_cc,
    is_ssv_vc_choice,
    run_install,
)
from deploy.ssv import (
    SSV_DATA_DIR,
    SSV_DKG_CONFIG_FILE,
    SSV_PASSWORD_FILE,
    SSV_PRIVATE_KEY_FILE,
    generate_ssv_config,
    generate_ssv_dkg_config,
    generate_ssv_dkg_service,
    generate_ssv_node_service,
    ssv_network_name,
)


def test_ssv_network_name_mapping():
    assert ssv_network_name("mainnet") == "mainnet"
    assert ssv_network_name("HOODI") == "hoodi"
    assert ssv_network_name("holesky") == "holesky"


def test_generate_ssv_config_paths_and_endpoints():
    cfg = generate_ssv_config(
        "hoodi",
        "http://127.0.0.1:5052",
        "ws://127.0.0.1:8546",
        p2p_external_ip="203.0.113.10",
    )
    assert "Network: hoodi" in cfg
    assert "BeaconNodeAddr: http://127.0.0.1:5052" in cfg
    assert "ETH1Addr: ws://127.0.0.1:8546" in cfg
    assert f"PrivateKeyFile: {SSV_PRIVATE_KEY_FILE}" in cfg
    assert f"PasswordFile: {SSV_PASSWORD_FILE}" in cfg
    assert f"Path: {SSV_DATA_DIR}/db" in cfg
    assert "HostAddress: 203.0.113.10" in cfg
    assert "MetricsAPIPort: 15000" in cfg
    assert "TcpPort: 13001" in cfg
    assert "UdpPort: 12001" in cfg


def test_generate_ssv_dkg_config_includes_tls_when_set():
    cfg = generate_ssv_dkg_config(
        "mainnet",
        "http://127.0.0.1:8545",
        operator_id="1",
        tls_cert_path="/var/lib/ssv/ssl/tls.crt",
        tls_key_path="/var/lib/ssv/ssl/tls.key",
    )
    # Absolute TLS paths under the datadir are rewritten to ./ relatives.
    assert "serverTLSCertPath: ./ssl/tls.crt" in cfg
    assert "serverTLSKeyPath: ./ssl/tls.key" in cfg


def test_generate_ssv_dkg_config_shares_keystore():
    cfg = generate_ssv_dkg_config(
        "mainnet",
        "http://127.0.0.1:8545",
        operator_id="42",
    )
    assert "privKey: ./encrypted_private_key.json" in cfg
    assert "privKeyPassword: ./password" in cfg
    assert "operatorID: 42" in cfg
    assert "port: 3030" in cfg
    assert "ethEndpointURL: http://127.0.0.1:8545" in cfg
    assert "outputPath: ./output" in cfg


def test_generate_ssv_services():
    node = generate_ssv_node_service("mainnet")
    assert "Description=SSV Operator Node for MAINNET" in node
    assert "User=ssv" in node
    assert "ssvnode start-node" in node
    assert f"--config={SSV_DATA_DIR}/config.yaml" in node
    assert "After=network-online.target consensus.service execution.service" in node

    dkg = generate_ssv_dkg_service("mainnet")
    assert "Description=SSV DKG Operator for MAINNET" in dkg
    assert "ssv-dkg start-operator" in dkg
    assert "--configPath=./operator.yaml" in dkg
    assert "WorkingDirectory=/var/lib/ssv" in dkg

    remote = generate_ssv_node_service("mainnet", unit_after=[])
    assert "consensus.service" not in remote
    assert "After=network-online.target\n" in remote or "After=network-online.target\r" in remote


def test_read_ssv_ports_from_config(tmp_path):
    from deploy.ssv import read_ssv_ports

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "p2p:\n  TcpPort: 13011\n  UdpPort: 12011\n"
        "MetricsAPIPort: 15011\nSSVAPIPort: 16011\n",
        encoding="utf-8",
    )
    dkg = tmp_path / "operator.yaml"
    dkg.write_text("port: 3033\n", encoding="utf-8")
    ports = read_ssv_ports(config_path=str(cfg), dkg_config_path=str(dkg), env={})
    assert ports == {
        "tcp": 13011,
        "udp": 12011,
        "metrics": 15011,
        "api": 16011,
        "dkg": 3033,
    }


def test_ssv_excluded_from_charon_signer_menu():
    menu = get_vc_options_for_cc(
        "Lighthouse", include_ssv=True, for_charon_signer=True
    )
    assert SSV_VC_LABEL not in menu
    assert is_ssv_vc_choice(SSV_VC_LABEL)


def test_ssv_on_top_level_vc_menu():
    menu = get_vc_menu(include_charon=True, include_web3signer=True, include_ssv=True)
    assert menu[-1] == SSV_VC_LABEL


def test_run_install_rejects_ssv_with_charon():
    with pytest.raises(ValueError, match="SSV Operator and Obol Charon"):
        run_install(
            role="Custom Setup",
            network="hoodi",
            ec_name=None,
            cc_name=None,
            vc_name=None,
            flags={
                "ssv": True,
                "charon": True,
                "web3signer": False,
                "validator": False,
                "validator_only": False,
                "node_only": False,
                "mevboost": False,
            },
            params={},
            env_vars={},
        )


def test_run_install_ssv_skips_validator_and_calls_install(monkeypatch):
    calls = {}

    def _fake_install(*args, **kwargs):
        calls["install"] = (args, kwargs)
        return ("v2.5.1", "/etc/systemd/system/ssv-node.service", "/etc/systemd/system/ssv-dkg.service")

    monkeypatch.setattr("deploy.ssv.install_ssv", _fake_install)
    monkeypatch.setattr("deploy.common.setup_node", lambda *a, **k: None)
    def _finish(*a, **k):
        calls["finish_args"] = a
        calls["finish"] = k

    monkeypatch.setattr("deploy.common.finish_install", _finish)
    monkeypatch.setattr("deploy.lighthouse.download_lighthouse", lambda *a, **k: "v0")
    monkeypatch.setattr(
        "deploy.lighthouse.install_lighthouse_bn",
        lambda *a, **k: "/tmp/consensus.service",
    )
    monkeypatch.setattr(
        "deploy.geth.download_and_install_geth",
        lambda *a, **k: ("v0", "/tmp/execution.service"),
    )

    flags = {
        "ssv": True,
        "charon": False,
        "web3signer": False,
        "validator": True,  # cleared by run_install when ssv is set
        "validator_only": False,
        "node_only": False,
        "mevboost": False,
        "builder_api": False,
        "epbs": False,
        "switch_client": False,
    }
    run_install(
        role="Custom Setup",
        network="hoodi",
        ec_name="Geth",
        cc_name="Lighthouse",
        vc_name=SSV_VC_LABEL,
        flags=flags,
        params={
            "fee_recipient": "0xabc",
            "graffiti": "x",
            "bn_address": "",
            "jwtsecret_path": "/tmp/jwt",
            "sync_url": "http://checkpoint",
            "el_p2p_port": "30303",
            "el_rpc_port": "8545",
            "el_max_peers": "50",
            "cl_p2p_port": "9000",
            "cl_rest_port": "5052",
            "cl_max_peers": "100",
            "mev_min_bid": "0",
            "skip_prompts": "true",
        },
        env_vars={"CL_IP_ADDRESS": "127.0.0.1", "EL_IP_ADDRESS": "127.0.0.1"},
    )
    assert "install" in calls
    assert flags["validator"] is False
    assert calls["finish"].get("ssv_enabled") is True
    # validator_enabled is a positional arg to finish_install (index 12).
    assert calls["finish_args"][12] is False


def test_run_install_ssv_passes_enable_ws_to_besu(monkeypatch):
    """Fresh SSV install opts Besu into localhost WS; non-SSV does not."""
    el_calls = []

    def _fake_besu(*args, **kwargs):
        el_calls.append(kwargs)
        return ("v0", "/tmp/execution.service")

    monkeypatch.setattr("deploy.besu.download_and_install_besu", _fake_besu)
    monkeypatch.setattr("deploy.ssv.install_ssv", lambda *a, **k: ("v1", "n", "d"))
    monkeypatch.setattr("deploy.common.setup_node", lambda *a, **k: None)
    monkeypatch.setattr("deploy.common.finish_install", lambda *a, **k: None)
    monkeypatch.setattr("deploy.lighthouse.download_lighthouse", lambda *a, **k: "v0")
    monkeypatch.setattr(
        "deploy.lighthouse.install_lighthouse_bn",
        lambda *a, **k: "/tmp/consensus.service",
    )
    monkeypatch.setattr(
        "deploy.lighthouse.install_lighthouse_vc",
        lambda *a, **k: "/tmp/validator.service",
    )

    params = {
        "fee_recipient": "0xabc",
        "graffiti": "x",
        "bn_address": "",
        "jwtsecret_path": "/tmp/jwt",
        "sync_url": "http://checkpoint",
        "el_p2p_port": "30303",
        "el_rpc_port": "8545",
        "el_max_peers": "50",
        "cl_p2p_port": "9000",
        "cl_rest_port": "5052",
        "cl_max_peers": "100",
        "mev_min_bid": "0",
        "skip_prompts": "true",
    }
    base_flags = {
        "charon": False,
        "web3signer": False,
        "validator": False,
        "validator_only": False,
        "node_only": False,
        "mevboost": False,
        "builder_api": False,
        "epbs": False,
        "switch_client": False,
    }

    run_install(
        role="Custom Setup",
        network="hoodi",
        ec_name="Besu",
        cc_name="Lighthouse",
        vc_name=SSV_VC_LABEL,
        flags={**base_flags, "ssv": True},
        params=params,
        env_vars={"CL_IP_ADDRESS": "127.0.0.1", "EL_IP_ADDRESS": "127.0.0.1"},
    )
    assert el_calls[-1].get("enable_ws") is True

    el_calls.clear()
    run_install(
        role="Custom Setup",
        network="hoodi",
        ec_name="Besu",
        cc_name="Lighthouse",
        vc_name="Lighthouse",
        flags={**base_flags, "ssv": False, "validator": True},
        params=params,
        env_vars={"CL_IP_ADDRESS": "127.0.0.1"},
    )
    assert el_calls[-1].get("enable_ws") is False
