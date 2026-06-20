#!/usr/bin/env bash
# Shared helpers for act smoke tests that use Docker bind mounts from inside act.
set -euo pipefail

act_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  git -C "${script_dir}" rev-parse --show-toplevel 2>/dev/null \
    || (cd "${script_dir}/../../.." && pwd)
}

# act on Windows/WSL runs inside a Linux container but Docker Desktop bind mounts must use
# a path the host daemon understands (/mnt/c/... from act is invisible to Docker Desktop).
host_bind_source() {
  local path="$1"
  if [[ "${ACT:-}" == "true" && "${path}" =~ ^/mnt/([a-zA-Z])/(.*)$ ]]; then
    printf '//%s/%s' "${BASH_REMATCH[1],,}" "${BASH_REMATCH[2]}"
    return
  fi
  printf '%s' "${path}"
}
