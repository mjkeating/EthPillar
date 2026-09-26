import os
import re
import shlex
import subprocess
from deploy.common import write_service_file, DOWNLOAD_DIR, INSTALL_DIR, setup_client_user_and_dir, download_file, get_machine_architecture, BASE_DATA_DIR
from client_requirements import validate_version_for_network
from typing import List, Tuple, Optional
from deploy.service_generators import form_exec_start, generate_systemd_template

NIMBUS_DATA_DIR = f"{BASE_DATA_DIR}/nimbus"


def _nimbus_network_flag(eth_network: str) -> str:
    if eth_network == "ephemery":
        return "--network=/opt/ethpillar/testnet/config.yaml"
    return f"--network={eth_network}"


def build_checkpoint_sync_exec_start_pre(network_flag: str, sync_url: str) -> str:
    """Build ``ExecStartPre`` that checkpoint-syncs Nimbus on first start.

    EthPillar passes no checkpoint URL flag to the beacon node; instead the
    database is initialised once with the separate ``trustedNodeSync``
    command (the documented Nimbus route). Runs only while no
    database exists, so it is a no-op on later starts and after datadir
    migrations (e.g. CDVN). The sync writes to a staging dir and the finished
    ``db`` is moved into place atomically, so an interrupted sync (provider
    down, start timeout) leaves no half-built ``db`` and is retried on the
    next start instead of Nimbus falling back to genesis sync.
    Uses the same trustedNodeSync arguments as resync_nimbus() in
    resync_consensus.sh (which syncs in place, without a staging dir).

    Every value is double-quoted inside the script, so URL characters such as
    ``?&;#`` stay literal to bash and the script contains no single quote. The
    outer ``shlex.quote`` then yields one plain single-quoted word, which keeps
    the unit line readable and predictable. Characters that systemd or bash
    would expand or re-interpret (``%`` specifiers, ``$`` variables, backtick,
    quotes, backslash escapes, whitespace) are rejected rather than escaped.

    Returns:
        Full ``ExecStartPre=`` value (command only, without the key).

    Raises:
        ValueError: A value contains characters that cannot be passed safely
            through systemd and bash (whitespace, quotes, backslash, backtick,
            ``$`` or ``%``).
    """
    db = _dq(f"{NIMBUS_DATA_DIR}/db")
    staging = _dq(f"{NIMBUS_DATA_DIR}/.checkpoint-sync")
    sync_cmd = " ".join(_dq(a) for a in [
        f"{INSTALL_DIR}/nimbus_beacon_node", "trustedNodeSync",
        network_flag,
        f"--trusted-node-url={sync_url}",
        f"--data-dir={NIMBUS_DATA_DIR}/.checkpoint-sync",
        "--backfill=false",
    ])
    staging_db = _dq(f"{NIMBUS_DATA_DIR}/.checkpoint-sync/db")
    script = (
        f"test -d {db} || {{ rm -rf {staging} && {sync_cmd} "
        f"&& mv {staging_db} {db} && rm -rf {staging}; }}"
    )
    return f"/bin/bash -c {shlex.quote(script)}"


# systemd expands % (specifiers) and $ (env vars) in Exec lines; bash expands
# $ and ` inside double quotes; quotes/backslash/whitespace would break quoting.
_UNSAFE_IN_EXEC = re.compile(r"[\s'\"\\`$%]")


def _dq(value: str) -> str:
    if _UNSAFE_IN_EXEC.search(value):
        raise ValueError(f"Unsupported character in Nimbus checkpoint-sync argument: {value!r}")
    return f'"{value}"'


