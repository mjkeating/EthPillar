#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"
  export COMMAND_LOG=$(mktemp)
  export CONSENSUS_SERVICE_FILE=$(mktemp)
  export VALIDATOR_SERVICE_FILE=$(mktemp)
  export CHARON_SERVICE_FILE=$(mktemp)
  export CL_IP_ADDRESS=127.0.0.1
  export CL_REST_PORT=5052

  sudo() {
    echo "sudo $@" >> "$COMMAND_LOG"
  }
  export -f sudo

  > "$COMMAND_LOG"
}

teardown() {
  rm -f "$COMMAND_LOG" "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE" "$CHARON_SERVICE_FILE"
}

write_grandine_integrated_consensus() {
  cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/grandine --keystore-dir=/var/lib/grandine/validator_keys
EOF
}

write_prysm_validator_service() {
  local endpoint="${1:-http://127.0.0.1:5052}"
  cat > "$VALIDATOR_SERVICE_FILE" <<EOF
[Unit]
Description=Prysm Validator Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/prysm-validator --beacon-rest-api-provider=${endpoint}
EOF
}

write_charon_service() {
  local endpoint="${1:-http://127.0.0.1:5052}"
  local p2p_port="${2:-3610}"
  cat > "$CHARON_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/charon run --beacon-node-endpoints=${endpoint} --validator-api-address=127.0.0.1:3600 --p2p-tcp-address=0.0.0.0:${p2p_port} --builder-api
EOF
}

write_lighthouse_validator_service() {
  cat > "$VALIDATOR_SERVICE_FILE" <<EOF
[Unit]
Description=Lighthouse Validator Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/lighthouse validator_client --beacon-nodes=http://127.0.0.1:5052
EOF
}

write_lodestar_validator_service() {
  cat > "$VALIDATOR_SERVICE_FILE" <<EOF
[Unit]
Description=Lodestar Validator Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/lodestar validator --beaconNodes=http://127.0.0.1:5052
EOF
}

# ── getValidatorMode ─────────────────────────────────────────────────────────

@test "getValidatorMode returns none when no validator services exist" {
  rm -f "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE"
  export CONSENSUS_SERVICE_FILE="/nonexistent/consensus.service"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  run getValidatorMode
  [ "$status" -eq 0 ]
  [ "$output" = "none" ]
}

@test "getValidatorMode returns separate when validator.service exists" {
  write_prysm_validator_service
  run getValidatorMode
  [ "$output" = "separate" ]
}

@test "getValidatorMode returns integrated_grandine when keystore-dir is present" {
  write_grandine_integrated_consensus
  run getValidatorMode
  [ "$output" = "integrated_grandine" ]
}

# ── getValidatorClient ─────────────────────────────────────────────────────────

@test "getValidatorClient reads validator.service description" {
  write_prysm_validator_service
  run getValidatorClient
  [ "$output" = "Prysm" ]
}

@test "getValidatorClient detects Grandine integrated VC" {
  rm -f "$VALIDATOR_SERVICE_FILE"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  write_grandine_integrated_consensus
  run getValidatorClient
  [ "$output" = "Grandine" ]
}

# ── epbsTuiSupported ───────────────────────────────────────────────────────────

@test "epbsTuiSupported is true for Prysm VC" {
  write_prysm_validator_service
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  run epbsTuiSupported
  [ "$status" -eq 0 ]
}

@test "epbsTuiSupported is true for Lodestar VC" {
  write_lodestar_validator_service
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  run epbsTuiSupported
  [ "$status" -eq 0 ]
}

@test "epbsTuiSupported is false when Charon is installed (even with Lodestar VC)" {
  write_lodestar_validator_service
  write_charon_service
  run epbsTuiSupported
  [ "$status" -ne 0 ]
}

@test "epbsTuiSupported is false when no VC and no MEV are installed" {
  rm -f "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE" "$CHARON_SERVICE_FILE"
  export CONSENSUS_SERVICE_FILE="/nonexistent/consensus.service"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  export MEVBOOST_SERVICE_FILE="/nonexistent/mevboost.service"
  run epbsTuiSupported
  [ "$status" -ne 0 ]
}

