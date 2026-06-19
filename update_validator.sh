#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: Update validator client binary (separate VC or Grandine integrated)
#
# Made for home and solo stakers 🏠🥩

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/functions.sh" ]]; then
    BASE_DIR="$SCRIPT_DIR"
else
    BASE_DIR="$HOME/git/ethpillar"
fi

__OTHERTAG=""

# shellcheck disable=SC1091
source "$BASE_DIR"/functions.sh

_platform=$(get_platform)
_arch=$(get_arch)

VALIDATOR_SVC="${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}"

# Grandine integrated VC runs inside consensus.service — reuse BN updater.
_validator_mode=$(getValidatorMode)
if [[ "$_validator_mode" == "integrated_grandine" ]]; then
    exec bash "$BASE_DIR/update_consensus.sh" "$@"
fi

if [[ "$_validator_mode" == "none" ]]; then
    if command -v whiptail >/dev/null 2>&1 && [[ -t 1 ]]; then
        whiptail --title "No Validator Client" --msgbox "No validator client is installed on this node." 8 78
    fi
    error "❌ No validator client installed."
fi

function selectCustomTag(){
    case $CLIENT in
      Lighthouse) _repo="sigp/lighthouse" ;;
      Lodestar)   _repo="ChainSafe/lodestar" ;;
      Teku)       _repo="ConsenSys/teku" ;;
      Nimbus)     _repo="status-im/nimbus-eth2" ;;
      Prysm)      _repo="OffchainLabs/prysm" ;;
      *)
        error "❌ Unsupported or unknown validator client '$CLIENT'."
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
    if [[ "${VERSION#v}" == "${TAG#v}" ]]; then
        whiptail --title "Already updated" --msgbox "You are already on the latest version: ${VERSION#v}" 10 78
        if whiptail --title "Different Version of ${CLIENT}" --defaultno --yesno "Would you like to install a different version?" 8 78; then
            selectCustomTag
            updateClient "$__OTHERTAG"
            promptViewLogs
        fi
        return
    fi
    __MSG="Installed Version is: ${VERSION#v}\nLatest Version is:    ${TAG#v}\n\nReminder: Always read the release notes for breaking changes: $CHANGES_URL\n\nDo you want to update $CLIENT validator to ${TAG#v}?"
    __SELECTTAG=$(whiptail --title "🔧 Update Validator | ${CLIENT}" --menu \
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

function promptViewLogs(){
    if whiptail --title "Update complete" --yesno "Would you like to view validator logs and confirm everything is running properly?" 8 78; then
        sudo bash -c 'journalctl -fu validator | ccze -A'
    fi
}

function getLatestVersion(){
    local _client_lower
    _client_lower=$(echo "$CLIENT" | tr '[:upper:]' '[:lower:]')
    RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "$_client_lower" "LATEST")
    TAG=$(echo "$RELEASE_DATA" | jq -r .version)
    if [[ -z $TAG ]] || [[ $TAG == "null" ]]; then
        error "❌ Couldn't find the latest version tag"
    fi
    case "$CLIENT" in
      Lighthouse) CHANGES_URL="https://github.com/sigp/lighthouse/releases" ;;
      Lodestar)   CHANGES_URL="https://github.com/ChainSafe/lodestar/releases" ;;
      Teku)       CHANGES_URL="https://github.com/ConsenSys/teku/releases" ;;
      Nimbus)     CHANGES_URL="https://github.com/status-im/nimbus-eth2/releases" ;;
      Prysm)      CHANGES_URL="https://github.com/OffchainLabs/prysm/releases" ;;
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
        EXTRACT_DIR="$HOME/lighthouse_vc_temp"
        mkdir -p "$EXTRACT_DIR"
        tar -xzvf "$FILENAME" -C "$EXTRACT_DIR" || error "❌ Unable to untar file"
        rm "$FILENAME"
        LH_BIN=$(find "$EXTRACT_DIR" -type f -name "lighthouse" | head -n 1)
        if [ -z "$LH_BIN" ]; then
            error "❌ Could not find the extracted lighthouse binary"
        fi
        EXEC_PATH=$(get_systemd_exec_path "$VALIDATOR_SVC" "/usr/local/bin/lighthouse")
        test -f "$VALIDATOR_SVC" && sudo systemctl stop validator
        sudo rm -f "$EXEC_PATH"
        sudo mkdir -p "$(dirname "$EXEC_PATH")"
        PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('${LH_BIN}', '${EXEC_PATH}')"
        rm -rf "$EXTRACT_DIR"
        test -f "$VALIDATOR_SVC" && sudo systemctl start validator
        ;;
      Lodestar)
        BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
        FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
        info "✅ Downloading URL: $BINARIES_URL"
        cd "$HOME" || true
        wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
        EXTRACTED_DIR="$HOME/lodestar_vc_temp"
        mkdir -p "$EXTRACTED_DIR"
        tar -xzvf "$FILENAME" -C "$EXTRACTED_DIR" || error "❌ Unable to untar file"
        rm "$FILENAME"
        EXEC_PATH=$(get_systemd_exec_path "$VALIDATOR_SVC" "/usr/local/bin/lodestar")
        LODESTAR_BIN=$(find "$EXTRACTED_DIR" -type f -name "lodestar" | head -n 1)
        test -f "$VALIDATOR_SVC" && sudo systemctl stop validator
        sudo rm -f "$EXEC_PATH"
        sudo mkdir -p "$(dirname "$EXEC_PATH")"
        PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('${LODESTAR_BIN}', '${EXEC_PATH}')"
        rm -rf "$EXTRACTED_DIR"
        test -f "$VALIDATOR_SVC" && sudo systemctl start validator
        ;;
      Teku)
        updateJRE 25 || error "❌ JDK 25 is required by Teku but could not be installed. Aborting update; Teku was left untouched."
        BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
        FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
        info "✅ Downloading URL: $BINARIES_URL"
        cd "$HOME" || true
        wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
        EXTRACT_DIR="$HOME/teku_vc_temp"
        mkdir -p "$EXTRACT_DIR"
        tar -xzvf "$FILENAME" -C "$EXTRACT_DIR" || error "❌ Unable to untar file"
        rm "$FILENAME"
        TEKU_DIR=$(find "$EXTRACT_DIR" -maxdepth 1 -type d -name "teku-*" | head -n 1)
        if [ -z "$TEKU_DIR" ]; then
            error "❌ Could not find the extracted teku directory"
        fi
        EXEC_PATH=$(get_systemd_exec_path "$VALIDATOR_SVC" "/usr/local/bin/teku/bin/teku")
        DEST_DIR=$(dirname "$(dirname "$EXEC_PATH")")
        test -f "$VALIDATOR_SVC" && sudo systemctl stop validator
        PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_directory; install_system_directory('${TEKU_DIR}', '${DEST_DIR}')"
        rm -rf "$EXTRACT_DIR"
        test -f "$VALIDATOR_SVC" && sudo systemctl start validator
        ;;
      Nimbus)
        BINARIES_URL=$(echo "$RELEASE_DATA" | jq -r '.download_urls[0]')
        FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
        info "✅ Downloading URL: $BINARIES_URL"
        cd "$HOME" || true
        wget -O "$FILENAME" "$BINARIES_URL" || error "❌ Unable to wget file"
        EXTRACT_DIR="$HOME/nimbus_vc_temp"
        mkdir -p "$EXTRACT_DIR"
        tar -xzvf "$FILENAME" -C "$EXTRACT_DIR" || error "❌ Unable to untar file"
        rm "$FILENAME"
        VC_BIN=$(find "$EXTRACT_DIR" -type f -name "nimbus_validator_client" | head -n 1)
        if [ -z "$VC_BIN" ]; then
            error "❌ Could not find the extracted nimbus_validator_client binary"
        fi
        VC_EXEC_PATH=$(get_systemd_exec_path "$VALIDATOR_SVC" "/usr/local/bin/nimbus_validator_client")
        test -f "$VALIDATOR_SVC" && sudo systemctl stop validator
        sudo rm -f "$VC_EXEC_PATH"
        sudo mkdir -p "$(dirname "$VC_EXEC_PATH")"
        PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('${VC_BIN}', '${VC_EXEC_PATH}')"
        rm -rf "$EXTRACT_DIR"
        test -f "$VALIDATOR_SVC" && sudo systemctl start validator
        ;;
      Prysm)
        cd "$HOME" || true
        local _vc_url
        _vc_url=$(echo "$RELEASE_DATA" | jq -r '.download_urls[1]')
        curl -L -f "${_vc_url}" -o validator || error "❌ Unable to download validator"
        chmod +x validator
        VC_EXEC_PATH=$(get_systemd_exec_path "$VALIDATOR_SVC" "/usr/local/bin/prysm-validator")
        test -f "$VALIDATOR_SVC" && sudo systemctl stop validator
        sudo rm -f "$VC_EXEC_PATH"
        sudo mkdir -p "$(dirname "$VC_EXEC_PATH")"
        PYTHONPATH="${BASE_DIR}" python3 -c "from deploy.common import install_system_binary; install_system_binary('$(pwd)/validator', '${VC_EXEC_PATH}')"
        test -f "$VALIDATOR_SVC" && sudo systemctl start validator
        ;;
      *)
        error "❌ Unsupported validator client '$CLIENT'."
        ;;
    esac
    true
}

getValidatorClient
CLIENT="$VALIDATOR_CLIENT"
if [[ -z "$CLIENT" ]]; then
    error "❌ Could not determine validator client."
fi

if [[ "${1:-}" == "--auto" ]]; then
    getClVcCurrentVersion "$CLIENT" vc
    getLatestVersion
    updateClient "LATEST"
else
    getClVcCurrentVersion "$CLIENT" vc
    getLatestVersion
    promptYesNo
fi