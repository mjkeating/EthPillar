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

function selectCustomTag(){
	case $CLIENT in
	  Lighthouse)
	    _repo="sigp/lighthouse"
	    ;;
	  Lodestar)
	    _repo="ChainSafe/lodestar"
	    ;;
	  Teku)
	    _repo="ConsenSys/teku"
	    ;;
	  Nimbus)
	    _repo="status-im/nimbus-eth2"
	    ;;
	  Prysm)
	    _repo="OffchainLabs/prysm"
	    ;;
	  Grandine)
	    _repo="grandinetech/grandine"
	    ;;
	  *)
	    error "❌ Unsupported or unknown client '$CLIENT'."
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
	    if whiptail --title "Different Version of ${CLIENT}" --defaultno --yesno "Would you like to install a different version?" 8 78; then
			selectCustomTag
			updateClient "$__OTHERTAG"
			promptViewLogs
		fi
		return
	fi
    __MSG="Installed Version is: ${VERSION#v}\nLatest Version is:    ${TAG#v}\n\nReminder: Always read the release notes for breaking changes: $CHANGES_URL\n\nDo you want to update $CLIENT to ${TAG#v}?"
	__SELECTTAG=$(whiptail --title "🔧 Update ${CLIENT}" --menu \
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
    if whiptail --title "Update complete" --yesno "Would you like to view logs and confirm everything is running properly?" 8 78; then
		if [[ ${NODE_MODE} =~ "Validator Client Only" ]]; then
			sudo bash -c 'journalctl -fu validator | ccze -A'
		else
			sudo bash -c 'journalctl -fu consensus | ccze -A'
		fi
    fi
}

