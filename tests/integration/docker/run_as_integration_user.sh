#!/usr/bin/env bash
# Run a command as the integration test user (passwordless sudo when root setup ran).
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  # shellcheck source=setup_integration_user.sh
  source "$(dirname "${BASH_SOURCE[0]}")/setup_integration_user.sh"
  ensure_integration_user
  export ETHPILLAR_VENV="${ETHPILLAR_VENV:-/tmp/ethpillar-integration-venv}"
  exec runuser -u "${INTEGRATION_USER}" -w /ethpillar -- env \
    ETHPILLAR_VENV="${ETHPILLAR_VENV}" \
    PYTHONPATH="${PYTHONPATH:-/ethpillar/tests/integration}" \
    "$@"
fi

exec "$@"
