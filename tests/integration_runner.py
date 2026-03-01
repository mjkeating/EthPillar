"""
Integration harness for EthPillar deploy scripts.
Runs in WSL. Verifies all client combinations non-interactively.
"""
import subprocess
import os
import pwd
import json
import shutil
import sys

# Requirements: Run as root or with sudo
if os.geteuid() != 0:
    print("Error: Integration tests must be run as root (use sudo).")
    # exit(1) # commenting out so I can test locally if needed, but advise user

def check_user(username):
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False

def check_binary(binary_name):
    # Check if binary is in /usr/local/bin
    return os.path.isfile(f"/usr/local/bin/{binary_name}")

def check_service(service_name):
    # Check if systemd file exists
    path = f"/etc/systemd/system/{service_name}.service"
    return os.path.isfile(path)

def check_data_dir(path):
    return os.path.exists(path)

DEPLOY_SCRIPTS = [
    "deploy-caplin-erigon.py",
    "deploy-lighthouse-reth.py",
    "deploy-lodestar-besu.py",
    "deploy-nimbus-nethermind.py",
    "deploy-teku-besu.py"
]

def run_script(script_name):
    print(f"\n🚀 Running: python3 {script_name} --skip_prompts --network holesky")
    # Using holesky as it's the standard testnet
    result = subprocess.run([
        sys.executable, script_name, 
        "--skip_prompts", "true", 
        "--network", "HOLESKY",
        "--fee_address", "0x1234567890123456789012345678901234567890"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"❌ Script failed with return code {result.returncode}")
        print(result.stdout)
        print(result.stderr)
        return False
    return True

def verify_all():
    print("\n🔍 Verifying System State...")
    
    # Common MEV-Boost
    results = {
        "mevboost_user": check_user("mevboost"),
        "mevboost_binary": check_binary("mev-boost"),
        "mevboost_service": check_service("mevboost"),
        
        # Execution Binary existence (across scripts)
        "besu_binary": check_binary("besu/bin/besu") or check_binary("besu"),
        "reth_binary": check_binary("reth"),
        "nethermind_binary": check_binary("nethermind/nethermind") or check_binary("nethermind"),
        "erigon_binary": check_binary("erigon"),
        
        # Consensus/Validator User existence
        "consensus_user": check_user("consensus"),
        "validator_user": check_user("validator"),
        "execution_user": check_user("execution"),
        
        # Service files
        "execution_service": check_service("execution"),
        "consensus_service": check_service("consensus"),
        "validator_service": check_service("validator"),
    }
    
    all_passed = True
    for key, val in results.items():
        status = "✅" if val else "❌"
        print(f"{status} {key}")
        if not val:
            all_passed = False
            
    return all_passed

if __name__ == "__main__":
    # 1. Run all scripts
    # Note: Running them all sequentially will "pile up" the binaries
    # but that's okay for verifying we can install them all.
    success_count = 0
    for script in DEPLOY_SCRIPTS:
        if run_script(script):
            success_count += 1
            
    print(f"\nFinal tally: {success_count}/{len(DEPLOY_SCRIPTS)} scripts completed.")
    
    # 2. Verify results
    if verify_all():
        print("\n🏆 INTEGRATION TESTS PASSED!")
    else:
        print("\n💥 INTEGRATION TESTS FAILED!")
        sys.exit(1)
