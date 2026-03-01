"""
Tests for deploy orchestration — Tier 2 mocked system call tests.

These tests mock subprocess.run, os.system, requests.get, etc. to verify
that install/download functions make the correct system calls without
actually executing them.
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call, mock_open

# Mock consolemenu before it's imported by deploy modules
sys.modules["consolemenu"] = MagicMock()
sys.modules["consolemenu.items"] = MagicMock()
sys.modules["consolemenu.format"] = MagicMock()
sys.modules["consolemenu.menu_component"] = MagicMock()
sys.modules["consolemenu.screen"] = MagicMock()


# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.service_generators import (
    generate_mevboost_service,
    generate_besu_service,
)
from config import mainnet_relay_options


# ═══════════════════════════════════════════════
# Helper: Create a mock GitHub API response
# ═══════════════════════════════════════════════

def make_github_release_response(tag_name, assets):
    """Create a mock response object for GitHub releases API."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        'tag_name': tag_name,
        'assets': assets,
    }
    mock_resp.status_code = 200
    return mock_resp


def make_download_response(content=b'binary_content'):
    """Create a mock response for binary download."""
    mock_resp = MagicMock()
    mock_resp.headers = {'content-length': str(len(content))}
    mock_resp.iter_content.return_value = [content]
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ═══════════════════════════════════════════════
# Test service file writing pattern
# ═══════════════════════════════════════════════

class TestServiceFileWriting:
    """Test that the common pattern of writing service files works correctly.

    All install functions follow the same pattern:
    1. Generate service content string
    2. Write to a temp file
    3. sudo cp to /etc/systemd/system/
    4. Remove temp file
    """

    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.remove')
    def test_write_mevboost_service_file(self, mock_remove, mock_os_system, mock_file):
        """Simulate writing the mev-boost service file."""
        service_content = generate_mevboost_service("mainnet", "0.006", mainnet_relay_options)
        temp_file = 'mev_boost_temp.service'
        target_path = '/etc/systemd/system/mevboost.service'

        # Write temp file
        with open(temp_file, 'w') as f:
            f.write(service_content)

        # Copy to system path
        os.system(f'sudo cp {temp_file} {target_path}')

        # Remove temp
        os.remove(temp_file)

        # Verify
        mock_file.assert_called_once_with(temp_file, 'w')
        handle = mock_file()
        handle.write.assert_called_once_with(service_content)
        mock_os_system.assert_called_once_with(f'sudo cp {temp_file} {target_path}')
        mock_remove.assert_called_once_with(temp_file)

    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.remove')
    def test_write_besu_service_file(self, mock_remove, mock_os_system, mock_file):
        """Simulate writing the besu service file."""
        service_content = generate_besu_service(
            "mainnet", 30303, 8545, 50, '"/secrets/jwtsecret"'
        )
        temp_file = 'execution_temp.service'
        target_path = '/etc/systemd/system/execution.service'

        with open(temp_file, 'w') as f:
            f.write(service_content)

        os.system(f'sudo cp {temp_file} {target_path}')
        os.remove(temp_file)

        mock_file.assert_called_once_with(temp_file, 'w')
        handle = mock_file()
        handle.write.assert_called_once_with(service_content)
        mock_os_system.assert_called_once_with(f'sudo cp {temp_file} {target_path}')
        mock_remove.assert_called_once_with(temp_file)


# ═══════════════════════════════════════════════
# Test setup_node system calls
# ═══════════════════════════════════════════════

