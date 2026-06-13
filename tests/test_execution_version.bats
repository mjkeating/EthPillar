#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"
}

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