function getLatestVersion(){
	local _client_lower
	_client_lower=$(echo "$CLIENT" | tr '[:upper:]' '[:lower:]')
	RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "$_client_lower" "LATEST")
	TAG=$(echo "$RELEASE_DATA" | jq -r .version)
	# Exit in case of null tag
	if [[ -z $TAG ]] || [[ $TAG == "null" ]]; then
		error "❌ Couldn't find the latest version tag"
	fi
	case "$CLIENT" in
	  Lighthouse) CHANGES_URL="https://github.com/sigp/lighthouse/releases" ;;
	  Lodestar)   CHANGES_URL="https://github.com/ChainSafe/lodestar/releases" ;;
	  Teku)       CHANGES_URL="https://github.com/ConsenSys/teku/releases" ;;
	  Nimbus)     CHANGES_URL="https://github.com/status-im/nimbus-eth2/releases" ;;
	  Prysm)      CHANGES_URL="https://github.com/OffchainLabs/prysm/releases" ;;
	  Grandine)   CHANGES_URL="https://github.com/grandinetech/grandine/releases" ;;
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

	local _client_lower
	_client_lower=$(echo "$CLIENT" | tr '[:upper:]' '[:lower:]')

	RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "$_client_lower" "$_target_tag")
	TAG=$(echo "$RELEASE_DATA" | jq -r .version)
	
	case "$CLIENT" in
	  Lighthouse)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXTRACT_DIR="$HOME/lighthouse_temp"
		mkdir -p "$EXTRACT_DIR"
		tar -xzvf "$FILENAME" -C "$EXTRACT_DIR" || error "❌ Unable to untar file"
		rm "$FILENAME"
		LH_BIN=$(find "$EXTRACT_DIR" -type f -name "lighthouse" | head -n 1)
		if [ -z "$LH_BIN" ]; then
			error "❌ Could not find the extracted lighthouse binary"
		fi
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/lighthouse")
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -f "$EXEC_PATH"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		sudo mv "$LH_BIN" "$EXEC_PATH" || error "❌ Unable to move file"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
	    ;;
	  Lodestar)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXTRACTED_DIR="$HOME/lodestar_temp"
		mkdir -p "$EXTRACTED_DIR"
		tar -xzvf "$FILENAME" -C "$EXTRACTED_DIR" || error "❌ Unable to untar file"
		rm "$FILENAME"
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/lodestar")
		LODESTAR_BIN=$(find "$EXTRACTED_DIR" -type f -name "lodestar" | head -n 1)
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -f "$EXEC_PATH"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		sudo mv "$LODESTAR_BIN" "$EXEC_PATH" || error "❌ Unable to move file"
		sudo chmod +x "$EXEC_PATH"
		rm -rf "$EXTRACTED_DIR"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
	    ;;
	  Teku)
		updateJRE
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXTRACT_DIR="$HOME/teku_temp"
		mkdir -p "$EXTRACT_DIR"
		tar -xzvf "$FILENAME" -C "$EXTRACT_DIR" || error "❌ Unable to untar file"
		rm "$FILENAME"
		TEKU_BIN=$(find "$EXTRACT_DIR" -type f -name "teku" | head -n 1)
		if [ -z "$TEKU_BIN" ]; then
			error "❌ Could not find the extracted teku binary"
		fi
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/teku/bin/teku")
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -rf "$(dirname "$(dirname "$EXEC_PATH")")"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		sudo mv "$TEKU_BIN" "$EXEC_PATH" || error "❌ Unable to move file"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
	    ;;
	  Nimbus)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXTRACT_DIR="$HOME/nimbus_temp"
		mkdir -p "$EXTRACT_DIR"
		tar -xzvf "$FILENAME" -C "$EXTRACT_DIR" || error "❌ Unable to untar file"
		rm "$FILENAME"
		BN_BIN=$(find "$EXTRACT_DIR" -type f -name "nimbus_beacon_node" | head -n 1)
		VC_BIN=$(find "$EXTRACT_DIR" -type f -name "nimbus_validator_client" | head -n 1)
		if [ -z "$BN_BIN" ] || [ -z "$VC_BIN" ]; then
			error "❌ Could not find the extracted nimbus binaries"
		fi
		BN_EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/nimbus_beacon_node")
		VC_EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/validator.service" "/usr/local/bin/nimbus_validator_client")
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -f "$BN_EXEC_PATH" "$VC_EXEC_PATH"
		sudo mkdir -p "$(dirname "$BN_EXEC_PATH")" "$(dirname "$VC_EXEC_PATH")"
		sudo mv "$BN_BIN" "$BN_EXEC_PATH" || error "❌ Unable to move file"
		sudo mv "$VC_BIN" "$VC_EXEC_PATH" || error "❌ Unable to move file"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
		rm -rf "$EXTRACT_DIR"
	    ;;
  	  Prysm)
		cd "$HOME" || true
		local _bn_url _vc_url _bn_file _vc_file
		_bn_url=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		_vc_url=$(echo "$RELEASE_DATA" | jq -r '.download_urls[1]')
		_bn_file=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		_vc_file=$(echo "$RELEASE_DATA" | jq -r '.filenames[1]')

		local _prysmctl_url
		_prysmctl_url="${_bn_url/beacon-chain/prysmctl}"

		curl -L -f "${_bn_url}" -o beacon-chain || error "❌ Unable to download beacon-chain"
		curl -L -f "${_vc_url}" -o validator || error "❌ Unable to download validator"
		curl -L -f "${_prysmctl_url}" -o prysmctl || error "❌ Unable to download prysmctl"
		
		chmod +x beacon-chain validator prysmctl
		BN_EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/prysm-beacon-chain")
		VC_EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/validator.service" "/usr/local/bin/prysm-validator")
		PRYSMCTL_EXEC_PATH="$(dirname "$BN_EXEC_PATH")/prysmctl"
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -f "$BN_EXEC_PATH" "$VC_EXEC_PATH" "$PRYSMCTL_EXEC_PATH"
		sudo mkdir -p "$(dirname "$BN_EXEC_PATH")" "$(dirname "$VC_EXEC_PATH")"
		sudo mv beacon-chain "$BN_EXEC_PATH" || error "❌ Unable to move beacon-chain"
		sudo mv validator "$VC_EXEC_PATH" || error "❌ Unable to move validator"
		sudo mv prysmctl "$PRYSMCTL_EXEC_PATH" || error "❌ Unable to move prysmctl"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo systemctl start validator
	    ;;
	  Grandine)
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O grandine "$BINARIES_URL" || error "❌ Unable to wget file"
		chmod +x grandine
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/grandine")
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -f "$EXEC_PATH"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		sudo mv "$HOME"/grandine "$EXEC_PATH" || error "❌ Unable to move file"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
	    ;;
	esac
	true
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