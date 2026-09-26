"""Tests for integration harness ``systemctl start`` timeout selection."""
from unittest.mock import patch

from tests.integration.service_start import (
    DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC,
    INTEGRATION_TIMEOUT_STOP_SEC,
    NIMBUS_CHECKPOINT_SYNC_START_TIMEOUT_SEC,
    NIMBUS_UNIT_TIMEOUT_START_SEC,
    parse_timeout_start_sec,
    rewrite_timeout_stop_sec,
    systemctl_start_timeout_sec,
    unit_has_nimbus_checkpoint_sync,
)

# Mirrors deploy/nimbus.py output when a checkpoint URL is set (#70).
NIMBUS_SYNC_UNIT = """[Unit]
Description=Nimbus Beacon Node Consensus Client service for HOODI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=consensus
TimeoutStopSec=900
TimeoutStartSec=1800
ExecStartPre=/bin/bash -c 'test -d "/var/lib/nimbus/db" || { rm -rf "/var/lib/nimbus/.checkpoint-sync" && "/usr/local/bin/nimbus_beacon_node" "trustedNodeSync" "--network=hoodi" "--trusted-node-url=http://127.0.0.1:19595" "--data-dir=/var/lib/nimbus/.checkpoint-sync" "--backfill=false" && mv "/var/lib/nimbus/.checkpoint-sync/db" "/var/lib/nimbus/db" && rm -rf "/var/lib/nimbus/.checkpoint-sync"; }'
ExecStart=/usr/local/bin/nimbus_beacon_node --network=hoodi --data-dir=/var/lib/nimbus

[Install]
WantedBy=multi-user.target
"""

NIMBUS_NO_SYNC_UNIT = """[Unit]
Description=Nimbus Beacon Node Consensus Client service for HOODI

[Service]
User=consensus
ExecStart=/usr/local/bin/nimbus_beacon_node --network=hoodi --data-dir=/var/lib/nimbus
"""

LIGHTHOUSE_UNIT = """[Unit]
Description=Lighthouse Consensus Client service for HOODI

[Service]
User=consensus
ExecStart=/usr/local/bin/lighthouse bn --checkpoint-sync-url=http://127.0.0.1:19595
"""

NIMBUS_FALLBACK_UNIT = """[Unit]
Description=Nimbus Beacon Node Consensus Client service for HOODI

[Service]
TimeoutStartSec=1800
ExecStart=/usr/local/bin/nimbus_beacon_node --network=hoodi --trusted-node-url=http://127.0.0.1:19595
"""


def test_nimbus_trusted_node_sync_pre_uses_long_timeout():
    assert unit_has_nimbus_checkpoint_sync(NIMBUS_SYNC_UNIT)
    assert parse_timeout_start_sec(NIMBUS_SYNC_UNIT) == NIMBUS_UNIT_TIMEOUT_START_SEC
    assert systemctl_start_timeout_sec("consensus", NIMBUS_SYNC_UNIT) == min(
        NIMBUS_CHECKPOINT_SYNC_START_TIMEOUT_SEC, NIMBUS_UNIT_TIMEOUT_START_SEC
    )


def test_nimbus_without_sync_step_stays_at_default():
    assert "ExecStartPre=" not in NIMBUS_NO_SYNC_UNIT
    assert not unit_has_nimbus_checkpoint_sync(NIMBUS_NO_SYNC_UNIT)
    assert systemctl_start_timeout_sec("consensus", NIMBUS_NO_SYNC_UNIT) == (
        DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC
    )


def test_non_nimbus_consensus_stays_at_default():
    assert not unit_has_nimbus_checkpoint_sync(LIGHTHOUSE_UNIT)
    assert systemctl_start_timeout_sec("consensus", LIGHTHOUSE_UNIT) == (
        DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC
    )


def test_non_consensus_services_keep_default_even_with_nimbus_pre():
    assert systemctl_start_timeout_sec("execution", NIMBUS_SYNC_UNIT) == (
        DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC
    )
    assert systemctl_start_timeout_sec("mevboost", NIMBUS_SYNC_UNIT) == (
        DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC
    )
    assert systemctl_start_timeout_sec("validator", NIMBUS_SYNC_UNIT) == (
        DEFAULT_SYSTEMCTL_START_TIMEOUT_SEC
    )


def test_fallback_detects_nimbus_plus_timeout_and_sync_url():
    assert unit_has_nimbus_checkpoint_sync(NIMBUS_FALLBACK_UNIT)
    assert systemctl_start_timeout_sec("consensus", NIMBUS_FALLBACK_UNIT) == min(
        NIMBUS_CHECKPOINT_SYNC_START_TIMEOUT_SEC, 1800
    )


def test_timeout_capped_at_unit_timeout_start_sec():
    with patch(
        "tests.integration.service_start.NIMBUS_CHECKPOINT_SYNC_START_TIMEOUT_SEC",
        4000,
    ):
        assert systemctl_start_timeout_sec("consensus", NIMBUS_SYNC_UNIT) == 1800


def test_parse_timeout_start_sec_skips_infinity():
    unit = "[Service]\nTimeoutStartSec=infinity\nExecStart=/bin/true\n"
    assert parse_timeout_start_sec(unit) is None
    nimbus_infinity = (
        "[Unit]\nDescription=Nimbus Beacon Node Consensus Client service for HOODI\n"
        "[Service]\nTimeoutStartSec=infinity\n"
        "ExecStart=/usr/local/bin/nimbus_beacon_node --network=hoodi\n"
    )
    assert unit_has_nimbus_checkpoint_sync(nimbus_infinity)
    assert systemctl_start_timeout_sec("consensus", nimbus_infinity) == min(
        NIMBUS_CHECKPOINT_SYNC_START_TIMEOUT_SEC, NIMBUS_UNIT_TIMEOUT_START_SEC
    )


def test_rewrite_timeout_stop_sec_replaces_production_900():
    rewritten = rewrite_timeout_stop_sec(NIMBUS_SYNC_UNIT)
    assert f"TimeoutStopSec={INTEGRATION_TIMEOUT_STOP_SEC}" in rewritten
    assert "TimeoutStopSec=900" not in rewritten
    assert rewritten.count("TimeoutStopSec=") == 1


def test_rewrite_timeout_stop_sec_inserts_when_missing():
    unit = "[Unit]\nDescription=x\n\n[Service]\nUser=consensus\nExecStart=/bin/true\n"
    rewritten = rewrite_timeout_stop_sec(unit)
    assert (
        f"[Service]\nTimeoutStopSec={INTEGRATION_TIMEOUT_STOP_SEC}\nUser=consensus"
        in rewritten
    )
