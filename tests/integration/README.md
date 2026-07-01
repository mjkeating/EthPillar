# EthPillar Integration Tests

This folder contains the integration testing suite for EthPillar. These tests verify the deployment scripts by running them inside a Docker environment that uses **systemd as PID 1** — the same init system used in production.

## Overview

The integration tests simulate various validator configurations (Solo Staking, Lido CSM, VC Only, etc.) across different client combinations and networks. They ensure that:
- Binaries are correctly downloaded and installed.
- Systemd service files are syntactically valid (`daemon-reload` passes).
- Services can be **actually started and stopped** via `systemctl`.
- Installed client versions are parsed correctly via `functions.sh` (same path as the update menus).
- Users and directories are properly set up.
- All configurations work as expected without manual intervention.

> **Why systemd in Docker?** Without a real systemd PID 1, we can only verify that service *files* are generated — not that they are correct. The systemd-enabled container lets us run `systemctl start <service>` and verify the service reaches the `active` state, catching bugs like wrong binary paths, bad flags, or permission issues that file generation tests would miss.

## Requirements

- **Docker**: The tests must be run on a system with Docker installed and the daemon running.
- **Linux host or WSL2**: The systemd-in-Docker pattern requires Linux kernel cgroups. On Windows, use WSL2 with Docker Desktop configured to use the WSL2 backend.
- **Python 3**: For running the verification logic within the containers.
- **PowerShell (Windows)** or **Bash (Linux/WSL)**: For orchestrating the test batches.
- **`GITHUB_TOKEN`**: A read-only GitHub classic PAT (no scopes required for public repos).
  Each install resolves release metadata via `api.github.com`. Without a token the
  unauthenticated limit (~60 requests/hour per IP) is exhausted quickly — especially
  after running the live release tests. Export the token in your shell before starting:

```powershell
# User env var is fine, but open a NEW terminal after saving it in Windows Settings.
# The .ps1 wrapper reads Windows env and forwards it into WSL (WSL does not inherit it).
./tests/integration/run_docker_tests.ps1
```

If you run from an already-open PowerShell session before the variable existed, set it for that session too:

```powershell
$env:GITHUB_TOKEN = "ghp_your_token_here"
./tests/integration/run_docker_tests.ps1
```

## How Systemd-in-Docker Works

The `Dockerfile.test` uses `ubuntu:24.04` and sets systemd as the `CMD`. The test orchestrator:
1. Starts each container with `docker run -d` (detached) plus the required flags:
   - `--privileged` — needed for cgroup management
   - `--cgroupns=host` — shares the host cgroup namespace
   - `--tmpfs /run --tmpfs /run/lock` — systemd runtime directories
2. Waits 3 seconds for systemd to initialize.
3. Runs the test via `docker exec <container> bash /ethpillar/tests/integration/run_test.sh ...`. `docker exec` starts as **root**, but `run_test.sh` immediately creates the **`epstaker`** user (UID/GID matched to the host checkout owner), grants **passwordless sudo**, and re-execs itself via `runuser` so deploy/update scripts run unprivileged — the same shape as production. Python deps bootstrap into `/tmp/ethpillar-integration-venv` (not the bind-mounted `.venv`).
4. Cleans up the container with `docker rm -f` in a `finally` block.

## Project Structure

- `Dockerfile.test`: Ubuntu 24.04 with systemd as PID 1. Only container infrastructure is pre-installed (systemd, sudo, python3, bats). EthPillar runtime apt packages are installed by `setup_node()` during deploy.
- `run_docker_tests.py`: Main orchestrator — builds the image, runs the test matrix with live UI, and generates HTML reports.
- `run_docker_tests.ps1`: (Windows) Thin WSL wrapper that invokes `run_docker_tests.sh`.
- `run_docker_tests.sh`: (Linux/WSL) Ensures host `rich` is installed, then invokes `run_docker_tests.py`.
- `run_test.sh`: Bootstrap wrapper — drops root to `epstaker` (passwordless sudo), sources `functions.sh`, then execs the test runner.
- `run_inside_docker.py`: Executes inside each container to run the deployment and verify artifacts via `systemctl`. Does not install Python deps itself.
- `check_client_versions.sh`: After deploy (and after update tests), verifies installed versions parse via `getExecutionCurrentVersion` / `getClVcCurrentVersion` and match `release_info … LATEST` (same comparison as the update menu’s “already on latest” path).
- `sitecustomize.py`: Caches release **binaries** only (revalidated with `HEAD` before reuse). GitHub API / release metadata always hits the network.
- `cache/`: Persistent cache for validated release binaries.

## Running Tests

### Windows (PowerShell)
```powershell
./run_docker_tests.ps1
```

### Linux / WSL (Bash)
```bash
./run_docker_tests.sh
```

## Install smoke tests

Lightweight checks for `install.sh` — separate from the full client deployment matrix.
Two isolated `docker run --rm` containers (no systemd required):

| Case | How it is simulated | Expected install root |
|------|----------------------|------------------------|
| Curl one-liner | `bash < /ethpillar/install.sh` from `/tmp` as `epstaker` | `~/git/ethpillar` under epstaker's home |
| Clone then install | copy repo to `/opt/ethpillar-custom`, run `install.sh` as `epstaker` | `/opt/ethpillar-custom` |

