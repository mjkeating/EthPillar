# AGENTS.md

EthPillar is a Linux Bash TUI (`ethpillar.sh`) plus a Python deploy layer (`deploy/`) for installing and managing Ethereum EL/CL/VC/MEV clients via systemd.

## Install & run
- Install the tool: `install.sh` (see README.md)
- Launch: `ethpillar` (symlink to `ethpillar.sh`)
- Production target: Linux + systemd. Dev/test on Windows/macOS/Linux: use Docker (below)

## Node deployment
- TUI install flow goes through `deploy/`; see `deploy/DEPLOY_FLOW.md`
- Client modules: `deploy/{client}.py`, contracts in `deploy/protocols.py`

## Repo map
| Path | Role |
|------|------|
| `ethpillar.sh` | TUI entry |
| `functions.sh` | Shared Bash helpers, venv bootstrap |
| `env` / `.env.overrides` | Runtime config (overrides gitignored) |
| `config.py` | Checkpoint-sync URLs, MEV relays |
| `deploy/` | Install orchestration + systemd generation |
| `plugins/` | Optional add-ons (CSM, Aztec, monitoring, etc.) |
| `tests/` | pytest, bats, Docker integration |

## Testing
See `tests/README.md` for instructions on how to test via docker and what test suites exist.
- Systemd output: golden tests in `tests/test_service_generators.py`

## Conventions
- Match existing Bash/Python patterns; minimal diffs, avoid code duplication, provide function docstrings and type-hinting
- Client modules must satisfy `deploy/protocols.py` (`tests/test_client_module_contracts.py`)
- Never commit secrets (`.env.overrides`, keys, tokens)
