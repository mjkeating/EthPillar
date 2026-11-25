"""Tests for client_requirements.py."""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import client_requirements

SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "client_requirements.py")


def _assert_dict_literal_no_dupes(name: str) -> None:
    tree = ast.parse(open(SOURCE, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            keys = [k.value for k in node.value.keys]
            assert len(keys) == len(set(keys)), sorted(k for k in keys if keys.count(k) > 1)
            return
    raise AssertionError(f"{name} not found")


def test_fusaka_min_versions_has_no_duplicate_keys():
    # A duplicate key in a dict literal silently overrides the earlier entry.
    _assert_dict_literal_no_dupes("FUSAKA_MIN_VERSIONS")


def test_gloas_sepolia_min_versions_has_no_duplicate_keys():
    _assert_dict_literal_no_dupes("GLOAS_SEPOLIA_MIN_VERSIONS")


def test_prysm_min_version():
    assert client_requirements.FUSAKA_MIN_VERSIONS["prysm"] == "v7.0.0"


def test_sepolia_rejects_pre_gloas_lighthouse():
    ok, msg = client_requirements.validate_version_for_network(
        "lighthouse", "v8.2.3", "sepolia"
    )
    assert ok is False
    assert msg is not None
    assert "Gloas" in msg
    assert "v8.3.0" in msg


def test_sepolia_accepts_gloas_lighthouse_stable():
    ok, msg = client_requirements.validate_version_for_network(
        "lighthouse", "v8.3.0", "SEPOLIA"
    )
    assert ok is True
    assert msg is None


def test_sepolia_rejects_gloas_lighthouse_rc_when_stable_required():
    ok, msg = client_requirements.validate_version_for_network(
        "lighthouse", "v8.3.0-rc.0", "SEPOLIA"
    )
    assert ok is False
    assert msg is not None


def test_sepolia_rejects_pre_gloas_lodestar_and_teku():
    ok_ls, _ = client_requirements.validate_version_for_network(
        "lodestar", "v1.48.0", "sepolia"
    )
    ok_teku, _ = client_requirements.validate_version_for_network(
        "teku", "26.9.0", "sepolia"
    )
    assert ok_ls is False
    assert ok_teku is False


def test_sepolia_teku_epbs_floor():
    ok_x, msg = client_requirements.validate_version_for_network(
        "teku", "26.10.1", "sepolia"
    )
    assert ok_x is False
    assert msg is not None
    assert "26.11.0" in msg
    ok_ga, msg_ga = client_requirements.validate_version_for_network(
        "teku", "26.11.0", "SEPOLIA"
    )
    assert ok_ga is True
    assert msg_ga is None


def test_sepolia_nimbus_gloas_floor():
    ok_old, msg = client_requirements.validate_version_for_network(
        "nimbus", "v26.10.1", "sepolia"
    )
    assert ok_old is False
    assert msg is not None
    assert "v26.10.2" in msg
    ok_new, msg_new = client_requirements.validate_version_for_network(
        "nimbus", "v26.10.2", "SEPOLIA"
    )
    assert ok_new is True
    assert msg_new is None


def test_mainnet_not_gloas_gated():
    ok, msg = client_requirements.validate_version_for_network(
        "lighthouse", "v8.2.3", "mainnet"
    )
    assert ok is True
    assert msg is None


def test_preferred_install_tag_lighthouse_sepolia_noop_after_stable():
    # Preferred remap table is empty now that v8.3.0 is GitHub LATEST.
    assert (
        client_requirements.preferred_install_tag("lighthouse", "sepolia", "v8.2.3")
        is None
    )
    assert (
        client_requirements.preferred_install_tag("lighthouse", "sepolia", "v8.3.0")
        is None
    )
    assert (
        client_requirements.preferred_install_tag("lighthouse", "hoodi", "v8.2.3")
        is None
    )
    assert (
        client_requirements.preferred_install_tag("lodestar", "sepolia", "v1.48.0")
        is None
    )
