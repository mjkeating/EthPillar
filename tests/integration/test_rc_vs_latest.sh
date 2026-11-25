#!/bin/bash
# Offline RC vs LATEST update-detection check.
#
# For each client with a resolvable prerelease/RC:
#   1. Download + extract the RC binary (no systemd start / no RPC)
#   2. Parse version via getExecutionCurrentVersion / getClVcCurrentVersion (--version)
#   3. Compare to release_info LATEST using version_matches_latest
#   4. Expect a mismatch (update menu would offer the official release)
#
# Usage (inside ethpillar-test container):
#   GITHUB_TOKEN=... bash /ethpillar/tests/integration/test_rc_vs_latest.sh
set -euo pipefail

cd /ethpillar
export BASE_DIR=/ethpillar
# Do not load integration sitecustomize / download cache wrappers.
unset ENABLE_EP_CACHE
export PYTHONPATH=/ethpillar

# shellcheck source=/dev/null
source ./functions.sh

PY="${ETHPILLAR_PYTHON:-python3}"

ROOT=/tmp/rc-vs-latest-$$
BIN_ROOT="$ROOT/bins"
DL_ROOT="$ROOT/dl"
SVC_ROOT="$ROOT/svc"
mkdir -p "$BIN_ROOT" "$DL_ROOT" "$SVC_ROOT"
trap 'rm -rf "$ROOT"' EXIT

# JDK for Besu/Teku --version
if ! java -version &>/dev/null; then
  ohai "Installing JDK for Besu/Teku --version"
  updateJRE 25 2>/dev/null || \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openjdk-21-jre-headless >/dev/null 2>&1 || true
fi

fail=0
checked=0
skipped=0

write_exec_stub() {
  local svc_file="$1"
  local exec_path="$2"
  cat > "$svc_file" <<EOF
[Service]
ExecStart=$exec_path
Description=RCTest Client
EOF
}

# Install RC payload under BIN_ROOT/<client>. Prints only the binary path on stdout.
install_rc_binary() {
  local client="$1"
  local tag="$2"
  local dest_dir="$BIN_ROOT/$client"
  local work="$DL_ROOT/$client"
  local data url file bin_path
  mkdir -p "$dest_dir" "$work"
  cd "$work"

  data=$("$PY" -m deploy.common release_info "$client" "$tag")
  url=$(echo "$data" | jq -r '.download_urls[0]')
  file=$(echo "$data" | jq -r '.filenames[0]')
  [[ -n "$url" && "$url" != "null" ]] || { echo "no download url for $client $tag" >&2; return 1; }

  echo "==> Downloading $client $tag" >&2
  wget -q -O "$file" "$url" || { echo "wget failed for $url" >&2; return 1; }

  case "$client" in
    lodestar)
      # Same helper as deploy/lodestar.py and the update scripts. v2 bundles Node 26.
      ensureLibatomic || { echo "libatomic1 required to run Lodestar" >&2; return 1; }
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "lodestar" "$dest_dir/lodestar" "binary" 0 --binary-name "lodestar" >&2
      bin_path="$dest_dir/lodestar"
      ;;
    lighthouse)
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "lighthouse" "$dest_dir/lighthouse" "binary" 0 >&2
      bin_path="$dest_dir/lighthouse"
      ;;
    nimbus)
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "nimbus" "$dest_dir/nimbus_beacon_node" "binary" 1 --binary-name "nimbus_beacon_node" >&2
      bin_path="$dest_dir/nimbus_beacon_node"
      ;;
    grandine)
      if [[ "$file" == *.tar.gz || "$file" == *.zip ]]; then
        "$PY" -m deploy.common extract_and_install \
          "$work/$file" "grandine" "$dest_dir/grandine" "binary" 0 >&2
      else
        "$PY" -c "from deploy.common import install_system_binary; install_system_binary(r'$work/$file', r'$dest_dir/grandine')" >&2
      fi
      bin_path="$dest_dir/grandine"
      ;;
    prysm)
      if [[ "$file" == *.tar.gz || "$file" == *.zip ]]; then
        "$PY" -m deploy.common extract_and_install \
          "$work/$file" "prysm" "$dest_dir/prysm-beacon-chain" "binary" 0 --binary-name "beacon-chain" >&2
      else
        install -m 0755 "$work/$file" "$dest_dir/prysm-beacon-chain"
      fi
      bin_path="$dest_dir/prysm-beacon-chain"
      ;;
    teku)
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "teku" "$dest_dir" "directory" 1 >&2
      bin_path="$dest_dir/bin/teku"
      ;;
    besu)
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "besu" "$dest_dir" "directory" 1 >&2
      bin_path="$dest_dir/bin/besu"
      ;;
    nethermind)
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "nethermind" "$dest_dir" "directory" 0 >&2
      bin_path="$dest_dir/nethermind"
      ;;
    reth)
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "reth" "$dest_dir/reth" "binary" 0 >&2
      bin_path="$dest_dir/reth"
      ;;
    erigon)
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "erigon" "$dest_dir/erigon" "binary" 1 >&2
      bin_path="$dest_dir/erigon"
      ;;
    ethrex)
      "$PY" -c "from deploy.common import install_system_binary; install_system_binary(r'$work/$file', r'$dest_dir/ethrex')" >&2
      bin_path="$dest_dir/ethrex"
      ;;
    mevboost)
      "$PY" -m deploy.common extract_and_install \
        "$work/$file" "mevboost" "$dest_dir/mev-boost" "binary" 0 --binary-name "mev-boost" >&2
      bin_path="$dest_dir/mev-boost"
      ;;
    *)
      echo "unsupported client install: $client" >&2
      return 1
      ;;
  esac

  if [[ ! -e "$bin_path" ]]; then
    echo "install produced no binary at $bin_path" >&2
    return 1
  fi
  chmod +x "$bin_path" 2>/dev/null || sudo chmod +x "$bin_path"
  printf '%s\n' "$bin_path"
}

