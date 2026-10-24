"""Test-only LATEST remapping for Upgrade-case deploy.

Upgrade-matrix cases (``--test-updates``) install a resolvable **upgrade seed**
first (RC newer than LATEST, else a sane previous stable), then run the
unchanged ``ethpillar upgrade`` path so it can move to official LATEST and
afterwards prove skip-when-already-latest.

Lifecycle:
  1. ``prepare_rc_overrides`` discovers seeds via
     :func:`find_client_rc.find_upgrade_seed` and writes this file **before**
     deploy. No file (or empty ``clients``) means deploy stays on official
     LATEST; ``test_updates.sh`` then soft-skips the real-upgrade assert.
  2. Integration ``sitecustomize`` wraps ``deploy.common.get_github_release``
     and ``deploy.geth.get_release_info`` so deploy-time ``LATEST`` can resolve
     to the seed tag (Geth does not use GitHub).
  3. Post-deploy version checks read the same file and expect the forced seed
     tags (base semver ± commit; missing ``-rc.N`` on the binary is OK).
  4. ``clear_override`` removes the remap file **before** upgrade so LATEST is
     real. A companion seeds manifest is left in place so the harness knows
     which clients were seeded and what official LATEST was at seed time.
  5. When that manifest shows a **consensus or execution** seed semver-newer
     than official LATEST, ``test_updates.sh`` wipes that role's chain data
     and leaves the unit stopped before ``ethpillar upgrade`` starts the
     older binary. Consensus wipes the beacon DB; execution wipes the EL
     datadir contents (``resync_execution.sh`` paths, not validator keys or
     the JWT). Previous stable seeds are older than LATEST and keep their
     DB so the forward migration path still runs.

This module is integration-test scoped. Production CLI upgrade semantics are
unchanged (always official LATEST; skip when already current).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Iterable

OVERRIDE_PATH = "/tmp/ethpillar-integration-latest-override.json"
SEEDS_PATH = "/tmp/ethpillar-integration-upgrade-seeds.json"
ENV_VAR = "ETHPILLAR_INTEGRATION_LATEST_OVERRIDE"
SEEDS_ENV = "ETHPILLAR_INTEGRATION_UPGRADE_SEEDS"
_HOOK_ATTR = "_ethpillar_latest_override_wrapped"
_GETH_HOOK_ATTR = "_ethpillar_geth_latest_override_wrapped"
_INTEGRATION_DIR = os.path.dirname(os.path.abspath(__file__))
if _INTEGRATION_DIR not in sys.path:
    sys.path.insert(0, _INTEGRATION_DIR)

# Role / combo tokens that are not GitHub-released clients.
_SKIP_NAMES = frozenset({"", "caplin", "same as cc", "same", "none"})

# Consensus clients whose beacon DB ``resync_consensus.sh`` can wipe.
# Keep this aligned with ``beacon_datadir_for_client`` (not EL / MEV / Charon).
CONSENSUS_CLIENTS = frozenset({
    "lighthouse",
    "lodestar",
    "teku",
    "nimbus",
    "prysm",
    "grandine",
})

# Execution clients whose chaindata ``resync_execution.sh`` wipes.
# Keep this aligned with ``resyncClient`` there (not CL / MEV / Charon).
EXECUTION_CLIENTS = frozenset({
    "nethermind",
    "besu",
    "geth",
    "erigon",
    "ethrex",
    "reth",
})


def override_path(path: str | None = None) -> str:
    """Return the override JSON path (explicit, env, or default)."""
    if path:
        return path
    return os.environ.get(ENV_VAR) or OVERRIDE_PATH


def seeds_path(path: str | None = None, override: str | None = None) -> str:
    """Return the persistent seeds-manifest path (survives ``clear_override``)."""
    if path:
        return path
    env = os.environ.get(SEEDS_ENV)
    if env:
        return env
    if override:
        return os.path.join(os.path.dirname(os.path.abspath(override)), os.path.basename(SEEDS_PATH))
    return SEEDS_PATH


def _ensure_repo_on_path() -> str:
    """Put the EthPillar repo root on ``sys.path`` and return it."""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    return repo_root


def _empty_override() -> dict[str, Any]:
    """Return the empty override / seeds shape."""
    return {"clients": {}, "repos": {}, "kinds": {}}


def load_override(path: str | None = None) -> dict[str, Any]:
    """Load ``{clients, repos, kinds}`` mappings, or empty dicts when absent/invalid."""
    target = override_path(path)
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return _empty_override()
    except (OSError, json.JSONDecodeError):
        return _empty_override()
    if not isinstance(data, dict):
        return _empty_override()
    clients = data.get("clients") if isinstance(data.get("clients"), dict) else {}
    repos = data.get("repos") if isinstance(data.get("repos"), dict) else {}
    kinds = data.get("kinds") if isinstance(data.get("kinds"), dict) else {}
    return {
        "clients": {str(key).lower(): str(value) for key, value in clients.items() if value},
        "repos": {str(key): str(value) for key, value in repos.items() if value},
        "kinds": {str(key).lower(): str(value) for key, value in kinds.items() if value},
    }


def write_override(
    clients: dict[str, str],
    path: str | None = None,
    repos: dict[str, str] | None = None,
    kinds: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write client and repo → seed tag mappings. Returns the payload."""
    from find_client_rc import CLIENT_REPOS

    payload: dict[str, Any] = {
        "clients": {name.lower(): tag for name, tag in clients.items() if tag},
        "repos": dict(repos or {}),
        "kinds": {name.lower(): kind for name, kind in (kinds or {}).items() if kind},
    }
    if not payload["repos"]:
        for name, tag in payload["clients"].items():
            repo = CLIENT_REPOS.get(name)
            if repo:
                payload["repos"][repo] = tag
    payload["kinds"] = {name: kind for name, kind in payload["kinds"].items() if name in payload["clients"]}
    target = override_path(path)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")
    return payload


