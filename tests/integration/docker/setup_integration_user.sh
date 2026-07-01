#!/usr/bin/env bash
# Create the non-root integration test user with passwordless sudo.
#
# Used by the systemd integration matrix (run_test.sh) and install-smoke tests.
# UID/GID default to the host orchestrator values so the user can write the
# bind-mounted repo when tests run with a read-write mount.
set -euo pipefail

INTEGRATION_USER="${ETHPILLAR_INTEGRATION_USER:-epstaker}"
INTEGRATION_UID="${ETHPILLAR_INTEGRATION_UID:-1000}"
INTEGRATION_GID="${ETHPILLAR_INTEGRATION_GID:-1000}"

ensure_integration_user() {
  local group_name="${INTEGRATION_USER}"

  if ! getent group "${INTEGRATION_GID}" >/dev/null 2>&1; then
    groupadd -g "${INTEGRATION_GID}" "${group_name}" 2>/dev/null || true
  fi
  if getent group "${INTEGRATION_GID}" >/dev/null 2>&1; then
    group_name="$(getent group "${INTEGRATION_GID}" | cut -d: -f1)"
  fi

  if id -u "${INTEGRATION_USER}" >/dev/null 2>&1; then
    :
  elif getent passwd "${INTEGRATION_UID}" >/dev/null 2>&1; then
    local existing_name
    existing_name="$(getent passwd "${INTEGRATION_UID}" | cut -d: -f1)"
    INTEGRATION_USER="${existing_name}"
  else
    useradd -m -u "${INTEGRATION_UID}" -g "${INTEGRATION_GID}" -s /bin/bash "${INTEGRATION_USER}"
  fi

  install -m 440 /dev/stdin "/etc/sudoers.d/${INTEGRATION_USER}-integration" <<EOF
${INTEGRATION_USER} ALL=(ALL) NOPASSWD:ALL
EOF

  if getent group systemd-journal >/dev/null 2>&1; then
    usermod -aG systemd-journal "${INTEGRATION_USER}" 2>/dev/null || true
  fi
}
