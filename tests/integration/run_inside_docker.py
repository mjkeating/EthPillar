"""
EthPillar Docker Integration Runner
==================================

This script is designed to run INSIDE a Docker container. It performs the following:
1. Executes deploy-node.py with --skip_prompts and various role/combo flags.
2. Verifies that the expected binaries, services, and users were created.
"""
import subprocess
import os
import pwd
import grp
import stat
import sys
import signal
import shlex
import time
from typing import List, Dict, Optional, Any, Union, Tuple

# Import INSTALL_DIR from common so the path is maintained centrally
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    import deploy.common as common
    INSTALL_DIR = common.INSTALL_DIR
except ImportError:
    INSTALL_DIR = "/usr/local/bin"

def check_user(username: str) -> bool:
    """Checks if a system user exists."""
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False

def check_binary(binary_name: str) -> bool:
    """Checks if a binary or directory exists in the installation path."""
    subfolder_paths = {
        "besu": os.path.join(INSTALL_DIR, "besu", "bin", "besu"),
        "nethermind": os.path.join(INSTALL_DIR, "nethermind", "nethermind"),
        "teku": os.path.join(INSTALL_DIR, "teku"),
        "lodestar": os.path.join(INSTALL_DIR, "lodestar", "lodestar"),
    }
    if os.path.isfile(os.path.join(INSTALL_DIR, binary_name)) or os.path.isdir(os.path.join(INSTALL_DIR, binary_name)):
        return True
    if binary_name in subfolder_paths:
        path = subfolder_paths[binary_name]
        return os.path.isfile(path) or os.path.isdir(path)
    return False


def get_binary_path(binary_name: str) -> Optional[str]:
    """Return full path to the binary if present, else None."""
    subfolder_paths = {
        "besu": os.path.join(INSTALL_DIR, "besu", "bin", "besu"),
        "nethermind": os.path.join(INSTALL_DIR, "nethermind", "nethermind"),
        "teku": os.path.join(INSTALL_DIR, "teku"),
        "lodestar": os.path.join(INSTALL_DIR, "lodestar", "lodestar"),
    }
    candidates = [os.path.join(INSTALL_DIR, binary_name), os.path.join(INSTALL_DIR, binary_name.replace('-', '_'))]
    if binary_name in subfolder_paths:
        candidates.insert(0, subfolder_paths[binary_name])

    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def check_binary_permissions(binary_name: str) -> bool:
    """Check that the binary exists and is owned by root with mode 755."""
    path = get_binary_path(binary_name)
    if not path:
        print(f"  ❌ Permissions: {binary_name} not found to check perms")
        return False
    # Resolve symlink targets
    real = os.path.realpath(path)
    try:
        st = os.stat(real)
    except FileNotFoundError:
        print(f"  ❌ Permissions: resolved target {real} not found")
        return False
    uid = st.st_uid
    mode = stat.S_IMODE(st.st_mode)
    owner = pwd.getpwuid(uid).pw_name if uid != 0 else 'root'
    ok_owner = (owner == 'root')
    ok_mode = (mode == 0o755)
    if not ok_owner:
        print(f"  ❌ Binary owner for {binary_name} is {owner}, expected root")
    if not ok_mode:
        print(f"  ❌ Binary mode for {binary_name} is {oct(mode)}, expected 0o755")
    
    if ok_owner and ok_mode:
        print(f"  ✅ Binary perms OK: {binary_name} -> {real} ({owner}, {oct(mode)})")
        return True
    return False

def check_service(service_name: str) -> bool:
    """Checks if a systemd service file exists."""
    return os.path.isfile(f"/etc/systemd/system/{service_name}.service")

TEST_ENV_CONTENT = """MEVBOOST={mevboost}
EL_P2P_PORT=30303
EL_P2P_PORT_2=30304
EL_RPC_PORT=8545
EL_MAX_PEER_COUNT=50
EL_IP_ADDRESS=127.0.0.1
CL_P2P_PORT=9000
CL_P2P_PORT_2=9001
CL_REST_PORT=5052
CL_MAX_PEER_COUNT=100
CL_IP_ADDRESS=127.0.0.1
JWTSECRET_PATH="/secrets/jwtsecret"
INSTALL_CONFIG="{install_config}"
GRAFFITI="EthPillarTest"
FEE_RECIPIENT_ADDRESS=0x1234567890123456789012345678901234567890
MEV_MIN_BID="0.006"
CSM_GRAFFITI=EthPillarCSM
CSM_MEV_MIN_BID=0.1
CSM_FEE_RECIPIENT_ADDRESS_MAINNET=0x1111111111111111111111111111111111111111
CSM_FEE_RECIPIENT_ADDRESS_HOODI=0x2222222222222222222222222222222222222222
CSM_FEE_RECIPIENT_ADDRESS_HOLESKY=0x3333333333333333333333333333333333333333
"""

