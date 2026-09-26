#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: EthPillar is a one-liner setup tool and node management TUI

# Made for home and solo stakers 🏠🥩

# Determine repo root if BASE_DIR is not already set by the caller
if [[ -z "${BASE_DIR:-}" ]]; then
    BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

set -u

# enable history and history expansion
set -o history -o histexpand

# Load BN and EL ENDPOINTS (integration tests may set ETHPILLAR_ENV_FILE to a sidecar env)
source "${ETHPILLAR_ENV_FILE:-./env}"

# Stores validator index
declare -a INDICES

# Colors
g="\033[32m" # Green
r="\033[31m" # Red
nc="\033[0m" # No-color
bold="\033[1m"

# Obol Charon branding (UTF-8 infinity). Guard so re-sourcing functions.sh
# (e.g. switch_client.sh after a bats setup already sourced this file) is safe.
if [[ ! -v OBOL_INF ]]; then
  readonly OBOL_INF='∞'
  readonly OBOL_MARK="${g}${OBOL_INF}${nc}"  # green ∞ for terminal output only (not whiptail)
  # Plain labels for whiptail / menus (no ANSI or UTF-8 symbols)
  readonly OBOL_CHARON_DV="Obol Charon DV"
  readonly OBOL_CHARON="Obol Charon"
  readonly OBOL_CHARON_KEY_SHARES="Obol Charon key shares"
  readonly OBOL_IMPORT_KEY_SHARES="Import Obol Charon key shares"
fi

function info {
  echo -e "${g}INFO: $1${nc}"
}

function error {
  echo -e "${r}${bold}ERROR: $1${nc}"
  exit 1
}

get_systemd_exec_path() {
  local service_file="$1"
  local default_path="$2"
  if [[ -f "$service_file" ]]; then
    local exec_start
    exec_start=$(grep -E "^ExecStart=" "$service_file" | head -n 1)
    if [[ -n "$exec_start" ]]; then
      echo "$exec_start" | sed -e 's/^ExecStart=//' | awk '{print $1}'
      return
    fi
  fi
  echo "$default_path"
}

# Default execution-client binary path (overridden by ExecStart when present).
get_execution_binary_path() {
  local el="$1"
  local svc="${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
  case "$el" in
    Nethermind) get_systemd_exec_path "$svc" "/usr/local/bin/nethermind/nethermind" ;;
    Besu)       get_systemd_exec_path "$svc" "/usr/local/bin/besu/bin/besu" ;;
    Erigon)     get_systemd_exec_path "$svc" "/usr/local/bin/erigon" ;;
    Geth)       get_systemd_exec_path "$svc" "/usr/local/bin/geth" ;;
    Reth)       get_systemd_exec_path "$svc" "/usr/local/bin/reth" ;;
    Ethrex)     get_systemd_exec_path "$svc" "/usr/local/bin/ethrex" ;;
    *)          echo "" ;;
  esac
}

# Run the client-specific version command and capture stdout/stderr.
get_execution_version_output() {
  local bin="$1"
  local el="$2"
  case "$el" in
    Geth) "$bin" version 2>&1 ;;
    *)    "$bin" --version 2>&1 ;;
  esac
}

# Extract x.y.z with optional prerelease (-rc.N / -alpha… / -beta… / -dev…) when it
# follows the client name (avoids rustc/JDK semver noise). Does not treat bare
# hex commit suffixes (e.g. Erigon 2.60.6-e3bd6a2c) as prereleases.
parse_execution_client_version() {
  local el="$1"
  local output="$2"
  local prefixes=() prefix parsed=""
  local ver_re='([0-9]+\.[0-9]+\.[0-9]+(-([rR][cC]|[aA][lL][pP][hH][aA]|[bB][eE][tT][aA]|[dD][eE][vV])[0-9A-Za-z.]*)?)'
  case "$el" in
    Geth)       prefixes=('[Gg]eth[[:space:]]*[^0-9]*') ;;
    Besu)       prefixes=('[Bb]esu[^0-9]*') ;;
    Nethermind) prefixes=('[Nn]ethermind[[:space:]]*[^0-9]*' '[Vv]ersion[[:space:]]*[^0-9]*') ;;
    Erigon)     prefixes=('[Ee]rigon[^0-9]*') ;;
    Reth)       prefixes=('[Rr]eth[^0-9]*') ;;
    Ethrex)     prefixes=('[Ee]threx[^0-9]*') ;;
    *)          echo ""; return 1 ;;
  esac
  for prefix in "${prefixes[@]}"; do
    parsed=$(printf '%s' "$output" | sed -z -nE "s/.*${prefix}v?${ver_re}.*/\\1/p" | head -1)
    if [[ -n "$parsed" ]]; then
      echo "$parsed"
      return 0
    fi
  done
  # Last resort: first x.y.z(+prerelease) in output when no client prefix matched.
  parsed=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+(-([rR][cC]|[aA][lL][pP][hH][aA]|[bB][eE][tT][aA]|[dD][eE][vV])[0-9A-Za-z.]*)?' <<< "$output" | head -1)
  if [[ -n "$parsed" ]]; then
    echo "$parsed"
    return 0
  fi
  echo ""
}

# Extract a git commit/hash from execution-client version output when present.
parse_execution_client_commit() {
  local el="$1"
  local output="$2"
  local commit=""
  case "$el" in
    Geth)
      commit=$(sed -nE 's/.*[0-9]+\.[0-9]+\.[0-9]+-[A-Za-z]+-([a-fA-F0-9]{6,40}).*/\1/p' <<< "$output" | head -1)
      ;;
    Nethermind)
      commit=$(sed -nE 's/.*[Cc]ommit:[[:space:]]*([a-fA-F0-9]{6,40}).*/\1/p' <<< "$output" | head -1)
      if [[ -z "$commit" ]]; then
        commit=$(grep -oE '\+[a-fA-F0-9]{6,40}' <<< "$output" | head -1 | tr -d '+')
      fi
      ;;
    Reth)
      commit=$(sed -nE 's/.*[Cc]ommit SHA:[[:space:]]*([a-fA-F0-9]{6,40}).*/\1/p' <<< "$output" | head -1)
      if [[ -z "$commit" ]]; then
        commit=$(grep -oE '\([a-fA-F0-9]{6,40}\)' <<< "$output" | head -1 | tr -d '()')
      fi
      ;;
    Erigon)
      # erigon version 3.4.0-rc.5-ac5e71d8  → trailing -<hash>
      commit=$(sed -nE 's/.*-([a-fA-F0-9]{7,40})[^a-fA-F0-9]*$/\1/p' <<< "$output" | head -1)
      ;;
    Ethrex)
      # ethrex/v19.0.0-HEAD-<40hex>/...  → hex after the -HEAD-/-tag- segment
      commit=$(sed -nE 's#.*[0-9]+\.[0-9]+\.[0-9]+-[A-Za-z]+-([a-fA-F0-9]{7,40}).*#\1#p' <<< "$output" | head -1)
      ;;
    *)
      commit=""
      ;;
  esac
  echo "$commit"
}

# Parse ``charon version`` stdout (e.g. ``v1.10.3 [git_commit_hash=abc,...]``).
# Requires a leading ``v`` so greedy ``1.10.3`` cannot collapse to ``0.3``.
parse_charon_version() {
  local output="$1"
  grep -oiE 'v[0-9]+\.[0-9]+(\.[0-9]+)?' <<< "$output" | head -1 || true
}

parse_charon_commit() {
  local output="$1"
  sed -nE 's/.*git_commit_hash=([a-fA-F0-9]+).*/\1/p' <<< "$output" | head -1 || true
}

# Sets VERSION (and INSTALLED_COMMIT when known) from the installed Charon binary.
getCharonCurrentVersion() {
  local charon_svc="${CHARON_SERVICE_FILE:-/etc/systemd/system/charon.service}"
  local bin output
  VERSION=""
  INSTALLED_COMMIT=""
  bin=$(get_systemd_exec_path "$charon_svc" "/usr/local/bin/charon")
  if [[ -z "$bin" || ! -x "$bin" ]]; then
    VERSION="Unable to query Charon version from binary."
    return 1
  fi
  output=$("$bin" version 2>/dev/null || true)
  VERSION=$(parse_charon_version "$output")
  INSTALLED_COMMIT=$(parse_charon_commit "$output")
  if [[ -z "$VERSION" ]]; then
    VERSION="Unable to query Charon version from binary."
    return 1
  fi
}

# Parse ``mev-boost --version`` stdout (e.g. ``mev-boost version v1.8.0``).
# Prefer a v-prefixed semver so greedy optional-v sed cannot collapse v1.8.0 to 8.0.
parse_mevboost_version() {
  local output="$1"
  local ver
  ver=$(grep -oiE 'v[0-9]+\.[0-9]+(\.[0-9]+)?' <<< "$output" | head -1 || true)
  ver="${ver#v}"
  if [[ -z "$ver" ]]; then
    ver=$(grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' <<< "$output" | head -1 || true)
  fi
  echo "$ver"
}

# Sets VERSION from the installed mev-boost binary (unit ExecStart).
getMevboostCurrentVersion() {
  local mev_svc="${MEVBOOST_SERVICE_FILE:-/etc/systemd/system/mevboost.service}"
  local bin output
  VERSION=""
  INSTALLED_COMMIT=""
  bin=$(get_systemd_exec_path "$mev_svc" "/usr/local/bin/mev-boost")
  if [[ -z "$bin" || ! -x "$bin" ]]; then
    VERSION="Unable to query mev-boost version from binary."
    return 1
  fi
  output=$("$bin" --version 2>&1 || true)
  VERSION=$(parse_mevboost_version "$output")
  if [[ -z "$VERSION" ]]; then
    VERSION="Unable to query mev-boost version from binary."
    return 1
  fi
}

# Sets VERSION (and INSTALLED_COMMIT when known) from the installed execution client binary.
getExecutionCurrentVersion() {
  local el="${1:-$EL}"
  local bin output
  VERSION=""
  INSTALLED_COMMIT=""
  bin=$(get_execution_binary_path "$el")
  if [[ -z "$bin" || ! -x "$bin" ]]; then
    VERSION="Unable to query ${el:-execution client} version from binary."
    return 1
  fi
  output=$(get_execution_version_output "$bin" "$el")
  VERSION=$(parse_execution_client_version "$el" "$output")
  INSTALLED_COMMIT=$(parse_execution_client_commit "$el" "$output")
  if [[ -z "$VERSION" ]]; then
    VERSION="Unable to query ${el} version from binary."
    return 1
  fi
}

getNetworkConfig() {
    ip_current=$( hostname --all-ip-address | awk '{print $1}')
    interface_current=$(ip route | grep default | head -1 | sed 's/.*dev \([^ ]*\) .*/\1/')
    network_current="$(ip route | grep "$interface_current" | grep -v default | head -1 | awk '{print $1}')"
    export ip_current interface_current network_current
}

exit_on_error() {
    exit_code=$1
    last_command="${@:2}"
    if [ $exit_code -ne 0 ]; then
        >&2 echo "\"${last_command}\" command failed with exit code ${exit_code}."
        exit $exit_code
    fi
}

# string formatters
if [[ -t 1 ]]; then
  tty_escape() { printf "\033[%sm" "$1"; }
else
  tty_escape() { :; }
fi
tty_mkbold() { tty_escape "1;$1"; }
tty_underline="$(tty_escape "4;39")"
tty_blue="$(tty_mkbold 34)"
tty_red="$(tty_mkbold 31)"
tty_bold="$(tty_mkbold 39)"
tty_reset="$(tty_escape 0)"

shell_join() {
  local arg
  printf "%s" "$1"
  shift
  for arg in "$@"; do
    printf " "
    printf "%s" "${arg// /\ }"
  done
}

ohai() {
  printf "${tty_blue}==>${tty_bold} %s${tty_reset}\n" "$(shell_join "$@")"
}

network_down() {
    getNetworkConfig
    sudo ip link set $interface_current down
}

network_up() {
    getNetworkConfig
    sudo ip link set $interface_current up
}

network_isConnected() {
  #check to see if the device is connected to the network
  sudo ip route get 1 2>/dev/null
}

get_arch(){
  machine_arch="$(uname --machine)"
  if [[ "${machine_arch}" = "x86_64" ]]; then
    binary_arch="amd64"
  elif [[ "${machine_arch}" = "aarch64" ]]; then
    binary_arch="arm64"
  else
    echo "Unsupported architecture: ${machine_arch}"
    exit 1
  fi
  echo "${binary_arch}"
}

get_platform(){
  platform="$(uname)"
  if [[ "${platform}" = "Linux" ]]; then
    echo "${platform}"
  else
    echo "Unsupported platform: ${platform}"
    exit 1
  fi
}

print_node_info() {
  current_time=$(date)
  os_descrip=$(grep PRETTY_NAME /etc/os-release | sed 's/PRETTY_NAME=//g')
  os_version=$(grep VERSION_ID /etc/os-release | sed 's/VERSION_ID=//g')
  kernel_version=$(uname -r)
  system_uptime=$(uptime | sed 's/.*up \([^,]*\), .*/\1/')
  chrony_status=$(if systemctl is-active --quiet chronyd ; then printf "Online" ; else printf "Offline" ; fi)
  consensus_status=$(if systemctl is-active --quiet consensus ; then printf "Online" ; elif [ -f /etc/systemd/system/consensus.service ]; then printf "Offline" ; else printf "Not Installed"; fi)
  execution_status=$(if systemctl is-active --quiet execution ; then printf "Online" ; elif [ -f /etc/systemd/system/execution.service ]; then printf "Offline" ; else printf "Not Installed"; fi)
  [[ $EL == "Erigon-Caplin" ]] && consensus_status=$execution_status
  validator_status=$(if systemctl is-active --quiet validator ; then printf "Online" ; elif [ -f /etc/systemd/system/validator.service ]; then printf "Offline" ; else printf "Not Installed"; fi)
  charon_line=""
  if [[ -f /etc/systemd/system/charon.service ]]; then
    charon_status=$(if systemctl is-active --quiet charon ; then printf "Online" ; else printf "Offline" ; fi)
    charon_line="Charon Status    :  ${charon_status}"$'\n'
  fi
  mevboost_status=$(if systemctl is-active --quiet mevboost ; then printf "Online" ; elif [ -f /etc/systemd/system/mevboost.service ]; then printf "Offline" ; else printf "Not Installed"; fi)
  ethpillar_commit=$(git -C "${BASE_DIR}" rev-parse HEAD)
  ethpillar_version=$(grep ^EP_VERSION= $BASE_DIR/ethpillar.sh | sed 's/EP_VERSION=//g')
  SERVICES=(execution consensus validator charon mevboost)
  autostart_status=()
  for UNIT in ${SERVICES[@]}
      do
        if [[ -f /etc/systemd/system/${UNIT}.service ]]; then
          autostart_status+=("${UNIT}: $(if systemctl is-enabled --quiet ${UNIT}; then printf "✔"; else printf "❌"; fi)")
        fi
      done

  info_txt=$(cat <<EOF
Current time     :  $current_time
OS Description   :  $os_descrip
OS Version       :  $os_version
Kernel Version   :  $kernel_version
Uptime           :  $system_uptime
Chrony           :  $chrony_status

Consensus Status :  $consensus_status
Execution Status :  $execution_status
Validator Status :  $validator_status
${charon_line}Mevboost Status  :  $mevboost_status
Autostart at Boot:  ${autostart_status[@]}

EthPillar Version:  $ethpillar_version
EthPillar Commit :  $ethpillar_commit
EOF
)
whiptail --title "General Node Information" --msgbox "$info_txt" 22 78
}

setWhiptailColors(){
    export NEWT_COLORS='root=,black
border=green,black
title=green,black
roottext=red,black
window=red,black
textbox=white,black
button=black,green
compactbutton=white,black
listbox=white,black
actlistbox=black,white
actsellistbox=black,green
checkbox=green,black
actcheckbox=black,green'
}

# Runs a script
runScript() {
    SCRIPT_NAME="$1"
    SCRIPT_PATH="$BASE_DIR/$SCRIPT_NAME"

    if [[ ! -x $SCRIPT_PATH ]]; then
        chmod +x $SCRIPT_PATH
    fi

    shift
    ARGUMENTS="$*"

    if [[ -f $SCRIPT_PATH && -x $SCRIPT_PATH ]]; then
        bash -c "$SCRIPT_PATH $ARGUMENTS"
    else
        echo "Error: $SCRIPT_PATH not run. Check permissions or path."
        exit 1
    fi
}

# Calculates Ephemery ChainID
getEphemeryChainID(){
    _dateToIteration(){
        echo "$(( ($1 - 1393527600) / $GENESIS_INTERVAL ))"
    }

    GENESIS_INTERVAL="2419200"
    ITERATION_NUMBER=$(_dateToIteration $(date +%s))
    EPH_CHAIN_ID=$(expr 39438000 + "$ITERATION_NUMBER")
}

# Infer network from installed systemd units when no local execution client exists.
_network_from_systemd_units(){
    local svc path net_raw
    for svc in validator consensus charon execution; do
        path="/etc/systemd/system/${svc}.service"
        [[ -f "$path" ]] || continue
        net_raw=$(grep -oEi '\-\-network[= ]([a-zA-Z0-9_-]+)' "$path" 2>/dev/null | head -1 \
            | sed -E 's/^.*=//; s/^.* //')
        if [[ -z "$net_raw" ]]; then
            net_raw=$(grep -oEi 'for ([A-Z][A-Z0-9_-]+)' "$path" 2>/dev/null | head -1 | awk '{print $2}')
        fi
        [[ -n "$net_raw" ]] || continue
        case "${net_raw,,}" in
        mainnet)  NETWORK="Mainnet";;
        holesky)  NETWORK="Holesky";;
        hoodi)    NETWORK="Hoodi";;
        sepolia)  NETWORK="Sepolia";;
        ephemery) NETWORK="Ephemery";;
        *)
            NETWORK="$(echo "$net_raw" | awk '{print toupper(substr($0,1,1)) tolower(substr($0,2))}')"
            ;;
        esac
        return 0
    done
    return 1
}

getNetwork(){
    local exec_svc="${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
    local result=""

    if [[ -f "$exec_svc" ]]; then
        result=$(curl -sf --connect-timeout 1 --max-time 2 -X POST -H "Content-Type: application/json" \
            --data '{"jsonrpc":"2.0","method":"net_version","params":[],"id":67}' \
            "${EL_RPC_ENDPOINT}" 2>/dev/null | jq -r '.result')
    elif _network_from_systemd_units; then
        export NETWORK
        return
    fi

    if [[ -z $result ]]; then NETWORK="Network Syncing"; export NETWORK; return; fi
    case $result in
    1)
      NETWORK="Mainnet"
      ;;
    17000)
      NETWORK="Holesky"
      ;;
    560048)
      NETWORK="Hoodi"
      ;;
    11155111)
      NETWORK="Sepolia"
      ;;
    *)
      getEphemeryChainID
      if [[ "$result" = "$EPH_CHAIN_ID" ]]; then
        NETWORK="Ephemery"
      else
        NETWORK="Custom Network"
      fi
    esac
    export NETWORK
}

