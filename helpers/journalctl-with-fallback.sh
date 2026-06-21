#!/bin/bash
# Thin wrapper so non-shell callers (e.g. plotProcessingTimes.py) reuse journalctl_run.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../functions.sh
source "$SCRIPT_DIR/../functions.sh"
journalctl_run "$@"
