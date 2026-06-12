# EthPillar Test Suite

This directory contains the comprehensive test suite for EthPillar, including unit tests, shell tests, and containerized integration tests.

## Prerequisites

- **Docker**: Required for all tests to ensure a consistent Ubuntu 24.04 environment.

## 1. Build the Test Environment

Before running any tests, build the unified test image:

```bash
# From the project root
docker build -t ethpillar-test -f tests/integration/Dockerfile.test .
```

## 2. Running Unit Tests (Pytest)

These tests verify the Python orchestration logic, flag resolution, and systemd service generation.

```bash
docker run --rm -v "${PWD}:/ethpillar" ethpillar-test bash /ethpillar/tests/run_unit_tests.sh tests/ -v
```

Live release-info tests (GitHub + geth.ethereum.org) are **skipped by default** in the unit run. They verify that `get_client_release_info()` resolves real download URLs for LATEST and for older tags — the same path used when picking a non-latest version in the update menus.

**Requires `GITHUB_TOKEN`** (read-only public repo access is sufficient).

1. Create a classic PAT: [github.com/settings/tokens](https://github.com/settings/tokens) → *Generate new token (classic)* → no scopes needed for public release metadata (or enable `public_repo`).
2. Run with the token in your shell:

```powershell
# PowerShell
$env:GITHUB_TOKEN = "ghp_your_token_here"
docker run --rm -e GITHUB_TOKEN -v "${PWD}:/ethpillar" ethpillar-test bash /ethpillar/tests/run_live_release_tests.sh
```

```bash
# bash
export GITHUB_TOKEN=ghp_your_token_here
docker run --rm -e GITHUB_TOKEN -v "${PWD}:/ethpillar" ethpillar-test bash /ethpillar/tests/run_live_release_tests.sh
```

## 3. Running Shell Tests (Bats)

These tests verify the Bash TUI logic in `ethpillar.sh` and the wrapper logic in `install-node.sh`.

```bash
docker run --rm -v "${PWD}:/ethpillar" ethpillar-test bats tests/
```

## 4. Running Integration Tests (Docker)

These tests perform end-to-end installations of various client combinations and roles in a sandbox container.

### On Windows (PowerShell)
```powershell
pwsh -ExecutionPolicy Bypass -File .\tests\integration\run_docker_tests.ps1
```

### On Linux/macOS (Manual)
You can run the full integration test suite directly:
```bash
bash tests/integration/run_docker_tests.sh
```

## Test Structure

- `tests/test_orchestrator.py`: Logic for role flags and CSM overrides.
- `tests/test_service_generators.py`: Golden-string tests for systemd units.
- `tests/test_install_node.bats`: Validation logic for the install wrapper.
- `tests/test_ethpillar_installnode.bats`: TUI routing and role selection logic.
- `tests/run_unit_tests.sh`: Bootstraps Python deps via production `functions.sh`, then runs pytest in the project venv.
- `tests/integration/run_test.sh`: Bootstraps Python deps via production `functions.sh`, then runs the test runner.
- `tests/integration/run_inside_docker.py`: The core engine for containerized installation testing.


## Manual Testing

Run a fresh container with systemd support:
```bash
# From the project root
docker run -d --name ep-manual --privileged --cgroupns=host --tmpfs /run --tmpfs /run/lock -v "${PWD}:/ethpillar" ethpillar-test
```

Enter the running container:
```bash
docker exec -it ep-manual bash
```

In the container, simply run the TUI:
```bash
./ethpillar.sh
```

When finished, clean up the container:
```bash
docker rm -f ep-manual
```
