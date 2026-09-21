#!/usr/bin/env bats
#
# tests/test_history_expiry_suggestions.bats
#
# Unit tests for helpers/history_expiry_suggestions.sh detection and
# suggestion text. Mock Description / ExecStart strings only — never
# talks to a live Ethereum client.
#
# Run: bats tests/test_history_expiry_suggestions.bats
#

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  # shellcheck disable=SC1091
  source ./helpers/history_expiry_suggestions.sh
}

@test "history_expiry_has_flag matches equals and space-separated values" {
  run history_expiry_has_flag "--history.chain=postmerge --http" "--history.chain" "postmerge"
  [ "$status" -eq 0 ]
  run history_expiry_has_flag "--history.chain postmerge --http" "--history.chain" "postmerge"
  [ "$status" -eq 0 ]
  run history_expiry_has_flag "--history.chain=all --http" "--history.chain" "postmerge"
  [ "$status" -ne 0 ]
}

@test "history_expiry_has_flag does not treat dots as regex" {
  run history_expiry_has_flag "--historyXchain=postmerge" "--history.chain" "postmerge"
  [ "$status" -ne 0 ]
}

@test "detects Geth from Description and ExecStart fallback" {
  run history_expiry_detect_client "Geth Execution Layer Client service for MAINNET" ""
  [ "$output" = "Geth" ]
  run history_expiry_detect_client "" "/usr/local/bin/geth --mainnet --http"
  [ "$output" = "Geth" ]
}

@test "detects Erigon-Caplin from Description first token" {
  run history_expiry_detect_client "Erigon-Caplin Integrated Execution-Consensus Client for MAINNET" "/usr/local/bin/erigon --caplin.enable-upnp"
  [ "$output" = "Erigon-Caplin" ]
}

@test "Geth without history.chain is missing and suggests postmerge" {
  local execstart="/usr/local/bin/geth --mainnet --state.scheme=path --datadir=/var/lib/geth"
  run history_expiry_status "Geth" "$execstart"
  [ "$output" = "missing" ]
  run history_expiry_suggested_flags "Geth"
  [[ "$output" == *"--history.chain=postmerge"* ]]
  run history_expiry_rationale "Geth"
  [[ "$output" == *"prune-history"* ]]
}

@test "Geth --history.chain=postmerge is recommended" {
  run history_expiry_status "Geth" "/usr/local/bin/geth --history.chain=postmerge --state.scheme=path"
  [ "$output" = "recommended" ]
}

@test "Geth --history.chain postprague is recommended" {
  run history_expiry_status "Geth" "/usr/local/bin/geth --history.chain postprague"
  [ "$output" = "recommended" ]
}

@test "Geth archive gcmode is archive not FAIL" {
  run history_expiry_status "Geth" "/usr/local/bin/geth --gcmode=archive --syncmode full"
  [ "$output" = "archive" ]
  run history_expiry_checker_level "archive"
  [ "$output" = "INFO" ]
}

@test "Nethermind Hybrid without History.Pruning is missing" {
  local execstart
  execstart="/usr/local/bin/nethermind/nethermind --Pruning.Mode=Hybrid --Pruning.FullPruningTrigger=VolumeFreeSpace --Pruning.FullPruningThresholdMb=300000"
  run history_expiry_status "Nethermind" "$execstart"
  [ "$output" = "missing" ]
  run history_expiry_suggested_flags "Nethermind"
  [[ "$output" == *"--History.Pruning=Rolling"* ]]
}

@test "Nethermind History.Pruning=Rolling is recommended" {
  run history_expiry_status "Nethermind" "--Pruning.Mode=Hybrid --History.Pruning=Rolling"
  [ "$output" = "recommended" ]
}

@test "Nethermind History.Pruning=UseAncientBarriers is recommended" {
  run history_expiry_status "Nethermind" "--History.Pruning=UseAncientBarriers"
  [ "$output" = "recommended" ]
}

@test "missing flags map to WARN not FAIL" {
  run history_expiry_checker_level "missing"
  [ "$output" = "WARN" ]
}

@test "Nethermind Pruning.Mode=None is archive" {
  run history_expiry_status "Nethermind" "--Pruning.Mode=None --Sync.FastSync=false"
  [ "$output" = "archive" ]
}

@test "Besu SNAP is recommended" {
  run history_expiry_status "Besu" "/usr/local/bin/besu/bin/besu --sync-mode=SNAP --data-storage-format=BONSAI"
  [ "$output" = "recommended" ]
}

