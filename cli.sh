#!/bin/bash
# EthPillar non-interactive CLI (automation / scripting).
# Sourced by ethpillar.sh; expects functions.sh and env already loaded.
# Commands: check-updates, help, logs [unit ...], start|stop|restart, status,
#           update|upgrade, version (--version alias).

# ── Installed targets ────────────────────────────────────────────────────────

# Absolute path to a systemd unit, honoring *_SERVICE_FILE test overrides.
cli_service_path() {
    local unit="$1"
    case "$unit" in
        execution) echo "${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}" ;;
        consensus) echo "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}" ;;
        validator) echo "${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}" ;;
        mevboost)  echo "${MEVBOOST_SERVICE_FILE:-/etc/systemd/system/mevboost.service}" ;;
        charon)    echo "${CHARON_SERVICE_FILE:-/etc/systemd/system/charon.service}" ;;
        csm_nimbusvalidator)
            echo "${CSM_VALIDATOR_SERVICE_FILE:-/etc/systemd/system/csm_nimbusvalidator.service}"
            ;;
        *)         echo "/etc/systemd/system/${unit}.service" ;;
    esac
}

# True when the unit file for a client target exists.
cli_is_client_installed() {
    local target="$1"
    [[ -f "$(cli_service_path "$target")" ]]
}

# Canonical start/restart order. Charon must precede validator so the VC can
# reach the intermediate (see startConsensusStackAfterUpdate / #57).
CLI_CLIENT_START_ORDER=(execution consensus mevboost charon validator)
# Stop order: reverse dependency (validator before charon).
CLI_CLIENT_STOP_ORDER=(validator charon mevboost consensus execution)
# Units accepted by `ethpillar logs [unit ...]`. Same set as the rolling
# consolidated journalctl stream. Named units are followed in argv order.
CLI_LOG_UNITS=(validator consensus execution mevboost charon csm_nimbusvalidator)

# Print space-separated client targets installed on this host (no ethpillar).
# Default listing follows start order.
cli_installed_client_targets() {
    local t targets=()
    local -n _order="${1:-CLI_CLIENT_START_ORDER}"
    for t in "${_order[@]}"; do
        if cli_is_client_installed "$t"; then
            targets+=("$t")
        fi
    done
    echo "${targets[*]}"
}

# Print space-separated targets valid for upgrade/check-updates (clients + ethpillar).
cli_upgradable_targets() {
    local clients
    clients=$(cli_installed_client_targets)
    if [[ -n "$clients" ]]; then
        echo "${clients} ethpillar"
    else
        echo "ethpillar"
    fi
}

# Print targets not installed (for help). Always excludes ethpillar.
cli_missing_client_targets() {
    local t missing=()
    for t in "${CLI_CLIENT_START_ORDER[@]}"; do
        if ! cli_is_client_installed "$t"; then
            missing+=("$t")
        fi
    done
    echo "${missing[*]}"
}

# Resolve a user target arg into a list of concrete targets.
# Usage: cli_resolve_targets <mode> <arg> [order_array_name]
#   mode=clients  → start/stop/restart (no ethpillar)
#   mode=upgrade  → check-updates/upgrade (includes ethpillar)
# Prints targets one per line; returns 1 on invalid/not-installed target.
cli_resolve_targets() {
    local mode="$1"
    local arg="${2:-all}"
    local order_name="${3:-CLI_CLIENT_START_ORDER}"
    local t allowed installed

    arg="${arg,,}"
    if [[ "$mode" == "upgrade" ]]; then
        allowed="all execution consensus validator mevboost charon ethpillar"
        installed=$(cli_upgradable_targets)
    else
        allowed="all execution consensus validator mevboost charon"
        installed=$(cli_installed_client_targets "$order_name")
    fi

    if [[ "$arg" == "all" ]]; then
        if [[ -z "$installed" ]]; then
            echo "No matching targets installed." >&2
            return 1
        fi
        # shellcheck disable=SC2086
        printf '%s\n' $installed
        return 0
    fi

    if ! [[ " $allowed " == *" $arg "* ]]; then
        echo "Unknown target: $arg" >&2
        echo "Valid targets: $allowed" >&2
        return 1
    fi

    if [[ "$arg" == "ethpillar" ]]; then
        echo "ethpillar"
        return 0
    fi

    if ! cli_is_client_installed "$arg"; then
        echo "Target not installed: $arg" >&2
        echo "Installed: ${installed:-none}" >&2
        return 1
    fi
    echo "$arg"
}

