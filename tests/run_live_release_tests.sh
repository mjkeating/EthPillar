#!/bin/bash
# Live release-info tests for all 12 EthPillar clients.
#
# Verifies get_client_release_info() against real GitHub / geth.ethereum.org
# endpoints (LATEST, explicit version, and older release). Requires GITHUB_TOKEN.
# Skipped by the default unit test run — invoke this script explicitly.
set -euo pipefail

cd /ethpillar
# shellcheck source=../functions.sh
source functions.sh

if ! "$ETHPILLAR_PYTHON" -c "import pytest" 2>/dev/null; then
    ohai "Installing unit test harness packages into venv"
    "$ETHPILLAR_VENV/bin/pip" install pytest pyyaml
fi

export ETHPILLAR_LIVE_TESTS=1
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    cat <<'EOF' >&2
ERROR: GITHUB_TOKEN is not set.

Live release tests call the GitHub API for 11 clients. Without a token,
GitHub allows ~60 requests/hour and tests will skip once rate-limited.

Create a token (read-only public repo access is enough):
  https://github.com/settings/tokens

Then pass it into Docker, e.g. on PowerShell:
  $env:GITHUB_TOKEN = "ghp_..."
  docker run --rm -e GITHUB_TOKEN -v "${PWD}:/ethpillar" ethpillar-test bash /ethpillar/tests/run_live_release_tests.sh

EOF
    exit 1
fi
exec "$ETHPILLAR_PYTHON" -m pytest tests/test_release_info_live.py -v "$@"
