#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: Update SSV operator node (build from source) and ssv-dkg binary

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/functions.sh" ]]; then
    BASE_DIR="$SCRIPT_DIR"
else
    BASE_DIR="$HOME/git/ethpillar"
fi

__OTHERTAG=""

# shellcheck disable=SC1091
source "$BASE_DIR"/functions.sh

function getCurrentVersion(){
    getSsvCurrentVersion || true
    if [[ -z "$VERSION" ]]; then
        VERSION="Client not installed."
    fi
}

function getLatestVersion(){
    fetch_latest_release "ssv" --strip-v || { echo "ERROR: Couldn't find the latest SSV version tag"; exit 1; }
    CHANGES_URL="https://github.com/ssvlabs/ssv/releases"
}

function selectCustomTag(){
    local _listTags _tag
    _listTags=$(github_curl "https://api.github.com/repos/ssvlabs/ssv/releases?per_page=100" \
        | jq -r '.[] | select(.draft == false) | .tag_name' | sort -Vr)
    if [ -z "$_listTags" ]; then
        error "❌ Could not retrieve SSV releases. Try again later."
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
    if whiptail --title "Update complete" --yesno "Would you like to view ssv-node logs?" 8 78; then
        view_journal_logs -fu ssv-node
    fi
}

# Dual-binary prompt: skip when both ssv-node and ssv-dkg (if present) match LATEST.
promptUpdateSsv() {
    local node_ok=0 dkg_ok=0
    local node_installed node_latest dkg_line=""
    local dkg_installed="" dkg_latest=""
    local __MSG __SELECTTAG

    getCurrentVersion
    getLatestVersion
    node_installed="${VERSION#v}"
    node_latest="${TAG#v}"
    version_matches_latest && node_ok=1

    if isSsvDkgEnabled; then
        local saved_v="$VERSION" saved_c="${INSTALLED_COMMIT:-}" saved_t="$TAG" saved_tc="${TAG_COMMIT:-}"
        getSsvDkgCurrentVersion || true
        dkg_installed="${VERSION#v}"
        if fetch_latest_release "ssv-dkg" --strip-v; then
            dkg_latest="${TAG#v}"
            version_matches_latest && dkg_ok=1
        fi
        dkg_line="\nssv-dkg installed: ${dkg_installed:-unknown}  latest: ${dkg_latest:-unknown}"
        VERSION="$saved_v"
        INSTALLED_COMMIT="$saved_c"
        TAG="$saved_t"
        TAG_COMMIT="$saved_tc"
    else
        dkg_ok=1
    fi

    if [[ "$node_ok" -eq 1 && "$dkg_ok" -eq 1 ]]; then
        whiptail --title "Already updated" --msgbox \
            "You are already on the latest versions.\n\nssv-node: ${node_installed}\nssv-dkg: ${dkg_installed:-not installed}" 12 78
        if whiptail --title "Different Version of SSV" --defaultno --yesno \
            "Would you like to install a different ssv-node version?" 8 78; then
            selectCustomTag
            updateClient "$__OTHERTAG"
            promptViewLogs
        fi
        return
    fi

    __MSG="ssv-node installed: ${node_installed}  latest: ${node_latest}${dkg_line}\n\nReminder: Always read the release notes for breaking changes:\n${CHANGES_URL}\nhttps://github.com/ssvlabs/ssv-dkg/releases\n\nUpdate SSV binaries to latest?"

    __SELECTTAG=$(whiptail --title "🔧 Update SSV" --menu \
          "$__MSG" 20 78 2 \
          "LATEST" "| Installs latest ssv-node (+ ssv-dkg if present)" \
          "OTHER " "| I will select a different ssv-node version" \
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

function updateClient(){
    local _target_tag="${1:-LATEST}"
    local RELEASE_DATA TAG FILENAME EXEC_PATH
    local node_ver=""

    info "ℹ️  Resolving SSV node release ($_target_tag)…"
    RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "ssv" "$_target_tag")
    TAG=$(echo "$RELEASE_DATA" | jq -r .version)

    getSsvCurrentVersion || true
    node_ver="${VERSION:-}"
    # Skip source rebuild when already on the requested tag (semver match).
    if [[ "$_target_tag" == "LATEST" || "$_target_tag" == "$TAG" || "$_target_tag" == "${TAG#v}" ]] \
        && [[ -n "$node_ver" ]] && version_matches_latest "$node_ver" "$TAG"; then
        info "ℹ️  ssv-node already at ${node_ver#v} — skipping build"
    else
        info "ℹ️  Building ssvnode from source ($TAG)…"
        isSsvEnabled && sudo systemctl stop ssv-node
        PYTHONPATH="${BASE_DIR}" python3 -c \
            "from deploy.ssv import build_ssvnode_from_source; build_ssvnode_from_source('${TAG}')"
        isSsvEnabled && sudo systemctl start ssv-node
    fi

    if isSsvDkgEnabled; then
        local dkg_tag dkg_ver
        info "ℹ️  Resolving ssv-dkg release…"
        RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "ssv-dkg" "LATEST")
        dkg_tag=$(echo "$RELEASE_DATA" | jq -r .version)
        getSsvDkgCurrentVersion || true
        dkg_ver="${VERSION:-}"
        if [[ -n "$dkg_ver" ]] && version_matches_latest "$dkg_ver" "$dkg_tag"; then
            info "ℹ️  ssv-dkg already at ${dkg_ver#v} — skipping download"
        else
            info "ℹ️  Updating ssv-dkg to ${dkg_tag#v}…"
            cd "$HOME" || true
            FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
            download_release_asset 0 "$FILENAME" "ssv-dkg"
            sudo systemctl stop ssv-dkg 2>/dev/null || true
            EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/ssv-dkg.service" "/usr/local/bin/ssv-dkg")
            PYTHONPATH="${BASE_DIR}" python3 -m deploy.common extract_and_install \
                "$FILENAME" "ssv-dkg" "$EXEC_PATH" "binary" 0 --binary-name "ssv-dkg"
            sudo systemctl start ssv-dkg 2>/dev/null || true
        fi
    fi
}

if [[ "${1:-}" == "--auto" ]]; then
    # Idempotent: each binary is skipped when already on LATEST (CLI also gates
    # before invoking --auto; TUI and direct calls benefit from the same skip).
    updateClient "LATEST"
else
    promptUpdateSsv
fi
