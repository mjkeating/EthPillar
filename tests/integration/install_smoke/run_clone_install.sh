#!/usr/bin/env bash
# Simulate: git clone to a non-default path, then ./install.sh
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export ETHPILLAR_INSTALL_NONINTERACTIVE=1

CUSTOM_REPO="/opt/ethpillar-custom"
rm -rf "${CUSTOM_REPO}"
cp -a /ethpillar "${CUSTOM_REPO}"

bash "${CUSTOM_REPO}/install.sh"

bash /ethpillar/tests/integration/install_smoke/verify_install.sh \
  --expected-repo "${CUSTOM_REPO}" \
  --forbid-repo "${HOME}/git/ethpillar"