def _empty_seeds() -> dict[str, Any]:
    """Return the empty seeds-manifest shape."""
    return {"clients": {}, "kinds": {}, "skipped": {}, "latest": {}}


def write_seeds_manifest(
    clients: dict[str, str],
    kinds: dict[str, str],
    skipped: dict[str, str],
    path: str | None = None,
    latest: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Persist which clients were seeded so the harness can assert after clear.

    ``latest`` is the official LATEST tag observed while choosing the seed.
    The upgrade harness compares it with the seed tag to detect a consensus
    binary downgrade.
    """
    client_map = {name.lower(): tag for name, tag in clients.items() if tag}
    payload = {
        "clients": client_map,
        "kinds": {name.lower(): kind for name, kind in kinds.items() if kind},
        "skipped": {name.lower(): reason for name, reason in skipped.items() if reason},
        "latest": {
            name.lower(): tag
            for name, tag in (latest or {}).items()
            if tag and name.lower() in client_map
        },
    }
    target = path or seeds_path()
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")
    return payload


def load_seeds_manifest(path: str | None = None) -> dict[str, Any]:
    """Load the persistent seeds manifest, or empty maps when absent."""
    target = path or seeds_path()
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return _empty_seeds()
    except (OSError, json.JSONDecodeError):
        return _empty_seeds()
    if not isinstance(data, dict):
        return _empty_seeds()
    clients = data.get("clients") if isinstance(data.get("clients"), dict) else {}
    kinds = data.get("kinds") if isinstance(data.get("kinds"), dict) else {}
    skipped = data.get("skipped") if isinstance(data.get("skipped"), dict) else {}
    latest = data.get("latest") if isinstance(data.get("latest"), dict) else {}
    client_map = {str(key).lower(): str(value) for key, value in clients.items() if value}
    return {
        "clients": client_map,
        "kinds": {str(key).lower(): str(value) for key, value in kinds.items() if value},
        "skipped": {str(key).lower(): str(value) for key, value in skipped.items() if value},
        "latest": {
            str(key).lower(): str(value)
            for key, value in latest.items()
            if value and str(key).lower() in client_map
        },
    }


def _manifest_seed_is_downgrade(client_key: str, path: str | None) -> bool:
    """Return True when *client_key*'s seed tag is semver-newer than recorded LATEST."""
    manifest = load_seeds_manifest(path)
    seed = (manifest.get("clients") or {}).get(client_key) or ""
    official = (manifest.get("latest") or {}).get(client_key) or ""
    if not seed or not official:
        return False
    return _seed_is_newer(seed, official)


def is_consensus_downgrade(client: str, path: str | None = None) -> bool:
    """Return True when upgrading *client* to official LATEST downgrades the BN.

    True only for a consensus client whose seed tag is semver-newer than the
    official LATEST stored in the seeds manifest (for example a newer
    Lighthouse seed vs an older LATEST). Previous-stable seeds are older, so they stay a
    forward upgrade and keep the beacon DB. Execution, MEV, and Charon seeds
    are ignored: this check only gates a consensus beacon wipe.
    """
    key = (client or "").strip().lower()
    if key not in CONSENSUS_CLIENTS:
        return False
    return _manifest_seed_is_downgrade(key, path)


def is_execution_downgrade(client: str, path: str | None = None) -> bool:
    """Return True when upgrading *client* to official LATEST downgrades the EL.

    Same semver rule as :func:`is_consensus_downgrade`, for an execution client
    (Reth ``v1.9.0-rc.1`` vs LATEST ``v1.8.0``). Previous-stable seeds keep
    their chaindata. Consensus, MEV, and Charon seeds are ignored: this check
    only gates an execution datadir wipe.
    """
    key = (client or "").strip().lower()
    if key not in EXECUTION_CLIENTS:
        return False
    return _manifest_seed_is_downgrade(key, path)


def is_harness_downgrade(client: str, path: str | None = None) -> bool:
    """Return True when a consensus or execution seed is newer than official LATEST."""
    return is_consensus_downgrade(client, path) or is_execution_downgrade(client, path)


def clear_override(path: str | None = None) -> None:
    """Remove the override file so LATEST resolves officially again."""
    target = override_path(path)
    try:
        os.remove(target)
    except FileNotFoundError:
        pass


def remap_latest_tag(repo: str, version_tag: str, path: str | None = None) -> str:
    """Return the forced seed tag when *version_tag* is LATEST and *repo* is overridden."""
    if not version_tag or version_tag.upper() != "LATEST":
        return version_tag
    mapping = load_override(path).get("repos") or {}
    return mapping.get(repo) or version_tag


def forced_rc_tag(client: str, path: str | None = None) -> str | None:
    """Return the forced seed tag for *client*, or ``None`` when not overridden."""
    key = (client or "").lower()
    if not key:
        return None
    return load_override(path).get("clients", {}).get(key)


forced_seed_tag = forced_rc_tag


def _seed_from_row(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(seed_tag, seed_kind)`` from a finder row (RC-compat included)."""
    seed_tag = row.get("seed_tag") or row.get("rc_tag")
    if not seed_tag:
        return None, None
    seed_kind = row.get("seed_kind")
    if seed_kind in {"rc", "stable"}:
        return str(seed_tag), str(seed_kind)
    # Legacy find_rc rows only set rc_tag.
    if row.get("rc_tag"):
        return str(seed_tag), "rc"
    return str(seed_tag), None


def normalize_deploy_clients(
    *names: str,
    mev: bool = False,
    charon: bool = False,
) -> list[str]:
    """Lowercase GitHub-released client names from deploy args (skip Caplin/empty)."""
    from find_client_rc import CLIENT_REPOS

    out: list[str] = []
    for raw in names:
        key = (raw or "").strip().lower()
        if key in _SKIP_NAMES or key not in CLIENT_REPOS:
            continue
        if key not in out:
            out.append(key)
    if mev and "mevboost" not in out:
        out.append("mevboost")
    if charon and "charon" not in out:
        out.append("charon")
    return out


def log_rc_override_plan(rows: Iterable[dict[str, Any]]) -> None:
    """Print a CI-readable summary of which clients use a seed vs official LATEST."""
    print("=========================================", flush=True)
    print(" Upgrade-case seed deploy override", flush=True)
    print("=========================================", flush=True)
    printed = False
    for row in rows:
        printed = True
        client = row.get("client") or "unknown"
        status = row.get("status") or "skip"
        latest = str(row.get("latest") or "")
        seed_tag, seed_kind = _seed_from_row(row)
        reason = row.get("reason") or ""
        latest_label = latest or "?"
        if status == "ok" and seed_tag:
            kind_label = "RC" if seed_kind == "rc" else "previous stable"
            print(
                f"  {client}: {kind_label} {seed_tag} (official LATEST is {latest_label}) "
                f"— deploy will install seed, then upgrade to LATEST",
                flush=True,
            )
            client_key = str(client).strip().lower()
            wipe_note = ""
            if client_key in CONSENSUS_CLIENTS:
                wipe_note = "wipe the beacon DB before starting the older binary"
            elif client_key in EXECUTION_CLIENTS:
                wipe_note = "wipe execution chaindata before starting the older binary"
            if (
                wipe_note
                and seed_kind == "rc"
                and latest
                and _seed_is_newer(str(seed_tag), latest)
            ):
                print(
                    f"    {client}: binary downgrade vs LATEST — upgrade harness will "
                    f"{wipe_note}",
                    flush=True,
                )
        else:
            detail = reason or "no resolvable RC or previous stable"
            print(
                f"  {client}: no sane upgrade seed ({detail}) — deploy official LATEST {latest_label} "
                f"(real-upgrade assert will soft-skip)",
                flush=True,
            )
    if not printed:
        print("  (no clients to consider)", flush=True)
    print("=========================================", flush=True)


def _seed_is_newer(seed_tag: str, latest: str) -> bool:
    """Return True when *seed_tag* is semver-newer than official *latest*."""
    from find_client_rc import is_newer_than_latest

    return is_newer_than_latest(seed_tag, latest)


def _accept_seed(seed_tag: str, seed_kind: str | None, latest: str) -> bool:
    """Defense in depth: RC must be newer than LATEST; stable must be older."""
    from find_client_rc import is_newer_than_latest, is_strictly_older_than_latest

    if not seed_tag or not latest or seed_tag == latest:
        return False
    if seed_kind == "stable":
        return is_strictly_older_than_latest(seed_tag, latest)
    # Default / legacy RC rows.
    return is_newer_than_latest(seed_tag, latest)


def _network_rejects_seed(
    client: str,
    seed_tag: str,
    seed_kind: str | None,
    latest: str,
    network: str,
) -> str | None:
    """Return a skip reason when *seed_tag* is unsafe for *network*, else ``None``.

    On Sepolia Gloas: reject seeds below the client minimum-version floor.
    """
    import sys

    repo_root = os.path.dirname(os.path.dirname(_INTEGRATION_DIR))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from client_requirements import validate_version_for_network

    net = (network or "").strip().lower()
    if net != "sepolia":
        return None

    # seed_kind / latest kept for call-site compatibility; only the seed floor matters.
    _ = (seed_kind, latest)

    ok_seed, _ = validate_version_for_network(client, seed_tag, net)
    if not ok_seed:
        return (
            f"seed {seed_tag} does not meet Sepolia Gloas floor "
            f"(soft-skip; deploy official LATEST)"
        )
    return None


def prepare_rc_overrides(
    clients: list[str],
    path: str | None = None,
    find_rc_fn: Callable[[str, str | None], dict[str, Any]] | None = None,
    seeds_dest: str | None = None,
    network: str | None = None,
) -> dict[str, Any]:
    """Discover upgrade seeds for *clients* and write the LATEST remap file.

    Defaults to :func:`find_client_rc.find_upgrade_seed` (RC-if-newer, else
    previous stable). Clients with ``status != ok`` or no usable seed are
    omitted so deploy stays on official LATEST.

    When *network* is Sepolia, seeds that fail the Gloas floor are soft-skipped.

    Returns a dict with ``rows``, ``clients``, ``repos``, ``kinds``,
    ``latest`` (official LATEST at seed time), ``skipped``, and ``payload``.
    """
    from find_client_rc import CLIENT_REPOS, find_upgrade_seed
    from known_failures import emit_soft_skip

    # Discover against official LATEST; a leftover override would remap LATEST
    # and make the finder skip the candidate as "same as latest".
    clear_override(path)

    net = (network or os.environ.get("NETWORK") or "").strip()
    finder = find_rc_fn or find_upgrade_seed
    rows: list[dict[str, Any]] = []
    client_map: dict[str, str] = {}
    kind_map: dict[str, str] = {}
    latest_map: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for client in clients:
        client = client.lower()
        try:
            row = finder(client, CLIENT_REPOS.get(client))
        except Exception as exc:  # noqa: BLE001 — surface per-client and continue
            row = {
                "client": client,
                "rc_tag": None,
                "seed_tag": None,
                "latest": None,
                "status": "skip",
                "reason": f"error: {exc}",
            }
        seed_tag, seed_kind = _seed_from_row(row)
        latest = str(row.get("latest") or "")
        reject = None
        if (
            row.get("status") == "ok"
            and seed_tag
            and _accept_seed(str(seed_tag), seed_kind, latest)
        ):
            reject = _network_rejects_seed(
                client, str(seed_tag), seed_kind, latest, net
            )
        if reject:
            row = {
                **row,
                "status": "skip",
                "reason": reject,
                "seed_tag": None,
                "rc_tag": None,
                "seed_kind": None,
            }
            seed_tag, seed_kind = None, None
        rows.append(row)
        if row.get("status") == "ok" and seed_tag and _accept_seed(str(seed_tag), seed_kind, latest):
            client_map[client] = str(seed_tag)
            if seed_kind:
                kind_map[client] = seed_kind
            if latest:
                latest_map[client] = latest
        else:
            skip_reason = str(row.get("reason") or "no usable upgrade seed")
            skipped[client] = skip_reason
            emit_soft_skip(client, skip_reason)

    log_rc_override_plan(rows)
    manifest_path = seeds_dest or seeds_path(override=override_path(path))
    write_seeds_manifest(client_map, kind_map, skipped, path=manifest_path, latest=latest_map)

    if not client_map:
        clear_override(path)
        print(
            "[Upgrade seed] No RC-newer-than-LATEST or sane previous stable — "
            "deploy stays on official LATEST (real-upgrade assert will soft-skip)",
            flush=True,
        )
        return {
            "rows": rows,
            "clients": {},
            "repos": {},
            "kinds": {},
            "latest": {},
            "skipped": skipped,
            "payload": _empty_override(),
        }

    payload = write_override(client_map, path=path, kinds=kind_map)
    print(
        f"[Upgrade seed] Wrote LATEST override for {', '.join(sorted(client_map))} "
        f"at {override_path(path)}",
        flush=True,
    )
    return {
        "rows": rows,
        "clients": payload["clients"],
        "repos": payload["repos"],
        "kinds": payload.get("kinds") or {},
        "latest": latest_map,
        "skipped": skipped,
        "payload": payload,
    }


prepare_upgrade_seeds = prepare_rc_overrides


def install_github_release_hook() -> bool:
    """Wrap GitHub + Geth release lookups so LATEST can remap to a seed tag.

    The wrappers read the override file on every call (no-op when absent).
    Idempotent. Returns True when the GitHub hook is installed.
    """
    _ensure_repo_on_path()
    hooked = False
    try:
        import deploy.common as common
    except ImportError:
        common = None  # type: ignore[assignment]

    if common is not None:
        current = common.get_github_release
        if getattr(current, _HOOK_ATTR, False):
            hooked = True
        else:

            def wrapped(repo: str, version_tag: str) -> dict:
                remapped = remap_latest_tag(repo, version_tag)
                if remapped != version_tag:
                    print(
                        f"[Upgrade seed] get_github_release({repo!r}, 'LATEST') → {remapped}",
                        flush=True,
                    )
                return current(repo, remapped)

            setattr(wrapped, _HOOK_ATTR, True)
            common.get_github_release = wrapped
            hooked = True

    try:
        import deploy.geth as geth
    except ImportError:
        return hooked

    geth_current = geth.get_release_info
    if getattr(geth_current, _GETH_HOOK_ATTR, False):
        return hooked

    def geth_wrapped(version_tag: str, arch_amd64: bool) -> dict:
        if version_tag and version_tag.upper() == "LATEST":
            seed = forced_seed_tag("geth")
            if seed:
                print(
                    f"[Upgrade seed] deploy.geth.get_release_info('LATEST') → {seed}",
                    flush=True,
                )
                return geth_current(seed, arch_amd64)
        return geth_current(version_tag, arch_amd64)

    setattr(geth_wrapped, _GETH_HOOK_ATTR, True)
    geth.get_release_info = geth_wrapped
    return hooked


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Upgrade-case LATEST→seed overrides")
    parser.add_argument(
        "action",
        choices=("prepare", "clear", "show", "seeds", "downgrade"),
        help="prepare: discover seeds and write override; clear: remove remap file; "
        "show: print remap file; seeds: print persistent seeds manifest; "
        "downgrade: print yes when upgrading that consensus or execution client "
        "to LATEST would install an older binary",
    )
    parser.add_argument("clients", nargs="*", help="Client names for prepare (default: none)")
    parser.add_argument(
        "--path",
        default=None,
        help=f"Override file path (default: ${ENV_VAR} or {OVERRIDE_PATH})",
    )
    parser.add_argument(
        "--seeds",
        default=None,
        help=f"Seeds manifest for downgrade (default: ${SEEDS_ENV} or {SEEDS_PATH})",
    )
    args = parser.parse_args()
    target = override_path(args.path)
    if args.action == "prepare":
        prepare_rc_overrides(args.clients, path=target)
    elif args.action == "clear":
        clear_override(target)
    elif args.action == "seeds":
        data = load_seeds_manifest(seeds_path(override=target))
        json.dump(data, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    elif args.action == "downgrade":
        if len(args.clients) != 1:
            print("downgrade requires exactly one client name", file=sys.stderr)
            return 2
        print("yes" if is_harness_downgrade(args.clients[0], path=args.seeds) else "no")
    else:
        data = load_override(target)
        json.dump(data, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