Both use the bind-mounted workspace as the install source
(`ETHPILLAR_INSTALL_COPY_FROM=/ethpillar` on the curl path) so PR branches are tested
without pulling from GitHub.

```bash
bash tests/integration/run_install_smoke_tests.sh
```

`install_smoke/verify_install.sh` asserts:

- `/usr/local/bin/ethpillar` symlink targets the expected `ethpillar.sh`
- `ethpillar` is on `PATH`
- `.venv` exists with runtime Python deps (`dotenv`, `requests`, `tqdm`)
- `functions.sh` resolves `BASE_DIR` to the expected repo
- the install user (`epstaker`) is in `systemd-journal` after `install.sh` runs
- clone-path test: symlink must **not** point at `~/git/ethpillar` under epstaker's home

Non-interactive installs skip the “Press RETURN” prompt when stdin is not a TTY or
`ETHPILLAR_INSTALL_NONINTERACTIVE=1` is set.

## Manual Testing in the Docker Container

Manual testing uses the same **non-root + passwordless sudo** shape as the integration matrix. Do not run `./ethpillar.sh` or deploy scripts as root inside the container.

```bash
# Build the image
docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .

# Start a long-lived container with systemd (bash helper sets host UID/GID)
bash tests/integration/docker/start_manual_container.sh
# Or set ETHPILLAR_TEST_IMAGE=ethpillar-rebuild if you used that tag

# Shell as the test user (not root)
docker exec -it ep-manual bash /ethpillar/tests/integration/docker/manual_shell.sh
```

Inside the container (prompt should be the test user, e.g. `ubuntu`):

```bash
./ethpillar.sh
# or bootstrap deps then deploy:
source /ethpillar/functions.sh
python3 /ethpillar/deploy/deploy-node.py --skip_prompts true \
  --network SEPOLIA --install_config "Custom Setup" \
  --ec Nethermind --cc Grandine --vc Lighthouse

sudo systemctl status consensus
sudo systemctl status execution
journalctl -fu consensus --no-pager -n 50
```

Pass `ETHPILLAR_INTEGRATION_UID` / `ETHPILLAR_INTEGRATION_GID` on `docker run` so the in-container user can write the bind-mounted repo (especially on WSL). `start_manual_container.sh` sets these from `id -u` / `id -g`.

Stop and remove when done:

```bash
docker rm -f ep-manual
```

## Results

Test results are saved in the `results/` directory. Each run creates a timestamped folder containing:
- Individual log files for each test case.
- `index.html`: A summary report showing the status (PASS/FAIL) of all test cases.

## Caching

Release **binaries** may be served from `cache/` after a live `HEAD` check confirms the URL is still valid and the file size matches. API/metadata requests are never cached, so release URL resolution is exercised on every run. Delete `cache/` to force a full re-download of all binaries.

After each integration matrix run, ``run_docker_tests.py`` prunes ``*.bin`` and
``extracted_*.tar`` files that were **not** read or written during that run (tracked
in ``cache/.accessed_this_run.log``). Reset and prune run inside a short Docker
container as root so WSL hosts never need ``sudo`` for root-owned cache files.
Old client releases fall out of the cache automatically when the matrix moves on.
Set ``ETHPILLAR_SKIP_BINARY_CACHE_PRUNE=1`` to keep everything.

### Checkpoint sync (SEPOLIA + HOODI)

Before the test matrix runs, `warm_checkpoint_cache.py` prefetches Beacon checkpoint API
responses from ethpandaops. Entries expire after **2 hours**; a fresh run re-downloads only
when stale.

**Cache location:** WSL/Windows runs store the cache at
``~/.cache/ethpillar/checkpoint_cache`` (not under the repo). Docker Desktop's repo bind
mount breaks ``mkdir`` on ``tests/integration/checkpoint_cache``; the orchestrator mounts
the sidecar cache into each container at ``/ethpillar/tests/integration/checkpoint_cache``.
Native Linux runs without ``ETHPILLAR_CHECKPOINT_CACHE_DIR`` use the in-repo path
(``tests/integration/checkpoint_cache/``, gitignored).

Each test container mounts that cache read-only and `run_test.sh` starts a local proxy on
`http://127.0.0.1:19595`. Consensus clients use that URL instead of hitting the WAN on
every install (~190 MB per HOODI client otherwise). Slightly stale checkpoints are fine:
clients backfill via P2P after checkpoint sync, and integration only waits for services to
reach `active` and bind ports.

Delete ``~/.cache/ethpillar/checkpoint_cache`` (WSL) or ``tests/integration/checkpoint_cache``
(Linux) to force a full re-warm. If warming fails, tests fall back to upstream ethpandaops
URLs automatically.

### Environment file

Integration tests write configuration to `/tmp/ethpillar-integration.env` inside the
container and set `ETHPILLAR_ENV_FILE` for `deploy-node.py`, `functions.sh`, and the
update/switch test scripts. The repo-root `env` file on the host checkout is never
modified, including on Ctrl-C or `docker rm -f`.
