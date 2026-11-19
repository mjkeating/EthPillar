"""Integration runtime check for ssv-node (Go ssvnode or Anchor).

Starts ssv-node with a throwaway operator key and asserts what each client can
actually reach in CI. In CI the local EL/CL are running but still syncing.

* Anchor (sigp/anchor v1.4.x): the metrics (127.0.0.1:5164) and HTTP API
  (127.0.0.1:5062) servers bind before any BN/EL I/O. P2P (13001/tcp,
  12001/udp discovery, 13002/udp QUIC on 0.0.0.0) binds once the BN serves
  genesis/spec and the EL WebSocket accepts a connection. Anchor does not wait
  for sync. With no BN at all it retries forever (P2P never binds; metrics/API
  stay up). With the BN up but EL WS refused it exits ("Unable to create
  syncer") and systemd restarts it.
* Go ssvnode (ssvlabs/ssv v2.5.x): nothing binds until the BN returns its
  beacon config (goclient.New waits up to 60s, then FATAL) and the EL WS dials.
  The metrics server (``:15000``, all interfaces) starts next. Then
  ``ensureComponentsHealthy`` requires a *synced* CL+EL within 30s; otherwise
  FATAL "component(s) are not healthy" and restart. P2P (13001/12001) and the
  SSV API (16000) only start after that gate *and* the historical
  registry-contract event sync, so they cannot be asserted against a syncing
  CI stack. The Go check therefore asserts the metrics port plus startup log
  lines, and tolerates the health-gate FATAL.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from port_bindings import PortExpectation, check_port_scope, read_ss_listeners

SSV_DATA_DIR = "/var/lib/ssv"
SSV_KEY = f"{SSV_DATA_DIR}/encrypted_private_key.json"
SSV_PASSWORD = f"{SSV_DATA_DIR}/password"

# Log lines (substring match) that prove progress / explain a soft outcome.
ANCHOR_WAITING_FOR_BN = ("Unable to read spec from beacon node", "Unable to connect to a beacon node")
ANCHOR_P2P_UP = "Listening established"
GO_METRICS_UP = "MetricsHandler"  # "MetricsHandler  setup collection {"address": ":15000"}"
GO_EXPECTED_FATALS = (
    "component(s) are not healthy",  # CL/EL still syncing in CI
)
GO_BN_TIMEOUT = "timed out awaiting Beacon config initialization"


@dataclass(frozen=True)
class SsvPortPlan:
    """Ports to assert for one client, split by what must bind vs. what may."""

    required: Tuple[PortExpectation, ...]
    p2p: Tuple[PortExpectation, ...]


def ssv_port_plan(client: str, ports: dict) -> SsvPortPlan:
    """Return the port expectations for *client* given ``read_ssv_ports()`` output."""
    if (client or "ssv").lower() == "anchor":
        return SsvPortPlan(
            required=(
                PortExpectation(int(ports["metrics"]), "localhost", ("tcp",), "Anchor metrics"),
                PortExpectation(int(ports["api"]), "localhost", ("tcp",), "Anchor HTTP API"),
            ),
            p2p=(
                PortExpectation(int(ports["tcp"]), "public", ("tcp",), "Anchor P2P TCP"),
                PortExpectation(int(ports["udp"]), "public", ("udp",), "Anchor discovery UDP"),
                PortExpectation(int(ports["quic"]), "public", ("udp",), "Anchor QUIC UDP"),
            ),
        )
    # Go ssvnode: metrics binds ":<port>" (all interfaces; no address knob) and is
    # UFW-denied by EthPillar. P2P/API are gated on a synced CL+EL (see module doc).
    return SsvPortPlan(
        required=(PortExpectation(int(ports["metrics"]), "public", ("tcp",), "Go SSV metrics"),),
        p2p=(),
    )


def keygen_command(client: str, network: str) -> List[str]:
    """Return the argv (run as root) that writes a throwaway encrypted operator key."""
    if (client or "ssv").lower() == "anchor":
        return [
            "sudo", "-u", "ssv", "/usr/local/bin/anchor", "keygen", "--encrypt",
            "--data-dir", SSV_DATA_DIR, "--network", network.lower(),
            "--password-file", SSV_PASSWORD,
        ]
    # ssvnode writes encrypted_private_key.json into the CWD.
    return [
        "sudo", "-u", "ssv", "sh", "-c",
        f"cd {SSV_DATA_DIR} && /usr/local/bin/ssvnode generate-operator-keys "
        f"--password-file={SSV_PASSWORD}",
    ]


def provision_throwaway_operator_key(client: str, network: str) -> bool:
    """Create a random password + encrypted operator key under /var/lib/ssv.

    Throwaway only: never registered on-chain, removed by uninstall/teardown.
    """
    password = secrets.token_urlsafe(24)
    write = subprocess.run(
        ["sudo", "sh", "-c", f"umask 077; cat > {SSV_PASSWORD} && chown ssv:ssv {SSV_PASSWORD}"],
        input=password, text=True, capture_output=True, check=False,
    )
    if write.returncode != 0:
        print(f"  ❌ could not write {SSV_PASSWORD}: {write.stderr.strip()}", flush=True)
        return False
    gen = subprocess.run(keygen_command(client, network), capture_output=True, text=True, check=False)
    ok = subprocess.run(["sudo", "test", "-f", SSV_KEY], check=False).returncode == 0
    if gen.returncode != 0 or not ok:
        print(f"  ❌ throwaway {client} keygen failed:\n{gen.stdout[-800:]}{gen.stderr[-800:]}", flush=True)
        return False
    subprocess.run(["sudo", "chmod", "600", SSV_KEY], check=False)
    print(f"  🔑 throwaway {client} operator key created (not registered; test only)", flush=True)
    return True


def journal_is_go_beacon_timeout(journal: str) -> bool:
    """True when Go gave up waiting for beacon config (soft-skip, not a port miss)."""
    return GO_BN_TIMEOUT in (journal or "")


def journal_is_anchor_waiting_for_bn(journal: str) -> bool:
    """True when Anchor is still retrying beacon spec, so P2P will not bind yet."""
    text = journal or ""
    return any(snippet in text for snippet in ANCHOR_WAITING_FOR_BN)


def _journal(service: str = "ssv-node", lines: int = 300) -> str:
    """Return recent unit logs.

    The integration runner is the non-root ``epstaker`` user. ``journalctl``
    without sudo often cannot read the system journal, which would hide the
    beacon-timeout lines and turn a soft-skip into a hard failure. Same rule
    as :func:`service_start.read_service_journal`.
    """
    cmd = ["journalctl", "-u", service, "--no-pager", "-o", "cat", "-n", str(lines)]
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        cmd = ["sudo", *cmd]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return (out.stdout or "") + (out.stderr or "")


def _poll(expectations: Sequence[PortExpectation], timeout_sec: int, interval_sec: int = 2,
          clock: Callable[[], float] = time.monotonic) -> Tuple[bool, List[str], float]:
    start = clock()
    errors: List[str] = []
    while True:
        bindings = read_ss_listeners()
        errors = [msg for ok, msg in (check_port_scope(bindings, e.port, e.scope, e.protocols, e.label)
                                      for e in expectations) if not ok]
        if not errors:
            return True, [], clock() - start
        if clock() - start >= timeout_sec:
            return False, errors, clock() - start
        time.sleep(interval_sec)


def check_ssv_node_runtime(client: str, ports: dict, *, soft_skip: Optional[Callable[[str], None]] = None,
                           required_timeout: int = 90, p2p_timeout: int = 120) -> bool:
    """Start ssv-node, assert client-specific ports, stop it. Returns pass/fail."""
    client = (client or "ssv").lower()
    plan = ssv_port_plan(client, ports)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
    subprocess.run(["sudo", "systemctl", "start", "ssv-node"], check=False)
    try:
        ok, errors, took = _poll(plan.required, required_timeout)
        if not ok:
            journal = _journal()
            if client != "anchor" and journal_is_go_beacon_timeout(journal):
                reason = "Go ssvnode: local beacon REST did not serve config within 60s (CL not ready)"
                print(f"  ⚠️  {reason}", flush=True)
                if soft_skip:
                    soft_skip(reason)
                return True
            print("  ❌ ssv-node required ports not bound: " + "; ".join(errors), flush=True)
            print(journal[-4000:], flush=True)
            return False
        labels = ", ".join(f"{e.label} :{e.port}" for e in plan.required)
        print(f"  ✅ {labels} bound after {took:.0f}s", flush=True)

        if client != "anchor":
            journal = _journal()
            if GO_METRICS_UP not in journal:
                print("  ❌ Go ssvnode: metrics port bound but no MetricsHandler log line", flush=True)
                print(journal[-4000:], flush=True)
                return False
            if any(snippet in journal for snippet in GO_EXPECTED_FATALS):
                print(
                    "  ℹ️  Go ssvnode: sync health-check exit is expected "
                    "while CL/EL are still syncing",
                    flush=True,
                )
            print("  ℹ️  Go ssvnode: P2P 13001/12001 and API 16000 not asserted "
                  "(start only after CL+EL are synced and contract events synced)", flush=True)
            return True

        ok, errors, took = _poll(plan.p2p, p2p_timeout)
        if ok:
            labels = ", ".join(f"{e.label} :{e.port}" for e in plan.p2p)
            print(f"  ✅ {labels} bound on all interfaces after {took:.0f}s", flush=True)
            return True
        journal = _journal()
        if journal_is_anchor_waiting_for_bn(journal):
            reason = "Anchor P2P not bound: beacon node not serving genesis/spec yet"
            print(f"  ⚠️  {reason}", flush=True)
            if soft_skip:
                soft_skip(reason)
            return True
        print("  ❌ Anchor P2P ports not bound: " + "; ".join(errors), flush=True)
        print(journal[-4000:], flush=True)
        return False
    finally:
        subprocess.run(["sudo", "systemctl", "stop", "ssv-node"], check=False)
