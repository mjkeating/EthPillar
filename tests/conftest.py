"""
Shared pytest configuration for the EthPillar test suite.

Live tests (``@pytest.mark.live``) hit real upstream APIs and are skipped during
the default unit test run. Enable them via ``tests/run_live_release_tests.sh``
or ``ETHPILLAR_LIVE_TESTS=1``.
"""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip ``@pytest.mark.live`` tests unless the live runner or ``-m live`` is used."""
    markexpr = config.getoption("-m", default="") or ""
    if "live" in markexpr or os.environ.get("ETHPILLAR_LIVE_TESTS") == "1":
        return

    skip = pytest.mark.skip(
        reason="live tests skipped by default; run tests/run_live_release_tests.sh"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
