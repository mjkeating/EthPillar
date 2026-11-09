#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: Update Consensys Web3Signer

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/functions.sh" ]]; then
    BASE_DIR="$SCRIPT_DIR"
else
    BASE_DIR="$HOME/git/ethpillar"
fi

# shellcheck disable=SC1091
source "$BASE_DIR"/functions.sh

function getCurrentVersion(){
    getWeb3SignerCurrentVersion || true
    if [[ -z "$VERSION" ]]; then
        VERSION="Client not installed."
    fi
}

function selectCustomTag(){
	local _listTags _tag
	_listTags=$(github_curl "https://api.github.com/repos/Consensys-Incorporated/web3signer/releases?per_page=100" \
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

function promptViewLogs(){
    if whiptail --title "Update complete" --yesno "Would you like to view logs and confirm everything is running properly?" 8 78; then
		view_journal_logs -fu web3signer
    fi
}

function getLatestVersion(){
	fetch_latest_release "web3signer" --strip-v || { echo "ERROR: Couldn't find the latest version tag"; exit 1; }
	CHANGES_URL="https://github.com/Consensys-Incorporated/web3signer/releases"
}

function updateClient(){
	local _target_tag
	if [[ "$1" == "LATEST" ]]; then
		_target_tag="LATEST"
	else
		_target_tag="$1"
	fi

	RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "web3signer" "$_target_tag")
	TAG=$(echo "$RELEASE_DATA" | jq -r .version | sed 's/^v//')
	BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
	FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')

	info "ℹ️  Downloading URL: $BINARIES_URL"
	cd "$HOME" || true
	download_release_asset 0 "$FILENAME" "web3signer"
	sudo systemctl stop web3signer
	PYTHONPATH="${BASE_DIR}" python3 -m deploy.common extract_and_install "$FILENAME" "web3signer" "/usr/local/bin/web3signer" "directory" 1
	PYTHONPATH="${BASE_DIR}" python3 -m deploy.web3signer apply-migrations
	sudo systemctl start web3signer
}

if [[ "${1:-}" == "--auto" ]]; then
    getLatestVersion
    updateClient "LATEST"
else
    getCurrentVersion
    getLatestVersion
    promptYesNo "Web3Signer" "Web3Signer"
fi
