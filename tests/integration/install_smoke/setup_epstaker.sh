#!/usr/bin/env bash
# Create the non-root install-smoke user with passwordless sudo.
set -euo pipefail

EPSTAKER_USER="${EPSTAKER_USER:-epstaker}"

ensure_epstaker() {
  if ! id "$EPSTAKER_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$EPSTAKER_USER"
    echo "${EPSTAKER_USER} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/${EPSTAKER_USER}-install-smoke"
    chmod 440 "/etc/sudoers.d/${EPSTAKER_USER}-install-smoke"
  fi
}