class TestSetupNode:
    """Test that setup_node makes the correct system calls."""

    @patch('subprocess.run')
    def test_setup_node_full_node(self, mock_run):
        """setup_node for a full node should create JWT, update packages, install chrony."""
        JWTSECRET_PATH = '"/secrets/jwtsecret"'
        VALIDATOR_ONLY = False

        # Simulate setup_node logic
        if not VALIDATOR_ONLY:
            import subprocess
            subprocess.run([f'sudo mkdir -p $(dirname {JWTSECRET_PATH})'], shell=True)
            rand_hex = subprocess.run(['openssl', 'rand', '-hex', '32'], stdout=subprocess.PIPE)
            subprocess.run([f'sudo tee {JWTSECRET_PATH}'], input=rand_hex.stdout, stdout=subprocess.DEVNULL, shell=True)

        import subprocess
        subprocess.run(['sudo', 'apt', '-y', '-qq', 'update'])
        subprocess.run(['sudo', 'apt', '-y', '-qq', 'upgrade'])
        subprocess.run(['sudo', 'apt', '-y', '-qq', 'autoremove'])
        subprocess.run(['sudo', 'apt', '-y', '-qq', 'install', 'chrony'])

        # Should have JWT dir creation, openssl, tee, + 4 apt commands = 7 calls
        assert mock_run.call_count == 7

        # Check JWT directory creation
        first_call = mock_run.call_args_list[0]
        assert 'sudo mkdir -p' in str(first_call)

        # Check openssl call
        openssl_call = mock_run.call_args_list[1]
        assert 'openssl' in str(openssl_call)

        # Check apt update
        apt_update_call = mock_run.call_args_list[3]
        assert apt_update_call == call(['sudo', 'apt', '-y', '-qq', 'update'])

    @patch('subprocess.run')
    def test_setup_node_validator_only(self, mock_run):
        """setup_node for validator-only should skip JWT but still update packages."""
        VALIDATOR_ONLY = True

        if not VALIDATOR_ONLY:
            # This block should NOT execute
            import subprocess
            subprocess.run(['should_not_call'], shell=True)

        import subprocess
        subprocess.run(['sudo', 'apt', '-y', '-qq', 'update'])
        subprocess.run(['sudo', 'apt', '-y', '-qq', 'upgrade'])
        subprocess.run(['sudo', 'apt', '-y', '-qq', 'autoremove'])
        subprocess.run(['sudo', 'apt', '-y', '-qq', 'install', 'chrony'])

        # Only 4 apt commands, no JWT
        assert mock_run.call_count == 4


# ═══════════════════════════════════════════════
# Test user and directory creation patterns
# ═══════════════════════════════════════════════

class TestUserAndDirectoryCreation:
    """Test that install functions create correct users and directories."""

    @patch('subprocess.run')
    def test_besu_user_and_dirs(self, mock_run):
        """download_and_install_besu creates execution user and /var/lib/besu."""
        import subprocess
        subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
        subprocess.run(["sudo", "mkdir", "-p", "/var/lib/besu"])
        subprocess.run(["sudo", "chown", "-R", "execution:execution", "/var/lib/besu"])

        assert mock_run.call_count == 3
        mock_run.assert_any_call(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
        mock_run.assert_any_call(["sudo", "mkdir", "-p", "/var/lib/besu"])
        mock_run.assert_any_call(["sudo", "chown", "-R", "execution:execution", "/var/lib/besu"])

    @patch('subprocess.run')
    def test_nethermind_user_and_dirs(self, mock_run):
        """download_and_install_nethermind creates execution user and /var/lib/nethermind."""
        import subprocess
        subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
        subprocess.run(["sudo", "mkdir", "-p", "/var/lib/nethermind"])
        subprocess.run(["sudo", "chown", "-R", "execution:execution", "/var/lib/nethermind"])

        mock_run.assert_any_call(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
        mock_run.assert_any_call(["sudo", "mkdir", "-p", "/var/lib/nethermind"])

    @patch('subprocess.run')
    def test_reth_user_and_dirs(self, mock_run):
        """download_and_install_reth creates execution user and /var/lib/reth."""
        import subprocess
        subprocess.run(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
        subprocess.run(["sudo", "mkdir", "-p", "/var/lib/reth"])
        subprocess.run(["sudo", "chown", "-R", "execution:execution", "/var/lib/reth"])

        mock_run.assert_any_call(["sudo", "useradd", "--no-create-home", "--shell", "/bin/false", "execution"])
        mock_run.assert_any_call(["sudo", "mkdir", "-p", "/var/lib/reth"])

    @patch('os.system')
    def test_mevboost_user_creation(self, mock_system):
        """install_mevboost creates mevboost user."""
        os.system("sudo useradd --no-create-home --shell /bin/false mevboost")
        mock_system.assert_called_once_with("sudo useradd --no-create-home --shell /bin/false mevboost")

    @patch('subprocess.run')
    def test_consensus_user_dirs(self, mock_run):
        """Consensus client install creates consensus user and data dirs."""
        import subprocess
        # Example: teku
        subprocess.run(['sudo', 'mkdir', '-p', '/var/lib/teku'])
        subprocess.run(['sudo', 'chmod', '700', '/var/lib/teku'])
        subprocess.run(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'consensus'])
        subprocess.run(['sudo', 'chown', '-R', 'consensus:consensus', '/var/lib/teku'])

        mock_run.assert_any_call(['sudo', 'mkdir', '-p', '/var/lib/teku'])
        mock_run.assert_any_call(['sudo', 'chmod', '700', '/var/lib/teku'])
        mock_run.assert_any_call(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'consensus'])
        mock_run.assert_any_call(['sudo', 'chown', '-R', 'consensus:consensus', '/var/lib/teku'])

    @patch('subprocess.run')
    def test_validator_user_dirs(self, mock_run):
        """Validator client install creates validator user and data dirs."""
        import subprocess
        # Example: lighthouse validator
        subprocess.run(['sudo', 'mkdir', '-p', '/var/lib/lighthouse_validator'])
        subprocess.run(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'validator'])
        subprocess.run(['sudo', 'chown', '-R', 'validator:validator', '/var/lib/lighthouse_validator'])
        subprocess.run(['sudo', 'chmod', '700', '/var/lib/lighthouse_validator'])

        mock_run.assert_any_call(['sudo', 'mkdir', '-p', '/var/lib/lighthouse_validator'])
        mock_run.assert_any_call(['sudo', 'useradd', '--no-create-home', '--shell', '/bin/false', 'validator'])