# Ensure Python runtime dependencies are available in a project venv.
# Ubuntu 24.04+ blocks system-wide/user pip installs (PEP 668).
ensure_python_deps() {
    local req_file="${BASE_DIR}/requirements.txt"
    local venv_dir="${ETHPILLAR_VENV:-${BASE_DIR}/.venv}"
    local py_version venv_python venv_pip
    [[ -f "$req_file" ]] || error "requirements.txt not found in ${BASE_DIR}"

    # venv creation needs ensurepip (provided by python3-venv / python3.X-venv)
    if ! python3 -c "import ensurepip" &>/dev/null; then
        ohai "Installing python3-venv"
        sudo apt-get update -qq
        py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
            python3-venv "python${py_version}-venv" python3-pip
        python3 -c "import ensurepip" &>/dev/null || error "python3-venv is required but ensurepip is still unavailable"
    fi

    # Recreate venv if missing or incomplete (can happen when ensurepip was absent)
    if [[ ! -x "${venv_dir}/bin/python3" || ! -x "${venv_dir}/bin/pip" ]]; then
        [[ -d "$venv_dir" ]] && rm -rf "$venv_dir"
        ohai "Creating Python virtual environment"
        python3 -m venv "$venv_dir" || error "Failed to create Python virtual environment"
        if [[ ! -x "${venv_dir}/bin/pip" ]]; then
            "${venv_dir}/bin/python3" -m ensurepip --upgrade || error "Failed to bootstrap pip in virtual environment"
        fi
    fi

    venv_python="${venv_dir}/bin/python3"
    venv_pip="${venv_dir}/bin/pip"

    # Check if any packages are missing
    local missing=()
    local pkg import_name
    while IFS= read -r pkg || [[ -n "$pkg" ]]; do
        [[ "$pkg" =~ ^#.*$ ]] && continue
        [[ -z "$pkg" ]] && continue
        import_name="$pkg"
        case "$pkg" in
            console-menu) import_name="consolemenu" ;;
            python-dotenv) import_name="dotenv" ;;
        esac
        if ! PYTHONPATH="${BASE_DIR}" "$venv_python" -c "import ${import_name}" 2>/dev/null; then
            missing+=("$pkg")
        fi
    done < "$req_file"

    if [[ ${#missing[@]} -gt 0 ]]; then
        ohai "Installing missing Python packages: ${missing[*]}"
        "$venv_pip" install -r "$req_file" || error "Failed to install Python dependencies"
    fi

    export ETHPILLAR_VENV="$venv_dir"
    export ETHPILLAR_PYTHON="$venv_python"
    export PATH="${venv_dir}/bin:${PATH}"
}

# Print EP_VERSION from origin/main:ethpillar.sh (requires a prior git fetch).
getEthPillarRemoteVersion() {
    git -C "${BASE_DIR}" show origin/main:ethpillar.sh 2>/dev/null \
        | grep '^EP_VERSION=' | cut -d'"' -f2
}

# Quiet-fetch origin/main and print remote EP_VERSION. Returns 1 on fetch/show failure.
fetch_ethpillar_remote_version() {
    git -C "${BASE_DIR}" fetch origin main --quiet || return 1
    local latest
    latest=$(getEthPillarRemoteVersion) || return 1
    [[ -n "$latest" ]] || return 1
    echo "$latest"
}

# Query deploy.common release_info LATEST. Sets TAG, TAG_COMMIT, RELEASE_DATA.
# Usage: fetch_latest_release <client> [--strip-v]
# Returns 1 if the request fails or version is missing (does not exit).
fetch_latest_release() {
    local client="$1"
    local strip_v=0
    local data
    [[ "${2:-}" == "--strip-v" ]] && strip_v=1
    TAG=""
    TAG_COMMIT=""
    RELEASE_DATA=""
    data=$(PYTHONPATH="${BASE_DIR}" "${ETHPILLAR_PYTHON:-python3}" -m deploy.common release_info "$client" "LATEST") || return 1
    RELEASE_DATA="$data"
    TAG=$(echo "$data" | jq -r .version)
    TAG_COMMIT=$(echo "$data" | jq -r '.commit // empty')
    if [[ -z "$TAG" || "$TAG" == "null" ]]; then
        return 1
    fi
    if [[ "$strip_v" -eq 1 ]]; then
        TAG="${TAG#v}"
    fi
    return 0
}

# Apply EthPillar self-update: fetch origin/main, check out main, fast-forward
# pull, hard-reset to HEAD, remove untracked and ignored files (git clean -xdf,
# incl. .venv), refresh Python deps. Preserves .env.overrides across the clean.
# Shared by System Administration → Update EthPillar and `ethpillar upgrade ethpillar`.
upgradeEthPillar() {
    cd "${BASE_DIR}" || return 1
    git fetch origin main || return 1
    [[ -f .env.overrides ]] && cp .env.overrides /tmp/env.overrides.backup
    git checkout main || return 1
    git pull --ff-only || return 1
    git reset --hard || return 1
    git clean -xdf || return 1
    ensure_python_deps || return 1
    [[ -f /tmp/env.overrides.backup ]] && mv /tmp/env.overrides.backup .env.overrides
    return 0
}

# Ensure a Java runtime is available for JVM-based clients (Teku, Besu)
updateJRE(){
  # Delegate Java installation to the Python helper in deploy.common. This
  # centralizes logic and allows the Python modules to call the same helper.
  # First argument is the minimum required Java major version (defaults to 21);
  # the helper upgrades the runtime when the installed one is too old.
  # Returns non-zero if a suitable Java could not be made available, so callers
  # can abort before replacing a working client binary.
  local min_version="${1:-21}"
  PYTHONPATH="${BASE_DIR}" python3 - "$min_version" <<'PY'
import sys
from deploy.common import ensure_java_available
sys.exit(0 if ensure_java_available(int(sys.argv[1])) else 1)
PY
}

# Install libjemalloc-dev so JVM clients (Teku, Besu) can LD_PRELOAD it on start.
# Debian's -dev package provides the unversioned libjemalloc.so symlink the
# start scripts look for. Returns non-zero if apt could not install it.
ensureJemalloc(){
  PYTHONPATH="${BASE_DIR}" python3 - <<'PY'
import sys
from deploy.common import ensure_jemalloc
sys.exit(0 if ensure_jemalloc() else 1)
PY
}

# Official Lodestar Version suffix is immediately after the semver:
#   v1.48.0/c7dc2b0  or  v1.8.0/stable/a4b29cf
# A last-/hex scan treats git branch metadata as a commit
# (v1.48.0/cursor/cli-upgrade-skip-when-latest-0415/14901a2 → 14901a2)
# and then version_matches_latest false-negatives against the GitHub tag peel.
# Branch paths leave INSTALLED_COMMIT empty so matching falls back to semver.
parse_lodestar_installed_commit() {
  local version_line="${1:-}"
  local version="${2:-}"
  local suffix=""
  [[ -n "$version" ]] || return 0
  suffix="${version_line#*"${version}"}"
  if [[ "$suffix" =~ ^/([a-fA-F0-9]{7,40})([^a-fA-F0-9]|$) ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "$suffix" =~ ^/(stable|unstable|dev|nightly|alpha|beta|rc)/([a-fA-F0-9]{7,40})([^a-fA-F0-9]|$) ]]; then
    echo "${BASH_REMATCH[2]}"
    return 0
  fi
}

# Lodestar readAndGetGitData() prefers live `git rev-parse` in cwd over baked
# .git-data.json. ethpillar.sh cds to BASE_DIR, so `--version` from the repo
# reports the EthPillar branch/tip. Run from a directory that is not a git
# worktree (default /tmp). Override via LODESTAR_VERSION_CWD in tests.
get_lodestar_version_output() {
  local bin="$1"
  local cwd="${LODESTAR_VERSION_CWD:-/tmp}"
  [[ -d "$cwd" ]] || cwd=/tmp
  (cd "$cwd" && "$bin" --version) 2>&1 || true
}

# Gets installed CL or VC version from binary.
# Args: client (optional, defaults to CLIENT from getClient), role cl|vc (optional, defaults to cl).
# Use role=cl for consensus/beacon (consensus.service); role=vc for validator (validator.service).
# Sets VERSION and INSTALLED_COMMIT (empty when unknown).
getClVcCurrentVersion(){
    local client="${1:-$CLIENT}"
    local role="${2:-cl}"
    local consensus_svc="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
    local validator_svc="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"
    local svc_file
    local raw_version=""
    local version_line=""
    if [[ "$role" == "vc" ]]; then
        svc_file="$validator_svc"
    else
        svc_file="$consensus_svc"
    fi
    VERSION="NotInstalled"
    INSTALLED_COMMIT=""
    case "$client" in
      Lighthouse)
        LH_BIN=$(get_systemd_exec_path "$svc_file" "/usr/local/bin/lighthouse")
        raw_version=$("$LH_BIN" --version 2>&1 | head -1 || true)
        VERSION=$(grep -oiE 'v[0-9]+\.[0-9]+\.[0-9]+(-(rc|alpha|beta|dev)[0-9A-Za-z.]*)?' <<< "$raw_version" | head -1 || true)
        # Trailing short hex commit: v5.2.1-abc1234 or v8.0.0-rc.2-b59feb0
        if [[ "$raw_version" =~ -([a-fA-F0-9]{7,40})[[:space:]]*$ ]]; then
          INSTALLED_COMMIT="${BASH_REMATCH[1]}"
        fi
        ;;
      Lodestar)
        LODESTAR_BIN=$(get_systemd_exec_path "$svc_file" "/usr/local/bin/lodestar")
        raw_version=$(get_lodestar_version_output "$LODESTAR_BIN")
        # Official: "* Version: v1.48.0/c7dc2b0" or "v1.8.0/stable/a4b29cf".
        # Only the Version line. Commit must sit immediately after the semver
        # (or after a single channel token). Do not take the last /hex on the
        # line — branch metadata like /cursor/.../14901a2 is not TAG_COMMIT.
        version_line=$(grep -iE 'Version:[[:space:]]*v?[0-9]+\.[0-9]+\.[0-9]+' <<< "$raw_version" | head -1 || true)
        if [[ -z "$version_line" ]]; then
          version_line=$(grep -iE 'v[0-9]+\.[0-9]+\.[0-9]+' <<< "$raw_version" | head -1 || true)
        fi
        VERSION=$(grep -oiE 'v[0-9]+\.[0-9]+\.[0-9]+(-(rc|alpha|beta|dev)[0-9A-Za-z.]*)?' <<< "$version_line" | head -1 || true)
        INSTALLED_COMMIT=$(parse_lodestar_installed_commit "$version_line" "$VERSION")
        ;;
      Teku)
        TEKU_BIN=$(get_systemd_exec_path "$svc_file" "/usr/local/bin/teku/bin/teku")
        raw_version=$("$TEKU_BIN" --version 2>&1 | head -1 || true)
        VERSION=$(grep -oiE 'v[0-9]+\.[0-9]+\.[0-9]+(-(rc|alpha|beta|dev)[0-9A-Za-z.]*)?' <<< "$raw_version" | head -1 || true)
        ;;
      Nimbus)
        if [[ "$role" == "vc" ]]; then
          NIMBUS_BIN=$(get_systemd_exec_path "$validator_svc" "/usr/local/bin/nimbus_validator_client")
        else
          NIMBUS_BIN=$(get_systemd_exec_path "$consensus_svc" "/usr/local/bin/nimbus_beacon_node")
        fi
        raw_version=$("$NIMBUS_BIN" --version 2>&1 | head -1 || true)
        VERSION=$(grep -oiE 'v[0-9]+\.[0-9]+\.[0-9]+(-(rc|alpha|beta|dev)[0-9A-Za-z.]*)?' <<< "$raw_version" | head -1 || true)
        # Nimbus beacon node v0.6.6-00aedddf  → trailing -<hash>
        if [[ "$raw_version" =~ -([a-fA-F0-9]{7,40})[[:space:]]*$ ]]; then
          INSTALLED_COMMIT="${BASH_REMATCH[1]}"
        fi
        ;;
      Grandine)
        GRANDINE_BIN=$(get_systemd_exec_path "$consensus_svc" "/usr/local/bin/grandine")
        raw_version=$("$GRANDINE_BIN" --version 2>&1 | head -1 || true)
        VERSION=$(grep -oiE 'v?[0-9]+\.[0-9]+\.[0-9]+(-(rc|alpha|beta|dev)[0-9A-Za-z.]*)?' <<< "$raw_version" | head -1 || true)
        if [[ -n "$VERSION" && $VERSION != v* ]]; then VERSION="v$VERSION"; fi
        ;;
      Prysm)
        if [[ "$role" == "vc" ]]; then
          PRYSM_BIN=$(get_systemd_exec_path "$validator_svc" "/usr/local/bin/prysm-validator")
        else
          PRYSM_BIN=$(get_systemd_exec_path "$consensus_svc" "/usr/local/bin/prysm-beacon-chain")
        fi
        raw_version=$("$PRYSM_BIN" --version 2>&1 | head -1 || true)
        VERSION=$(grep -oiE 'v[0-9]+\.[0-9]+\.[0-9]+(-(rc|alpha|beta|dev)[0-9A-Za-z.]*)?' <<< "$raw_version" | head -1 || true)
        # Prysm/v7.1.2-rc.0/<40hex>. Built at: ...  → hex after last slash
        INSTALLED_COMMIT=$(grep -oE '/[a-fA-F0-9]{7,40}' <<< "$raw_version" | tail -1 | tr -d '/' || true)
        ;;
      *)
        echo "ERROR: Unable to determine client."
        exit 1
        ;;
      esac
}

# True when installed VERSION matches TAG (after stripping leading v).
# When both INSTALLED_COMMIT and TAG_COMMIT are set, also require a case-insensitive
# prefix match either way (short hash vs full SHA). Missing commits → semver-only.
version_matches_latest() {
  local installed="${1:-$VERSION}"
  local latest="${2:-$TAG}"
  local inst_commit="${3:-${INSTALLED_COMMIT:-}}"
  local tag_commit="${4:-${TAG_COMMIT:-}}"
  local inst_lc tag_lc

  [[ "${installed#v}" == "${latest#v}" ]] || return 1

  if [[ -n "$inst_commit" && -n "$tag_commit" ]]; then
    inst_lc="${inst_commit,,}"
    tag_lc="${tag_commit,,}"
    [[ "$tag_lc" == "$inst_lc"* || "$inst_lc" == "$tag_lc"* ]] || return 1
  fi
  return 0
}

# Print "1.45.0" or "1.45.0 (668ea9d)" from a version and optional commit.
format_version_label() {
  local ver="${1#v}"
  local commit="${2:-}"
  if [[ -n "$commit" ]]; then
    echo "${ver} (${commit:0:7})"
  else
    echo "$ver"
  fi
}

# Load installed + LATEST fields for one stack target (execution/consensus/
# validator/mevboost/charon). Sets VERSION, INSTALLED_COMMIT, TAG, TAG_COMMIT
# via the same TUI helpers promptYesNo uses (get*CurrentVersion + release_info).
# Returns 0 when fields are resolved, 1 on error.
load_client_versions() {
    local target="$1"
    local release_client

    VERSION=""
    INSTALLED_COMMIT=""
    TAG=""
    TAG_COMMIT=""

    case "$target" in
        execution)
            getClient
            if [[ -z "${EL:-}" ]]; then
                echo "execution: not installed"
                return 1
            fi
            release_client="$EL"
            [[ "$release_client" == "Erigon-Caplin" ]] && release_client="Erigon"
            getExecutionCurrentVersion "$release_client" || true
            fetch_latest_release "$release_client" || {
                echo "execution ($EL): could not resolve LATEST"
                return 1
            }
            ;;
        consensus)
            getClient
            if [[ -z "${CL:-}" ]]; then
                echo "consensus: not installed"
                return 1
            fi
            # Same call as update_consensus.sh before promptYesNo.
            CLIENT="$CL"
            getClVcCurrentVersion || true
            fetch_latest_release "${CLIENT,,}" || {
                echo "consensus ($CL): could not resolve LATEST"
                return 1
            }
            ;;
        validator)
            getClient
            if [[ -z "${VC:-}" ]]; then
                echo "validator: not installed"
                return 1
            fi
            # Same call as update_validator.sh before promptYesNo.
            CLIENT="$VC"
            getClVcCurrentVersion "$CLIENT" vc || true
            fetch_latest_release "${CLIENT,,}" || {
                echo "validator ($VC): could not resolve LATEST"
                return 1
            }
            ;;
        mevboost)
            getMevboostCurrentVersion || true
            [[ -z "$VERSION" ]] && VERSION="unknown"
            fetch_latest_release "mevboost" --strip-v || {
                echo "mevboost: could not resolve LATEST"
                return 1
            }
            ;;
        charon)
            getCharonCurrentVersion || true
            [[ -z "$VERSION" ]] && VERSION="unknown"
            fetch_latest_release "charon" --strip-v || {
                echo "charon: could not resolve LATEST"
                return 1
            }
            ;;
        *)
            echo "Unsupported check target: $target" >&2
            return 1
            ;;
    esac
}

# Read clients from systemd config files
getClient(){
    local exec_svc="${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
    local consensus_svc="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
    local validator_svc="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"
    local csm_svc="${CSM_VALIDATOR_SERVICE_FILE:-/etc/systemd/system/csm_nimbusvalidator.service}"
    EL=""; CL=""; VC=""; CSM_VC=""
    if [ -f "$exec_svc" ]; then
        EL=$(grep Description= "$exec_svc" | awk -F'=' '{print $2}' | awk '{print $1}')
    fi
    if [ -f "$consensus_svc" ]; then
        CL=$(grep Description= "$consensus_svc" | awk -F'=' '{print $2}' | awk '{print $1}')
    fi
    if [ -f "$validator_svc" ]; then
        VC=$(grep Description= "$validator_svc" | awk -F'=' '{print $2}' | awk '{print $1}')
    fi
    if [ -f "$csm_svc" ]; then
        CSM_VC=$(grep Description= "$csm_svc" | awk -F'=' '{print $2}' | awk '{print $1}')
    fi
    if [[ -n $CL  ]]; then
        CLIENT=$CL
    elif [[ -n $VC ]]; then
        CLIENT=$VC
    fi
}

# ── Validator mode helpers (used by CC switch, update_validator.sh, TUI) ──

# Return whether Obol Charon middleware is installed (charon.service present).
isCharonEnabled(){
    local charon_svc="${CHARON_SERVICE_FILE:-/etc/systemd/system/charon.service}"
    [[ -f "$charon_svc" ]]
}

# EthPillar Charon cluster datadir (overridable in tests via CHARON_CLUSTER_DIR).
getCharonClusterDir(){
    echo "${CHARON_CLUSTER_DIR:-/var/lib/charon/.charon}"
}

