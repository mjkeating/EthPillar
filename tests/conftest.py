"""
Shared pytest configuration for the EthPillar test suite.

Live tests (``@pytest.mark.live``) hit real upstream APIs and are skipped during
the default unit test run. Enable them via ``tests/run_live_release_tests.sh``
or ``ETHPILLAR_LIVE_TESTS=1``.
"""

import json
import os
import re
from pathlib import Path

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


def _live_tests_enabled() -> bool:
    return os.environ.get("ETHPILLAR_LIVE_TESTS") == "1"


def _client_from_nodeid(nodeid: str) -> str:
    """Best-effort client name from a parametrized node id."""
    # e.g. tests/test_release_info_live.py::test_release_info[nimbus-status-im/nimbus-eth2]
    match = re.search(r"\[([^\]\-]+)", nodeid)
    if match:
        return match.group(1).strip().lower()
    return "unknown"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Surface live-release soft-skips as GHA warnings + ci-notices rows."""
    outcome = yield
    report = outcome.get_result()
    if not _live_tests_enabled():
        return
    if report.when != "call" or report.outcome != "skipped":
        return
    reason = ""
    longrepr = report.longrepr
    if hasattr(longrepr, "reason"):
        reason = str(longrepr.reason)
    elif isinstance(longrepr, tuple) and len(longrepr) >= 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr or "skipped")
    # Default collection skip ("live tests skipped by default") is not a soft-skip.
    if "live tests skipped by default" in reason:
        return
    client = _client_from_nodeid(item.nodeid)
    try:
        from tests.integration.known_failures import (
            CiNotice,
            emit_live_skip,
            write_ci_notices_artifact,
        )
    except ImportError:
        from known_failures import (  # type: ignore
            CiNotice,
            emit_live_skip,
            write_ci_notices_artifact,
        )

    emit_live_skip(client, reason)
    notice = CiNotice(
        kind="live-skip",
        status="skipped",
        client=client,
        reason=reason,
    )
    dest = Path(
        os.environ.get(
            "ETHPILLAR_CI_NOTICES_PATH",
            "tests/integration/results/ci-notices-live.json",
        )
    )
    existing: list[CiNotice] = []
    if dest.is_file():
        try:
            payload = json.loads(dest.read_text(encoding="utf-8"))
            for row in payload.get("notices") or []:
                existing.append(
                    CiNotice(
                        kind=str(row.get("kind") or ""),
                        status=str(row.get("status") or ""),
                        id=str(row.get("id") or ""),
                        cell=str(row.get("cell") or ""),
                        client=str(row.get("client") or ""),
                        reason=str(row.get("reason") or ""),
                        issue=str(row.get("issue") or ""),
                        issue_state=str(row.get("issue_state") or ""),
                        link=str(row.get("link") or ""),
                    )
                )
        except (json.JSONDecodeError, TypeError, OSError):
            existing = []
    existing.append(notice)
    write_ci_notices_artifact(existing, dest)