parse_installed() {
  local client="$1"
  local bin="$2"
  local display="$3"
  local layer="$4"
  local svc

  VERSION=""
  INSTALLED_COMMIT=""

  case "$layer" in
    el)
      svc="$SVC_ROOT/${client}.execution.service"
      write_exec_stub "$svc" "$bin"
      export EXEC_SERVICE_FILE="$svc"
      EL="$display"
      getExecutionCurrentVersion "$display"
      ;;
    cl)
      svc="$SVC_ROOT/${client}.consensus.service"
      write_exec_stub "$svc" "$bin"
      export CONSENSUS_SERVICE_FILE="$svc"
      getClVcCurrentVersion "$display" cl
      ;;
    mev)
      VERSION=$("$bin" --version 2>&1 | grep -oE 'v?[0-9]+\.[0-9]+(\.[0-9]+)?(-(rc|alpha|beta)[0-9A-Za-z.]*)?' | head -1 || true)
      INSTALLED_COMMIT=""
      ;;
  esac
}

check_client() {
  local row="$1"
  local client rc_tag latest status reason display layer bin

  client=$(echo "$row" | jq -r .client)
  status=$(echo "$row" | jq -r .status)
  reason=$(echo "$row" | jq -r .reason)
  rc_tag=$(echo "$row" | jq -r '.rc_tag // empty')
  latest=$(echo "$row" | jq -r '.latest // empty')

  case "$client" in
    besu) display=Besu; layer=el ;;
    nethermind) display=Nethermind; layer=el ;;
    reth) display=Reth; layer=el ;;
    erigon) display=Erigon; layer=el ;;
    ethrex) display=Ethrex; layer=el ;;
    geth) display=Geth; layer=el ;;
    lighthouse) display=Lighthouse; layer=cl ;;
    lodestar) display=Lodestar; layer=cl ;;
    teku) display=Teku; layer=cl ;;
    nimbus) display=Nimbus; layer=cl ;;
    grandine) display=Grandine; layer=cl ;;
    prysm) display=Prysm; layer=cl ;;
    mevboost) display=Mevboost; layer=mev ;;
    *) display="$client"; layer=cl ;;
  esac

  if [[ "$status" != "ok" || -z "$rc_tag" ]]; then
    echo "SKIP $client — $reason"
    skipped=$((skipped + 1))
    return 0
  fi

  echo ""
  ohai "=== $display: RC $rc_tag vs LATEST $latest ==="

  if ! bin=$(install_rc_binary "$client" "$rc_tag"); then
    echo "FAIL $display: failed to install RC $rc_tag"
    fail=$((fail + 1))
    return 0
  fi
  bin=$(printf '%s' "$bin" | tail -n 1)
  if [[ ! -x "$bin" ]]; then
    echo "FAIL $display: binary not executable at [$bin]"
    fail=$((fail + 1))
    return 0
  fi

  echo "-> binary: $bin"
  echo "-> raw version output:"
  if [[ "$client" == "geth" ]]; then
    "$bin" version 2>&1 | head -5 | sed 's/^/    /' || true
  else
    "$bin" --version 2>&1 | head -8 | sed 's/^/    /' || true
  fi

  parse_installed "$client" "$bin" "$display" "$layer"
  if [[ -z "$VERSION" || "$VERSION" == Unable* || "$VERSION" == "NotInstalled" ]]; then
    echo "FAIL $display: failed to parse installed version from binary ($VERSION)"
    fail=$((fail + 1))
    return 0
  fi

  local latest_data
  latest_data=$(PYTHONPATH=/ethpillar python3 -m deploy.common release_info "$client" "LATEST")
  TAG=$(echo "$latest_data" | jq -r .version)
  TAG_COMMIT=$(echo "$latest_data" | jq -r '.commit // empty')

  echo "-> parsed installed: ${VERSION#v}${INSTALLED_COMMIT:+ (${INSTALLED_COMMIT:0:7})}"
  echo "-> latest release:   ${TAG#v}${TAG_COMMIT:+ (${TAG_COMMIT:0:7})}"

  checked=$((checked + 1))
  if version_matches_latest "$VERSION" "$TAG" "${INSTALLED_COMMIT:-}" "${TAG_COMMIT:-}"; then
    echo "FAIL $display: version_matches_latest=TRUE (Already updated) — expected upgrade from RC"
    echo "   VERSION=$VERSION INSTALLED_COMMIT=${INSTALLED_COMMIT:-} TAG=$TAG TAG_COMMIT=${TAG_COMMIT:-}"
    fail=$((fail + 1))
  else
    echo "PASS $display: version_matches_latest=FALSE — would offer update to ${TAG#v}"
  fi
}

ohai "Discovering RC candidates (GitHub + release_info)"
mapfile -t ROWS < <(PYTHONPATH=/ethpillar python3 /ethpillar/tests/integration/find_client_rc.py)

for row in "${ROWS[@]}"; do
  [[ -n "$row" ]] || continue
  check_client "$row"
done

echo ""
ohai "Summary: checked=$checked skipped=$skipped failures=$fail"
if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
if [[ "$checked" -eq 0 ]]; then
  echo "FAIL: No clients were checked"
  exit 1
fi
echo "PASS: RC vs LATEST offline --version checks succeeded"