# Charon DKG key-share directory (overridable via CHARON_VALIDATOR_KEYS_DIR).
getCharonValidatorKeysDir(){
    echo "${CHARON_VALIDATOR_KEYS_DIR:-$(getCharonClusterDir)/validator_keys}"
}

# Parse Charon libp2p TCP port from charon.service (--p2p-tcp-address=host:port).
# Prints nothing when charon.service is missing; defaults to 3610 when flag absent.
getCharonP2pPort(){
    local charon_svc="${CHARON_SERVICE_FILE:-/etc/systemd/system/charon.service}"
    local bind="" port=""
    [[ -f "$charon_svc" ]] || return 0
    bind=$(grep -oE -- '--p2p-tcp-address=[^[:space:]\\]+' "$charon_svc" 2>/dev/null | head -1 | cut -d= -f2-)
    if [[ -n "$bind" ]]; then
        port="${bind##*:}"
    else
        port="3610"
    fi
    if [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )); then
        echo "$port"
    fi
}

# Stop validator → charon → consensus before BN maintenance (update/resync).
stopConsensusStackForUpdate(){
    test -f /etc/systemd/system/validator.service && sudo systemctl stop validator 2>/dev/null || true
    isCharonEnabled && sudo systemctl stop charon 2>/dev/null || true
    test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus 2>/dev/null || true
}

# Start consensus → charon → validator after BN maintenance.
startConsensusStackAfterUpdate(){
    test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus 2>/dev/null || true
    isCharonEnabled && sudo systemctl start charon 2>/dev/null || true
    test -f /etc/systemd/system/validator.service && sudo systemctl start validator 2>/dev/null || true
}

# Stop validator (and Charon when DVT) before VC binary update.
stopValidatorStackForUpdate(){
    test -f /etc/systemd/system/validator.service && sudo systemctl stop validator 2>/dev/null || true
    isCharonEnabled && sudo systemctl stop charon 2>/dev/null || true
}

# Start charon → validator after VC binary update.
startValidatorStackAfterUpdate(){
    isCharonEnabled && sudo systemctl start charon 2>/dev/null || true
    test -f /etc/systemd/system/validator.service && sudo systemctl start validator 2>/dev/null || true
}

# Ensure Charon is up before starting the VC (key import / loadKeys).
ensureCharonBeforeValidator(){
    isCharonEnabled && sudo systemctl try-restart charon 2>/dev/null || sudo systemctl start charon 2>/dev/null || true
}

# Reload systemd units after .env.overrides edits (Charon + core stack).
reloadEnvOverridesAndMaybeRestart(){
    sudo systemctl daemon-reload
    if ! whiptail --title "Reload Environment values" --yesno \
        "Reload systemd and restart affected services?\n\n(execution, consensus, charon, validator, mevboost — whichever is installed)" 10 78; then
        return 0
    fi
    test -f /etc/systemd/system/execution.service && sudo systemctl try-restart execution 2>/dev/null || true
    test -f /etc/systemd/system/consensus.service && sudo systemctl try-restart consensus 2>/dev/null || true
    test -f /etc/systemd/system/mevboost.service && sudo systemctl try-restart mevboost 2>/dev/null || true
    isCharonEnabled && sudo systemctl try-restart charon 2>/dev/null || true
    test -f /etc/systemd/system/validator.service && sudo systemctl try-restart validator 2>/dev/null || true
}

# Allow inbound TCP on Charon P2P port in UFW (no-op when Charon not installed).
ufwAllowCharonP2p(){
    local port
    port=$(getCharonP2pPort)
    [[ -n "$port" ]] || return 0
    sudo ufw allow "${port}/tcp" comment 'Allow Charon P2P port'
}

# Classify how this node runs validator duties.
# Returns: none | separate | integrated_grandine
getValidatorMode(){
    local consensus_svc="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
    local validator_svc="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"

    if [[ -f "$consensus_svc" ]] && grep -q 'keystore-dir' "$consensus_svc" 2>/dev/null; then
        echo "integrated_grandine"
    elif [[ -f "$validator_svc" ]]; then
        echo "separate"
    else
        echo "none"
    fi
}

# Resolve the effective validator client name.
# Sets VALIDATOR_CLIENT and VC (for backward compatibility).
getValidatorClient(){
    local consensus_svc="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
    local validator_svc="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"

    VALIDATOR_CLIENT=""

    if [[ -f "$validator_svc" ]]; then
        VALIDATOR_CLIENT=$(grep -m1 '^Description=' "$validator_svc" 2>/dev/null | awk -F'=' '{print $2}' | awk '{print $1}')
    elif [[ -f "$consensus_svc" ]] && grep -q 'keystore-dir' "$consensus_svc" 2>/dev/null; then
        VALIDATOR_CLIENT="Grandine"
    fi

    VC="$VALIDATOR_CLIENT"
    echo "$VALIDATOR_CLIENT"
}

# True when Charon has shipped Gloas/ePBS support (version gate).
# Stub: always false until Obol publishes a stable ePBS release —
# https://github.com/ObolNetwork/charon/releases
# When true, the Charon menu owns split-LXC ePBS import/complete.
charonEpbsSupported() {
    return 1
}

# True when the MEV-Boost TUI should offer ePBS migration.
# - Split LXC (MEV, no local VC): always show (export / remote complete).
# - Charon DVT on this host: hide until charonEpbsSupported (builder path is Charon's).
# - Solo: manage.epbs.support_level == "full" (Prysm, Lodestar).
# CLI (`python -m manage.epbs`) is not gated; placeholders stay there.
epbsTuiSupported() {
    local validator_svc="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"
    local mev_svc="${MEVBOOST_SERVICE_FILE:-/etc/systemd/system/mevboost.service}"
    # Machine A: MEV present, no local validator → always offer export/complete.
    if [[ -f "$mev_svc" && ! -f "$validator_svc" ]]; then
        return 0
    fi
    if isCharonEnabled; then
        return 1
    fi
    local client
    client=$(getValidatorClient)
    case "$client" in
        Prysm|Lodestar) return 0 ;;
        *) return 1 ;;
    esac
}

# True when the Charon submenu should show split-LXC ePBS import.
# Charon owns the builder path; hide entirely until charonEpbsSupported.
epbsImportUnderCharon() {
    local mev_svc="${MEVBOOST_SERVICE_FILE:-/etc/systemd/system/mevboost.service}"
    [[ -f "$mev_svc" ]] && return 1
    isCharonEnabled && charonEpbsSupported
}

# True when the Validator submenu should show split-LXC ePBS import.
# Solo VC only — never when Charon is installed (Charon menu or hidden).
epbsImportUnderValidator() {
    local mev_svc="${MEVBOOST_SERVICE_FILE:-/etc/systemd/system/mevboost.service}"
    [[ -f "$mev_svc" ]] && return 1
    isCharonEnabled && return 1
    local client
    client=$(getValidatorClient)
    case "$client" in
        Prysm|Lodestar) return 0 ;;
        *) return 1 ;;
    esac
}

# True when either import menu entry should appear (VC-only or Charon ePBS).
epbsImportMenuSupported() {
    epbsImportUnderCharon || epbsImportUnderValidator
}

# True when this MEV host has no local VC (split-LXC export/complete mode).
epbsRemoteVcMode() {
    local validator_svc="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"
    local mev_svc="${MEVBOOST_SERVICE_FILE:-/etc/systemd/system/mevboost.service}"
    [[ -f "$mev_svc" && ! -f "$validator_svc" ]]
}

# Build the beacon node REST URL that a separate VC should target.
# Port: CL_REST_PORT, else scraped from consensus.service, else 5052.
# IP: a scraped --http-address wins over CL_IP_ADDRESS (default 127.0.0.1).
# Sets BEACON_NODE_ENDPOINT.
getBeaconNodeEndpoint(){
    local consensus_svc="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
    local cl_ip="${CL_IP_ADDRESS:-127.0.0.1}"
    local cl_port="${CL_REST_PORT:-}"

    if [[ -f "$consensus_svc" ]]; then
        # Try to scrape port from common flags if not set via env
        if [[ -z "$cl_port" ]]; then
            cl_port=$(grep -oE '(--http-port=|--rest-port=|--rest-api-port=|--rest\.port=)[0-9]+' \
                "$consensus_svc" 2>/dev/null | head -1 | grep -oE '[0-9]+' || true)
        fi

        # Try to scrape IP address
        local scraped_ip
        scraped_ip=$(grep -oE '(--http-address=)[^ ]+' "$consensus_svc" 2>/dev/null | head -1 | cut -d= -f2- || true)
        [[ -n "$scraped_ip" ]] && cl_ip="$scraped_ip"
    fi

    cl_port="${cl_port:-5052}"
    BEACON_NODE_ENDPOINT="http://${cl_ip}:${cl_port}"
    echo "$BEACON_NODE_ENDPOINT"
}

# Stop validator duties based on current mode.
stopValidatorService(){
    local mode
    mode=$(getValidatorMode)

    case "$mode" in
        separate)
            if [[ -f "${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}" ]]; then
                sudo systemctl stop validator
            fi
            ;;
        integrated_grandine)
            if [[ -f "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}" ]]; then
                sudo systemctl stop consensus
            fi
            ;;
    esac
}

# Start validator duties based on current mode.
# Includes daemon-reload when starting a separate validator (useful after patching).
startValidatorService(){
    local mode
    mode=$(getValidatorMode)

    case "$mode" in
        separate)
            if [[ -f "${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}" ]]; then
                sudo systemctl daemon-reload
                sudo systemctl start validator
            fi
            ;;
        integrated_grandine)
            if [[ -f "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}" ]]; then
                sudo systemctl start consensus
            fi
            ;;
    esac
}

# Update the beacon-node flag after a consensus client switch.
# When Charon is installed, patch Charon's upstream BN URL (VC stays on :3600).
# No-op unless validator mode is separate.
patchValidatorBeaconEndpoint(){
    local validator_svc="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"
    local charon_svc="${CHARON_SERVICE_FILE:-/etc/systemd/system/charon.service}"
    local mode
    mode=$(getValidatorMode)

    if [[ "$mode" != "separate" ]]; then
        return 0
    fi

    getBeaconNodeEndpoint

    if isCharonEnabled; then
        PYTHONPATH="${BASE_DIR}" python3 -m deploy.charon patch_beacon \
            --endpoint "$BEACON_NODE_ENDPOINT" \
            --service-path "$charon_svc"
        return $?
    fi

    getValidatorClient

    if [[ -z "$VALIDATOR_CLIENT" ]]; then
        echo "WARNING: Could not determine validator client for beacon endpoint patch." >&2
        return 1
    fi

    PYTHONPATH="${BASE_DIR}" python3 -m deploy.vc_service patch \
        --vc "$VALIDATOR_CLIENT" \
        --endpoint "$BEACON_NODE_ENDPOINT" \
        --service-path "$validator_svc"
}

# Start Charon + validator after CDVN migration (before optional monitoring install).
_migrateCdvnStartValidatorStack(){
    [[ -f /etc/systemd/system/charon.service ]] \
        || [[ -f /etc/systemd/system/validator.service ]] || return 0
    ohai "Starting Charon and validator services…"
    [[ -f /etc/systemd/system/charon.service ]] \
        && sudo systemctl start charon 2>/dev/null || true
    [[ -f /etc/systemd/system/validator.service ]] \
        && sudo systemctl start validator 2>/dev/null || true
}

# Legacy prompt wrapper (unused by migrate; kept for manual use).
_migrateCdvnPromptStartValidatorStack(){
    [[ -f /etc/systemd/system/charon.service ]] \
        || [[ -f /etc/systemd/system/validator.service ]] || return 0
    if ! whiptail --title "Start services" --yesno \
"Start Charon (and validator if installed) now?" 9 70; then
        return 0
    fi
    _migrateCdvnStartValidatorStack
}

# Return 0 when EthPillar monitoring units are present (deb or custom systemd paths).
_migrateCdvnMonitoringPresent(){
    [[ -f /etc/systemd/system/ethereum-metrics-exporter.service ]] && return 0
    systemctl cat grafana-server.service >/dev/null 2>&1 && return 0
    return 1
}

# Start Grafana/Prometheus stack after monitoring is installed during CDVN migration.
_migrateCdvnPromptStartMonitoring(){
    _migrateCdvnMonitoringPresent || return 0
    if ! whiptail --title "Start monitoring" --yesno \
"Start Grafana, Prometheus, and metrics exporter now?" 9 70; then
        return 0
    fi
    sudo systemctl start grafana-server prometheus prometheus-node-exporter 2>/dev/null || true
    [[ -f /etc/systemd/system/ethereum-metrics-exporter.service ]] \
        && sudo systemctl start ethereum-metrics-exporter 2>/dev/null || true
    if whiptail --title "View monitoring logs" --yesno \
"View Grafana/Prometheus/metrics exporter logs now?" 9 70; then
        view_journal_logs -u grafana-server -u prometheus -u ethereum-metrics-exporter \
            -u prometheus-node-exporter --no-hostname -f
    fi
}

# Return 0 when Charon key shares exist under the EthPillar Charon datadir.
charonKeysharesPresent(){
    local keys
    keys="$(getCharonValidatorKeysDir)"
    sudo test -d "$keys" 2>/dev/null || return 1
    [[ "$(sudo find "$keys" -maxdepth 1 -name 'keystore-*.json' 2>/dev/null | wc -l)" -gt 0 ]]
}

# Return 0 when Lodestar already has imported validator keystores.
lodestarValidatorKeysPresent(){
    sudo test -d /var/lib/lodestar_validator/keystores 2>/dev/null || return 1
    [[ "$(sudo find /var/lib/lodestar_validator/keystores -maxdepth 1 -name 'keystore-*.json' 2>/dev/null | wc -l)" -gt 0 ]]
}

# Return 0 when the installed EthPillar VC already has imported keystores (any client).
_ethpillarVcHasImportedKeys(){
    PYTHONPATH="${BASE_DIR}" python3 - <<'PY'
from deploy.charon import _vc_already_has_keys
from deploy.cdvn_migrate import detect_ethpillar_vc_name

vc = detect_ethpillar_vc_name()
raise SystemExit(0 if vc and _vc_already_has_keys(vc) else 1)
PY
}

# Append a timestamped line to the active CDVN migration log (if set).
_migrateCdvnLog(){
    [[ -n "${_migrate_log:-}" ]] || return 0
    printf '%s %s\n' "$(date -Is)" "$*" >>"$_migrate_log"
}