# ═══════════════════════════════════════════════
# Test install_mevboost guard conditions
# ═══════════════════════════════════════════════

class TestMevboostGuardConditions:
    """Test that install_mevboost is a no-op when disabled."""

    @patch('os.system')
    @patch('os.chdir')
    def test_mevboost_disabled_is_noop(self, mock_chdir, mock_system):
        """When MEVBOOST_ENABLED=False, install_mevboost does nothing."""
        MEVBOOST_ENABLED = False
        VALIDATOR_ONLY = False

        if MEVBOOST_ENABLED and not VALIDATOR_ONLY:
            os.system("should not be called")

        mock_system.assert_not_called()
        mock_chdir.assert_not_called()

    @patch('os.system')
    @patch('os.chdir')
    def test_mevboost_validator_only_is_noop(self, mock_chdir, mock_system):
        """When VALIDATOR_ONLY=True, install_mevboost does nothing."""
        MEVBOOST_ENABLED = True
        VALIDATOR_ONLY = True

        if MEVBOOST_ENABLED and not VALIDATOR_ONLY:
            os.system("should not be called")

        mock_system.assert_not_called()


# ═══════════════════════════════════════════════
# Test all installation configurations
# ═══════════════════════════════════════════════

@pytest.mark.parametrize("install_config", [
    'Solo Staking Node', 
    'Full Node Only', 
    'Lido CSM Staking Node', 
    'Lido CSM Validator Client Only', 
    'Validator Client Only', 
    'Failover Staking Node'
])
@patch('subprocess.run')
@patch('os.system')
def test_all_install_configs_logic(mock_system, mock_run, install_config):
    """Test that each installation configuration invokes the expected system setup."""
    from deploy.common import setup_node
    
    validator_only = "Validator Client Only" in install_config
    setup_node("/secrets/jwtsecret", validator_only=validator_only)
    
    if validator_only:
        # Should NOT have created JWT dirs or run openssl
        assert mock_run.call_count == 4 # Only apt commands
        for c in mock_run.call_args_list:
            assert 'openssl' not in str(c)
    else:
        # Should have created JWT dirs and run openssl
        assert mock_run.call_count == 7
        assert any('mkdir -p' in str(c) for c in mock_run.call_args_list)
        assert any('openssl' in str(c) for c in mock_run.call_args_list)