@test "epbsTuiSupported is true for MEV without local VC (split LXC export)" {
  rm -f "$VALIDATOR_SERVICE_FILE" "$CHARON_SERVICE_FILE"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  export MEVBOOST_SERVICE_FILE
  MEVBOOST_SERVICE_FILE=$(mktemp)
  echo "[Service]" > "$MEVBOOST_SERVICE_FILE"
  run epbsTuiSupported
  status_rc=$status
  rm -f "$MEVBOOST_SERVICE_FILE"
  [ "$status_rc" -eq 0 ]
}

@test "epbsImportUnderValidator is true for Prysm VC without MEV" {
  write_prysm_validator_service
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  export MEVBOOST_SERVICE_FILE="/nonexistent/mevboost.service"
  run epbsImportUnderValidator
  [ "$status" -eq 0 ]
}

@test "epbsImportUnderValidator is false when MEV is installed" {
  write_prysm_validator_service
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  export MEVBOOST_SERVICE_FILE
  MEVBOOST_SERVICE_FILE=$(mktemp)
  echo "[Service]" > "$MEVBOOST_SERVICE_FILE"
  run epbsImportUnderValidator
  status_rc=$status
  rm -f "$MEVBOOST_SERVICE_FILE"
  [ "$status_rc" -ne 0 ]
}

@test "epbsImportUnderValidator is false when Charon is installed even with Prysm VC" {
  write_prysm_validator_service
  write_charon_service
  export MEVBOOST_SERVICE_FILE="/nonexistent/mevboost.service"
  run epbsImportUnderValidator
  [ "$status" -ne 0 ]
  run epbsImportUnderCharon
  [ "$status" -ne 0 ]
}

@test "epbsImportUnderCharon is false while charonEpbsSupported stub returns false" {
  write_lodestar_validator_service
  write_charon_service
  export MEVBOOST_SERVICE_FILE="/nonexistent/mevboost.service"
  run charonEpbsSupported
  [ "$status" -ne 0 ]
  run epbsImportUnderCharon
  [ "$status" -ne 0 ]
  # Charon owns the builder path: hide import entirely until Charon ePBS ships.
  run epbsImportUnderValidator
  [ "$status" -ne 0 ]
}

@test "epbsTuiSupported is false for Lighthouse VC" {
  write_lighthouse_validator_service
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  export MEVBOOST_SERVICE_FILE="/nonexistent/mevboost.service"
  run epbsTuiSupported
  [ "$status" -ne 0 ]
}

@test "epbsTuiSupported is false for Grandine integrated VC" {
  rm -f "$VALIDATOR_SERVICE_FILE" "$CHARON_SERVICE_FILE"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  export MEVBOOST_SERVICE_FILE="/nonexistent/mevboost.service"
  write_grandine_integrated_consensus
  run epbsTuiSupported
  [ "$status" -ne 0 ]
}

# ── getBeaconNodeEndpoint ──────────────────────────────────────────────────────

@test "getBeaconNodeEndpoint uses environment defaults" {
  rm -f "$CONSENSUS_SERVICE_FILE"
  export CONSENSUS_SERVICE_FILE="/nonexistent/consensus.service"
  run getBeaconNodeEndpoint
  [ "$output" = "http://127.0.0.1:5052" ]
}

@test "getBeaconNodeEndpoint scrapes http-port from consensus.service" {
  cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/lighthouse bn --http-port=16052 --http-address=10.1.2.3
EOF
  export CL_REST_PORT=""
  run getBeaconNodeEndpoint
  [ "$output" = "http://10.1.2.3:16052" ]
}

@test "getBeaconNodeEndpoint scrapes rest-api-port from teku consensus.service" {
  cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/teku --rest-api-port=16099
EOF
  unset CL_REST_PORT
  run getBeaconNodeEndpoint
  [ "$output" = "http://127.0.0.1:16099" ]
}