def generate_nimbus_bn_service(eth_network: str, jwtsecret_path: str,
                               cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                               fee_parameters: str = '', mev_parameters: str = '',
                               network_override: Optional[str] = None,
                               sync_url: str = '') -> str:
    """Generate Nimbus beacon node systemd service file content.

    Args:
        eth_network: Network name
        jwtsecret_path: Path to JWT secret file
        cl_rest_port: CL REST port
        cl_p2p_port: CL P2P port
        cl_p2p_port_2: CL QUIC port (UDP)
        cl_max_peer_count: CL max peer count
        fee_parameters: Optional fee recipient parameters
        mev_parameters: Optional MEV relay parameters
        network_override: Optional network flag override
        sync_url: Optional checkpoint sync URL (trustedNodeSync on first start)

    Returns:
        Service file content as a string
    """
    _network = network_override or _nimbus_network_flag(eth_network)

    _args = [
        f"{INSTALL_DIR}/nimbus_beacon_node",
        _network,
        f"--data-dir={NIMBUS_DATA_DIR}",
        f"--tcp-port={cl_p2p_port}",
        f"--udp-port={cl_p2p_port}",
        f"--quic-port={cl_p2p_port_2}",
        f"--max-peers={cl_max_peer_count}",
        f"--rest-port={cl_rest_port}",
        "--enr-auto-update=true",
        "--el=http://127.0.0.1:8551",
        "--rest",
        "--metrics",
        "--metrics-port=8008",
        f"--jwt-secret={jwtsecret_path}",
        "--non-interactive",
        "--status-bar=false",
    ]
    if fee_parameters:
        _args.append(fee_parameters.strip())
    if mev_parameters:
        _args.append(mev_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Nimbus Beacon Node Consensus Client service for {eth_network.upper()}",
        user="consensus",
        exec_start=_exec_start,
        extra_env=None,
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=None,
        exec_start_pre=[build_checkpoint_sync_exec_start_pre(_network, sync_url)] if sync_url else None,
        # trustedNodeSync downloads a full state; the systemd default of 90 s is too short.
        timeout_start_sec=1800 if sync_url else None,
    )

