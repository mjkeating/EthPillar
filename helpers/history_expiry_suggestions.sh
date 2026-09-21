#!/usr/bin/env bash
#
# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: Print suggested history-expiry / prune flags for ~2TB staking nodes.
#
# Made for home and solo stakers 🏠🥩
#
# Suggest-first / print-only. Does not rewrite systemd units.
# Research notes: docs/history-expiry-suggestions.md
#

# Allow sourcing from node-checker / bats without running main.
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  _HISTORY_EXPIRY_SELF="${BASH_SOURCE[0]}"
else
  _HISTORY_EXPIRY_SELF="$0"
fi

history_expiry_norm() {
  # Lowercase and collapse whitespace so `--flag value` and `--flag=value` match.
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g'
}

history_expiry_has_flag() {
  # Usage: history_expiry_has_flag HAYSTACK FLAG [VALUE]
  # Glob matching (not regex) so dots in flag names are literal.
  local hay flag value
  hay=" $(history_expiry_norm "${1:-}") "
  flag=$(history_expiry_norm "${2:-}")
  value=$(history_expiry_norm "${3:-}")
  [[ -n "$flag" ]] || return 1
  if [[ -n "$value" ]]; then
    [[ "$hay" == *" ${flag}=${value} "* || "$hay" == *" ${flag} ${value} "* ]]
    return $?
  fi
  [[ "$hay" == *" ${flag} "* || "$hay" == *" ${flag}="* ]]
}

history_expiry_extract_description() {
  printf '%s\n' "${1:-}" | grep -m1 -E '^Description=' | sed 's/^Description=//'
}

history_expiry_extract_execstart() {
  # Join a systemd ExecStart= block (backslash continuations) into one line.
  local content="${1:-}" in_exec=0 line payload
  local -a parts=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ $in_exec -eq 0 ]]; then
      if [[ "$line" == ExecStart=* ]]; then
        in_exec=1
        payload="${line#ExecStart=}"
      else
        continue
      fi
    else
      payload="$line"
    fi
    if [[ "$payload" == *\\ ]]; then
      payload="${payload%\\}"
      parts+=("$payload")
      continue
    fi
    parts+=("$payload")
    break
  done <<< "$content"
  printf '%s' "${parts[*]}"
}

history_expiry_detect_client() {
  # Prefer Description first token (EthPillar style); fall back to ExecStart binary.
  local description="${1:-}"
  local execstart="${2:-}"
  local token
  token=$(printf '%s' "$description" | awk '{print $1}')
  case "$token" in
    Geth|Nethermind|Besu|Reth|Ethrex) printf '%s\n' "$token"; return 0 ;;
    Erigon-Caplin) printf '%s\n' "Erigon-Caplin"; return 0 ;;
    Erigon) printf '%s\n' "Erigon"; return 0 ;;
  esac
  local n
  n=$(history_expiry_norm "$execstart")
  if [[ "$n" == *nethermind* ]]; then echo "Nethermind"
  elif [[ "$n" == *besu* ]]; then echo "Besu"
  elif [[ "$n" == *reth* ]]; then echo "Reth"
  elif [[ "$n" == *ethrex* ]]; then echo "Ethrex"
  elif [[ "$n" == *erigon* ]]; then
    if [[ "$n" == *caplin* ]]; then echo "Erigon-Caplin"; else echo "Erigon"; fi
  elif [[ "$n" == *geth* ]]; then echo "Geth"
  else echo ""
  fi
}

history_expiry_is_el_archive() {
  local client="${1:-}" execstart="${2:-}"
  case "$client" in
    Geth)
      history_expiry_has_flag "$execstart" "--gcmode" "archive" && return 0
      history_expiry_has_flag "$execstart" "--history.state" "0" && return 0
      return 1
      ;;
    Nethermind)
      history_expiry_has_flag "$execstart" "--Pruning.Mode" "None" && return 0
      history_expiry_has_flag "$execstart" "--Pruning.Mode" "Archive" && return 0
      return 1
      ;;
    Besu)
      history_expiry_has_flag "$execstart" "--data-storage-format" "FOREST" && return 0
      history_expiry_has_flag "$execstart" "--data-storage-format" "X_BONSAI_ARCHIVE" && return 0
      return 1
      ;;
    Reth)
      history_expiry_has_flag "$execstart" "--archive" && return 0
      # Reth default with no prune profile is archive.
      if history_expiry_has_flag "$execstart" "--full"; then return 1; fi
      if history_expiry_has_flag "$execstart" "--minimal"; then return 1; fi
      if history_expiry_has_flag "$execstart" "--prune.bodies.pre-merge"; then return 1; fi
      if history_expiry_has_flag "$execstart" "--prune.bodies.distance"; then return 1; fi
      if history_expiry_has_flag "$execstart" "--prune.mode"; then return 1; fi
      [[ -n "$execstart" ]]
      return $?
      ;;
    Erigon|Erigon-Caplin)
      history_expiry_has_flag "$execstart" "--prune.mode" "archive" && return 0
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

