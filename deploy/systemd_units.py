"""Shared systemd unit retirement / restore helpers.

Retiring a unit always means:

1. ``systemctl stop``
2. ``systemctl disable``
3. rename the unit file to ``<name>.service.bak`` (numbered if needed)
4. ``systemctl daemon-reload``
5. verify systemd no longer knows the unit (abort if it does)

This prevents accidental restart via boot, ``Wants=``, or manual start —
critical when operator keys must never run twice (SSV / validators).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import List, Optional, Sequence

DEFAULT_SYSTEMD_UNIT_DIR = "/etc/systemd/system"


def unit_stem(unit_path_or_name: str) -> str:
    """Return the systemd unit stem (``ssvnode`` from ``…/ssvnode.service``)."""
    name = os.path.basename(unit_path_or_name)
    if name.endswith(".service"):
        return name[: -len(".service")]
    return name


def unit_basename(unit_path_or_name: str) -> str:
    """Return ``name.service`` for a path or stem."""
    name = os.path.basename(unit_path_or_name)
    if not name.endswith(".service"):
        name = f"{name}.service"
    return name


def resolve_unit_path(
    unit_path_or_name: str,
    *,
    unit_dir: str = DEFAULT_SYSTEMD_UNIT_DIR,
) -> str:
    """Return an absolute ``.service`` path under *unit_dir* when given a name."""
    raw = unit_path_or_name.strip()
    if os.path.isabs(raw):
        return raw
    return os.path.join(unit_dir, unit_basename(raw))


def next_bak_path(unit_path: str) -> str:
    """Return ``unit_path.bak`` or the next free ``.bak.N`` if bak exists."""
    bak = f"{unit_path}.bak"
    if not os.path.exists(bak):
        return bak
    i = 1
    while os.path.exists(f"{bak}.{i}"):
        i += 1
    return f"{bak}.{i}"


def unit_known_to_systemd(unit_name: str) -> bool:
    """Return True when systemd still knows *unit_name* (unit file or active).

    After a successful retirement the unit must be unknown: ``systemctl cat``
    fails and the unit is not active. ``list-unit-files`` may print a header
    with an empty body; only treat a matching unit row as "known".
    """
    name = unit_basename(unit_name)
    cat = subprocess.run(
        ["systemctl", "cat", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if cat.returncode == 0 and (cat.stdout or "").strip():
        return True
    is_active = subprocess.run(
        ["systemctl", "is-active", "--quiet", name],
        check=False,
    )
    if is_active.returncode == 0:
        return True
    listed = subprocess.run(
        ["systemctl", "list-unit-files", name, "--no-legend", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    body = (listed.stdout or "").strip()
    if not body:
        return False
    for line in body.splitlines():
        first = line.split(None, 1)[0] if line.strip() else ""
        if first == name:
            return True
    return False


def verify_unit_absent(unit_name: str) -> None:
    """Abort if *unit_name* is still known/active after retirement.

    Raises:
        RuntimeError: When systemd still knows the unit — callers must not
            start a replacement operator/service in that state.
    """
    name = unit_basename(unit_name)
    if unit_known_to_systemd(name):
        raise RuntimeError(
            f"Unit retirement verification failed: systemd still knows {name}. "
            "Aborting before starting any replacement unit "
            "(refusing dual-instance / double-signing risk). "
            "Inspect `systemctl cat/status` and *.service.bak, then retry."
        )


def retire_systemd_unit(
    unit_path_or_name: str,
    *,
    unit_dir: str = DEFAULT_SYSTEMD_UNIT_DIR,
    dry_run: bool = False,
    verify: bool = True,
    sudo: bool = True,
) -> str:
    """Stop, disable, rename to ``*.service.bak``, daemon-reload, verify absent.

    Args:
        unit_path_or_name: Absolute unit path or ``name.service`` / stem.
        unit_dir: Directory used when resolving a bare unit name.
        dry_run: When True, only compute the destination bak path.
        verify: When True, require the unit to be unknown after reload.
        sudo: Prefix privileged commands with ``sudo`` (production default).

    Returns:
        Path of the ``.service.bak`` (or ``.bak.N``) file.

    Raises:
        FileNotFoundError: When the unit file is missing and not already bak'd.
        RuntimeError: When post-retirement verification fails.
        ValueError: When the path is not a ``.service`` unit.
    """
    unit_path = resolve_unit_path(unit_path_or_name, unit_dir=unit_dir)
    if not unit_path.endswith(".service"):
        raise ValueError(f"Not a .service unit: {unit_path}")
    unit_name = os.path.basename(unit_path)
    stem = unit_stem(unit_path)
    plain_bak = f"{unit_path}.bak"
    # Destination for *this* retirement: prefer plain .bak; if taken, rotate
    # the existing plain bak aside and reuse the plain name so rollback docs
    # keep finding ``*.service.bak``.
    if dry_run:
        return plain_bak if not os.path.exists(plain_bak) else next_bak_path(unit_path)

    prefix: List[str] = ["sudo"] if sudo else []

    def _run(args: Sequence[str], *, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run([*prefix, *args], check=check)

    _run(["systemctl", "stop", stem], check=False)
    _run(["systemctl", "disable", stem], check=False)

    if os.path.exists(unit_path):
        if os.path.exists(plain_bak):
            rotated = next_bak_path(unit_path)
            _run(["mv", plain_bak, rotated], check=True)
        bak = plain_bak
        _run(["mv", unit_path, bak], check=True)
    elif os.path.exists(plain_bak):
        # Already renamed on a prior attempt; still reload + verify.
        bak = plain_bak
    else:
        raise FileNotFoundError(f"Unit file not found: {unit_path}")

    _run(["systemctl", "daemon-reload"], check=True)
    if verify:
        verify_unit_absent(unit_name)
    return bak


def restore_retired_unit(
    bak_path: str,
    *,
    dry_run: bool = False,
    enable: bool = True,
    sudo: bool = True,
) -> str:
    """Restore ``*.service.bak`` → ``*.service``, daemon-reload, optionally enable.

    Does not start the unit. Caller must retire any conflicting live unit
    first (e.g. EthPillar units before restoring legacy bak files).

    Args:
        bak_path: Path ending in ``.service.bak`` or ``.service.bak.N``.
        dry_run: When True, only report the destination path.
        enable: Run ``systemctl enable`` after restore.
        sudo: Prefix privileged commands with ``sudo``.

    Returns:
        Restored ``.service`` path.
    """
    if not bak_path.endswith(".bak") and ".service.bak." not in os.path.basename(bak_path):
        raise ValueError(f"Not a .service.bak path: {bak_path}")
    restored = bak_path
    if restored.endswith(".bak"):
        restored = restored[: -len(".bak")]
    else:
        restored = re.sub(r"\.bak\.\d+$", "", bak_path)
    if not restored.endswith(".service"):
        raise ValueError(f"Cannot derive .service path from {bak_path}")
    if dry_run:
        return restored
    if os.path.exists(restored):
        raise RuntimeError(
            f"Cannot restore {bak_path}: {restored} already exists "
            "(retire the live unit first)"
        )
    if not os.path.exists(bak_path):
        raise FileNotFoundError(bak_path)

    prefix: List[str] = ["sudo"] if sudo else []
    subprocess.run([*prefix, "mv", bak_path, restored], check=True)
    subprocess.run([*prefix, "systemctl", "daemon-reload"], check=True)
    if enable:
        subprocess.run(
            [*prefix, "systemctl", "enable", unit_stem(restored)],
            check=False,
        )
    return restored


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: ``retire`` / ``restore`` / ``verify-absent`` for shell callers."""
    parser = argparse.ArgumentParser(description="Retire or restore systemd units")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ret = sub.add_parser(
        "retire",
        help="stop+disable+rename to .service.bak+daemon-reload+verify absent",
    )
    p_ret.add_argument("unit", help="Unit path or name (e.g. ssvnode.service)")
    p_ret.add_argument("--unit-dir", default=DEFAULT_SYSTEMD_UNIT_DIR)
    p_ret.add_argument("--dry-run", action="store_true")
    p_ret.add_argument("--no-verify", action="store_true")
    p_ret.add_argument("--no-sudo", action="store_true")

    p_res = sub.add_parser("restore", help="Restore *.service.bak → *.service")
    p_res.add_argument("bak", help="Path to .service.bak")
    p_res.add_argument("--dry-run", action="store_true")
    p_res.add_argument("--no-enable", action="store_true")
    p_res.add_argument("--no-sudo", action="store_true")

    p_ver = sub.add_parser("verify-absent", help="Fail if systemd still knows unit")
    p_ver.add_argument("unit", help="Unit name or path")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "retire":
            bak = retire_systemd_unit(
                args.unit,
                unit_dir=args.unit_dir,
                dry_run=args.dry_run,
                verify=not args.no_verify,
                sudo=not args.no_sudo,
            )
            print(bak)
            return 0
        if args.cmd == "restore":
            path = restore_retired_unit(
                args.bak,
                dry_run=args.dry_run,
                enable=not args.no_enable,
                sudo=not args.no_sudo,
            )
            print(path)
            return 0
        if args.cmd == "verify-absent":
            verify_unit_absent(args.unit)
            return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
