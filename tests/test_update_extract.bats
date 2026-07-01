#!/usr/bin/env bats
#
# tests/test_update_extract.bats
#
# Static checks that archive-based update scripts route extraction through
# deploy.common extract_and_install (shared with deploy modules and integration
# extract cache wrappers).
#

setup() {
  cd "$BATS_TEST_DIRNAME/.."
}

@test "update_execution.sh uses extract_and_install for archive clients" {
  grep -Fq 'extract_and_install "$FILENAME" "nethermind"' update_execution.sh
  grep -Fq 'extract_and_install "$FILENAME" "besu"' update_execution.sh
  grep -Fq 'extract_and_install "$FILENAME" "erigon"' update_execution.sh
  grep -Fq 'extract_and_install "$FILENAME" "geth"' update_execution.sh
  grep -Fq 'extract_and_install "$FILENAME" "reth"' update_execution.sh
}

@test "update_execution.sh does not inline tar extraction" {
  ! grep -q 'tar -xzvf' update_execution.sh
}

@test "update_consensus.sh uses extract_and_install for archive clients" {
  grep -Fq 'extract_and_install "$FILENAME" "lighthouse"' update_consensus.sh
  grep -Fq 'extract_and_install "$FILENAME" "lodestar"' update_consensus.sh
  grep -Fq 'extract_and_install "$FILENAME" "teku"' update_consensus.sh
  grep -Fq 'extract_and_install "$FILENAME" "nimbus"' update_consensus.sh
  grep -Fq 'binary-name "nimbus_beacon_node"' update_consensus.sh
}

@test "update_consensus.sh does not inline tar extraction for unified clients" {
  ! grep -q 'tar -xzvf' update_consensus.sh
}

@test "update_validator.sh uses extract_and_install for archive clients" {
  grep -Fq 'extract_and_install "$FILENAME" "lighthouse"' update_validator.sh
  grep -Fq 'extract_and_install "$FILENAME" "lodestar"' update_validator.sh
  grep -Fq 'extract_and_install "$FILENAME" "teku"' update_validator.sh
  grep -Fq 'extract_and_install "$FILENAME" "nimbus"' update_validator.sh
  grep -Fq 'binary-name "nimbus_validator_client"' update_validator.sh
}

@test "update_validator.sh does not inline tar extraction" {
  ! grep -q 'tar -xzvf' update_validator.sh
}

@test "update_mevboost.sh uses extract_and_install" {
  grep -Fq 'extract_and_install "$FILENAME" "mevboost"' update_mevboost.sh
  grep -Fq 'binary-name "mev-boost"' update_mevboost.sh
  ! grep -q 'tar -xzvf' update_mevboost.sh
}

@test "bare-binary update paths remain on install_system_binary" {
  grep -Fq 'install_system_binary' update_execution.sh
  grep -Fq 'Ethrex)' update_execution.sh
  grep -Fq 'install_system_binary' update_consensus.sh
  grep -Fq 'Prysm)' update_consensus.sh
  grep -Fq 'Grandine)' update_consensus.sh
}