# Print space-separated log units installed on this host (CLI_LOG_UNITS order).
cli_installed_log_units() {
    local t units=()
    for t in "${CLI_LOG_UNITS[@]}"; do
        if cli_is_client_installed "$t"; then
            units+=("$t")
        fi
    done
    echo "${units[*]}"
}

# Resolve one or more log unit names. Prints units one per line in the order
# given (first occurrence wins). Returns 1 on unknown or not-installed.
cli_resolve_log_units() {
    local arg seen=" " installed allowed
    allowed="${CLI_LOG_UNITS[*]}"
    installed=$(cli_installed_log_units)

    for arg in "$@"; do
        arg="${arg,,}"
        if ! [[ " $allowed " == *" $arg "* ]]; then
            echo "Unknown target: $arg" >&2
            echo "Valid targets: $allowed" >&2
            return 1
        fi
        if ! cli_is_client_installed "$arg"; then
            echo "Target not installed: $arg" >&2
            echo "Installed: ${installed:-none}" >&2
            return 1
        fi
        if [[ " $seen " == *" $arg "* ]]; then
            continue
        fi
        seen+="$arg "
        printf '%s\n' "$arg"
    done
}

# ── Help ─────────────────────────────────────────────────────────────────────

cli_cmd_help() {
    local clients missing upgradable
    clients=$(cli_installed_client_targets)
    missing=$(cli_missing_client_targets)
    upgradable=$(cli_upgradable_targets)

    cat <<EOF
Usage: ethpillar [<command> [target]]

With no arguments, launches the interactive TUI.

Commands:
  check-updates [target]          Report available updates (default: all)
  help | --help | -h              Show this help
  logs [unit ...]                 View rolling consolidated logs (same as TUI Rolling Consolidated Logs)
  --migrate_cdvn [--migrate_cdvn_path=PATH]
                                  Migrate a Charon DV node (advanced)
  start|stop|restart [target]     Control installed clients (default: all)
  status [--json]                 Show systemd status of installed clients
  update [target]                 Apply updates non-interactively (same as upgrade)
  upgrade [target]                Apply updates non-interactively (default: all)
  version                         Print installed client and EthPillar versions (alias: --version)

Targets on this node:
  logs|restart|start|status|stop:  ${clients:-none}
  check-updates|update|upgrade:  ${upgradable}

EOF
    if [[ -n "$missing" ]]; then
        echo "(Not installed: $missing)"
        echo
    fi
    cat <<EOF
Exit codes:
  check-updates   0 = up to date; 2 = update(s) available; 1 = error
  logs            0 = normal exit from the log viewer; 1 = unexpected arguments or error
  start|stop|restart  0 = success; 1 = error
  status          0 = all installed clients active; 1 = any inactive/failed
  update|upgrade  0 = success; 1 = error

Examples:
  ethpillar check-updates
  ethpillar logs
  ethpillar logs charon validator
  ethpillar logs execution
  ethpillar restart consensus
  ethpillar status
  ethpillar upgrade ethpillar
  ethpillar upgrade execution
EOF
}

# ── Status / lifecycle ───────────────────────────────────────────────────────

# Print ActiveState for a systemd unit name (execution, consensus, ...).
cli_unit_active_state() {
    local unit="$1"
    local state
    state=$(systemctl is-active "$unit" 2>/dev/null) || true
    echo "${state:-inactive}"
}

cli_cmd_status() {
    local json=0
    local t clients state rc=0 first=1

    if [[ "${1:-}" == "--json" ]]; then
        json=1
        shift
    fi
    if [[ -n "${1:-}" ]]; then
        echo "Unexpected argument: $1 (try: ethpillar status [--json])" >&2
        return 1
    fi

    clients=$(cli_installed_client_targets)
    if [[ -z "$clients" ]]; then
        if [[ "$json" -eq 1 ]]; then
            echo '{"services":{}}'
        else
            echo "No clients installed."
        fi
        return 0
    fi

    if [[ "$json" -eq 1 ]]; then
        printf '{"services":{'
        # shellcheck disable=SC2086
        for t in $clients; do
            state=$(cli_unit_active_state "$t")
            [[ "$state" == "active" ]] || rc=1
            [[ "$first" -eq 1 ]] || printf ','
            first=0
            printf '"%s":"%s"' "$t" "$state"
        done
        printf '}}\n'
        return "$rc"
    fi

    printf '%-12s %s\n' "SERVICE" "STATE"
    # shellcheck disable=SC2086
    for t in $clients; do
        state=$(cli_unit_active_state "$t")
        printf '%-12s %s\n' "$t" "$state"
        [[ "$state" == "active" ]] || rc=1
    done
    return "$rc"
}

