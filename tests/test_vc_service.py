"""Tests for deploy/vc_service.py beacon endpoint helpers."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.vc_service import (
    normalize_vc_name,
    scrape_beacon_endpoint,
    scrape_validator_service_params,
    patch_beacon_endpoint,
    get_beacon_endpoint,
)


PRYSM_VC_SERVICE = """[Unit]
Description=Prysm Validator Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/prysm-validator \\
    --mainnet \\
    --datadir=/var/lib/prysm_validator \\
    --beacon-rest-api-provider=http://127.0.0.1:5052 \\
    --accept-terms-of-use
"""

LIGHTHOUSE_VC_SERVICE = """[Unit]
Description=Lighthouse Validator Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/lighthouse vc \\
    --network=mainnet \\
    --beacon-nodes=http://192.168.1.10:5052
"""


class TestNormalizeVcName:
    def test_canonical_names(self):
        for name in ("Lighthouse", "Nimbus", "Teku", "Lodestar", "Prysm"):
            assert normalize_vc_name(name) == name

    def test_description_prefix(self):
        assert normalize_vc_name("Prysm Validator Client") == "Prysm"
        assert normalize_vc_name("lighthouse vc") == "Lighthouse"

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported validator client"):
            normalize_vc_name("Grandine")


class TestScrapeBeaconEndpoint:
    def test_scrape_prysm(self):
        url = scrape_beacon_endpoint(PRYSM_VC_SERVICE, "Prysm")
        assert url == "http://127.0.0.1:5052"

    def test_scrape_lighthouse(self):
        url = scrape_beacon_endpoint(LIGHTHOUSE_VC_SERVICE, "Lighthouse")
        assert url == "http://192.168.1.10:5052"

    def test_scrape_validator_service_params(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write(PRYSM_VC_SERVICE)
            path = fh.name
        try:
            params = scrape_validator_service_params(path, "Prysm")
            assert params["vc_name"] == "Prysm"
            assert params["beacon_flag"] == "--beacon-rest-api-provider"
            assert params["beacon_endpoint"] == "http://127.0.0.1:5052"
            assert "Prysm Validator Client" in params["description"]
        finally:
            os.remove(path)


class TestPatchBeaconEndpoint:
    def test_patch_updates_endpoint(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write(PRYSM_VC_SERVICE)
            path = fh.name
        try:
            updated = patch_beacon_endpoint(path, "Prysm", "http://10.0.0.5:5052")
            assert updated is True
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            assert "--beacon-rest-api-provider=http://10.0.0.5:5052" in content
            assert "http://127.0.0.1:5052" not in content
        finally:
            os.remove(path)

    def test_patch_no_change_returns_false(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write(PRYSM_VC_SERVICE)
            path = fh.name
        try:
            updated = patch_beacon_endpoint(path, "Prysm", "http://127.0.0.1:5052")
            assert updated is False
        finally:
            os.remove(path)

    def test_patch_missing_flag_raises(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("[Service]\nExecStart=/usr/local/bin/prysm-validator --mainnet\n")
            path = fh.name
        try:
            with pytest.raises(ValueError, match="Beacon flag"):
                patch_beacon_endpoint(path, "Prysm", "http://127.0.0.1:5052")
        finally:
            os.remove(path)

    def test_patch_invalid_url_raises(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write(PRYSM_VC_SERVICE)
            path = fh.name
        try:
            with pytest.raises(ValueError, match="Invalid beacon endpoint"):
                patch_beacon_endpoint(path, "Prysm", "not-a-url")
        finally:
            os.remove(path)

    def test_patch_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            patch_beacon_endpoint("/tmp/does-not-exist-validator.service", "Prysm", "http://127.0.0.1:5052")


def test_get_beacon_endpoint_helper():
    assert get_beacon_endpoint("127.0.0.1", "5052") == "http://127.0.0.1:5052"
    assert get_beacon_endpoint("", "") == "http://127.0.0.1:5052"