#!/bin/bash
# Copyright (C) 2026  b0a7
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLOTTER="$SCRIPT_DIR/plotProcessingTimes.py"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=../../functions.sh
source "$REPO_ROOT/functions.sh"
ensure_journal_access || true

if [[ ! -f /etc/systemd/system/execution.service ]]; then
  echo "No execution.service found. This plotter requires an installed execution client."
  echo "Press ENTER to continue."
  read -r
  exit 0
fi

# Ensure 'rich' is available in the active Python environment for best plotting 
# (works with venvs).
if ! python3 -c "import rich" >/dev/null 2>&1; then
  echo "'rich' not found in the active Python environment. Attempting to install via pip..."
  if python3 -m pip install --upgrade --no-cache-dir rich; then
    echo "Installed 'rich' into the active Python environment."
  else
    echo "Failed to install 'rich'. Activate your venv and run: python -m pip install rich" >&2
    exit 1
  fi
fi

python3 "$PLOTTER" --source journalctl --unit execution "$@"
