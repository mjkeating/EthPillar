"""Timeouts and start-failure journaling for the integration harness.

Nimbus first-start runs ``trustedNodeSync`` as ``ExecStartPre``. That job can
block well beyond the default 30s subprocess budget even when the checkpoint
cache/proxy is hot (HOODI finalized-state import). Other clients checkpoint
sync after ``ExecStart``, so they keep the short timeout.

We keep a blocking ``systemctl start`` (not ``--no-block``) so the harness
does not declare the unit healthy until ExecStartPre finishes and systemd
has spawned the main process. ActiveState/port polling still runs afterward.

When ``systemctl start`` fails, :func:`journal_error_excerpt` pulls the last
ERR/error/FATAL lines (else the last few journal lines) so CI failure
summaries and known-failure signature matching see the real cause — not an
empty ``systemctl start <unit> failed:`` line.
"""
from __future__ import annotations

import re
import subprocess
from typing import Dict, List, Optional, Sequence

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


# Prefer client ERR/FATAL lines over systemd bookkeeping when summarizing a
# failed ``systemctl start``. Case-insensitive; matched as whole tokens.
_JOURNAL_ERROR_TOKEN_RE = re.compile(
    r"(?:\bERR\b|\bERROR\b|\bFATAL\b|\bCritical\b|\bfailed\b|\bFailed\b)",
    re.IGNORECASE,
)


def journal_error_excerpt(
    journal_text: str,
    *,
    max_error_lines: int = 3,
    fallback_lines: int = 10,
) -> str:
    """Return a concise journal excerpt for a failed service start.

    Prefers the last *max_error_lines* lines that look like errors (ERR /
    ERROR / FATAL / failed). Falls back to the last *fallback_lines* of
    non-empty journal output when no error-shaped lines are present.

    Args:
        journal_text: Raw ``journalctl`` (or ``systemctl status``) output.
        max_error_lines: Cap on error-shaped lines kept.
        fallback_lines: Cap on trailing lines when no error tokens match.

    Returns:
        A single-line-friendly excerpt (newlines collapsed to `` | ``), or
        an empty string when *journal_text* is blank.
    """
    lines = [ln.strip() for ln in (journal_text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    error_lines = [ln for ln in lines if _JOURNAL_ERROR_TOKEN_RE.search(ln)]
    chosen: Sequence[str]
    if error_lines:
        chosen = error_lines[-max_error_lines:]
    else:
        chosen = lines[-fallback_lines:]
    # Collapse for the ❌ summary line; full journal still prints separately.
    return " | ".join(chosen)


def read_service_journal(
    service_name: str,
    *,
    lines: int = 40,
    journalctl_cmd: Optional[Sequence[str]] = None,
) -> str:
    """Capture recent ``journalctl -u`` output for *service_name*.

    Args:
        service_name: Systemd unit name without ``.service``.
        lines: ``-n`` line budget.
        journalctl_cmd: Optional full argv override (tests).

    Returns:
        Combined stdout+stderr text, or ``\"\"`` if journalctl fails.
    """
    if journalctl_cmd is not None:
        cmd = list(journalctl_cmd)
    else:
        import os

        prefix: List[str] = []
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            prefix = ["sudo"]
        cmd = prefix + [
            "journalctl",
            "-u",
            service_name,
            "--no-pager",
            "-n",
            str(lines),
        ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or "") + (result.stderr or "")


def format_systemctl_start_failure(
    service_name: str,
    *,
    stderr: str = "",
    journal_text: str = "",
    timed_out: bool = False,
    elapsed_sec: Optional[float] = None,
    limit_sec: Optional[float] = None,
) -> str:
    """Build the ``❌ systemctl start …`` summary line including journal errors.

    Args:
        service_name: Unit name (consensus, execution, …).
        stderr: ``systemctl start`` stderr (often only the Job-for-… blurb).
        journal_text: Captured journal / status text to excerpt.
        timed_out: When True, phrase as a start timeout.
        elapsed_sec: Elapsed seconds (timeouts).
        limit_sec: Timeout budget (timeouts).

    Returns:
        A single failure message suitable for printing after ``❌ ``.
    """
    stderr_bit = " ".join((stderr or "").split())
    excerpt = journal_error_excerpt(journal_text)
    parts: List[str] = []
    if timed_out:
        timing = ""
        if elapsed_sec is not None and limit_sec is not None:
            timing = f" after {elapsed_sec:.1f}s (limit {limit_sec:g}s)"
        elif elapsed_sec is not None:
            timing = f" after {elapsed_sec:.1f}s"
        parts.append(f"systemctl start {service_name} timed out{timing}")
        if stderr_bit:
            parts.append(stderr_bit)
    elif stderr_bit:
        parts.append(f"systemctl start {service_name} failed: {stderr_bit}")
    elif excerpt:
        parts.append(f"systemctl start {service_name} failed: {excerpt}")
        excerpt = ""  # already in parts
    else:
        parts.append(f"systemctl start {service_name} failed:")
    if excerpt and excerpt not in " | ".join(parts):
        parts.append(excerpt)
    return " | ".join(parts)


def print_systemctl_start_failure(
    service_name: str,
    *,
    stderr: str = "",
    timed_out: bool = False,
    elapsed_sec: Optional[float] = None,
    limit_sec: Optional[float] = None,
    journal_lines: int = 40,
) -> str:
    """Capture the unit journal, print full + summarized failure, return summary.

    Always prints the raw journal (for the per-cell log) and a single ``❌``
    line that includes the journal excerpt so CI summaries and known-failure
    signature matching see the real error.
    """
    journal_text = read_service_journal(service_name, lines=journal_lines)
    if journal_text.strip():
        print("--- journalctl ---", flush=True)
        print(journal_text.rstrip(), flush=True)
        print("------------------", flush=True)
    message = format_systemctl_start_failure(
        service_name,
        stderr=stderr,
        journal_text=journal_text,
        timed_out=timed_out,
        elapsed_sec=elapsed_sec,
        limit_sec=limit_sec,
    )
    print(f"  ❌ {message}", flush=True)
    return message

