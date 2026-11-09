#!/usr/bin/env bats

# Version-mismatch and list behavior against tests/fixtures/known_failures.yaml.
# ETHPILLAR_KNOWN_FAILURES_PATH keeps these cases off the shipped registry.

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/integration/ethpillar_test_python.sh"
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/integration/known_version_mismatches.sh"
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  ETHPILLAR_TEST_PYTHON="$(ethpillar_test_python "$REPO_ROOT")"
  export ETHPILLAR_KNOWN_FAILURES_PATH="$BATS_TEST_DIRNAME/fixtures/known_failures.yaml"
}

@test "known_upstream_version_mismatch accepts the fixture pair" {
  run known_upstream_version_mismatch FixtureVM 9.9.1 9.9.2
  [ "$status" -eq 0 ]

  run known_upstream_version_mismatch fixturevm v9.9.1 v9.9.2
  [ "$status" -eq 0 ]
}

@test "known_upstream_version_mismatch rejects other fixture pairs" {
  run known_upstream_version_mismatch FixtureVM 9.9.1 9.9.3
  [ "$status" -ne 0 ]

  run known_upstream_version_mismatch FixtureVM 9.9.2 9.9.2
  [ "$status" -ne 0 ]

  run known_upstream_version_mismatch FixtureVM 9.9.0 9.9.2
  [ "$status" -ne 0 ]
}

@test "known_upstream_version_mismatch rejects other clients" {
  run known_upstream_version_mismatch OtherClient 9.9.1 9.9.2
  [ "$status" -ne 0 ]
}

@test "known_failures list prints the fixture registry" {
  run env PYTHONPATH="${REPO_ROOT}/tests/integration${PYTHONPATH:+:$PYTHONPATH}" \
    "$ETHPILLAR_TEST_PYTHON" "$BATS_TEST_DIRNAME/integration/known_failures.py" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"fixture-version-closed-unfixed"* ]]
  [[ "$output" == *"version_mismatch"* ]]
  [[ "$output" == *"fixture-log-substring-marker"* ]]
  [[ "$output" == *"fixture-log-regex-marker"* ]]
}