cli_cmd_service_action() {
    local action="$1"
    local target_arg="${2:-all}"
    local t targets rc=0 order_name="CLI_CLIENT_START_ORDER"

    # Stop tears down dependents first (validator → charon); start/restart
    # bring Charon up before the VC so :3600 is available.
    if [[ "$action" == "stop" ]]; then
        order_name="CLI_CLIENT_STOP_ORDER"
    fi

    if ! targets=$(cli_resolve_targets clients "$target_arg" "$order_name"); then
        return 1
    fi

    while IFS= read -r t; do
        [[ -n "$t" ]] || continue
        echo "${action}: $t"
        if ! sudo systemctl "$action" "$t"; then
            echo "Failed to ${action} $t" >&2
            rc=1
        fi
    done <<< "$targets"
    return "$rc"
}

# ── Updates ──────────────────────────────────────────────────────────────────

# Check one client target; prints a status line.
# Returns 0 if up to date, 2 if update available, 1 on error.
# Compare with TUI promptYesNo: load versions then version_matches_latest.
cli_check_client_update() {
    local target="$1"

    load_client_versions "$target" || return 1
    if version_matches_latest; then
        echo "${target}: up to date ($(format_version_label "$VERSION" "${INSTALLED_COMMIT:-}"))"
        return 0
    fi
    echo "${target}: update available ($(format_version_label "$VERSION" "${INSTALLED_COMMIT:-}") → $(format_version_label "$TAG" "${TAG_COMMIT:-}"))"
    return 2
}

# Compare local EP_VERSION to origin/main ethpillar.sh.
cli_check_ethpillar_update() {
    local current latest
    current="${EP_VERSION}"
    latest=$(fetch_ethpillar_remote_version) || {
        echo "ethpillar: could not fetch origin/main"
        return 1
    }
    if [[ "$current" == "$latest" ]]; then
        echo "ethpillar: up to date ($current)"
        return 0
    fi
    echo "ethpillar: update available ($current → $latest)"
    return 2
}

cli_cmd_check_updates() {
    local target_arg="${1:-all}"
    local t targets rc=0 saw_update=0

    if ! targets=$(cli_resolve_targets upgrade "$target_arg"); then
        return 1
    fi

    while IFS= read -r t; do
        [[ -n "$t" ]] || continue
        if [[ "$t" == "ethpillar" ]]; then
            cli_check_ethpillar_update
        else
            cli_check_client_update "$t"
        fi
        case $? in
            0) ;;
            2) saw_update=1 ;;
            *) rc=1 ;;
        esac
    done <<< "$targets"

    [[ "$rc" -ne 0 ]] && return 1
    [[ "$saw_update" -eq 1 ]] && return 2
    return 0
}

# Non-interactive EthPillar self-update (shared core: upgradeEthPillar).
# Skip when local EP_VERSION already matches origin/main (same compare as check-updates).
cli_upgrade_ethpillar() {
    local current latest
    current="${EP_VERSION}"
    # Best-effort remote version; only skip when it is known and matches.
    latest=$(fetch_ethpillar_remote_version || true)
    if [[ -n "$latest" && "$current" == "$latest" ]]; then
        echo "ethpillar: already up to date ($current) — skipping"
        return 0
    fi
    echo "Updating EthPillar..."
    echo "Current: $current  Remote: ${latest:-unknown}"
    upgradeEthPillar || return 1
    latest=$(grep '^EP_VERSION=' "$BASE_DIR/ethpillar.sh" 2>/dev/null | cut -d'"' -f2)
    echo "EthPillar updated to ${latest:-unknown}."
    return 0
}

# Path to update_<target>.sh (ETHPILLAR_UPDATE_SCRIPT_DIR overrides for tests).
cli_auto_update_script() {
    local target="$1"
    echo "${ETHPILLAR_UPDATE_SCRIPT_DIR:-$BASE_DIR}/update_${target}.sh"
}

