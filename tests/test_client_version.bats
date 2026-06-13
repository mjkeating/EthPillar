#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"
  TEST_BIN_DIR=$(mktemp -d)
  export EXEC_SERVICE_FILE=$(mktemp)
  export CONSENSUS_SERVICE_FILE=$(mktemp)
  export VALIDATOR_SERVICE_FILE=$(mktemp)
}

teardown() {
  rm -rf "$TEST_BIN_DIR"
  rm -f "$EXEC_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE"
}

write_stub_binary() {
  local path="$1"
  shift
  cat > "$path" <<EOF
#!/bin/bash
$*
EOF
  chmod +x "$path"
}

# ── parse_execution_client_version ───────────────────────────────────────────

@test "parse_execution_client_version ignores trailing toolchain versions" {
  run parse_execution_client_version Ethrex $'ethrex/v16.0.0/rust/1.91.0'
  [ "$status" -eq 0 ]
  [ "$output" = "16.0.0" ]
}

@test "parse_execution_client_version ignores leading JDK version for Besu" {
  run parse_execution_client_version Besu $'openjdk version "25.0.1"\nbesu 25.3.0'
  [ "$status" -eq 0 ]
  [ "$output" = "25.3.0" ]
}

@test "parse_execution_client_version parses geth version output" {
  run parse_execution_client_version Geth 'Geth Version: 1.14.12-stable-abc123'
  [ "$status" -eq 0 ]
  [ "$output" = "1.14.12" ]
}

@test "parse_execution_client_version parses reth version output" {
  run parse_execution_client_version Reth 'reth-ethereum-client 1.9.0 (abcdef)'
  [ "$status" -eq 0 ]
  [ "$output" = "1.9.0" ]
}

@test "parse_execution_client_version parses nethermind version output" {
  run parse_execution_client_version Nethermind 'Nethermind v1.32.0+abc'
  [ "$status" -eq 0 ]
  [ "$output" = "1.32.0" ]
}

@test "parse_execution_client_version parses erigon version output" {
  run parse_execution_client_version Erigon 'erigon version 3.0.12-alpha1'
  [ "$status" -eq 0 ]
  [ "$output" = "3.0.12" ]
}

@test "parse_execution_client_version parses ethrex binary version output" {
  run parse_execution_client_version Ethrex 'ethrex 16.0.0'
  [ "$status" -eq 0 ]
  [ "$output" = "16.0.0" ]
}

@test "parse_execution_client_version returns empty for unknown client" {
  run parse_execution_client_version Unknown 'client 1.2.3'
  [ "$status" -eq 1 ]
  [ "$output" = "" ]
}

# ── get_execution_version_output ─────────────────────────────────────────────

@test "get_execution_version_output uses geth version subcommand" {
  local stub="$TEST_BIN_DIR/geth"
  write_stub_binary "$stub" '[[ "$1" == "version" ]] && echo "Geth Version: 1.14.0"'
  run get_execution_version_output "$stub" Geth
  [ "$status" -eq 0 ]
  [[ "$output" == *"Geth Version: 1.14.0"* ]]
}

@test "get_execution_version_output uses --version for other clients" {
  local stub="$TEST_BIN_DIR/ethrex"
  write_stub_binary "$stub" '[[ "$1" == "--version" ]] && echo "ethrex 16.0.0"'
  run get_execution_version_output "$stub" Ethrex
  [ "$status" -eq 0 ]
  [ "$output" = "ethrex 16.0.0" ]
}

# ── getExecutionCurrentVersion ───────────────────────────────────────────────

@test "getExecutionCurrentVersion reads ethrex from execution service stub" {
  local stub="$TEST_BIN_DIR/ethrex"
  write_stub_binary "$stub" '[[ "$1" == "--version" ]] && echo "ethrex 16.0.0 (rustc 1.91.0)"'
  cat <<EOF > "$EXEC_SERVICE_FILE"
ExecStart=$stub
EOF
  EL=Ethrex
  getExecutionCurrentVersion
  [ "$VERSION" = "16.0.0" ]
}

@test "getExecutionCurrentVersion reads besu from execution service stub" {
  local stub="$TEST_BIN_DIR/besu"
  write_stub_binary "$stub" '[[ "$1" == "--version" ]] && printf "%s\n%s\n" "openjdk version \"25.0.1\"" "besu 25.3.0"'
  cat <<EOF > "$EXEC_SERVICE_FILE"
ExecStart=$stub
EOF
  EL=Besu
  getExecutionCurrentVersion
  [ "$VERSION" = "25.3.0" ]
}

# ── getClVcCurrentVersion ────────────────────────────────────────────────────

@test "getClVcCurrentVersion reads lighthouse from consensus service stub" {
  local stub="$TEST_BIN_DIR/lighthouse"
  write_stub_binary "$stub" 'echo "Lighthouse v5.2.1-abc"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lighthouse
  [ "$VERSION" = "v5.2.1" ]
}

@test "getClVcCurrentVersion falls back to validator service for vc-only nimbus" {
  local stub="$TEST_BIN_DIR/nimbus_validator_client"
  write_stub_binary "$stub" 'echo "Nimbus v24.11.0"'
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Nimbus
  [ "$VERSION" = "v24.11.0" ]
}

@test "getClVcCurrentVersion accepts explicit client override" {
  local stub="$TEST_BIN_DIR/prysm-validator"
  write_stub_binary "$stub" 'echo "Prysm v5.0.0"'
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$stub
EOF
  CLIENT=Lighthouse
  getClVcCurrentVersion Prysm
  [ "$VERSION" = "v5.0.0" ]
}

@test "getClVcCurrentVersion normalizes grandine version prefix" {
  local stub="$TEST_BIN_DIR/grandine"
  write_stub_binary "$stub" 'echo "grandine 2.0.4"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Grandine
  [ "$VERSION" = "v2.0.4" ]
}
