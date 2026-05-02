# EthPillar Test Suite

This directory contains the comprehensive test suite for EthPillar, including unit tests, shell tests, and containerized integration tests.

## Prerequisites

- **Docker Desktop**: Required for all tests to ensure a consistent Ubuntu 24.04 environment.
- **PowerShell**: Required for running the integration test harness on Windows.

## 1. Build the Test Environment

Before running any tests, build the unified test image:

```bash
# From the project root
docker build -t ethpillar-test -f tests/integration/Dockerfile.test .
```

## 2. Running Unit Tests (Pytest)

These tests verify the Python orchestration logic, flag resolution, and systemd service generation.

```bash
docker run --rm -v "${PWD}:/ethpillar" ethpillar-test python3 -m pytest tests/ -v
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
cd tests
.\run_docker_tests.ps1
```

### On Linux/macOS (Manual)
You can run individual integration tests directly:
```bash
docker run --rm -v "${PWD}:/ethpillar" ethpillar-test python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --combo Lighthouse-Reth --config "Solo Staking Node" --network SEPOLIA
```

## Test Structure

- `tests/test_orchestrator.py`: Logic for role flags and CSM overrides.
- `tests/test_service_generators.py`: Golden-string tests for systemd units.
- `tests/test_install_node.bats`: Validation logic for the install wrapper.
- `tests/test_ethpillar_installnode.bats`: TUI routing and role selection logic.
- `tests/integration/run_inside_docker.py`: The core engine for containerized installation testing.


## Manual Testing

Run a fresh container
```bash
# From the project root
docker run -it --rm -v "${PWD}:/ethpillar" ethpillar-test bash
```

In the container:
- install some basic tools:
```bash
apt-get update && apt-get install -y whiptail bc jq curl iproute2 kmod nano
```
- set terminal type:
```bash
export TERM=xterm
```
- run ethpillar:
```bash
bash ./ethpillar.sh
```
