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

def check_service(service_name: str) -> bool:
    """Checks if a systemd service file exists."""
    return os.path.isfile(f"/etc/systemd/system/{service_name}.service")

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

def parse_expected_artifacts(args: Any) -> Tuple[List[str], List[str], List[str]]:
    """Determines expected artifacts based on CLI arguments."""
    binaries = []
    services = []
    users = []

    config = args.config
    is_validator_only = "Validator Client Only" in config
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
    if not is_validator_only:
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
    if not is_validator_only:
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
    if mev_enabled and not is_validator_only:
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
            if is_staking or is_validator_only:
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


def check_service_journal_errors(service_name: str) -> bool:
    """Check journal for known fatal startup errors after a service start."""
    result = subprocess.run(
        ["journalctl", "-u", service_name, "--no-pager", "-n", "50"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return True
    for pattern in ("caxa stub:", "Failed to create the lock directory"):
        if pattern in result.stdout:
            print(f"  ❌ Service {service_name} journal contains fatal error: {pattern}")
            return False
    return True


def check_service_start(service_name: str) -> bool:
    """Validates the service file via systemd and verifies it can start.
    
    With real systemd (Dockerfile uses systemd as PID 1):
      1. daemon-reload to pick up the file and catch syntax errors
      2. start the service
      3. verify it is active (running)
      4. stop the service cleanly
    Without systemd (fallback): parse ExecStart and do a timed process check.
    """
    service_path = f"/etc/systemd/system/{service_name}.service"
    if not os.path.exists(service_path):
        return False

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
            # Dump the journal for debugging
            subprocess.run(["journalctl", "-u", service_name, "--no-pager", "-n", "20"])
            return False

        # Step 3: wait briefly and check active state
        import time
        time.sleep(3)
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True, text=True
        )
        active_state = result.stdout.strip()
        if active_state not in ("active", "activating"):
            print(f"  ❌ Service {service_name} is not active (state: {active_state})")
            subprocess.run(["journalctl", "-u", service_name, "--no-pager", "-n", "20"])
            return False
        print(f"  ✅ Service {service_name} is active")

        if not check_service_journal_errors(service_name):
            return False

        # Skip stopping the service - leave it running for the test
        # The container will be destroyed after the test anyway
        return True

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

def check_p2p_ports(expected_services: List[str]) -> bool:
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
    if "consensus" in expected_services:
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

    # Advisory port checks — runs after services are started
    is_validator_only = "Validator Client Only" in args.config
    if not is_validator_only:
        check_p2p_ports(expected_services)

    return success

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Docker Test Runner')
    parser.add_argument('script_name', help='The deploy script to run (deploy/deploy-node.py)')
    parser.add_argument('--combo', type=str, default="")
    parser.add_argument('--ec', type=str, default="")
    parser.add_argument('--cc', type=str, default="")
    parser.add_argument('--vc', type=str, default="")
    parser.add_argument('--mev', action='store_true', default=False)
    parser.add_argument('--config', type=str, default='Solo Staking Node')
    parser.add_argument('--network', type=str, default='SEPOLIA')
    parser.add_argument('--vc_only_bn_address', type=str, default="http://192.168.1.123:5052")
    parser.add_argument('--test-updates', action='store_true', default=False)
    args = parser.parse_args()

    with open(".env", "w") as f:
        f.write(f"MEVBOOST={'true' if args.mev else 'false'}\n")
        f.write("EL_P2P_PORT=30303\nCL_P2P_PORT=9000\n")
        f.write(f"INSTALL_CONFIG={args.config}\n")
        f.write("CSM_GRAFFITI=dummy\nCSM_MEV_MIN_BID=0.1\n")
        f.write("CSM_FEE_RECIPIENT_ADDRESS_HOLESKY=0xCSM123\n")
        f.write("CSM_FEE_RECIPIENT_ADDRESS_MAINNET=0xCSM456\n")
            
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
        print(f"\n🐳 Integration Test PASSED for {args.combo or args.ec}.")
    finally:
        for f in [".env"] + [p for p in os.listdir(".") if p.endswith((".tar.gz", ".tar.xz", ".zip"))]:
            try:
                if os.path.exists(f): os.remove(f)
            except: pass