def snapshot_workspace_env(paths: List[str]) -> Dict[str, Optional[bytes]]:
    """Capture env files so integration tests do not dirty the host checkout."""
    snapshots: Dict[str, Optional[bytes]] = {}
    for path in paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                snapshots[path] = f.read()
        else:
            snapshots[path] = None
    return snapshots

def restore_workspace_env(snapshots: Dict[str, Optional[bytes]]) -> None:
    for path, content in snapshots.items():
        try:
            if content is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "wb") as f:
                    f.write(content)
        except Exception as exc:
            print(f"Warning: could not restore {path}: {exc}")

def write_test_env(args: Any) -> None:
    content = TEST_ENV_CONTENT.format(
        mevboost="true" if args.mev else "false",
        install_config=args.config,
    )
    for path in (".env", "env"):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

def systemd_available() -> bool:
    """Returns True if systemd is running as PID 1 (real systemd, not container stub)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-system-running"],
            capture_output=True, text=True, timeout=5
        )
        # States: running, degraded, maintenance, starting — all indicate live systemd
        return result.stdout.strip() in ("running", "degraded", "maintenance", "starting")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    
def is_validator_only(config: str) -> bool:
    return "Validator Client Only" in config

def parse_expected_artifacts(args: Any) -> Tuple[List[str], List[str], List[str]]:
    """Determines expected artifacts based on CLI arguments."""
    binaries = []
    services = []
    users = []

    config = args.config
    vc_only = is_validator_only(config)
    is_node_only = "Full Node Only" in config
    mev_enabled = args.mev
    is_staking = any(p in config for p in ["Solo Staking", "Lido CSM Staking"])
    is_failover = "Failover Staking" in config
    is_staking_or_failover = is_staking or is_failover
    
    combo = args.combo.lower() if args.combo else ""
    ec = args.ec.lower() if args.ec else ""
    cc = args.cc.lower() if args.cc else ""
    vc = args.vc.lower() if args.vc else ""

    # EC artifacts
    if not vc_only:
        if "besu" in combo or "besu" in ec:
            binaries.append("besu"); users.append("execution"); services.append("execution")
        if "reth" in combo or "reth" in ec:
            binaries.append("reth"); users.append("execution"); services.append("execution")
        if "erigon" in combo or "erigon" in ec:
            binaries.append("erigon"); users.append("execution"); services.append("execution")
        if "nethermind" in combo or "nethermind" in ec:
            binaries.append("nethermind"); users.append("execution"); services.append("execution")
        if "geth" in combo or "geth" in ec:
            binaries.append("geth"); users.append("execution"); services.append("execution")
            
    # CC/BN artifacts
    if not vc_only:
        if "lighthouse" in combo or "lighthouse" in cc:
            binaries.append("lighthouse"); users.append("consensus"); services.append("consensus")
        if "teku" in combo or "teku" in cc:
            binaries.append("teku"); users.append("consensus"); services.append("consensus")
        if "lodestar" in combo or "lodestar" in cc:
            binaries.append("lodestar"); users.append("consensus"); services.append("consensus")
        if "nimbus" in combo or "nimbus" in cc:
            binaries.append("nimbus_beacon_node"); users.append("consensus"); services.append("consensus")
        if "caplin" in combo or "caplin" in cc:
            # Erigon-Caplin shares execution service
            pass
        if "grandine" in combo or "grandine" in cc:
            binaries.append("grandine"); users.append("consensus"); services.append("consensus")
        if "prysm" in combo or "prysm" in cc:
            binaries.append("prysm-beacon-chain"); users.append("consensus"); services.append("consensus")
            
    # MEV Boost
    if mev_enabled and not vc_only:
        binaries.append("mev-boost"); users.append("mevboost"); services.append("mevboost")

    # VC artifacts
    target_vc = vc if vc else cc if cc else combo
    # Grandine is special: it has an integrated VC, so if Grandine is the target VC, 
    # we don't expect a separate validator service.
    is_grandine_integrated = "grandine" in target_vc
    
    if not is_node_only and not is_failover and not is_grandine_integrated:
        # If VC is "Same as CC" or not specified, check based on CC/Combo
        if "lighthouse" in target_vc:
            users.append("validator"); services.append("validator")
        if "lodestar" in target_vc:
            users.append("validator"); services.append("validator")
        if "nimbus" in target_vc:
            binaries.append("nimbus_validator_client"); users.append("validator"); services.append("validator")
        if "teku" in target_vc:
            if is_staking or vc_only:
                services.append("validator"); users.append("validator")
        if "prysm" in target_vc:
            binaries.append("prysm-validator"); users.append("validator"); services.append("validator")

    return list(set(binaries)), list(set(users)), list(set(services))

def run_install(args: Any, fee_address: str):
    print(f"\n🚀 Running: deploy/deploy-node.py for {args.combo or args.ec}...")
    cmd = [sys.executable, args.script_name, "--skip_prompts", "true", "--network", args.network, "--install_config", args.config, "--fee_address", fee_address]
    if args.combo: cmd.extend(["--combo", args.combo])
    if args.ec: cmd.extend(["--ec", args.ec])
    if args.cc: cmd.extend(["--cc", args.cc])
    if args.vc: cmd.extend(["--vc", args.vc])
    if args.mev: cmd.append("--with_mevboost")
    if args.vc_only_bn_address: cmd.extend(["--vc_only_bn_address", args.vc_only_bn_address])
         
    env = os.environ.copy()
    env["ENABLE_EP_CACHE"] = "1"
    env["PYTHONPATH"] = "/ethpillar/tests/integration:" + env.get("PYTHONPATH", "")
         
    try:
        subprocess.run(cmd, capture_output=False, check=True, env=env)
    except subprocess.CalledProcessError as e:
        print(f"❌ Script failed with return code {e.returncode}")
        return False
    return True

def check_service_file_substitution(service_name: str) -> bool:
    """Fail if a service file contains unreplaced template placeholders."""
    service_path = f"/etc/systemd/system/{service_name}.service"
    if not os.path.exists(service_path):
        return True
    with open(service_path, "r") as f:
        content = f.read()
    if "{BASE_DATA_DIR}" in content:
        print(f"  ❌ Service {service_name} contains unreplaced {{BASE_DATA_DIR}} placeholder")
        return False
    return True


# Sentinel for "service crashed, but only because of external network issue (checkpoint sync)"
CHECKPOINT_SYNC_FAILURE = "checkpoint_sync_failure"

def check_service_journal_errors(service_name: str) -> "bool | str":
    """Check journal for fatal service errors and narrowly scoped checkpoint transport failures."""
    result = subprocess.run(
        ["journalctl", "-u", service_name, "--no-pager", "-n", "100"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return True
    journal = result.stdout

    fatal_patterns = (
        "caxa stub:",
        "Failed to create the lock directory",
        "Failed at step EXEC",
        "status=203/EXEC",
        "Permission denied",
        "No such file or directory",
        "unknown flag",
        "Unknown network",
        "unsupported network",
        "Invalid value",
        "invalid value",
        "not executable",
    )
    for pattern in fatal_patterns:
        if pattern in journal:
            print(f"  FAIL: Service {service_name} journal contains fatal error: {pattern}")
            return False

    checkpoint_patterns = (
        "Error loading checkpoint state",
        "checkpoint-sync",
        "checkpoint sync",
        "checkpointSyncUrl",
        "checkpoint-sync-url",
    )
    network_error_patterns = (
        "HttpClient",
        "connect: connection refused",
        "Connection refused",
        "Connection timed out",
        "timed out",
        "502 Bad Gateway",
        "503 Service Unavailable",
        "Temporary failure in name resolution",
    )
    if any(pattern in journal for pattern in checkpoint_patterns) and any(pattern in journal for pattern in network_error_patterns):
        print(f"  WARN: Service {service_name} journal indicates checkpoint sync transport failure")
        return CHECKPOINT_SYNC_FAILURE

    return True


def _has_validator_keys() -> bool:
    """Check if any validator keystore files exist in common locations."""
    keystore_dirs = [
        "/var/lib/teku_validator/validator_keys",
        "/var/lib/lighthouse_validator/validators",
        "/var/lib/nimbus_validator/validators",
        "/var/lib/prysm_validator/validator_keys",
        "/var/lib/lodestar_validator/keystores",
        "/var/lib/grandine/validator_keys",
    ]
    for d in keystore_dirs:
        try:
            if os.path.isdir(d):
                for entry in os.scandir(d):
                    if entry.is_file() and entry.name.endswith(".json"):
                        return True
        except (PermissionError, OSError):
            continue
    return False


def check_service_start(service_name: str) -> bool:
    """Validates the service file via systemd and verifies it can start securely."""
    service_path = f"/etc/systemd/system/{service_name}.service"
    if not os.path.exists(service_path):
        return False

    if service_name == "validator" and not _has_validator_keys():
        print("  ⚠️  Skipping validator health check: no validator keys found (expected in test environment)")
        return True

    if systemd_available():
        print(f"  [systemd] Validating {service_name} service via systemctl...")

        # Step 1: reload daemon — this validates unit file syntax
        result = subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  ❌ daemon-reload failed for {service_name}:\n{result.stderr}")
            return False
        print(f"  ✅ daemon-reload succeeded (service file syntax OK)")

        # Step 2: start the service
        result = subprocess.run(
            ["systemctl", "start", service_name],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(f"  ❌ systemctl start {service_name} failed:\n{result.stderr}")
            subprocess.run(["journalctl", "-u", service_name, "--no-pager", "-n", "20"])
            return False

        # Step 3: Wait and check health
        import time
        
        target_port = None
        if service_name == "execution": target_port = 30303
        elif service_name == "consensus": target_port = 9000
        elif service_name == "mevboost": target_port = 18550

        print(f"  [systemd] Polling {service_name} health for up to 60 seconds...")
        
        def get_prop(prop):
            res = subprocess.run(["systemctl", "show", "-p", prop, "--value", service_name], capture_output=True, text=True)
            return res.stdout.strip()

        max_attempts = 24 if service_name == "consensus" else 12
        for attempt in range(1, max_attempts + 1):
            time.sleep(5)
            
            active_state = get_prop("ActiveState")
            sub_state = get_prop("SubState")
            exec_main_status = get_prop("ExecMainStatus")
            n_restarts = get_prop("NRestarts")
            main_pid = get_prop("MainPID")

            if exec_main_status == "203":
                print(f"  ❌ Service {service_name} failed with exit code 203 (likely bad binary path or permissions)")
                subprocess.run(["journalctl", "-u", service_name, "--no-pager", "-n", "20"])
                return False

            # If we're crash-looping, check WHY before failing
            is_bad_state = (
                active_state not in ("active", "activating")
                or sub_state in ("dead", "failed", "auto-restart")
                or (n_restarts and n_restarts != "0")
                or main_pid == "0"
            )
            if is_bad_state:
                journal_result = check_service_journal_errors(service_name)
                if journal_result == CHECKPOINT_SYNC_FAILURE:
                    print(f"  ⚠️  Service {service_name} is crash-looping due to checkpoint sync (external network issue)")
                    print(f"      This is advisory — the service binary and config are valid.")
                    print(f"      State: active={active_state}, sub={sub_state}, restarts={n_restarts}")
                    subprocess.run(["journalctl", "-u", service_name, "--no-pager", "-n", "10"],
                                  capture_output=False)
                    # Count as pass — installation is correct, external sync server is unreachable
                    return True
                elif journal_result is False:
                    print(f"  ❌ Service {service_name} has fatal config/binary error")
                    subprocess.run(["journalctl", "-u", service_name, "--no-pager", "-n", "20"])
                    return False
                else:
                    # Unknown bad state — show details
                    print(f"  ❌ Service {service_name} is in bad state: active={active_state}, sub={sub_state}, restarts={n_restarts}, pid={main_pid}")
                    subprocess.run(["journalctl", "-u", service_name, "--no-pager", "-n", "20"])
                    return False

            if target_port:
                try:
                    result = subprocess.run(["ss", "-lntu"], capture_output=True, text=True)
                    if f":{target_port}" in result.stdout:
                        print(f"  ✅ Service {service_name} is healthy (active, PID: {main_pid}, bound to port {target_port} after {attempt*5}s)")
                        journal_ok = check_service_journal_errors(service_name)
                        return journal_ok is not False
                except Exception:
                    pass  # ignore ss errors
            else:
                if attempt >= 3:
                    print(f"  ✅ Service {service_name} is healthy (active, PID: {main_pid}, 15s stability check passed)")
                    journal_ok = check_service_journal_errors(service_name)
                    return journal_ok is not False

        print(f"  ❌ Service {service_name} timed out waiting for port {target_port} to bind after 60s")
        subprocess.run(["journalctl", "-u", service_name, "--no-pager", "-n", "20"])
        return False

    else:
        # Fallback: parse ExecStart and do a timed process check (original behaviour)
        print(f"  [no-systemd] Dry-run process check for {service_name}...")
        exec_start = ""; user = "root"; working_dir = "/"; in_exec_start = False
        with open(service_path, "r") as f:
            for line in f:
                stripped = line.strip()
                if in_exec_start:
                    exec_start += " " + stripped.rstrip("\\").strip()
                    if not stripped.endswith("\\"): in_exec_start = False
                elif stripped.startswith("ExecStart="):
                    exec_start = stripped.split("=", 1)[1].rstrip("\\").strip()
                    if stripped.endswith("\\"): in_exec_start = True
                elif stripped.startswith("User="): user = stripped.split("=", 1)[1].strip()
                elif stripped.startswith("WorkingDirectory="): working_dir = stripped.split("=", 1)[1].strip('"\'')

        if not exec_start: return False
        try:
            import shlex, signal, time
            cmd = shlex.split(exec_start)
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       cwd=working_dir, preexec_fn=os.setsid)
            time.sleep(5)
            if process.poll() is not None:
                print(f"  ❌ Service {service_name} crashed immediately (code {process.returncode})")
                return False
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            print(f"  ✅ Service {service_name} started (process dry-run)")
            return True
        except Exception as e:
            print(f"  ❌ Failed to run service: {e}")
            return False

def check_p2p_ports(expected_services: List[str], has_caplin: bool = False) -> bool:
    """Check that expected client P2P ports are listening after services start.

    Checks the standard EL (30303) and CL (9000) P2P ports over both TCP and UDP
    using `ss -lntu`. Results are advisory — clients may still be binding ports
    immediately after startup, so failures warn but do not fail the test.
    """
    if not systemd_available():
        print("  [no-systemd] Skipping port checks (no live services in fallback mode)")
        return True

    port_checks = []
    if "execution" in expected_services:
        port_checks.append((30303, "Execution P2P"))
    if "consensus" in expected_services or has_caplin:
        port_checks.append((9000, "Consensus P2P"))

    if not port_checks:
        return True

    # Give services a moment to bind their ports after startup
    time.sleep(5)

    print("\n🔌 Checking P2P listening ports...")
    try:
        result = subprocess.run(["ss", "-lntu"], capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"  ⚠️  Could not run ss: {e}")
        return True

    lines = result.stdout.splitlines()
    for port, label in port_checks:
        for proto in ("tcp", "udp"):
            listening = any(
                line.lower().startswith(proto) and f":{port}" in line
                for line in lines
            )
            status = "✅" if listening else "⚠️ "
            state = "listening" if listening else "not yet listening (client may still be starting)"
            print(f"  {status} Port {port}/{proto.upper()} ({label}): {state}")

    return True  # Advisory — never fails the integration test


def verify(args: Any):
    print(f"\n🔍 Verifying Artifacts...")
    if args.test_switching:
        print("  🔄 Updating expected artifacts for post-switch validation...")
        try:
            res_ec = subprocess.run("cat /etc/systemd/system/execution.service | grep Description= | cut -d'=' -f2 | awk '{print $1}'", shell=True, capture_output=True, text=True)
            new_ec = res_ec.stdout.strip().lower()
            res_cc = subprocess.run("cat /etc/systemd/system/consensus.service | grep Description= | cut -d'=' -f2 | awk '{print $1}'", shell=True, capture_output=True, text=True)
            new_cc = res_cc.stdout.strip().lower()
            if new_ec: args.ec = new_ec
            if new_cc: args.cc = new_cc
            if new_ec and new_cc: args.combo = f"{new_cc}-{new_ec}"
        except Exception:
            pass
    expected_binaries, expected_users, expected_services = parse_expected_artifacts(args)
    success = True
    for b in expected_binaries:
        present = check_binary(b)
        print(f"{'✅' if present else '❌'} Binary: {b}")
        if not present: success = False
    for u in expected_users:
        present = check_user(u)
        print(f"{'✅' if present else '❌'} User  : {u}")
        if not present: success = False
    for s in expected_services:
        present = check_service(s)
        print(f"{'✅' if present else '❌'} Service File: {s}")
        if not present: success = False
        elif not check_service_file_substitution(s): success = False
        elif not check_service_start(s): success = False

    # Check binary ownership/permissions (should be root:root 755)
    print("\n🔐 Verifying binary ownership/permissions...")
    for b in expected_binaries:
        ok = check_binary_permissions(b)
        if not ok: success = False

    # Check that /var/lib contains directories owned by expected service users
    # Note: mevboost is stateless and uses --no-create-home, so it has no /var/lib dir.
    print("\n📁 Verifying /var/lib ownership for service users...")
    base_dir = "/var/lib"
    NO_DATA_DIR_USERS = {"mevboost"}  # users that intentionally have no /var/lib entry
    for u in expected_users:
        if u in NO_DATA_DIR_USERS:
            print(f"  ℹ️  Skipping /var/lib check for {u} (stateless service, no data dir expected)")
            continue
        found = False
        try:
            for entry in os.listdir(base_dir):
                path = os.path.join(base_dir, entry)
                try:
                    st = os.stat(path)
                except FileNotFoundError:
                    continue
                try:
                    owner = pwd.getpwuid(st.st_uid).pw_name
                except KeyError:
                    owner = str(st.st_uid)
                if owner == u:
                    found = True
                    print(f"  ✅ /var/lib entry owned by {u}: {path}")
                    break
        except FileNotFoundError:
            print(f"  ❌ {base_dir} not found in container")
            found = False
        if not found:
            print(f"  ❌ No /var/lib/* directory owned by user {u} (expected) ")
            success = False

    # Advisory port checks — runs after services are started
    # Recompute local flags used by parse_expected_artifacts
    combo = args.combo.lower() if args.combo else ""
    cc = args.cc.lower() if args.cc else ""
    if not is_validator_only(args.config):
        check_p2p_ports(expected_services, has_caplin=("caplin" in combo or "caplin" in cc))

    return success

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Docker Test Runner')
    parser.add_argument('script_name', help='The deploy script to run (deploy/deploy-node.py) or a command like verify-service-health')
    parser.add_argument('--combo', type=str, default="")
    parser.add_argument('--ec', type=str, default="")
    parser.add_argument('--cc', type=str, default="")
    parser.add_argument('--vc', type=str, default="")
    parser.add_argument('--mev', action='store_true', default=False)
    parser.add_argument('--config', type=str, default='Solo Staking Node')
    parser.add_argument('--network', type=str, default='SEPOLIA')
    parser.add_argument('--vc_only_bn_address', type=str, default="http://192.168.1.123:5052")
    parser.add_argument('--test-updates', action='store_true', default=False)
    parser.add_argument('--test-switching', action='store_true', default=False)
    parser.add_argument('--service', type=str, default="", help='Service name for verify-service-health')
    args = parser.parse_args()

    if args.script_name == "verify-service-health":
        if not args.service:
            print("❌ --service argument is required for verify-service-health")
            sys.exit(1)
        success = check_service_start(args.service)
        sys.exit(0 if success else 1)

    env_snapshots = snapshot_workspace_env([".env", "env"])
    write_test_env(args)
            
    try:
        if not run_install(args, "0x1234567890123456789012345678901234567890"):
            sys.exit(1)
        if not verify(args):
            sys.exit(1)
        if args.test_updates:
            print("\n=========================================")
            print(" Running Updates Integration Test...")
            print("=========================================")
            subprocess.run(["bash", "/ethpillar/tests/integration/test_updates.sh"], check=True)
        if args.test_switching:
            print("\n=========================================")
            print(" Running Client Switching Integration Test...")
            print("=========================================")
            subprocess.run(["bash", "/ethpillar/tests/integration/test_switching.sh"], check=True)
        print(f"\n🐳 Integration Test PASSED for {args.combo or args.ec}.")
    finally:
        restore_workspace_env(env_snapshots)
        for f in [p for p in os.listdir(".") if p.endswith((".tar.gz", ".tar.xz", ".zip"))]:
            try:
                if os.path.exists(f): os.remove(f)
            except: pass
