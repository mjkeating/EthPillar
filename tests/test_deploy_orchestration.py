"""
Tests for deploy/common.py

All tests call real functions imported from common.py.
No inline logic is reimplemented here.
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, mock_open, call

# Silence consolemenu on import
sys.modules["consolemenu"] = MagicMock()
sys.modules["consolemenu.items"] = MagicMock()
sys.modules["consolemenu.format"] = MagicMock()
sys.modules["consolemenu.menu_component"] = MagicMock()
sys.modules["consolemenu.screen"] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.common import (
    is_valid_eth_address,
    validate_beacon_node_address,
    get_machine_architecture,
    get_raw_architecture,
    network_type,
    write_service_file,
    setup_node,
)


# ─────────────────────────────────────────────────────────────────────────────
# is_valid_eth_address
# ─────────────────────────────────────────────────────────────────────────────

class TestIsValidEthAddress:
    @pytest.mark.parametrize("addr", [
        "0x1234567890123456789012345678901234567890",
        "0xabcdefABCDEF1234567890abcdef123456789012",
        "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    ])
    def test_valid_addresses_return_true(self, addr):
        assert is_valid_eth_address(addr) is True

    @pytest.mark.parametrize("addr", [
        "",                                              # empty
        "0x123",                                         # too short
        "1234567890123456789012345678901234567890",      # missing 0x prefix
        "0x12345678901234567890123456789012345678901",   # too long (41 hex chars)
        "0xGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG",  # invalid hex chars
        "0x 234567890123456789012345678901234567890",    # space in hex
    ])
    def test_invalid_addresses_return_false(self, addr):
        assert is_valid_eth_address(addr) is False


# ─────────────────────────────────────────────────────────────────────────────
# validate_beacon_node_address
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateBeaconNodeAddress:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:5052",
        "http://192.168.1.100:5052",
        "https://192.168.1.100:5052",
        "ws://10.0.0.1:9000",
        "http://0.0.0.0:5052",
        "http://192.168.1.1",           # port is optional per the production regex
    ])
    def test_valid_beacon_urls_return_true(self, url):
        assert validate_beacon_node_address(url) is True

    @pytest.mark.parametrize("url", [
        "",                             # empty
        "http://localhost:5052",        # hostname not IP
        "192.168.1.1:5052",            # missing scheme
        "ftp://192.168.1.1:5052",      # wrong scheme
        "http://999.999.999.999:5052", # invalid IP octets
    ])
    def test_invalid_beacon_urls_return_false(self, url):
        assert validate_beacon_node_address(url) is False


# ─────────────────────────────────────────────────────────────────────────────
# get_machine_architecture
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMachineArchitecture:
    def test_x86_64_maps_to_amd64(self):
        with patch('platform.machine', return_value='x86_64'):
            assert get_machine_architecture() == 'amd64'

    def test_aarch64_maps_to_arm64(self):
        with patch('platform.machine', return_value='aarch64'):
            assert get_machine_architecture() == 'arm64'

    def test_unsupported_architecture_exits(self):
        with patch('platform.machine', return_value='mips'):
            with pytest.raises(SystemExit):
                get_machine_architecture()

    def test_get_raw_architecture_returns_platform_machine(self):
        with patch('platform.machine', return_value='x86_64'):
            assert get_raw_architecture() == 'x86_64'


# ─────────────────────────────────────────────────────────────────────────────
# network_type
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkType:
    @pytest.mark.parametrize("name,expected", [
        ("mainnet", "MAINNET"),
        ("MAINNET", "MAINNET"),
        ("holesky", "HOLESKY"),
        ("Holesky", "HOLESKY"),
        ("hoodi", "HOODI"),
        ("sepolia", "SEPOLIA"),
        ("ephemery", "EPHEMERY"),
    ])
    def test_valid_networks_return_uppercase(self, name, expected):
        assert network_type(name) == expected

    @pytest.mark.parametrize("name", ["ropsten", "goerli", "invalid", ""])
    def test_invalid_networks_raise_argument_type_error(self, name):
        import argparse
        with pytest.raises(argparse.ArgumentTypeError):
            network_type(name)


# ─────────────────────────────────────────────────────────────────────────────
# write_service_file
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteServiceFile:
    def test_writes_content_to_temp_file(self):
        content = "[Unit]\nDescription=Test\n"
        pid = os.getpid()
        expected_temp = f"{pid}_test.service"

        with patch('builtins.open', mock_open()) as mock_file, \
             patch('os.system') as mock_system, \
             patch('os.remove') as mock_remove:
            write_service_file(content, '/etc/systemd/system/test.service', 'test.service')
            mock_file.assert_called_once_with(expected_temp, 'w')
            mock_file().write.assert_called_once_with(content)

    def test_copies_temp_file_to_target_with_sudo(self):
        content = "[Unit]\nDescription=Test\n"
        pid = os.getpid()
        expected_temp = f"{pid}_test.service"
        target = '/etc/systemd/system/test.service'

        with patch('builtins.open', mock_open()), \
             patch('os.system') as mock_system, \
             patch('os.remove'):
            write_service_file(content, target, 'test.service')
            mock_system.assert_called_once_with(f'sudo cp {expected_temp} {target}')

    def test_removes_temp_file_after_copy(self):
        content = "[Unit]\nDescription=Test\n"
        pid = os.getpid()
        expected_temp = f"{pid}_test.service"

        with patch('builtins.open', mock_open()), \
             patch('os.system'), \
             patch('os.remove') as mock_remove:
            write_service_file(content, '/etc/systemd/system/test.service', 'test.service')
            mock_remove.assert_called_once_with(expected_temp)

    def test_temp_filename_includes_pid_to_prevent_collisions(self):
        """Two calls from different PIDs must produce different temp filenames."""
        content = "[Unit]\nDescription=Test\n"
        seen_names = set()

        with patch('builtins.open', mock_open()) as mock_file, \
             patch('os.system'), \
             patch('os.remove'):
            write_service_file(content, '/etc/systemd/system/test.service', 'test.service')
            # The PID-prefixed temp name is always the same within this process
            actual_temp = mock_file.call_args[0][0]
            assert str(os.getpid()) in actual_temp

    def test_missing_temp_file_on_remove_does_not_raise(self):
        content = "[Unit]\nDescription=Test\n"
        with patch('builtins.open', mock_open()), \
             patch('os.system'), \
             patch('os.remove', side_effect=FileNotFoundError):
            # Should not raise
            write_service_file(content, '/etc/systemd/system/test.service', 'test.service')


# ─────────────────────────────────────────────────────────────────────────────
# setup_node
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupNode:
    @patch('subprocess.run')
    def test_full_node_creates_jwt_directory(self, mock_run):
        setup_node('/secrets/jwtsecret', validator_only=False)
        calls_as_str = [str(c) for c in mock_run.call_args_list]
        assert any('mkdir -p' in s for s in calls_as_str)

    @patch('subprocess.run')
    def test_full_node_runs_openssl_to_generate_jwt(self, mock_run):
        setup_node('/secrets/jwtsecret', validator_only=False)
        calls_as_str = [str(c) for c in mock_run.call_args_list]
        assert any('openssl' in s for s in calls_as_str)

    @patch('subprocess.run')
    def test_validator_only_skips_jwt_creation(self, mock_run):
        setup_node('/secrets/jwtsecret', validator_only=True)
        calls_as_str = [str(c) for c in mock_run.call_args_list]
        assert not any('openssl' in s for s in calls_as_str)
        assert not any('mkdir -p' in s for s in calls_as_str)

    @patch('subprocess.run')
    def test_always_runs_apt_update(self, mock_run):
        setup_node('/secrets/jwtsecret', validator_only=False)
        mock_run.assert_any_call(['sudo', 'apt', '-y', '-qq', 'update'])

    @patch('subprocess.run')
    def test_always_runs_apt_upgrade(self, mock_run):
        setup_node('/secrets/jwtsecret', validator_only=False)
        mock_run.assert_any_call(['sudo', 'apt', '-y', '-qq', 'upgrade'])

    @patch('subprocess.run')
    def test_always_installs_chrony(self, mock_run):
        setup_node('/secrets/jwtsecret', validator_only=False)
        mock_run.assert_any_call(['sudo', 'apt', '-y', '-qq', 'install', 'chrony'])

    @patch('subprocess.run')
    def test_validator_only_still_runs_apt_commands(self, mock_run):
        setup_node('/secrets/jwtsecret', validator_only=True)
        mock_run.assert_any_call(['sudo', 'apt', '-y', '-qq', 'update'])
        mock_run.assert_any_call(['sudo', 'apt', '-y', '-qq', 'install', 'chrony'])

    @patch('subprocess.run')
    def test_full_node_call_count(self, mock_run):
        # mkdir + openssl + tee + update + upgrade + autoremove + install = 7
        setup_node('/secrets/jwtsecret', validator_only=False)
        assert mock_run.call_count == 7

    @patch('subprocess.run')
    def test_validator_only_call_count(self, mock_run):
        # update + upgrade + autoremove + install = 4
        setup_node('/secrets/jwtsecret', validator_only=True)
        assert mock_run.call_count == 4
