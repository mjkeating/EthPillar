#!/bin/bash
# Verify functions.sh can parse installed client versions and that each matches
# release_info LATEST — the same comparison update_*.sh uses before showing
# "You are already on the latest version".
set -euo pipefail

cd /ethpillar
# shellcheck source=../../functions.sh
source ./functions.sh

fail=0

# Same normalization as update_execution.sh / update_consensus.sh promptYesNo.
installed_matches_latest_tag() {
  [[ "${1#v}" == "${2#v}" ]]
}

get_latest_release_tag() {
  local client="$1"
  local tag
  tag=$(PYTHONPATH="/ethpillar" python3 -m deploy.common release_info "$client" "LATEST" | jq -r .version)
  if [[ -z "$tag" || "$tag" == "null" ]]; then
    return 1
  fi
  echo "$tag"
}

assert_matches_latest() {
  local label="$1"
  local release_client="$2"
  local installed="$3"
  local latest

  if ! latest=$(get_latest_release_tag "$release_client"); then
    echo "❌ ${label}: could not resolve LATEST release tag"
    fail=1
    return 0
  fi
  if installed_matches_latest_tag "$installed" "$latest"; then
    echo "✅ ${label} matches LATEST (${installed#v}) — update menu would show already on latest"
    return 0
  fi
  echo "❌ ${label} mismatch: installed ${installed#v}, LATEST ${latest#v}"
  fail=1
}

check_el_version() {
  [[ -f /etc/systemd/system/execution.service ]] || return 0
  local el
  el=$(grep Description= /etc/systemd/system/execution.service | awk -F= '{print $2}' | awk '{print $1}')
  # Erigon+Caplin integrated unit uses the erigon binary.
  [[ "$el" == "Erigon-Caplin" ]] && el=Erigon
  getExecutionCurrentVersion "$el"
  if [[ -z "$VERSION" || "$VERSION" == Unable* ]]; then
    echo "❌ EL version parse failed for ${el}: ${VERSION:-empty}"
    fail=1
    return 0
  fi
  echo "✅ EL ${el} version: ${VERSION}"
  assert_matches_latest "EL ${el}" "$el" "$VERSION"
}

check_cl_version() {
  [[ -f /etc/systemd/system/consensus.service ]] || return 0
  local cl
  cl=$(grep Description= /etc/systemd/system/consensus.service | awk -F= '{print $2}' | awk '{print $1}')
  if [[ "$cl" == "Caplin" ]]; then
    echo "ℹ️  Skipping Caplin version (integrated in Erigon)"
    return 0
  fi
  getClVcCurrentVersion "$cl"
  if [[ -z "$VERSION" || "$VERSION" == "NotInstalled" ]]; then
    echo "❌ CL version parse failed for ${cl}: ${VERSION:-empty}"
    fail=1
    return 0
  fi
  echo "✅ CL ${cl} version: ${VERSION}"
  assert_matches_latest "CL ${cl}" "$cl" "$VERSION"
}

check_vc_version() {
  [[ -f /etc/systemd/system/validator.service ]] || return 0
  local vc
  vc=$(grep Description= /etc/systemd/system/validator.service | awk -F= '{print $2}' | awk '{print $1}')
  getClVcCurrentVersion "$vc"
  if [[ -z "$VERSION" || "$VERSION" == "NotInstalled" ]]; then
    echo "❌ VC version parse failed for ${vc}: ${VERSION:-empty}"
    fail=1
    return 0
  fi
  echo "✅ VC ${vc} version: ${VERSION}"
  assert_matches_latest "VC ${vc}" "$vc" "$VERSION"
}

check_mevboost_version() {
  [[ -f /etc/systemd/system/mevboost.service ]] || return 0
  local installed version
  installed=$(mev-boost --version 2>&1 || true)
  if [[ -z "$installed" ]]; then
    echo "❌ MEV-Boost version parse failed: empty output"
    fail=1
    return 0
  fi
  version=$(echo "$installed" | sed 's/.*v\?\([0-9]\+\.[0-9]\+\(\.[0-9]\+\)\?\).*/\1/')
  if [[ -z "$version" || "$version" == "$installed" ]]; then
    echo "❌ MEV-Boost version parse failed: ${installed}"
    fail=1
    return 0
  fi
  echo "✅ MEV-Boost version: ${version}"
  assert_matches_latest "MEV-Boost" "mevboost" "$version"
}

echo "🔢 Verifying installed client versions (parse + LATEST match)..."
check_el_version
check_cl_version
check_vc_version
check_mevboost_version

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
