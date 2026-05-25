#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLOTTER="$SCRIPT_DIR/plotProcessingTimes.py"

if [[ ! -f /etc/systemd/system/execution.service ]]; then
  echo "No execution.service found. This plotter requires an installed execution client."
  echo "Press ENTER to continue."
  read -r
  exit 0
fi

missing_packages=()
python3 -c "import rich" >/dev/null 2>&1 || missing_packages+=("python3-rich")
python3 -c "import systemd.journal" >/dev/null 2>&1 || missing_packages+=("python3-systemd")

if [[ ${#missing_packages[@]} -gt 0 ]]; then
  echo "Installing execution time plotter dependencies: ${missing_packages[*]}"
  sudo apt-get update
  sudo apt-get install --no-install-recommends --no-install-suggests -y "${missing_packages[@]}"
fi

python3 "$PLOTTER" --source auto --unit execution "$@"
