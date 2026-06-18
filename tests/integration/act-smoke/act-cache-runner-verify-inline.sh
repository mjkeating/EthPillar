#!/usr/bin/env bash
# Run inside ethpillar-rebuild with /ethpillar bind-mounted. Simulates GHA runner access.
# Usage: act-cache-runner-verify-inline.sh deny|allow
set -euo pipefail

EXPECT="${1:?Usage: $0 deny|allow}"
BINARY_ROOT="${ACT_SMOKE_BINARY_CACHE_DIR:-/ethpillar/tests/integration/act-smoke/cache}"
CHECKPOINT_ROOT="${ACT_SMOKE_CHECKPOINT_CACHE_DIR:-/ethpillar/tests/integration/act-smoke/checkpoint_cache}"
FIXTURE="${BINARY_ROOT}/fixture_act_smoke.bin"
MANIFEST="${CHECKPOINT_ROOT}/manifest.json"
# Real tar — /usr/local/sbin/tar is the integration extract-cache wrapper and is not
# what actions/cache/save uses on the runner host.
REAL_TAR="/usr/bin/tar"
TAR_PATHS=(
  "tests/integration/act-smoke/cache/fixture_act_smoke.bin"
  "tests/integration/act-smoke/checkpoint_cache/manifest.json"
  "tests/integration/act-smoke/checkpoint_cache/SEPOLIA/entries/fixture_sepolia.body"
  "tests/integration/act-smoke/checkpoint_cache/SEPOLIA/entries/fixture_sepolia.json"
  "tests/integration/act-smoke/checkpoint_cache/HOODI/entries/fixture_hoodi.body"
  "tests/integration/act-smoke/checkpoint_cache/HOODI/entries/fixture_hoodi.json"
)

id runner &>/dev/null || useradd -m runner

if [[ ! -f "${FIXTURE}" ]]; then
  echo "ERROR: missing ${FIXTURE}" >&2
  exit 1
fi

if [[ "${EXPECT}" == deny ]]; then
  if sudo -u runner test -r "${FIXTURE}"; then
    echo "ERROR: runner can read fixture before chmod" >&2
    exit 1
  fi
  if sudo -u runner "${REAL_TAR}" -cf /tmp/pre.tar -C /ethpillar "${TAR_PATHS[@]}" 2>/dev/null; then
    echo "ERROR: runner could tar fixtures before chmod" >&2
    exit 1
  fi
  echo "OK: runner cannot read fixture or tar caches before chmod (matches GHA failure mode)"
elif [[ "${EXPECT}" == "allow" ]]; then
  if ! sudo -u runner test -r "${FIXTURE}"; then
    echo "ERROR: runner still cannot read fixture after chmod" >&2
    exit 1
  fi
  if [[ -f "${MANIFEST}" ]] && ! sudo -u runner test -r "${MANIFEST}"; then
    echo "ERROR: runner still cannot read checkpoint manifest after chmod" >&2
    exit 1
  fi
  sudo -u runner "${REAL_TAR}" -cf /tmp/post.tar -C /ethpillar "${TAR_PATHS[@]}"
  echo "OK: runner can read fixture and tar caches after chmod (actions/cache/save should succeed)"
else
  echo "ERROR: unknown expectation '${EXPECT}'" >&2
  exit 1
fi
