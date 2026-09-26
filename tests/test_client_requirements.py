"""Tests for client_requirements.py."""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import client_requirements

SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "client_requirements.py")


def test_fusaka_min_versions_has_no_duplicate_keys():
    # A duplicate key in a dict literal silently overrides the earlier entry.
    tree = ast.parse(open(SOURCE).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FUSAKA_MIN_VERSIONS" for t in node.targets
        ):
            keys = [k.value for k in node.value.keys]
            assert len(keys) == len(set(keys)), sorted(k for k in keys if keys.count(k) > 1)
            return
    raise AssertionError("FUSAKA_MIN_VERSIONS not found")


def test_prysm_min_version():
    assert client_requirements.FUSAKA_MIN_VERSIONS["prysm"] == "v7.0.0"
