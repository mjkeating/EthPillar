#!/bin/bash
# EthPillar Integration Test Orchestrator (Linux/WSL)
# ==================================================
#
# This script ensures dependencies are met and runs the Python orchestrator
# to build the test Docker image and execute the full test matrix.
#

set -e

# Keep checkpoint cache off Docker Desktop's repo bind mount (broken mkdir on WSL).
export ETHPILLAR_CHECKPOINT_CACHE_DIR="${ETHPILLAR_CHECKPOINT_CACHE_DIR:-${HOME}/.cache/ethpillar/checkpoint_cache}"
mkdir -p "$ETHPILLAR_CHECKPOINT_CACHE_DIR"

# Ensure rich is installed
if ! python3 -c "import rich" 2>/dev/null; then
    echo "Installing required Python library 'rich' for the terminal UI..."
    pip3 install rich --quiet
fi

# Pass all arguments to the Python script
python3 tests/integration/run_docker_tests.py "$@"
