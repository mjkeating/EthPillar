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

# Load functions
# shellcheck disable=SC1091
source "$BASE_DIR"/functions.sh

# Get machine info
_platform=$(get_platform)
_arch=$(get_arch)

function getCurrentVersion(){
    INSTALLED=$(mev-boost --version 2>&1)  # capture stderr too, just in case
    if [[ -n $INSTALLED ]] ; then
        # shellcheck disable=SC2001
		# Extract major.minor or major.minor.patch, optional leading 'v', ignore suffix/commit info
		# Patch part is optional to handle versions like 1.11 (no patch)
		VERSION=$(echo "$INSTALLED" | sed 's/.*v\?\([0-9]\+\.[0-9]\+\(\.[0-9]\+\)\?\).*/\1/')
        # Fallback if sed fails or no match
        if [[ -z $VERSION || $VERSION == "$INSTALLED" ]]; then
            VERSION="unknown"
        fi
    else
        VERSION="Client not installed."
    fi
}

function selectCustomTag(){
	local _listTags _tag
	# Published releases only — git tags like v1.11.0 may exist without release assets.
	_listTags=$(curl -fsSL "https://api.github.com/repos/flashbots/mev-boost/releases?per_page=100" \
		| jq -r '.[] | select(.draft == false) | .tag_name' | sort -Vr)
	if [ -z "$_listTags" ]; then
		error "❌ Could not retrieve releases. Try again later."
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
	    if whiptail --title "Different Version of mevboost" --defaultno --yesno "Would you like to install a different version?" 8 78; then
			selectCustomTag
			updateClient "$__OTHERTAG"
			promptViewLogs
		fi
		return
	fi
    __MSG="Installed Version is: ${VERSION#v}\nLatest Version is:    ${TAG#v}\n\nReminder: Always read the release notes for breaking changes: $CHANGES_URL\n\nDo you want to update mevboost to ${TAG#v}?"
	__SELECTTAG=$(whiptail --title "🔧 Update mevboost" --menu \
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
		sudo bash -c 'journalctl -fu mevboost | ccze -A'
    fi
}

function getLatestVersion(){
	RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "mevboost" "LATEST")
	TAG=$(echo "$RELEASE_DATA" | jq -r .version | sed 's/^v//')
	# Exit in case of null tag
	[[ -z $TAG ]] || [[ $TAG == "null"  ]] && echo "ERROR: Couldn't find the latest version tag" && exit 1
	CHANGES_URL="https://github.com/flashbots/mev-boost/releases"
}

function updateClient(){
	local _target_tag
	if [[ "$1" == "LATEST" ]]; then
		_target_tag="LATEST"
	else
		_target_tag="$1"
	fi

	RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "mevboost" "$_target_tag")
	TAG=$(echo "$RELEASE_DATA" | jq -r .version | sed 's/^v//')
	BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
	FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')

	info "ℹ️  Downloading URL: $BINARIES_URL"
	cd "$HOME" || true
	# Download
	wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Failed to download mev-boost binary."
	# Untar
	tar -xzvf "$FILENAME" -C "$HOME" || error "❌ Failed to extract mev-boost archive."
	# Cleanup
	rm "$FILENAME" LICENSE README.md 2>/dev/null || true
	EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/mevboost.service" "/usr/local/bin/mev-boost")
	sudo systemctl stop mevboost
	sudo rm -f "$EXEC_PATH"
	sudo mkdir -p "$(dirname "$EXEC_PATH")"
	# install_system_binary will move and configure the mev-boost binary at the full exec path
	PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('$HOME/mev-boost', '${EXEC_PATH}')"
	sudo systemctl start mevboost
}

if [[ "${1:-}" == "--auto" ]]; then
    getLatestVersion
    updateClient "LATEST"
else
    getCurrentVersion
    getLatestVersion
    promptYesNo
fi