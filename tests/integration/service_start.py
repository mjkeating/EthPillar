"""Timeouts for blocking ``systemctl start`` in the integration harness.

Nimbus first-start runs ``trustedNodeSync`` as ``ExecStartPre``. That job can
block well beyond the default 30s subprocess budget even when the checkpoint
cache/proxy is hot (HOODI finalized-state import). Other clients checkpoint
sync after ``ExecStart``, so they keep the short timeout.

We keep a blocking ``systemctl start`` (not ``--no-block``) so the harness
does not declare the unit healthy until ExecStartPre finishes and systemd
has spawned the main process. ActiveState/port polling still runs afterward.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# Default budget for units whose start job returns quickly (EL, MEV, non-Nimbus CL).
DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC = 30

# Production Nimbus BN ``TimeoutStartSec`` when a checkpoint URL is set
# (``deploy/nimbus.py``). The harness never waits longer than this.
NIMBUS_UNIT_TIMEOUT_START_SEC = 1800

# First-boot Nimbus ``trustedNodeSync`` (HOODI Solo Staking, checkpoint cache/proxy).
# Measured 2026-09-26 on this cloud agent: blocking ``systemctl start consensus``
# returned after 33.3s (proxy 200 on HOODI finalized state, 226 MB SSZ).
# 2×33.3s = 66.6s, rounded up to 90s. Capped at :data:`NIMBUS_UNIT_TIMEOUT_START_SEC`.
NIMBUS_CHECKPOINT_SYNC_START_TIMEOUT_SEC = 90

# Integration-only: cap unit stop so RPC expose/revoke ``service restart`` does
# not wait the production TimeoutStopSec=900 for a slow Nimbus SIGTERM.
# Production generators stay at 900; the harness rewrites installed units.
INTEGRATION_TIMEOUT_STOP_SEC = 90

_TIMEOUT_START_RE = re.compile(r"^TimeoutStartSec=(.+)$", re.MULTILINE)
_TIMEOUT_STOP_RE = re.compile(r"^TimeoutStopSec=\S+", re.MULTILINE)


def _unit_assignments(unit_text: str) -> Dict[str, List[str]]:
    """Return systemd key → values for simple ``Key=value`` lines."""
    assignments: Dict[str, List[str]] = {}
    for line in unit_text.splitlines():
        if not line or line.startswith("#") or line.startswith("[") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        assignments.setdefault(key, []).append(value)
    return assignments


def _is_nimbus_beacon_unit(unit_text: str) -> bool:
    """Return True when the unit describes a Nimbus beacon node."""
    return "nimbus_beacon_node" in unit_text or "Nimbus Beacon Node" in unit_text


def unit_has_nimbus_checkpoint_sync(unit_text: str) -> bool:
    """Return True when first start will run Nimbus ``trustedNodeSync``.

    Primary signal: ``ExecStartPre`` contains ``trustedNodeSync``.
    Fallback: Nimbus BN plus a checkpoint URL and/or ``TimeoutStartSec``.
    """
    assignments = _unit_assignments(unit_text)
    if any("trustedNodeSync" in value for value in assignments.get("ExecStartPre", [])):
        return True
    if not _is_nimbus_beacon_unit(unit_text):
        return False
    has_timeout = bool(assignments.get("TimeoutStartSec"))
    has_sync_url = "--trusted-node-url=" in unit_text
    return has_timeout or has_sync_url


def parse_timeout_start_sec(unit_text: str) -> Optional[int]:
    """Parse integer ``TimeoutStartSec`` seconds from a unit file.

    Returns None when the key is missing, ``infinity``, or not an integer
    (systemd also accepts unit suffixes such as ``30min``).
    """
    values = _unit_assignments(unit_text).get("TimeoutStartSec", [])
    if not values:
        return None
    raw = values[-1].strip()
    if raw.isdigit():
        return int(raw)
    return None


def systemctl_start_timeout_sec(service_name: str, unit_text: str) -> int:
    """Return the subprocess timeout for a blocking ``systemctl start``.

    Non-Nimbus / no-ExecStartPre starts stay at
    :data:`DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC`. Nimbus first-start
    ``trustedNodeSync`` uses :data:`NIMBUS_CHECKPOINT_SYNC_START_TIMEOUT_SEC`,
    capped at the unit's ``TimeoutStartSec`` (or
    :data:`NIMBUS_UNIT_TIMEOUT_START_SEC` when that key is absent/unparsed).
    """
    if service_name != "consensus" or not unit_has_nimbus_checkpoint_sync(unit_text):
        return DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC
    cap = parse_timeout_start_sec(unit_text)
    if cap is None:
        cap = NIMBUS_UNIT_TIMEOUT_START_SEC
    return min(NIMBUS_CHECKPOINT_SYNC_START_TIMEOUT_SEC, cap)


def rewrite_timeout_stop_sec(
    unit_text: str, seconds: int = INTEGRATION_TIMEOUT_STOP_SEC
) -> str:
    """Return unit text with ``TimeoutStopSec`` set to *seconds*.

    Replaces an existing ``TimeoutStopSec=`` line. If the key is missing,
    inserts it immediately after the ``[Service]`` header. Production
    generators keep ``TimeoutStopSec=900``; Integration applies this
    rewrite before RPC expose/revoke restarts.
    """
    replacement = f"TimeoutStopSec={seconds}"
    if _TIMEOUT_STOP_RE.search(unit_text):
        return _TIMEOUT_STOP_RE.sub(replacement, unit_text, count=1)
    return re.sub(
        r"(?m)^(\[Service\][ \t]*\n)",
        rf"\1{replacement}\n",
        unit_text,
        count=1,
    )
