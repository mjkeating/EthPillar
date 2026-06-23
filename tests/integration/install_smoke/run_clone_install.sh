#!/usr/bin/env bash
# Simulate: git clone to a non-default path, then ./install.sh as epstaker.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export ETHPILLAR_INSTALL_NONINTERACTIVE=1

# shellcheck source=setup_epstaker.sh
source /ethpillar/tests/integration/install_smoke/setup_epstaker.sh
ensure_epstaker

CUSTOM_REPO="/opt/ethpillar-custom"
rm -rf "${CUSTOM_REPO}"
cp -a /ethpillar "${CUSTOM_REPO}"
chown -R "${EPSTAKER_USER}:${EPSTAKER_USER}" "${CUSTOM_REPO}"

su - "$EPSTAKER_USER" -c "
  export DEBIAN_FRONTEND=noninteractive
  export ETHPILLAR_INSTALL_NONINTERACTIVE=1
  bash '${CUSTOM_REPO}/install.sh'
"

bash /ethpillar/tests/integration/install_smoke/verify_install.sh \
  --expected-repo "${CUSTOM_REPO}" \
  --forbid-repo "/home/${EPSTAKER_USER}/git/ethpillar" \
  --install-user "${EPSTAKER_USER}"
