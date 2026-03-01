"""
EthPillar Docker Integration Runner
This script runs INSIDE the container to verify the deploy scripts.
It executes each script with --skip_prompts and verifies the resulting state.
"""
import subprocess
import os
import pwd
import sys
import platform

# 🔍 Verification helpers
def check_user(username):
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False

def check_binary(binary_name):
    # Search for binary in /usr/local/bin
    return os.path.isfile(f"/usr/local/bin/{binary_name}")

def check_service(service_name):
    # Check if systemd file exists in /etc/systemd/system
    path = f"/etc/systemd/system/{service_name}.service"
    return os.path.isfile(path)

DEPLOY_SCRIPTS = [
    "deploy-caplin-erigon.py",
    "deploy-lighthouse-reth.py",
    "deploy-lodestar-besu.py",
    "deploy-nimbus-nethermind.py",
    "deploy-teku-besu.py"
]

def run_script(script_name):
    print(f"\n🚀 Running: python3 {script_name} --skip_prompts --network holesky")
    # Using python executable directly
    result = subprocess.run([
        sys.executable, script_name, 
        "--skip_prompts", "true", 
        "--network", "HOLESKY",
        "--fee_address", "0x1234567890123456789012345678901234567890"
    ], capture_output=False) # Showing output to the console for user visibility
    
    if result.returncode != 0:
        print(f"❌ Script {script_name} failed with return code {result.returncode}")
        # Note: In Docker, scripts might fail at the very end when trying to start services with systemctl.
        # We handle this by continuing and verifying the artifacts (binaries, users, services).
        return False
    print(f"✅ Script {script_name} completed successfully.")
    return True

def verify_all():
    print("\n🔍 Verifying System State...")
    print("-" * 40)
    
    # Check binaries
    binaries = ["mev-boost", "besu", "reth", "erigon", "teku", "lodestar", "nimbus_beacon_node", "lighthouse"]
    for b in binaries:
        present = check_binary(b)
        status = "✅" if present else "❌"
        # Special check for besu which is in /usr/local/bin/besu/bin/besu
        if not present and b == "besu":
            present = os.path.isfile("/usr/local/bin/besu/bin/besu")
            status = "✅" if present else "❌"
        # Special check for Teku which is a folder
        if not present and b == "teku":
            present = os.path.isdir("/usr/local/bin/teku")
            status = "✅" if present else "❌"
        if not present and b == "lodestar":
            present = os.path.isfile("/usr/local/bin/lodestar/lodestar")
            status = "✅" if present else "❌"
        # Special check for Nethermind which is in /usr/local/bin/nethermind/nethermind
        if not present and b == "nethermind":
            present = os.path.isfile("/usr/local/bin/nethermind/nethermind")
            status = "✅" if present else "❌"
        print(f"{status} Binary: {b}")
        
    # Check users
    users = ["execution", "consensus", "validator", "mevboost"]
    for u in users:
        present = check_user(u)
        status = "✅" if present else "❌"
        print(f"{status} User  : {u}")
        
    # Check services
    services = ["execution", "consensus", "validator", "mevboost"]
    for s in services:
        present = check_service(s)
        status = "✅" if present else "❌"
        print(f"{status} Service: {s}")
    
    # Debug: List /usr/local/bin
    print("\n[DEBUG] /usr/local/bin contents:")
    subprocess.run(["ls", "-F", "/usr/local/bin"])

if __name__ == "__main__":
    # In Docker, we need a dummy .env file if it's missing (scripts use dotenv)
    if not os.path.exists(".env"):
        with open(".env", "w") as f:
            f.write("MEVBOOST=true\n")
            
    # Run all scripts sequentially
    for script in DEPLOY_SCRIPTS:
        run_script(script)
            
    # Final verification
    verify_all()
    print("\n🐳 Integration Test run inside Docker complete.")
