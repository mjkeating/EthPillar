"""
EthPillar Docker Integration Runner
==================================

This script is designed to run INSIDE a Docker container. It performs the following:
1. Executes a specific EthPillar deployment script with --skip_prompts.
2. Verifies that the expected binaries, services, and users were created.
EthPillar Search Path Customizer
================================

This module is automatically loaded by Python (via sitecustomize) inside the
integration test containers. It implements a local caching layer for the
'requests' library to mitigate GitHub API rate limiting.
"""
import subprocess
import os
import pwd
import sys
import signal
from typing import List, Dict, Optional, Any, Union, Tuple

# Import INSTALL_DIR from common so the path is maintained centrally
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    import deploy.common as common
    INSTALL_DIR = common.INSTALL_DIR
except ImportError:
    import argparse
    INSTALL_DIR = "/usr/local/bin"
    # Fallback if common is not available (should not happen in Docker)
    class common:
        VALID_NETWORKS = ['MAINNET', 'HOODI', 'EPHEMERY', 'HOLESKY', 'SEPOLIA']
        @staticmethod
        def network_type(s):
            return s.upper()

def check_user(username: str) -> bool:
    """Checks if a system user exists."""
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False

def check_binary(binary_name: str) -> bool:
    """
    Checks if a binary or directory exists in the installation path.
    Handles subfolder extraction for specific clients like Besu and Lodestar.
    """
    # Some binaries extract to subfolders
    subfolder_paths = {
        "besu": os.path.join(INSTALL_DIR, "besu", "bin", "besu"),
        "lodestar": os.path.join(INSTALL_DIR, "lodestar", "lodestar"),
        "nethermind": os.path.join(INSTALL_DIR, "nethermind", "nethermind"),
        "teku": os.path.join(INSTALL_DIR, "teku") # Teku is just a directory we check
    }

    # Direct check
    if os.path.isfile(os.path.join(INSTALL_DIR, binary_name)) or os.path.isdir(os.path.join(INSTALL_DIR, binary_name)):
        return True
    
    # Subfolder fallback
    if binary_name in subfolder_paths:
        path = subfolder_paths[binary_name]
        return os.path.isfile(path) or os.path.isdir(path)
    
    return False

def check_service(service_name: str) -> bool:
    """Checks if a systemd service file exists in the standard location."""
    return os.path.isfile(f"/etc/systemd/system/{service_name}.service")

def parse_expected_artifacts(script_name: str, env_vars: Optional[Dict[str, str]] = None) -> Tuple[List[str], List[str], List[str]]:
    """
    Parses the expected binaries, services, and users based on the script name
    and environment variables (configuration).
    """
    binaries = []
    services = []
    users = []

    config = env_vars.get('INSTALL_CONFIG', '') if env_vars else ''
    is_validator_only = "Validator Client Only" in config
    is_node_only = "Full Node Only" in config
    mev_enabled = (env_vars and env_vars.get('MEVBOOST', 'false').lower() == 'true')
    is_staking = any(p in config for p in ["Solo Staking", "Lido CSM Staking", "Failover Staking"])
    
    # Execution Client artifacts
    if not is_validator_only:
        if "besu" in script_name:
            binaries.extend(["besu"])
            users.append("execution")
            services.append("execution")
        if "reth" in script_name:
            binaries.append("reth")
            users.append("execution")
            services.append("execution")
        if "erigon" in script_name:
            binaries.append("erigon")
            users.append("execution")
            services.append("execution")
        if "nethermind" in script_name:
            binaries.append("nethermind")
            users.append("execution")
            services.append("execution")
            
    # Consensus Client Beacon Node artifacts
    if not is_validator_only:
        if "lighthouse" in script_name:
            binaries.append("lighthouse")
            users.append("consensus")
            services.append("consensus")
        if "teku" in script_name:
            binaries.append("teku")
            users.append("consensus")
            services.append("consensus")
        if "lodestar" in script_name:
            binaries.append("lodestar")
            users.append("consensus")
            services.append("consensus")
        if "nimbus" in script_name:
            binaries.append("nimbus_beacon_node")
            users.append("consensus")
            services.append("consensus")
            
    # MEV Boost artifacts
    if mev_enabled and not is_validator_only:
        binaries.append("mev-boost")
        users.append("mevboost")
        services.append("mevboost")

    # Validator Client artifacts
    if not is_node_only:
        if "lighthouse" in script_name: 
            users.append("validator")
            services.append("validator")
        if "lodestar" in script_name:
            users.append("validator")
            services.append("validator")
        if "nimbus" in script_name:
            binaries.append("nimbus_validator_client")
            users.append("validator")
            services.append("validator")
        if "teku" in script_name:
            # Teku installs validator service in both Solo Staking and VC Only
            if is_staking or is_validator_only:
                services.append("validator")
                users.append("validator")

    return list(set(binaries)), list(set(users)), list(set(services))

