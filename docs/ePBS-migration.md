# ePBS / Gloas MEV migration

Gloas (the consensus-layer half of [Glamsterdam](https://docs.ethstaker.org/upgrades/glamsterdam-features/)) moves builder relay configuration **off MEV-Boost and onto the validator client**. Until that fork, proposals still go through the local MEV-Boost sidecar.

EthPillar follows EthStaker’s two-step cutover so you do not drop MEV too early.

Most operators only need [Solo node (everything on one host)](#solo-node-everything-on-one-host). Read the Charon or split-host sections only if they apply to you.

---

## For node operators

This section is the TUI only. You do not need to run Python yourself.

### The two steps (all setups)

1. **Before the Gloas fork** — get relays onto the post-Gloas builder path. Keep MEV-Boost running. The beacon node still talks to local MEV-Boost.
2. **After the Gloas fork** — stop MEV-Boost and remove the beacon-node setting that pointed at the local sidecar (`127.0.0.1:18550`).

Do **not** run the after-fork step until Gloas is live on your network. Doing it early means the beacon node no longer talks to MEV-Boost, and most validator clients cannot fetch relays themselves yet.

---

### Solo node (everything on one host)

Use this when execution, consensus, MEV-Boost, and a **solo** validator client all run on the same machine (no Obol Charon).

#### Open the menu

**MEV-Boost → ePBS migration**

That item appears when the local validator fully supports migration (**Prysm**, **Lodestar** v1.47.0+, or integrated **Erigon-Caplin** v3.7.1+). Lighthouse, Teku, Nimbus, and Grandine do not get the TUI entry. The menu is shown for Caplin even when the installed binary is older; Prepare then explains that it did not write a builder list.

| Menu item | When to use it |
|-----------|----------------|
| Before Gloas Fork — Apply Relays to VC | Before the Gloas fork |
| After Gloas Fork — Complete ePBS migration | After the Gloas fork |
| Show current ePBS status | Anytime (read-only) |

#### What you see

**Before Gloas Fork** and **After Gloas Fork** use the same four screens. Nothing is written until you say yes on the confirm screen.

1. **Preview (dry-run).** A scrollable textbox. It lists your client, what *would* change, warnings, and which services would need a restart. The last line is **`Dry-run (no files written).`** Press OK. Disk is unchanged.
2. **Confirm.** Yes/no. Before-fork: *Write these VC changes now?* (MEV-Boost stays running). After-fork: *Stop MEV-Boost and remove BN sidecar flags now?* (cutting over early can miss proposals). **No** or Esc returns to the ePBS menu with no changes.
3. **Applied.** If you confirmed, EthPillar copies the old files next to the originals, writes the new config, then shows a second textbox titled **`… — applied`**.
4. **Restart?** Only if something actually changed. Example: *Restart now so the new flags take effect?* **No** leaves the new config on disk; it takes effect the next time you restart that client from the usual menus. After Complete, the applied textbox also shows how to roll back (restore `*.bak.epbs.*` and `systemctl enable --now mevboost`).

**Show current ePBS status** is one textbox: which clients you have, whether relays are already on the validator, and whether the beacon node still points at local MEV-Boost. No confirm, no writes.

#### What each step does

**Before the Gloas fork**

| Your validator | What EthPillar does |
|----------------|---------------------|
| **Prysm** (v7.2.0+) | Writes each MEV-Boost relay URL into Prysm’s proposer settings as a builder (`default_config.builder.builders`). That list is what opts the validator into relay registration before Gloas and what Prysm calls after Gloas. Removes the deprecated `--enable-builder` flag. Restarts the validator if you agree. **Does not** stop MEV-Boost. |
| **Lodestar** (v1.47.0+) | Writes `--builder.urls` and `--builder.minBid` on the validator. Older Lodestar builds skip this so the client can still start. **Does not** stop MEV-Boost. |
| **Erigon-Caplin** (v3.7.1+) | Writes each MEV-Boost relay URL into `/var/lib/erigon/caplin-builders.json` (`builders[].url`, `max_execution_payment` `"0"`). Keeps `--caplin.mev-relay-url` pointed at local MEV-Boost. Older Erigon builds skip this so the node can still start. **Does not** stop MEV-Boost. v3.7.1 already schedules Sepolia's 200M gas limit; EthPillar does not set one. |
| **Lighthouse, Teku, Nimbus, Grandine** | Not offered in the TUI. |

After this step, the beacon node still uses local MEV-Boost. Pre-fork blocks keep working as they do today.

If an older EthPillar already wrote `builder.relays` (and `--enable-builder`), run the before-fork step again. Prysm v7.2.0 ignores `relays`. Complete stays refused until `builders` is present.

**Sepolia gas limit (Prysm v7.2.0).** This Prysm release does not include the 200M gas-limit schedule, so Gloas proposals default to 60M. If you want 200M, add `"gas_limit": "200000000"` yourself under `default_config` (or under a key in `proposer_config`). EthPillar does not write that value, and it does not set it on mainnet or Hoodi. `--suggested-gas-limit` only affects pre-Gloas mev-boost registrations.

**After the Gloas fork** (after the first step succeeded)

- Stops and disables MEV-Boost (the service file stays on disk).
- Removes the beacon-node setting that pointed at local MEV-Boost (`127.0.0.1:18550`). Other builder URLs are left alone.
- Leaves any validator relay config from the first step in place.

If you skipped the first step, **Complete is refused** so you do not drop MEV-Boost with no VC relay replacement.

If you ran Complete too early: restore `consensus.service` from the newest `consensus.service.bak.epbs.*`, then `sudo systemctl enable --now mevboost` and restart consensus (`sudo systemctl daemon-reload && sudo systemctl restart consensus`).

#### Safety

- Run the before-fork step while MEV-Boost is healthy.
- Run the after-fork step only after Gloas on that network.
- Read the preview before you confirm.
- Old files are copied beside the originals before overwrite (`*.bak.epbs.` plus a timestamp).
- Too-early Complete: restore the BN unit from that backup and `sudo systemctl enable --now mevboost`.

#### Checking from the TUI

Use **Show current ePBS status**. It reports:

- validator and beacon-node clients
- whether your client fully supports VC relays yet
- whether MEV-Boost is installed and how many relays it has
- whether the validator already has a relay list
- whether the beacon node still has the local MEV-Boost URL

---

### Obol Charon DV (everything on one host)

Use this when Charon sits between your validator client and beacon node on the **same** machine as MEV-Boost. See also [docs/charon.md](charon.md).

On the pre-Gloas path, `charon.service` runs with **`--builder-api`** (MEV-Boost builder proxy). Charon owns the builder path — not the signer VC.

**TUI:** **MEV-Boost → ePBS migration** is **hidden** while Charon is installed, even if the signer VC is Prysm or Lodestar. Obol has not shipped stable Gloas/ePBS support yet (`charonEpbsSupported` is false). When upstream support lands, that same **MEV-Boost → ePBS migration** entry will be shown again for co-located Charon nodes.

**CLI today** (`python -m manage.epbs`):

| Step | Behavior |
|------|----------|
| **prepare** | Keeps `--builder-api`; **does not** write Prysm/Lodestar VC relay lists (those would bypass Charon) |
| **complete** | Removes `--builder-api` from `charon.service`, strips the BN sidecar URL, disables MEV-Boost. Allowed while `--builder-api` is still present (no VC relay list required) |

After **complete**, restart in order: **consensus → charon → validator**.

**Upstream:** confirm Charon versions against [Charon releases](https://github.com/ObolNetwork/charon/releases) before relying on Gloas block production through Charon.

---

### Split hosts (VC or DV remote from CC and MEV)

Use this when consensus + MEV-Boost run on one machine and the validator (or Charon + VC) on another. Execution placement does not matter for this flow.

You still do the same two steps (before Gloas / after Gloas), but relays move via a small portable file instead of a local prepare.

#### Before Gloas

1. **MEV/CC host** — **MEV-Boost → ePBS migration** (always shown when MEV is present and there is no local `validator.service`). The submenu title is **ePBS migration (remote VC)**. Choose **Before Gloas Fork — Export migration file**. EthPillar writes `~/hostname-YYYYMMDD-HHMMSS.ethpillar.epbs-migration` immediately (relays + min-bid) and shows one result textbox. There is no dry-run/confirm — Export always writes. Copy that file to the VC/DV host.

2. **Solo VC host** (no Charon, no local MEV) — **Validator → ePBS migration (import)** when the VC is Prysm or Lodestar. Submenu title **ePBS migration (import)**. Choose **Before Gloas Fork — Import migration file**. Path inputbox, then the usual four-screen dry-run → confirm → apply → optional restart.

3. **Charon + VC host** (no local MEV) — **Charon → ePBS migration (import)** only when `charonEpbsSupported` is true. Until Obol ships Charon ePBS, that entry is **hidden** (not under Validator). The CLI `import` command also **refuses** while Charon is installed without ePBS support.

#### After Gloas

1. **MEV/CC host** — **After Gloas Fork — Complete ePBS migration**. Confirm that the other host already imported. Stops MEV-Boost and strips the BN sidecar URL.
2. **Solo VC host** — Complete is a local no-op (relays were already applied on import).
3. **Charon + VC host** — Complete strips Charon `--builder-api` when present (once that path is available in the TUI/CLI for your Charon version).

#### File format

- Extension: `.ethpillar.epbs-migration`
- Default name: `{hostname}-{YYYYMMDD-HHMMSS}.ethpillar.epbs-migration`
- Plain JSON (relays, min-bid, network, hostname, timestamp). Rejected on import if format/version is unknown or relays are empty.

---

## For automation and developers

The TUI calls `python -m manage.epbs`. Scripts and tests can do the same. Default is dry-run; pass `--apply` to write.

```bash
# From the EthPillar install directory
PYTHONPATH="${PWD}" python3 -m manage.epbs status
PYTHONPATH="${PWD}" python3 -m manage.epbs prepare          # dry-run
PYTHONPATH="${PWD}" python3 -m manage.epbs prepare --apply
PYTHONPATH="${PWD}" python3 -m manage.epbs complete         # dry-run
PYTHONPATH="${PWD}" python3 -m manage.epbs complete --apply
# Only if you really want local EL + P2P bids with no VC relays:
PYTHONPATH="${PWD}" python3 -m manage.epbs complete --apply --force
# Split hosts: export on MEV host, import on solo VC host
PYTHONPATH="${PWD}" python3 -m manage.epbs export -o ~/bn.ethpillar.epbs-migration
PYTHONPATH="${PWD}" python3 -m manage.epbs import ~/bn.ethpillar.epbs-migration
PYTHONPATH="${PWD}" python3 -m manage.epbs import ~/bn.ethpillar.epbs-migration --apply
# MEV/CC host after the VC host imported:
PYTHONPATH="${PWD}" python3 -m manage.epbs complete --apply --remote-vc-prepared
```

`--json` prints a machine-readable plan (the TUI uses this after apply). `--systemd-dir` and `--prysm-settings` override paths for tests. `--force` allows `complete` when the VC has no relay list. `--remote-vc-prepared` allows `complete` on a MEV/CC host when the VC on another host already imported.

Changed units and Prysm settings are copied to `*.bak.epbs.<timestamp>` before overwrite. `complete` stops and disables `mevboost.service`; the unit file is kept. If you completed too early: restore the newest `consensus.service.bak.epbs.*` over `consensus.service`, then `sudo systemctl enable --now mevboost && sudo systemctl daemon-reload && sudo systemctl restart consensus`.

Implementation: `manage/epbs.py`. TUI wrappers: `runEpbsCli` / `runEpbsMigrationStep` / `submenuEPBS` / `submenuEPBSImport` in `functions.sh`. Menu visibility: `epbsTuiSupported`, `epbsImportUnderValidator`, `epbsImportUnderCharon` (`charonEpbsSupported` / `charon_epbs_supported` stub until Obol ships).

### What each command changes

Relays and `-min-bid` are read from `mevboost.service` (or from a migration file for `import`). Sidecar URLs are those containing `127.0.0.1:18550`, `localhost:18550`, or `[::1]:18550`. Non-sidecar builder URLs on the BN are kept.

#### `export` / `import`

| Command | Host | Behavior |
|---------|------|----------|
| **export** | MEV | Writes `.ethpillar.epbs-migration` JSON (format version 1: relays, min-bid, network, hostname, timestamp). Always writes. |
| **import** | Solo VC | Loads that file and applies the same VC relay writes as solo `prepare`. Does not require local MEV. **Refused** when Charon is installed and `charon_epbs_supported` is false (matches the TUI). |

#### `prepare`

| Client | Behavior |
|--------|----------|
| **Obol Charon** (any signer VC, co-located) | Keeps `--builder-api`; **skips** VC relay writes. TUI entry hidden until Obol ships Gloas/ePBS support. |
| **Prysm** (v7.2.0+, no Charon) | Writes `/var/lib/prysm_validator/proposer-settings.json` (schema version 2). Each MEV-Boost relay becomes `default_config.builder.builders[].url`. A nonempty `builders` list opts the key into pre-Gloas mev-boost registration and is the post-Gloas builder list. `auth_data` is omitted (Prysm signs the URL bytes). `max_execution_payment` is `"0"` (trustless-only: collateral-backed bid value counts; a builder’s promised execution-layer payment does not). MEV-Boost `-min-bid` (ETH) is copied to `builder.min_bid` as integer Gwei. Copies `--suggested-fee-recipient` into `fee_recipient` if missing. Sets `--proposer-settings-file` and **removes** deprecated `--enable-builder` (that flag only produces legacy pre-Gloas content and does not override v2 settings). Does not write `gas_limit` or `--suggested-gas-limit`. Drops legacy `builder.enabled`, `builder.relays`, and `builders_set` (v7.2.0 ignores `relays` and rejects unknown keys / `builders_set`). On Sepolia, warns that v7.2.0 defaults to a 60M gas limit unless you set `"gas_limit": "200000000"` yourself. Restarts `validator` if the TUI operator agrees. Does not stop MEV-Boost. |
| **Lodestar** (v1.47.0+, no Charon) | Adds VC flags `--builder`, `--builder.urls=<comma URLs>`, and `--builder.minBid` (MEV-Boost ETH min-bid converted to integer Gwei), **only when** `lodestar validator --help` lists `--builder.urls`. Older builds are skipped so the VC can still start. |
| **Erigon-Caplin** (v3.7.1+, no separate VC) | Writes `/var/lib/erigon/caplin-builders.json` **only when** `erigon --version` is at least v3.7.1. Each MEV-Boost relay becomes `builders[].url` with `max_execution_payment` `"0"` (trustless-only). `min_bid` is MEV-Boost `-min-bid` in integer Gwei. Does **not** replace `--caplin.mev-relay-url` (that flag is a single pre-Gloas sidecar; Caplin v3.7.1 has no multi-relay CLI flag). Complete removes it, which switches Caplin from the legacy relay client to the Gloas dynamic builder client. A validator client still supplies builder URLs on each block-production request; this file is the list EthPillar requires before that switch. Older binaries are skipped. |
| **Lighthouse, Teku, Nimbus, Grandine** | Documented no-op; units are not mutated. |

BN sidecar flags stay until `complete`.

#### `complete`

Refused unless the VC already has a relay list (successful solo `prepare` / `import`), Charon still has `--builder-api`, you pass `--remote-vc-prepared` (split MEV host), or you pass `--force` (local EL + P2P bids only).

On a **VC/Charon-only** host (no consensus/MEV): strips Charon `--builder-api` when present; solo VC reports nothing local to complete.

On a **MEV/CC** host:

1. Stop and disable `mevboost.service`.
2. Strip BN sidecar builder flags (skipped with a warning if no consensus unit):

   | Beacon node | Flag removed when it points at local MEV-Boost |
   |-------------|--------------------------------------------------|
   | Prysm | `--http-mev-relay` |
   | Lighthouse | `--builder` |
   | Teku | `--builder-endpoint` |
   | Lodestar | `--builder.urls` (and boolean `--builder` if no URL remains) |
   | Nimbus | `--payload-builder-url` |
   | Grandine | `--builder-url` / `--builder-api-url` |
   | Erigon-Caplin | `--caplin.mev-relay-url` |
   | Obol Charon | `--builder-api` (MEV-Boost proxy; no stable upstream ePBS release yet) |

3. Do not rewrite VC relay config from `prepare` / `import`.

Restart `consensus` after apply so the BN drops the sidecar URL. Integrated Caplin restarts `execution` instead (the sidecar is `--caplin.mev-relay-url` on `execution.service`). When Charon is installed, also restart `charon` (and `validator` if its flags changed on prepare/import). Prysm/Lodestar VC flags and `caplin-builders.json` do not change on this step alone.

### Client support levels

| Validator | Support | Notes |
|-----------|---------|--------|
| Prysm v7.2.0+ | **full** | TUI + CLI. Relay URLs in proposer-settings `default_config.builder.builders` (schema v2). `--enable-builder` is removed on prepare. BN `--http-mev-relay` until complete. A file that only has legacy `builder.relays` is not treated as prepared. |
| Lodestar v1.47.0+ | **full** | TUI + CLI. VC `--builder.urls` / `--builder.minBid` written only if `--help` lists them. |
| Erigon-Caplin v3.7.1+ | **full** | TUI + CLI. Integrated client (no `validator.service`). Prepare writes `/var/lib/erigon/caplin-builders.json` when `erigon --version` is at least v3.7.1. `--caplin.mev-relay-url` stays until complete. QUIC is `--caplin.discovery.quicport` on UDP 9001 (`CL_P2P_PORT_2`, eth-docker #2836); Caplin's native QUIC default is UDP 4001, which collides with its native TCP port. |
| Lighthouse | **placeholder** | VC `--builder-proposals` only; one BN `--builder` URL. |
| Teku | **placeholder** | Staked Builder REST client ([Consensys/teku#11026](https://github.com/Consensys/teku/issues/11026)) not wired. Relays stay on BN `--builder-endpoint`. |
| Nimbus | **placeholder** | VC `--payload-builder=true`; URL on BN. |
| Grandine | **placeholder** | Integrated client; single `--builder-url`. |

### Inspecting a running Prysm VC

After import (or co-located prepare), Prysm’s journal may show **both**:

- `Proposer settings loaded from default` — from `--suggested-fee-recipient`
- `Proposer settings loaded from file` — from `--proposer-settings-file`

That pair is expected. Builder URLs live in the JSON at `default_config.builder.builders` (each entry’s `url`). A legacy `builder.relays` array is ignored by Prysm v7.2.0. Seeing “loaded from default” does **not** mean import failed. A startup warning about `--enable-builder` means that deprecated flag is still on the unit; prepare removes it.

Confirm the import from the running process flags and the JSON file, not from that journal line alone:

```bash
# journal: both "from default" and "from file" is OK
sudo journalctl -u validator --no-pager -n 80 | grep -i "proposer settings"

pid=$(sudo systemctl show -p MainPID --value validator)
tr '\0' ' ' < /proc/${pid}/cmdline
# expect --proposer-settings-file=... and no --enable-builder
sudo cat /var/lib/prysm_validator/proposer-settings.json
# builder URLs are under default_config.builder.builders[].url
```
