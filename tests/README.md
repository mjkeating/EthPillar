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

## 5. Local CI smoke (act)

Simulate GitHub Actions workflows locally with [act](https://github.com/nektos/act). Requires Docker Desktop and `winget install nektos.act`.

**Cache permission smoke** exercises the integration cache restore/save path using tiny fixtures in `tests/integration/act-smoke/` (never touches your real `tests/integration/cache` or `checkpoint_cache`).

```powershell
# PowerShell (Bypass required on Windows default execution policy)
pwsh -ExecutionPolicy Bypass -File .\tests\integration\act-smoke\run-act-integration.ps1 -Run -Job cache-smoke
```

```bash
# Linux / macOS (pwsh) — Bypass not needed
pwsh -File ./tests/integration/act-smoke/run-act-integration.ps1 -Run -Job cache-smoke
```

Optional: set `$env:GITHUB_TOKEN` to avoid API rate limits when running the full integration matrix (`-Job integration`).

## Test Structure

- `tests/test_orchestrator.py`: Logic for role flags and CSM overrides.
- `tests/test_service_generators.py`: Golden-string tests for systemd units (generators live in each `deploy/{client}.py` module).
- `tests/test_client_module_contracts.py`: Verifies each client module exports required functions per `deploy/protocols.py`.
- `tests/test_extract_and_install.py`: Unit tests for `extract_and_install` and deploy-module adoption.
- `tests/test_update_extract.bats`: Static checks that update scripts use the unified extract CLI.
- `tests/test_integration_user.bats`: Static checks for non-root integration test execution.
- `tests/test_install_node.bats`: Validation logic for the install wrapper.
- `tests/test_ethpillar_installnode.bats`: TUI routing and role selection logic.
- `tests/run_unit_tests.sh`: Bootstraps Python deps via production `functions.sh`, then runs pytest in the project venv.
- `tests/integration/run_test.sh`: Bootstraps Python deps via production `functions.sh`, then runs the test runner.
- `tests/integration/run_inside_docker.py`: The core engine for containerized installation testing.


## Manual Testing

Manual installs should mirror production: **non-root user + passwordless sudo** (same as the integration matrix). `docker exec` starts as root; use `manual_shell.sh` to drop privileges before running the TUI or deploy scripts.

Build the image once (from the project root):

```bash
docker build -t ethpillar-test -f tests/integration/Dockerfile.test .
```

**Linux / WSL / Git Bash** — start the container (UID/GID match the host checkout for bind-mount writes):

```bash
bash tests/integration/docker/start_manual_container.sh
docker exec -it ep-manual bash /ethpillar/tests/integration/docker/manual_shell.sh
```

**PowerShell** — equivalent `docker run` (use your WSL uid/gid if not `1000`):

```powershell
docker run -d --name ep-manual --privileged --cgroupns=host --tmpfs /run --tmpfs /run/lock `
  -e ETHPILLAR_INTEGRATION_UID=1000 -e ETHPILLAR_INTEGRATION_GID=1000 `
  -v "${PWD}:/ethpillar" ethpillar-test

docker exec -it ep-manual bash /ethpillar/tests/integration/docker/manual_shell.sh
```

You should see:

```
[manual] Dropping root; shell as ubuntu (uid=1000)
```

Then run the TUI or deploy tooling:

```bash
./ethpillar.sh
# or: python3 deploy/deploy-node.py ...
```

Use `sudo systemctl …` for service control (same as on a production node). Plain `systemctl` as the test user will fail with “Failed to connect to bus”.

If you already opened a root shell (`docker exec -it ep-manual bash`), drop privileges from inside the container:

```bash
bash /ethpillar/tests/integration/docker/manual_shell.sh
```

When finished:

```bash
docker rm -f ep-manual
```
