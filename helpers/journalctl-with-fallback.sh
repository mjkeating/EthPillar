#!/bin/bash
# Invoke journalctl via bash so callers do not depend on the +x bit.
# Thin wrapper so shell callers can reuse journalctl_run.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../functions.sh
source "$SCRIPT_DIR/../functions.sh"
journalctl_run "$@"
