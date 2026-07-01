#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: EthPillar is a one-liner setup tool and node management TUI
#
# Made for home and solo stakers 🏠🥩

# Resolve BASE_DIR relative to this script's location, fallback to legacy path
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/functions.sh" ]]; then
    BASE_DIR="$SCRIPT_DIR"
else
    BASE_DIR="$HOME/git/ethpillar"
fi

__OTHERTAG=""

# Load functions
# shellcheck disable=SC1091
source "$BASE_DIR"/functions.sh

# Get machine info
_platform=$(get_platform)
_arch=$(get_arch)

function getClient(){
    EL=$(cat /etc/systemd/system/execution.service | grep Description= | awk -F'=' '{print $2}' | awk '{print $1}')
    # Handle integrated ELs i.e. Erigon-Caplin
    EL=${EL%-*}
}

function selectCustomTag(){
	case $EL in
	  Nethermind)
	    _repo="NethermindEth/nethermind"
	    ;;
	  Besu)
	    _repo="besu-eth/besu"
	    ;;
	  Erigon)
	    _repo="erigontech/erigon"
	    ;;
	  Geth)
	    _repo="ethereum/go-ethereum"
	    ;;
	  Reth)
	    _repo="paradigmxyz/reth"
	    ;;
	  Ethrex)
	    _repo="lambdaclass/ethrex"
	    ;;
	  *)
	    error "❌ Unsupported or unknown client '$EL'."
	    ;;
	esac
	local _listTags _tag
	_listTags=$(curl -fsSL https://api.github.com/repos/"${_repo}"/tags | jq -r '.[].name' | sort -hr)
	if [ -z "$_listTags" ]; then
		error "❌ Could not retrieve tags for ${_repo}. Try again later."
	fi
	info "ℹ️  Select the Version: Type the number to use. For example, 2 (for the 2nd most recent release)"
	select _tag in $_listTags; do
        if [ -n "$_tag" ]; then
			__OTHERTAG=$_tag
            break
        else
            error "❌ Invalid input. Enter the line # corresponding to a tag."
        fi
    done
}

function promptViewLogs(){
    if whiptail --title "Update complete - $EL" --yesno "Would you like to view logs and confirm everything is running properly?" 8 78; then
		view_journal_logs -fu execution
    fi
}

function getLatestVersion(){
	RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "$EL" "LATEST")
	TAG=$(echo "$RELEASE_DATA" | jq -r .version)
	# Exit in case of null tag
	if [[ -z $TAG ]] || [[ $TAG == "null" ]]; then
		error "❌ Couldn't find the latest version tag"
	fi
	case $EL in
	  Nethermind) CHANGES_URL="https://github.com/NethermindEth/nethermind/releases" ;;
	  Besu)       CHANGES_URL="https://github.com/besu-eth/besu/releases" ;;
	  Erigon)     CHANGES_URL="https://github.com/erigontech/erigon/releases" ;;
	  Geth)       CHANGES_URL="https://github.com/ethereum/go-ethereum/releases" ;;
	  Reth)       CHANGES_URL="https://github.com/paradigmxyz/reth/releases" ;;
	  Ethrex)     CHANGES_URL="https://github.com/lambdaclass/ethrex/releases" ;;
	  *)          CHANGES_URL="" ;;
	esac
}

function updateClient(){
	local _target_tag
	if [[ "$1" == "LATEST" ]]; then
		_target_tag="LATEST"
	else
		_target_tag="$1"
	fi

	RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "$EL" "$_target_tag")
	TAG=$(echo "$RELEASE_DATA" | jq -r .version)
	
	case $EL in
	  Nethermind)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/nethermind/nethermind")
		DEST_DIR=$(dirname "$EXEC_PATH")
		sudo systemctl stop execution
		PYTHONPATH="${BASE_DIR}" python3 -m deploy.common extract_and_install "$FILENAME" "nethermind" "$DEST_DIR" "directory" 0
		sudo systemctl start execution
		;;
	  Besu)
		# Ensure JDK 25 is available BEFORE touching the running client; abort
		# the update otherwise so we don't replace a working Besu with one that
		# cannot start (UnsupportedClassVersionError).
		# NOTE: keep this version in sync with ensure_java_available(25) in deploy/besu.py.
		updateJRE 25 || error "❌ JDK 25 is required by Besu but could not be installed. Aborting update; Besu was left untouched."
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/besu/bin/besu")
		DEST_DIR=$(dirname "$(dirname "$EXEC_PATH")")
		sudo systemctl stop execution
		PYTHONPATH="${BASE_DIR}" python3 -m deploy.common extract_and_install "$FILENAME" "besu" "$DEST_DIR" "directory" 1
		sudo systemctl start execution
		;;
	  Erigon)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/erigon")
		sudo systemctl stop execution
		PYTHONPATH="${BASE_DIR}" python3 -m deploy.common extract_and_install "$FILENAME" "erigon" "$EXEC_PATH" "binary" 1
		sudo systemctl start execution
		;;
	  Geth)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/geth")
		sudo systemctl stop execution
		PYTHONPATH="${BASE_DIR}" python3 -m deploy.common extract_and_install "$FILENAME" "geth" "$EXEC_PATH" "binary" 1
		sudo systemctl start execution
	    ;;
  	  Reth)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/reth")
		sudo systemctl stop execution
		PYTHONPATH="${BASE_DIR}" python3 -m deploy.common extract_and_install "$FILENAME" "reth" "$EXEC_PATH" "binary" 0
		sudo systemctl start execution
	    ;;
	  Ethrex)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/ethrex")
		sudo systemctl stop execution
		sudo rm -f "$EXEC_PATH"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		# install_system_binary will move and configure the binary at the full exec path
		PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('${FILENAME}', '${EXEC_PATH}')"
		sudo systemctl start execution
		rm -f "$FILENAME"
	    ;;
	esac
}

if [[ "${1:-}" == "--auto" ]]; then
    getClient
    getLatestVersion
    updateClient "LATEST"
else
    getClient
    getExecutionCurrentVersion "$EL"
    getLatestVersion
    promptYesNo
fi