# Upgrade one target. Clients already on LATEST are skipped so --auto does not
# stop/replace a running binary with the same version. update_*.sh --auto is
# unchanged (forced reinstall when invoked directly).
# Same gate as TUI update menus: load versions, then version_matches_latest
# (promptYesNo).
cli_upgrade_one() {
    local target="$1"

    case "$target" in
        ethpillar)
            cli_upgrade_ethpillar
            return
            ;;
        execution|consensus|validator|mevboost|charon)
            ;;
        *)
            echo "Unsupported upgrade target: $target" >&2
            return 1
            ;;
    esac

    load_client_versions "$target" || return 1
    if version_matches_latest; then
        echo "${target}: already up to date ($(format_version_label "$VERSION" "${INSTALLED_COMMIT:-}")) — skipping"
        return 0
    fi

    bash "$(cli_auto_update_script "$target")" --auto
}

cli_cmd_upgrade() {
    local target_arg="${1:-all}"
    local t targets rc=0

    if ! targets=$(cli_resolve_targets upgrade "$target_arg"); then
        return 1
    fi

    while IFS= read -r t; do
        [[ -n "$t" ]] || continue
        echo "========================================="
        echo " Upgrading: $t"
        echo "========================================="
        if ! cli_upgrade_one "$t"; then
            echo "Upgrade failed: $t" >&2
            rc=1
        fi
    done <<< "$targets"
    return "$rc"
}

# ── Logs ─────────────────────────────────────────────────────────────────────

# Same as TUI Logging & Monitoring → 🔍 View Rolling Consolidated Logs.
# No args: Aztec remote-rpc compose (when applicable) plus the full unit set.
# With unit names: follow only those systemd units, in the order given
# (duplicates dropped). Aztec docker compose is not used when units are named.
cli_cmd_logs() {
    local units u
    local args=()

    if [[ $# -eq 0 ]]; then
        show_rolling_consolidated_logs
        return
    fi

    if ! units=$(cli_resolve_log_units "$@"); then
        return 1
    fi

    while IFS= read -r u; do
        [[ -n "$u" ]] || continue
        args+=(-u "$u")
    done <<< "$units"

    view_journal_logs "${args[@]}" --no-hostname -f
}

# ── Dispatcher ───────────────────────────────────────────────────────────────

# Handle ethpillar CLI subcommands. Returns 0 if a CLI command was handled
# (caller should exit with the command's status). Returns 1 if argv should
# fall through to the TUI (no CLI command).
#
# Sets CLI_EXIT_CODE when a command was handled.
cli_dispatch() {
    local cmd="${1:-}"
    CLI_EXIT_CODE=0

    case "$cmd" in
        "" )
            return 1
            ;;
        --help|-h|help)
            cli_cmd_help
            CLI_EXIT_CODE=0
            return 0
            ;;
        version|--version)
            printInstalledVersions
            CLI_EXIT_CODE=0
            return 0
            ;;
        status)
            shift
            cli_cmd_status "$@"
            CLI_EXIT_CODE=$?
            return 0
            ;;
        start|stop|restart)
            local action="$1"
            shift
            cli_cmd_service_action "$action" "${1:-all}"
            CLI_EXIT_CODE=$?
            return 0
            ;;
        check-updates)
            shift
            cli_cmd_check_updates "${1:-all}"
            CLI_EXIT_CODE=$?
            return 0
            ;;
        update|upgrade)
            shift
            cli_cmd_upgrade "${1:-all}"
            CLI_EXIT_CODE=$?
            return 0
            ;;
        logs)
            shift
            cli_cmd_logs "$@"
            CLI_EXIT_CODE=$?
            return 0
            ;;
        --migrate_cdvn|--migrate_cdvn=*)
            # Handled by ethpillar.sh entrypoint (needs whiptail colors).
            return 1
            ;;
        -*)
            echo "Unknown option: $cmd" >&2
            echo "Try: ethpillar --help" >&2
            CLI_EXIT_CODE=1
            return 0
            ;;
        *)
            echo "Unknown command: $cmd" >&2
            echo "Try: ethpillar --help" >&2
            CLI_EXIT_CODE=1
            return 0
            ;;
    esac
}