history_expiry_is_caplin_archive() {
  local execstart="${1:-}"
  history_expiry_has_flag "$execstart" "--caplin.states-archive" && return 0
  history_expiry_has_flag "$execstart" "--caplin.blocks-archive" && return 0
  history_expiry_has_flag "$execstart" "--caplin.blobs-archive" && return 0
  history_expiry_has_flag "$execstart" "--caplin.blobs-no-pruning" && return 0
  history_expiry_has_flag "$execstart" "--caplin.archive" && return 0
  return 1
}

history_expiry_has_recommended() {
  local client="${1:-}" execstart="${2:-}"
  case "$client" in
    Geth)
      history_expiry_has_flag "$execstart" "--history.chain" "postmerge" && return 0
      history_expiry_has_flag "$execstart" "--history.chain" "postprague" && return 0
      history_expiry_has_flag "$execstart" "--history.chain" "recent" && return 0
      return 1
      ;;
    Nethermind)
      history_expiry_has_flag "$execstart" "--History.Pruning" "Rolling" && return 0
      history_expiry_has_flag "$execstart" "--History.Pruning" "UseAncientBarriers" && return 0
      return 1
      ;;
    Besu)
      history_expiry_has_flag "$execstart" "--sync-mode" "SNAP" && return 0
      history_expiry_has_flag "$execstart" "--Xchain-pruning-enabled" && return 0
      return 1
      ;;
    Reth)
      history_expiry_has_flag "$execstart" "--full" && return 0
      history_expiry_has_flag "$execstart" "--minimal" && return 0
      history_expiry_has_flag "$execstart" "--prune.bodies.pre-merge" && return 0
      history_expiry_has_flag "$execstart" "--prune.bodies.distance" && return 0
      return 1
      ;;
    Erigon|Erigon-Caplin)
      history_expiry_has_flag "$execstart" "--prune.mode" "minimal" && return 0
      history_expiry_has_flag "$execstart" "--prune.mode" "full" && return 0
      history_expiry_has_flag "$execstart" "--prune.mode" "blocks" && return 0
      return 1
      ;;
    Ethrex)
      # No history-expiry flags yet; snap is the staking-oriented default.
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

history_expiry_status() {
  # Prints: recommended | missing | archive | caplin_archive | unsupported | unknown | no_el
  local client="${1:-}" execstart="${2:-}"
  if [[ -z "$client" && -z "$execstart" ]]; then
    echo "no_el"
    return 0
  fi
  if [[ -z "$client" ]]; then
    echo "unknown"
    return 0
  fi
  if [[ "$client" == "Ethrex" ]]; then
    echo "unsupported"
    return 0
  fi
  if history_expiry_is_el_archive "$client" "$execstart"; then
    echo "archive"
    return 0
  fi
  if [[ "$client" == "Erigon-Caplin" ]] && history_expiry_is_caplin_archive "$execstart"; then
    echo "caplin_archive"
    return 0
  fi
  if history_expiry_has_recommended "$client" "$execstart"; then
    echo "recommended"
    return 0
  fi
  echo "missing"
}

history_expiry_suggested_flags() {
  local client="${1:-}"
  case "$client" in
    Geth)
      echo "--history.chain=postmerge"
      ;;
    Nethermind)
      echo "--History.Pruning=Rolling"
      ;;
    Besu)
      echo "--sync-mode=SNAP --data-storage-format=BONSAI"
      ;;
    Reth)
      echo "--full"
      ;;
    Erigon|Erigon-Caplin)
      echo "--prune.mode=minimal"
      ;;
    Ethrex)
      echo "(none — Ethrex has no history-expiry CLI yet; keep --syncmode snap)"
      ;;
    *)
      echo "(unknown client)"
      ;;
  esac
}

