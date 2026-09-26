"""Shared systemd unit parsing and ExecStart canonicalization.

Used by keymanager patching, config compare, and any future unit inspection.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from deploy.service_generators import form_exec_start

SYSTEMD_DIR = "/etc/systemd/system"
KNOWN_NETWORKS = ("MAINNET", "HOLESKY", "SEPOLIA", "HOODI", "EPHEMERY")

SERVICE_FILES: Dict[str, str] = {
    "execution": f"{SYSTEMD_DIR}/execution.service",
    "consensus": f"{SYSTEMD_DIR}/consensus.service",
    "validator": f"{SYSTEMD_DIR}/validator.service",
    "mevboost": f"{SYSTEMD_DIR}/mevboost.service",
    "charon": f"{SYSTEMD_DIR}/charon.service",
}

# Description first-token → canonical client name (title case used by EthPillar).
_CLIENT_ALIASES: Dict[str, str] = {
    "geth": "Geth",
    "besu": "Besu",
    "nethermind": "Nethermind",
    "reth": "Reth",
    "erigon": "Erigon",
    "ethrex": "Ethrex",
    "erigon-caplin": "Erigon-Caplin",
    "lighthouse": "Lighthouse",
    "nimbus": "Nimbus",
    "teku": "Teku",
    "lodestar": "Lodestar",
    "grandine": "Grandine",
    "prysm": "Prysm",
    "obol": "Charon",
    "charon": "Charon",
    "caplin": "Erigon-Caplin",
    "mev-boost": "MEV-Boost",
    "mevboost": "MEV-Boost",
}


class ServiceParseError(Exception):
    """Raised when a systemd unit cannot be parsed."""


@dataclass
class ParsedUnit:
    """Structured view of a systemd service unit."""

    content: str
    description: str = ""
    client: str = ""
    network: str = ""
    directives: Dict[str, List[str]] = field(default_factory=dict)
    exec_start_index: int = -1
    exec_start_end_index: int = -1
    exec_args: List[str] = field(default_factory=list)


def read_text_file(path: str) -> Optional[str]:
    """Read a text file, falling back to ``sudo -n cat`` on permission errors."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except PermissionError:
        try:
            result = subprocess.run(
                ["sudo", "-n", "cat", path],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return None
    except OSError:
        return None


def unit_exists(path: str) -> bool:
    """True if *path* exists as a file (including via passwordless sudo)."""
    if os.path.isfile(path):
        return True
    try:
        result = subprocess.run(
            ["sudo", "-n", "test", "-f", path],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def normalize_client_name(name: str) -> str:
    """Map a Description token or free-form name to EthPillar's client label."""
    raw = (name or "").strip()
    if not raw:
        return ""
    if raw in _CLIENT_ALIASES.values():
        return raw
    key = raw.lower().replace("_", "-")
    if key in _CLIENT_ALIASES:
        return _CLIENT_ALIASES[key]
    # "Lighthouse Consensus Client..." → Lighthouse
    first = raw.split()[0]
    first_key = first.lower().replace("_", "-")
    return _CLIENT_ALIASES.get(first_key, first)


def parse_description_client(description: str) -> str:
    """Extract client name from a ``Description=`` value."""
    if not description:
        return ""
    return normalize_client_name(description.split()[0])


def parse_description_network(description: str) -> str:
    """Extract network name (lowercase) from a ``Description=`` value."""
    if not description:
        return ""
    match = re.search(
        r"\b(" + "|".join(KNOWN_NETWORKS) + r")\b",
        description,
        flags=re.IGNORECASE,
    )
    return match.group(1).lower() if match else ""


def parse_exec_start(content: str) -> Tuple[int, int, List[str]]:
    """Parse the multi-line ``ExecStart=`` block from a systemd unit.

    Returns:
        ``(start_line_index, end_line_index, args)`` where *args* is the ordered
        list of ExecStart line tokens (binary first), and indices refer to
        ``content.splitlines()``.
    """
    lines = content.splitlines()
    start: Optional[int] = None
    for i, line in enumerate(lines):
        if line.startswith("ExecStart="):
            start = i
            break
    if start is None:
        raise ServiceParseError("Service file has no ExecStart= line")

    args: List[str] = []
    end = start
    i = start
    while i < len(lines):
        line = lines[i]
        if i == start:
            payload = line[len("ExecStart=") :]
        else:
            if not lines[i - 1].rstrip().endswith("\\"):
                break
            payload = line

        stripped = payload.rstrip()
        continued = stripped.endswith("\\")
        if continued:
            stripped = stripped[:-1].rstrip()
        token = stripped.strip()
        if token:
            args.append(token)
        end = i
        if not continued:
            break
        i += 1

    if not args:
        raise ServiceParseError("Service file ExecStart= is empty")
    return start, end, args


def rebuild_service_content(
    content: str,
    start: int,
    end: int,
    args: Sequence[str],
) -> str:
    """Rebuild unit file content with a new ExecStart argument list."""
    lines = content.splitlines()
    exec_block = "ExecStart=" + form_exec_start(list(args))
    new_lines = lines[:start] + exec_block.splitlines() + lines[end + 1 :]
    new_content = "\n".join(new_lines)
    if content.endswith("\n"):
        new_content += "\n"
    return new_content


def _split_atomic_args(tokens: Sequence[str]) -> List[str]:
    """Flatten ExecStart line tokens into atomic CLI arguments."""
    atomic: List[str] = []
    for token in tokens:
        # Keep quoted substrings together; otherwise split on whitespace.
        parts = re.findall(r'(?:[^\s"]|"(?:\\.|[^"])*")+', token.strip())
        atomic.extend(p for p in parts if p)
    return atomic


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def normalize_cli_args(tokens: Sequence[str]) -> List[str]:
    """Normalize CLI args to ``--flag`` / ``--flag=value`` form (sorted later).

    Space-separated ``--flag value`` pairs become ``--flag=value``. Repeated
    flags (e.g. ``-relay``) are preserved as separate entries. The first token
    (binary / ``client subcommand``) is kept intact and not whitespace-split.
    """
    if not tokens:
        return []

    binary = tokens[0].strip()
    rest = _split_atomic_args(tokens[1:])
    normalized: List[str] = [binary]
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg.startswith("-") and "=" not in arg and i + 1 < len(rest) and not rest[i + 1].startswith("-"):
            normalized.append(f"{arg}={_strip_quotes(rest[i + 1])}")
            i += 2
            continue
        if "=" in arg and arg.startswith("-"):
            flag, value = arg.split("=", 1)
            normalized.append(f"{flag}={_strip_quotes(value)}")
        else:
            normalized.append(_strip_quotes(arg) if not arg.startswith("-") else arg)
        i += 1
    return normalized


def flag_name(arg: str) -> str:
    """Return the flag key for an argument (``--foo`` from ``--foo=bar``)."""
    if not arg.startswith("-"):
        return arg
    return arg.split("=", 1)[0]


def get_flag_values(args: Sequence[str], *names: str) -> List[str]:
    """Return all values for flags matching any of *names* (with or without ``=``)."""
    want = {n.lower() for n in names}
    values: List[str] = []
    normalized = normalize_cli_args(args)
    for arg in normalized[1:] if normalized else []:
        key = flag_name(arg).lower()
        if key not in want:
            continue
        if "=" in arg:
            values.append(arg.split("=", 1)[1])
        else:
            values.append("")
    return values


def get_flag_value(args: Sequence[str], *names: str, default: str = "") -> str:
    """Return the first value for flags matching any of *names*."""
    values = get_flag_values(args, *names)
    return values[0] if values else default


def has_flag(args: Sequence[str], *names: str) -> bool:
    """True if any flag name is present (value optional)."""
    want = {n.lower() for n in names}
    for arg in normalize_cli_args(args)[1:]:
        if flag_name(arg).lower() in want:
            return True
    return False


def parse_unit(content: str) -> ParsedUnit:
    """Parse a full systemd unit into a :class:`ParsedUnit`."""
    directives: Dict[str, List[str]] = {}
    description = ""
    for line in content.splitlines():
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if line.rstrip().endswith("\\"):
            continue
        if "=" not in line:
            continue
        if line.startswith("ExecStart="):
            continue
        key, value = line.split("=", 1)
        directives.setdefault(key.strip(), []).append(value.strip())
        if key.strip() == "Description":
            description = value.strip()

    start, end, args = parse_exec_start(content)
    return ParsedUnit(
        content=content,
        description=description,
        client=parse_description_client(description),
        network=parse_description_network(description),
        directives=directives,
        exec_start_index=start,
        exec_start_end_index=end,
        exec_args=args,
    )


def canonicalize_unit(content: str) -> str:
    """Return unit text with ExecStart flags and [Service] Environment= lines sorted.

    Other directives keep their original order.

    Suitable for side-by-side diffs where flag order should not matter.
    """
    unit = parse_unit(content)
    lines = content.splitlines()

    # Preserve the original section structure and directive order: replace
    # only ExecStart with sorted normalized args, and sort consecutive
    # Environment= lines within [Service].
    normalized_args = normalize_cli_args(unit.exec_args)
    if normalized_args:
        binary = normalized_args[0]
        flags = sorted(normalized_args[1:], key=lambda a: (flag_name(a).lower(), a.lower()))
        new_args = [binary] + flags
    else:
        new_args = list(unit.exec_args)

    rebuilt = rebuild_service_content(
        content, unit.exec_start_index, unit.exec_start_end_index, new_args
    )

    # Sort Environment= lines within [Service] for stable comparison.
    out_lines: List[str] = []
    env_buf: List[str] = []
    in_service = False

    def flush_env() -> None:
        nonlocal env_buf
        out_lines.extend(sorted(env_buf))
        env_buf = []

    for line in rebuilt.splitlines():
        if line.startswith("["):
            flush_env()
            in_service = line.strip().lower() == "[service]"
            out_lines.append(line)
            continue
        if in_service and line.startswith("Environment="):
            env_buf.append(line)
            continue
        if env_buf and (not line.startswith("Environment=")):
            flush_env()
        out_lines.append(line)
    flush_env()

    text = "\n".join(out_lines)
    if content.endswith("\n") or rebuilt.endswith("\n"):
        text += "\n"
    return text


def semantic_equal(a: str, b: str) -> bool:
    """True if two units are equivalent ignoring ExecStart / Environment order."""
    return canonicalize_unit(a) == canonicalize_unit(b)


def installed_service_paths() -> Dict[str, str]:
    """Return ``{service_key: path}`` for units present on this host."""
    found: Dict[str, str] = {}
    for key, path in SERVICE_FILES.items():
        if unit_exists(path):
            found[key] = path
    return found
