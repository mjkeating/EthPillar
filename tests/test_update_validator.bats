#!/usr/bin/env bats

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  export ROUTING_LOG=$(mktemp)
  export CONSENSUS_SERVICE_FILE=$(mktemp)
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
}

teardown() {
  rm -f "$ROUTING_LOG" "$CONSENSUS_SERVICE_FILE"
  if [[ -f update_consensus.sh.batsbak ]]; then
    mv update_consensus.sh.batsbak update_consensus.sh
  fi
}

@test "update_validator none mode fails when no validator is installed" {
  rm -f "$CONSENSUS_SERVICE_FILE"
  export CONSENSUS_SERVICE_FILE="/nonexistent/consensus.service"
  run bash -c 'whiptail() { return 0; }; export -f whiptail; ./update_validator.sh'
  [ "$status" -ne 0 ]
  [[ "$output" == *"No validator client"* ]]
}

@test "update_validator integrated_grandine routes to update_consensus.sh" {
  cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/grandine --keystore-dir=/var/lib/grandine/validator_keys
EOF

  cp update_consensus.sh update_consensus.sh.batsbak
  cat > update_consensus.sh <<EOF
#!/bin/bash
echo "integrated_routed" > "$ROUTING_LOG"
exit 0
EOF
  chmod +x update_consensus.sh

  run bash ./update_validator.sh
  [ "$status" -eq 0 ]
  [ "$(cat "$ROUTING_LOG")" = "integrated_routed" ]
}

@test "update_validator separate mode does not route to update_consensus.sh" {
  rm -f "$ROUTING_LOG"
  export VALIDATOR_SERVICE_FILE=$(mktemp)
  cat > "$VALIDATOR_SERVICE_FILE" <<EOF
Description=Prysm Validator Client service for MAINNET
EOF
  rm -f "$CONSENSUS_SERVICE_FILE"
  export CONSENSUS_SERVICE_FILE="/nonexistent/consensus.service"

  cp update_consensus.sh update_consensus.sh.batsbak
  cat > update_consensus.sh <<EOF
#!/bin/bash
echo "should_not_run" > "$ROUTING_LOG"
exit 99
EOF
  chmod +x update_consensus.sh

  # Cancel at the interactive whiptail menu (separate path must not exec consensus updater).
  run bash -c 'whiptail() { return 1; }; export -f whiptail; ./update_validator.sh'
  [ ! -f "$ROUTING_LOG" ]
}