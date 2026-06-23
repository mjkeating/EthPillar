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

function promptViewLogs(){
    if whiptail --title "Update complete" --yesno "Would you like to view logs and confirm everything is running properly?" 8 78; then
		if [[ ${NODE_MODE} =~ "Validator Client Only" ]]; then
			view_journal_logs -fu validator
		else
			view_journal_logs -fu consensus
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
		# install_system_binary will move and configure the binary at the full exec path
		PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('${LH_BIN}', '${EXEC_PATH}')"
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
		LODESTAR_DIR=$(dirname "$EXEC_PATH")
		LODESTAR_BIN=$(find "$EXTRACTED_DIR" -type f -name "lodestar" | head -n 1)
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -f "$EXEC_PATH"
		sudo mkdir -p "$(dirname "$EXEC_PATH")"
		# install_system_binary will move and configure the binary at the full exec path
		PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('${LODESTAR_BIN}', '${EXEC_PATH}')"
		rm -rf "$EXTRACTED_DIR"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
	    ;;
	  Teku)
		# Ensure JDK 25 is available BEFORE touching the running client; abort
		# the update otherwise so we don't replace a working Besu with one that
		# cannot start (UnsupportedClassVersionError).
		# NOTE: keep this version in sync with ensure_java_available(25) in deploy/teku.py.
		updateJRE 25|| error "❌ JDK 25 is required by Teku but could not be installed. Aborting update; Teku was left untouched."
		BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		info "✅ Downloading URL: $BINARIES_URL"
		cd "$HOME" || true
		wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
		EXTRACT_DIR="$HOME/teku_temp"
		mkdir -p "$EXTRACT_DIR"
		tar -xzvf "$FILENAME" -C "$EXTRACT_DIR" || error "❌ Unable to untar file"
		rm "$FILENAME"
		TEKU_DIR=$(find "$EXTRACT_DIR" -maxdepth 1 -type d -name "teku-*" | head -n 1)
		if [ -z "$TEKU_DIR" ]; then
			error "❌ Could not find the extracted teku directory"
		fi
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/teku/bin/teku")
		DEST_DIR=$(dirname "$(dirname "$EXEC_PATH")")
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		# install_system_directory will move the Teku directory and harden perms
		PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_directory; install_system_directory('${TEKU_DIR}', '${DEST_DIR}')"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
		rm -rf "$EXTRACT_DIR"
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
		if [ -z "$BN_BIN" ]; then
			error "❌ Could not find the extracted nimbus_beacon_node binary"
		fi
		BN_EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/nimbus_beacon_node")
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -f "$BN_EXEC_PATH"
		sudo mkdir -p "$(dirname "$BN_EXEC_PATH")"
		PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('${BN_BIN}', '${BN_EXEC_PATH}')"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
		rm -rf "$EXTRACT_DIR"
	    ;;
  	  Prysm)
		cd "$HOME" || true
		local _bn_url
		_bn_url=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')

		local _prysmctl_url
		_prysmctl_url="${_bn_url/beacon-chain/prysmctl}"

		curl -L -f "${_bn_url}" -o beacon-chain || error "❌ Unable to download beacon-chain"
		curl -L -f "${_prysmctl_url}" -o prysmctl || error "❌ Unable to download prysmctl"
		
		chmod +x beacon-chain prysmctl
		BN_EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/prysm-beacon-chain")
		PRYSMCTL_EXEC_PATH="$(dirname "$BN_EXEC_PATH")/prysmctl"
		test -f /etc/systemd/system/consensus.service && sudo systemctl stop consensus
		test -f /etc/systemd/system/validator.service && sudo service validator stop
		sudo rm -f "$BN_EXEC_PATH" "$PRYSMCTL_EXEC_PATH"
		sudo mkdir -p "$(dirname "$BN_EXEC_PATH")"
		PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('$(pwd)/beacon-chain', '${BN_EXEC_PATH}')"
		PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('$(pwd)/prysmctl', '${PRYSMCTL_EXEC_PATH}')"
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
		# install_system_binary will move and configure the binary at the full exec path
		PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('$HOME/grandine', '${EXEC_PATH}')"
		test -f /etc/systemd/system/consensus.service && sudo systemctl start consensus
		test -f /etc/systemd/system/validator.service && sudo service validator start
	    ;;
	esac
	true
}

if [[ "${1:-}" == "--auto" ]]; then
    getClient
    getLatestVersion
    updateClient "LATEST"
else
    getClient
    getClVcCurrentVersion
    getLatestVersion
    promptYesNo
fi