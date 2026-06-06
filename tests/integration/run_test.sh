#!/bin/bash
# Bootstrap integration tests through the same Python dependency path as production.
set -euo pipefail

cd /ethpillar
# shellcheck source=../../functions.sh
source functions.sh
exec python3 /ethpillar/tests/integration/run_inside_docker.py "$@"