def run_script(script_name, install_config="Solo Staking Node", network="sepolia", fee_address="0x1234567890123456789012345678901234567890", vc_address="http://192.168.1.123:5052"):
    print(f"\n🚀 Running: python3 {script_name} --skip_prompts true --network {network} --install_config \"{install_config}\" --fee_address {fee_address}")
    
    cmd = [sys.executable, script_name, "--skip_prompts", "true", "--network", network, "--install_config", install_config, "--fee_address", fee_address]
    if vc_address:
         cmd.extend(["--vc_only_bn_address", vc_address])
         
    env = os.environ.copy()
    env["ENABLE_EP_CACHE"] = "1"
    env["PYTHONPATH"] = "/ethpillar/tests/integration:" + env.get("PYTHONPATH", "")
         
    try:
        # Run the command and print output to stdout in real-time
        result = subprocess.run(cmd, capture_output=False, check=True, env=env)
    except subprocess.CalledProcessError as e:
        print(f"❌ Script {script_name} failed with return code {e.returncode}")
        return False
    print(f"✅ Script {script_name} completed successfully.")
    return True

def verify_script(script_name, env_vars=None):
    print(f"\n🔍 Verifying Artifacts for {script_name}...")
    print("-" * 40)
    
    expected_binaries, expected_users, expected_services = parse_expected_artifacts(script_name, env_vars)
    success = True
    
    for b in expected_binaries:
        present = check_binary(b)
        status = "✅" if present else "❌"
        print(f"{status} Binary: {b}")
        if not present:
            success = False
            
    for u in expected_users:
        present = check_user(u)
        status = "✅" if present else "❌"
        print(f"{status} User  : {u}")
        if not present:
            success = False
            
    for s in expected_services:
        present = check_service(s)
        status = "✅" if present else "❌"
        print(f"{status} Service File: {s}")
        if not present:
            success = False
        else:
            # Check if it runs correctly
            if not check_service_start(s):
                success = False

    print(f"\n[DEBUG] {INSTALL_DIR} contents recursively:")
    os.system(f"ls -R {INSTALL_DIR} | head -n 100")
    
    if not success:
        print("\n[DEBUG] Failure Diagnostics:")
        print("--- /usr/local/bin contents ---")
        subprocess.run(["ls", "-l", INSTALL_DIR])
        print("--- /etc/systemd/system contents ---")
        subprocess.run(["ls", "-l", "/etc/systemd/system/"])
        
    if success:
        print(f"\n✅ Verification PASSED for {script_name}.")
    else:
        print(f"\n❌ Verification FAILED for {script_name}.")
    
    return success

import shlex
import time

