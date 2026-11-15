#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"
  TEST_BIN_DIR=$(mktemp -d)
  export EXEC_SERVICE_FILE=$(mktemp)
  export CONSENSUS_SERVICE_FILE=$(mktemp)
  export VALIDATOR_SERVICE_FILE=$(mktemp)
  export CHARON_SERVICE_FILE=$(mktemp)
  export MEVBOOST_SERVICE_FILE=$(mktemp)
  export SSV_NODE_SERVICE_FILE=$(mktemp)
  export SSV_DKG_SERVICE_FILE=$(mktemp)
}

teardown() {
  rm -rf "$TEST_BIN_DIR"
  rm -f "$EXEC_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE" \
      "$CHARON_SERVICE_FILE" "$MEVBOOST_SERVICE_FILE" \
      "$SSV_NODE_SERVICE_FILE" "$SSV_DKG_SERVICE_FILE"
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

@test "parse_execution_client_version preserves uppercase Besu RC suffix" {
  run parse_execution_client_version Besu 'besu/v25.10.0-RC2/linux-x86_64/openjdk-java-25'
  [ "$status" -eq 0 ]
  [ "$output" = "25.10.0-RC2" ]
}

@test "parse_execution_client_version parses geth version output" {
  run parse_execution_client_version Geth $'Geth\nVersion: 1.14.12-stable-abc123'
  [ "$status" -eq 0 ]
  [ "$output" = "1.14.12" ]
}

@test "parse_execution_client_version parses geth version on one line" {
  run parse_execution_client_version Geth 'Geth Version: 1.14.12-stable-abc123'
  [ "$status" -eq 0 ]
  [ "$output" = "1.14.12" ]
}

@test "parse_execution_client_commit parses geth commit from channel suffix" {
  run parse_execution_client_commit Geth $'Geth\nVersion: 1.14.12-stable-abc1234'
  [ "$status" -eq 0 ]
  [ "$output" = "abc1234" ]
}

@test "parse_execution_client_version parses reth version output" {
  run parse_execution_client_version Reth 'reth-ethereum-client 1.9.0 (abcdef)'
  [ "$status" -eq 0 ]
  [ "$output" = "1.9.0" ]
}

@test "parse_execution_client_commit parses reth commit in parentheses" {
  run parse_execution_client_commit Reth 'reth-ethereum-client 1.9.0 (abcdef1)'
  [ "$status" -eq 0 ]
  [ "$output" = "abcdef1" ]
}

@test "parse_execution_client_commit parses reth Commit SHA line" {
  run parse_execution_client_commit Reth $'reth Version: 1.0.0-rc.2\nCommit SHA: d786b45'
  [ "$status" -eq 0 ]
  [ "$output" = "d786b45" ]
}

@test "parse_execution_client_commit parses erigon trailing hash" {
  run parse_execution_client_commit Erigon 'erigon version 3.4.0-rc.5-ac5e71d8'
  [ "$status" -eq 0 ]
  [ "$output" = "ac5e71d8" ]
}

@test "parse_execution_client_commit parses ethrex HEAD hash" {
  run parse_execution_client_commit Ethrex 'ethrex ethrex/v19.0.0-HEAD-f88b98eccf3de017bc2b91bd69d177f0e31a3e40/x86_64-unknown-linux-gnu/rustc-v1.91.0'
  [ "$status" -eq 0 ]
  [ "$output" = "f88b98eccf3de017bc2b91bd69d177f0e31a3e40" ]
}

@test "parse_execution_client_version parses nethermind version output" {
  run parse_execution_client_version Nethermind $'Version:     1.38.0+c07a4d65\nCommit:      c07a4d65'
  [ "$status" -eq 0 ]
  [ "$output" = "1.38.0" ]
}

@test "parse_execution_client_commit prefers Nethermind Commit line" {
  run parse_execution_client_commit Nethermind $'Version:     1.38.0+deadbeef\nCommit:      c07a4d65'
  [ "$status" -eq 0 ]
  [ "$output" = "c07a4d65" ]
}

@test "parse_execution_client_version parses nethermind version on one line" {
  run parse_execution_client_version Nethermind 'Nethermind v1.32.0+abc'
  [ "$status" -eq 0 ]
  [ "$output" = "1.32.0" ]
}

@test "parse_execution_client_version preserves erigon prerelease suffix" {
  run parse_execution_client_version Erigon 'erigon version 3.0.12-alpha1'
  [ "$status" -eq 0 ]
  [ "$output" = "3.0.12-alpha1" ]
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
  write_stub_binary "$stub" '[[ "$1" == "version" ]] && printf "%s\n%s\n" "Geth" "Version: 1.14.0-stable"'
  run get_execution_version_output "$stub" Geth
  [ "$status" -eq 0 ]
  [[ "$output" == *"Version: 1.14.0-stable"* ]]
}

@test "getExecutionCurrentVersion reads geth from execution service stub" {
  local stub="$TEST_BIN_DIR/geth"
  write_stub_binary "$stub" '[[ "$1" == "version" ]] && printf "%s\n%s\n" "Geth" "Version: 1.17.3-stable-aabbccd"'
  cat <<EOF > "$EXEC_SERVICE_FILE"
ExecStart=$stub
EOF
  EL=Geth
  getExecutionCurrentVersion
  [ "$VERSION" = "1.17.3" ]
  [ "$INSTALLED_COMMIT" = "aabbccd" ]
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
  write_stub_binary "$stub" 'echo "Lighthouse v5.2.1-abc1234"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lighthouse cl
  [ "$VERSION" = "v5.2.1" ]
  [ "$INSTALLED_COMMIT" = "abc1234" ]
}

@test "getClVcCurrentVersion reads lighthouse vc from validator when consensus is grandine" {
  local grandine="$TEST_BIN_DIR/grandine"
  local lighthouse="$TEST_BIN_DIR/lighthouse"
  write_stub_binary "$grandine" 'echo "grandine 2.0.4"'
  write_stub_binary "$lighthouse" 'echo "Lighthouse v8.1.3-def5678"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$grandine
EOF
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$lighthouse vc --network=sepolia
EOF
  getClVcCurrentVersion Lighthouse vc
  [ "$VERSION" = "v8.1.3" ]
  [ "$INSTALLED_COMMIT" = "def5678" ]
}

@test "getClVcCurrentVersion cl role ignores validator service for lighthouse" {
  local cl_stub="$TEST_BIN_DIR/lighthouse-bn"
  local vc_stub="$TEST_BIN_DIR/lighthouse-vc"
  write_stub_binary "$cl_stub" 'echo "Lighthouse v5.0.0-aaa1111"'
  write_stub_binary "$vc_stub" 'echo "Lighthouse v9.9.9-bbb2222"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$cl_stub
EOF
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$vc_stub vc --network=mainnet
EOF
  getClVcCurrentVersion Lighthouse cl
  [ "$VERSION" = "v5.0.0" ]
  [ "$INSTALLED_COMMIT" = "aaa1111" ]
}

@test "getClVcCurrentVersion reads lodestar version and commit hash" {
  local stub="$TEST_BIN_DIR/lodestar"
  write_stub_binary "$stub" 'echo "* Version: v1.45.0/668ea9d"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lodestar cl
  [ "$VERSION" = "v1.45.0" ]
  [ "$INSTALLED_COMMIT" = "668ea9d" ]
}

@test "getClVcCurrentVersion reads official lodestar v1.48.0/c7dc2b0" {
  local stub="$TEST_BIN_DIR/lodestar"
  write_stub_binary "$stub" 'echo "* Version: v1.48.0/c7dc2b0"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lodestar cl
  [ "$VERSION" = "v1.48.0" ]
  [ "$INSTALLED_COMMIT" = "c7dc2b0" ]
}

@test "getClVcCurrentVersion ignores extra /hex outside the Lodestar Version line" {
  local stub="$TEST_BIN_DIR/lodestar"
  write_stub_binary "$stub" 'printf "%s\n" "Unpacking Lodestar binary from /tmp/lodestar-v1.48.0-linux-amd64/deadbeef/lodestar" "* Version: v1.48.0/c7dc2b0" "* by ChainSafe Systems, 2018-2026"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lodestar cl
  [ "$VERSION" = "v1.48.0" ]
  [ "$INSTALLED_COMMIT" = "c7dc2b0" ]
}

@test "getClVcCurrentVersion reads lodestar v1.8.0/stable/a4b29cf" {
  local stub="$TEST_BIN_DIR/lodestar"
  write_stub_binary "$stub" 'echo "* Version: v1.8.0/stable/a4b29cf"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lodestar cl
  [ "$VERSION" = "v1.8.0" ]
  [ "$INSTALLED_COMMIT" = "a4b29cf" ]
}

@test "get_lodestar_version_output invokes binary with cwd=/tmp" {
  local stub="$TEST_BIN_DIR/lodestar"
  cat > "$stub" <<'EOF'
#!/bin/bash
printf 'cwd=%s\n' "$(pwd)"
EOF
  chmod +x "$stub"
  run get_lodestar_version_output "$stub"
  [ "$status" -eq 0 ]
  [[ "$output" == "cwd=/tmp" ]]
}

@test "getClVcCurrentVersion uses /tmp cwd so baked Lodestar commit wins over repo git" {
  local stub="$TEST_BIN_DIR/lodestar"
  cat > "$stub" <<'EOF'
#!/bin/bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "* Version: v1.48.0/cursor/cli-upgrade-skip-when-latest-0415/14901a2"
else
  echo "* Version: v1.48.0/c7dc2b0"
fi
EOF
  chmod +x "$stub"
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lodestar cl
  [ "$VERSION" = "v1.48.0" ]
  [ "$INSTALLED_COMMIT" = "c7dc2b0" ]
}

@test "getClVcCurrentVersion ignores EthPillar branch SHA on Lodestar Version line" {
  local stub="$TEST_BIN_DIR/lodestar"
  write_stub_binary "$stub" 'echo "* Version: v1.48.0/cursor/cli-upgrade-skip-when-latest-0415/14901a2"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lodestar cl
  [ "$VERSION" = "v1.48.0" ]
  [ -z "$INSTALLED_COMMIT" ]
  TAG=v1.48.0
  TAG_COMMIT=c7dc2b0b3b715635fb9b616bf178137ad64f7bba
  version_matches_latest
}

@test "parse_lodestar_installed_commit rejects last-hex branch metadata" {
  run parse_lodestar_installed_commit "* Version: v1.48.0/cursor/cli-upgrade-skip-when-latest-0415/14901a2" "v1.48.0"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  run parse_lodestar_installed_commit "* Version: v1.48.0/c7dc2b0" "v1.48.0"
  [ "$output" = "c7dc2b0" ]
  run parse_lodestar_installed_commit "* Version: v1.8.0/stable/a4b29cf" "v1.8.0"
  [ "$output" = "a4b29cf" ]
}

@test "getClVcCurrentVersion preserves lodestar prerelease when present" {
  local stub="$TEST_BIN_DIR/lodestar"
  write_stub_binary "$stub" 'echo "* Version: v1.45.0-rc.0/668ea9d"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lodestar cl
  [ "$VERSION" = "v1.45.0-rc.0" ]
  [ "$INSTALLED_COMMIT" = "668ea9d" ]
}

@test "getClVcCurrentVersion preserves uppercase Teku RC suffix" {
  local stub="$TEST_BIN_DIR/teku"
  write_stub_binary "$stub" 'echo "teku/v22.9.1-RC1/linux-x86_64/openjdk-java-25"'
  mkdir -p "$TEST_BIN_DIR/teku-home/bin"
  mv "$stub" "$TEST_BIN_DIR/teku-home/bin/teku"
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$TEST_BIN_DIR/teku-home/bin/teku
EOF
  getClVcCurrentVersion Teku cl
  [ "$VERSION" = "v22.9.1-RC1" ]
}

@test "getClVcCurrentVersion parses lighthouse commit after prerelease suffix" {
  local stub="$TEST_BIN_DIR/lighthouse"
  write_stub_binary "$stub" 'echo "Lighthouse v8.0.0-rc.2-b59feb0"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lighthouse cl
  [ "$VERSION" = "v8.0.0-rc.2" ]
  [ "$INSTALLED_COMMIT" = "b59feb0" ]
}

@test "getClVcCurrentVersion reads vc-only nimbus from validator service stub" {
  local stub="$TEST_BIN_DIR/nimbus_validator_client"
  write_stub_binary "$stub" 'echo "Nimbus v24.11.0"'
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Nimbus vc
  [ "$VERSION" = "v24.11.0" ]
  [ -z "$INSTALLED_COMMIT" ]
}

@test "getClVcCurrentVersion parses nimbus trailing commit hash" {
  local stub="$TEST_BIN_DIR/nimbus_beacon_node"
  write_stub_binary "$stub" 'echo "Nimbus beacon node v0.6.6-00aedddf"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Nimbus cl
  [ "$VERSION" = "v0.6.6" ]
  [ "$INSTALLED_COMMIT" = "00aedddf" ]
}

@test "getClVcCurrentVersion parses prysm commit after slash" {
  local stub="$TEST_BIN_DIR/prysm-beacon-chain"
  write_stub_binary "$stub" 'echo "beacon-chain version Prysm/v7.1.2-rc.0/7950a249266a692551e5a910adb9a82a02c92040. Built at: 2025-12-22"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Prysm cl
  [ "$VERSION" = "v7.1.2-rc.0" ]
  [ "$INSTALLED_COMMIT" = "7950a249266a692551e5a910adb9a82a02c92040" ]
}

@test "getClVcCurrentVersion accepts explicit client override" {
  local stub="$TEST_BIN_DIR/prysm-validator"
  write_stub_binary "$stub" 'echo "Prysm v5.0.0"'
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$stub
EOF
  CLIENT=Lighthouse
  getClVcCurrentVersion Prysm vc
  [ "$VERSION" = "v5.0.0" ]
}

@test "getClVcCurrentVersion normalizes grandine version prefix" {
  local stub="$TEST_BIN_DIR/grandine"
  write_stub_binary "$stub" 'echo "grandine 2.0.4"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Grandine cl
  [ "$VERSION" = "v2.0.4" ]
}

# ── parse_charon_version / parse_charon_commit ───────────────────────────────

@test "parse_charon_version keeps v1.10.3 and does not collapse to 0.3" {
  run parse_charon_version 'v1.10.3 [git_commit_hash=e60c838,git_commit_time=2026-06-24T09:43:48Z]'
  [ "$status" -eq 0 ]
  [ "$output" = "v1.10.3" ]
}

@test "parse_charon_commit reads git_commit_hash" {
  run parse_charon_commit 'v1.10.3 [git_commit_hash=e60c838,git_commit_time=2026-06-24T09:43:48Z]'
  [ "$status" -eq 0 ]
  [ "$output" = "e60c838" ]
}

@test "parse_charon_version ignores charon --version help/error text" {
  run parse_charon_version $'Usage:\n  charon [command]\n01:53:40.998 ERRO unknown flag: --version'
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "getCharonCurrentVersion reads charon from service stub" {
  local stub="$TEST_BIN_DIR/charon"
  write_stub_binary "$stub" '[[ "$1" == "version" ]] && echo "v1.10.3 [git_commit_hash=e60c838,git_commit_time=2026-06-24T09:43:48Z]"'
  cat <<EOF > "$CHARON_SERVICE_FILE"
ExecStart=$stub run
EOF
  getCharonCurrentVersion
  [ "$VERSION" = "v1.10.3" ]
  [ "$INSTALLED_COMMIT" = "e60c838" ]
}

# ── parse_mevboost_version / getMevboostCurrentVersion ───────────────────────

@test "parse_mevboost_version keeps 1.8.0 and does not collapse to 8.0" {
  run parse_mevboost_version 'mev-boost version v1.8.0'
  [ "$status" -eq 0 ]
  [ "$output" = "1.8.0" ]
}

@test "parse_mevboost_version reads bare x.y.z when no v prefix" {
  run parse_mevboost_version 'mev-boost 1.11'
  [ "$status" -eq 0 ]
  [ "$output" = "1.11" ]
}

@test "getMevboostCurrentVersion reads mev-boost from service stub" {
  local stub="$TEST_BIN_DIR/mev-boost"
  write_stub_binary "$stub" 'echo "mev-boost version v1.8.0"'
  cat <<EOF > "$MEVBOOST_SERVICE_FILE"
ExecStart=$stub
EOF
  getMevboostCurrentVersion
  [ "$VERSION" = "1.8.0" ]
}

@test "format_version_label includes short commit when present" {
  run format_version_label "v1.45.0" "668ea9dea24189d9"
  [ "$status" -eq 0 ]
  [ "$output" = "1.45.0 (668ea9d)" ]
}

# ── version_matches_latest ───────────────────────────────────────────────────

@test "version_matches_latest matches equal semver without commits" {
  VERSION=v1.45.0
  TAG=v1.45.0
  INSTALLED_COMMIT=
  TAG_COMMIT=
  version_matches_latest
}

@test "version_matches_latest mismatches different semver" {
  ! version_matches_latest "v1.45.0-rc.0" "v1.45.0" "" ""
}

@test "version_matches_latest matches commit prefix either way" {
  version_matches_latest "v1.45.0" "v1.45.0" "668ea9d" "668ea9dea24189d9be99940acd923e8920e75bf6"
  version_matches_latest "1.45.0" "v1.45.0" "668ea9dea24189d9be99940acd923e8920e75bf6" "668ea9d"
}

@test "version_matches_latest matches official lodestar short commit vs tag peel" {
  version_matches_latest "v1.48.0" "v1.48.0" "c7dc2b0" "c7dc2b0b3b715635fb9b616bf178137ad64f7bba"
}

@test "version_matches_latest mismatches same semver different commits" {
  ! version_matches_latest "v1.45.0" "v1.45.0" "668ea9d" "6051fa3335c3dadf3f99292a8fe345ccd69121f7"
}

@test "version_matches_latest falls back to semver when either commit missing" {
  version_matches_latest "v1.45.0" "v1.45.0" "668ea9d" ""
  version_matches_latest "v1.45.0" "v1.45.0" "" "6051fa3"
}

# ── SSV node / DKG version probes ────────────────────────────────────────────

@test "getSsvCurrentVersion parses ssvnode version output" {
  local stub="$TEST_BIN_DIR/ssvnode"
  write_stub_binary "$stub" 'echo "v2.5.1"'
  cat <<EOF > "$SSV_NODE_SERVICE_FILE"
ExecStart=$stub start-node
EOF
  getSsvCurrentVersion
  [ "$VERSION" = "v2.5.1" ]
}

@test "getSsvDkgCurrentVersion parses ssv-dkg version output" {
  local stub="$TEST_BIN_DIR/ssv-dkg"
  write_stub_binary "$stub" 'echo "ssv-dkg version v3.1.1"'
  cat <<EOF > "$SSV_DKG_SERVICE_FILE"
ExecStart=$stub start-operator
EOF
  getSsvDkgCurrentVersion
  [ "$VERSION" = "v3.1.1" ]
}
