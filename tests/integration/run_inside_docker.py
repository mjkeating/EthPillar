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
    from deploy.common import INSTALL_DIR
except ImportError:
    INSTALL_DIR = "/usr/local/bin"

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

def run_script(script_name):
    # Some scripts don't support HOLESKY so we'll test on SEPOLIA to be safe and compatible with old versions
    network = "SEPOLIA" 
    
    print(f"\n🚀 Running: python3 {script_name} --skip_prompts true --network {network} --fee_address 0x1234567890123456789012345678901234567890")
    
    result = subprocess.run([
        sys.executable, script_name, 
        "--skip_prompts", "true", 
        "--network", network,
        "--fee_address", "0x1234567890123456789012345678901234567890"
    ], capture_output=False)
    
    if result.returncode != 0:
        print(f"❌ Script {script_name} failed with return code {result.returncode}")
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
        present = check_service(s)
        status = "✅" if present else "❌"
        print(f"{status} Service: {s}")
        if not present:
            success = False

    print(f"\n[DEBUG] {INSTALL_DIR} contents:")
    subprocess.run(["ls", "-F", INSTALL_DIR])
    
    if not success:
        print(f"\n❌ Verification FAILED for {script_name}.")
        sys.exit(1)
    else:
        print(f"\n✅ Verification PASSED for {script_name}.")

if __name__ == "__main__":
    if not os.path.exists(".env"):
        with open(".env", "w") as f:
            f.write("MEVBOOST=true\n")
            
    if len(sys.argv) < 2:
        print("Usage: python run_inside_docker.py <script_name>")
        sys.exit(1)
        
    target_script = sys.argv[1]
    
    if not run_script(target_script):
        sys.exit(1)
        
    verify_script(target_script)
    print(f"\n🐳 Integration Test run inside Docker complete for {target_script}.")
