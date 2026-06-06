# EthPillar Integration Tests

This folder contains the integration testing suite for EthPillar. These tests verify the deployment scripts by running them inside a Docker environment that uses **systemd as PID 1** — the same init system used in production.

## Overview

The integration tests simulate various validator configurations (Solo Staking, Lido CSM, VC Only, etc.) across different client combinations and networks. They ensure that:
- Binaries are correctly downloaded and installed.
- Systemd service files are syntactically valid (`daemon-reload` passes).
- Services can be **actually started and stopped** via `systemctl`.
- Users and directories are properly set up.
- All configurations work as expected without manual intervention.

> **Why systemd in Docker?** Without a real systemd PID 1, we can only verify that service *files* are generated — not that they are correct. The systemd-enabled container lets us run `systemctl start <service>` and verify the service reaches the `active` state, catching bugs like wrong binary paths, bad flags, or permission issues that file generation tests would miss.

## Requirements

- **Docker**: The tests must be run on a system with Docker installed and the daemon running.
- **Linux host or WSL2**: The systemd-in-Docker pattern requires Linux kernel cgroups. On Windows, use WSL2 with Docker Desktop configured to use the WSL2 backend.
- **Python 3**: For running the verification logic within the containers.
- **PowerShell (Windows)** or **Bash (Linux/WSL)**: For orchestrating the test batches.

## How Systemd-in-Docker Works

The `Dockerfile.test` uses `ubuntu:24.04` and sets systemd as the `CMD`. The test orchestrator:
1. Starts each container with `docker run -d` (detached) plus the required flags:
   - `--privileged` — needed for cgroup management
   - `--cgroupns=host` — shares the host cgroup namespace
   - `--tmpfs /run --tmpfs /run/lock` — systemd runtime directories
2. Waits 3 seconds for systemd to initialize.
3. Runs the test via `docker exec <container> python3 ...`.
4. Cleans up the container with `docker rm -f` in a `finally` block.

## Project Structure

- `Dockerfile.test`: Ubuntu 24.04 with systemd as PID 1.
- `run_docker_tests.ps1`: (Windows) Main script to run all test combinations in parallel batches.
- `run_docker_tests.sh`: (Linux/WSL) Main script to run all test combinations in parallel batches.
- `run_inside_docker.py`: Executes inside each container to run the deployment and verify artifacts via `systemctl`.
- `sitecustomize.py`: Caches remote HTTP(S) downloads (API metadata and release assets) to speed up repeated test runs and avoid rate limits.
- `cache/`: Persistent cache for GitHub API responses and release binaries.

## Running Tests

### Windows (PowerShell)
```powershell
./run_docker_tests.ps1
```

### Linux / WSL (Bash)
```bash
./run_docker_tests.sh
```

## Manual Testing in the Docker Container

To manually test inside the container (e.g. to debug a service file):

```bash
# Build the image
docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .

# Start a long-lived container with systemd
docker run -d --name ep-manual \
  --privileged --cgroupns=host \
  --tmpfs /run --tmpfs /run/lock \
  -v $(pwd):/ethpillar \
  ethpillar-rebuild

# Open a shell inside
docker exec -it ep-manual bash

# Inside: run a deployment, then check systemd
python3 /ethpillar/deploy/deploy-node.py --skip_prompts true \
  --network SEPOLIA --install_config "Custom Setup" \
  --ec Nethermind --cc Grandine --vc Lighthouse

systemctl status consensus
systemctl status execution
journalctl -fu consensus --no-pager -n 50

# Stop and remove when done
docker rm -f ep-manual
```

## Results

Test results are saved in the `results/` directory. Each run creates a timestamped folder containing:
- Individual log files for each test case.
- `index.html`: A summary report showing the status (PASS/FAIL) of all test cases.

## Caching

To avoid hitting GitHub API rate limits, the tests use a local cache. This cache is persistent by default (stored in the `cache/` directory) and is mapped into the Docker containers during test execution.
