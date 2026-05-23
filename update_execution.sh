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

function getCurrentVersion(){
  EL_INSTALLED=$(curl -s -X POST -H "Content-Type: application/json" \
    --data '{"jsonrpc":"2.0","method":"web3_clientVersion","params":[],"id":2}' \
    "${EL_RPC_ENDPOINT}" | jq -r '.result // empty')
  if [[ -z "$EL_INSTALLED" ]]; then
    VERSION="Client not running or still starting up. Unable to query version."
    return
  fi
  VERSION=$(sed -E 's/.*[v\/]([0-9]+\.[0-9]+\.[0-9]+).*/\1/' <<< "$EL_INSTALLED")
}

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

function promptYesNo(){
	# Remove front v if present
	if [[ "${VERSION#v}" == "${TAG#v}" ]]; then
		whiptail --title "Already updated" --msgbox "You are already on the latest version: ${VERSION#v}" 10 78
	    if whiptail --title "Different Version of $EL" --defaultno --yesno "Would you like to install a different version?" 8 78; then
			selectCustomTag
			updateClient "$__OTHERTAG"
			promptViewLogs
		fi
		return
	fi
    __MSG="Installed Version is: ${VERSION#v}\nLatest Version is:    ${TAG#v}\n\nReminder: Always read the release notes for breaking changes: $CHANGES_URL\n\nDo you want to update $EL to ${TAG#v}?"
	__SELECTTAG=$(whiptail --title "🔧 Update Execution Client" --menu \
	      "$__MSG" 18 78 2 \
	      "LATEST" "| Installs ${TAG#v}, the latest release" \
	      "OTHER " "| I will select a different version" \
	      3>&1 1>&2 2>&3)
	if [ -z "$__SELECTTAG" ]; then exit; fi # pressed cancel
	if [[ $__SELECTTAG == "LATEST" ]]; then
		updateClient "LATEST"
		promptViewLogs
	else
		selectCustomTag
		updateClient "$__OTHERTAG"
		promptViewLogs
	fi
}

function promptViewLogs(){
    if whiptail --title "Update complete - $EL" --yesno "Would you like to view logs and confirm everything is running properly?" 8 78; then
		sudo bash -c 'journalctl -fu execution | ccze -A'
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
		unzip -o "$FILENAME" -d "$HOME"/nethermind || error "❌ Unable to unzip file"
		rm -f "$FILENAME"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/nethermind/nethermind")
		BASE_DIR=$(dirname "$EXEC_PATH")
		sudo systemctl stop execution
		sudo rm -rf "$BASE_DIR"
		sudo mv "$HOME"/nethermind "$BASE_DIR" || error "❌ Unable to move file"
		sudo systemctl start execution		
		;;
	  Besu)
		updateJRE
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		tar -xzvf "$FILENAME" -C "$HOME" || error "❌ Unable to untar file"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/besu/bin/besu")
		BASE_DIR=$(dirname "$(dirname "$EXEC_PATH")")
		sudo systemctl stop execution
		sudo rm -rf "$BASE_DIR"
		sudo mv "$HOME"/besu-"${TAG}" "$BASE_DIR" || error "❌ Unable to move file"
		sudo systemctl start execution
		rm "$FILENAME"
		;;
	  Erigon)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXTRACTED_DIR="$HOME/erigon_temp"
		mkdir -p "$EXTRACTED_DIR"
		tar -xzvf "$FILENAME" -C "$EXTRACTED_DIR" || error "❌ Unable to untar file"
		rm "$FILENAME"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/erigon")
		ERIGON_BIN=$(find "$EXTRACTED_DIR" -type f -name "erigon" | head -n 1)
		sudo systemctl stop execution
		sudo rm -f "$EXEC_PATH"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		sudo mv "$ERIGON_BIN" "$EXEC_PATH" || error "❌ Unable to move file"
		sudo systemctl start execution
		rm -rf "$EXTRACTED_DIR"
		;;
	  Geth)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXTRACTED_DIR="geth_temp"
		mkdir -p "$EXTRACTED_DIR"
		tar -xzvf "$FILENAME" -C "$EXTRACTED_DIR" || error "❌ Unable to untar file"
		GETH_BIN=$(find "./$EXTRACTED_DIR" -type f -name "geth" | head -n 1)
		if [ -z "$GETH_BIN" ]; then
			error "❌ Could not find the extracted geth binary"
		fi
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/geth")
		sudo systemctl stop execution
		sudo rm -f "$EXEC_PATH"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		sudo mv "$GETH_BIN" "$EXEC_PATH" || error "❌ Unable to move file"
		sudo chmod +x "$EXEC_PATH"
		sudo chown execution:execution "$EXEC_PATH"
		sudo systemctl start execution
		rm -rf "$EXTRACTED_DIR" "$FILENAME"
	    ;;
  	  Reth)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXTRACTED_DIR="$HOME/reth_temp"
		mkdir -p "$EXTRACTED_DIR"
		tar -xzvf "$FILENAME" -C "$EXTRACTED_DIR" || error "❌ Unable to untar file"
		rm "$FILENAME"
		RETH_BIN=$(find "$EXTRACTED_DIR" -type f \( -name "reth" -o -name "reth-*" \) | head -n 1)
		if [ -z "$RETH_BIN" ]; then
			error "❌ Could not find the extracted reth binary"
		fi
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/execution.service" "/usr/local/bin/reth")
		sudo systemctl stop execution
		sudo rm -f "$EXEC_PATH"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		sudo mv "$RETH_BIN" "$EXEC_PATH" || error "❌ Unable to move file"
		sudo chmod +x "$EXEC_PATH"
		sudo systemctl start execution
		rm -rf "$EXTRACTED_DIR"
	    ;;
	esac
}

function updateJRE(){
	# Check if OpenJDK-21-JRE or OpenJDK-21-JDK is already installed
	if dpkg --list | grep -q -E "openjdk-21-jre|openjdk-21-jdk"; then
	   info "✅ OpenJDK-21-JRE or OpenJDK-21-JDK is already installed. Skipping installation."
	else
	   # Install OpenJDK-21-JRE
	   sudo apt-get update
	   sudo apt-get install -y openjdk-21-jre

       # Check if the installation was successful
       # shellcheck disable=SC2181
       if [ $? -eq 0 ]; then
	      info "✅ OpenJDK-21-JRE installed successfully!"
	   else
	      error "❌ Error installing OpenJDK-21-JRE. Please check the error log."
	   fi
	fi
}

if [[ "${1:-}" == "--auto" ]]; then
    getClient
    getLatestVersion
    updateClient "LATEST"
else
    getClient
    getCurrentVersion
    getLatestVersion
    promptYesNo
fi