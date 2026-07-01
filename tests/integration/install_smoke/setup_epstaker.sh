#!/usr/bin/env bash
# Create the non-root install-smoke user with passwordless sudo.
set -euo pipefail

EPSTAKER_USER="${EPSTAKER_USER:-epstaker}"
export ETHPILLAR_INTEGRATION_USER="${EPSTAKER_USER}"

# shellcheck source=../docker/setup_integration_user.sh
source "$(dirname "${BASH_SOURCE[0]}")/../docker/setup_integration_user.sh"

ensure_epstaker() {
  ensure_integration_user
  # UID 1000 may already be "ubuntu" on ubuntu:24.04; keep smoke scripts aligned.
  EPSTAKER_USER="${INTEGRATION_USER}"
  export EPSTAKER_USER
}
