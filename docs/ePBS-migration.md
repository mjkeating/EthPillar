# ePBS / Gloas MEV migration

Gloas (the consensus-layer half of [Glamsterdam](https://docs.ethstaker.org/upgrades/glamsterdam-features/)) can move builder relay configuration **off MEV-Boost and onto the validator client**. ePBS is optional: if you never run **Before Gloas Fork**, classic MEV-Boost stays as it is. The fork does not change that, and it does not run complete for you.

If you do run **Before Gloas Fork** (prepare), that only stages validator relays beside MEV-Boost. After Gloas is live on that network, run **After Gloas Fork** (complete) yourself. Complete stops MEV-Boost and strips the beacon-node sidecar. Having both at once is the staging window between those two menu steps, not a setup to leave in place.

Most operators only need [Solo node (everything on one host)](#solo-node-everything-on-one-host). Read the Charon or split-host sections only if they apply to you.

---

## For node operators

This section is the TUI only. You do not need to run Python yourself.

### The two steps (all setups)

1. **Before Gloas Fork (prepare)** — stage relays onto the validator’s ePBS builder path. Keep MEV-Boost running. The beacon node still talks to the local sidecar.
2. **After Gloas Fork (complete)** — after Gloas on that network, run this yourself. It stops MEV-Boost and removes the beacon-node sidecar (`127.0.0.1:18550`).

The fork does not run **After Gloas Fork** for you. If you never prepared, classic MEV stays. If you did prepare, run complete after the fork; do not leave MEV-Boost and the VC builder flags both enabled past that point. Completing before the validator can use its builder list means the beacon node no longer talks to MEV-Boost.

---

### Solo node (everything on one host)

Use this when execution, consensus, MEV-Boost, and a **solo** validator client all run on the same machine (no Obol Charon).

#### Open the menu

**MEV-Boost → ePBS migration**

That item appears when the local validator fully supports migration (**Prysm**, **Lodestar** v1.47.0+, **Teku** v26.11.0+, **Nimbus** v26.10.2+, integrated **Erigon-Caplin** v3.7.1+, or **Lighthouse** v8.3.0+). Grandine does not get the TUI entry. The menu is shown for Teku, Nimbus, Caplin, and Lighthouse even when the installed binary is older; Prepare then explains that it did not write a builder list.

| Menu item | When to use it |
|-----------|----------------|
| Before Gloas Fork — Apply Relays to VC | Before the Gloas fork. Stages VC relays. MEV-Boost stays up. |
| After Gloas Fork — Complete ePBS migration | After Gloas on this network. Run this yourself if you prepared. Stops MEV-Boost and strips the BN sidecar. |
| Show current ePBS status | Anytime (read-only) |

Those labels are the order. Prepare does not finish the migration. After the fork, **After Gloas Fork** is the step that does.

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
| **Prysm** (v7.2.1+) | Writes `--builder-urls`, `--builder-min-bid` (integer Gwei), and `--builder-boost-factor` on the validator (`EPBS_BUILD_FACTOR`: `0`/`local` → `0`, `always` → uint64 max, `maxprofit` → `100`). Removes deprecated `--enable-builder`. Leaves beacon `--http-mev-relay` in place on every network, including Sepolia, until you run **After Gloas Fork**. The fork does not strip it. v7.2.0 still uses the proposer-settings file instead of these flags. **Does not** stop MEV-Boost. |
| **Lodestar** (v1.47.0+) | Writes `--builder.urls` and `--builder.minBid` on the validator. Older Lodestar builds skip this so the client can still start. **Does not** stop MEV-Boost. |
| **Teku** (v26.11.0+) | Writes `--builder-urls`, `--builder-min-bid`, and `--p2p-builder-min-bid` (integer Gwei) on the validator client. `--builder-boost-factor` and `--p2p-builder-boost-factor` are written only when a build factor is set (`TEKU_EPBS_BUILD_FACTOR`, else `EPBS_BUILD_FACTOR`, else the beacon node's `--builder-bid-compare-factor`): `0`/`local` → `0`, `always`/`BUILDER_ALWAYS` → uint64 max, `maxprofit` → `100`, a number as-is. The same value is written on both flags (Teku 26.10.x used one combined flag). Unset leaves Teku 26.11.0's default (100). On Sepolia, sets `--validators-builder-registration-default-gas-limit=200000000` unless you already set that flag (Teku still has no 200M schedule). Older Teku builds skip this so the client can still start. A unit that still has the removed `--Xbuilder-*` names is rewritten on prepare, complete, and when EthPillar upgrades Teku. **Does not** stop MEV-Boost. The beacon node keeps `--builder-endpoint` until the after-fork step. |
| **Erigon-Caplin** (v3.7.1+) | Writes each MEV-Boost relay URL into `/var/lib/erigon/caplin-builders.json` (`builders[].url`, `max_execution_payment` `"0"`). Keeps `--caplin.mev-relay-url` pointed at local MEV-Boost. Older Erigon builds skip this so the node can still start. **Does not** stop MEV-Boost. v3.7.1 already schedules Sepolia's 200M gas limit; EthPillar does not set one. |
| **Lighthouse** (v8.3.0+) | Writes each MEV-Boost relay URL into `<datadir>/validators/builder_definitions.yml` as `builders[].url` (`enabled: true`, `max_execution_payment: 0`). That file is the post-Gloas builder list (applies from Gloas only). Keeps `--builder-proposals` and does **not** set `--gas-limit`. Older Lighthouse builds skip this so the client can still start. **Does not** stop MEV-Boost. |
| **Nimbus** (v26.10.2+) | Writes one `--payload-builder-url` per non-sidecar relay on the validator (with `--payload-builder=true`), plus `--builder-min-bid` (integer Gwei), `--builder-max-execution-payment=0`, and `--builder-boost-factor` from `EPBS_BUILD_FACTOR` (`0`/`local` → `0`, `always` → uint64 max, `maxprofit` → `100`). Unset leaves Nimbus's default (100). On Sepolia, sets `--suggested-gas-limit=200000000` unless you already set that flag. Older Nimbus builds skip this so the client can still start. **Does not** stop MEV-Boost. The beacon node keeps `--payload-builder-url` until the after-fork step. |
| **Grandine** | Not offered in the TUI. |

After **Before Gloas Fork**, the beacon node still uses local MEV-Boost. That overlap is only the staging window. After Gloas on this network, run **After Gloas Fork** yourself. The fork does not strip the sidecar.

If an older EthPillar already wrote `builder.relays` (and `--enable-builder`), run the before-fork step again. Prysm ignores `relays`. Complete stays refused until `--builder-urls` or a `builders` list is present.

**Sepolia gas limit.** Prysm v7.2.1 includes the 200M `GAS_LIMIT_SCHEDULE`, so EthPillar does not write a Prysm gas limit. `--suggested-gas-limit` overrides that schedule from Gloas onward and the client warns if it is set; remove it to follow the schedule. Prysm before v7.2.1 still defaults to 60M. Teku still has no 200M schedule: on Sepolia, prepare sets `--validators-builder-registration-default-gas-limit=200000000` unless that flag is already present. Nimbus proposer preferences copy `--suggested-gas-limit` (default 60M) into `target_gas_limit`: on Sepolia, prepare sets `--suggested-gas-limit=200000000` unless that flag is already present.

**Lighthouse gas limit (v8.3.0+).** Lighthouse schedules 200000000 gas at Sepolia Gloas epoch 353024 and Hoodi Gloas epoch 132352 (26 October 2026). EthPillar does not set `--gas-limit` or a per-validator `gas_limit` in `validator_definitions.yml`, because those override the schedule. If either is already set, the preview says so and leaves it. Use Lighthouse v8.3.0 or newer on both the beacon node and the validator client for Gloas networks.

**Web3Signer + Lighthouse.** Gloas signing follows `remote-signing-api` v1.4.0. If the validator uses Web3Signer, run **Web3Signer 26.9.0 or newer** before Gloas (see [web3signer.md](web3signer.md)).

**MEV-Boost on mainnet vs Gloas.** Lighthouse v8.3.0 on mainnet keeps using the BN `--builder` sidecar with current MEV-Boost (v1.12 serves `submitBlindedBlockV2` and falls back to V1 per relay). There is still no Gloas-capable MEV-Boost release ([flashbots/mev-boost#914](https://github.com/flashbots/mev-boost/pull/914)); after Gloas, use the VC `builder_definitions.yml` path from prepare rather than waiting on a sidecar update.

If you never run **Before Gloas Fork**, classic MEV-Boost stays; ePBS is optional. If you did prepare, run **After Gloas Fork** after the fork so both paths are not left on. No builders were onboarded on Sepolia before the fork (ACDT #99); a successful prepare does not mean a live builder bid will arrive.

**After Gloas Fork (complete)** — run this yourself after Gloas, once prepare has succeeded

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

**TUI:** **MEV-Boost → ePBS migration** is **hidden** while Charon is installed, even if the signer VC is Prysm, Lodestar, Teku, Nimbus, or Lighthouse. Obol has not shipped stable Gloas/ePBS support yet (`charonEpbsSupported` is false). When upstream support lands, that same **MEV-Boost → ePBS migration** entry will be shown again for co-located Charon nodes.

**CLI today** (`python -m manage.epbs`):

| Step | Behavior |
|------|----------|
| **prepare** | Keeps `--builder-api`; **does not** write Prysm/Lodestar/Teku/Nimbus/Lighthouse VC relay lists (those would bypass Charon) |
| **complete** | Removes `--builder-api` from `charon.service`, strips the BN sidecar URL, disables MEV-Boost. Allowed while `--builder-api` is still present (no VC relay list required) |

After **complete**, restart in order: **consensus → charon → validator**.

**Upstream:** confirm Charon versions against [Charon releases](https://github.com/ObolNetwork/charon/releases) before relying on Gloas block production through Charon.

---

### Split hosts (VC or DV remote from CC and MEV)

Use this when consensus + MEV-Boost run on one machine and the validator (or Charon + VC) on another. Execution placement does not matter for this flow.

You still do the same two steps (before Gloas / after Gloas), but relays move via a small portable file instead of a local prepare.

#### Before Gloas

1. **MEV/CC host** — **MEV-Boost → ePBS migration** (always shown when MEV is present and there is no local `validator.service`). The submenu title is **ePBS migration (remote VC)**. Choose **Before Gloas Fork — Export migration file**. EthPillar writes `~/hostname-YYYYMMDD-HHMMSS.ethpillar.epbs-migration` immediately (relays + min-bid) and shows one result textbox. There is no dry-run/confirm — Export always writes. Copy that file to the VC/DV host.

2. **Solo VC host** (no Charon, no local MEV) — **Validator → ePBS migration (import)** when the VC is Prysm, Lodestar, Teku, Nimbus, or Lighthouse. Submenu title **ePBS migration (import)**. Choose **Before Gloas Fork — Import migration file**. Path inputbox, then the usual four-screen dry-run → confirm → apply → optional restart.

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
| **Prysm** (v7.2.1+, no Charon) | Writes VC `--builder-urls` (non-sidecar relay URLs), `--builder-min-bid` (MEV-Boost ETH min-bid as integer Gwei), `--builder-max-execution-payment=0` (trustless-only), and `--builder-boost-factor` from `EPBS_BUILD_FACTOR` (`0`/`local` → `0`, `always` → `18446744073709551615`, `maxprofit` → `100`, a number capped at uint64 max). Unset omits the flag once builders are on (Prysm default 100) and drops a leftover `--builder-boost-factor=0` so that default applies. **Removes** deprecated `--enable-builder`. Does not create a proposer-settings file just for builders. If `--proposer-settings-file` is already set, the file is rewritten to schema v2 and kept in sync, because Prysm replaces CLI builder defaults with `default_config`. `pubkeys` is renamed to `builder_pubkeys`; `auth_data` and pubkeys are 0x-hex (legacy base64 is converted). Omitted `auth_data` defaults to the URL hostname; URLs without a hostname are rejected. Does not write `gas_limit`. On v7.2.1+, `--suggested-gas-limit` is left in place but warned: it overrides the gas schedule after Gloas. BN `--http-mev-relay` stays until complete on every network, including Sepolia. v7.2.0 (or an unreadable `--version`) falls back to writing the settings file and, on Sepolia, the old 60M gas note. |
| **Lodestar** (v1.47.0+, no Charon) | Adds VC flags `--builder`, `--builder.urls=<comma URLs>`, and `--builder.minBid` (MEV-Boost ETH min-bid converted to integer Gwei), **only when** `lodestar validator --help` lists `--builder.urls`. `EPBS_BUILD_FACTOR` maps to `--builder.selection` (`0`/`local` → `executionalways`, `always` → `builderalways`, `maxprofit` or a number → `maxprofit` plus `--builder.boostFactor`). Older builds are skipped so the VC can still start. |
| **Teku** (v26.11.0+, no Charon) | Adds VC flags `--builder-urls=<comma URLs>`, `--builder-min-bid`, and `--p2p-builder-min-bid` (MEV-Boost ETH min-bid converted to integer Gwei), **only when** `teku --version` is at least 26.11.0. Teku 26.11.0 removed the hidden `--Xbuilder-*` names (Consensys/teku#11563); they are not aliases, so 26.9.0–26.10.x are skipped. `--builder-boost-factor` and `--p2p-builder-boost-factor` use the same token map as Prysm, from `TEKU_EPBS_BUILD_FACTOR`, else `EPBS_BUILD_FACTOR`, else the beacon node's `--builder-bid-compare-factor`, and receive the same value. Unset omits both flags (Teku 26.11.0 default 100). On Sepolia, sets `--validators-builder-registration-default-gas-limit=200000000` when that flag is absent. Keeps `--validators-builder-registration-default-enabled` for pre-Gloas registration. EthPillar does not run an embedded Teku validator inside the beacon node; the flags are written on `validator.service` only. Prepare, complete, and the Teku upgrade path rewrite a leftover `--Xbuilder-*` unit so 26.11.0 can start. |
| **Erigon-Caplin** (v3.7.1+, no separate VC) | Writes `/var/lib/erigon/caplin-builders.json` **only when** `erigon --version` is at least v3.7.1. Each MEV-Boost relay becomes `builders[].url` with `max_execution_payment` `"0"` (trustless-only). `min_bid` is MEV-Boost `-min-bid` in integer Gwei. Does **not** replace `--caplin.mev-relay-url` (that flag is a single pre-Gloas sidecar; Caplin v3.7.1 has no multi-relay CLI flag). Complete removes it, which switches Caplin from the legacy relay client to the Gloas dynamic builder client. A validator client still supplies builder URLs on each block-production request; this file is the list EthPillar requires before that switch. Older binaries are skipped. |
| **Lighthouse** (v8.3.0+, no Charon) | Writes `<datadir>/validators/builder_definitions.yml` **only when** `lighthouse --version` is at least v8.3.0. Each MEV-Boost relay becomes `builders[].url` with `enabled: true` and `max_execution_payment: 0` (trustless-only). `auth_data` is omitted (Lighthouse signs the URL hostname). Global `min_bid` is MEV-Boost `-min-bid` in integer Gwei. Keeps `--builder-proposals` and the beacon-node `--builder` sidecar. Does not set `--gas-limit` or a per-validator `gas_limit` (Sepolia/Hoodi schedules are already 200M at Gloas; an existing override is left in place and warned about). Older builds are skipped so the VC can still start. Does not stop MEV-Boost. |
| **Nimbus** (v26.10.2+, no Charon) | Adds VC flags `--payload-builder=true`, one `--payload-builder-url=<url>` per non-sidecar relay (repeatable; nimbus-eth2#9336), `--builder-min-bid` (MEV-Boost ETH min-bid as integer Gwei), `--builder-max-execution-payment=0` (trustless-only), and `--builder-boost-factor` from `EPBS_BUILD_FACTOR` (same tokens as Prysm). Unset omits the flag (Nimbus default 100) and drops a leftover `0`. On Sepolia, sets `--suggested-gas-limit=200000000` when that flag is absent (proposer preferences copy it into `target_gas_limit`). Hostname-less URLs are rejected (`get_default_auth_data`). EthPillar writes CLI/systemd units (repeat the flag); TOML must use list form. BN `--payload-builder-url` stays until complete. Older binaries are skipped. |
| **Grandine** | Documented no-op; units are not mutated. |

Prepare leaves BN sidecar flags in place on every network. Fork activation does not remove them. After prepare, run `complete` yourself once Gloas is live on that network. MEV-Boost plus VC builder flags together is only the staging window between the two steps. If you never prepare, classic MEV stays.

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
   | Nimbus | `--payload-builder-url` (and `--payload-builder` when no builder URL remains) |
   | Grandine | `--builder-url` / `--builder-api-url` |
   | Erigon-Caplin | `--caplin.mev-relay-url` |
   | Obol Charon | `--builder-api` (MEV-Boost proxy; no stable upstream ePBS release yet) |

3. Do not rewrite VC relay config from `prepare` / `import`.

Restart `consensus` after apply so the BN drops the sidecar URL. Integrated Caplin restarts `execution` instead (the sidecar is `--caplin.mev-relay-url` on `execution.service`). When Charon is installed, also restart `charon` (and `validator` if its flags changed on prepare/import). Prysm/Lodestar/Teku/Nimbus VC flags, `caplin-builders.json`, and the Lighthouse `builder_definitions.yml` file do not change on this step alone.

### Client support levels

| Validator | Support | Notes |
|-----------|---------|--------|
| Prysm v7.2.1+ | **full** | TUI + CLI. `--builder-urls` / `--builder-min-bid` / `--builder-boost-factor` when `prysm-validator --version` is at least v7.2.1. v7.2.0 uses proposer-settings `builders`. `--enable-builder` is removed on prepare. BN `--http-mev-relay` stays until complete on every network. A file that only has legacy `builder.relays` is not treated as prepared. |
| Lodestar v1.47.0+ | **full** | TUI + CLI. VC `--builder.urls` / `--builder.minBid` written only if `--help` lists them. |
| Teku v26.11.0+ | **full** | TUI + CLI. Separate VC (no embedded validator on the beacon node). Prepare writes `--builder-urls` / `--builder-min-bid` / `--p2p-builder-min-bid` when `teku --version` is at least 26.11.0. Optional `--builder-boost-factor` and `--p2p-builder-boost-factor` (same value) from `TEKU_EPBS_BUILD_FACTOR` or BN `--builder-bid-compare-factor`. Leftover `--Xbuilder-*` flags are renamed. BN `--builder-endpoint` stays until complete. |
| Erigon-Caplin v3.7.1+ | **full** | TUI + CLI. Integrated client (no `validator.service`). Prepare writes `/var/lib/erigon/caplin-builders.json` when `erigon --version` is at least v3.7.1. `--caplin.mev-relay-url` stays until complete. QUIC is `--caplin.discovery.quicport` on UDP 9001 (`CL_P2P_PORT_2`, eth-docker #2836); Caplin's native QUIC default is UDP 4001, which collides with its native TCP port. |
| Lighthouse v8.3.0+ | **full** | TUI + CLI. `builder_definitions.yml` `builders[].url` written only if `lighthouse --version` is new enough. BN `--builder` sidecar until complete. Does not set `--gas-limit` (Sepolia/Hoodi schedule is 200M). Web3Signer users need 26.9.0+. |
| Nimbus v26.10.2+ | **full** | TUI + CLI. Separate VC. Prepare writes repeated `--payload-builder-url` plus `--builder-min-bid` / `--builder-max-execution-payment=0` / `--builder-boost-factor` when `nimbus_validator_client --version` is at least 26.10.2. BN `--payload-builder-url` stays until complete, which also removes BN `--payload-builder` when no URL remains. |
| Grandine | **placeholder** | Integrated client; single `--builder-url`. |

### Inspecting a running Prysm VC

After import (or co-located prepare), Prysm’s journal may show **both**:

- `Proposer settings loaded from default` — from `--suggested-fee-recipient`
- `Proposer settings loaded from file` — from `--proposer-settings-file`

On v7.2.1+ the builder list is `--builder-urls` on the process. A settings file is only present when one was already configured; its `default_config` replaces the CLI builder defaults, so prepare keeps the file in sync. `builder_pubkeys` and `auth_data` are 0x-hex. A legacy `builder.relays` array is ignored. Seeing “loaded from default” does **not** mean import failed. A startup warning about `--enable-builder` means that deprecated flag is still on the unit; prepare removes it. A warning about `--suggested-gas-limit` means that flag overrides the post-Gloas gas schedule.

Confirm the import from the running process flags:

```bash
pid=$(sudo systemctl show -p MainPID --value validator)
tr '\0' ' ' < /proc/${pid}/cmdline
# v7.2.1+: expect --builder-urls=... and no --enable-builder
# v7.2.0 fallback: expect --proposer-settings-file=... and no --enable-builder
```
