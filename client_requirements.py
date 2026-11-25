"""
Client version requirements for Ethereum networks.

This module defines minimum client versions required for specific fork activations
and provides validation utilities to ensure compatibility.

Networks gated on Fusaka fork (PeerDAS support) minimum versions:
- Ephemery: Activates Fusaka at epoch 10 (resets every 28 days)
- Hoodi: Fusaka active since epoch 50688

Networks gated on Gloas / Glamsterdam (Sepolia, activated 2026-10-06):
- Sepolia: Gloas epoch 353024; Lighthouse floor is stable v8.3.0.
Other networks (including mainnet) are not Gloas-gated here.
"""

from typing import Optional

# Minimum client versions for Fusaka fork (PeerDAS support)
# Enforced only for: Ephemery (active at epoch 10), Hoodi (active since epoch 50688)
FUSAKA_MIN_VERSIONS = {
    # Consensus clients
    'lighthouse': 'v8.0.0',
    'teku': '25.9.3',
    'nimbus': 'v25.9.2',
    'lodestar': 'v1.35.0',
    'grandine': 'v2.0.0',
    'prysm': 'v7.0.0',
    # Execution clients
    'reth': 'v1.7.0',
    'besu': '25.7.0',
    'nethermind': 'v1.34.0',
    'erigon': 'v3.2.1',
    'geth': 'v1.16.3'
}

# Minimum client versions for Sepolia Gloas (activated 2026-10-06 epoch 353024).
GLOAS_SEPOLIA_MIN_VERSIONS = {
    'lighthouse': 'v8.3.0',
    'lodestar': 'v1.49.0',
    # GA --builder-urls / --builder-min-bid / --builder-boost-factor
    # (Consensys/teku#11563, shipped in 26.11.0). 26.9.1–26.10.x only have
    # the hidden --Xbuilder-* names, which 26.11.0 removed.
    'teku': '26.11.0',
    'prysm': 'v7.2.0',
    # Multi-URL --payload-builder-url (status-im/nimbus-eth2#9336) + Gloas VC
    # builder flags; install still resolves GitHub LATEST when it meets this.
    'nimbus': 'v26.10.2',
}

# Install tag to use when GitHub LATEST fails the Sepolia Gloas floor.
# Empty: Lighthouse v8.3.0 is GitHub LATEST, so no preferred remap is needed.
GLOAS_SEPOLIA_PREFERRED_TAGS: dict[str, str] = {}


def parse_version(version_string):
    """
    Pure function: Parse semantic version string into comparable parts.

    Args:
        version_string: Version string (e.g., 'v8.0.0', '25.9.3-rc.0')

    Returns:
        Tuple of (major, minor, patch, prerelease)

    Examples:
        >>> parse_version('v8.0.0')
        (8, 0, 0, None)
        >>> parse_version('25.9.3-rc.0')
        (25, 9, 3, 'rc.0')
    """
    clean_version = version_string.lstrip('v')
    parts = clean_version.split('-')
    version_nums = parts[0].split('.')

    nums = [int(n) if n.isdigit() else 0 for n in version_nums]
    nums.extend([0] * (3 - len(nums)))  # Pad to 3 elements

    prerelease = parts[1] if len(parts) > 1 else None
    return (*nums[:3], prerelease)


def compare_versions(v1, v2):
    """
    Pure function: Compare two semantic version strings.

    Args:
        v1: First version string
        v2: Second version string

    Returns:
        -1 if v1 < v2, 0 if equal, 1 if v1 > v2

    Examples:
        >>> compare_versions('v8.0.0', 'v7.1.0')
        1
        >>> compare_versions('v8.0.0-rc.0', 'v8.0.0')
        -1
    """
    v1_parts = parse_version(v1)
    v2_parts = parse_version(v2)

    # Compare major, minor, patch
    for a, b in zip(v1_parts[:3], v2_parts[:3]):
        if a < b: return -1
        if a > b: return 1

    # Compare prerelease (no prerelease > has prerelease)
    v1_pre, v2_pre = v1_parts[3], v2_parts[3]
    if v1_pre is None and v2_pre is not None: return 1
    if v1_pre is not None and v2_pre is None: return -1
    if v1_pre == v2_pre: return 0
    return -1 if v1_pre < v2_pre else 1


