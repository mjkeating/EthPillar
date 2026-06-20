"""Validator client service helpers: beacon endpoint resolution and patching."""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, Optional

from deploy.common import write_service_file

# Canonical VC name → beacon-node flag used in validator.service ExecStart.
BEACON_FLAG_BY_VC: Dict[str, str] = {
    "Lighthouse": "--beacon-nodes",
    "Nimbus": "--beacon-node",
    "Teku": "--beacon-node-api-endpoint",
    "Lodestar": "--beaconNodes",
    "Prysm": "--beacon-rest-api-provider",
}

DEFAULT_VALIDATOR_SERVICE = "/etc/systemd/system/validator.service"

# Matches flag=http://host:port inside ExecStart (handles line continuations).
_BEACON_URL_RE = re.compile(
    r"(?P<flag>"
    + "|".join(re.escape(f) for f in BEACON_FLAG_BY_VC.values())
    + r")=(?P<url>https?://[^\s\\]+)"
)


def normalize_vc_name(name: str) -> str:
    """Map a Description token or client name to a canonical VC key."""
    name = (name or "").strip()
    if name in BEACON_FLAG_BY_VC:
        return name
    for vc in BEACON_FLAG_BY_VC:
        if name.lower().startswith(vc.lower()):
            return vc
    raise ValueError(f"Unsupported validator client: {name!r}")


def get_beacon_endpoint(cl_ip: str = "127.0.0.1", cl_rest_port: str = "5052") -> str:
    """Build the beacon node REST URL (mirrors getBeaconNodeEndpoint in functions.sh)."""
    cl_ip = (cl_ip or "127.0.0.1").strip()
    cl_rest_port = (cl_rest_port or "5052").strip()
    return f"http://{cl_ip}:{cl_rest_port}"


def _read_service_file(service_path: str) -> str:
    with open(service_path, encoding="utf-8") as fh:
        return fh.read()


def _write_service_file(service_path: str, content: str) -> None:
    """Write validator.service; use sudo cp for system paths (same as deploy.common)."""
    if service_path.startswith("/etc/"):
        write_service_file(content, service_path, temp_filename="validator_temp.service")
    else:
        with open(service_path, "w", encoding="utf-8") as fh:
            fh.write(content)


def scrape_beacon_endpoint(content: str, vc_name: str) -> Optional[str]:
    """Extract the current beacon REST URL from service file content."""
    flag = BEACON_FLAG_BY_VC[normalize_vc_name(vc_name)]
    match = re.search(re.escape(flag) + r"=(?P<url>https?://[^\s\\]+)", content)
    return match.group("url") if match else None


def scrape_validator_service_params(service_path: str, vc_name: str) -> Dict[str, str]:
    """Scrape validator.service metadata needed for patching or diagnostics."""
    content = _read_service_file(service_path)
    canonical = normalize_vc_name(vc_name)
    flag = BEACON_FLAG_BY_VC[canonical]
    beacon_endpoint = scrape_beacon_endpoint(content, canonical) or ""

    description = ""
    for line in content.splitlines():
        if line.startswith("Description="):
            description = line.split("=", 1)[1].strip()
            break

    return {
        "vc_name": canonical,
        "beacon_flag": flag,
        "beacon_endpoint": beacon_endpoint,
        "description": description,
        "service_path": service_path,
    }


def patch_beacon_endpoint(
    service_path: str,
    vc_name: str,
    new_endpoint: str,
) -> bool:
    """Replace only the beacon-node flag value in validator.service.

    Args:
        service_path: Path to validator.service.
        vc_name: Validator client name (e.g. Prysm, Lighthouse).
        new_endpoint: Full beacon REST URL.
            Common defaults:
              - Lighthouse/Nimbus/Teku: http://127.0.0.1:5052
              - Prysm:                  http://127.0.0.1:3500
              - Lodestar:               http://127.0.0.1:9596

    Returns:
        True if the file was updated.

    Raises:
        ValueError: Unknown VC or beacon flag not found in service file.
        FileNotFoundError: service_path does not exist.
    """
    if not os.path.isfile(service_path):
        raise FileNotFoundError(f"Validator service file not found: {service_path}")

    new_endpoint = new_endpoint.strip()
    if not re.match(r"^https?://", new_endpoint):
        raise ValueError(f"Invalid beacon endpoint URL: {new_endpoint!r}")

    flag = BEACON_FLAG_BY_VC[normalize_vc_name(vc_name)]
    new_flag_arg = f"{flag}={new_endpoint}"
    pattern = re.escape(flag) + r"=https?://[^\s\\]+"

    content = _read_service_file(service_path)
    if not re.search(pattern, content):
        raise ValueError(f"Beacon flag {flag} not found in {service_path}")

    new_content = re.sub(pattern, new_flag_arg, content, count=1)
    if new_content == content:
        return False

    _write_service_file(service_path, new_content)
    return True


def _cmd_patch(args: argparse.Namespace) -> int:
    try:
        updated = patch_beacon_endpoint(args.service_path, args.vc, args.endpoint)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if updated:
        print(f"Patched beacon endpoint in {args.service_path} → {args.endpoint}")
    else:
        print(f"No change needed in {args.service_path}")
    return 0


def _cmd_scrape(args: argparse.Namespace) -> int:
    try:
        params = scrape_validator_service_params(args.service_path, args.vc)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for key, value in params.items():
        print(f"{key}={value}")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validator client service utilities (beacon endpoint patching)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    patch_parser = subparsers.add_parser(
        "patch", help="Update the beacon-node flag in validator.service"
    )
    patch_parser.add_argument("--vc", required=True, help="Validator client name")
    patch_parser.add_argument("--endpoint", required=True, help="Beacon REST URL")
    patch_parser.add_argument(
        "--service-path",
        default=DEFAULT_VALIDATOR_SERVICE,
        help="Path to validator.service",
    )
    patch_parser.set_defaults(func=_cmd_patch)

    scrape_parser = subparsers.add_parser(
        "scrape", help="Print beacon endpoint and metadata from validator.service"
    )
    scrape_parser.add_argument("--vc", required=True, help="Validator client name")
    scrape_parser.add_argument(
        "--service-path",
        default=DEFAULT_VALIDATOR_SERVICE,
        help="Path to validator.service",
    )
    scrape_parser.set_defaults(func=_cmd_scrape)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())