def generate_nimbus_vc_service(eth_network: str, graffiti: str, beacon_node_address: str,
                               fee_parameters: str = '', extra_parameters: str = '',
                               unit_after: Optional[List[str]] = None) -> str:
    """Generate Nimbus validator client systemd service file content.

    Args:
        eth_network: Network name
        graffiti: Graffiti string
        beacon_node_address: Beacon node address
        fee_parameters: Optional fee recipient parameters
        extra_parameters: Optional extra ExecStart flags (builder/MEV and/or DVT)
        unit_after: Optional extra systemd ``After=``/``Wants=`` units
            (e.g. ``["charon.service"]`` when running behind Obol Charon).

    Returns:
        Service file content as a string
    """
    _args = [
        f"{INSTALL_DIR}/nimbus_validator_client",
        f"--data-dir={BASE_DATA_DIR}/nimbus_validator",
        "--metrics",
        "--metrics-port=8009",
        "--non-interactive",
        "--doppelganger-detection=off",
        f"--graffiti={graffiti}",
        beacon_node_address,
    ]
    if fee_parameters:
        _args.append(fee_parameters.strip())
    if extra_parameters:
        _args.append(extra_parameters.strip())

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Nimbus Validator Client service for {eth_network.upper()}",
        user="validator",
        exec_start=_exec_start,
        extra_env=None,
        working_dir=None,
        timeout_stop_sec=900,
        limit_nofile=65536,
        unit_after=unit_after,
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Nimbus release version, download URL, and filename.

    Args:
        version_tag: 'LATEST' or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys 'version', 'download_urls', and 'filenames'.
    """
    from deploy.common import get_github_release, pick_github_release_asset, release_info_from_github
    repo = "status-im/nimbus-eth2"
    data = get_github_release(repo, version_tag)
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("nimbus", "_linux_"),
        client_label="Nimbus",
    )
    return release_info_from_github(data, [download_url], [filename])



def download_nimbus(eth_network: str) -> str:
    # Create User and directories
    setup_client_user_and_dir("consensus", "nimbus")
    setup_client_user_and_dir("validator", "nimbus_validator")
    
    # Install dependencies for Nimbus
    print(f">> Installing Nimbus dependencies")
    subprocess.run(["sudo", "apt-get", "-y", "-qq", "install", "libnss3", "libsqlite3-0"], check=True)

    # Resolve version and download URL
    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    nimbus_version = info["version"]

    # Validate version for network requirements
    is_valid, error_msg = validate_version_for_network('nimbus', nimbus_version, eth_network)
    if not is_valid:
        print(error_msg)
        exit(1)

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]

    # Download the latest release binary
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "Nimbus")

    # Extract the binary to /usr/local/bin/ using sudo
    subprocess.run(["sudo", "mkdir", "-p", "/tmp/nimbus_extract"], check=True)
    subprocess.run(["sudo", "tar", "xzf", download_path, "-C", "/tmp/nimbus_extract", "--strip-components=1"], check=True)

    # Move the actual binaries we need
    subprocess.run(["sudo", "cp", "/tmp/nimbus_extract/build/nimbus_beacon_node", f"{INSTALL_DIR}/"], check=True)
    subprocess.run(["sudo", "cp", "/tmp/nimbus_extract/build/nimbus_validator_client", f"{INSTALL_DIR}/"], check=True)

    # Remove the tar file and extract dir
    os.remove(download_path)
    subprocess.run(["sudo", "rm", "-rf", "/tmp/nimbus_extract"])
    return nimbus_version

def install_nimbus_bn(eth_network: str, jwtsecret_path: str,
                     cl_rest_port: str, cl_p2p_port: str, cl_p2p_port_2: str, cl_max_peer_count: str,
                     fee_parameters: str = '', mev_parameters: str = '', sync_url: str = '') -> str:
    """Generate and write Nimbus beacon node service file.

    Args:
        eth_network: Network name.
        jwtsecret_path: Path to JWT secret file.
        cl_rest_port: Consensus client REST port.
        cl_p2p_port: Consensus client P2P port.
        cl_p2p_port_2: Consensus client QUIC port.
        cl_max_peer_count: Consensus client max peer count.
        fee_parameters: Optional fee recipient parameters.
        mev_parameters: Optional MEV relay parameters.
        sync_url: Optional checkpoint sync URL (trustedNodeSync on first start).

    Returns:
        The path to the created service file.
    """
    service_content = generate_nimbus_bn_service(
        eth_network, jwtsecret_path,
        cl_rest_port, cl_p2p_port, cl_p2p_port_2, cl_max_peer_count,
        fee_parameters, mev_parameters, sync_url=sync_url
    )
    service_file_path = '/etc/systemd/system/consensus.service'
    write_service_file(service_content, service_file_path, 'consensus_temp.service')
    return service_file_path

def install_nimbus_vc(nimbus_version: str, eth_network: str, cl_rest_port: str, graffiti: str, bn_addr_flag: str,
                     fee_parameters: str = '', extra_parameters: str = '',
                     unit_after: Optional[List[str]] = None) -> str:
    """Generate and write Nimbus validator client service file.

    Args:
        nimbus_version: Installed Nimbus version.
        eth_network: Network name.
        cl_rest_port: Consensus client REST port.
        graffiti: Graffiti string.
        bn_addr_flag: Beacon node address flag.
        fee_parameters: Optional fee recipient parameters.
        extra_parameters: Optional extra ExecStart flags (builder/MEV and/or DVT).
        unit_after: Optional extra systemd ``After=``/``Wants=`` units.

    Returns:
        The path to the created service file.
    """
    service_content = generate_nimbus_vc_service(
        eth_network, graffiti, bn_addr_flag,
        fee_parameters, extra_parameters, unit_after=unit_after,
    )
    service_file_path = '/etc/systemd/system/validator.service'
    write_service_file(service_content, service_file_path, 'validator_temp.service')
    return service_file_path