# ── stopValidatorService / startValidatorService ───────────────────────────────

@test "stopValidatorService stops validator in separate mode" {
  write_prysm_validator_service
  stopValidatorService
  run cat "$COMMAND_LOG"
  [[ "$output" == *"sudo systemctl stop validator"* ]]
  [[ "$output" != *"sudo systemctl stop consensus"* ]]
}

@test "stopValidatorService stops consensus in integrated_grandine mode" {
  write_grandine_integrated_consensus
  stopValidatorService
  run cat "$COMMAND_LOG"
  [[ "$output" == *"sudo systemctl stop consensus"* ]]
  [[ "$output" != *"sudo systemctl stop validator"* ]]
}

@test "stopValidatorService is a no-op in none mode" {
  rm -f "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE"
  export CONSENSUS_SERVICE_FILE="/nonexistent/consensus.service"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  stopValidatorService
  run cat "$COMMAND_LOG"
  [ -z "$output" ]
}

@test "startValidatorService starts validator with daemon-reload in separate mode" {
  write_prysm_validator_service
  startValidatorService
  run cat "$COMMAND_LOG"
  [[ "$output" == *"sudo systemctl daemon-reload"* ]]
  [[ "$output" == *"sudo systemctl start validator"* ]]
}

@test "startValidatorService starts consensus in integrated_grandine mode" {
  write_grandine_integrated_consensus
  startValidatorService
  run cat "$COMMAND_LOG"
  [[ "$output" == *"sudo systemctl start consensus"* ]]
  [[ "$output" != *"sudo systemctl start validator"* ]]
}

# ── isCharonEnabled / patchValidatorBeaconEndpoint ─────────────────────────────

@test "isCharonEnabled is false when charon.service is missing" {
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  run isCharonEnabled
  [ "$status" -eq 1 ]
}

@test "isCharonEnabled is true when charon.service exists" {
  write_charon_service
  run isCharonEnabled
  [ "$status" -eq 0 ]
}

@test "ensureCharonBeforeValidator (re)starts Charon when installed" {
  write_charon_service
  ensureCharonBeforeValidator
  run cat "$COMMAND_LOG"
  # restart, not try-restart: try-restart is a no-op for a stopped unit.
  [[ "$output" == *"sudo systemctl restart charon"* ]]
  [[ "$output" != *"try-restart"* ]]
}

@test "ensureCharonBeforeValidator does nothing without Charon" {
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  ensureCharonBeforeValidator
  run cat "$COMMAND_LOG"
  [[ "$output" != *"charon"* ]]
}

@test "getCharonP2pPort reads p2p-tcp-address from charon.service" {
  write_charon_service "http://127.0.0.1:5052" 3812
  run getCharonP2pPort
  [ "$status" -eq 0 ]
  [ "$output" = "3812" ]
}

@test "getCharonP2pPort is empty when charon.service is missing" {
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  run getCharonP2pPort
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "patchValidatorBeaconEndpoint updates Charon upstream and leaves VC on :3600" {
  write_prysm_validator_service "http://127.0.0.1:3600"
  write_charon_service "http://127.0.0.1:5052"
  export CL_REST_PORT=16052

  run patchValidatorBeaconEndpoint
  [ "$status" -eq 0 ]
  grep -q -- "--beacon-node-endpoints=http://127.0.0.1:16052" "$CHARON_SERVICE_FILE"
  grep -q -- "--beacon-rest-api-provider=http://127.0.0.1:3600" "$VALIDATOR_SERVICE_FILE"
}

@test "patchValidatorBeaconEndpoint updates VC when Charon is not installed" {
  write_prysm_validator_service "http://127.0.0.1:5052"
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  export CL_REST_PORT=16052

  run patchValidatorBeaconEndpoint
  [ "$status" -eq 0 ]
  grep -q -- "--beacon-rest-api-provider=http://127.0.0.1:16052" "$VALIDATOR_SERVICE_FILE"
}