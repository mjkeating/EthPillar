# Copyright (C) 2026  b0a7
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# Journal access, formatted live logs, and raw journal export.
# Sourced by functions.sh so existing callers keep the same function names.
#
# journal_format.py is run as a script. logging/ is not a Python package:
# adding logging/__init__.py would shadow the stdlib logging module.

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

# Format one journalctl JSON record per line into the EthPillar log shape.
# Stdlib only, so system python3 is enough (no venv).
_journal_format_stream() {
    python3 -u "${BASE_DIR}/logging/journal_format.py"
}

# Build a journalctl -o json | formatter | ccze pipeline for tmux/log panes.
journalctl_ccze_pipeline() {
    local _args=("$@")
    local _inner="journalctl -o json"
    local _a _fmt
    for _a in "${_args[@]}"; do
        _inner+=" $(printf '%q' "$_a")"
    done
    _fmt="$(printf '%q -u %q' python3 "${BASE_DIR}/logging/journal_format.py")"
    if can_read_journal; then
        printf '%s | %s | ccze -A' "$_inner" "$_fmt"
    elif user_in_journal_group "$(whoami)"; then
        printf 'sg systemd-journal -c %q | %s | ccze -A' "$_inner" "$_fmt"
    else
        printf 'sudo %s | %s | ccze -A' "$_inner" "$_fmt"
    fi
}

view_journal_logs() {
    # Parent ignores SIGINT so EthPillar survives Ctrl-C.
    # Child restores default so journalctl still stops.
    # -o json is added here so every viewer (TUI, CLI, tmux panes) shares one
    # formatter. export_logs calls journalctl_run directly and stays raw.
    export BASE_DIR
    export -f _journal_log_colorizer _journal_format_stream journalctl_run can_read_journal user_in_journal_group 2>/dev/null || true
    trap '' INT

    bash -c 'trap - INT; journalctl_run -o json "$@" | _journal_format_stream | _journal_log_colorizer' _ "$@" || true

    trap - INT
    return 0
}

# TUI Logging & Monitoring → 🔍 View Rolling Consolidated Logs, and `ethpillar logs`.
# Aztec remote-rpc compose follow (when applicable), then journalctl for installed
# client units only — SSV-only hosts must not follow missing EL/CL/VC units.
show_rolling_consolidated_logs() {
    local units=()
    local u
    # Aztec with remote rpc
    if [[ -d /opt/ethpillar/aztec ]] && [[ ! -f /etc/systemd/system/consensus.service ]]; then
          cd  /opt/ethpillar/aztec && docker compose logs -f --tail=233
    fi
    for u in validator consensus execution mevboost charon web3signer ssv-node ssv-dkg csm_nimbusvalidator; do
        if [[ -f /etc/systemd/system/${u}.service ]]; then
            units+=(-u "$u")
        fi
    done
    if [[ ${#units[@]} -eq 0 ]]; then
        echo "No client systemd units installed to follow." >&2
        return 1
    fi
    view_journal_logs "${units[@]}" --no-hostname -f
}

# Function to display log dialog and return the selected option
function journal_export_prompt() {
    local OPTIONS=()
    local service date_range
    test -f /etc/systemd/system/execution.service && OPTIONS+=("execution" "")
    test -f /etc/systemd/system/consensus.service && OPTIONS+=("consensus" "")
    test -f /etc/systemd/system/validator.service && OPTIONS+=("validator" "")
    test -f /etc/systemd/system/charon.service && OPTIONS+=("charon" "")
    test -f /etc/systemd/system/web3signer.service && OPTIONS+=("web3signer" "")
    test -f /etc/systemd/system/mevboost.service && OPTIONS+=("mevboost" "" )
    test -f /etc/systemd/system/ssv-node.service && OPTIONS+=("ssv-node" "")
    test -f /etc/systemd/system/ssv-dkg.service && OPTIONS+=("ssv-dkg" "")
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
    user_input=$(journal_export_prompt)
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