# Full CDVN → EthPillar migration (CLI + TUI). Detects active EL/CL/VC/MEV.
migrateCdvnFull(){
    local default_root path_in plan_file moves_file selected_moves line rel src dest
    local docker_up=0 _cdvn_env _grafana_port=""
    local _migrate_log_dir _migrate_log

    default_root="${HOME}/charon-distributed-validator-node"
    [[ -d "$default_root" ]] || default_root="${HOME}/git/charon-distributed-validator-node"
    [[ -d "$default_root" ]] || default_root="${HOME}"

    path_in="${1:-}"
    if [[ -z "$path_in" ]] || [[ ! -e "$path_in" ]]; then
        path_in=$(whiptail --title "Migrate from CDVN" --inputbox \
            "Path to your CDVN checkout directory (or .env file):" \
            10 78 "${path_in:-$default_root}" 3>&1 1>&2 2>&3) || return 0
    fi
    if [[ ! -e "$path_in" ]]; then
        whiptail --title "Migrate from CDVN" --msgbox "Path not found:\n${path_in}" 9 70
        return 1
    fi

    if ! whiptail --title "Migrate from CDVN" --yesno \
"Migrate charon-distributed-validator-node to EthPillar:

  - Detect active EL / CL / VC / MEV / network from .env
  - Install matching EthPillar clients (if needed)
  - Move Docker datadirs into /var/lib when confirmed
  - Overlay .charon + Charon systemd flags
  - Fresh EthPillar monitoring; CDVN dashboards re-provisioned

WARNING: Stop CDVN Docker Compose first (slashing risk).

Continue?" 20 78; then
        return 0
    fi

    # Ensure ethpillar CLI symlink + Python deps
    if [[ ! -e /usr/local/bin/ethpillar ]]; then
        ohai "Installing ethpillar symlink"
        sudo ln -sf "${BASE_DIR}/ethpillar.sh" /usr/local/bin/ethpillar
    fi
    ensure_python_deps

    plan_file=$(mktemp)
    set +e
    PYTHONPATH="${BASE_DIR}" python3 -m deploy.cdvn_migrate plan --path "$path_in" \
        >"$plan_file" 2>/tmp/ethpillar-cdvn-migrate-err
    local rc=$?
    set -e
    if [[ $rc -eq 2 ]]; then
        whiptail --title "CDVN migration abort" --msgbox \
"CDVN Docker is still running, or its status could not be verified
(missing docker/docker-compose, permission denied, or timeout).

Stop CDVN first, then re-run:
  cd <cdvn-root> && docker compose down
  # or: docker-compose down

Then: ethpillar --migrate_cdvn" 16 72
        rm -f "$plan_file"
        return 1
    fi
    if [[ $rc -ne 0 ]]; then
        whiptail --title "CDVN migration error" --msgbox \
"$(cat /tmp/ethpillar-cdvn-migrate-err 2>/dev/null || echo "Failed to build plan.")" 14 78
        rm -f "$plan_file"
        return 1
    fi

    whiptail --title "CDVN migration plan" --textbox "$plan_file" 24 88

    _migrate_log_dir="${HOME}/.ethpillar/logs"
    mkdir -p "$_migrate_log_dir"
    _migrate_log="${_migrate_log_dir}/cdvn-migrate-$(date +%Y%m%d-%H%M%S).log"
    {
        echo "=== EthPillar CDVN migration $(date -Is) ==="
        echo "checkout: ${path_in}"
        echo "log: ${_migrate_log}"
        echo ""
        cat "$plan_file"
        echo ""
    } >>"$_migrate_log"
    ohai "Migration log: ${_migrate_log}"

    if ! whiptail --title "Migrate from CDVN" --yesno \
"Proceed with deploy using this plan?

Optional Docker data/ moves are confirmed next.
(.charon cluster copy always runs when cluster-lock.json is present.)" 12 70; then
        rm -f "$plan_file"
        return 0
    fi

    local _migrate_fresh=0
    if sudo test -f /var/lib/charon/.charon/cluster-lock.json 2>/dev/null \
        || [[ -f /etc/systemd/system/charon.service ]] \
        || [[ -f /etc/systemd/system/validator.service ]]; then
        local _fresh_default="--defaultno"
        if charonKeysharesPresent && ! _ethpillarVcHasImportedKeys; then
            _fresh_default=""
        fi
        if whiptail --title "Fresh migration" "${_fresh_default}" --yesno \
"Previous EthPillar Charon/VC state detected.

Reset Charon cluster + VC keys and run a clean end-to-end migration?

(Stops charon/validator, clears /var/lib/charon/.charon and VC keystores)" 14 78; then
            _migrate_fresh=1
        fi
    fi

    # Build checklist of movable datadirs
    selected_moves=""
    local prompted_moves=0
    moves_file=$(mktemp)
    PYTHONPATH="${BASE_DIR}" python3 - <<PY >"$moves_file"
import os
from deploy.cdvn_migrate import plan_cdvn_migration
plan = plan_cdvn_migration("$path_in")
for m in plan.datadir_moves:
    if m.relative_src == ".charon":
        continue
    if m.will_move:
        dest_name = os.path.basename(m.dest.rstrip(os.sep)) or m.dest
        print(m.relative_src)
        print(f"-> {dest_name} ({m.owner})")
PY
    if [[ -s "$moves_file" ]]; then
        local checklist=() rel_line dest_line
        while IFS= read -r rel_line && IFS= read -r dest_line; do
            checklist+=("$rel_line" "$dest_line" "ON")
        done <"$moves_file"
        if [[ ${#checklist[@]} -gt 0 ]]; then
            prompted_moves=1
            selected_moves=$(whiptail --title "Confirm datadir moves" --checklist \
                "Move optional CDVN Docker data/ into /var/lib (uncheck = skip).\n.charon cluster copy is always applied separately." \
                22 76 10 "${checklist[@]}" 3>&1 1>&2 2>&3) || selected_moves=""
            selected_moves=$(echo "$selected_moves" | tr -d '"')
            selected_moves=$(echo "$selected_moves" | tr ' ' ',')
            _migrateCdvnLog "datadir checklist selection: ${selected_moves:-<none>}"
        fi
    else
        _migrateCdvnLog "datadir checklist: not shown (no optional Docker data/ moves)"
    fi
    rm -f "$moves_file" "$plan_file"

    # After checklist: pass explicit --moves (empty = none). If no prompt, omit = all eligible.
    local moves_arg=() fresh_arg=()
    if [[ "$prompted_moves" -eq 1 ]]; then
        moves_arg=(--moves "$selected_moves")
    fi
    if [[ "$_migrate_fresh" -eq 1 ]]; then
        fresh_arg=(--fresh)
        _migrateCdvnLog "fresh reset: enabled"
    fi

    ohai "Deploying EthPillar clients from CDVN plan…"
    _migrateCdvnLog "=== deploy.cdvn_migrate run ==="
    set +e
    PYTHONPATH="${BASE_DIR}" python3 -m deploy.cdvn_migrate run --path "$path_in" \
        "${moves_arg[@]}" "${fresh_arg[@]}" 2>&1 | tee -a "$_migrate_log"
    rc=${PIPESTATUS[0]}
    set -e
    _migrateCdvnLog "deploy.cdvn_migrate exit=${rc}"
    if [[ $rc -ne 0 ]]; then
        whiptail --title "CDVN migration failed" --msgbox \
"Migration failed (exit ${rc}).

Full log:
${_migrate_log}" 12 78
        return 1
    fi

    ensure_journal_access || ohai "Journal access ready (sg systemd-journal used when needed)"
    _migrateCdvnLog "journal access: $(can_read_journal && echo ok || echo re-login-required)"

    # Key shares: auto-synced during deploy.cdvn_migrate run; retry if still missing.
    if [[ -f /etc/systemd/system/validator.service ]] \
        && charonKeysharesPresent \
        && ! _ethpillarVcHasImportedKeys; then
        ohai "Importing ${OBOL_MARK} Obol Charon key shares into validator client…"
        PYTHONPATH="${BASE_DIR}" python3 - <<'PY' 2>&1 | tee -a "$_migrate_log"
from deploy.charon import sync_charon_keyshares_to_vc
from deploy.cdvn_migrate import detect_ethpillar_vc_name

vc = detect_ethpillar_vc_name() or "Lodestar"
result = sync_charon_keyshares_to_vc(vc, force=True)
print(result)
if result.get("status") == "failed":
    raise SystemExit(1)
if result.get("status") == "skipped" and not str(result.get("reason", "")).startswith("destination already has"):
    raise SystemExit(1)
PY
        rc=${PIPESTATUS[0]}
        if [[ $rc -ne 0 ]]; then
            whiptail --title "CDVN migration failed" --msgbox \
"Key share import failed. See log:
${_migrate_log}" 12 78
            return 1
        fi
    fi

    # Start Charon/VC before optional monitoring (monitoring install can block on log prompts).
    _migrateCdvnStartValidatorStack

    if [[ -d "$path_in" ]]; then
        _cdvn_env="$path_in/.env"
    else
        _cdvn_env="$path_in"
    fi
    [[ -f "$_cdvn_env" ]] || _cdvn_env=""

    # Fresh EthPillar monitoring + Charon dashboard
    if [[ ! -f /etc/systemd/system/ethereum-metrics-exporter.service ]]; then
        if whiptail --title "Monitoring" --yesno \
"Install EthPillar Monitoring (Grafana/Prometheus) with CDVN ${OBOL_CHARON} dashboards?" 10 70; then
            export ETHPILLAR_CDVN_MIGRATE=1
            [[ -n "$_cdvn_env" ]] && export ETHPILLAR_CDVN_ENV="$_cdvn_env"
            runScript ethereum-metrics-exporter.sh -i
            unset ETHPILLAR_CDVN_ENV ETHPILLAR_CDVN_MIGRATE
        fi
    fi
    if [[ -f /etc/prometheus/prometheus.yml ]] || [[ -d /etc/grafana ]]; then
        PYTHONPATH="${BASE_DIR}" python3 -m manage.charon_monitoring provision --restart >/dev/null 2>&1 || true
    fi
    if [[ -f "$_cdvn_env" ]]; then
        _grafana_port=$(PYTHONPATH="${BASE_DIR}" python3 -c \
            "from deploy.cdvn_migrate import apply_cdvn_monitoring_from_env; import sys; print(apply_cdvn_monitoring_from_env(sys.argv[1]) or '')" \
            "$_cdvn_env")
    elif [[ -d /etc/grafana ]]; then
        _grafana_port=$(PYTHONPATH="${BASE_DIR}" python3 -c \
            "from manage.grafana import read_grafana_http_port; print(read_grafana_http_port())")
    fi
    if [[ -n "${_grafana_port:-}" ]]; then
        _migrateCdvnLog "Grafana http_port: ${_grafana_port}"
        ohai "Grafana: http://127.0.0.1:${_grafana_port}/"
    fi

    _migrateCdvnPromptStartMonitoring

    local _grafana_url="http://127.0.0.1:${_grafana_port:-3000}/d/charon_overview/"

    whiptail --title "CDVN migration done" --msgbox \
"Reminders:
- Keep CDVN Docker Charon/VC stopped
- Open Charon P2P TCP if peers need access
  (port in charon.service)
- Start order: EL -> CL -> Charon -> VC
- Grafana Charon dashboards:
  ${_grafana_url}
  Also: clusterview-user, node_overview
- Logs dashboard needs Loki (not installed)

Migration log:
${_migrate_log}" 20 76
}

# Charon TUI menu + legacy alias
migrateCharonFromCdvn(){
    migrateCdvnFull "$@"
}

importCharonCdvnEnv(){
    migrateCdvnFull "$@"
}

# Copy a .charon tree via deploy.charon (overridable in bats).
runCopyCharonCluster(){
    local src="$1" dest="$2" force_flag="${3:-}"
    # shellcheck disable=SC2086
    PYTHONPATH="${BASE_DIR}" python3 -m deploy.charon copy_charon \
        --src "$src" --dest "$dest" ${force_flag}
}

# Run Validator Charon key-share import without the yes/no confirm.
# Overridable in bats (avoids spawning manage_validator_keys.sh).
runImportCharonKeySharesYes(){
    bash "${BASE_DIR}/manage_validator_keys.sh" charon-import-yes
}

# Manual setup: copy a CDVN/DKG .charon tree into EthPillar's Charon datadir.
importCharonClusterFolder(){
    local SRC DEST
    DEST="$(getCharonClusterDir)"
    local vc_svc="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"
    SRC=$(whiptail --title "Import .charon cluster folder" --inputbox \
"Path to your CDVN or DKG .charon directory:

Copies cluster-lock.json, validator_keys, and related
cluster files into:
  ${DEST}

If key shares are present, you will be asked whether to
import them into the signer VC (same as CDVN migrate)." 18 78 --ok-button "Submit" 3>&1 1>&2 2>&3) || return

    SRC="${SRC/#\~/$HOME}"
    # Accept either .../.charon or a parent that contains .charon
    if [[ -d "${SRC}/.charon" ]]; then
        SRC="${SRC}/.charon"
    fi
    if [[ ! -d "$SRC" ]]; then
        whiptail --title "Import .charon cluster folder" --msgbox \
"Directory not found:
${SRC}" 9 70
        return 1
    fi
    if [[ ! -f "${SRC}/cluster-lock.json" ]]; then
        whiptail --title "Import .charon cluster folder" --msgbox \
"Missing cluster-lock.json in:
${SRC}" 9 70
        return 1
    fi

    local force_flag=""
    if sudo test -f "${DEST}/cluster-lock.json" 2>/dev/null; then
        if ! whiptail --title "Import .charon cluster folder" --yesno \
"Destination already has a cluster:
${DEST}

Overwrite with:
${SRC}?" 12 70; then
            return
        fi
        force_flag="--force"
    elif ! whiptail --title "Import .charon cluster folder" --yesno \
"Copy .charon cluster:
  ${SRC}
→ ${DEST}

Continue?" 12 70; then
        return
    fi

    ohai "Importing ${OBOL_MARK} .charon cluster folder…"
    set +e
    runCopyCharonCluster "$SRC" "$DEST" "$force_flag"
    local rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        whiptail --title "Import .charon cluster folder" --msgbox \
"Failed to copy .charon into ${DEST}.
Check the terminal output for details." 10 70
        return 1
    fi

    # Same UX as CDVN migrate: offer key-share import when shares are on disk.
    if charonKeysharesPresent && [[ -f "$vc_svc" ]]; then
        local vc_label=""
        getClientVC 2>/dev/null || true
        getValidatorClient >/dev/null 2>&1 || true
        vc_label="${VC:-${VALIDATOR_CLIENT:-signer VC}}"
        if whiptail --title "Import .charon cluster folder" --yesno \
"Copied .charon into ${DEST}.

Key shares were found under validator_keys.
Also import them into ${vc_label} now?

(You can re-run later via Validator → ${OBOL_IMPORT_KEY_SHARES})" 14 70; then
            runImportCharonKeySharesYes
            return
        fi
    fi

    whiptail --title "Import .charon cluster folder" --msgbox \
"Copied .charon into ${DEST}.

Next:
  1. Start Charon (this menu → Start Charon)
  2. Validator → ${OBOL_IMPORT_KEY_SHARES} (if not imported yet)" 14 70
}

# Get execution client datadir from systemd config (for reth)
getExecutionDatadir(){
    local svc_file=${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}
    DATADIR=$(test -f "$svc_file" && grep -oP '(?<=--datadir=)[^\s]+' "$svc_file" || echo "")
}

# Get execution client static files directory from systemd config (for reth)
getExecutionStaticFiles(){
    local svc_file=${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}
    STATIC_FILES=$(test -f "$svc_file" && grep -oP '(?<=--datadir.static-files=)[^\s]+' "$svc_file" || echo "")
}

# Get list of validator public keys
getPubKeys(){
    TEMP=""
    local ARGUMENT=${1:-"default"}

    # Charon DV: VC CLIs list key-share pubkeys; on-chain identity is the
    # composite distributed_public_key from cluster-lock.json.
    if isCharonEnabled; then
        local cluster_dir
        cluster_dir="$(getCharonClusterDir)"
        TEMP=$(PYTHONPATH="${BASE_DIR}" python3 - <<PY
from deploy.charon import list_distributed_validator_pubkeys
for pk in list_distributed_validator_pubkeys("${cluster_dir}"):
    print(pk)
PY
)
        if [[ -n "$TEMP" ]]; then
            convertLIST
            return 0
        fi
    fi

    # Use modern client detection first
    getValidatorClient
    local client="${VALIDATOR_CLIENT:-$VC}"

    case "$client" in
        Lighthouse)
            local vc_path
            if [[ -d /var/lib/lighthouse_validator ]]; then
                vc_path="/var/lib/lighthouse_validator"
            else
                vc_path="/var/lib/lighthouse"
            fi
            local LH_BIN
            LH_BIN=$(get_systemd_exec_path "/etc/systemd/system/validator.service" "/usr/local/bin/lighthouse")
            TEMP=$(sudo -u validator "$LH_BIN" account validator list --datadir "$vc_path" 2>/dev/null | grep -Eo '0x[a-fA-F0-9]{96}' || true)
            convertLIST
            ;;

        Lodestar)
            local vc_path
            if [[ -d /var/lib/lodestar_validator ]]; then
                vc_path="/var/lib/lodestar_validator"
            else
                vc_path="/var/lib/lodestar/validators"
            fi
            local LODESTAR_BIN
            LODESTAR_BIN=$(get_systemd_exec_path "/etc/systemd/system/validator.service" "/usr/local/bin/lodestar")
            TEMP=$(sudo -u validator "$LODESTAR_BIN" validator list --dataDir "$vc_path" --force 2>/dev/null | grep -Eo '0x[a-fA-F0-9]{96}' || true)
            convertLIST
            ;;

        Teku)
            local _teku=()
            local teku_cmd

            if [[ -f /etc/systemd/system/validator.service ]]; then
                teku_cmd="ls /var/lib/teku_validator/validator_keys/*.json 2>/dev/null"
            else
                teku_cmd="ls /var/lib/teku/validator_keys/*.json 2>/dev/null"
            fi

            for json in $(sudo -u validator bash -c "$teku_cmd" 2>/dev/null); do
                local pubkey
                pubkey=$(sudo -u validator bash -c "cat '$json' | jq -r '.pubkey' 2>/dev/null")
                [[ -n "$pubkey" && "$pubkey" != "null" ]] && _teku+=("0x$pubkey")
            done

            TEMP="${_teku[*]}"
            convertLIST
            ;;

        Nimbus)
            local nimbus_cmd
            if [[ "$ARGUMENT" == "plugin_csm_validator" ]]; then
                nimbus_cmd="ls ${DATA_DIR}/validators 2>/dev/null | grep -Eo '0x[a-fA-F0-9]{96}'"
            else
                if [[ -f /etc/systemd/system/validator.service ]]; then
                    nimbus_cmd="ls /var/lib/nimbus_validator/validators 2>/dev/null | grep -Eo '0x[a-fA-F0-9]{96}'"
                else
                    nimbus_cmd="ls /var/lib/nimbus/validators 2>/dev/null | grep -Eo '0x[a-fA-F0-9]{96}'"
                fi
            fi
            TEMP=$(sudo -u validator bash -c "$nimbus_cmd" 2>/dev/null || true)
            convertLIST
            ;;

        Prysm)
            local PRYSM_VC
            PRYSM_VC=$(get_systemd_exec_path "/etc/systemd/system/validator.service" "/usr/local/bin/prysm-validator")
            TEMP=$(sudo -u validator "$PRYSM_VC" accounts list --wallet-dir=/var/lib/prysm/validators 2>/dev/null | grep -Eo '0x[a-fA-F0-9]{96}' || true)
            convertLIST
            ;;

        *)
            echo "No supported validator client detected for pubkey listing."
            LIST=()
            return 1
            ;;
    esac
}

convertLIST(){
# Reset var
LIST=()
for key in $TEMP
do
   LIST+=($key)
done
}

# Probe whether a beacon REST base answers (node/version).
_probeBeaconApi(){
    local base="${1%/}"
    [[ -n "$base" ]] || return 1
    curl -sf -m 2 -H "Accept: application/json" "${base}/eth/v1/node/version" >/dev/null 2>&1
}

# First Charon upstream BN URL from charon.service (--beacon-node-endpoints).
getCharonUpstreamBeacon(){
    local charon_svc="${CHARON_SERVICE_FILE:-/etc/systemd/system/charon.service}"
    local raw=""
    [[ -f "$charon_svc" ]] || return 0
    raw=$(grep -oE -- '--beacon-node-endpoints=[^[:space:]\\]+' "$charon_svc" 2>/dev/null | head -1 | cut -d= -f2-)
    [[ -n "$raw" ]] || return 0
    # Comma-separated list → first URL
    echo "${raw%%,*}"
}

