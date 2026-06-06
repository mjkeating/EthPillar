#!/bin/bash
# Bootstrap unit tests through the same Python dependency path as production.
set -euo pipefail

cd /ethpillar
# shellcheck source=../functions.sh
source functions.sh

# Pytest is a test-harness tool (pre-installed in the image), not a runtime dep.
if ! "$ETHPILLAR_PYTHON" -c "import pytest" 2>/dev/null; then
    ohai "Installing unit test harness packages into venv"
    "$ETHPILLAR_VENV/bin/pip" install pytest pyyaml
fi

exec "$ETHPILLAR_PYTHON" -m pytest "$@"
