#!/bin/bash
# Resolve a Python interpreter for integration/bats helpers that need PyYAML.
#
# Prefer the EthPillar project venv (created by ensure_python_deps /
# run_unit_tests.sh) so bats does not depend on system site-packages.
# Fall back to python3 (Dockerfile.test installs python3-yaml for that case).
#
# Usage:  py=$(ethpillar_test_python [repo_root])
# shellcheck disable=SC2034
ethpillar_test_python() {
  local root="${1:-}"
  local venv candidate
  if [[ -z "$root" ]]; then
    # When sourced from tests/integration/*.sh, BASH_SOURCE[0] is this file.
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fi
  if [[ -n "${ETHPILLAR_PYTHON:-}" && -x "${ETHPILLAR_PYTHON}" ]]; then
    printf '%s\n' "${ETHPILLAR_PYTHON}"
    return 0
  fi
  venv="${ETHPILLAR_VENV:-${root}/.venv}"
  candidate="${venv}/bin/python3"
  if [[ -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  printf '%s\n' "python3"
}
