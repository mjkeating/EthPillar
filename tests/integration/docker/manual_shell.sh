#!/usr/bin/env bash
# Interactive shell as the integration test user (passwordless sudo, production-shaped).
#
# Usage (from host, after starting ep-manual with systemd):
#   docker exec -it ep-manual /ethpillar/tests/integration/docker/manual_shell.sh
#
# Keep this script executable in git (100755). On Windows: git add --chmod=+x this file.
set -euo pipefail

cd /ethpillar

if [[ "$(id -u)" -eq 0 ]]; then
  # shellcheck source=setup_integration_user.sh
  source "$(dirname "${BASH_SOURCE[0]}")/setup_integration_user.sh"
  ensure_integration_user
  export ETHPILLAR_VENV="${ETHPILLAR_VENV:-/tmp/ethpillar-integration-venv}"
  home_dir="$(getent passwd "${INTEGRATION_USER}" | cut -d: -f6)"
  echo "[manual] Dropping root; shell as ${INTEGRATION_USER} (uid=${INTEGRATION_UID})"
  echo "[manual] Use sudo for systemctl; Python venv: ${ETHPILLAR_VENV}"
  exec runuser -u "${INTEGRATION_USER}" -w /ethpillar -- env \
    HOME="${home_dir}" \
    ETHPILLAR_VENV="${ETHPILLAR_VENV}" \
    USER="${INTEGRATION_USER}" \
    LOGNAME="${INTEGRATION_USER}" \
    bash -l
fi

exec bash -l