def _normalize_network(network: Optional[str]) -> str:
    """Return lowercase network slug, or empty when unset."""
    return (network or "").strip().lower()


def _normalize_client(client_name: Optional[str]) -> str:
    """Return lowercase client key used in requirement maps."""
    return (client_name or "").strip().lower()


def validate_version_for_network(client_name, version, network):
    """
    Pure function: Validate if version meets network requirements.

    Args:
        client_name: Name of the client (e.g., 'lighthouse', 'reth')
        version: Version string to validate
        network: Network name (e.g., 'ephemery', 'hoodi', 'sepolia', 'mainnet')

    Returns:
        Tuple of (is_valid: bool, error_message: str | None)

    Networks gated on Fusaka (PeerDAS) minimum versions:
    - Ephemery: Active at epoch 10 (resets every 28 days)
    - Hoodi: Active since epoch 50688

    Networks gated on Gloas / Glamsterdam:
    - Sepolia: Active since epoch 353024 (2026-10-06)

    Examples:
        >>> validate_version_for_network('lighthouse', 'v8.0.0', 'ephemery')
        (True, None)
        >>> validate_version_for_network('lighthouse', 'v7.1.0', 'ephemery')
        (False, 'ERROR: ...')
        >>> validate_version_for_network('lighthouse', 'v7.1.0', 'mainnet')
        (True, None)  # mainnet is not Fusaka/Gloas-gated here
        >>> validate_version_for_network('lighthouse', 'v8.2.3', 'sepolia')
        (False, 'ERROR: ...')
    """
    client = _normalize_client(client_name)
    net = _normalize_network(network)

    if net in ("ephemery", "hoodi"):
        min_version = FUSAKA_MIN_VERSIONS.get(client)
        fork_label = "Fusaka fork support"
    elif net == "sepolia":
        min_version = GLOAS_SEPOLIA_MIN_VERSIONS.get(client)
        fork_label = "Gloas / Glamsterdam support"
    else:
        return (True, None)

    if not min_version:
        return (True, None)

    if compare_versions(version, min_version) >= 0:
        return (True, None)

    error_msg = (
        f"\nERROR: {client.capitalize()} {version} is not compatible with {net.capitalize()}\n"
        f"{net.capitalize()} requires {fork_label} (minimum version: {min_version})\n"
        f"The latest {client.capitalize()} release ({version}) does not meet this requirement.\n"
        f"\nPlease wait for a newer {client.capitalize()} release or choose a different network."
    )
    return (False, error_msg)


def preferred_install_tag(
    client_name: str,
    network: str,
    latest_tag: str,
) -> Optional[str]:
    """Return an alternate install tag when *latest_tag* fails the network floor.

    Returns an alternate tag from :data:`GLOAS_SEPOLIA_PREFERRED_TAGS` when
    *latest_tag* fails the Sepolia floor. Returns ``None`` when *latest_tag*
    already meets the floor or no preferred tag is configured.

    Args:
        client_name: Client key (e.g. ``lighthouse``).
        network: Network name (case-insensitive).
        latest_tag: Version resolved from GitHub LATEST (or equivalent).

    Returns:
        Preferred tag string, or ``None``.
    """
    client = _normalize_client(client_name)
    net = _normalize_network(network)
    if not latest_tag:
        return None
    is_valid, _ = validate_version_for_network(client, latest_tag, net)
    if is_valid:
        return None
    if net != "sepolia":
        return None
    preferred = GLOAS_SEPOLIA_PREFERRED_TAGS.get(client)
    if not preferred or preferred == latest_tag:
        return None
    return preferred