# Pick a reachable beacon REST URL for index / duty queries.
# Prefers API_BN_ENDPOINT, then local consensus scrape, then Charon upstream.
resolveBeaconApiEndpoint(){
    local candidates=()
    local u=""
    [[ -n "${API_BN_ENDPOINT:-}" ]] && candidates+=("${API_BN_ENDPOINT}")
    if [[ -f "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}" ]]; then
        u=$(getBeaconNodeEndpoint 2>/dev/null || true)
        [[ -n "$u" ]] && candidates+=("$u")
    fi
    if isCharonEnabled; then
        u=$(getCharonUpstreamBeacon 2>/dev/null || true)
        [[ -n "$u" ]] && candidates+=("$u")
    fi
    # Deduplicate while preserving order
    local seen="|"
    local unique=()
    for u in "${candidates[@]}"; do
        u="${u%/}"
        [[ -n "$u" ]] || continue
        [[ "$seen" == *"|$u|"* ]] && continue
        seen+="${u}|"
        unique+=("$u")
    done
    for u in "${unique[@]}"; do
        if _probeBeaconApi "$u"; then
            echo "$u"
            return 0
        fi
    done
    # Fall back to first candidate (or default) even if probe failed
    if [[ ${#unique[@]} -gt 0 ]]; then
        echo "${unique[0]}"
    else
        echo "http://127.0.0.1:5052"
    fi
}

# Convert pubkeys to validator indices using the beacon node API (batch).
getIndices(){
    INDICES=()
    local beacon=""
    beacon=$(resolveBeaconApiEndpoint)
    export API_BN_ENDPOINT="$beacon"

    if [[ ${#LIST[@]} -eq 0 ]]; then
        return 0
    fi

    local json_out err_file
    err_file=$(mktemp)
    json_out=$(
        printf '%s\n' "${LIST[@]}" | PYTHONPATH="${BASE_DIR}" python3 -m deploy.charon lookup_indices \
            --beacon "$beacon" --json 2>"$err_file"
    )
    local rc=$?
    local stderr_txt=""
    stderr_txt=$(cat "$err_file" 2>/dev/null || true)
    rm -f "$err_file"

    if [[ $rc -ne 0 || -z "$json_out" ]]; then
        echo "WARNING: Beacon index lookup failed via ${beacon}"
        [[ -n "$stderr_txt" ]] && echo "$stderr_txt"
        return 0
    fi

    local found
    found=$(echo "$json_out" | jq -r '.found // 0' 2>/dev/null || echo 0)
    INDICES=()
    local pk idx
    for pk in "${LIST[@]}"; do
        idx=$(echo "$json_out" | jq -r --arg pk "$pk" '.indices[$pk] // empty' 2>/dev/null || true)
        [[ -n "$idx" && "$idx" != "null" ]] && INDICES+=("$idx")
    done

    if [[ "${found}" -eq 0 ]]; then
        local err
        err=$(echo "$json_out" | jq -r '.error // empty' 2>/dev/null || true)
        echo "WARNING: No validator indices returned from ${beacon} for ${#LIST[@]} pubkey(s)."
        if [[ -n "$err" ]]; then
            echo "  ${err}"
        else
            echo "  Confirm the beacon node is synced on the correct network and reachable."
            if isCharonEnabled; then
                echo "  Charon DV pubkeys come from cluster-lock.json (composite keys, not key shares)."
            fi
        fi
        [[ -n "$stderr_txt" ]] && echo "$stderr_txt"
    fi
}

# Prints list of pubkeys and indices
viewPubkeyAndIndices(){
    local COUNT=${#LIST[@]}

    if [[ "$COUNT" -eq 0 ]]; then
        echo "No validator keys loaded. Press ENTER to finish."
        read -r
        return
    fi

    ohai "==========================================="
    ohai "Total # Validator Keys: $COUNT"
    if isCharonEnabled; then
        ohai "Charon DV: composite validator pubkeys from cluster-lock.json"
    fi
    ohai "Beacon API: ${API_BN_ENDPOINT:-unknown}"
    ohai "==========================================="
    ohai "Pubkeys:"

    for key in "${LIST[@]}"; do
        echo "$key"
    done

    ohai "==========================================="
    ohai "Indices (${#INDICES[@]} found):"

    if [[ ${#INDICES[@]} -gt 0 ]]; then
        echo "${INDICES[@]}"
    elif isCharonEnabled; then
        echo "No validator index on chain yet (or beacon API unreachable — see warnings above)."
        echo "After the distributed validator is deposited and activated on this network, retry."
    else
        echo "No validators currently active. Once a validator is activated, an index is assigned."
    fi

    ohai "Press ENTER to finish."
    read -r
}

# Checks for open ports. Diagnose peering/router/port-forwarding issues.
checkOpenPorts(){
    clear
    [[ -f /etc/systemd/system/execution.service ]] \
        && ! systemctl is-active --quiet execution \
        && echo "${tty_red}WARNING: Execution client service not running. EL port may appear NOT open."
    [[ -f /etc/systemd/system/consensus.service ]] \
        && ! systemctl is-active --quiet consensus \
        && echo "${tty_red}WARNING: Consensus client service not running. CL port may appear NOT open."
    isCharonEnabled \
        && ! systemctl is-active --quiet charon \
        && echo "${tty_red}WARNING: Charon service not running. Charon P2P port may appear NOT open."
    ohai "Checking for Open Ports:"
    ohai "- Properly configuring open ports will improve validator performance and network health."
    ohai "- Test if ports (e.g. 30303, 9000, Charon P2P) are accessible from the Internet."
    ohai "- Test if port forwarding and/or firewalls are properly configured."
    ohai "- Replace defaults with custom or client-specific port numbers as needed."

    local CL_PORT EL_PORT CHARON_PORT CHECK_PORTS
    # Read the ports from user input
    read -r -p "Enter your Consensus Client's P2P port (press Enter to use default 9000): " CL_PORT
    CL_PORT=${CL_PORT:-9000}
    ohai "Using port ${CL_PORT} for Consensus Client's P2P port."
    read -r -p "Enter your Execution Client's P2P port (press Enter to use default 30303): " EL_PORT
    EL_PORT=${EL_PORT:-30303}
    ohai "Using port ${EL_PORT} for Execution Client's P2P port."
    CHECK_PORTS="${EL_PORT},${CL_PORT}"
    if isCharonEnabled; then
        CHARON_PORT=$(getCharonP2pPort)
        if [[ -n "$CHARON_PORT" ]]; then
            CHECK_PORTS="${CHECK_PORTS},${CHARON_PORT}"
            ohai "Including Charon P2P port ${CHARON_PORT} (TCP only)."
        fi
    fi

    # Call port checker
    ohai "Calling https://eth2-client-port-checker.vercel.app/api/checker?ports=${CHECK_PORTS}"
    json=$(curl -s "https://eth2-client-port-checker.vercel.app/api/checker?ports=${CHECK_PORTS}")

    # Parse JSON using jq and print requester IP
    ohai "Your IP: $(echo "$json" | jq -r .requester_ip)"

    # Parse JSON using jq and check if any open ports exist
    if $(echo "$json" | jq -e '.open_ports[]' > /dev/null 2>&1); then
      ohai "Open ports found:"
      echo "$json" | jq -r '.open_ports[]' | while read port; do echo $port; done
    else
      ohai "No open ports found."
    fi
    ohai "Press ENTER to finish."
    read
}

# Find largest disk usage
findLargestDiskUsage(){
  # Install ncdu if not installed
  if ! command -v ncdu >/dev/null 2>&1 ; then sudo apt-get install ncdu; fi
  clear
  # Explain ncdu's purpose
  ohai "ncdu (NCurses Disk Usage) is a disk usage analysis tool that runs on the Linux command line interface (CLI)."
  echo "- Provides an interactive, graphical display of your file system's directory content and their respective sizes."
  echo "- Navigate through your directories to see a detailed breakdown of file and folder sizes in a tree-like hierarchy."
  echo "- This tool is particularly useful for finding large files or folders that are consuming excessive storage space on your Linux systems."
  ohai "Press ENTER to run ncdu."
  read
  # Run ncdu on root directory
  ncdu /
}

testAndSystemctlCommand() {
  local _service
  for _service in "${_SERVICES[@]}"; do
    test -f /etc/systemd/system/"${_service}".service && sudo systemctl "$1" "${_service}"
  done
}
 
# Configure autostart of services
configureAutoStart(){
    clear
    echo "${tty_bold}Enable node to autostart when system boots up? [y|n]${tty_reset}" 
    read -rsn1 yn
    if [[ ${yn} = [Yy]* ]]; then
        testAndSystemctlCommand enable
        ohai "Enabled node's systemd services. Node will autostart at boot."
    else
        testAndSystemctlCommand disable
        ohai "Disabled node's systemd services. Node will not autostart at boot."
    fi
    read -r -p "Press ENTER to continue"
    echo
}

# Checks whether a validator pubkey is registered on all relays found in mevboost.service
checkRelayRegistration(){
    #Variables
    URL_PATH="relay/v1/data/validator_registration?pubkey="

    # Check for mevboost installation
    if [ ! -f /etc/systemd/system/mevboost.service ]; then echo "No relays to check. Mevboost service not installed."; exit 1; fi;

    # Extract relay urls from mevboost.service, store in array
    RELAYS=($(cat /etc/systemd/system/mevboost.service  | sed "s/ /\n/g" | sed -n "/https.*@/p"))
    ohai "Found # of relays in mevboost.service:  ${#RELAYS[@]}"

    # Populate pubkeys into LIST
    getPubKeys
    if [ ${#LIST[@]} -gt 0 ]; then
        # Query checks with the first pubkey
        VALIDATOR_KEY=${LIST[0]}
    else
        echo "No validator pubkeys detected."
        exit 1
    fi
    ohai "To check for relay registration, using the first pubkey: $VALIDATOR_KEY"

    for INDEX in ${!RELAYS[@]}
       do
          # Strip out the relays domain name
          URL_BASE=$(echo ${RELAYS[INDEX]} | sed 's/.*@\(.*\)/https:\/\/\1/')
          # Build relay registration check url
          URL_CHECK=${URL_BASE}/${URL_PATH}${VALIDATOR_KEY}
          # Print out if registered to relay or not
          if [ "$(curl --max-time 10 -Ls ${URL_CHECK} | jq .code)"  = null ]; then
             echo "Relay $((INDEX+1)): $URL_BASE ✅"
          else
             echo "Relay $((INDEX+1)): $URL_BASE ❌"
          fi
       done
    ohai "Relay check complete"
    ohai "Press ENTER to continue"
    read
}

addSwapfile(){
    # Check if there is already an active swap file
    if [ "$(swapon --show | wc -l)" -eq "0" ]; then
        # Prompt the user for the swap file size
        read -r -p "Enter the size of the swap file (e.g. '8G' for 8GB). Press Enter to use default, 8G: " SWAP_SIZE
        SWAP_SIZE=${SWAP_SIZE:-8G}

        # Prompt the user for the swap path
        read -r -p "Enter the path of the swap file (e.g. /swapfile). Press Enter to use default '/swapfile': " SWAP_PATH
        SWAP_PATH=${SWAP_PATH:-/swapfile}

        # Create the swap file at ${SWAP_PATH} with the given size
        sudo fallocate -l "${SWAP_SIZE}" ${SWAP_PATH}

        # Change the permissions to read and write for root
        sudo chmod 600 ${SWAP_PATH}

        # Format the file as swap space
        sudo mkswap ${SWAP_PATH}

        # Enable swapping on the new file and remember the setting persistently across reboots
        sudo swapon ${SWAP_PATH}
        echo "${SWAP_PATH} swap swap defaults 0 0" | sudo tee -a /etc/fstab > /dev/null
        echo "Swap file created."

        # Update Swappiness
        echo "Lower RAM Swappiness to 10"
        # Temporarily change the swappiness value
        sudo sysctl vm.swappiness=10
        # Make the change permanent using sysctl.d
        sudo mkdir -p /etc/sysctl.d
        echo "vm.swappiness = 10" | sudo tee /etc/sysctl.d/99-swappiness.conf > /dev/null
    else
        echo "Swap is already enabled."
    fi
    ohai "Press ENTER to continue"
    read
}

generateVoluntaryExitMessage(){
    local VEM_PATH=$HOME/voluntary-exit-messages
    clear
    echo "################################################################"
    ohai "Generate a Voluntary Exit Message (VEM) for each validator."
    echo "################################################################"
    ohai "Before starting: Validators must be currently active and assigned a validator index."
    echo ""
    ohai "Requirements: To generate voluntary exit messages, have the following ready:"
    echo "1) A path to the directory containing your keystore-m_####.json file(s)"
    echo "2) The keystore's passphrase"
    echo ""
    ohai "Note: “passphrase” is NOT your mnemonic or secret recovery phrase!"
    echo ""
    ohai "Result of this operation:"
    echo "- One VEM file (e.g. exit_validator_index_#.json) per validator is generated."
    echo "- VEMs do not expire and are valid throughout future forks/upgrades."
    echo "- This operation does NOT broadcast your VEM and consequently, exit your validator."
    echo ""
    ohai "Next steps:"
    echo "- When it's time to exit your validator, broadcast the VEM locally or with beaconcha.in tool"
    echo "- Backup and save VEMs to external storage. (e.g. USB drive)"
    echo "- Share with your heirs."
    echo "- For more information on what happens AFTER broadcasting a VEM with detailed timelines, see:"
    echo "  https://docs.coincashew.com/guides/voluntary-exiting-a-validator"
    echo ""
    echo "${tty_bold}Do you wish to continue? [y|n]${tty_reset}"
    read -rsn1 yn
    if [[ ${yn} = [Yy]* ]]; then
        # Create path to store VEMs
        [[ -d $HOME/voluntary-exit-messages ]] || mkdir -p $VEM_PATH

        # Prompt user for path to keystores
        read -r -p "Enter path to your keystore-m_##.json file(s): " KEYSTORE_PATH
        # Check number of keystores
        local COUNT=$(ls "${KEYSTORE_PATH}"/keystore*.json | wc -l)
        if [[ $COUNT -gt 0 ]]; then
            echo "INFO: Found $COUNT keystore files"
            echo "INFO: Using keystore path: $KEYSTORE_PATH"
        else
            echo "No keystores found at $KEYSTORE_PATH"
            ohai "Press ENTER to continue"
            read
            exit 1
        fi

        # Prompt user for keystore passphrase
        read -r -p "Enter keystore passphrase: " KEYSTORE_PASSPHRASE
        echo "INFO: Using keystore passphrase: $KEYSTORE_PASSPHRASE"

        # Iterate through each file and create the VEM
        for KEYSTORE in "${KEYSTORE_PATH}"/keystore*.json;
        do
           ethdo --allow-insecure-connections --connection ${API_BN_ENDPOINT} validator exit --validator=${KEYSTORE} "--passphrase=${KEYSTORE_PASSPHRASE}" --json > $VEM_PATH/exit_tmp.json
           INDEX=$(cat $VEM_PATH/exit_tmp.json | jq -r .message.validator_index)
           if [[ $INDEX =~ ^[0-9]+$ ]]; then
               # Rename exit file with validator index
               mv $VEM_PATH/exit_tmp.json $VEM_PATH/exit_validator_index_${INDEX}.json
               echo "INFO: Generated voluntary exit message for index ${INDEX}"
           else
               echo "ERROR: Unable to retrieve Validator INDEX"
               exit 1
           fi
        done
        echo "${tty_bold}${COUNT} Voluntary exit message(s) saved at: $VEM_PATH${tty_reset}"
    else
        echo "Cancelled."
    fi
    ohai "Press ENTER to continue"
    read
}

broadcastVoluntaryExitMessageLocally(){
    local VEM_PATH_DEFAULT=$HOME/voluntary-exit-messages
    clear
    echo "################################################################"
    ohai "Broadcast Voluntary Exit Message (VEM)"
    echo "################################################################"
    ohai "Requirements: To broadcast voluntary exit messages, have the following ready:"
    echo "1) A path to the directory containing your VEM file(s) e.g. exit_validator_index_#####.json"
    echo ""
    ohai "Result of this operation:"
    echo "- Exit Queue: Your validator(s) will soon no longer be responsible for attesting/proposing duties."
    echo "- Irreversible: This operation exits your validator permanently."
    echo ""
    ohai "Next steps:"
    echo "- Status: Keep validator processes running until a validator has fully exited the exit queue."
    echo "- Verification: Using ethdo or beaconcha.in, check your validator's status to confirm exiting status. e.g. Status: active_exiting"
    echo "- Balances: Validator's balance will be swept to your withdrawal address."
    echo "- Wait time: Check estimated exit queue wait times at https://www.validatorqueue.com"
    echo "- Timelines: For more detailed sequence of events, see:"
    echo "  https://docs.coincashew.com/guides/voluntary-exiting-a-validator"
    echo ""
    echo "${tty_bold}Do you wish to continue? [y|n]${tty_reset}"
    read -rsn1 yn
    if [[ ${yn} = [Yy]* ]]; then
        # Prompt user for path to VEMs
        read -r -p "Enter path to your VEM file(s) (Press enter to use default: $VEM_PATH_DEFAULT):" VEM_PATH
        VEM_PATH=${VEM_PATH:-$VEM_PATH_DEFAULT}
        # Check number of VEM (exit*.json) files
        local COUNT=$(ls "${VEM_PATH}"/exit*.json | wc -l)
        if [[ $COUNT -gt 0 ]]; then
            echo "INFO: Found $COUNT VEM files"
            echo "INFO: Using VEM path: $VEM_PATH"
        else
            echo "No VEMs found at $VEM_PATH"
            ohai "Press ENTER to continue"
            read
            exit 1
        fi

        # Final confirmation
        if whiptail --title "Broadcast Voluntary Exit Messages" --defaultno --yesno "This will voluntary exit ${COUNT} validator(s).\nAre you sure you want to continue?" 9 78; then
            # Iterate through each file and broadcast the VEM
            for VEM in "${VEM_PATH}"/exit*.json;
            do
                INDEX=$(cat $VEM | jq -r .message.validator_index)
                if [[ $INDEX =~ ^[0-9]+$ ]]; then
                   ethdo --allow-insecure-connections --connection ${API_BN_ENDPOINT} validator exit --signed-operations ${VEM}
                   echo "INFO: Broadcast VEM for index ${INDEX}"
                else
                   echo "ERROR: Unable to retrieve Validator INDEX. Broadcast failed."
                   exit 1
                fi
            done
            echo "${tty_bold}${COUNT} Voluntary exit message(s) broadcasted.${tty_reset}"
        fi
    else
        echo "Cancelled."
    fi
    ohai "Press ENTER to continue"
    read
}

# Takes a validator index # and checks status with ethdo
checkValidatorStatus(){
    local _INDEX=""
    clear
    echo "#############################################################################"
    ohai "Validator Status: Given a validator index #, checks the status with ethdo"
    echo "#############################################################################"
    ohai "Key Points:"
    echo "* Your validator will receive a unique index # after going live."
    echo "* Until then, you'll need to use the public key to access it's status at beaconcha.in directly."
    echo "* A validator can be identified by either its public key or its index #."
    # Get validator index from user
    while true; do
    read -r -p "${tty_blue}Enter your Validator's Index: (Press enter for example)${tty_reset} " _INDEX
    _INDEX=${_INDEX:-1337}
    ethdo --connection ${API_BN_ENDPOINT} validator info --validator=${_INDEX}
    read -r -p "${tty_blue}Check another index? (y/n) ${tty_reset}" yn
    case ${yn} in
      [Nn]*) break ;;
          *) continue ;;
    esac
    done
}

# Takes a validator index # or pubkey and checks attestation inclusion
checkValidatorAttestationInclusion(){
    local _INDEX=""
    clear
    echo "#############################################################################"
    ohai "Attestation Performance: Obtain information about attester inclusion"
    echo "#############################################################################"
    ohai "Key Points:"
    echo "* Timely: Validators are called to attest (or vote) only once every epoch."
    echo "* Correctness: When attesting, validators vote on their version of the perceived state of the chain, namely the source, head and target."
    echo "* Inclusion delay: Ideally, 1. The number of slots separating the block proposal and attestation."
    # Get validator index from user
    while true; do
    read -r -p "${tty_blue}Enter your Validator's Index or public key: (Press enter for example)${tty_reset} " _INDEX
    _INDEX=${_INDEX:-1337}
    read -r -p "${tty_blue}Enter epoch: (Press enter for last epoch)${tty_reset} " _EPOCH
    _EPOCH=${_EPOCH:-"-1"}
    ethdo --connection ${API_BN_ENDPOINT} attester inclusion --validator=${_INDEX} --epoch=${_EPOCH} --verbose
    read -r -p "${tty_blue}Check another validator or epoch? (y/n) ${tty_reset}" yn
    case ${yn} in
      [Nn]*) break ;;
          *) continue ;;
    esac
    done
}

# Install ethdo if not yet installed
installEthdo(){
    if [[ ! -f /usr/local/bin/ethdo ]]; then
      if whiptail --title "Install ethdo" --yesno "Do you want to install ethdo?\n\nethdo helps you check validator status, generate and broadcast exit messages." 10 78; then
        runScript ethdo.sh -i
      else
        break
      fi
    fi
}

# Display peer count information from EL and CL
getPeerCount(){
    declare -A _peer_status=()
    local _warn=""
    # Get peer counts from CL and EL
    _peer_status["Consensus_Layer_Connected_Peer_Count"]="$(curl -sf -m 2 -X GET "${API_BN_ENDPOINT}/eth/v1/node/peer_count" -H "accept: application/json" 2>/dev/null | jq -r ".data.connected")"
    if [[ -f /etc/systemd/system/execution.service ]]; then
        _peer_status["Execution_Layer_Connected_Peer_Count"]="$(curl -sf -m 2 -X POST -H "Content-Type: application/json" --data '{"jsonrpc": "2.0", "method":"net_peerCount", "params": [], "id":1}' "${EL_RPC_ENDPOINT}" 2>/dev/null | jq -r ".result" | mawk '{printf "%d\n",$1}')"
    fi
    # Get CL peers by direction
    _json_cl=$(curl -sf -m 2 "${API_BN_ENDPOINT}/eth/v1/node/peers" 2>/dev/null | jq -c '.data')
    _peer_status["Consensus_Layer_Known_Inbound_Peers"]=$(jq -c '.[] | select(.direction == "inbound")' <<< "$_json_cl" | wc -l)
    _peer_status["Consensus_Layer_Known_Outbound_Peers"]=$(jq -c '.[] | select(.direction == "outbound")' <<< "$_json_cl" | wc -l)

    # Print each peer status
    for _key in ${!_peer_status[@]}
      do
        if [[ ${_peer_status[$_key]} -gt 0 ]]; then printf "[${tty_blue}✔${tty_reset}]"; else printf "[${tty_red}✗${tty_reset}]" && _warn=1; fi
        echo " ${tty_blue}[$_key]${tty_bold}: ${_peer_status[$_key]} peers${tty_reset}"
      done
    [[ ! -z ${_warn} ]] && echo "Suboptimal connectivity may affect validating nodes. To resolve, restart the service and check port forwarding, firewall-router settings, public IP, ENR."
    ohai "Press ENTER to continue"
    read
}

# Create Beaconcha.in Validator Dashboard Link
createBeaconChainDashboardLink(){
    getPubKeys
    getIndices
    local _ids=$(echo ${INDICES[@]} | sed  's/ /,/g')
    case ${NETWORK,,} in
       holesky)
          _link="https://holesky.beaconcha.in/dashboard?validators=" ;;
       mainnet)
          _link="https://beaconcha.in/dashboard?validators=" ;;
       ephemery)
          _link="https://beaconchain.ephemery.dev/dashboard?validators=" ;;
       hoodi)
          _link="https://hoodi.beaconcha.in/dashboard?validators=" ;;
       *)
          echo "Unsupported Network: ${NETWORK}" && exit 1
    esac
    _linkresult=${_link}${_ids}
    ohai "Beaconcha.in Validator Dashboard: Copy and paste your link into a web browser. Bookmark."
    echo ${_linkresult}
    ohai "Press ENTER to continue"
    read
}

testBandwidth(){
    clear
    echo "################################################################"
    ohai "Test internet bandwidth using speedtest.net"
    echo "################################################################"
    ohai "Requirements: A full node uses at least 10Mbit/s upload and 10Mbit/s download."
    ohai "Starting speedtest ..."
    curl -s https://raw.githubusercontent.com/sivel/speedtest-cli/master/speedtest.py | python3 -
    ohai "Press ENTER to continue"
    read
}

testYetAnotherBenchScript(){
    clear
    echo "#######################################################"
    ohai "Yet-Another-Bench-Script - yabs.sh"
    echo "#######################################################"
    ohai "Automated Benchmarking: Runs popular tools to test node performance"
    echo "- Multi-Test Suite: It includes tests for:"
    echo "  * Disk performance using fio"
    echo "  * Network performance using iperf3"
    echo "  * CPU/memory performance using Geekbench"
    echo "- No External Dependencies Required: No additional downloads"
    ohai "Reminder: Full node requirements"
    echo "- Network:"
    echo "  * Bandwidth should be at least 10Mbit/s upload and 10Mbit/s download"
    echo "  * At least 2TB data transfer per month"
    echo "- Disk:"
    echo "  * Capacity at least 2TB Mainnet, 50GB Hoodi testnet, 3GB Ephemery testnet"
    echo "  * NVME drive preferred, SSD with TLC cache can work"
    echo "  * I/O Per Second on 4k block size test at least 15K IOPS read, 5K IOPS write"
    echo "- CPU:"
    echo "  * At least 2 cores, 4 threads"
    echo "  * Geekbench 6 scores at least 700 single core score, 1400 multi-core score"
    echo "- RAM:"
    echo "  * At least 16GB. 32GB for future-proofing."
    ohai "Testing completes in 30 minutes max, generally around 10 minutes."
    echo "${tty_bold}Do you wish to continue? [y|n]${tty_reset}"
    read -rsn1 yn
    if [[ ${yn} = [Yy]* ]]; then
      curl -sL yabs.sh | bash
      ohai "Press ENTER to continue"
      read
    fi
}

# Allow external validators to connect to this consensus client (port 5052)
exposeRpcCL(){
    _closed='127.0.0.1'
    _exposed='0.0.0.0'
    _service='consensus'
    _file="/etc/systemd/system/${_service}.service"
    getNetworkConfig

    case "${CL}" in
        Nimbus     ) _flag='--rest-address';;
        Lodestar   ) _flag='--rest.address';;
        Lighthouse ) _flag='--http-address';;
        Grandine   ) _flag='--http-address';;
        Prysm      ) _flag='--http-host';;
        Teku       ) _flag='--rest-api-interface';;
        * ) echo "Consensus client not detected."; return 0;;
    esac

    clear
    echo "###########################################################################"
    ohai "Expose CL RPC: Allowing External Validator Clients to Connect to this Node"
    echo "###########################################################################"
    ohai "Purpose:"
    echo "To allow attaching an external Validator client to your node's Consensus client, enable this feature."
    echo "This will open up RPC ports (default 5052 for HTTP) on your node, allowing other machines on your local network to connect."
    echo "For example, you can access this node's Consensus client (also called beacon-node) URL at http://${ip_current}:5052"
    echo "When running multiple pairs of execution and consensus clients for client diversity or redundancy purposes, a staker may want to connect their Validator Client to multiple beacon nodes."
    echo ""
    ohai "Result of this operation:"
    echo "- Flag Change:  This will modify ${CL}'s flag: ${_flag}"
    echo "- Restarts ${_service} client for changes to take effect."
    ohai "Next Steps:"
    echo "- Review UFW firewall settings. Whitelist the connecting machine's IP or allow local LAN access."
    echo "${tty_bold}Do you wish to continue? [y|n]${tty_reset}"
    read -rsn1 yn
    if [[ ${yn} = [Nn]* ]]; then return 0; fi

    echo "${tty_bold}Do you wish to expose ${CL} RPC Port? This will modify ${_flag} and restart ${_service} client. Answer n to revoke access.[y|n]${tty_reset}"
    read -rsn1 yn
    if [[ ${yn} = [Yy]* ]]; then
        _value=${_exposed}
        ohai "Exposing $CL RPC Access with flag: ${_flag}"
    else
        _value=${_closed}
        ohai "Closing $CL RPC Access with flag: ${_flag}"
    fi

    _updateFlagAndRestartService
}

