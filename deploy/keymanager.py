"""Ethereum Keymanager API client (local keystores only).

Phase 1: list, import, and delete validator keystores via the standard
Keymanager API endpoints (``/eth/v1/keystores``). Remote signer, fee
recipient, graffiti, and ``/eth/v1/validator/config`` are out of scope.

Also provides discovery of endpoint/token paths and **enablement** of the
Keymanager API for Lighthouse, Lodestar, Nimbus, and Prysm by patching
systemd unit files (with backup + optional dry-run).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Discovery constants (EthPillar + common client layouts)
# ---------------------------------------------------------------------------

# Networks EthPillar commonly deploys.
SUPPORTED_NETWORKS: tuple[str, ...] = (
    "mainnet",
    "hoodi",
    "holesky",
    "sepolia",
    "ephemery",
)

# Port-scan order when the client is unknown (deduplicated).
DEFAULT_DISCOVERY_PORTS: tuple[int, ...] = (5062, 7500, 5052, 5051, 3500)

# Canonical client name → preferred keymanager HTTP ports (first is best guess).
_CLIENT_PORTS: dict[str, tuple[int, ...]] = {
    "lighthouse": (5062,),
    "lodestar": (5062,),
    # Prefer 7500 (common keymanager bind); 5052 often collides with beacon REST.
    "nimbus": (7500, 5052),
    "prysm": (7500,),
    # Known but not fully profiled yet (port fallback only).
    "teku": (7500,),
    "grandine": (5052,),
}

_BASE_DATA_DIR = "/var/lib"

DEFAULT_VALIDATOR_SERVICE = "/etc/systemd/system/validator.service"
DEFAULT_CONSENSUS_SERVICE = "/etc/systemd/system/consensus.service"

# Nimbus requires this file to exist before the process starts (it does not create it).
NIMBUS_KEYMANAGER_TOKEN_FILE = "/var/lib/nimbus_validator/api-token.txt"
NIMBUS_KEYMANAGER_DATA_DIR = "/var/lib/nimbus_validator"

# Prysm wallet + auth-token (EthPillar default; overridable via --wallet-dir in unit).
PRYSM_DEFAULT_WALLET_DIR = "/var/lib/prysm_validator/validator_keys"
PRYSM_DATA_DIR = "/var/lib/prysm_validator"
PRYSM_PASSWORD_FILE = "/var/lib/prysm_validator/password.txt"
PRYSM_VALIDATOR_HOME = "/home/validator"
PRYSM_VALIDATOR_BINARY = "/usr/local/bin/prysm-validator"
# Common location when the validator service runs as user ``validator``.
PRYSM_VALIDATOR_HOME_AUTH_TOKEN = (
    "/home/validator/.eth2validators/prysm-wallet-v2/auth-token"
)
PRYSM_WALLET_NOT_READY_MSG = (
    "Prysm wallet not initialized (no auth-token found). "
    "Create a Prysm wallet first, then re-run Enable Keymanager API."
)

# Clients that support enable_keymanager_api (Phase 1).
_ENABLEABLE_CLIENTS: frozenset[str] = frozenset(
    {"lighthouse", "lodestar", "nimbus", "prysm"}
)

# Flags required to enable keymanager per client (order preserved when adding).
# Presence is checked by flag *name* (before '='); values are defaults we set.
_ENABLE_FLAGS: dict[str, tuple[str, ...]] = {
    "lighthouse": (
        "--http",
        "--http-port=5062",
        "--http-address=127.0.0.1",
        # Lighthouse refuses to start with --http-address unless this is also set.
        "--unencrypted-http-transport",
    ),
    "lodestar": (
        "--keymanager",
        "--keymanager.port=5062",
        "--keymanager.address=127.0.0.1",
    ),
    "nimbus": (
        "--keymanager",
        "--keymanager-port=7500",
        "--keymanager-address=127.0.0.1",
        # Required by Nimbus; file must pre-exist (see ensure_nimbus_keymanager_token_file).
        f"--keymanager-token-file={NIMBUS_KEYMANAGER_TOKEN_FILE}",
    ),
    "prysm": (
        "--rpc",
        "--rpc-host=127.0.0.1",
        "--rpc-port=7500",
        "--enable-beacon-rest-api",
        # --beacon-rpc-provider added dynamically when consensus is Prysm.
    ),
}

# Substrings expected in ExecStart for a rough client match.
_CLIENT_BINARY_HINTS: dict[str, tuple[str, ...]] = {
    "lighthouse": ("lighthouse",),
    "lodestar": ("lodestar",),
    "nimbus": ("nimbus_validator_client", "nimbus_beacon_node", "nimbus"),
    "prysm": ("prysm-validator", "prysm-beacon-chain", "prysm"),
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class KeymanagerError(Exception):
    """Base exception for Keymanager API client failures."""


class KeymanagerConnectionError(KeymanagerError):
    """Raised when the Keymanager API cannot be reached."""


class KeymanagerAuthError(KeymanagerError):
    """Raised when authentication fails (HTTP 401/403)."""


class KeymanagerAPIError(KeymanagerError):
    """Raised for non-success HTTP responses from the Keymanager API.

    Attributes:
        status_code: HTTP status code from the response.
        body: Response body text (may be empty).
    """

    def __init__(self, message: str, status_code: int, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class KeymanagerValidationError(KeymanagerError):
    """Raised for invalid client-side arguments (e.g. mismatched list lengths)."""


def is_prysm_empty_keymanager_response(status_code: int, body: str) -> bool:
    """True for Prysm's empty-wallet GET /keystores response.

    Fresh Prysm wallets with Keymanager RPC enabled but no keystores imported
    return HTTP 500 with message ``keymanager is not initialized``. That means
    the API is reachable; there are simply zero keys yet.
    """
    if status_code != 500:
        return False
    return "keymanager is not initialized" in (body or "").lower()


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def normalize_client_name(client: str) -> str:
    """Normalize a validator client name to a lowercase canonical key.

    Accepts values like ``"Lighthouse"``, ``"lighthouse"``, ``"LH"``-style
    full names used in EthPillar env (``VALIDATOR_CLIENT=NIMBUS``).

    Raises:
        KeymanagerValidationError: Empty or unsupported client name.
    """
    raw = (client or "").strip().lower()
    if not raw:
        raise KeymanagerValidationError("client name must be a non-empty string")

    aliases = {
        "lh": "lighthouse",
        "lighthouse": "lighthouse",
        "ls": "lodestar",
        "lodestar": "lodestar",
        "nimbus": "nimbus",
        "prysm": "prysm",
        "teku": "teku",
        "grandine": "grandine",
    }
    # Strip common suffixes: "lighthouse_validator", "nimbus-vc", etc.
    cleaned = re.sub(r"[_\-\s]*(validator|vc|client)$", "", raw)
    cleaned = cleaned.strip("_- ")
    if cleaned in aliases:
        return aliases[cleaned]
    if raw in aliases:
        return aliases[raw]
    raise KeymanagerValidationError(
        f"Unsupported validator client for keymanager detection: {client!r}. "
        f"Supported: {', '.join(sorted(_CLIENT_PORTS))}"
    )


def normalize_network(network: str) -> str:
    """Normalize a network name (case-insensitive)."""
    net = (network or "mainnet").strip().lower()
    if not net:
        return "mainnet"
    return net


def _token_path_candidates(client: str, network: str) -> list[str]:
    """Return ordered filesystem paths that may hold a keymanager bearer token.

    Paths follow EthPillar layouts (``/var/lib/<client>_validator/...``) and
    common combined / home-directory layouts. For most clients,
    network-specific subdirs (including ``mainnet``) follow the non-network
    EthPillar paths; see each client branch for the exact order (e.g. the
    Lighthouse home-dir network path precedes ``~/.lighthouse/validators``,
    and Teku adds no network-specific paths).
    """
    net = normalize_network(network)
    base = _BASE_DATA_DIR
    home = os.path.expanduser("~")
    paths: list[str] = []

    def add(*parts: str) -> None:
        path = os.path.join(*parts)
        if path not in paths:
            paths.append(path)

    if client == "lighthouse":
        # EthPillar split VC datadir; token written when --http is enabled.
        add(base, "lighthouse_validator", "validators", "api-token.txt")
        add(base, "lighthouse", "validators", "api-token.txt")
        # Network-scoped datadirs (docker / multi-net hosts).
        add(base, "lighthouse_validator", net, "validators", "api-token.txt")
        add(base, "lighthouse", net, "validators", "api-token.txt")
        add(home, ".lighthouse", net, "validators", "api-token.txt")
        add(home, ".lighthouse", "validators", "api-token.txt")

    elif client == "lodestar":
        # Lodestar keymanager token (when --keymanager is enabled).
        add(base, "lodestar_validator", "validator-db", "api-token.txt")
        add(base, "lodestar_validator", "api-token.txt")
        add(base, "lodestar", "validators", "validator-db", "api-token.txt")
        add(base, "lodestar", "validators", "api-token.txt")
        add(base, "lodestar_validator", net, "validator-db", "api-token.txt")
        add(base, "lodestar_validator", net, "api-token.txt")
        add(base, "lodestar", net, "validators", "api-token.txt")

    elif client == "nimbus":
        # EthPillar creates api-token.txt under --data-dir (Nimbus reads it; it
        # does not create it) — see ensure_nimbus_keymanager_token_file.
        add(base, "nimbus_validator", "api-token.txt")
        add(base, "nimbus", "api-token.txt")
        add(base, "nimbus_validator", "validators", "api-token.txt")
        add(base, "nimbus_validator", net, "api-token.txt")
        add(base, "nimbus", net, "api-token.txt")

    elif client == "prysm":
        # Prefer wallet-dir token, then validator-user home (common Prysm layout).
        add(base, "prysm_validator", "validator_keys", "auth-token")
        add(PRYSM_VALIDATOR_HOME_AUTH_TOKEN)
        add(base, "prysm_validator", "auth-token")
        add(base, "prysm", "validators", "auth-token")
        add(base, "prysm_validator", net, "validator_keys", "auth-token")
        add(base, "prysm", net, "validators", "auth-token")
        add(home, ".eth2validators", "prysm-wallet-v2", "auth-token")

    elif client == "teku":
        add(base, "teku_validator", "validator", "key-manager", "validator-api-bearer")
        add(base, "teku", "validator", "key-manager", "validator-api-bearer")
        add(base, "teku_validator", "validator-api-bearer")

    return paths


def _read_text_file(path: str) -> Optional[str]:
    """Read a text file, optionally via ``sudo`` if permission is denied.

    Returns:
        File contents, or ``None`` if the file is missing or unreadable.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except PermissionError:
        logger.debug("Permission denied reading %s; trying sudo", path)
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
            logger.debug("sudo cat failed for %s (rc=%s)", path, result.returncode)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("sudo cat error for %s: %s", path, exc)
        return None
    except OSError as exc:
        logger.debug("Failed to read %s: %s", path, exc)
        return None