history_expiry_optional_rolling_flags() {
  local client="${1:-}"
  case "$client" in
    Geth)
      echo "--history.chain=postprague   # if binary supports it; still not rolling"
      echo "# rolling (may still be experimental): --history.chain=recent --history.blocks=N  (N > 100000)"
      ;;
    Nethermind)
      echo "--History.Pruning=UseAncientBarriers   # pre-merge expiry; safer than rolling"
      echo "# rolling window is ~1 year (min --History.RetentionEpochs=82125 on mainnet)"
      ;;
    Besu)
      echo "--Xchain-pruning-enabled=ALL --Xchain-pruning-blocks-retained=1056768   # ~5 months; experimental"
      ;;
    Reth)
      echo "--prune.bodies.distance 1056768 --prune.receipts.distance 1056768   # ~5 months rolling"
      echo "--minimal   # aggressive; not for protocols that need local receipts/logs"
      ;;
    Erigon|Erigon-Caplin)
      echo "--prune.mode=full --persist.receipts=false --prune.distance=1056768 --prune.distance.blocks=1056768   # ~5 months"
      echo "# --prune.mode=minimal already keeps ~100k blocks (~14 days); most aggressive built-in"
      ;;
    *)
      echo ""
      ;;
  esac
}

history_expiry_rationale() {
  local client="${1:-}"
  case "$client" in
    Geth)
      cat <<'EOF'
Geth defaults to --history.chain=all (full bodies/receipts). For a ~2TB staking
full node, --history.chain=postmerge drops pre-merge PoW history (~300-500 GB).
Stop Geth first, then run: geth prune-history --datadir <datadir> --history.chain postmerge
Restart with the same --history.chain flag. Path-based state (--state.scheme=path)
is separate from block-history expiry. --gcmode=archive / --history.state=0 is
intentional archive — skip these suggestions.
EOF
      ;;
    Nethermind)
      cat <<'EOF'
EthPillar already sets Hybrid pruning + FullPruningTrigger=VolumeFreeSpace
(ThresholdMb=300000). That prunes *state*, not block history. For ~2TB staking,
add --History.Pruning=Rolling (~1 year, min 82125 epochs on mainnet) or
--History.Pruning=UseAncientBarriers for pre-merge expiry. Rolling was still
marked experimental in Nethermind/eth-docker notes (early 2026). Protocols
that scrape local eth_getLogs (Rocket Pool / SSV / StakeWise) should keep
pre-merge expiry rather than a short rolling window, or use an external RPC.
EOF
      ;;
    Besu)
      cat <<'EOF'
EthPillar SNAP + BONSAI already skips downloading pre-merge bodies/receipts on
Mainnet (checkpoint genesis). That is the staking-oriented ~2TB starting point
(~1.14 TB observed for Besu 26.5.0 snap). Existing full-history DBs can prune
offline: besu --data-path=<path> storage prune-pre-merge-blocks. Online
--history-expiry-prune is deprecated in Besu 26.1.0. Optional rolling
(--Xchain-pruning-*) is experimental. Forest/FULL archive is intentional.
EOF
      ;;
    Reth)
      cat <<'EOF'
EthPillar --full is the staking full-node profile: ~10,064-block state window,
pre-merge body pruning, receipts retained for that window. Reth with no prune
flags is archive. Optional rolling (~5 months, 1,056,768 blocks) or --minimal
saves more disk but drops receipts/logs that some staking protocols need.
Pruning is destructive.
EOF
      ;;
    Erigon|Erigon-Caplin)
      cat <<'EOF'
EthPillar --prune.mode=minimal already keeps ~100k blocks (~14 days) — the
leanest built-in mode and the usual 2TB staking choice. --prune.mode=full now
follows a ~262k-block window (EIP-8252) unless you pin --prune.distance.blocks.
Caplin --caplin.states-archive / --caplin.blocks-archive / blob-archive flags
mean the consensus side may intentionally keep more history; do not treat
those as a failure. --prune.mode=archive is intentional EL archive.
EOF
      ;;
    Ethrex)
      cat <<'EOF'
Ethrex has no history-expiry / prune flags comparable to other ELs (eth-docker
prune-history is a no-op). EthPillar's --syncmode snap is the staking default.
Monitor disk; there is nothing to add for rolling expiry yet.
EOF
      ;;
    *)
      echo "No suggestion table for this client."
      ;;
  esac
}