# Allow external EL RPC access (port 8545)
exposeRpcEL(){
    _closed='127.0.0.1'
    _exposed='0.0.0.0'
    _service='execution'
    _file="/etc/systemd/system/${_service}.service"
    getNetworkConfig

    case "${EL}" in
        Nethermind ) _flag='--JsonRpc.Host';;
        Besu       ) _flag='--rpc-http-host';;
        Erigon     ) _flag='--http.addr';;
        Geth       ) _flag='--http.addr';;
        Reth       ) _flag='--http.addr';;
        Ethrex     ) _flag='--http.addr';;
        * ) echo "Execution client not detected"; return 0;;
    esac

    clear
    echo "###########################################################################"
    ohai "Expose EL RPC: Allowing External Access to Connect to this Node"
    echo "###########################################################################"
    ohai "Purpose:"
    echo "To allow access from an external service to your node's Execution client, enable this feature."
    echo "This will open up RPC ports (default 8545 for HTTP) on your node, allowing other machines on your local network to connect."
    echo "For example, you can access this node's Execution client URL at http://${ip_current}:8545"
    echo "A common use case is configuring your ETH wallet to use your own node as a RPC endpoint."
    echo ""
    ohai "Result of this operation:"
    echo "- Flag Change:  This will modify ${EL}'s flag: ${_flag}"
    echo "- Restarts ${_service} client for changes to take effect."
    ohai "Next Steps:"
    echo "- Review UFW firewall settings. Whitelist the connecting machine's IP or allow local LAN access."
    echo "${tty_bold}Do you wish to continue? [y|n]${tty_reset}"
    read -rsn1 yn
    if [[ ${yn} = [Nn]* ]]; then return 0; fi

    echo "${tty_bold}Do you wish to expose ${EL} RPC Port? This will modify ${_flag} and restart ${_service} client. Answer n to revoke access. [y|n]${tty_reset}"
    read -rsn1 yn
    if [[ ${yn} = [Yy]* ]]; then
        _value=${_exposed}
        ohai "Exposing $EL RPC Access with flag: ${_flag}=${_value}"
    else
        _value=${_closed}
        ohai "Closing $EL RPC Access with flag: ${_flag}=${_value}"
    fi

    _updateFlagAndRestartService
}

# Helper function for Exposing RPC ports
_updateFlagAndRestartService(){
    sudo test -f "${_file}" || return 0
    if sudo grep -q -e "${_flag}=${_value}" "${_file}"; then
      info "✅ Already configured with ${_flag}=${_value}. Nothing to change."
    else
      info "🔧 Updating ${_service} service file..."
      # Check if multiline configuration file that ends with \
      if grep -q 'ExecStart.*\\$' "${_file}"; then
        # Remove old value; match any non-whitespace value, not just IPs
        sudo sed -i -r "s|${_flag}[= ]+[^ \\]+[ ]*[\\]*||" "${_file}"
        sudo sed -i '/^[[:space:]]*$/d' "${_file}"
        # Add new value after ExecStart line with \
        sudo sed -i -e "/^ExecStart=/a\  ${_flag}=${_value} \\\\" "${_file}"
      else
        # Remove old value; match any non-whitespace value, not just IPs
        sudo sed -i -r "s|${_flag}[= ]+[^ \\]+[ ]*[\\]*||" "${_file}"
        # Add new value to end of ExecStart line
        sudo sed -i "s|^ExecStart.*$|& ${_flag}=${_value}|" "${_file}"
      fi
      # Reload and restart
      sudo systemctl daemon-reload && sudo service "${_service}" restart
      info "✅ Configuration change complete: ${_flag}=${_value}"
    fi
    sleep 5
}

# Returns yield per validator
ethdoYield(){
    ethdo validator --connection ${API_BN_ENDPOINT} yield
    ohai "Current yield per validator (APY). Press ENTER to continue"
    read
}

# Returns expectation between block proposals, sync committee duties
ethdoExpectation(){
    read -r -p "${tty_blue}How many validators do you have? (Press enter for example of 1)${tty_reset} " _NUM
    _NUM=${_NUM:-1}
    ethdo validator --connection ${API_BN_ENDPOINT} expectation --validators=${_NUM}
    ohai "Expectation is based on current # of active validators on the Ethereum network. Press ENTER to continue"
    read
}

# Returns time until next withdrawal sweep for given validator
ethdoNextWithdrawalSweep(){
    clear
    echo "########################################################################################"
    ohai "Next Withdrawal: Obtains when next withdrawal occurs"
    echo "########################################################################################"
    ohai "Key Points:"
    echo "* Withdrawals: Every block, 16 withdrawals are processed."
    echo "* Withdrawal Cycle: The order of withdrawals happens in a cycle, ordered by validator index"
    read -r -p "${tty_blue}Enter your Validator's Index or pubkey: (Press enter for example)${tty_reset} " _INDEX
    _INDEX=${_INDEX:-1337}
    ethdo validator --connection ${API_BN_ENDPOINT} withdrawal --validator=${_INDEX}
    ohai "Results for Validator # ${_INDEX} ~ Press ENTER to continue"
    read
}

# Returns withdrawal address for given validator
ethdoWithdrawalAddress(){
    read -r -p "${tty_blue}Enter your Validator's Index or pubkey: (Press enter for example)${tty_reset} " _INDEX
    _INDEX=${_INDEX:-1337}
    ethdo validator --connection ${API_BN_ENDPOINT} credentials get --validator=${_INDEX}
    ohai "Results for Validator # ${_INDEX} ~ Press ENTER to continue"
    read
}

calculate_days_hours_and_minutes() {
    local total_days=$1

    # Check if the input is a valid number
    if ! [[ "$total_days" =~ ^([0-9]+)?(\.[0-9]+)?$ ]]; then
        echo "Error: Input must be a valid number."
        return 1
    fi

    # Calculate the total number of minutes
    local total_minutes
    total_minutes=$(echo "$total_days * 24 * 60" | bc)

    # Calculate the number of days, hours, and remaining minutes
    local days
    days=$(echo "$total_days / 1" | bc)
    local remaining_minutes
    remaining_minutes=$(echo "$total_minutes % (24 * 60)" | bc)
    local hours
    hours=$(echo "$remaining_minutes / 60" | bc)
    local minutes
    minutes=$(echo "$remaining_minutes % 60" | bc)

     # Ensure minutes is an integer
    minutes=$(echo "$minutes / 1" | bc)

    # Format the output
    if (( $(echo "$days >= 1" | bc -l) )); then
        echo "$days days, $hours hours and $minutes minutes"
    elif (( $(echo "$hours >= 1" | bc -l) )); then
        echo "$hours hours and $minutes minutes"
    else
        echo "$minutes minutes"
    fi
}

# Checks validator queue by querying beaconcha.in
checkValidatorQueue(){
    BEACONCHAIN_VALIDATOR_QUEUE_API_URL="/api/v1/validators/queue"
    declare -A BEACONCHAIN_URLS=(
        ["Mainnet"]="https://beaconcha.in"
        ["Holesky"]="https://holesky.beaconcha.in"
        ["Hoodi"]="https://hoodi.beaconcha.in"
        ["Ephemery"]="https://beaconchain.ephemery.dev"
    )
    # Validate network mapping
    if [[ -z "${BEACONCHAIN_URLS["${NETWORK}"]}" ]]; then
        echo "Error: Unsupported Network '${NETWORK}' for validator queue queries." >&2
        return 1
    fi

    # Pectra churn values
    local CHURN_LIMIT_PER_EPOCH=256
    local CHURN_LIMIT_PER_DAY=57600

    # helper function
    display_queue() {
      local label=$1 count=$2 wait_time
      ohai "${label} Queue"
      echo "ETH ${label}: $count"
      if (( count > 0 )); then
        wait_time=$(calculate_days_hours_and_minutes "$(echo "scale=6; $count / $CHURN_LIMIT_PER_DAY" | bc)")
      else
        wait_time="No wait"
      fi
      echo "Estimated wait time: $wait_time"
      echo "Churn: ${CHURN_LIMIT_PER_EPOCH} ETH per epoch or ${CHURN_LIMIT_PER_DAY} ETH per day"
    }

    # Query for data
    local json entering exiting count
    if ! json=$(curl -fsSL "${BEACONCHAIN_URLS["${NETWORK}"]}"${BEACONCHAIN_VALIDATOR_QUEUE_API_URL}); then
        echo "ERROR: Validator Queue API request failed." >&2
        return 1
    fi

    # Parse JSON using jq and print data
    if echo "$json" | jq -e 'has("data") and .data.beaconchain_entering != null' > /dev/null; then
        entering=$(echo "$json" | jq -r '.data.beaconchain_entering')
        exiting=$(echo "$json" | jq -r '.data.beaconchain_exiting')
        count=$(echo "$json" | jq -r '.data.validatorscount')
        echo "#######################################################"
        ohai "${NETWORK} ETH Entry/Exit Queue Stats"
        echo "#######################################################"
        ohai "Reminder: Important Timing Consideration"
        echo "- Wait for Beacon Node Sync: Before making a deposit, ensure your beacon node is synced to avoid missing rewards."
        echo "- Timing of Validator Activation: After depositing, it takes about ~13 minutes for a validator to be activated unless there's a long entry queue."
        echo "- Timing of Validator Exiting: After initiating an exit by broadcasting a VEM, it takes validator a minimum of 4 epochs to be exited unless there's a long exit queue."
        display_queue "Entering" "$entering"
        display_queue "Exiting" "$exiting"
        ohai "Total Active Validator Count: $count"
    else
      ohai "Unable to query beaconcha.in for $NETWORK validator queue data."
    fi
    ohai "Press ENTER to continue."
    read -r
}

