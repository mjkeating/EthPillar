# SSV Operator (EthPillar)

SSV is a **validator-client alternative**, not Charon-style beacon middleware.
Selecting **SSV Operator** in the install VC menu installs `ssv-node.service`
(and `ssv-dkg.service`) instead of `validator.service`. The node talks to your
local execution WebSocket and beacon REST API directly.

## Layout

| Path | Role |
|------|------|
| `/var/lib/ssv/` | Shared datadir (`ssv` user, mode `700`) |
| `/var/lib/ssv/encrypted_private_key.json` | Operator RSA keystore |
| `/var/lib/ssv/password` | Keystore password |
| `/var/lib/ssv/config.yaml` | Node config |
| `/var/lib/ssv/db/` | Persistent DB (**slashing-critical** — back up) |
| `/var/lib/ssv/operator.yaml` | DKG config (same key + password; **relative** paths — `ssv-dkg` rejects absolute) |
| `/var/lib/ssv/output/` | DKG ceremony artifacts |
| `/usr/local/bin/ssvnode` | Node binary (built from source) |
| `/usr/local/bin/ssv-dkg` | DKG binary (GitHub release zip) |

Upstream does not publish `ssvnode` Linux binaries on GitHub. EthPillar clones
the release tag and runs `CGO_ENABLED=1 make build` (needs Go 1.26+ and
`build-essential`). That path works on amd64 and arm64 systemd hosts without
Docker. DKG still uses the published zip assets.

## Install

1. Install EL + CL as usual. SSV needs ETH1 over WebSocket on localhost `:8546`.
   Geth and ethrex already expose WS; Besu/Nethermind/Reth/Erigon enable localhost
   WS only when the install includes **SSV Operator** (`enable_ws`).
2. On the validator-mode prompt, choose **SSV Operator** (or pass `--with_ssv`).
3. Not available as the Charon/Web3Signer *second* signer prompt.
4. Mutually exclusive with Charon and Web3Signer.
5. **SSV-only host** (remote BN/EL): use **Validator Client Only** → **SSV Operator**, or migrate an existing systemd operator (below). Remote endpoints omit local `Wants=consensus/execution`.

## Migrate from existing systemd SSV

For a VM that already runs `ssvnode.service` / `ssvdkg.service` (or name variants
like `ssv_node`, `ssv.service`, or a *legacy* unit that happens to be named
`ssv-node` / `ssv-dkg` but is **not** already EthPillar-managed), EthPillar can
take over without reinstalling EL/CL:

```bash
ethpillar --migrate_ssv
# or:
PYTHONPATH=. python3 -m deploy.ssv_migrate plan
PYTHONPATH=. python3 -m deploy.ssv_migrate run          # enable only
PYTHONPATH=. python3 -m deploy.ssv_migrate run --start  # enable + start
```

What it does:

1. Discovers legacy node/DKG units under `/etc/systemd/system/` (skips units that
   already look like EthPillar `/var/lib/ssv` + `/usr/local/bin/ssvnode`)
2. Reads `ExecStart` / `WorkingDirectory` / `Environment` / `EnvironmentFile` to
   find config + binaries (also parses `run_ssv-dkg.sh` CLI flags)
3. Preserves legacy `config.yaml` knobs (rewrites only KeyStore/db/log paths) and
   full multi-endpoint `BeaconNodeAddr` / `ETH1Addr`
4. Installs EthPillar binaries, **stops** legacy for a consistent DB copy
5. If a legacy unit is already named `ssv-node` / `ssv-dkg`, **retires it first**
   (body kept as `*.service.bak`), then writes the EthPillar unit into the vacant
   path — never quarantines a file that already looks EthPillar-owned
6. Differently-named legacy units are retired after the EthPillar write
7. Only after verification succeeds: enable (and optionally start) EthPillar units
8. Reuses legacy binaries when they match GitHub LATEST; otherwise rebuilds

