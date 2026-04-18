# EthPillar Integration Tests

This folder contains the integration testing suite for EthPillar. These tests verify the deployment scripts by running them inside a controlled Docker environment.

## Overview

The integration tests simulate various validator configurations (Solo Staking, Lido CSM, VC Only, etc.) across different client combinations and networks. They ensure that:
- Binaries are correctly downloaded and installed.
- Systemd services are created and configured.
- Users and directories are properly set up.
- All configurations work as expected without manual intervention.

## Requirements

- **Docker**: The tests must be run on a system with Docker installed and the daemon running.
- **Python 3**: For running the verification logic within the containers.
- **PowerShell (Windows)** or **Bash (Linux/WSL)**: For orchestrating the test batches.

## Project Structure

- `Dockerfile.test`: The base image used for running the tests. It mimics a clean Ubuntu environment.
- `run_docker_tests.ps1`: (Windows) Main script to run all test combinations in parallel batches.
- `run_docker_tests.sh`: (Linux/WSL) Main script to run all test combinations in parallel batches.
- `run_inside_docker.py`: The script that executes inside each container to perform the actual deployment and verify artifacts.
- `sitecustomize.py`: A helper script that provides GitHub API caching to avoid rate limits during testing.
- `cache/`: A directory storing cached GitHub API responses and release binaries.

## Running Tests

### Windows (PowerShell)
```powershell
./run_docker_tests.ps1
```

### Linux / WSL (Bash)
```bash
./run_docker_tests.sh
```

## Results

Test results are saved in the `results/` directory. Each run creates a timestamped folder containing:
- Individual log files for each test case.
- `index.html`: A summary report showing the status (PASS/FAIL) of all test cases.

## Caching

To avoid hitting GitHub API rate limits, the tests use a local cache. This cache is persistent by default (stored in the `cache/` directory) and is mapped into the Docker containers during test execution.
