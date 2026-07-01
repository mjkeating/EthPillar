#!/usr/bin/env bats
#
# tests/test_integration_user.bats
#
# Static checks for non-root integration test execution.
#

setup() {
  cd "$BATS_TEST_DIRNAME/.."
}

@test "run_test.sh drops root before running integration runner" {
  grep -Fq 'ETHPILLAR_INTEGRATION_PRIVS_DROPPED' tests/integration/run_test.sh
  grep -Fq 'runuser -u "${INTEGRATION_USER}"' tests/integration/run_test.sh
  grep -Fq 'setup_integration_user.sh' tests/integration/run_test.sh
}

@test "run_inside_docker.py rejects root execution" {
  grep -Fq 'require_non_root_integration_runner' tests/integration/run_inside_docker.py
  grep -Fq 'must not run as root' tests/integration/run_inside_docker.py
}

@test "integration user setup grants passwordless sudo" {
  grep -Fq 'NOPASSWD:ALL' tests/integration/docker/setup_integration_user.sh
}

@test "run_inside_docker uses sudo systemctl for non-root integration user" {
  grep -Fq '_systemctl_cmd' tests/integration/run_inside_docker.py
  grep -Fq '_pid1_is_systemd' tests/integration/run_inside_docker.py
}

@test "manual docker entry drops to integration user" {
  test -x tests/integration/docker/manual_shell.sh
  test -x tests/integration/docker/start_manual_container.sh
  grep -Fq 'runuser -u "${INTEGRATION_USER}"' tests/integration/docker/manual_shell.sh
  grep -Fq 'manual_shell.sh' tests/README.md
}

@test "orchestrator passes host uid to containers" {
  grep -Fq 'ETHPILLAR_INTEGRATION_UID' tests/integration/run_docker_tests.py
  grep -Fq 'integration_container_env_flags' tests/integration/run_docker_tests.py
}
