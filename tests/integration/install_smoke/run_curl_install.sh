#!/usr/bin/env bash
# Simulate: curl ... | bash
# Script content comes from stdin; cwd is outside the repo.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export ETHPILLAR_INSTALL_NONINTERACTIVE=1
export ETHPILLAR_INSTALL_COPY_FROM="${ETHPILLAR_INSTALL_COPY_FROM:-/ethpillar}"

cd /tmp
bash < /ethpillar/install.sh

bash /ethpillar/tests/integration/install_smoke/verify_install.sh \
  --expected-repo "${HOME}/git/ethpillar"
