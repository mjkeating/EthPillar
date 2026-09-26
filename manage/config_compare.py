"""Compare installed systemd units against EthPillar-generated defaults.

Prepares canonicalized installed vs default trees, launches ``tmeld`` for
side-by-side review/merge, and applies saved left-pane edits back to
``/etc/systemd/system/`` with optional ``.bak`` backups.

``prepare-prune-suggest`` is a thin sibling of ``prepare``: it copies the
installed ``execution.service`` to the left pane and merges suggested prune
flags into ExecStart only on the right. It does **not** regenerate EthPillar
defaults.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from dotenv import load_dotenv

import config
import deploy.besu as besu
import deploy.charon as charon
import deploy.erigon as erigon
import deploy.ethrex as ethrex
import deploy.geth as geth
import deploy.grandine as grandine
import deploy.lighthouse as lighthouse
import deploy.lodestar as lodestar
import deploy.mevboost as mevboost
import deploy.nethermind as nethermind
import deploy.nimbus as nimbus
import deploy.prysm as prysm
import deploy.reth as reth
import deploy.teku as teku
from deploy.common import write_service_file
from deploy.orchestrator import _with_dvt_params
from deploy.vc_service import BEACON_FLAG_BY_VC, scrape_beacon_endpoint
from manage.service_parse import (
    SERVICE_FILES,
    canonicalize_unit,
    get_flag_value,
    has_flag,
    installed_service_paths,
    parse_unit,
    read_text_file,
    semantic_equal,
    unit_exists,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_DIFF = 2

_META_NAME = "compare_meta.json"


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env() -> Dict[str, str]:
    """Load ``env`` then ``.env.overrides`` into a flat dict (and os.environ)."""
    root = _repo_root()
    env_file = os.getenv("ETHPILLAR_ENV_FILE", os.path.join(root, "env"))
    load_dotenv(env_file)
    overrides = os.path.join(root, ".env.overrides")
    if os.path.isfile(overrides):
        load_dotenv(overrides, override=True)
    return dict(os.environ)


def _env_or(env: Dict[str, str], key: str, *fallbacks: str, default: str = "") -> str:
    value = (env.get(key) or "").strip()
    if value:
        return value
    for fb in fallbacks:
        if fb and str(fb).strip():
            return str(fb).strip()
    return default


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _detect_mev_enabled(paths: Dict[str, str], consensus_content: Optional[str]) -> bool:
    if "mevboost" in paths:
        return True
    if not consensus_content:
        return False
    unit = parse_unit(consensus_content)
    return has_flag(
        unit.exec_args,
        "--builder",
        "--builder-url",
        "--builder.urls",
        "--payload-builder",
        "--payload-builder-url",
        "--builder-endpoint",
        "--http-mev-relay",
        "--caplin.mev-relay-url",
    )


def _fee_params_for_client(client: str, fee_recipient: str, role: str) -> str:
    if not fee_recipient:
        return ""
    if role == "bn":
        mapping = {
            "Nimbus": f"--suggested-fee-recipient={fee_recipient}",
            "Teku": f"--validators-proposer-default-fee-recipient={fee_recipient}",
            "Lodestar": f"--suggestedFeeRecipient={fee_recipient}",
            "Grandine": f"--suggested-fee-recipient={fee_recipient}",
            "Prysm": f"--suggested-fee-recipient={fee_recipient}",
        }
        return mapping.get(client, "")
    mapping = {
        "Lighthouse": f"--suggested-fee-recipient={fee_recipient}",
        "Nimbus": f"--suggested-fee-recipient={fee_recipient}",
        "Teku": f"--validators-proposer-default-fee-recipient={fee_recipient}",
        "Lodestar": f"--suggestedFeeRecipient={fee_recipient}",
        "Prysm": f"--suggested-fee-recipient={fee_recipient}",
    }
    return mapping.get(client, "")


def _mev_params_for_client(client: str, role: str, enabled: bool) -> str:
    if not enabled:
        return ""
    if role == "bn":
        mapping = {
            "Lighthouse": "--builder http://127.0.0.1:18550",
            "Nimbus": "--payload-builder=true --payload-builder-url=http://127.0.0.1:18550",
            "Teku": "--validators-builder-registration-default-enabled=true --builder-endpoint=http://127.0.0.1:18550",
            "Lodestar": "--builder --builder.urls http://127.0.0.1:18550",
            "Grandine": "--builder-url=http://127.0.0.1:18550",
            "Prysm": "--http-mev-relay=http://127.0.0.1:18550",
            "Erigon-Caplin": "--caplin.mev-relay-url=http://127.0.0.1:18550",
        }
        return mapping.get(client, "")
    mapping = {
        "Lighthouse": "--builder-proposals",
        "Nimbus": "--payload-builder=true",
        "Teku": "--validators-builder-registration-default-enabled=true",
        "Lodestar": "--builder",
        "Prysm": "--enable-builder",
    }
    return mapping.get(client, "")


def _scrape_fee_recipient(args: Sequence[str]) -> str:
    return get_flag_value(
        args,
        "--suggested-fee-recipient",
        "--suggestedFeeRecipient",
        "--validators-proposer-default-fee-recipient",
    )


def _scrape_sync_url(args: Sequence[str]) -> str:
    return get_flag_value(
        args,
        "--checkpoint-sync-url",
        "--checkpointSyncUrl",
        "--checkpoint-sync-url",
    )


def _beacon_flag_arg(vc_client: str, endpoint: str) -> str:
    flag = BEACON_FLAG_BY_VC.get(vc_client)
    if not flag:
        return f"--beacon-nodes={endpoint}"
    return f"{flag}={endpoint}"


def _resolve_context(env: Dict[str, str], paths: Dict[str, str]) -> Dict[str, object]:
    """Build generation context from env + installed units."""
    contents: Dict[str, str] = {}
    for key, path in paths.items():
        raw = read_text_file(path)
        if raw is None:
            raise RuntimeError(f"Cannot read {path}")
        contents[key] = raw

    network = ""
    for key in ("consensus", "execution", "validator", "charon", "mevboost"):
        if key in contents:
            network = parse_unit(contents[key]).network
            if network:
                break
    network = network or "mainnet"

    el_client = parse_unit(contents["execution"]).client if "execution" in contents else ""
    cl_client = parse_unit(contents["consensus"]).client if "consensus" in contents else ""
    vc_client = parse_unit(contents["validator"]).client if "validator" in contents else ""

    cons_args = parse_unit(contents["consensus"]).exec_args if "consensus" in contents else []
    val_args = parse_unit(contents["validator"]).exec_args if "validator" in contents else []
    exec_args = parse_unit(contents["execution"]).exec_args if "execution" in contents else []

    fee_recipient = _env_or(
        env,
        "FEE_RECIPIENT_ADDRESS",
        _scrape_fee_recipient(val_args),
        _scrape_fee_recipient(cons_args),
    )
    graffiti = _env_or(
        env,
        "GRAFFITI",
        get_flag_value(val_args, "--graffiti"),
        default="EthPillar",
    )
    jwtsecret = _env_or(
        env,
        "JWTSECRET_PATH",
        get_flag_value(
            cons_args,
            "--execution-jwt",
            "--jwt-secret",
            "--jwt-secret",
            "--ee-jwt-secret-file",
        ),
        get_flag_value(exec_args, "--authrpc.jwtsecret", "--engine-jwt-secret", "--JsonRpc.JwtSecretFile"),
        default="/secrets/jwtsecret",
    )
    sync_url = _scrape_sync_url(cons_args)
    if not sync_url and "consensus" in contents:
        # Nimbus keeps its checkpoint URL in the trustedNodeSync ExecStartPre.
        m = re.search(r"--trusted-node-url=\"?([^\s'\"]+)", contents["consensus"])
        sync_url = m.group(1) if m else ""
    if not sync_url:
        sync_urls = getattr(config, f"{network}_sync_urls", [])
        if sync_urls:
            sync_url = sync_urls[0][1]

    el_p2p = _env_or(env, "EL_P2P_PORT", get_flag_value(exec_args, "--port", "--p2p-port", "--p2p.port", "--Network.P2PPort"), default="30303")
    el_p2p_2 = _env_or(env, "EL_P2P_PORT_2", default="30304")
    el_rpc = _env_or(env, "EL_RPC_PORT", get_flag_value(exec_args, "--http.port", "--rpc-http-port", "--JsonRpc.Port", "--http.port"), default="8545")
    el_peers = _env_or(env, "EL_MAX_PEER_COUNT", get_flag_value(exec_args, "--maxpeers", "--max-peers", "--Network.MaxActivePeers"), default="50")
    cl_p2p = _env_or(env, "CL_P2P_PORT", get_flag_value(cons_args, "--port", "--p2p-port", "--tcp-port", "--libp2p-port", "--p2p-tcp-port"), default="9000")
    cl_p2p_2 = _env_or(env, "CL_P2P_PORT_2", get_flag_value(cons_args, "--quic-port", "--quicPort", "--p2p-quic-port", "--p2p-udp-port", "--discovery-port"), default="9001")
    cl_rest = _env_or(env, "CL_REST_PORT", get_flag_value(cons_args, "--http-port", "--rest-port", "--rest.port", "--rest-api-port"), default="5052")
    cl_peers = _env_or(env, "CL_MAX_PEER_COUNT", get_flag_value(cons_args, "--target-peers", "--max-peers", "--targetPeers", "--p2p-peer-upper-bound", "--p2p-max-peers"), default="100")
    mev_min_bid = _env_or(env, "MEV_MIN_BID", get_flag_value(parse_unit(contents["mevboost"]).exec_args, "-min-bid") if "mevboost" in contents else "", default="0.006")

    mev_enabled = _detect_mev_enabled(paths, contents.get("consensus"))

    bn_endpoint = ""
    if "validator" in contents and vc_client in BEACON_FLAG_BY_VC:
        bn_endpoint = scrape_beacon_endpoint(contents["validator"], vc_client) or ""
    if not bn_endpoint:
        if "charon" in paths:
            bn_endpoint = charon.DEFAULT_VALIDATOR_API_URL
        else:
            cl_ip = env.get("CL_IP_ADDRESS", "127.0.0.1")
            bn_endpoint = f"http://{cl_ip}:{cl_rest}"

    is_integrated_grandine = (
        cl_client == "Grandine"
        and "validator" not in contents
        and has_flag(cons_args, "--keystore-dir")
    )

    return {
        "network": network,
        "el_client": el_client,
        "cl_client": cl_client,
        "vc_client": vc_client,
        "fee_recipient": fee_recipient,
        "graffiti": graffiti,
        "jwtsecret": jwtsecret,
        "sync_url": sync_url,
        "el_p2p": str(el_p2p),
        "el_p2p_2": str(el_p2p_2),
        "el_rpc": str(el_rpc),
        "el_peers": str(el_peers),
        "cl_p2p": str(cl_p2p),
        "cl_p2p_2": str(cl_p2p_2),
        "cl_rest": str(cl_rest),
        "cl_peers": str(cl_peers),
        "mev_min_bid": str(mev_min_bid),
        "mev_enabled": mev_enabled,
        "bn_endpoint": bn_endpoint,
        "is_integrated_grandine": is_integrated_grandine,
        "contents": contents,
    }


def generate_default_unit(service_key: str, ctx: Dict[str, object]) -> str:
    """Generate EthPillar default unit content for *service_key*."""
    network = str(ctx["network"])
    jwt = str(ctx["jwtsecret"])
    sync_url = str(ctx["sync_url"])
    fee = str(ctx["fee_recipient"])
    graffiti = str(ctx["graffiti"])
    mev = bool(ctx["mev_enabled"])

    if service_key == "mevboost":
        relays = getattr(config, f"{network}_relay_options", [])
        return mevboost.generate_mevboost_service(network, str(ctx["mev_min_bid"]), relays)

    if service_key == "execution":
        el = str(ctx["el_client"])
        el_p2p, el_rpc, el_peers = str(ctx["el_p2p"]), str(ctx["el_rpc"]), str(ctx["el_peers"])
        if el == "Geth":
            return geth.generate_geth_service(network, el_p2p, el_rpc, el_peers, jwt)
        if el == "Besu":
            return besu.generate_besu_service(network, el_p2p, el_rpc, el_peers, jwt)
        if el == "Nethermind":
            sync_params = getattr(config, f"{network}_nethermind_sync_parameters", "")
            return nethermind.generate_nethermind_service(
                network, el_p2p, el_rpc, el_peers, jwt, sync_parameters=sync_params
            )
        if el == "Reth":
            return reth.generate_reth_service(
                network, el_p2p, str(ctx["el_p2p_2"]), el_rpc, el_peers, jwt
            )
        if el == "Ethrex":
            return ethrex.generate_ethrex_service(network, el_p2p, el_rpc, el_peers, jwt)
        if el in ("Erigon", "Erigon-Caplin"):
            # Caplin integrated lives on execution.service with a Caplin-ish description.
            if el == "Erigon-Caplin" or (
                "consensus" not in ctx["contents"]  # type: ignore[operator]
                and "Caplin" in parse_unit(ctx["contents"]["execution"]).description  # type: ignore[index]
            ):
                mev_params = _mev_params_for_client("Erigon-Caplin", "bn", mev)
                return erigon.generate_erigon_service(
                    network,
                    el_p2p,
                    el_rpc,
                    el_peers,
                    jwt,
                    str(ctx["cl_p2p"]),
                    str(ctx["cl_rest"]),
                    str(ctx["cl_peers"]),
                    sync_url,
                    mev_parameters=mev_params,
                )
            return erigon.generate_erigon_standalone_service(
                network, el_p2p, el_rpc, el_peers, jwt
            )
        raise RuntimeError(f"Unsupported execution client for compare: {el!r}")

    if service_key == "consensus":
        cl = str(ctx["cl_client"])
        if cl == "Erigon-Caplin":
            # Caplin is folded into execution.service; no separate consensus default.
            raise RuntimeError("Erigon-Caplin has no separate consensus.service")
        fee_params = _fee_params_for_client(cl, fee, "bn")
        mev_params = _mev_params_for_client(cl, "bn", mev)
        cl_rest, cl_p2p, cl_p2p_2, cl_peers = (
            str(ctx["cl_rest"]),
            str(ctx["cl_p2p"]),
            str(ctx["cl_p2p_2"]),
            str(ctx["cl_peers"]),
        )
        if cl == "Lighthouse":
            return lighthouse.generate_lighthouse_bn_service(
                network, sync_url, jwt, cl_rest, cl_p2p, cl_p2p_2, cl_peers,
                fee_parameters=fee_params, mev_parameters=mev_params,
            )
        if cl == "Nimbus":
            return nimbus.generate_nimbus_bn_service(
                network, jwt, cl_rest, cl_p2p, cl_p2p_2, cl_peers,
                fee_parameters=fee_params, mev_parameters=mev_params,
                sync_url=sync_url,
            )
        if cl == "Teku":
            if Path("/etc/systemd/system/charon.service").is_file():
                fee_params = (
                    f"{fee_params} --validators-graffiti-client-append-format=DISABLED".strip()
                )
            return teku.generate_teku_bn_service(
                network, sync_url, jwt, cl_rest, cl_p2p, cl_peers,
                fee_parameters=fee_params, mev_parameters=mev_params,
            )
        if cl == "Lodestar":
            return lodestar.generate_lodestar_bn_service(
                network, sync_url, jwt, cl_rest, cl_p2p, cl_p2p_2, cl_peers,
                fee_parameters=fee_params, mev_parameters=mev_params,
            )
        if cl == "Grandine":
            return grandine.generate_grandine_bn_service(
                network, sync_url, jwt, cl_rest, cl_p2p, cl_p2p_2, cl_peers,
                fee_parameters=fee_params, mev_parameters=mev_params,
                is_integrated_vc=bool(ctx["is_integrated_grandine"]),
            )
        if cl == "Prysm":
            return prysm.generate_prysm_bn_service(
                network, sync_url, jwt, cl_rest, cl_p2p, cl_p2p_2, cl_peers,
                fee_parameters=fee_params, mev_parameters=mev_params,
            )
        raise RuntimeError(f"Unsupported consensus client for compare: {cl!r}")

    if service_key == "validator":
        vc = str(ctx["vc_client"])
        fee_params = _fee_params_for_client(vc, fee, "vc")
        extra_params = _mev_params_for_client(vc, "vc", mev)
        # Match install: Charon adds per-VC DVT flags and After=charon.service.
        contents = ctx.get("contents") or {}
        charon_enabled = (
            "charon" in contents  # type: ignore[operator]
            or Path("/etc/systemd/system/charon.service").is_file()
        )
        extra_params = _with_dvt_params(extra_params, vc, charon_enabled)
        vc_unit_after = ["charon.service"] if charon_enabled else None
        bn_arg = _beacon_flag_arg(vc, str(ctx["bn_endpoint"]))
        if vc == "Lighthouse":
            return lighthouse.generate_lighthouse_vc_service(
                network, graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )
        if vc == "Nimbus":
            return nimbus.generate_nimbus_vc_service(
                network, graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )
        if vc == "Teku":
            return teku.generate_teku_vc_service(
                network, graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )
        if vc == "Lodestar":
            return lodestar.generate_lodestar_vc_service(
                network, graffiti, bn_arg, fee_params, extra_params,
                unit_after=vc_unit_after,
            )
        if vc == "Prysm":
            cl = str(ctx["cl_client"])
            beacon_rpc = "127.0.0.1:4000" if cl == "Prysm" and not charon_enabled else None
            return prysm.generate_prysm_vc_service(
                network,
                graffiti,
                bn_arg,
                fee_params,
                extra_params,
                beacon_rpc_provider=beacon_rpc,
                unit_after=vc_unit_after,
            )
        raise RuntimeError(f"Unsupported validator client for compare: {vc!r}")

    if service_key == "charon":
        ch_content = str(ctx["contents"].get("charon", ""))
        ch_args = parse_unit(ch_content).exec_args if ch_content else []
        cl_rest = str(ctx["cl_rest"])
        beacon = charon.scrape_beacon_endpoints(ch_content) or ""
        if not beacon:
            cl_ip = os.environ.get("CL_IP_ADDRESS", "127.0.0.1")
            beacon = f"http://{cl_ip}:{cl_rest}"
        return charon.generate_charon_service(
            network,
            beacon,
            builder_api=has_flag(ch_args, "--builder-api"),
            p2p_external_ip=get_flag_value(ch_args, "--p2p-external-ip"),
            validator_api_address=get_flag_value(
                ch_args, "--validator-api-address", default=charon.DEFAULT_VALIDATOR_API_ADDRESS
            ),
            monitoring_address=get_flag_value(
                ch_args, "--monitoring-address", default=charon.DEFAULT_MONITORING_ADDRESS
            ),
            p2p_tcp_address=get_flag_value(
                ch_args, "--p2p-tcp-address", default=charon.DEFAULT_P2P_TCP_ADDRESS
            ),
            feature_set_enable=get_flag_value(ch_args, "--feature-set-enable"),
        )

    raise RuntimeError(f"Unknown service key: {service_key!r}")


def prepare_workdir(workdir: Path) -> Tuple[List[str], Dict[str, str]]:
    """Generate compare trees under *workdir*.

    Returns:
        ``(differing_service_keys, meta)``
    """
    env = _load_env()
    paths = installed_service_paths()
    if not paths:
        raise RuntimeError("No EthPillar systemd units found under /etc/systemd/system/")

    ctx = _resolve_context(env, paths)
    installed_dir = workdir / "installed"
    default_dir = workdir / "default"
    installed_dir.mkdir(parents=True, exist_ok=True)
    default_dir.mkdir(parents=True, exist_ok=True)

    differing: List[str] = []
    pre_hashes: Dict[str, str] = {}

    for key, path in paths.items():
        installed_raw = ctx["contents"][key]  # type: ignore[index]
        try:
            default_raw = generate_default_unit(key, ctx)
        except RuntimeError as exc:
            print(f"Skipping {key}: {exc}", file=sys.stderr)
            continue

        if semantic_equal(installed_raw, default_raw):
            continue

        installed_canon = canonicalize_unit(installed_raw)
        default_canon = canonicalize_unit(default_raw)
        (installed_dir / f"{key}.service").write_text(installed_canon, encoding="utf-8")
        default_path = default_dir / f"{key}.service"
        default_path.write_text(default_canon, encoding="utf-8")
        # Keep default writable: tmeld saves the focused pane, and a
        # PermissionError on Ctrl+S (right pane) crashes the UI. Apply only
        # reads installed/; accidental saves to default/ are discarded.
        pre_hashes[key] = _sha256(installed_canon)
        differing.append(key)

    meta = {
        "differing": differing,
        "pre_hashes": pre_hashes,
        "system_paths": {k: paths[k] for k in differing},
    }
    (workdir / _META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return differing, meta


def _load_meta(workdir: Path) -> dict:
    meta_path = workdir / _META_NAME
    if not meta_path.is_file():
        raise RuntimeError(f"Missing compare metadata at {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def find_tmeld() -> Optional[str]:
    """Locate the ``tmeld`` executable (PATH or EthPillar venv)."""
    which = shutil.which("tmeld")
    if which:
        return which
    venv = os.getenv("ETHPILLAR_VENV") or os.path.join(_repo_root(), ".venv")
    candidate = os.path.join(venv, "bin", "tmeld")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def _run_history_expiry(fn: str, *args: str) -> str:
    """Call a function from helpers/history_expiry_suggestions.sh."""
    helper = os.path.join(_repo_root(), "helpers", "history_expiry_suggestions.sh")
    completed = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" && shift && fn="$1" && shift && "$fn" "$@"',
            "bash",
            helper,
            fn,
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or f"{fn} failed").strip()
        raise RuntimeError(err)
    return completed.stdout


def _detect_el_client(unit_text: str) -> str:
    """Detect the execution client using the history-expiry helper."""
    parsed = parse_unit(unit_text)
    execstart = " ".join(parsed.exec_args)
    return _run_history_expiry(
        "history_expiry_detect_client", parsed.description, execstart
    ).strip()


def merge_prune_flags_into_unit(unit_text: str, flags: str) -> str:
    """Merge *flags* into ExecStart only via the history-expiry helper."""
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".service",
        prefix="ethpillar-prune-",
        delete=False,
        encoding="utf-8",
    ) as handle:
        handle.write(unit_text)
        path = handle.name
    try:
        return _run_history_expiry("history_expiry_merge_unit_file", path, flags)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def prepare_prune_suggest_workdir(
    workdir: Path,
    *,
    unit_path: Optional[str] = None,
    level: str = "recommended",
    flags: str = "",
) -> Tuple[List[str], Dict[str, object]]:
    """Write execution.service left=installed, right=ExecStart prune merge.

    Unlike :func:`prepare_workdir`, this does not generate EthPillar defaults.
    Left is an exact copy of the installed unit (the apply contract).
    """
    path = (
        unit_path
        or os.environ.get("EXEC_SERVICE_FILE")
        or SERVICE_FILES["execution"]
    )
    raw = read_text_file(path)
    if raw is None:
        raise RuntimeError(f"Cannot read execution unit at {path}")

    merge_flags = (flags or "").strip()
    if not merge_flags:
        client = _detect_el_client(raw)
        if not client:
            raise RuntimeError("Could not detect the installed execution client")
        merge_flags = _run_history_expiry(
            "history_expiry_flags_for_level", client, level
        ).strip()
    if not merge_flags:
        raise RuntimeError("No prune flags to merge for this client/level")

    merged = merge_prune_flags_into_unit(raw, merge_flags)
    workdir.mkdir(parents=True, exist_ok=True)
    installed_dir = workdir / "installed"
    default_dir = workdir / "default"
    installed_dir.mkdir(parents=True, exist_ok=True)
    default_dir.mkdir(parents=True, exist_ok=True)

    if merged == raw or merged.rstrip("\n") == raw.rstrip("\n"):
        meta: Dict[str, object] = {
            "differing": [],
            "pre_hashes": {},
            "system_paths": {},
            "mode": "prune-suggest",
        }
        (workdir / _META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return [], meta

    dest = installed_dir / "execution.service"
    dest.write_text(raw, encoding="utf-8")
    (default_dir / "execution.service").write_text(merged, encoding="utf-8")
    meta = {
        "differing": ["execution"],
        "pre_hashes": {"execution": _sha256(raw)},
        "system_paths": {"execution": path},
        "mode": "prune-suggest",
        "left_label": "installed unit (this is what gets applied)",
        "right_label": "same unit with suggested prune flags on ExecStart",
    }
    (workdir / _META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return ["execution"], meta


def cmd_prepare_prune_suggest(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    differing, _meta = prepare_prune_suggest_workdir(
        workdir,
        unit_path=args.unit or None,
        level=args.level,
        flags=args.flags or "",
    )
    if not differing:
        print("Selected prune flags are already present on ExecStart.")
        return EXIT_NO_DIFF
    print("Differing units: " + ", ".join(differing))
    print(f"Workdir: {workdir}")
    return EXIT_OK


def tmeld_pane_paths(
    workdir: Path, meta: Optional[Dict[str, object]] = None
) -> Tuple[str, str]:
    """Return left/right paths to pass to tmeld.

    A single differing unit opens the two ``.service`` files so the first
    screen is the content diff (used by prune-suggest). Multiple units keep
    the installed/ vs default/ folder compare.
    """
    if meta is None:
        meta = _load_meta(workdir)
    differing: List[str] = list(meta.get("differing") or [])
    installed_dir = workdir / "installed"
    default_dir = workdir / "default"
    if len(differing) == 1:
        key = str(differing[0])
        left = installed_dir / f"{key}.service"
        right = default_dir / f"{key}.service"
        if left.is_file() and right.is_file():
            return str(left), str(right)
    return str(installed_dir), str(default_dir)


def tmeld_command(
    workdir: Path,
    tmeld: str,
    meta: Optional[Dict[str, object]] = None,
) -> List[str]:
    """Build the tmeld argv for a prepared workdir (no subprocess)."""
    left, right = tmeld_pane_paths(workdir, meta)
    return [tmeld, left, right, "--show-line-numbers"]


def launch_tmeld(workdir: Path) -> int:
    """Open tmeld: file pair when one unit differs, else folder compare."""
    meta = _load_meta(workdir)
    differing: List[str] = meta.get("differing") or []
    if not differing:
        print("No differing units to compare.")
        return EXIT_NO_DIFF

    tmeld = find_tmeld()
    if not tmeld:
        raise RuntimeError(
            "tmeld is not installed. It should be pulled in via requirements.txt "
            "(ensure_python_deps). Install manually with: pip install tmeld"
        )

    left_label = str(meta.get("left_label") or "installed (what gets applied)")
    right_label = str(meta.get("right_label") or "EthPillar default (reference)")
    cmd = tmeld_command(workdir, tmeld, meta)
    print(f"Launching: {' '.join(cmd)}")
    print(f"Left = {left_label} | Right = {right_label}")
    print("Stay on LEFT: Alt+Left copies a chunk from right→left, then Ctrl+S.")
    print("Esc / Ctrl+Q to quit. (Saving the right pane is ignored on apply.)")
    return subprocess.call(cmd)


def list_changed(workdir: Path) -> List[str]:
    """Return service keys whose left-pane files changed since prepare."""
    meta = _load_meta(workdir)
    pre_hashes: Dict[str, str] = meta.get("pre_hashes") or {}
    changed: List[str] = []
    for key, before in pre_hashes.items():
        path = workdir / "installed" / f"{key}.service"
        if not path.is_file():
            continue
        after = _sha256(path.read_text(encoding="utf-8"))
        if after != before:
            changed.append(key)
    return changed


def apply_changes(workdir: Path, backup: bool = True) -> List[str]:
    """Write changed left-pane units back to systemd paths.

    Returns list of applied service keys.
    """
    meta = _load_meta(workdir)
    system_paths: Dict[str, str] = meta.get("system_paths") or {}
    changed = list_changed(workdir)
    applied: List[str] = []

    for key in changed:
        dest = system_paths.get(key) or SERVICE_FILES.get(key)
        if not dest:
            continue
        src = workdir / "installed" / f"{key}.service"
        content = src.read_text(encoding="utf-8")
        if backup and unit_exists(dest):
            bak = f"{dest}.bak"
            subprocess.run(["sudo", "cp", dest, bak], check=True)
            print(f"Backed up {dest} → {bak}")
        write_service_file(content, dest, temp_filename=f"{key}_compare_temp.service")
        print(f"Applied {key} → {dest}")
        applied.append(key)
    return applied


def cmd_prepare(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    differing, _meta = prepare_workdir(workdir)
    if not differing:
        print("All installed systemd units match EthPillar defaults (canonicalized).")
        return EXIT_NO_DIFF
    print("Differing units: " + ", ".join(differing))
    print(f"Workdir: {workdir}")
    return EXIT_OK


def cmd_launch(args: argparse.Namespace) -> int:
    return launch_tmeld(Path(args.workdir))


def cmd_list_changed(args: argparse.Namespace) -> int:
    changed = list_changed(Path(args.workdir))
    print(" ".join(changed))
    return EXIT_OK


def cmd_apply(args: argparse.Namespace) -> int:
    applied = apply_changes(Path(args.workdir), backup=not args.no_backup)
    if not applied:
        print("Nothing to apply.")
        return EXIT_OK
    print("Applied: " + ", ".join(applied))
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Prepare, then launch interactive tmeld; print WORKDIR=/CHANGED= (apply left to Bash/TUI)."""
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="ethpillar-compare-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"Workdir: {workdir}")
    rc = cmd_prepare(argparse.Namespace(workdir=str(workdir)))
    if rc == EXIT_NO_DIFF:
        return EXIT_NO_DIFF
    if rc != EXIT_OK:
        return rc
    launch_rc = launch_tmeld(workdir)
    changed = list_changed(workdir)
    print("CHANGED=" + " ".join(changed))
    # Preserve workdir path for the caller
    print(f"WORKDIR={workdir}")
    return EXIT_OK if launch_rc in (0, EXIT_NO_DIFF) else launch_rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare installed systemd units to EthPillar defaults (tmeld UI)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help="Build installed/ vs default/ trees")
    p_prep.add_argument("--workdir", required=True)
    p_prep.set_defaults(func=cmd_prepare)

    p_prune = sub.add_parser(
        "prepare-prune-suggest",
        help="Build installed/ vs ExecStart prune-merge for execution.service only",
    )
    p_prune.add_argument("--workdir", required=True)
    p_prune.add_argument(
        "--unit",
        default="",
        help="execution.service path (default: /etc/systemd/system/execution.service)",
    )
    p_prune.add_argument(
        "--level",
        choices=("recommended", "further"),
        default="recommended",
        help="Which helper flag set to merge when --flags is omitted",
    )
    p_prune.add_argument(
        "--flags",
        default="",
        help="Override flags to merge into ExecStart (skips helper lookup)",
    )
    p_prune.set_defaults(func=cmd_prepare_prune_suggest)

    p_launch = sub.add_parser("launch", help="Open tmeld on a prepared workdir")
    p_launch.add_argument("--workdir", required=True)
    p_launch.set_defaults(func=cmd_launch)

    p_changed = sub.add_parser("list-changed", help="Print services edited in left pane")
    p_changed.add_argument("--workdir", required=True)
    p_changed.set_defaults(func=cmd_list_changed)

    p_apply = sub.add_parser("apply", help="Write left-pane edits to /etc/systemd/system")
    p_apply.add_argument("--workdir", required=True)
    p_apply.add_argument("--no-backup", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    p_run = sub.add_parser("run", help="prepare + launch (prints WORKDIR= / CHANGED=)")
    p_run.add_argument("--workdir", default="")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Put the repo root on sys.path for any later imports. Module-level imports
    # (config, deploy.*) already ran, so callers must set PYTHONPATH / cwd.
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