def _parse_token_contents(raw: str, path: str) -> Optional[str]:
    """Extract a bearer token from file contents.

    Handles:
    - Single-line token files (Lighthouse, Lodestar, Nimbus, Teku).
    - Prysm multi-line ``auth-token`` (URL / host line + JWT on last line).
    - Optional ``Bearer `` prefix.
    """
    if not raw:
        return None

    lines = [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return None

    # Prefer the last non-URL line (Prysm auth-token: host/URL lines + JWT last).
    token: Optional[str] = None
    for line in reversed(lines):
        candidate = line
        if candidate.lower().startswith("bearer "):
            candidate = candidate[7:].strip()
        if candidate.startswith("http://") or candidate.startswith("https://"):
            continue
        # Prysm sometimes writes "localhost:7500" as a non-token line.
        if re.fullmatch(r"[A-Za-z0-9._-]+:\d+", candidate):
            continue
        token = candidate
        break

    if not token:
        logger.warning("Token file %s had no usable bearer token line", path)
        return None
    return token


def find_token_file(client: str, network: str = "mainnet") -> Optional[str]:
    """Locate the first readable keymanager token file for *client*.

    Returns:
        Absolute path to the token file, or ``None`` if none found.
    """
    canonical = normalize_client_name(client)
    for path in _token_path_candidates(canonical, network):
        if os.path.isfile(path) or _path_exists_via_sudo(path):
            logger.debug("Candidate token file exists: %s", path)
            # Confirm we can extract a token (may need sudo for content).
            raw = _read_text_file(path)
            if raw is not None and _parse_token_contents(raw, path):
                logger.info("Found keymanager token file for %s: %s", canonical, path)
                return path
            if raw is not None:
                logger.debug("File exists but no token parsed: %s", path)
    logger.info("No keymanager token file found for %s (network=%s)", canonical, network)
    return None


def _path_exists_via_sudo(path: str) -> bool:
    """Return True if *path* exists (sudo fallback: regular file only, via ``test -f``)."""
    if os.path.exists(path):
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


def read_keymanager_token(client: str, network: str = "mainnet") -> Optional[str]:
    """Read the bearer token for *client* from common token file locations.

    Returns:
        Token string, or ``None`` if not found / unreadable.
    """
    path = find_token_file(client, network)
    if not path:
        return None
    raw = _read_text_file(path)
    if raw is None:
        return None
    token = _parse_token_contents(raw, path)
    if token:
        logger.debug("Loaded keymanager token from %s (%d chars)", path, len(token))
    return token


def detect_keymanager(
    client: str,
    network: str = "mainnet",
    host: str = "127.0.0.1",
) -> tuple[str, Optional[str]]:
    """Detect the likely Keymanager API base URL and bearer token for a client.

    Uses client-specific default ports and EthPillar / common data-directory
    token paths. Does **not** probe the network or enable the API — it only
    resolves configuration that is already present on disk.

    Args:
        client: Validator client name (e.g. ``"lighthouse"``, ``"Nimbus"``).
        network: Network name (``mainnet``, ``hoodi``, ``holesky``, ``sepolia``,
            ``ephemery``, …). Affects network-scoped path candidates.
        host: Host for the returned base URL (default ``127.0.0.1``).

    Returns:
        ``(base_url, token)`` where *token* may be ``None`` if no token file
        was found. *base_url* is always set from the client's preferred port
        (e.g. ``http://127.0.0.1:5062`` for Lighthouse).

    Raises:
        KeymanagerValidationError: Unknown client name.
    """
    canonical = normalize_client_name(client)
    net = normalize_network(network)
    ports = _CLIENT_PORTS.get(canonical, DEFAULT_DISCOVERY_PORTS)
    port = ports[0]
    base_url = f"http://{host}:{port}"
    token = read_keymanager_token(canonical, net)

    if canonical == "prysm":
        wallet_info = check_prysm_wallet_ready()
        if not wallet_info["ready"]:
            logger.warning("%s (wallet_dir=%s)", wallet_info["message"], wallet_info["wallet_dir"])
            token = None
        elif token is None:
            # Prefer auth-token next to the resolved wallet-dir.
            raw = _read_text_file(wallet_info["auth_token_path"])
            if raw is not None:
                token = _parse_token_contents(raw, wallet_info["auth_token_path"])

    if token:
        logger.info(
            "detect_keymanager(%s, network=%s) → %s (token found)",
            canonical,
            net,
            base_url,
        )
    else:
        logger.info(
            "detect_keymanager(%s, network=%s) → %s (no token file found)",
            canonical,
            net,
            base_url,
        )
    return base_url, token


# ---------------------------------------------------------------------------
# Prysm wallet readiness (auth-token / wallet-dir)
# ---------------------------------------------------------------------------


def _dir_exists(path: str) -> bool:
    """True if *path* is a directory (direct check or passwordless sudo)."""
    if os.path.isdir(path):
        return True
    try:
        result = subprocess.run(
            ["sudo", "-n", "test", "-d", path],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def scrape_prysm_wallet_dir(service_content: str) -> Optional[str]:
    """Extract ``--wallet-dir=...`` from a systemd unit's ExecStart, if present."""
    try:
        _, _, args = _parse_exec_start(service_content)
    except KeymanagerError:
        return None
    for arg in args:
        if arg.startswith("--wallet-dir="):
            value = arg.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    return None


def resolve_prysm_wallet_dir(
    service_path: Optional[str] = None,
    service_content: Optional[str] = None,
) -> str:
    """Resolve Prysm ``--wallet-dir`` from the unit file, else EthPillar default.

    Order:
      1. Explicit *service_content*
      2. *service_path* (or default ``validator.service``) on disk
      3. :data:`PRYSM_DEFAULT_WALLET_DIR`
    """
    content = service_content
    if content is None:
        path = service_path
        if path is None and _service_file_exists(DEFAULT_VALIDATOR_SERVICE):
            path = DEFAULT_VALIDATOR_SERVICE
        if path and _service_file_exists(path):
            try:
                content = _read_service_file(path)
            except KeymanagerError as exc:
                logger.debug("Could not read service for wallet-dir: %s", exc)
                content = None
    if content:
        scraped = scrape_prysm_wallet_dir(content)
        if scraped:
            logger.debug("Prysm wallet-dir from service: %s", scraped)
            return scraped
    return PRYSM_DEFAULT_WALLET_DIR


def prysm_auth_token_candidates(wallet_dir: Optional[str] = None) -> list[str]:
    """Ordered auth-token paths for Prysm (wallet-dir first, then home layouts).

    Prefer ``<wallet-dir>/auth-token``, then the EthPillar validator-user home
    path used by many Prysm installs, then the current user's
    ``~/.eth2validators/...`` path.
    """
    wd = (wallet_dir or PRYSM_DEFAULT_WALLET_DIR).rstrip("/")
    candidates: list[str] = [
        os.path.join(wd, "auth-token"),
        PRYSM_VALIDATOR_HOME_AUTH_TOKEN,
        os.path.join(
            os.path.expanduser("~"),
            ".eth2validators",
            "prysm-wallet-v2",
            "auth-token",
        ),
    ]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def find_prysm_auth_token(
    wallet_dir: Optional[str] = None,
) -> Optional[str]:
    """Return the first existing/readable Prysm auth-token path, or None."""
    for path in prysm_auth_token_candidates(wallet_dir):
        if not _token_file_exists(path):
            continue
        raw = _read_text_file(path)
        if raw is None:
            # Exists but unreadable without sudo content — still a find if file exists.
            logger.debug("Prysm auth-token exists but unreadable: %s", path)
            return path
        if _parse_token_contents(raw, path):
            logger.info("Found Prysm auth-token at %s", path)
            return path
        # File present but empty/unparseable — still treat as located.
        logger.debug("Prysm auth-token unparseable at %s", path)
        return path
    return None


def check_prysm_wallet_ready(
    service_path: Optional[str] = None,
    service_content: Optional[str] = None,
    wallet_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Check whether a Prysm auth-token is available for the Keymanager API.

    Does not create anything. Used to avoid cryptic HTTP 401 responses when the
    RPC port is open but no wallet/token was ever initialized.

    Readiness: a readable (or at least present) ``auth-token`` is found under
    the resolved wallet-dir **or** known home-path candidates such as
    :data:`PRYSM_VALIDATOR_HOME_AUTH_TOKEN`. Wallet-dir token is preferred.

    Returns:
        Dict with ``ready``, ``wallet_dir``, ``auth_token_path``,
        ``wallet_dir_exists``, ``auth_token_exists``, and ``message``.
    """
    resolved = wallet_dir or resolve_prysm_wallet_dir(
        service_path=service_path,
        service_content=service_content,
    )
    dir_ok = _dir_exists(resolved)
    preferred_token = os.path.join(resolved, "auth-token")
    found_token = find_prysm_auth_token(wallet_dir=resolved)
    token_ok = found_token is not None
    auth_token_path = found_token or preferred_token
    ready = token_ok
    if ready:
        message = f"Prysm wallet ready (auth-token={auth_token_path})"
    else:
        message = PRYSM_WALLET_NOT_READY_MSG
    return {
        "ready": ready,
        "wallet_dir": resolved,
        "auth_token_path": auth_token_path,
        "wallet_dir_exists": dir_ok,
        "auth_token_exists": token_ok,
        "message": message,
    }


def require_prysm_wallet_ready(
    service_path: Optional[str] = None,
    service_content: Optional[str] = None,
    wallet_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Like :func:`check_prysm_wallet_ready`, but raise if the wallet is not ready.

    Raises:
        KeymanagerError: No ``auth-token`` file found.
    """
    info = check_prysm_wallet_ready(
        service_path=service_path,
        service_content=service_content,
        wallet_dir=wallet_dir,
    )
    if not info["ready"]:
        logger.error("%s (wallet_dir=%s)", info["message"], info["wallet_dir"])
        raise KeymanagerError(info["message"])
    return info


def _prysm_network_cli_flag(network: str) -> str:
    """Map EthPillar network name to a Prysm CLI network flag."""
    net = normalize_network(network)
    if net == "mainnet":
        return "--mainnet"
    if net in ("sepolia", "holesky", "hoodi", "ephemery"):
        return f"--{net}"
    return f"--{net}"


def _dir_listing(path: str) -> list[str]:
    """List directory entries (sudo fallback). Empty list if missing/unreadable."""
    try:
        return os.listdir(path)
    except FileNotFoundError:
        return []
    except PermissionError:
        try:
            result = subprocess.run(
                ["sudo", "-n", "ls", "-A", path],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []
            return [line for line in result.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            return []
    except OSError:
        return []


def prysm_direct_wallet_present(wallet_dir: str = PRYSM_DEFAULT_WALLET_DIR) -> bool:
    """True if a Prysm direct wallet already exists under *wallet_dir*.

    Only inspects *wallet_dir* itself (auth-token or any contents). A token under
    ``/home/validator/.eth2validators/...`` does not count as a wallet under a
    different ``--wallet-dir`` path.
    """
    if _token_file_exists(os.path.join(wallet_dir, "auth-token")):
        return True
    if not _dir_exists(wallet_dir):
        return False
    # Non-empty wallet-dir implies a prior wallet create (never wipe).
    return bool(_dir_listing(wallet_dir))


def ensure_prysm_wallet(
    network: str = "mainnet",
    wallet_dir: str = PRYSM_DEFAULT_WALLET_DIR,
    password_file: str = PRYSM_PASSWORD_FILE,
    data_dir: str = PRYSM_DATA_DIR,
    binary: str = PRYSM_VALIDATOR_BINARY,
    owner: str = "validator",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Bootstrap Prysm wallet dirs, password file, and empty direct wallet.

    Idempotent and non-destructive:
      - Never overwrites an existing ``password.txt``
      - Never re-runs ``wallet create`` if wallet-dir is non-empty or an
        auth-token already exists
      - Does **not** import keystores

    Args:
        network: Eth network (``mainnet``, ``hoodi``, …) for ``wallet create``.
        wallet_dir: Prysm ``--wallet-dir`` path.
        password_file: Path for ``--wallet-password-file``.
        data_dir: Parent data directory (``/var/lib/prysm_validator``).
        binary: Path to ``prysm-validator`` binary.
        owner: Unix user for ownership (default ``validator``).
        dry_run: If True, report planned actions without writing.

    Returns:
        Summary dict with keys such as ``dirs_ensured``, ``password_created``,
        ``wallet_created``, ``wallet_already_existed``, ``dry_run``, ``message``.
    """
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "wallet_dir": wallet_dir,
        "password_file": password_file,
        "dirs_ensured": False,
        "password_created": False,
        "password_already_existed": False,
        "wallet_created": False,
        "wallet_already_existed": False,
        "message": "",
    }

    wallet_exists = prysm_direct_wallet_present(wallet_dir)
    password_exists = _token_file_exists(password_file)  # any file exists check
    # Prefer isfile for password (not a token parser)
    if not password_exists:
        password_exists = os.path.isfile(password_file) or _path_exists_via_sudo(
            password_file
        )

    if password_exists:
        result["password_already_existed"] = True
    if wallet_exists:
        result["wallet_already_existed"] = True

    planned: list[str] = []
    if not password_exists:
        planned.append(f"create password file {password_file}")
    if not wallet_exists:
        planned.append(f"create empty direct wallet in {wallet_dir}")
    planned.append(f"ensure dirs/ownership under {data_dir} and {PRYSM_VALIDATOR_HOME}")

    if dry_run:
        result["dirs_ensured"] = True
        result["password_created"] = not password_exists
        result["wallet_created"] = not wallet_exists
        result["message"] = "dry_run: would " + "; ".join(planned)
        logger.info(result["message"])
        return result

    # --- Directories & ownership ---
    home_eth2 = os.path.join(PRYSM_VALIDATOR_HOME, ".eth2validators")
    for directory in (data_dir, wallet_dir, PRYSM_VALIDATOR_HOME, home_eth2):
        try:
            os.makedirs(directory, mode=0o700 if directory == wallet_dir else 0o755, exist_ok=True)
        except PermissionError:
            subprocess.run(
                ["sudo", "mkdir", "-p", directory],
                check=True,
                capture_output=True,
                timeout=15,
            )
        _chown_path(directory, owner)
        if directory == wallet_dir:
            try:
                os.chmod(wallet_dir, 0o700)
            except OSError:
                subprocess.run(
                    ["sudo", "chmod", "700", wallet_dir],
                    check=False,
                    capture_output=True,
                    timeout=10,
                )
    result["dirs_ensured"] = True

    # --- Password file (create once) ---
    if not password_exists:
        password = secrets.token_hex(32)
        try:
            fd = os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(password + "\n")
            os.chmod(password_file, 0o600)
            _chown_path(password_file, owner)
        except FileExistsError:
            result["password_already_existed"] = True
        except PermissionError:
            script = (
                'set -euo pipefail\n'
                'path="$1"; owner="$2"\n'
                'if [ -e "$path" ]; then exit 2; fi\n'
                'umask 077\n'
                'cat > "$path"\n'
                'chmod 600 "$path"\n'
                'chown "$owner:$owner" "$path" 2>/dev/null || true\n'
            )
            proc = subprocess.run(
                ["sudo", "bash", "-c", script, "ensure_prysm_pw", password_file, owner],
                input=password + "\n",
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 2:
                result["password_already_existed"] = True
            elif proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "failed").strip()
                raise KeymanagerError(
                    f"Failed to create Prysm wallet password file: {err}"
                )
            else:
                result["password_created"] = True
        else:
            result["password_created"] = True
    else:
        result["password_already_existed"] = True

    # --- Empty direct wallet (create once) ---
    if wallet_exists:
        result["wallet_already_existed"] = True
        result["message"] = (
            f"Prysm wallet already present under {wallet_dir}; no overwrite"
        )
        logger.info(result["message"])
        return result

    if not os.path.isfile(binary) and not _path_exists_via_sudo(binary):
        raise KeymanagerError(
            f"Cannot create Prysm wallet: binary not found at {binary}"
        )

    net_flag = _prysm_network_cli_flag(network)
    cmd = [
        "sudo",
        "-u",
        owner,
        binary,
        "wallet",
        "create",
        net_flag,
        f"--wallet-dir={wallet_dir}",
        f"--wallet-password-file={password_file}",
        "--keymanager-kind=direct",
        "--accept-terms-of-use",
    ]
    # HOME for auth-token / wallet metadata under /home/validator
    env = os.environ.copy()
    env["HOME"] = PRYSM_VALIDATOR_HOME
    logger.info("Creating Prysm direct wallet: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise KeymanagerError(f"Prysm wallet create failed: {exc}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "wallet create failed").strip()
        # Race: wallet appeared concurrently — treat as already existed.
        if prysm_direct_wallet_present(wallet_dir):
            result["wallet_already_existed"] = True
            result["message"] = f"Prysm wallet already present under {wallet_dir}"
            logger.info(result["message"])
            return result
        raise KeymanagerError(f"Prysm wallet create failed: {err}")

    result["wallet_created"] = True
    result["message"] = (
        f"Created Prysm direct wallet at {wallet_dir} "
        f"(password_file={'created' if result['password_created'] else 'existing'})"
    )
    logger.info(result["message"])
    return result


def _consensus_is_prysm() -> bool:
    """True if consensus.service looks like a Prysm beacon node."""
    if not _service_file_exists(DEFAULT_CONSENSUS_SERVICE):
        return False
    try:
        content = _read_service_file(DEFAULT_CONSENSUS_SERVICE).lower()
    except KeymanagerError:
        return False
    return "prysm-beacon" in content or (
        "prysm" in content and "beacon" in content
    )


def enable_flags_for_client(
    client: str,
    service_content: Optional[str] = None,
) -> list[str]:
    """Return the ordered enable-flag list for *client* (may be dynamic for Prysm)."""
    canonical = normalize_client_name(client)
    if canonical not in _ENABLE_FLAGS:
        raise KeymanagerValidationError(
            f"Keymanager enablement not supported for {client!r}"
        )
    flags = list(_ENABLE_FLAGS[canonical])
    if canonical == "prysm" and _consensus_is_prysm():
        rpc_flag = "--beacon-rpc-provider=127.0.0.1:4000"
        if not any(_flag_name(f) == "--beacon-rpc-provider" for f in flags):
            flags.append(rpc_flag)
    return flags


# ---------------------------------------------------------------------------
# Enablement (systemd service patching)
# ---------------------------------------------------------------------------


def _flag_name(flag: str) -> str:
    """Return the flag name without a value (``--http-port`` from ``--http-port=5062``)."""
    from manage.service_parse import flag_name

    return flag_name(flag)


def _args_include_flag(args: list[str], desired: str) -> bool:
    """Return True if *args* already contains a flag with the same name as *desired*."""
    want = _flag_name(desired)
    for arg in args:
        if _flag_name(arg) == want:
            return True
    return False


def _parse_exec_start(content: str) -> tuple[int, int, list[str]]:
    """Parse the multi-line ``ExecStart=`` block from a systemd unit.

    Returns:
        ``(start_line_index, end_line_index, args)`` where *args* is the ordered
        list of ExecStart tokens (binary first), and indices refer to
        ``content.splitlines()``.

    Raises:
        KeymanagerError: If ``ExecStart=`` is missing or empty.
    """
    from manage.service_parse import ServiceParseError, parse_exec_start

    try:
        return parse_exec_start(content)
    except ServiceParseError as exc:
        raise KeymanagerError(str(exc)) from exc


def _rebuild_service_content(
    content: str,
    start: int,
    end: int,
    args: list[str],
) -> str:
    """Rebuild unit file content with a new ExecStart argument list."""
    from manage.service_parse import rebuild_service_content

    return rebuild_service_content(content, start, end, args)


def plan_keymanager_flags(content: str, client: str) -> tuple[list[str], list[str], list[str]]:
    """Compute which keymanager flags are present vs missing in a service file.

    Args:
        content: Full systemd unit file text.
        client: Validator client name.

    Returns:
        ``(desired_flags, already_present, to_add)`` — all are ordered lists of
        flag strings from the client enable profile.

    Raises:
        KeymanagerValidationError: Unsupported client.
        KeymanagerError: ExecStart missing/unparseable.
    """
    canonical = normalize_client_name(client)
    if canonical not in _ENABLEABLE_CLIENTS:
        raise KeymanagerValidationError(
            f"Keymanager enablement not supported for {client!r}. "
            f"Supported: {', '.join(sorted(_ENABLEABLE_CLIENTS))}"
        )
    desired = enable_flags_for_client(canonical)
    _, _, args = _parse_exec_start(content)
    already = [f for f in desired if _args_include_flag(args, f)]
    to_add = [f for f in desired if not _args_include_flag(args, f)]
    return desired, already, to_add


def patch_service_for_keymanager(content: str, client: str) -> tuple[str, list[str], list[str]]:
    """Return service content with keymanager flags appended to ExecStart.

    Does not write to disk.

    Returns:
        ``(new_content, flags_added, flags_already_present)``.
        If nothing to add, *new_content* equals *content*.
    """
    canonical = normalize_client_name(client)
    desired, already, to_add = plan_keymanager_flags(content, canonical)
    if not to_add:
        return content, [], already

    start, end, args = _parse_exec_start(content)
    new_args = list(args) + to_add
    new_content = _rebuild_service_content(content, start, end, new_args)
    logger.info(
        "Planned keymanager flags for %s: add %s (already present: %s)",
        canonical,
        to_add,
        already,
    )
    return new_content, to_add, already


def _service_file_exists(path: str) -> bool:
    """True if *path* exists as a file (including via passwordless sudo)."""
    if os.path.isfile(path):
        return True
    return _path_exists_via_sudo(path)


def _read_service_file(path: str) -> str:
    """Read a systemd unit file (sudo fallback for root-owned paths)."""
    raw = _read_text_file(path)
    if raw is None:
        raise KeymanagerError(f"Cannot read service file: {path}")
    return raw


def _assert_client_matches_service(content: str, client: str) -> None:
    """Best-effort check that ExecStart looks like the expected client binary."""
    canonical = normalize_client_name(client)
    hints = _CLIENT_BINARY_HINTS.get(canonical, ())
    _, _, args = _parse_exec_start(content)
    blob = " ".join(args).lower()
    if hints and not any(h.lower() in blob for h in hints):
        raise KeymanagerError(
            f"Service ExecStart does not look like {canonical!r} "
            f"(expected one of {hints}). Refusing to modify."
        )


def resolve_keymanager_service_path(
    client: str,
    service_path: Optional[str] = None,
) -> str:
    """Choose the systemd unit path to patch for keymanager enablement.

    Preference order:
      1. Explicit *service_path* if provided.
      2. ``/etc/systemd/system/validator.service`` (split VC — EthPillar default).
      3. For Nimbus only: ``consensus.service`` (in-process validator layouts).

    Raises:
        KeymanagerError: No suitable service file found.
    """
    canonical = normalize_client_name(client)

    if service_path:
        if not _service_file_exists(service_path):
            raise KeymanagerError(f"Service file not found: {service_path}")
        return service_path

    if _service_file_exists(DEFAULT_VALIDATOR_SERVICE):
        logger.info("Using validator service: %s", DEFAULT_VALIDATOR_SERVICE)
        return DEFAULT_VALIDATOR_SERVICE

    if canonical == "nimbus" and _service_file_exists(DEFAULT_CONSENSUS_SERVICE):
        logger.info(
            "No validator.service; falling back to Nimbus consensus.service: %s",
            DEFAULT_CONSENSUS_SERVICE,
        )
        return DEFAULT_CONSENSUS_SERVICE

    raise KeymanagerError(
        f"No service file found for {canonical}. Looked for "
        f"{DEFAULT_VALIDATOR_SERVICE}"
        + (
            f" and {DEFAULT_CONSENSUS_SERVICE}"
            if canonical == "nimbus"
            else ""
        )
        + ". Pass service_path= to override."
    )


def _backup_service_file(path: str) -> str:
    """Copy *path* to a timestamped ``.bak.keymanager.*`` sibling via sudo.

    Returns:
        Absolute backup path.
    """
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup_path = f"{path}.bak.keymanager.{stamp}"
    logger.info("Backing up %s → %s", path, backup_path)
    try:
        subprocess.run(
            ["sudo", "cp", "-a", path, backup_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        raise KeymanagerError(f"Failed to backup service file {path}: {err}") from exc
    return backup_path


def _write_service_file(path: str, content: str) -> None:
    """Write unit content to *path* (uses deploy.common helper when available)."""
    try:
        from deploy.common import write_service_file

        write_service_file(content, path, temp_filename="keymanager_temp.service")
    except Exception:
        # Fallback: temp file + sudo cp (same pattern as deploy.common).
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix="keymanager_", suffix=".service")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            subprocess.run(["sudo", "cp", tmp, path], check=True)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _restart_systemd_unit(service_path: str) -> str:
    """daemon-reload and restart the unit named by *service_path*.

    Returns:
        Unit name that was restarted (e.g. ``validator.service``).
    """
    unit = os.path.basename(service_path)
    logger.info("Reloading systemd and restarting %s", unit)
    try:
        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["sudo", "systemctl", "restart", unit],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        raise KeymanagerError(f"Failed to restart {unit}: {err}") from exc
    return unit


def _wait_for_token(
    client: str,
    network: str,
    attempts: int = 10,
    delay_sec: float = 1.0,
) -> Optional[str]:
    """Poll for a keymanager token file after enabling the API."""
    for i in range(attempts):
        token = read_keymanager_token(client, network)
        if token:
            return token
        if i + 1 < attempts:
            time.sleep(delay_sec)
    return None


def _token_file_exists(path: str) -> bool:
    """True if *path* exists as a regular file (direct or via sudo)."""
    if os.path.isfile(path):
        return True
    return _path_exists_via_sudo(path)


def _chown_path(path: str, owner: str) -> None:
    """Best-effort chown *path* to *owner*:*owner* (direct, then sudo)."""
    try:
        import grp
        import pwd

        uid = pwd.getpwnam(owner).pw_uid
        gid = grp.getgrnam(owner).gr_gid
        os.chown(path, uid, gid)
        return
    except (ImportError, KeyError, PermissionError, OSError):
        pass
    try:
        subprocess.run(
            ["sudo", "chown", f"{owner}:{owner}", path],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("chown %s to %s failed: %s", path, owner, exc)


def ensure_nimbus_keymanager_token_file(
    token_path: str = NIMBUS_KEYMANAGER_TOKEN_FILE,
    owner: str = "validator",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ensure the Nimbus keymanager token file exists with secure permissions.

    Nimbus requires ``--keymanager-token-file`` to point at an existing file; it
    does not create the file on startup. This helper:

    - Creates the parent directory when missing
    - Creates the token file with a 32-byte hex secret when missing
    - Sets mode ``600`` and ownership ``owner:owner`` (best-effort)
    - Never overwrites an existing token file

    Args:
        token_path: Absolute path for the token file.
        owner: Unix user/group for ownership (default ``validator``).
        dry_run: If True, report whether the file would be created; no writes.

    Returns:
        Summary dict with ``path``, ``created``, ``already_existed``,
        ``dry_run``, and ``message``. On real create, may include ``token``.
    """
    result: dict[str, Any] = {
        "path": token_path,
        "created": False,
        "already_existed": False,
        "dry_run": dry_run,
        "message": "",
    }

    if _token_file_exists(token_path):
        result["already_existed"] = True
        result["message"] = f"Token file already exists: {token_path}"
        logger.info(result["message"])
        return result

    if dry_run:
        result["created"] = True  # would create
        result["message"] = f"dry_run: would create secure token file at {token_path}"
        logger.info(result["message"])
        return result

    token = secrets.token_hex(32)
    data_dir = os.path.dirname(token_path) or "."

    try:
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        try:
            fd = os.open(token_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            result["already_existed"] = True
            result["message"] = f"Token file already exists: {token_path}"
            logger.info(result["message"])
            return result
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
        os.chmod(token_path, 0o600)
        _chown_path(data_dir, owner)
        _chown_path(token_path, owner)
    except PermissionError:
        # Root-owned EthPillar paths: create via sudo without overwriting.
        try:
            subprocess.run(
                ["sudo", "mkdir", "-p", data_dir],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            # Exit 2 if file appeared between check and create (no overwrite).
            script = (
                'set -euo pipefail\n'
                'token_path="$1"\n'
                'owner="$2"\n'
                'if [ -e "$token_path" ]; then exit 2; fi\n'
                'umask 077\n'
                'cat > "$token_path"\n'
                'chmod 600 "$token_path"\n'
                'chown "$owner:$owner" "$token_path" 2>/dev/null || true\n'
                'chown "$owner:$owner" "$(dirname "$token_path")" 2>/dev/null || true\n'
            )
            proc = subprocess.run(
                ["sudo", "bash", "-c", script, "ensure_nimbus_token", token_path, owner],
                input=token + "\n",
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 2:
                result["already_existed"] = True
                result["message"] = f"Token file already exists: {token_path}"
                logger.info(result["message"])
                return result
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "sudo create failed").strip()
                raise KeymanagerError(
                    f"Failed to create Nimbus keymanager token file {token_path}: {err}"
                )
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeymanagerError(
                f"Failed to create Nimbus keymanager token file {token_path}: {exc}"
            ) from exc
    except OSError as exc:
        raise KeymanagerError(
            f"Failed to create Nimbus keymanager token file {token_path}: {exc}"
        ) from exc

    result["created"] = True
    result["token"] = token
    result["message"] = (
        f"Created Nimbus keymanager token file {token_path} "
        f"(mode 600, owner {owner}:{owner})"
    )
    logger.info(result["message"])
    return result


def enable_keymanager_api(
    client: str,
    network: str = "mainnet",
    dry_run: bool = False,
    service_path: Optional[str] = None,
    restart: bool = True,
    host: str = "127.0.0.1",
) -> dict[str, Any]:
    """Enable the Keymanager API for a validator client via its systemd unit.

    Patches ``ExecStart`` to add client-specific flags, backs up the original
    unit, reloads systemd, and restarts the service (unless *dry_run* or
    *restart* is false). Does not implement Teku/Grandine.

    Args:
        client: Validator client (``lighthouse``, ``lodestar``, ``nimbus``,
            ``prysm`` — case-insensitive).
        network: Network name for post-enable token path discovery.
        dry_run: If True, only report planned changes; no write/restart.
        service_path: Optional explicit unit path. Default: prefer
            ``validator.service``, then Nimbus ``consensus.service``.
        restart: If True (default), restart the unit after a real write.
        host: Host used in returned ``base_url``.

    Returns:
        Summary dict with keys including::

            client, network, dry_run, service_path, backup_path,
            flags_desired, flags_already_present, flags_added,
            changed, restarted, base_url, token, message

    Raises:
        KeymanagerValidationError: Unsupported client.
        KeymanagerError: Service missing, parse failure, backup/write/restart
            failure, or client binary mismatch.
    """
    canonical = normalize_client_name(client)
    if canonical not in _ENABLEABLE_CLIENTS:
        raise KeymanagerValidationError(
            f"Keymanager enablement not supported for {client!r}. "
            f"Supported: {', '.join(sorted(_ENABLEABLE_CLIENTS))}"
        )
    net = normalize_network(network)
    path = resolve_keymanager_service_path(canonical, service_path=service_path)
    content = _read_service_file(path)
    _assert_client_matches_service(content, canonical)

    # Prysm: bootstrap empty wallet/password if missing (never overwrite).
    prysm_wallet: Optional[dict[str, Any]] = None
    prysm_bootstrap: Optional[dict[str, Any]] = None
    if canonical == "prysm":
        wallet_dir = resolve_prysm_wallet_dir(
            service_path=path, service_content=content
        )
        prysm_bootstrap = ensure_prysm_wallet(
            network=net,
            wallet_dir=wallet_dir,
            dry_run=dry_run,
        )
        prysm_wallet = check_prysm_wallet_ready(
            service_path=path,
            service_content=content,
            wallet_dir=wallet_dir,
        )
        # After bootstrap, wallet files should exist; auth-token may wait until restart.
        if (
            not dry_run
            and not prysm_wallet["ready"]
            and not prysm_bootstrap.get("wallet_created")
            and not prysm_bootstrap.get("wallet_already_existed")
        ):
            raise KeymanagerError(prysm_wallet["message"])

    new_content, flags_added, flags_already = patch_service_for_keymanager(
        content, canonical
    )
    desired = enable_flags_for_client(canonical)

    preferred_url, _ = detect_keymanager(canonical, net, host=host)
    summary: dict[str, Any] = {
        "client": canonical,
        "network": net,
        "dry_run": dry_run,
        "service_path": path,
        "backup_path": None,
        "flags_desired": desired,
        "flags_already_present": flags_already,
        "flags_added": flags_added,
        "changed": bool(flags_added),
        "restarted": False,
        "base_url": preferred_url,
        "token": None,
        "message": "",
        "nimbus_token_file": None,
        "prysm_wallet": prysm_wallet,
        "prysm_wallet_bootstrap": prysm_bootstrap,
    }

    # Nimbus: token file must exist before the process starts (does not auto-create).
    if canonical == "nimbus":
        summary["nimbus_token_file"] = ensure_nimbus_keymanager_token_file(
            dry_run=dry_run
        )
        if summary["nimbus_token_file"].get("created") and not dry_run:
            summary["changed"] = True

    if prysm_bootstrap and (
        prysm_bootstrap.get("wallet_created") or prysm_bootstrap.get("password_created")
    ):
        summary["changed"] = True

    if not flags_added and not (
        summary["changed"] and canonical in ("nimbus", "prysm")
    ):
        token_note = ""
        ntf = summary.get("nimbus_token_file") or {}
        if ntf.get("created"):
            token_note = (
                " Token file would be created."
                if dry_run
                else f" Created token file {ntf.get('path')}."
            )
        pb = prysm_bootstrap or {}
        if pb.get("wallet_already_existed") or pb.get("password_already_existed"):
            token_note += " Prysm wallet already present."
        summary["message"] = (
            f"Keymanager flags already present for {canonical} in {path}; "
            f"no service changes needed.{token_note}"
        )
        logger.info(summary["message"])
        # Still try to surface token/endpoint if API is already up.
        base_url, token = detect_keymanager(canonical, net, host=host)
        summary["base_url"] = base_url
        summary["token"] = token or (ntf.get("token") if ntf else None)
        if not dry_run:
            try:
                discovered = KeymanagerClient.discover(
                    client=canonical, network=net, host=host, timeout=2.0
                )
            except KeymanagerError:
                discovered = None
            if discovered is not None:
                summary["base_url"] = discovered.base_url
                summary["token"] = discovered.token or summary["token"]
        return summary

    # Flags already present but Prysm wallet/password bootstrap needs a restart
    if not flags_added and summary["changed"] and not dry_run and restart:
        if canonical == "prysm" and (
            (prysm_bootstrap or {}).get("wallet_created")
            or (prysm_bootstrap or {}).get("password_created")
        ):
            unit = _restart_systemd_unit(path)
            summary["restarted"] = True
            summary["unit"] = unit
            token = _wait_for_token(canonical, net)
            summary["token"] = token
            summary["message"] = (
                f"Prysm wallet bootstrapped; restarted {unit} "
                f"(flags already present)."
            )
            logger.info(summary["message"])
            return summary

    logger.info(
        "Enable keymanager for %s: will add flags %s to %s (dry_run=%s)",
        canonical,
        flags_added,
        path,
        dry_run,
    )

    if dry_run:
        ntf = summary.get("nimbus_token_file") or {}
        token_note = ""
        if ntf.get("created"):
            token_note = f"; would create token file {ntf.get('path')}"
        elif ntf.get("already_existed"):
            token_note = f"; token file exists at {ntf.get('path')}"
        pb = prysm_bootstrap or {}
        if pb.get("message"):
            token_note += f"; {pb['message']}"
        flag_part = (
            f"would add {flags_added}"
            if flags_added
            else "no new flags"
        )
        summary["message"] = (
            f"dry_run: {flag_part} for {path} and "
            f"{'restart' if restart else 'not restart'} the unit"
            f"{token_note}"
        )
        return summary

    backup_path = _backup_service_file(path)
    summary["backup_path"] = backup_path
    _write_service_file(path, new_content)
    logger.info("Wrote updated service file %s", path)

    if restart:
        unit = _restart_systemd_unit(path)
        summary["restarted"] = True
        summary["unit"] = unit
        # Token files are often created on first listen after restart.
        token = _wait_for_token(canonical, net)
        summary["token"] = token
        discovered = KeymanagerClient.discover(
            client=canonical,
            network=net,
            host=host,
            token=token,
            timeout=2.0,
        )
        if discovered is not None:
            summary["base_url"] = discovered.base_url
            summary["token"] = discovered.token or token
        else:
            base_url, detected = detect_keymanager(canonical, net, host=host)
            summary["base_url"] = base_url
            summary["token"] = token or detected
        summary["message"] = (
            f"Enabled keymanager for {canonical}: added {flags_added}, "
            f"backed up to {backup_path}, restarted {unit}."
        )
    else:
        base_url, token = detect_keymanager(canonical, net, host=host)
        summary["base_url"] = base_url
        summary["token"] = token
        summary["message"] = (
            f"Enabled keymanager for {canonical}: added {flags_added}, "
            f"backed up to {backup_path}; service not restarted "
            f"(restart=False)."
        )

    logger.info(summary["message"])
    return summary


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class KeymanagerClient:
    """HTTP client for the Ethereum Keymanager API (local keystores).

    Talks to a validator client's keymanager HTTP endpoint (e.g. Lighthouse,
    Teku, Nimbus, Prysm, Lodestar) using the standard ``/eth/v1/keystores``
    routes. Only local keystore import/list/delete is supported.

    Args:
        base_url: Base URL of the keymanager API, e.g.
            ``"http://127.0.0.1:5062"``. Trailing slashes are stripped.
        token: Optional Bearer token for ``Authorization`` header. Most
            clients require this for mutating endpoints.
        timeout: Request timeout in seconds (default 30).
    """

    KEYSTORES_PATH = "/eth/v1/keystores"

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        if not base_url or not base_url.strip():
            raise KeymanagerValidationError("base_url must be a non-empty string")

        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    # -- public API ----------------------------------------------------------

    def list_keys(self) -> list[dict[str, Any]]:
        """List local validating keystores known to the keymanager.

        Calls ``GET /eth/v1/keystores``.

        Prysm empty wallets return HTTP 500 ``keymanager is not initialized``;
        that is treated as an empty list (API available, zero keys).

        Returns:
            List of key objects from the response ``data`` field. Each item
            typically includes ``validating_pubkey``, ``derivation_path``,
            and ``readonly``.

        Raises:
            KeymanagerConnectionError: Network/connection failure.
            KeymanagerAuthError: HTTP 401 or 403.
            KeymanagerAPIError: Other non-success status codes.
        """
        keys, _meta = self.list_keys_with_meta()
        return keys

    def list_keys_with_meta(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Like :meth:`list_keys`, plus metadata for status/TUI classification.

        Returns:
            ``(keys, meta)`` where *meta* may include
            ``empty_uninitialized`` (Prysm empty wallet) and ``available``.
        """
        meta: dict[str, Any] = {
            "available": True,
            "empty_uninitialized": False,
        }
        try:
            response = self._request("GET", self.KEYSTORES_PATH)
        except KeymanagerAPIError as exc:
            if is_prysm_empty_keymanager_response(exc.status_code, exc.body):
                logger.info(
                    "Prysm keymanager not initialized (empty wallet) at %s; "
                    "treating as available with 0 keys",
                    self.base_url,
                )
                meta["empty_uninitialized"] = True
                return [], meta
            raise
        payload = self._parse_json(response)
        data = payload.get("data")
        if not isinstance(data, list):
            raise KeymanagerAPIError(
                "Unexpected list_keys response: missing or invalid 'data' array",
                status_code=response.status_code,
                body=response.text,
            )
        return data, meta

    def import_keystores(
        self,
        keystores: list[str],
        passwords: list[str],
        slashing_protection: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Import EIP-2335 keystore JSON strings into the local keymanager.

        Calls ``POST /eth/v1/keystores``.

        Args:
            keystores: List of keystore JSON strings (EIP-2335).
            passwords: Password for each keystore; must match ``keystores`` length.
            slashing_protection: Optional EIP-3076 slashing-protection JSON string
                to import alongside the keystores.

        Returns:
            Per-keystore status list from the response ``data`` field
            (e.g. ``status`` of ``imported``, ``duplicate``, or ``error``).

        Raises:
            KeymanagerValidationError: Empty lists or length mismatch.
            KeymanagerConnectionError: Network/connection failure.
            KeymanagerAuthError: HTTP 401 or 403.
            KeymanagerAPIError: Other non-success status codes.
        """
        if not keystores:
            raise KeymanagerValidationError("keystores must be a non-empty list")
        if len(keystores) != len(passwords):
            raise KeymanagerValidationError(
                f"keystores and passwords length mismatch: "
                f"{len(keystores)} keystore(s), {len(passwords)} password(s)"
            )

        body: dict[str, Any] = {
            "keystores": keystores,
            "passwords": passwords,
        }
        if slashing_protection is not None:
            body["slashing_protection"] = slashing_protection

        response = self._request("POST", self.KEYSTORES_PATH, json_body=body)
        payload = self._parse_json(response)
        data = payload.get("data")
        if not isinstance(data, list):
            raise KeymanagerAPIError(
                "Unexpected import_keystores response: missing or invalid 'data' array",
                status_code=response.status_code,
                body=response.text,
            )
        return data

    def delete_keys(self, pubkeys: list[str]) -> dict[str, Any]:
        """Delete local validating keys by public key.

        Calls ``DELETE /eth/v1/keystores``.

        Args:
            pubkeys: List of validating public keys (``0x``-prefixed hex).

        Returns:
            Full response body as a dict. Includes ``data`` (per-key status
            list) and optionally ``slashing_protection`` (EIP-3076 JSON string
            for the deleted keys).

        Raises:
            KeymanagerValidationError: Empty pubkey list.
            KeymanagerConnectionError: Network/connection failure.
            KeymanagerAuthError: HTTP 401 or 403.
            KeymanagerAPIError: Other non-success status codes.
        """
        if not pubkeys:
            raise KeymanagerValidationError("pubkeys must be a non-empty list")

        response = self._request(
            "DELETE",
            self.KEYSTORES_PATH,
            json_body={"pubkeys": pubkeys},
        )
        return self._parse_json(response)

    @classmethod
    def discover(
        cls,
        host: str = "127.0.0.1",
        ports: Optional[list[int]] = None,
        token: Optional[str] = None,
        timeout: float = 2.0,
        client: Optional[str] = None,
        network: str = "mainnet",
    ) -> Optional["KeymanagerClient"]:
        """Discover a reachable local Keymanager API and return a client.

        Strategy:

        1. If *client* is set, use :func:`detect_keymanager` for preferred
           port + on-disk token, then probe that endpoint first.
        2. Probe remaining client-preferred ports.
        3. Then probe *ports* if given, otherwise scan
           :data:`DEFAULT_DISCOVERY_PORTS` (only when *ports* is ``None``).

        An endpoint counts as "found" on any HTTP response except 404 (e.g.
        2xx, 401/403, 5xx). HTTP 404 is ignored (often a beacon REST API on a
        shared port). Connection failures continue to the next port.

        Args:
            host: Host to probe (default localhost).
            ports: Optional extra ports to probe after client-preferred ports.
                When omitted, falls back to :data:`DEFAULT_DISCOVERY_PORTS`.
            token: Optional bearer token override (else loaded from disk when
                *client* is known, or tried from common token paths).
            timeout: Per-request timeout in seconds (short for discovery).
            client: Optional validator client name for targeted detection.
            network: Network name for token path resolution.

        Returns:
            A :class:`KeymanagerClient` for the first reachable endpoint, or
            ``None`` if nothing responded.

        Raises:
            KeymanagerError: *client* is Prysm and no wallet ``auth-token``
                is found (see :func:`check_prysm_wallet_ready`).
        """
        net = normalize_network(network)
        probe_ports: list[int] = []
        resolved_token = token

        # --- Phase 1: client-specific detection ---
        if client:
            try:
                canonical = normalize_client_name(client)
            except KeymanagerValidationError as exc:
                logger.warning("Client-specific detection skipped: %s", exc)
                canonical = ""
            else:
                # Prysm without a wallet still may open :7500 and return 401 — fail clearly.
                if canonical == "prysm":
                    wallet_info = check_prysm_wallet_ready()
                    if not wallet_info["ready"]:
                        logger.error(
                            "%s (wallet_dir=%s)",
                            wallet_info["message"],
                            wallet_info["wallet_dir"],
                        )
                        raise KeymanagerError(wallet_info["message"])
                try:
                    base_url, detected_token = detect_keymanager(
                        client, net, host=host
                    )
                    if resolved_token is None:
                        resolved_token = detected_token
                    preferred = int(urlparse(base_url).port or 0)
                    if preferred:
                        probe_ports.append(preferred)
                    for p in _CLIENT_PORTS.get(canonical, ()):
                        if p not in probe_ports:
                            probe_ports.append(p)
                    logger.info(
                        "Discovery targeting client=%s network=%s ports=%s token=%s",
                        client,
                        net,
                        probe_ports,
                        "set" if resolved_token else "none",
                    )
                except KeymanagerValidationError as exc:
                    logger.warning("Client-specific detection skipped: %s", exc)

        # If no explicit token and no client, try well-known clients' token files.
        if resolved_token is None and not client:
            for name in ("lighthouse", "lodestar", "nimbus", "prysm"):
                resolved_token = read_keymanager_token(name, net)
                if resolved_token:
                    logger.info("Using token discovered for %s", name)
                    break

        # --- Phase 2: explicit ports or broad defaults ---
        if ports is not None:
            for p in ports:
                if p not in probe_ports:
                    probe_ports.append(p)
        else:
            for p in DEFAULT_DISCOVERY_PORTS:
                if p not in probe_ports:
                    probe_ports.append(p)

        for port in probe_ports:
            base_url = f"http://{host}:{port}"
            found = cls._probe_endpoint(base_url, resolved_token, timeout)
            if found is not None:
                return found

        logger.info(
            "No keymanager API discovered on %s ports %s (client=%s)",
            host,
            probe_ports,
            client or "any",
        )
        return None

    @classmethod
    def _probe_endpoint(
        cls,
        base_url: str,
        token: Optional[str],
        timeout: float,
    ) -> Optional["KeymanagerClient"]:
        """Probe a single base URL; return a client if a keymanager answers.

        Counts as found: any HTTP response except 404 (2xx, 401/403, Prysm
        empty-wallet 500, other error statuses).
        Counts as not found: connection errors, other client errors, and 404
        (often a non-keymanager service such as a beacon REST API on the same
        port).
        """
        km = cls(base_url=base_url, token=token, timeout=timeout)
        try:
            km.list_keys()
            logger.info("Discovered keymanager API at %s", base_url)
            return km
        except KeymanagerAuthError:
            logger.info(
                "Discovered keymanager API at %s (auth required or rejected)",
                base_url,
            )
            return km
        except KeymanagerAPIError as exc:
            if is_prysm_empty_keymanager_response(exc.status_code, exc.body):
                logger.info(
                    "Discovered keymanager API at %s (Prysm empty wallet)",
                    base_url,
                )
                return km
            if exc.status_code == 404:
                # Beacon REST and other HTTP services often share 5052/3500.
                logger.debug(
                    "HTTP 404 at %s — not treating as keymanager",
                    base_url,
                )
                return None
            logger.info(
                "Discovered keymanager API at %s (HTTP %s)",
                base_url,
                exc.status_code,
            )
            return km
        except KeymanagerConnectionError:
            logger.debug("No keymanager at %s", base_url)
            return None
        except KeymanagerError as exc:
            logger.debug("Probe failed for %s: %s", base_url, exc)
            return None

    # -- internals -----------------------------------------------------------

    def _url(self, path: str) -> str:
        """Build an absolute URL for *path* under ``base_url``."""
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict[str, Any]] = None,
    ) -> requests.Response:
        """Send an HTTP request and map common failures to custom exceptions."""
        url = self._url(path)
        try:
            response = self._session.request(
                method,
                url,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            msg = f"Keymanager API timed out after {self.timeout}s: {method} {url}"
            logger.debug(msg)
            raise KeymanagerConnectionError(msg) from exc
        except requests.exceptions.ConnectionError as exc:
            msg = f"Cannot connect to Keymanager API at {url}: {exc}"
            logger.debug(msg)
            raise KeymanagerConnectionError(msg) from exc
        except requests.exceptions.RequestException as exc:
            msg = f"Keymanager API request failed: {method} {url}: {exc}"
            logger.debug(msg)
            raise KeymanagerConnectionError(msg) from exc

        if response.status_code in (401, 403):
            msg = (
                f"Keymanager API authentication failed (HTTP {response.status_code}) "
                f"for {method} {url}. Check the Bearer token."
            )
            logger.error(msg)
            raise KeymanagerAuthError(msg)

        if not response.ok:
            body = response.text or ""
            # Prysm empty wallet: API is up; list_keys maps this to [].
            if is_prysm_empty_keymanager_response(response.status_code, body):
                logger.info(
                    "Prysm keymanager not initialized (empty wallet) for %s %s",
                    method,
                    url,
                )
                raise KeymanagerAPIError(
                    "Prysm keymanager is not initialized (empty wallet)",
                    status_code=response.status_code,
                    body=body,
                )
            body_preview = body[:500]
            msg = (
                f"Keymanager API error HTTP {response.status_code} for {method} {url}"
                + (f": {body_preview}" if body_preview else "")
            )
            logger.error(msg)
            raise KeymanagerAPIError(
                msg,
                status_code=response.status_code,
                body=body,
            )

        return response

    @staticmethod
    def _parse_json(response: requests.Response) -> dict[str, Any]:
        """Parse a JSON object response body."""
        try:
            payload = response.json()
        except ValueError as exc:
            raise KeymanagerAPIError(
                f"Keymanager API returned non-JSON body (HTTP {response.status_code})",
                status_code=response.status_code,
                body=response.text or "",
            ) from exc
        if not isinstance(payload, dict):
            raise KeymanagerAPIError(
                f"Keymanager API returned non-object JSON (HTTP {response.status_code})",
                status_code=response.status_code,
                body=response.text or "",
            )
        return payload

    def __repr__(self) -> str:
        parsed = urlparse(self.base_url)
        host = parsed.netloc or self.base_url
        auth = "token=set" if self.token else "token=none"
        return f"KeymanagerClient(base_url={host!r}, {auth}, timeout={self.timeout})"


# ---------------------------------------------------------------------------
# CLI (for EthPillar shell helpers)
# ---------------------------------------------------------------------------


def _cli_load_keystores(directory: str) -> list[str]:
    """Load EIP-2335 keystore JSON strings from a directory."""
    import glob

    if not os.path.isdir(directory):
        raise KeymanagerValidationError(f"Keystore directory not found: {directory}")

    paths = sorted(glob.glob(os.path.join(directory, "keystore*.json")))
    if not paths:
        paths = sorted(
            p
            for p in glob.glob(os.path.join(directory, "*.json"))
            if "deposit_data" not in os.path.basename(p).lower()
        )
    if not paths:
        raise KeymanagerValidationError(
            f"No keystore JSON files found in {directory} "
            f"(expected keystore*.json)"
        )

    keystores: list[str] = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            keystores.append(handle.read())
    return keystores


def _cli_connect(
    client: str,
    network: str,
    host: str,
    timeout: float,
    token: Optional[str] = None,
) -> KeymanagerClient:
    """Discover/connect a KeymanagerClient or raise a clear error."""
    canonical = normalize_client_name(client)
    if canonical == "prysm":
        require_prysm_wallet_ready()
    detected_url, detected_token = detect_keymanager(client, network, host=host)
    use_token = token if token is not None else detected_token
    km = KeymanagerClient.discover(
        host=host,
        token=use_token,
        timeout=timeout,
        client=client,
        network=network,
    )
    if km is None:
        raise KeymanagerConnectionError(
            f"Keymanager API not reachable for {client} "
            f"(expected near {detected_url}). Enable it first."
        )
    return km


def _cli_print(payload: dict[str, Any]) -> None:
    import json

    print(json.dumps(payload, indent=2))


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point for EthPillar shell integration.

    Subcommands: status, list, import, delete, enable.
    Prints JSON to stdout; non-zero exit on failure.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="EthPillar Keymanager API helper (local keystores).",
    )
    parser.add_argument(
        "--client",
        required=True,
        help="Validator client (lighthouse, lodestar, nimbus, prysm)",
    )
    parser.add_argument(
        "--network",
        default="mainnet",
        help="Network name (default: mainnet)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Keymanager host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout seconds (default: 10)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Check API availability and list key count")
    sub.add_parser("list", help="List validating public keys")

    import_p = sub.add_parser("import", help="Import keystores from a directory")
    import_p.add_argument(
        "--dir",
        required=True,
        dest="directory",
        help="Directory containing keystore*.json files",
    )
    import_p.add_argument(
        "--password",
        required=True,
        help="Keystore password (applied to every keystore)",
    )
    import_p.add_argument(
        "--slashing-protection",
        default=None,
        help="Optional path to EIP-3076 slashing protection JSON",
    )

    delete_p = sub.add_parser("delete", help="Delete keys by public key")
    delete_p.add_argument(
        "--pubkeys",
        required=True,
        help="Comma-separated validating public keys (0x...)",
    )

    enable_p = sub.add_parser("enable", help="Enable Keymanager API on the VC service")
    enable_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned changes without writing or restarting",
    )
    enable_p.add_argument(
        "--no-restart",
        action="store_true",
        help="Write service changes but do not restart the unit",
    )
    enable_p.add_argument(
        "--service-path",
        default=None,
        help="Optional path to systemd unit file",
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            base_url, token = detect_keymanager(
                args.client, args.network, host=args.host
            )
            # Prysm: missing wallet/auth-token → clear message (avoid cryptic 401).
            try:
                if normalize_client_name(args.client) == "prysm":
                    wallet_info = check_prysm_wallet_ready()
                    if not wallet_info["ready"]:
                        _cli_print(
                            {
                                "ok": True,
                                "available": False,
                                "base_url": base_url,
                                "token_set": False,
                                "key_count": 0,
                                "keys": [],
                                "prysm_wallet": wallet_info,
                                "message": wallet_info["message"],
                            }
                        )
                        return 0
            except KeymanagerValidationError:
                pass

            try:
                km = KeymanagerClient.discover(
                    host=args.host,
                    token=token,
                    timeout=min(args.timeout, 3.0),
                    client=args.client,
                    network=args.network,
                )
            except KeymanagerError as exc:
                # e.g. Prysm wallet not ready raised from discover
                _cli_print(
                    {
                        "ok": True,
                        "available": False,
                        "base_url": base_url,
                        "token_set": bool(token),
                        "key_count": 0,
                        "keys": [],
                        "message": str(exc),
                    }
                )
                return 0
            if km is None:
                _cli_print(
                    {
                        "ok": True,
                        "available": False,
                        "base_url": base_url,
                        "token_set": bool(token),
                        "key_count": 0,
                        "keys": [],
                        "message": "Keymanager API not reachable",
                    }
                )
                return 0
            try:
                keys, keys_meta = km.list_keys_with_meta()
                empty_uninit = bool(keys_meta.get("empty_uninitialized"))
                msg = "Keymanager API available"
                if empty_uninit:
                    msg = (
                        "Keymanager API available (0 keys; Prysm wallet empty — "
                        "import keystores to initialize)"
                    )
                elif not keys:
                    msg = "Keymanager API available (0 keys)"
                payload: dict[str, Any] = {
                    "ok": True,
                    "available": True,
                    "base_url": km.base_url,
                    "token_set": bool(km.token or token),
                    "key_count": len(keys),
                    "keys": keys,
                    "message": msg,
                }
                if empty_uninit:
                    payload["empty_uninitialized"] = True
                    payload["prysm_empty_wallet"] = True
                _cli_print(payload)
            except KeymanagerAuthError as exc:
                auth_msg = str(exc)
                # If Prysm answers 401 with no usable token, prefer the wallet message.
                try:
                    if normalize_client_name(args.client) == "prysm":
                        wallet_info = check_prysm_wallet_ready()
                        if not wallet_info["ready"]:
                            auth_msg = wallet_info["message"]
                except KeymanagerValidationError:
                    pass
                _cli_print(
                    {
                        "ok": True,
                        "available": False,
                        "base_url": km.base_url,
                        "token_set": bool(km.token or token),
                        "key_count": 0,
                        "keys": [],
                        "auth_error": str(exc),
                        "message": auth_msg,
                    }
                )
            return 0

        if args.command == "list":
            km = _cli_connect(
                args.client, args.network, args.host, args.timeout
            )
            keys, keys_meta = km.list_keys_with_meta()
            payload = {
                "ok": True,
                "base_url": km.base_url,
                "key_count": len(keys),
                "keys": keys,
            }
            if keys_meta.get("empty_uninitialized"):
                payload["empty_uninitialized"] = True
                payload["prysm_empty_wallet"] = True
                payload["message"] = (
                    "No keys loaded (Prysm empty wallet). "
                    "Import keystores to initialize the keymanager."
                )
            _cli_print(payload)
            return 0

        if args.command == "import":
            km = _cli_connect(
                args.client, args.network, args.host, args.timeout
            )
            keystores = _cli_load_keystores(args.directory)
            passwords = [args.password] * len(keystores)
            slashing: Optional[str] = None
            if args.slashing_protection:
                with open(args.slashing_protection, encoding="utf-8") as handle:
                    slashing = handle.read()
            statuses = km.import_keystores(
                keystores, passwords, slashing_protection=slashing
            )
            _cli_print(
                {
                    "ok": True,
                    "base_url": km.base_url,
                    "imported": len(keystores),
                    "statuses": statuses,
                }
            )
            return 0

        if args.command == "delete":
            pubkeys = [
                p.strip() for p in args.pubkeys.split(",") if p.strip()
            ]
            if not pubkeys:
                raise KeymanagerValidationError("No pubkeys provided")
            km = _cli_connect(
                args.client, args.network, args.host, args.timeout
            )
            result = km.delete_keys(pubkeys)
            _cli_print(
                {
                    "ok": True,
                    "base_url": km.base_url,
                    "deleted": len(pubkeys),
                    "pubkeys": pubkeys,
                    "response": result,
                }
            )
            return 0

        if args.command == "enable":
            summary = enable_keymanager_api(
                client=args.client,
                network=args.network,
                dry_run=args.dry_run,
                service_path=args.service_path,
                restart=not args.no_restart,
                host=args.host,
            )
            summary["ok"] = True
            # The JSON includes the full token for shell capture (local use only);
            # the TUI (manage_validator_keys.sh) only reports whether one was found.
            _cli_print(summary)
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 2

    except KeymanagerError as exc:
        _cli_print({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 1
    except OSError as exc:
        _cli_print({"ok": False, "error": str(exc), "error_type": "OSError"})
        return 1
    except Exception as exc:  # pragma: no cover - last-resort CLI guard
        _cli_print({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
