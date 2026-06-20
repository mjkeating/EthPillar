#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"
  export COMMAND_LOG=$(mktemp)
  export CONSENSUS_SERVICE_FILE=$(mktemp)
  export VALIDATOR_SERVICE_FILE=$(mktemp)
  export CL_IP_ADDRESS=127.0.0.1
  export CL_REST_PORT=5052

  sudo() {
    echo "sudo $@" >> "$COMMAND_LOG"
  }
  export -f sudo

  > "$COMMAND_LOG"
}

teardown() {
  rm -f "$COMMAND_LOG" "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE"
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