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
        "lodestar": os.path.join(INSTALL_DIR, "lodestar", "lodestar"),
        "nethermind": os.path.join(INSTALL_DIR, "nethermind", "nethermind"),
        "teku": os.path.join(INSTALL_DIR, "teku") 
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

def parse_expected_artifacts(args: Any) -> Tuple[List[str], List[str], List[str]]:
    """Determines expected artifacts based on CLI arguments."""
    binaries = []
    services = []
    users = []

    config = args.config
    is_validator_only = "Validator Client Only" in config
    is_node_only = "Full Node Only" in config
    mev_enabled = args.mev
    is_staking = any(p in config for p in ["Solo Staking", "Lido CSM Staking", "Failover Staking"])
    
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
            
    # MEV Boost
    if mev_enabled and not is_validator_only:
        binaries.append("mev-boost"); users.append("mevboost"); services.append("mevboost")

    # VC artifacts
    if not is_node_only:
        # If VC is "Same as CC" or not specified, check based on CC/Combo
        target_vc = vc if vc else cc if cc else combo
        if "lighthouse" in target_vc:
            users.append("validator"); services.append("validator")
        if "lodestar" in target_vc:
            users.append("validator"); services.append("validator")
        if "nimbus" in target_vc:
            binaries.append("nimbus_validator_client"); users.append("validator"); services.append("validator")
        if "teku" in target_vc:
            if is_staking or is_validator_only:
                services.append("validator"); users.append("validator")

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

def check_service_start(service_name):
    print(f"  Attempting to dry-run service {service_name}...")
    service_path = f"/etc/systemd/system/{service_name}.service"
    if not os.path.exists(service_path): return False
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
        cmd = shlex.split(exec_start)
        # We use a timeout to see if it starts without immediate crash
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=working_dir, preexec_fn=os.setsid)
        time.sleep(5)
        if process.poll() is not None:
            print(f"  ❌ Service {service_name} crashed immediately with code {process.returncode}")
            return False
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        print(f"  ✅ Service {service_name} started successfully.")
        return True
    except Exception as e:
        print(f"  ❌ Failed to run service: {e}")
        return False

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
        elif not check_service_start(s): success = False
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
        print(f"\n🐳 Integration Test PASSED for {args.combo or args.ec}.")
    finally:
        for f in [".env"] + [p for p in os.listdir(".") if p.endswith((".tar.gz", ".tar.xz", ".zip"))]:
            try:
                if os.path.exists(f): os.remove(f)
            except: pass