history_expiry_cl_note() {
  local cl="${1:-}"
  case "$cl" in
    Lighthouse)
      echo "CL: Lighthouse already prunes blobs/payloads by default. Avoid --prune-blobs=false and --supernode on ~2TB staking disks."
      ;;
    Teku)
      echo "CL: Teku --data-storage-mode=minimal (default) is the staking setting; archive reconstructs historic states."
      ;;
    Prysm)
      echo "CL: Prysm --beacon-db-pruning is opt-in for operators who do not need historic beacon data."
      ;;
    Nimbus)
      echo "CL: Nimbus staking nodes should keep the pruned/default storage profile, not archive."
      ;;
    Lodestar)
      echo "CL: Lodestar staking nodes should keep the pruned/default profile; archive is for historic queries."
      ;;
    Grandine)
      echo "CL: Grandine aggressive-pruned is optional; default pruned is enough for staking."
      ;;
    *)
      echo "CL: Beacon archive / supernode / no-blob-prune flags are optional extras, not required to validate."
      ;;
  esac
}

history_expiry_banner() {
  cat <<'EOF'
################################################################################
History expiry suggestions for ~2TB home staking / full nodes
################################################################################
These are SUGGESTIONS only — they are not applied automatically.
Not for intentional archive, Caplin archive, or operators who need full
eth_getLogs / receipt history locally (Rocket Pool, SSV, StakeWise, indexers).
To apply later: Execution Client → Edit configuration. Review upstream docs
first. Pruning is destructive and may require an offline prune or a resync.
EOF
}

history_expiry_print_disclaimer() {
  cat <<'EOF'

Disclaimer: suggestions target staking/full nodes on ~2TB NVMe. Archive,
Caplin archive, and full-history RPC nodes should ignore them. Rolling windows
shorter than ~1 year can break local receipt/log queries.
Suggestions only — systemd units are not modified.
EOF
}

history_expiry_print_client_block() {
  local client="${1:-}"
  echo
  echo "=== ${client} ==="
  echo "Suggested staking flags: $(history_expiry_suggested_flags "$client")"
  history_expiry_rationale "$client"
  echo "Optional extra savings:"
  history_expiry_optional_rolling_flags "$client"
}

history_expiry_print_all() {
  history_expiry_banner
  local c
  for c in Geth Nethermind Besu Reth Erigon Ethrex; do
    history_expiry_print_client_block "$c"
  done
  echo
  echo "=== Consensus layer (if relevant) ==="
  echo "Lighthouse: default blob/payload prune is enough; skip --supernode / --prune-blobs=false."
  echo "Teku: --data-storage-mode=minimal (default)."
  echo "Prysm: consider --beacon-db-pruning if you do not need historic CL data."
  history_expiry_print_disclaimer
}

history_expiry_read_unit() {
  local path="${1:-}"
  if [[ -z "$path" || ! -e "$path" ]]; then
    return 1
  fi
  if [[ -r "$path" ]]; then
    cat "$path"
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -n cat "$path" 2>/dev/null && return 0
  fi
  return 1
}

history_expiry_evaluate_unit_text() {
  local unit_text="${1:-}"
  local cl_text="${2:-}"
  local description execstart client cl status
  description=$(history_expiry_extract_description "$unit_text")
  execstart=$(history_expiry_extract_execstart "$unit_text")
  client=$(history_expiry_detect_client "$description" "$execstart")
  cl=$(history_expiry_detect_client "$(history_expiry_extract_description "$cl_text")" "")
  if [[ -z "$client" ]]; then
    echo "no_el"
    echo "No execution client detected in the unit file."
    return 0
  fi
  status=$(history_expiry_status "$client" "$execstart")
  echo "$status"
  echo "Detected EL: ${client}"
  if [[ -n "$execstart" ]]; then
    echo "ExecStart (collapsed): ${execstart}"
  fi
  case "$status" in
    recommended)
      echo "Installed flags already match the staking-oriented ~2TB suggestion: $(history_expiry_suggested_flags "$client")"
      echo "Optional extra savings (only if disk is still tight and you do not need local receipts):"
      history_expiry_optional_rolling_flags "$client"
      ;;
    missing)
      echo "Installed EL is missing recommended history-expiry / prune flags for a ~2TB staking full node."
      echo "Suggested flags: $(history_expiry_suggested_flags "$client")"
      history_expiry_rationale "$client"
      echo "Optional extra savings:"
      history_expiry_optional_rolling_flags "$client"
      ;;
    archive)
      echo "INFO: This looks like an intentional archive / full-history EL. Suggestions are opt-in and skipped as a failure."
      echo "If you actually want a staking full node on ~2TB, suggested flags: $(history_expiry_suggested_flags "$client")"
      ;;
    caplin_archive)
      echo "INFO: Caplin archive flags detected; consensus history may be kept on purpose. Not a failure."
      echo "EL prune suggestion remains: $(history_expiry_suggested_flags "$client")"
      ;;
    unsupported)
      echo "INFO: ${client} has no history-expiry CLI yet. $(history_expiry_suggested_flags "$client")"
      history_expiry_rationale "$client"
      ;;
    *)
      echo "Could not classify this execution client."
      ;;
  esac
  if [[ -n "$cl" ]]; then
    echo
    history_expiry_cl_note "$cl"
  fi
  history_expiry_print_disclaimer
}