# Checks local latency of relays found in mevboost.service file
checkRelayLatency(){
    echo "###########################################################"
    ohai "Relay Latency Check: Tests response time to each relay"
    echo "###########################################################"

    # Initialize warning flag
    local _warn=0

    # Check if mevboost service is installed
    if [ ! -f "/etc/systemd/system/mevboost.service" ]; then
      echo "No relays to check. Mevboost service not installed."
      exit 1
    fi

    # Extract relay URLs from mevboost.service, store in array
    RELAYS=( $(cat /etc/systemd/system/mevboost.service | tr -s ' ' '\n' | grep -o "https.*@.*") )

    ohai "Found ${#RELAYS[@]} relays in mevboost.service"

    for (( i=0; i<${#RELAYS[@]}; i++ )); do
      # Get relay domain name
      URL=${RELAYS[i]##*@}
      # Calculate response time in milliseconds using curl and awk
      LATENCY=$(curl -s -w %{time_total} -o /dev/null "https://${URL}/relay/v1/data/bidtraces/proposer_payload_delivered?limit=1")
      # Convert to millisec integer
      LATENCY=$(echo "$LATENCY*1000/1" | bc)
      # Check response time and assign emoji based on latency
      if (( LATENCY < 500 )); then
        EMOJI="✅"
      elif (( LATENCY < 1000 )); then
        EMOJI="⚠️"
        _warn=1
      else
        EMOJI="❌"
        _warn=1
      fi

      # Print relay information with emoji and response time
      echo "Relay $((i+1)) - ${URL}: $LATENCY ms $EMOJI"
    done

    # If any relays have high latency, warn user to consider removing distant relays
    if [[ ${_warn} -eq 1 && "${NODE_MODE}" != "Lido CSM Staking Node" ]]; then
      echo "${tty_bold}When relays are distant from your node, response times can be high. Consider removing relays with ⚠️ or ❌."
    fi
    ohai "Relay latency check complete."
    ohai "Press ENTER to continue"
    read
}

# True when a local consensus systemd unit is installed.
hasConsensusService(){
    [[ -f "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}" ]]
}

# Low-disk guidance when consensus.service is absent. Do not recommend CL resync
# or chain-growth storage upgrades (no local EL/CL data to grow into TB scale).
lowDiskSpaceTipsNoConsensusResync(){
    local tips=" - Free disk space: Remove unused files, logs, and leftover client data to reclaim storage.
 - NCDU: Find large files and analyze disk usage from the EthPillar toolbox."
    if isCharonEnabled; then
        tips+=$'\n'" - This node runs Obol Charon (distributed validator). There is no local consensus client to resync."
    elif [[ -f "${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}" ]]; then
        tips+=$'\n'" - This is a validator-client-only node. There is no local consensus client to resync."
    fi
    printf '%s' "$tips"
}

# Checks disk space. Offers checkpoint sync only when a local consensus client exists.
# THRESHOLD, MOUNT_POINTS, and ALERT_FILE are set in env or .env.overrides.
checkDiskSpace(){
    # Clear the alert file at the beginning
    > "$ALERT_FILE"

    for MOUNT in "${MOUNT_POINTS[@]}"; do
        if df -h "$MOUNT" &> /dev/null; then
            FREE_GB=$(df -h "$MOUNT" | awk 'NR==2 {print $4}')
            FREE_PERCENT=$(df -h "$MOUNT" | awk 'NR==2 {print $5}' | sed 's/%//')
            if [ "$FREE_PERCENT" -ge $((100 - THRESHOLD)) ]; then
                echo -e "WARNING: $MOUNT has only $((100 - FREE_PERCENT))% free space left ($FREE_GB)." >> "$ALERT_FILE"
            else
                echo -e "\e[32mINFO: $MOUNT has sufficient free space: $FREE_GB ($((100 - FREE_PERCENT))% free).\e[0m" >> "$ALERT_FILE"
            fi
        else
            echo -e "\e[31mERROR: $MOUNT is not mounted.\e[0m" >> "$ALERT_FILE"
        fi
    done

    if [[ $(grep --ignore-case -oE "WARNING" "$ALERT_FILE") ]]; then
        if hasConsensusService; then
            if whiptail --title "Low Disk Space Detected" --yesno "$(cat "$ALERT_FILE")\n\nRecommend to resync consensus client. Proceed?" 10 78; then
                runScript resync_consensus.sh
                MSG_TIPS=" - Consensus resync complete. If disk is still low, resync the execution client from the execution client menu (this can take hours to days), run NCDU from the toolbox, or upgrade storage.
\n - Upgrade Storage: Until portal clients are available, upgrading to 4TB NVME is the best option for the foreseeable future."
                whiptail --title "Tips: Disk Space" --msgbox "$MSG_TIPS" 12 78
            fi
        else
            whiptail --title "Low Disk Space Detected" --msgbox "$(cat "$ALERT_FILE")\n\n$(lowDiskSpaceTipsNoConsensusResync)" 16 78
        fi
    else
        # Notify completion
        ohai ">> Free space check results:"
        cat "$ALERT_FILE"
    fi
}

# Checks and outputs CPU Load. Notify user if load is high.
checkCPULoad(){
    cpus=$(lscpu | grep -e "^CPU(s):" | cut -f2 -d: | awk '{print $1}')
    cpu_threshold=$(echo "scale=2;${cpus} * 0.9"| bc -l)
    ohai ">> CPU Load Avg check results:"
    cat <<EOF
##############################################################
CPU Load Avg Check :   <$cpu_threshold Normal,  >$cpu_threshold Caution,  >$cpus Unhealthy
# of CPUs : $cpus
##############################################################
CPU Load Average : $(uptime | awk -F'load average:' '{ print $2 }' | cut -f1 -d,)
CPU Heath Status : $(uptime | awk -F'load average:' '{ print $2 }' | cut -f1 -d, | awk -v num="$cpu_threshold" -v num2="$cpus" '{if ($1 < num) print "✅ Normal"; else if ($1 > num2) print "❌ Unhealthy"; else print "⚠️ Caution"}')
EOF
}

# Explain Validator Actions and Topup features
showValidatorActions(){
    [[ -z $NETWORK ]] && error "Unable to determine NETWORK"
    local VA_PATH="/en/validator-actions"
    local TOPUP_PATH="/en/top-up"
    declare -A VALIDATOR_ACTION_URLS=()
    VALIDATOR_ACTION_URLS["Mainnet"]="https://launchpad.ethereum.org"
    VALIDATOR_ACTION_URLS["Hoodi"]="https://hoodi.launchpad.ethereum.org"
    VALIDATOR_ACTION_URLS["Holesky"]="https://holesky.launchpad.ethereum.org"
    VALIDATOR_ACTION_URLS["Ephemery"]="https://launchpad.ephemery.dev"
    local VA_URL=${VALIDATOR_ACTION_URLS["${NETWORK}"]}${VA_PATH}
    local TOPUP_URL=${VALIDATOR_ACTION_URLS["${NETWORK}"]}${TOPUP_PATH}
    local MSG="Visit the link below with your browser and connect your withdrawal address wallet to

- upgrade to compounding validator (0x02),
- consolidate validator(s),
- make a partial withdrawal,
- top up / add ETH to validator balance,
- force an exit

Actions: $VA_URL
  TopUp: $TOPUP_URL"
    whiptail --title "Validator Actions: New features since Pectra Upgrade" --msgbox "$MSG" 18 78
}

# Return 0 when the current user can read system journal entries without sudo.
can_read_journal() {
    if [[ "$(id -u)" -eq 0 ]]; then
        return 0
    fi
    journalctl -n 1 --quiet _UID=0 >/dev/null 2>&1 && return 0
    if user_in_journal_group "$(whoami)"; then
        sg systemd-journal -c 'journalctl -n 1 --quiet _UID=0 >/dev/null' 2>/dev/null && return 0
    fi
    return 1
}

# Return 0 when the named user appears in systemd-journal.
user_in_journal_group() {
    local user="${1:-$(whoami)}"
    getent group systemd-journal 2>/dev/null | grep -qE -o -- "$user"
}

# Return 0 when the user can read system journal entries without sudo.
user_can_read_system_journal() {
    local user="${1:-$(whoami)}"

    if [[ "$user" == "$(whoami)" ]]; then
        can_read_journal && return 0
        if user_in_journal_group "$user"; then
            sg systemd-journal -c 'journalctl -n 1 --quiet _UID=0 >/dev/null' 2>/dev/null && return 0
        fi
        return 1
    fi

    if su - "$user" -c 'journalctl -n 1 --quiet _UID=0 >/dev/null 2>&1'; then
        return 0
    fi
    if user_in_journal_group "$user"; then
        su - "$user" -c "sg systemd-journal -c 'journalctl -n 1 --quiet _UID=0 >/dev/null'" && return 0
    fi
    return 1
}

# Add the current user to systemd-journal when needed. Returns 0 when journal
# access works in this session, 1 when a new login is required.
ensure_journal_access() {
    local current_user
    current_user=$(whoami)

    if [[ "$(id -u)" -eq 0 ]]; then
        return 0
    fi

    if ! user_in_journal_group "$current_user"; then
        sudo usermod -aG systemd-journal "$current_user"
    fi

    can_read_journal
}

_journal_log_colorizer() {
    if command -v ccze >/dev/null 2>&1; then
        ccze -A
    else
        cat
    fi
}

# Run journalctl with sudo only when unprivileged access is unavailable.
journalctl_run() {
    if can_read_journal; then
        journalctl "$@"
        return $?
    fi

    ensure_journal_access || true
    if can_read_journal; then
        journalctl "$@"
        return $?
    fi

    if user_in_journal_group "$(whoami)"; then
        local _jcmd=(journalctl "$@")
        sg systemd-journal -c "$(printf '%q ' "${_jcmd[@]}")"
        return $?
    fi

    sudo journalctl "$@"
}

# Build a journalctl … | ccze -A pipeline for tmux/log panes (sg when group is new).
journalctl_ccze_pipeline() {
    local _args=("$@")
    local _inner="journalctl"
    local _a
    for _a in "${_args[@]}"; do
        _inner+=" $(printf '%q' "$_a")"
    done
    if can_read_journal; then
        printf '%s | ccze -A' "$_inner"
    elif user_in_journal_group "$(whoami)"; then
        printf 'sg systemd-journal -c %q | ccze -A' "$_inner"
    else
        printf 'sudo %s | ccze -A' "$_inner"
    fi
}

view_journal_logs() {
    # Parent ignores SIGINT so EthPillar survives Ctrl-C.
    # Child restores default so journalctl still stops.
    export -f _journal_log_colorizer journalctl_run can_read_journal user_in_journal_group 2>/dev/null || true
    trap '' INT

    bash -c 'trap - INT; journalctl_run "$@" | _journal_log_colorizer' _ "$@" || true

    trap - INT
    return 0
}

# TUI Logging & Monitoring → 🔍 View Rolling Consolidated Logs, and `ethpillar logs`.
# Aztec remote-rpc compose follow, then one journalctl stream for all client units.
show_rolling_consolidated_logs() {
    # Aztec with remote rpc
    if [[ -d /opt/ethpillar/aztec ]] && [[ ! -f /etc/systemd/system/consensus.service ]]; then
          cd  /opt/ethpillar/aztec && docker compose logs -f --tail=233
    fi
    view_journal_logs -u validator -u consensus -u execution -u mevboost -u charon -u csm_nimbusvalidator --no-hostname -f
}

# Function to display log dialog and return the selected option
function get_user_input() {
    local OPTIONS=()
    local service date_range
    test -f /etc/systemd/system/execution.service && OPTIONS+=("execution" "")
    test -f /etc/systemd/system/consensus.service && OPTIONS+=("consensus" "")
    test -f /etc/systemd/system/validator.service && OPTIONS+=("validator" "")
    test -f /etc/systemd/system/charon.service && OPTIONS+=("charon" "")
    test -f /etc/systemd/system/mevboost.service && OPTIONS+=("mevboost" "" )
    test -f /etc/systemd/system/csm_nimbusvalidator.service && OPTIONS+=("csm_nimbusvalidator" "")
    service=$(whiptail --title "Export journalctl service logs" --menu \
          "I want to export logs for:" 15 60 6 \
          "${OPTIONS[@]}" \
          3>&1 1>&2 2>&3)
    if [ -z "$service" ]; then return; fi # pressed cancel
    date_range=$(whiptail --title "Date Range Selection" --menu "Choose a date range:" 15 60 5 \
        "Today" "" \
        "Yesterday" "" \
        "Last_Hour" "" \
        "Last_Week" "" \
        "Custom" ""  3>&1 1>&2 2>&3)
    if [ -z "$date_range" ]; then return; fi # pressed cancel
    echo "$service $date_range"
}

# Exports journalctl logs
function export_logs() {
    local user_input service date_range output_file
    user_input=$(get_user_input)
    if [ -z "$user_input" ]; then return; fi # pressed cancel
    service=$(echo "$user_input" | awk '{print $1}')
    date_range=$(echo "$user_input" | awk '{print $2}')

    # Determine the start and end times based on the selected date range
    local start_time=""
    local end_time=""
    case $date_range in
        "Today")
            start_time="00:00"
            end_time="23:59:59"
            ;;
        "Yesterday")
            start_time="$(date -d yesterday +%F) 00:00:00"
            end_time="$(date -d yesterday +%F) 23:59:59"
            ;;
        "Last_Hour")
            start_time="$(date -d '1 hour ago' '+%F %H:%M:%S')"
            end_time="$(date '+%F %H:%M:%S')"
            ;;
        "Last_Week")
            start_time="$(date -d 'last week' +%F) 00:00:00"
            end_time="$(date -d 'this week' +%F) 23:59:59"
            ;;
        "Custom")
            local custom_start custom_end
            custom_start=$(whiptail --title "Custom Start Date" --inputbox "Enter start date (YYYY-MM-DD HH:MM):" 10 60 "$(date +%F)" 3>&1 1>&2 2>&3)
            [[ -z $custom_start ]] && return 1 # user pressed <Cancel> button
            custom_end=$(whiptail --title "Custom End Date" --inputbox "Enter end date (YYYY-MM-DD HH:MM):" 10 60 "$(date +%F)" 3>&1 1>&2 2>&3)
            [[ -z $custom_end ]] && return 1 # user pressed <Cancel> button
            start_time="$custom_start"
            end_time="$custom_end"
            ;;
        *)
            whiptail --title "Invalid Option" --msgbox "Invalid date range selected." 10 60
            return 1
            ;;
    esac

    # Prompt for the output file name
    output_file=$(whiptail --title "Output File Name" --inputbox "Enter the output file name:" 10 60 "ethpillar_logs_${service}.txt" 3>&1 1>&2 2>&3)

    # Generate journalctl command based on user input and save to a log file
    journalctl_run --since "$start_time" --until "$end_time" -u "$service" | tee "$HOME"/"$output_file"

    whiptail --title "Export Complete" --msgbox "Logs have been exported to $HOME/$output_file" 10 60
}