def check_service_start(service_name):
    print(f"  Attempting to dry-run service {service_name}...")
    service_path = f"/etc/systemd/system/{service_name}.service"
    if not os.path.exists(service_path):
        return False
        
    exec_start = ""
    user = "root"
    working_dir = "/"
    env_vars = {}
    in_exec_start = False
    with open(service_path, "r") as f:
        for line in f:
            stripped = line.strip()
            if in_exec_start:
                exec_start += " " + stripped.rstrip("\\").strip()
                if not stripped.endswith("\\"):
                    in_exec_start = False
            elif stripped.startswith("ExecStart="):
                exec_start = stripped.split("=", 1)[1].rstrip("\\").strip()
                if stripped.endswith("\\"):
                    in_exec_start = True
            elif stripped.startswith("User="):
                user = stripped.split("=", 1)[1].strip()
            elif stripped.startswith("WorkingDirectory="):
                working_dir = stripped.split("=", 1)[1].strip('"\'')
            elif stripped.startswith("Environment="):
                val = stripped.split("=", 1)[1].strip('"\'')
                if "=" in val:
                    k, v = val.split("=", 1)
                    env_vars[k.strip()] = v.strip()
                
    if not exec_start:
        print(f"  ❌ No ExecStart found in {service_name}.service")
        return False
        
    # Hack for Nethermind .NET bundle extraction
    if "nethermind" in exec_start.lower():
        env_vars["DOTNET_BUNDLE_EXTRACT_BASE_DIR"] = "/tmp/nethermind-bundle"
        os.makedirs("/tmp/nethermind-bundle", exist_ok=True)
        os.system("chmod 777 /tmp/nethermind-bundle")

    print(f"  Running: {exec_start}")
    print(f"  As User: {user}")
    
    try:
        env_str = " ".join([f"{k}={v}" for k, v in env_vars.items()])
        cd_cmd = f"cd {working_dir} && " if working_dir != "/" else ""
        if env_str:
            full_cmd = f"{cd_cmd} env {env_str} {exec_start}"
        else:
            full_cmd = f"{cd_cmd} {exec_start}"
            
        cmd = ["sudo", "-u", user, "bash", "-c", full_cmd]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=os.setsid)
        
        # Wait up to 5 seconds
        start_time = time.time()
        while time.time() - start_time < 5:
            if process.poll() is not None:
                break
            time.sleep(0.5)
        
        if process.poll() is not None:
            # It exited!
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                print(f"  ❌ Process exited prematurely with code {process.returncode}")
                # Use slicing on list of lines/chars if needed, but stdout is string here
                print(f"  STDOUT: {stdout[-500:] if stdout else ''}")
                print(f"  STDERR: {stderr[-500:] if stderr else ''}")
                return False
            else:
                print(f"  ✅ Service {service_name} executed and exited cleanly (0).")
                return True
            
        # It's still running, which means it successfully started!
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except:
            pass
            
        print(f"  ✅ Service {service_name} successfully executed for 5 seconds without crashing.")
        return True
        
    except Exception as e:
        print(f"  ❌ Failed to run service: {e}")
        return False

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Docker Test Runner')
    parser.add_argument('script_name', help='The deploy script to run')
    parser.add_argument('--mev', dest='mev', action='store_true', default=False)
    parser.add_argument('--config', dest='config', type=str, default='Solo Staking Node')
    parser.add_argument('--network', dest='network', type=common.network_type, default='SEPOLIA')
    parser.add_argument('--vc_only_bn_address', dest='vc_only_bn_address', type=str, default="http://192.168.1.123:5052")
    args = parser.parse_args()

    # Create dummy .env file based on arguments
    with open(".env", "w") as f:
        f.write(f"MEVBOOST={'true' if args.mev else 'false'}\n")
        f.write("EL_P2P_PORT=30303\n")
        f.write("CL_P2P_PORT=9000\n")
        f.write(f"INSTALL_CONFIG={args.config}\n")
        
        # Lido dummy vars
        f.write("CSM_GRAFFITI=dummy_cs_graffiti\n")
        f.write("CSM_MEV_MIN_BID=0.1\n")
        f.write("CSM_FEE_RECIPIENT_ADDRESS_HOLESKY=0xCSM1234567890123456789012345678901234567890\n")
        f.write("CSM_FEE_RECIPIENT_ADDRESS_MAINNET=0xCSM1234567890123456789012345678901234567890\n")
            
    try:
        target_script = args.script_name
        env_vars = {
            'INSTALL_CONFIG': args.config,
            'MEVBOOST': 'true' if args.mev else 'false'
        }
        if not run_script(target_script, args.config, args.network, "0x1234567890123456789012345678901234567890", args.vc_only_bn_address):
            sys.exit(1)
        verify_script(target_script, env_vars)
        print(f"\n🐳 Integration Test run inside Docker complete for {target_script} (MEV={args.mev}).")
    finally:
        # Cleanup artifacts that might have leaked to the host via volume mount
        for f in [".env"] + [path for path in os.listdir(".") if path.endswith((".tar.gz", ".tar.xz", ".zip"))]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass
