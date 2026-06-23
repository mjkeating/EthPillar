#!/usr/bin/env bash
# Simulate: curl ... | bash as a non-root user with sudo.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export ETHPILLAR_INSTALL_NONINTERACTIVE=1
export ETHPILLAR_INSTALL_COPY_FROM="${ETHPILLAR_INSTALL_COPY_FROM:-/ethpillar}"

# shellcheck source=setup_epstaker.sh
source /ethpillar/tests/integration/install_smoke/setup_epstaker.sh
ensure_epstaker

cd /tmp
su - "$EPSTAKER_USER" -c '
  export DEBIAN_FRONTEND=noninteractive
  export ETHPILLAR_INSTALL_NONINTERACTIVE=1
  export ETHPILLAR_INSTALL_COPY_FROM=/ethpillar
  cd /tmp
  bash < /ethpillar/install.sh
'

bash /ethpillar/tests/integration/install_smoke/verify_install.sh \
  --expected-repo "/home/${EPSTAKER_USER}/git/ethpillar" \
  --install-user "${EPSTAKER_USER}"