Existence probes for keystore / password / DB use `sudo test` so a mode-`750`
operator home is still visible to the TUI user. A missing legacy DB is a
**hard error** (pass `--allow-empty-db` only if intentional).

If EthPillar SSV is already installed and no legacy unit remains, migrate
short-circuits with “Already migrated”. A missing EthPillar `ssv-dkg.service`
is **repaired** when feasible (keystore + `ssv-dkg` binary present).

If `ETH1Addr` is local and EthPillar `execution.service` lacks WebSocket
(Besu/Nethermind/Reth/Erigon without `enable_ws`), migrate **fails with a clear
error** rather than rewriting the live EL unit mid-migrate. Enable WS on the EL
(e.g. reinstall EL together with a fresh SSV install, or regenerate the unit
with `enable_ws=True`), then re-run migrate.

**Rollback** (safe — EthPillar units are retired *before* legacy bak restore).
Legacy `ssv-dkg.service.bak` / `ssv-node.service.bak` are restored when their
**content** is not EthPillar-owned (baks created by this rollback are skipped).

```bash
PYTHONPATH=. python3 -m deploy.ssv_migrate rollback [--start]
```

## Secrets

On first start without existing files, the node can generate
`encrypted_private_key.json` + `password`. Import existing keys via
**EthPillar → SSV Operator → Import operator keystore** (stops services first).

**Back up both files on a separate device.** Losing either file means losing
the operator with no recovery path. The same files are required by `ssv-dkg`.

## Uninstall

Full-node uninstall (`uninstall.sh`) always **retires** `ssv-node` / `ssv-dkg`
(stop → disable → `*.service.bak` → daemon-reload → verify absent) and removes
binaries/`ssv` user.

Wiping `/var/lib/ssv` is a **separate opt-in** (strong confirm, **default No**).
If you choose to wipe:

1. You are prompted to **back up first** (**default Yes**): keystore, password,
   `config.yaml`, `operator.yaml`, and optionally `db/`
2. Backup goes to a timestamped root-only directory outside the datadir
   (e.g. `~/ssv-backup-<timestamp>/` or `/root/ssv-backup-<timestamp>/`,
   mode `700` / files `600`); the path is printed
3. If the backup fails, the wipe is **aborted** (datadir kept; units already retired)

## Ports / firewall

| Port | Proto | Purpose | UFW |
|------|-------|---------|-----|
| 13001 | TCP | P2P | Open (from config.yaml when present) |
| 12001 | UDP | Discovery | Open |
| 3030 | TCP | DKG | Open when DKG enabled |
| 15000 | TCP | Prometheus metrics | **Denied** (scrape via localhost) |
| 16000 | TCP | Health/API | **Denied** (localhost only) |

EthPillar → Security → UFW → **SSV: Allow P2P…** reads ports from
`/var/lib/ssv/config.yaml` (and DKG yaml) with `SSV_*` env as fallback, opens
P2P + DKG, and **denies** metrics/API. Override ports with `SSV_*` in `env` /
`.env.overrides` before install.

## Monitoring

When Prometheus/Grafana are installed, EthPillar provisions:

- Scrape job `ssv` → `127.0.0.1:15000/metrics` with a static `pod` label
- **SSV Operational** Grafana dashboard (from `ssvlabs/ssv-stack`)

## DKG

After registering the operator at [app.ssv.network](https://app.ssv.network/),
set `operatorID` in `/var/lib/ssv/operator.yaml` (or `SSV_OPERATOR_ID` before
install) and start `ssv-dkg`.

## Next steps after install

1. Back up keystore + password + `db/`
2. Register operator (web app)
3. Open firewall ports
4. `sudo systemctl start ssv-node`
5. Optional: `sudo systemctl start ssv-dkg`

See also: [SSV node setup](https://docs.ssv.network/operators/operator-node/node-setup/),
[DKG](https://docs.ssv.network/operators/operator-node/setup-sidecars/enabling-dkg/start-dkg-node/),
[Monitoring](https://docs.ssv.network/operators/operator-node/monitoring/).
