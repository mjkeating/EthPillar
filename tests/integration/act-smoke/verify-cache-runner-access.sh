#!/usr/bin/env bash
# Verify whether the non-root CI runner can tar cache dirs (what actions/cache/save does).
# Usage: verify-cache-runner-access.sh deny|allow
#
# Under act, deny runs inside act-cache-fixture.sh and allow inside fix-cache-permissions.sh
# (one Docker container each — avoids slow nested docker run from the act VM).
set -euo pipefail

EXPECT="${1:?Usage: $0 deny|allow}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/act-bind-mount.sh
source "${SCRIPT_DIR}/lib/act-bind-mount.sh"
REPO_ROOT="$(act_repo_root)"
cd "${REPO_ROOT}"

FIXTURE_BIN="tests/integration/act-smoke/cache/fixture_act_smoke.bin"
ARCHIVE="/tmp/act-cache-runner-access.tar"
SMOKE_BINARY="tests/integration/act-smoke/cache"
SMOKE_CHECKPOINT="tests/integration/act-smoke/checkpoint_cache"

if [[ "${ACT:-}" == "true" ]]; then
  if [[ "${EXPECT}" == deny ]]; then
    echo "OK: deny check ran during Populate caches via Docker (act-cache-fixture.sh)"
  else
    echo "OK: allow check ran during Fix cache permissions (fix-cache-permissions.sh)"
  fi
  exit 0
fi

run_as_runner() {
  if id runner &>/dev/null; then
    sudo -u runner "$@"
  elif id ubuntu &>/dev/null; then
    sudo -u ubuntu "$@"
  else
    echo "ERROR: no runner/ubuntu user found to simulate GHA non-root cache save" >&2
    exit 1
  fi
}

if [[ ! -f "${FIXTURE_BIN}" ]]; then
  echo "ERROR: missing ${FIXTURE_BIN} — run act-cache-fixture.sh first" >&2
  exit 1
fi

if [[ "${EXPECT}" == deny ]]; then
  if run_as_runner test -r "${FIXTURE_BIN}"; then
    echo "ERROR: runner can read ${FIXTURE_BIN} before chmod" >&2
    exit 1
  fi
  set +e
  run_as_runner tar -cf "${ARCHIVE}" "${SMOKE_BINARY}" "${SMOKE_CHECKPOINT}"
  TAR_RC=$?
  set -e
  if [[ "${TAR_RC}" -eq 0 ]]; then
    echo "ERROR: runner could tar caches before chmod" >&2
    exit 1
  fi
  echo "OK: runner cannot read fixture or tar caches before chmod (matches GHA failure mode)"
elif [[ "${EXPECT}" == "allow" ]]; then
  if ! run_as_runner test -r "${FIXTURE_BIN}"; then
    echo "ERROR: runner still cannot read ${FIXTURE_BIN} after chmod" >&2
    exit 1
  fi
  set +e
  run_as_runner tar -cf "${ARCHIVE}" "${SMOKE_BINARY}" "${SMOKE_CHECKPOINT}"
  TAR_RC=$?
  set -e
  if [[ "${TAR_RC}" -ne 0 ]]; then
    echo "ERROR: runner still cannot tar caches after chmod" >&2
    exit 1
  fi
  echo "OK: runner can read fixture and tar caches after chmod"
else
  echo "ERROR: unknown expectation '${EXPECT}'" >&2
  exit 1
fi

rm -f "${ARCHIVE}"