history_expiry_checker_summary() {
  # One-line summary for node-checker (status already known).
  local status="${1:-}" client="${2:-}"
  case "$status" in
    recommended)
      echo "${client} already has staking-oriented expiry/prune flags ($(history_expiry_suggested_flags "$client"))."
      ;;
    missing)
      echo "${client} lacks recommended ~2TB staking expiry/prune flags. Suggested: $(history_expiry_suggested_flags "$client")"
      ;;
    archive)
      echo "${client} looks like an intentional archive / full-history node; history-expiry suggestions are opt-in."
      ;;
    caplin_archive)
      echo "Caplin archive flags present; extra consensus history may be intentional. Not a failure."
      ;;
    unsupported)
      echo "${client} has no history-expiry flags yet; nothing to add for rolling expiry."
      ;;
    no_el)
      echo "No execution client unit found."
      ;;
    *)
      echo "Could not classify execution-client history expiry."
      ;;
  esac
}

history_expiry_checker_level() {
  # Map status → node-checker print_check_result level. Never FAIL.
  case "${1:-}" in
    recommended) echo "PASS" ;;
    missing) echo "WARN" ;;
    archive|caplin_archive|unsupported) echo "INFO" ;;
    *) echo "INFO" ;;
  esac
}

history_expiry_main() {
  local print_all=0 checker=0 unit="${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
  local cl_unit="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
  local pause=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --all) print_all=1; shift ;;
      --checker) checker=1; pause=0; shift ;;
      --no-pause) pause=0; shift ;;
      --unit) unit="${2:-}"; shift 2 ;;
      --cl-unit) cl_unit="${2:-}"; shift 2 ;;
      -h|--help)
        cat <<'EOF'
Usage: history_expiry_suggestions.sh [--all] [--checker] [--unit FILE] [--cl-unit FILE]

Print suggested rolling-history / prune flags for ~2TB staking full nodes.
Does not modify systemd units.

  --all        Print the per-client suggestion table (ignore installed unit)
  --checker    Compact output for node-checker (no pause)
  --unit FILE  Read this execution.service instead of /etc/systemd/system/execution.service
EOF
        return 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        return 1
        ;;
    esac
  done

  if [[ $print_all -eq 1 ]]; then
    history_expiry_print_all
    if [[ $pause -eq 1 ]]; then
      echo
      echo "Press ENTER to return to menu"
      read -r
    fi
    return 0
  fi

  local unit_text="" cl_text=""
  unit_text=$(history_expiry_read_unit "$unit" 2>/dev/null || true)
  cl_text=$(history_expiry_read_unit "$cl_unit" 2>/dev/null || true)

  if [[ -z "$unit_text" ]]; then
    echo "No execution.service found at ${unit}."
    echo "Printing the full per-client suggestion table instead."
    echo
    history_expiry_print_all
    if [[ $pause -eq 1 ]]; then
      echo
      echo "Press ENTER to return to menu"
      read -r
    fi
    return 0
  fi

  if [[ $checker -eq 1 ]]; then
    local description execstart client status
    description=$(history_expiry_extract_description "$unit_text")
    execstart=$(history_expiry_extract_execstart "$unit_text")
    client=$(history_expiry_detect_client "$description" "$execstart")
    status=$(history_expiry_status "$client" "$execstart")
    echo "$status"
    echo "$client"
    history_expiry_checker_summary "$status" "$client"
    history_expiry_evaluate_unit_text "$unit_text" "$cl_text" | tail -n +2
    return 0
  fi

  history_expiry_banner
  echo
  history_expiry_evaluate_unit_text "$unit_text" "$cl_text"
  echo
  echo "Full table: $0 --all"
  echo "Docs: docs/history-expiry-suggestions.md"
  if [[ $pause -eq 1 ]]; then
    echo
    echo "Press ENTER to return to menu"
    read -r
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  history_expiry_main "$@"
fi