# Install apt packages required by the TUI, deploy scripts, and updates.
# List lives in deploy/runtime_packages.txt (no Python imports — safe before venv exists).
ensure_host_runtime_packages() {
    local pkg packages_file="${BASE_DIR}/deploy/runtime_packages.txt"
    local -a packages missing=()
    [[ -f "$packages_file" ]] || error "runtime_packages.txt not found in ${BASE_DIR}/deploy"
    mapfile -t packages < <(grep -v '^#' "$packages_file" | grep -v '^[[:space:]]*$')
    for pkg in "${packages[@]}"; do
        dpkg -s "$pkg" &>/dev/null || missing+=("$pkg")
    done
    [[ ${#missing[@]} -eq 0 ]] && return 0
    ohai "Installing host packages: ${missing[*]}"
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}"
}

# Shared yes/no + version selection prompt used by all update scripts
promptYesNo() {
    local client_name="${1:-${CLIENT:-${EL:-Client}}}"
    local title_client="${2:-${title_client:-$client_name}}"
    local installed_label latest_label

    if version_matches_latest; then
        whiptail --title "Already updated" --msgbox "You are already on the latest version: ${VERSION#v}" 10 78
        if whiptail --title "Different Version of ${title_client}" --defaultno --yesno "Would you like to install a different version?" 8 78; then
            selectCustomTag
            updateClient "$__OTHERTAG"
            promptViewLogs
        fi
        return
    fi

    installed_label="${VERSION#v}"
    latest_label="${TAG#v}"
    if [[ -n "${INSTALLED_COMMIT:-}" ]]; then
        installed_label="${installed_label} (${INSTALLED_COMMIT:0:7})"
    fi
    if [[ -n "${TAG_COMMIT:-}" ]]; then
        latest_label="${latest_label} (${TAG_COMMIT:0:7})"
    fi

    __MSG="Installed Version is: ${installed_label}\nLatest Version is:    ${latest_label}\n\nReminder: Always read the release notes for breaking changes: $CHANGES_URL\n\nDo you want to update ${client_name} to ${TAG#v}?"

    __SELECTTAG=$(whiptail --title "🔧 Update ${title_client}" --menu \
          "$__MSG" 18 78 2 \
          "LATEST" "| Installs ${TAG#v}, the latest release" \
          "OTHER " "| I will select a different version" \
          3>&1 1>&2 2>&3)

    if [ -z "$__SELECTTAG" ]; then exit; fi

    if [[ $__SELECTTAG == "LATEST" ]]; then
        updateClient "LATEST"
        promptViewLogs
    else
        selectCustomTag
        updateClient "$__OTHERTAG"
        promptViewLogs
    fi
}


# Edit a systemd unit with $EDITOR. Prompt to restart only when content changed.
# If the unit changed but restart is declined, still run daemon-reload so a later
# restart/reboot picks up the new definition.
editSystemdUnitAndMaybeRestart() {
    local unit_path="$1"
    local yesno_prompt="$2"
    local service_name="$3"
    local before after

    if [[ ! -f "$unit_path" ]] && ! sudo test -f "$unit_path"; then
        whiptail --title "Edit configuration" --msgbox "Unit file not found:\n${unit_path}" 8 70
        return 1
    fi

    before=$(sudo sha256sum "$unit_path" | awk 'NR==1 {print $1}')
    sudo "${EDITOR}" "$unit_path"
    after=$(sudo sha256sum "$unit_path" | awk 'NR==1 {print $1}')

    if [[ -z "$before" || "$before" == "$after" ]]; then
        return 0
    fi

    if whiptail --title "Reload daemon and restart services" --yesno "$yesno_prompt" 8 78; then
        sudo systemctl daemon-reload && sudo service "$service_name" restart
    else
        sudo systemctl daemon-reload
    fi
}


# Launch tmeld on a prepared workdir, then list-changed → confirm → .bak → apply
# → daemon-reload / restart. Caller deletes *workdir*.
# Left pane is the apply contract (same as compareSystemdDefaults).
finishTmeldSystemdApply() {
    local workdir="$1"
    local py="${ETHPILLAR_PYTHON:-python3}"
    local changed svc restart_list rc

    set +e
    PYTHONPATH="${BASE_DIR}" "$py" -m manage.config_compare launch --workdir "$workdir"
    set -e

    changed=$(PYTHONPATH="${BASE_DIR}" "$py" -m manage.config_compare list-changed --workdir "$workdir" | tr -d '\r')
    if [[ -z "${changed// }" ]]; then
        whiptail --title "Compare systemd configs" --msgbox \
            "No changes were saved in the left pane.\nNothing to apply." 9 60
        return 0
    fi

    if ! whiptail --title "Apply systemd changes" --yesno \
        "Apply saved changes to:\n\n${changed}\n\nA .bak backup will be created for each unit (same as client switch)." 14 70; then
        return 0
    fi

    set +e
    PYTHONPATH="${BASE_DIR}" "$py" -m manage.config_compare apply --workdir "$workdir"
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        whiptail --title "Apply systemd changes" --msgbox \
            "Apply failed. Original units should still be intact\n(or restorable from .bak)." 10 70
        return 1
    fi

    restart_list=""
    for svc in $changed; do
        restart_list+=" • ${svc}\n"
    done
    if whiptail --title "Reload daemon and restart services" --yesno \
        "Do you want to daemon-reload and restart:\n\n${restart_list}" 14 70; then
        sudo systemctl daemon-reload
        for svc in $changed; do
            sudo systemctl restart "$svc" || true
        done
        ohai "Restarted: ${changed}"
    else
        # Unit files already updated on disk; reload so a later restart uses them.
        sudo systemctl daemon-reload
    fi

    ohai "Done. Press ENTER to continue."
    read
}


# Compare installed systemd units to what EthPillar would generate today.
# Opens tmeld (Meld-in-terminal): left=installed (applied), right=default (reference).
# Merge with Alt+Left (right→left) then Ctrl+S on LEFT. On exit: .bak / apply / restart.
compareSystemdDefaults() {
    local workdir py rc
    ensure_python_deps

    if [[ ! -f /etc/systemd/system/execution.service \
       && ! -f /etc/systemd/system/consensus.service \
       && ! -f /etc/systemd/system/validator.service \
       && ! -f /etc/systemd/system/charon.service \
       && ! -f /etc/systemd/system/mevboost.service ]]; then
        whiptail --title "Compare systemd configs" --msgbox \
            "No EthPillar systemd units found.\n\nInstall a node first." 10 70
        return 0
    fi

    workdir=$(mktemp -d /tmp/ethpillar-compare-XXXXXX)
    py="${ETHPILLAR_PYTHON:-python3}"

    set +e
    PYTHONPATH="${BASE_DIR}" "$py" -m manage.config_compare prepare --workdir "$workdir"
    rc=$?
    set -e

    if [[ $rc -eq 2 ]]; then
        whiptail --title "Compare systemd configs" --msgbox \
            "All installed systemd units match EthPillar defaults\n(after normalizing flag order)." 10 70
        rm -rf "$workdir"
        return 0
    fi
    if [[ $rc -ne 0 ]]; then
        whiptail --title "Compare systemd configs" --msgbox \
            "Failed to prepare comparison.\n\nSee terminal output for details." 10 70
        rm -rf "$workdir"
        return 1
    fi

    whiptail --title "Compare systemd configs" --msgbox \
"Opening tmeld (side-by-side compare/merge).

Left  = installed unit (this is what gets applied)
Right = EthPillar default (reference only)

Workflow — stay on the LEFT pane:
 • Enter a file from the folder view to open a tab
 • Alt+Down / Alt+Up  jump between differences
 • Alt+Left           copy this chunk from RIGHT → LEFT
 • Ctrl+S             save LEFT (required to keep merges)
 • Esc / Ctrl+Q       quit

Saving the right pane does nothing for EthPillar.
After you quit, you can apply saved left-pane changes
(with optional .bak backup)." 22 72

    finishTmeldSystemdApply "$workdir"
    rm -rf "$workdir"
}


# Build Execution Client SUBOPTIONS with sequential visible tags.
# Sets SUBOPTIONS and EXEC_MENU_{SUGGEST,UPDATE,RESYNC,EXPOSE,SWITCH,BACK}.
# EXEC_MENU_SUGGEST is empty when the installed EL is unsupported (Ethrex).
buildExecutionSuboptions() {
    local client="${1:-}"
    local helper="${BASE_DIR}/helpers/history_expiry_suggestions.sh"
    local n=5
    EXEC_MENU_SUGGEST=""
    if [[ -f "$helper" ]]; then
        # shellcheck source=helpers/history_expiry_suggestions.sh
        source "$helper"
    fi
    SUBOPTIONS=(
      1 "View logs"
      2 "Start execution"
      3 "Stop execution"
      4 "Restart execution"
      5 "Edit configuration"
    )
    if history_expiry_prune_suggest_menu_visible "$client"; then
        n=$((n + 1))
        EXEC_MENU_SUGGEST="$n"
        SUBOPTIONS+=("$n" "Suggest pruning parameters")
    fi
    n=$((n + 1)); EXEC_MENU_UPDATE="$n"; SUBOPTIONS+=("$n" "Update to latest release")
    n=$((n + 1)); EXEC_MENU_RESYNC="$n"; SUBOPTIONS+=("$n" "Resync execution client")
    n=$((n + 1)); EXEC_MENU_EXPOSE="$n"; SUBOPTIONS+=("$n" "Expose execution client RPC Port")
    n=$((n + 1)); EXEC_MENU_SWITCH="$n"; SUBOPTIONS+=("$n" "Switch execution client")
    SUBOPTIONS+=(- "")
    n=$((n + 1)); EXEC_MENU_BACK="$n"; SUBOPTIONS+=("$n" "Back to main menu")
}


# Suggest pruning / history-expiry flags for the installed execution client.
# Left = exact execution.service; right = same unit with prune flags merged
# into ExecStart only (not an EthPillar regen). Apply path matches compare.
suggestPruningParameters() {
    local helper="${BASE_DIR}/helpers/history_expiry_suggestions.sh"
    local unit="${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
    local cl_unit="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
    local workdir py rc
    local unit_text="" cl_text="" description execstart client status cl_client
    local level="recommended" flags warning_tmp

    # shellcheck source=helpers/history_expiry_suggestions.sh
    source "$helper"
    ensure_python_deps

    if [[ ! -f "$unit" ]] && ! sudo test -f "$unit"; then
        whiptail --title "Suggest pruning parameters" --msgbox \
            "No execution client unit found.\n\nInstall an execution client first." 10 70
        return 0
    fi

    unit_text=$(history_expiry_read_unit "$unit" 2>/dev/null || true)
    cl_text=$(history_expiry_read_unit "$cl_unit" 2>/dev/null || true)
    if [[ -z "$unit_text" ]]; then
        whiptail --title "Suggest pruning parameters" --msgbox \
            "Could not read ${unit}." 8 72
        return 1
    fi

    description=$(history_expiry_extract_description "$unit_text")
    execstart=$(history_expiry_extract_execstart "$unit_text")
    client=$(history_expiry_detect_client "$description" "$execstart")
    status=$(history_expiry_status "$client" "$execstart")
    cl_client=$(history_expiry_detect_client "$(history_expiry_extract_description "$cl_text")" "")

    case "$status" in
        no_el)
            whiptail --title "Suggest pruning parameters" --msgbox \
                "No execution client detected.\n\nInstall an execution client first." 10 70
            return 0
            ;;
        unsupported)
            whiptail --title "Suggest pruning parameters" --msgbox \
                "${client:-Ethrex} has no history-expiry CLI yet.\nNothing to suggest." 10 70
            return 0
            ;;
        unknown)
            whiptail --title "Suggest pruning parameters" --msgbox \
                "Could not classify the installed execution client." 8 70
            return 0
            ;;
        recommended)
            whiptail --title "Suggest pruning parameters" --msgbox \
                "${client} already has recommended expiry/prune flags.\n\n$(history_expiry_suggested_flags "$client")\n\nNothing to change." 12 72
            return 0
            ;;
        archive|caplin_archive)
            if ! whiptail --title "Suggest pruning parameters" --yesno \
                "${client} looks like an intentional archive / full-history node.\n\nPruning is destructive and can drop historic data.\n\nOpen the suggestion editor anyway?" 14 72; then
                return 0
            fi
            ;;
    esac

    if history_expiry_has_further_savings "$client"; then
        # Geth is excluded until rolling history ships in a tagged release
        # (restore Further = recent --history.blocks=1056768, experimental).
        level=$(whiptail --title "Suggest pruning parameters" --radiolist \
            "${client}: choose prune level. Recommended is the usual choice for home staking on ~2TB disks.

Recommended:
  $(history_expiry_suggested_flags "$client")

Further savings:
  $(history_expiry_further_savings_flags "$client")" \
            20 78 2 \
            recommended "Recommended (suitable for a ~2TB drive)" ON \
            further "Further savings (more aggressive)" OFF \
            3>&1 1>&2 2>&3) || return 0
    fi

    flags=$(history_expiry_flags_for_level "$client" "$level")
    if [[ -z "${flags// }" ]]; then
        whiptail --title "Suggest pruning parameters" --msgbox \
            "No prune flags to merge for ${client}." 8 70
        return 0
    fi

    warning_tmp=$(mktemp /tmp/ethpillar-prune-warn-XXXXXX)
    history_expiry_pre_tmeld_warnings "$client" "$status" "$cl_client" "$flags" >"$warning_tmp"
    whiptail --title "Suggest pruning parameters — warnings" --scrolltext --textbox "$warning_tmp" 20 78
    rm -f "$warning_tmp"

    workdir=$(mktemp -d /tmp/ethpillar-prune-suggest-XXXXXX)
    py="${ETHPILLAR_PYTHON:-python3}"

    set +e
    PYTHONPATH="${BASE_DIR}" "$py" -m manage.config_compare prepare-prune-suggest \
        --workdir "$workdir" --unit "$unit" --level "$level" --flags "$flags"
    rc=$?
    set -e

    if [[ $rc -eq 2 ]]; then
        whiptail --title "Suggest pruning parameters" --msgbox \
            "Selected flags are already present on ExecStart.\nNothing to merge." 10 70
        rm -rf "$workdir"
        return 0
    fi
    if [[ $rc -ne 0 ]]; then
        whiptail --title "Suggest pruning parameters" --msgbox \
            "Failed to prepare the suggestion.\n\nSee terminal output for details." 10 70
        rm -rf "$workdir"
        return 1
    fi

    whiptail --title "Suggest pruning parameters" --msgbox \
"Opening tmeld (side-by-side compare/merge).

Left  = installed execution.service (this is what gets applied)
Right = same unit with selected prune flags merged into ExecStart only

Workflow — stay on the LEFT pane:
 • Alt+Down / Alt+Up  jump between differences
 • Alt+Left           copy this chunk from RIGHT → LEFT
 • Ctrl+S             save LEFT (required to keep merges)
 • Esc / Ctrl+Q       quit

Saving the right pane does nothing for EthPillar.
After you quit, you can apply saved left-pane changes
(with optional .bak backup)." 21 72

    finishTmeldSystemdApply "$workdir"
    rm -rf "$workdir"
}


# Run manage.epbs (status | prepare | complete | export | import). Extra args are forwarded.
# Usage: runEpbsCli <command> [flags...]
# Relies on ETHPILLAR_PYTHON / PYTHONPATH from ensure_python_deps.
runEpbsCli() {
    local py
    ensure_python_deps
    py="${ETHPILLAR_PYTHON:-python3}"
    PYTHONPATH="${BASE_DIR}" "$py" -m manage.epbs "$@"
}

# Dry-run, confirm, apply an ePBS prepare/complete/import step. Prompt to restart units.
# Args:
#   $1  command  — prepare | complete | import
#   $2  title    — whiptail window title
#   $3  confirm  — yes/no prompt after the dry-run textbox
#   $@  remaining args forwarded to manage.epbs (path, --remote-vc-prepared, …)
runEpbsMigrationStep() {
    local cmd="$1"
    local title="$2"
    local confirm="$3"
    shift 3
    local tmp err rc json restarts svc

    tmp=$(mktemp /tmp/ethpillar-epbs-XXXXXX)
    err="${tmp}.err"
    set +e
    runEpbsCli "$cmd" "$@" >"$tmp" 2>"$err"
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        whiptail --title "$title" --scrolltext --msgbox "$(cat "$err" "$tmp" 2>/dev/null)" 20 78
        rm -f "$tmp" "$err"
        return 1
    fi
    whiptail --title "$title" --scrolltext --textbox "$tmp" 22 78
    if ! whiptail --title "$title" --yesno "$confirm" 12 78; then
        rm -f "$tmp" "$err"
        return 0
    fi
    set +e
    json=$(runEpbsCli "$cmd" "$@" --apply --json 2>"$err")
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        whiptail --title "$title" --scrolltext --msgbox "$(cat "$err")" 20 78
        rm -f "$tmp" "$err"
        return 1
    fi
    printf '%s\n' "$json" >"$tmp"
    whiptail --title "$title — applied" --scrolltext --textbox "$tmp" 22 78

    restarts=$(printf '%s\n' "$json" | PYTHONPATH="${BASE_DIR}" "${ETHPILLAR_PYTHON:-python3}" -c \
        "import json,sys; print(' '.join(json.load(sys.stdin).get('services_to_restart') or []))")
    sudo systemctl daemon-reload
    if [[ -n "$restarts" ]]; then
        if whiptail --title "Restart services" --yesno \
            "Restart now so the new flags take effect?\n\n${restarts}" 12 70; then
            for svc in $restarts; do
                sudo systemctl restart "$svc" || true
            done
        fi
    fi
    rm -f "$tmp" "$err"
}

# Export MEV relays to ~/hostname-timestamp.ethpillar.epbs-migration for a remote VC.
runEpbsExport() {
    local out tmp err rc host stamp
    host=$(hostname 2>/dev/null || echo host)
    stamp=$(date +%Y%m%d-%H%M%S)
    out="${HOME}/${host}-${stamp}.ethpillar.epbs-migration"
    tmp=$(mktemp /tmp/ethpillar-epbs-XXXXXX)
    err="${tmp}.err"
    set +e
    runEpbsCli export --output "$out" >"$tmp" 2>"$err"
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        whiptail --title "Export ePBS migration file" --scrolltext --msgbox \
            "$(cat "$err" "$tmp" 2>/dev/null)" 20 78
        rm -f "$tmp" "$err"
        return 1
    fi
    {
        echo "Copy this file to the VC (or Charon+VC) host and use Import ePBS migration."
        echo ""
        cat "$tmp"
    } >"${tmp}.out"
    whiptail --title "Export ePBS migration file" --scrolltext --textbox "${tmp}.out" 22 78
    rm -f "$tmp" "$err" "${tmp}.out"
}

# Prompt for a migration file path and run import (dry-run → confirm → apply).
runEpbsImport() {
    local src
    src=$(whiptail --title "Import ePBS migration file" --inputbox \
"Path to the .ethpillar.epbs-migration file from the MEV/CC host:

Applies relays to this validator client (prepare step).
Keep MEV-Boost running on the other host until after Gloas." \
        16 78 --ok-button "Submit" 3>&1 1>&2 2>&3) || return
    src="${src/#\~/$HOME}"
    if [[ ! -f "$src" ]]; then
        whiptail --title "Import ePBS migration file" --msgbox \
"File not found:
${src}" 9 70
        return 1
    fi
    runEpbsMigrationStep import "Import ePBS migration file" \
        "Write these VC changes now?\n\nMEV-Boost on the other host stays running until after Gloas." \
        "$src"
}

# MEV-Boost submenu: prepare/export (before Gloas), complete (after Gloas), status.
submenuEPBS() {
    local choice tmp menu_blurb prep_label prep_confirm
    while true; do
        getBackTitle
        if epbsRemoteVcMode; then
            menu_blurb="Split LXC: this host has MEV (and usually CC) but no local validator.\n\nBefore the fork: export a migration file for the VC/Charon host.\nAfter the fork: disable MEV-Boost and drop the BN sidecar URL (confirm the other host already imported)."
            choice=$(whiptail --clear --cancel-button "Back" \
                --backtitle "$BACKTITLE" \
                --title "ePBS migration (remote VC)" \
                --menu "$menu_blurb" \
                0 0 0 \
                1 "Before Gloas Fork — Export migration file" \
                2 "After Gloas Fork — Complete ePBS migration" \
                3 "Show current ePBS status" \
                4 "Back" \
                3>&1 1>&2 2>&3) || break
            case "$choice" in
                1)
                    runEpbsExport
                    ;;
                2)
                    runEpbsMigrationStep complete "After Gloas Fork — Complete ePBS migration" \
                        "Stop MEV-Boost and remove BN sidecar flags now?\n\nConfirm the other LXC already imported the migration file.\nOnly do this after the Gloas fork." \
                        --remote-vc-prepared
                    ;;
                3)
                    tmp=$(mktemp /tmp/ethpillar-epbs-XXXXXX)
                    if runEpbsCli status >"$tmp" 2>&1; then
                        whiptail --title "ePBS status" --scrolltext --textbox "$tmp" 22 78
                    else
                        whiptail --title "ePBS status" --scrolltext --msgbox "$(cat "$tmp")" 20 78
                    fi
                    rm -f "$tmp"
                    ;;
                4|"")
                    break
                    ;;
            esac
            continue
        fi
        if isCharonEnabled; then
            menu_blurb="With Obol Charon, builder/MEV traffic goes through Charon (--builder-api), not VC relay lists.\n\nBefore the fork: keep Charon --builder-api and MEV-Boost running.\nAfter the fork: strip Charon --builder-api, disable MEV-Boost, and drop the BN sidecar URL."
            prep_label="Before Gloas Fork — Keep Charon builder-api"
            prep_confirm="Confirm Charon ePBS prepare now?\n\nVC relay lists are not written. MEV-Boost stays running. Do not complete until after the Gloas fork."
        else
            menu_blurb="Gloas moves relay config from MEV-Boost onto the validator client.\n\nBefore the fork: copy relays to the VC and keep MEV-Boost running.\nAfter the fork: disable MEV-Boost and drop the BN sidecar URL."
            prep_label="Before Gloas Fork — Apply Relays to VC"
            prep_confirm="Write these VC changes now?\n\nMEV-Boost stays running. Do not complete migration until after the Gloas fork."
        fi
        choice=$(whiptail --clear --cancel-button "Back" \
            --backtitle "$BACKTITLE" \
            --title "ePBS migration" \
            --menu "$menu_blurb" \
            0 0 0 \
            1 "$prep_label" \
            2 "After Gloas Fork — Complete ePBS migration" \
            3 "Show current ePBS status" \
            4 "Back" \
            3>&1 1>&2 2>&3) || break
        case "$choice" in
            1)
                runEpbsMigrationStep prepare "$prep_label" "$prep_confirm"
                ;;
            2)
                runEpbsMigrationStep complete "After Gloas Fork — Complete ePBS migration" \
                    "Stop MEV-Boost and remove BN sidecar flags now?\n\nOnly do this after the Gloas fork. Missed proposals if you cut over early."
                ;;
            3)
                tmp=$(mktemp /tmp/ethpillar-epbs-XXXXXX)
                if runEpbsCli status >"$tmp" 2>&1; then
                    whiptail --title "ePBS status" --scrolltext --textbox "$tmp" 22 78
                else
                    whiptail --title "ePBS status" --scrolltext --msgbox "$(cat "$tmp")" 20 78
                fi
                rm -f "$tmp"
                ;;
            4|"")
                break
                ;;
        esac
    done
}

# VC/Charon host submenu: import migration file, complete (Charon strip), status.
submenuEPBSImport() {
    local choice tmp
    while true; do
        getBackTitle
        choice=$(whiptail --clear --cancel-button "Back" \
            --backtitle "$BACKTITLE" \
            --title "ePBS migration (import)" \
            --menu "This host has the validator (and maybe Charon) but no local MEV-Boost.\n\nBefore the fork: import the migration file from the MEV/CC host.\nAfter the fork: complete locally (strip Charon --builder-api when present)." \
            0 0 0 \
            1 "Before Gloas Fork — Import migration file" \
            2 "After Gloas Fork — Complete ePBS migration" \
            3 "Show current ePBS status" \
            4 "Back" \
            3>&1 1>&2 2>&3) || break
        case "$choice" in
            1)
                runEpbsImport
                ;;
            2)
                runEpbsMigrationStep complete "After Gloas Fork — Complete ePBS migration" \
                    "Complete ePBS on this VC/Charon host now?\n\nStrips Charon --builder-api when present. MEV-Boost stop runs on the other host."
                ;;
            3)
                tmp=$(mktemp /tmp/ethpillar-epbs-XXXXXX)
                if runEpbsCli status >"$tmp" 2>&1; then
                    whiptail --title "ePBS status" --scrolltext --textbox "$tmp" 22 78
                else
                    whiptail --title "ePBS status" --scrolltext --msgbox "$(cat "$tmp")" 20 78
                fi
                rm -f "$tmp"
                ;;
            4|"")
                break
                ;;
        esac
    done
}


# Host tools first (whiptail, curl, …), then Python venv deps.
ensure_host_runtime_packages
ensure_python_deps