@test "Besu Forest is archive" {
  run history_expiry_status "Besu" "--sync-mode=FULL --data-storage-format=FOREST"
  [ "$output" = "archive" ]
}

@test "Reth --full is recommended" {
  run history_expiry_status "Reth" "/usr/local/bin/reth node --full --chain mainnet"
  [ "$output" = "recommended" ]
}

@test "Reth with no prune profile is archive" {
  run history_expiry_status "Reth" "/usr/local/bin/reth node --chain mainnet --http"
  [ "$output" = "archive" ]
}

@test "Erigon prune.mode=minimal is recommended" {
  run history_expiry_status "Erigon" "/usr/local/bin/erigon --prune.mode=minimal --externalcl"
  [ "$output" = "recommended" ]
}

@test "Erigon prune.mode=archive is archive" {
  run history_expiry_status "Erigon" "--prune.mode=archive --prune.distance=0"
  [ "$output" = "archive" ]
}

@test "Erigon-Caplin archive flags are INFO caplin_archive" {
  local execstart
  execstart="/usr/local/bin/erigon --prune.mode=minimal --caplin.states-archive=true --caplin.blocks-archive=true"
  run history_expiry_status "Erigon-Caplin" "$execstart"
  [ "$output" = "caplin_archive" ]
  run history_expiry_checker_level "caplin_archive"
  [ "$output" = "INFO" ]
}

@test "Ethrex is unsupported INFO not WARN" {
  run history_expiry_status "Ethrex" "/usr/local/bin/ethrex --syncmode snap"
  [ "$output" = "unsupported" ]
  run history_expiry_checker_level "unsupported"
  [ "$output" = "INFO" ]
}

@test "checker never maps statuses to FAIL" {
  local status
  for status in recommended missing archive caplin_archive unsupported unknown no_el; do
    run history_expiry_checker_level "$status"
    [[ "$output" != "FAIL" ]]
  done
}

@test "evaluate_unit_text parses multiline ExecStart and prints Geth suggestion" {
  local unit
  unit=$(cat <<'EOF'
[Unit]
Description=Geth Execution Layer Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/geth \
    --mainnet \
    --state.scheme=path \
    --datadir=/var/lib/geth
EOF
)
  run history_expiry_evaluate_unit_text "$unit" ""
  [ "$status" -eq 0 ]
  [[ "$output" == $'missing\n'* ]]
  [[ "$output" == *"Detected EL: Geth"* ]]
  [[ "$output" == *"--history.chain=postmerge"* ]]
  [[ "$output" == *"Suggestions only"* ]]
  [[ "$output" == *"systemd units are not modified"* ]]
}

@test "evaluate_unit_text labels Caplin archive as INFO opt-in" {
  local unit
  unit=$(cat <<'EOF'
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET

[Service]
ExecStart=/usr/local/bin/erigon --prune.mode=minimal --caplin.states-archive=true
EOF
)
  run history_expiry_evaluate_unit_text "$unit" ""
  [ "$status" -eq 0 ]
  [[ "$output" == $'caplin_archive\n'* ]]
  [[ "$output" == *"Not a failure"* ]]
  [[ "$output" == *"--prune.mode=minimal"* ]]
}

@test "CLI --all prints per-client table without needing a unit" {
  run bash ./helpers/history_expiry_suggestions.sh --all --no-pause
  [ "$status" -eq 0 ]
  [[ "$output" == *"Geth"* ]]
  [[ "$output" == *"Nethermind"* ]]
  [[ "$output" == *"Besu"* ]]
  [[ "$output" == *"Reth"* ]]
  [[ "$output" == *"Erigon"* ]]
  [[ "$output" == *"Ethrex"* ]]
  [[ "$output" == *"--history.chain=postmerge"* ]]
  [[ "$output" == *"--History.Pruning=Rolling"* ]]
  [[ "$output" == *"SUGGESTIONS only"* ]]
}

@test "CLI --unit uses a mock execution.service path" {
  local unit
  unit=$(mktemp)
  cat > "$unit" <<'EOF'
[Unit]
Description=Reth Execution Layer Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/reth node --full --chain mainnet
EOF
  run bash ./helpers/history_expiry_suggestions.sh --unit "$unit" --no-pause
  [ "$status" -eq 0 ]
  [[ "$output" == *"Detected EL: Reth"* ]]
  [[ "$output" == *"already match"* ]]
  rm -f "$unit"
}
