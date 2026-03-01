"""
EthPillar Docker Integration Runner
This script runs INSIDE the container to verify the deploy scripts.
It executes a specific script with --skip_prompts and verifies the resulting state.
"""
import subprocess
import os
import pwd
import sys

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

def check_user(username):
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False

def check_binary(binary_name):
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

def check_service(service_name):
    return os.path.isfile(f"/etc/systemd/system/{service_name}.service")

def parse_expected_artifacts(script_name):
    binaries = []
    services = []
    users = []

    # Simple heuristic to determine expected artifacts
    if "besu" in script_name:
        binaries.append("besu")
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
        
    if "lighthouse" in script_name:
        binaries.append("lighthouse")
        users.extend(["consensus"])
        services.extend(["consensus"])
    if "teku" in script_name:
        binaries.append("teku")
        users.extend(["consensus"])
        services.extend(["consensus"])
    if "lodestar" in script_name:
        binaries.append("lodestar")
        users.extend(["consensus"])
        services.extend(["consensus"])
    if "nimbus" in script_name:
        binaries.extend(["nimbus_beacon_node"])
        users.extend(["consensus"])
        services.extend(["consensus"])
        
    # Caplin does not use external consensus client binaries or services
    if "caplin" in script_name:
        pass # All bundled in erigon

        # Actually Caplin-Erigon script DOES install MEV-boost
        binaries.append("mev-boost")
        users.append("mevboost")
        services.append("mevboost")

    return list(set(binaries)), list(set(users)), list(set(services))

def run_script(script_name, network="sepolia", fee_address="0x1234567890123456789012345678901234567890", vc_address="http://192.168.1.123:5052"):
    print(f"\n🚀 Running: python3 {script_name} --skip_prompts true --network {network} --fee_address {fee_address}")
    
    cmd = [sys.executable, script_name, "--skip_prompts", "true", "--network", network, "--fee_address", fee_address]
    if vc_address:
         cmd.extend(["--vc_only_bn_address", vc_address])
         
    try:
        # Run the command and print output to stdout in real-time
        result = subprocess.run(cmd, capture_output=False, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Script {script_name} failed with return code {e.returncode}")
        return False
    print(f"✅ Script {script_name} completed successfully.")
    return True

def verify_script(script_name):
    print(f"\n🔍 Verifying Artifacts for {script_name}...")
    print("-" * 40)
    
    expected_binaries, expected_users, expected_services = parse_expected_artifacts(script_name)
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
        # For Full Node Only config, consensus services like validator and beacon might not be installed depending on script
        # Actually we need to make sure we don't look for expected_services if they're not supposed to be there.
        present = check_service(s)
        status = "✅" if present else "❌"
        print(f"{status} Service File: {s}")
        if not present:
            success = False
        else:
            # Check if it runs correctly
            if not check_service_start(s):
                success = False

    print(f"\n[DEBUG] {INSTALL_DIR} contents:")
    subprocess.run(["ls", "-F", INSTALL_DIR])
    
    if not success:
        print(f"\n❌ Verification FAILED for {script_name}.")
        sys.exit(1)
    else:
        print(f"\n✅ Verification PASSED for {script_name}.")

import shlex
import time

def check_service_start(service_name):
    print(f"  Attempting to dry-run service {service_name}...")
    service_path = f"/etc/systemd/system/{service_name}.service"
    if not os.path.exists(service_path):
        return False
        
    exec_start = None
    user = "root"
    with open(service_path, "r") as f:
        for line in f:
            if line.strip().startswith("ExecStart="):
                exec_start = line.split("=", 1)[1].strip()
            elif line.strip().startswith("User="):
                user = line.split("=", 1)[1].strip()
                
    if not exec_start:
        print(f"  ❌ No ExecStart found in {service_name}.service")
        return False
        
    print(f"  Running: {exec_start}")
    print(f"  As User: {user}")
    
    # We want to run it so it fails fast if there are missing arguments or bad configs.
    # 5 seconds is plenty for consensus/execution clients to boot and validate arguments.
    try:
        su_cmd = ["su", "-s", "/bin/bash", "-c", str(exec_start), str(user)]
        process = subprocess.Popen(su_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Wait up to 5 seconds
        for _ in range(5):
            if process.poll() is not None:
                break
            time.sleep(1)
        
        if process.poll() is not None:
            # It exited!
            out, err = process.communicate()
            if process.returncode != 0:
                print(f"  ❌ Process exited prematurely with code {process.returncode}")
                # Print last 5 lines of stderr
                stderr_lines = err.decode('utf-8').strip().split('\n')[-5:]
                print(f"  STDERR: {''.join(stderr_lines)}")
                return False
            else:
                print(f"  ✅ Service {service_name} executed and exited cleanly (0).")
                return True
            
        # It's still running, which means it successfully started its main loop!
        process.terminate()
        time.sleep(1)
        if process.poll() is None:
            process.kill()
            
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
            
    target_script = args.script_name
    
    if not run_script(target_script, args.network, "0x1234567890123456789012345678901234567890", args.vc_only_bn_address):
        sys.exit(1)
        
    verify_script(target_script)
    print(f"\n🐳 Integration Test run inside Docker complete for {target_script} (MEV={args.mev}).")
