"""Tests for manage.epbs prepare / complete / relay parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploy.erigon import generate_erigon_service
from deploy.lighthouse import generate_lighthouse_bn_service, generate_lighthouse_vc_service
from deploy.lodestar import generate_lodestar_bn_service, generate_lodestar_vc_service
from deploy.mevboost import generate_mevboost_service
from deploy.nimbus import generate_nimbus_bn_service, generate_nimbus_vc_service
from deploy.prysm import generate_prysm_bn_service, generate_prysm_vc_service
from deploy.teku import generate_teku_bn_service, generate_teku_vc_service
from deploy.grandine import generate_grandine_bn_service
from deploy.charon import generate_charon_service
from manage.epbs import (
    CHARON_EPBS_NOTE,
    COMPLETE_REFUSED,
    MIGRATION_FORMAT,
    MIGRATION_VERSION,
    EPBS_BUILD_FACTOR_ENV,
    NIMBUS_EPBS_MIN_VERSION,
    NIMBUS_MAX_BUILDER_ENTRIES,
    NIMBUS_MAX_BUILDER_URL_SIZE,
    NIMBUS_SUGGESTED_GAS_FLAG,
    PRYSM_BUILDER_CLI_MIN_VERSION,
    PRYSM_SUGGESTED_GAS_LIMIT_NOTE,
    TEKU_BOOST_FACTOR_UINT64_MAX,
    TEKU_EPBS_BUILD_FACTOR_ENV,
    TEKU_SEPOLIA_GAS_FLAG,
    TEKU_EPBS_MIN_VERSION,
    _default_write_data,
    default_auth_data_hex,
    map_epbs_build_factor,
    EpbsError,
    caplin_supports_epbs,
    complete_rollback_hint,
    EpbsFilesystem,
    charon_has_builder_api,
    complete,
    eth_min_bid_to_gwei,
    export_migration,
    import_migration,
    load_migration_file,
    nimbus_has_builder_url,
    nimbus_supports_epbs,
    parse_mevboost_relays,
    prepare,
    prysm_has_builder_list,
    status,
    strip_bn_sidecar,
    strip_charon_builder_api,
    support_level,
    teku_supports_epbs,
    promote_installed_teku_flags,
    teku_builder_boost_factor,
)
from manage.service_parse import (
    get_flag_value,
    get_flag_values,
    has_flag,
    normalize_cli_args,
    parse_unit,
)

RELAYS = [
    {
        "name": "Flashbots",
        "url": "https://0xac6e77dfe25ecd6110b8e780608cce0dab71fdd5ebea22a16c0205200f2f8e2e3ad3b71d3499c54ad14d6c21b41a37ae@boost-relay.flashbots.net",
    },
    {
        "name": "Ultra Sound",
        "url": "https://0xa1559ace749633b997cb3fdacffb890aeebdb0f5a3b6aaa7eeeaf1a38af0a8fe88b9e4b1f61f236d2e64d95733327a62@relay.ultrasound.money",
    },
]
FEE = "0x0000000000000000000000000000000000000001"
JWT = "/secrets/jwtsecret"
SYNC = "https://example.invalid"


def _fs(tmp_path: Path) -> EpbsFilesystem:
    """Build an in-memory-style filesystem under *tmp_path* for unit tests.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        :class:`EpbsFilesystem` writing units into ``tmp_path/systemd`` and
        Prysm settings into ``tmp_path/proposer-settings.json``. Stopping
        mev-boost sets ``fs.mevboost_disabled``.
    """
    systemd = tmp_path / "systemd"
    systemd.mkdir(parents=True, exist_ok=True)
    settings = tmp_path / "proposer-settings.json"
    fs = EpbsFilesystem(
        systemd_dir=str(systemd),
        prysm_settings_path=str(settings),
        read_text=lambda p: Path(p).read_text(encoding="utf-8") if Path(p).is_file() else None,
        exists=lambda p: Path(p).is_file(),
        write_unit=lambda p, c: Path(p).write_text(c if c.endswith("\n") else c + "\n", encoding="utf-8"),
        write_data=lambda p, c: Path(p).write_text(c if c.endswith("\n") else c + "\n", encoding="utf-8"),
        stop_disable_mevboost=lambda: setattr(fs, "mevboost_disabled", True),
    )
    fs.mevboost_disabled = False  # type: ignore[attr-defined]
    return fs


def _write(fs: EpbsFilesystem, key: str, content: str) -> None:
    """Write a systemd unit named *key* into *fs*.

    Args:
        fs: Test filesystem.
        key: Logical unit (``validator``, ``consensus``, ``mevboost``).
        content: Full unit file text.
    """
    Path(fs.unit_path(key)).write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")


def _args(content: str) -> list[str]:
    """Return normalized ExecStart tokens from a unit file.

    Args:
        content: Full systemd unit text.

    Returns:
        CLI argument list including the binary path.
    """
    return normalize_cli_args(parse_unit(content).exec_args)


def test_tui_is_gated_to_full_support_only() -> None:
    """MEV-Boost TUI (``epbsTuiSupported``) matches ``support_level == full``."""
    assert support_level("Prysm") == "full"
    assert support_level("Lodestar") == "full"
    assert support_level("Teku") == "full"
    assert support_level("Nimbus") == "full"
    assert support_level("Erigon-Caplin") == "full"
    assert support_level("Lighthouse") == "full"
    for client in ("Grandine", ""):
        assert support_level(client) != "full"


def test_caplin_prepare_and_complete(tmp_path: Path) -> None:
    """Caplin v3.7.1 prepare writes builders JSON; complete strips the sidecar."""
    fs = _fs(tmp_path)
    fs.caplin_builders_path = str(tmp_path / "caplin-builders.json")
    fs.run_version = lambda _argv: "erigon version 3.7.1-abcdef0\n"
    _write(fs, "mevboost", generate_mevboost_service("sepolia", "0.006", RELAYS))
    _write(
        fs,
        "execution",
        generate_erigon_service(
            "sepolia",
            "30303",
            "8545",
            "50",
            JWT,
            "9000",
            "5052",
            "100",
            SYNC,
            mev_parameters="--caplin.mev-relay-url=http://127.0.0.1:18550",
        ),
    )
    plan = prepare(fs, apply=False)
    assert plan.support == "full"
    assert plan.client == "Erigon-Caplin"
    assert not Path(fs.caplin_builders_path).exists()

    applied = prepare(fs, apply=True)
    assert applied.applied
    assert "execution" in applied.services_to_restart
    exec_text = Path(fs.unit_path("execution")).read_text(encoding="utf-8")
    assert "--caplin.mev-relay-url=http://127.0.0.1:18550" in exec_text
    data = json.loads(Path(fs.caplin_builders_path).read_text(encoding="utf-8"))
    urls = [entry["url"] for entry in data["builders"]]
    assert len(urls) == 2
    assert data["min_bid"] == "6000000"
    assert data["builders"][0]["max_execution_payment"] == "0"
    assert any("200M" in warning for warning in applied.warnings)

    done = complete(fs, apply=True)
    assert done.applied
    assert done.disable_mevboost is True
    assert getattr(fs, "mevboost_disabled") is True
    stripped = Path(fs.unit_path("execution")).read_text(encoding="utf-8")
    assert "--caplin.mev-relay-url" not in stripped
    assert "execution" in done.services_to_restart
    kept = json.loads(Path(fs.caplin_builders_path).read_text(encoding="utf-8"))
    assert len(kept["builders"]) == 2


def test_caplin_prerelease_meets_v371_floor(tmp_path: Path) -> None:
    """A v3.7.1 prerelease meets the Caplin builder floor; v3.7.0 does not."""
    fs = _fs(tmp_path)
    unit = generate_erigon_service(
        "sepolia", "30303", "8545", "50", JWT, "9000", "5052", "100", SYNC,
    )
    fs.run_version = lambda _argv: "erigon version 3.7.1-rc.0\n"
    assert caplin_supports_epbs(fs, unit) is True
    fs.run_version = lambda _argv: "erigon version 3.7.0-rc.1\n"
    assert caplin_supports_epbs(fs, unit) is False


def test_caplin_prepare_skips_older_than_v371(tmp_path: Path) -> None:
    """Erigon before v3.7.1 does not get a builder list."""
    fs = _fs(tmp_path)
    fs.caplin_builders_path = str(tmp_path / "caplin-builders.json")
    fs.run_version = lambda _argv: "erigon version 3.7.0\n"
    _write(fs, "mevboost", generate_mevboost_service("sepolia", "0.006", RELAYS))
    _write(
        fs,
        "execution",
        generate_erigon_service(
            "sepolia",
            "30303",
            "8545",
            "50",
            JWT,
            "9000",
            "5052",
            "100",
            SYNC,
            mev_parameters="--caplin.mev-relay-url=http://127.0.0.1:18550",
        ),
    )
    plan = prepare(fs, apply=True)
    assert any("older than 3.7.1" in action.detail for action in plan.actions)
    assert not Path(fs.caplin_builders_path).exists()
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=True)


def test_parse_mevboost_relays_and_min_bid() -> None:
    """``-relay`` URLs and ``-min-bid`` are scraped from mevboost.service."""
    unit = generate_mevboost_service("mainnet", "0.006", RELAYS)
    cfg = parse_mevboost_relays(unit)
    assert cfg.min_bid == "0.006"
    assert len(cfg.urls) == 2
    assert "boost-relay.flashbots.net" in cfg.urls[0]


def test_prysm_prepare_and_complete(tmp_path: Path) -> None:
    """Prysm prepare writes proposer-settings; complete strips the BN sidecar."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )

    dry = prepare(fs, apply=False)
    assert dry.support == "full"
    assert dry.applied is False
    assert "Do not stop MEV-Boost" in dry.warnings[0]
    assert "--proposer-settings-file" not in Path(fs.unit_path("validator")).read_text(encoding="utf-8")

    plan = prepare(fs, apply=True)
    assert plan.applied
    vc = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    args = _args(vc)
    assert not has_flag(args, "--enable-builder")
    assert has_flag(args, "--proposer-settings-file")
    settings = json.loads(Path(fs.prysm_settings_path).read_text(encoding="utf-8"))
    assert settings["version"] == 2
    assert settings["default_config"]["fee_recipient"] == FEE
    builder = settings["default_config"]["builder"]
    assert "enabled" not in builder
    assert "relays" not in builder
    assert builder["builders"] == [{"url": r["url"]} for r in RELAYS]
    assert builder["max_execution_payment"] == "0"
    assert builder["min_bid"] == "6000000"  # 0.006 ETH → Gwei
    assert "gas_limit" not in settings["default_config"]
    assert not any("200000000" in w for w in plan.warnings)
    assert "validator" in plan.services_to_restart

    # Idempotent
    again = prepare(fs, apply=True)
    assert again.applied
    assert any("already has these relays" in w for w in again.warnings)

    # BN sidecar still present until complete
    bn_before = Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert "18550" in bn_before

    done = complete(fs, apply=True)
    assert done.disable_mevboost
    assert fs.mevboost_disabled is True  # type: ignore[attr-defined]
    bn = Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert "18550" not in bn
    assert not has_flag(_args(bn), "--http-mev-relay")
    # VC builders remain; deprecated flag stays off
    kept = json.loads(Path(fs.prysm_settings_path).read_text(encoding="utf-8"))
    assert kept["default_config"]["builder"]["builders"]
    assert not has_flag(_args(Path(fs.unit_path("validator")).read_text(encoding="utf-8")), "--enable-builder")
    hint = complete_rollback_hint(fs)
    assert hint in done.format_text()
    assert "restart consensus validator" in hint
    assert "charon" not in hint
    assert "enable --now mevboost" in hint

    # Idempotent complete + status after a successful cutover
    again_done = complete(fs, apply=True)
    assert again_done.applied
    assert "18550" not in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    after = status(fs)
    assert "VC relays: yes" in after
    assert "already removed" in after
    assert "Complete: refused" not in after


def test_prysm_prepare_migrates_legacy_relays_and_preserves_other_fields(tmp_path: Path) -> None:
    """v7.1 relays/enabled are replaced; graffiti, gas limit, and per-key config stay."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    pubkey = "0x" + "ab" * 48
    legacy = {
        "version": 1,
        "proposer_config": {
            pubkey: {"fee_recipient": FEE, "graffiti": "keep-me"},
        },
        "default_config": {
            "fee_recipient": FEE,
            "graffiti": "default-graffiti",
            "gas_limit": "30000000",
            "builder": {
                "enabled": True,
                "relays": ["https://old.example/relay"],
                "gas_limit": "30000000",
                "max_execution_payment": "0",
                "builder_boost_factor": "100",
                "builders_set": True,
            },
        },
    }
    Path(fs.prysm_settings_path).write_text(json.dumps(legacy), encoding="utf-8")
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters=(
                "--enable-builder "
                f"--proposer-settings-file={fs.prysm_settings_path} "
                "--suggested-gas-limit=30000000"
            ),
        ),
    )

    # Legacy relays alone are not a prepared builder list.
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)
    st = status(fs)
    assert "legacy builder.relays is ignored" in st
    assert "VC relays: no" in st

    plan = prepare(fs, apply=True)
    assert plan.applied
    settings = json.loads(Path(fs.prysm_settings_path).read_text(encoding="utf-8"))
    assert settings["version"] == 2
    assert settings["proposer_config"][pubkey]["graffiti"] == "keep-me"
    default = settings["default_config"]
    assert default["graffiti"] == "default-graffiti"
    assert default["gas_limit"] == "30000000"
    builder = default["builder"]
    assert builder["builders"] == [{"url": r["url"]} for r in RELAYS]
    assert "enabled" not in builder
    assert "relays" not in builder
    assert "builders_set" not in builder
    # Operator builder gas limit is left; it is legacy v1 content Prysm drops at the fork.
    assert builder["gas_limit"] == "30000000"
    assert builder["builder_boost_factor"] == "100"
    assert builder["min_bid"] == "6000000"
    args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert not has_flag(args, "--enable-builder")
    assert has_flag(args, "--suggested-gas-limit")
    assert get_flag_value(args, "--suggested-gas-limit") == "30000000"

    done = complete(fs, apply=True)
    assert done.disable_mevboost
    assert prysm_has_builder_list(
        json.loads(Path(fs.prysm_settings_path).read_text(encoding="utf-8"))
    )


def _prysm_version(fs: EpbsFilesystem, version: str = "v7.2.1") -> None:
    """Stub ``prysm-validator --version`` so prepare takes the CLI or file path."""
    fs.run_version = lambda _argv: f"Prysm/{version}/linux-amd64\n"


def test_prysm_sepolia_gas_limit_is_documented_not_written(tmp_path: Path) -> None:
    """Prysm before v7.2.1 on Sepolia warns about 200M and does not write gas_limit."""
    fs = _fs(tmp_path)
    _prysm_version(fs, "v7.2.0")
    _write(fs, "mevboost", generate_mevboost_service("sepolia", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "sepolia", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "sepolia",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )
    plan = prepare(fs, apply=True)
    settings = json.loads(Path(fs.prysm_settings_path).read_text(encoding="utf-8"))
    assert "gas_limit" not in settings["default_config"]
    assert any("200000000" in w for w in plan.warnings)
    assert any("60M" in w for w in plan.warnings)

    settings["default_config"]["gas_limit"] = "200000000"
    Path(fs.prysm_settings_path).write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    again = prepare(fs, apply=True)
    assert not any("200000000" in w for w in again.warnings)
    kept = json.loads(Path(fs.prysm_settings_path).read_text(encoding="utf-8"))
    assert kept["default_config"]["gas_limit"] == "200000000"
    assert kept["default_config"]["builder"]["builders"]
    # Sepolia keeps the classic sidecar until complete, same as Hoodi.
    assert "18550" in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")


def test_map_epbs_build_factor_shared_and_disabled() -> None:
    """Prysm and Teku share one token table; ePBS off is always factor 0."""
    assert map_epbs_build_factor("always", enabled=False, client="Prysm") == ("0", None)
    assert map_epbs_build_factor("local", client="Prysm") == ("0", None)
    assert map_epbs_build_factor("always", client="Prysm") == (TEKU_BOOST_FACTOR_UINT64_MAX, None)
    assert map_epbs_build_factor("maxprofit", client="Prysm") == ("100", None)
    capped, warning = map_epbs_build_factor("18446744073709551616", client="Prysm")
    assert capped == TEKU_BOOST_FACTOR_UINT64_MAX
    assert warning and "capping" in warning
    assert map_epbs_build_factor("", client="Prysm") == (None, None)
    assert default_auth_data_hex("https://User:pass@Boost-Relay.Example/path") == (
        "0x" + b"boost-relay.example".hex()
    )


def test_prysm_v721_prepare_uses_builder_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """v7.2.1 writes builder CLI flags and does not create a settings file."""
    monkeypatch.setenv(EPBS_BUILD_FACTOR_ENV, "maxprofit")
    fs = _fs(tmp_path)
    _prysm_version(fs, PRYSM_BUILDER_CLI_MIN_VERSION)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder --builder-boost-factor=0",
        ),
    )
    plan = prepare(fs, apply=True)
    assert plan.applied
    assert not Path(fs.prysm_settings_path).exists()
    args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert not has_flag(args, "--enable-builder")
    assert not has_flag(args, "--proposer-settings-file")
    urls = get_flag_value(args, "--builder-urls")
    assert "boost-relay.flashbots.net" in urls
    assert "18550" not in urls
    assert get_flag_value(args, "--builder-min-bid") == "6000000"
    assert get_flag_value(args, "--builder-boost-factor") == "100"
    assert get_flag_value(args, "--builder-max-execution-payment") == "0"
    # The beacon-node sidecar stays until complete.
    assert "18550" in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    done = complete(fs, apply=True)
    assert done.disable_mevboost
    kept = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert "boost-relay.flashbots.net" in get_flag_value(kept, "--builder-urls")
    assert "18550" not in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")


def test_prysm_v721_sepolia_keeps_sidecar_until_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sepolia v7.2.1 follows the 200M schedule and keeps classic BN MEV until complete."""
    monkeypatch.delenv(EPBS_BUILD_FACTOR_ENV, raising=False)
    fs = _fs(tmp_path)
    _prysm_version(fs)
    _write(fs, "mevboost", generate_mevboost_service("sepolia", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "sepolia", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "sepolia",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder --builder-boost-factor=0 --suggested-gas-limit=60000000",
        ),
    )
    plan = prepare(fs, apply=True)
    assert not Path(fs.prysm_settings_path).exists()
    assert not any("60M" in w for w in plan.warnings)
    assert any(PRYSM_SUGGESTED_GAS_LIMIT_NOTE in w for w in plan.warnings)
    assert not any("past Glamsterdam" in w for w in plan.warnings)
    assert "Do not stop MEV-Boost" in plan.warnings[0]
    args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert not has_flag(args, "--enable-builder")
    assert has_flag(args, "--builder-urls")
    # Empty factor clears a leftover 0 so Prysm's default 100 applies.
    assert not has_flag(args, "--builder-boost-factor")
    assert has_flag(args, "--suggested-gas-limit")
    assert has_flag(_args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8")), "--http-mev-relay")
    assert "consensus" not in plan.services_to_restart
    done = complete(fs, apply=True)
    assert done.disable_mevboost
    assert not has_flag(_args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8")), "--http-mev-relay")


def test_prysm_v721_syncs_existing_settings_hex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing settings file is rewritten to 0x-hex and matches the CLI."""
    monkeypatch.setenv(EPBS_BUILD_FACTOR_ENV, "always")
    fs = _fs(tmp_path)
    _prysm_version(fs)
    pubkey_bytes = bytes(range(48))
    pubkey_b64 = __import__("base64").b64encode(pubkey_bytes).decode("ascii")
    auth_b64 = __import__("base64").b64encode(b"relay.example").decode("ascii")
    legacy = {
        "version": 2,
        "default_config": {
            "fee_recipient": FEE,
            "builder": {
                "builders": [
                    {
                        "url": RELAYS[0]["url"],
                        "pubkeys": [pubkey_b64],
                        "auth_data": auth_b64,
                    }
                ],
                "relays": ["https://old.example"],
            },
        },
    }
    Path(fs.prysm_settings_path).write_text(json.dumps(legacy), encoding="utf-8")
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            extra_parameters=f"--proposer-settings-file={fs.prysm_settings_path}",
        ),
    )
    prepare(fs, apply=True)
    settings = json.loads(Path(fs.prysm_settings_path).read_text(encoding="utf-8"))
    builders = settings["default_config"]["builder"]["builders"]
    first = builders[0]
    assert "pubkeys" not in first
    assert first["builder_pubkeys"] == ["0x" + pubkey_bytes.hex()]
    assert first["auth_data"] == "0x" + b"relay.example".hex()
    assert "relays" not in settings["default_config"]["builder"]
    assert settings["default_config"]["builder"]["builder_boost_factor"] == TEKU_BOOST_FACTOR_UINT64_MAX
    args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(args, "--builder-boost-factor") == TEKU_BOOST_FACTOR_UINT64_MAX
    assert has_flag(args, "--builder-urls")


def test_prysm_v721_caps_oversized_build_factor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A factor above uint64 max is capped on the CLI and in a synced settings file."""
    monkeypatch.setenv(EPBS_BUILD_FACTOR_ENV, "18446744073709551616")
    fs = _fs(tmp_path)
    _prysm_version(fs)
    Path(fs.prysm_settings_path).write_text("{}\n", encoding="utf-8")
    _write(fs, "mevboost", generate_mevboost_service("hoodi", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "hoodi", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "hoodi",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            extra_parameters=f"--proposer-settings-file={fs.prysm_settings_path}",
        ),
    )
    plan = prepare(fs, apply=True)
    assert any("capping" in w for w in plan.warnings)
    args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(args, "--builder-boost-factor") == TEKU_BOOST_FACTOR_UINT64_MAX
    settings = json.loads(Path(fs.prysm_settings_path).read_text(encoding="utf-8"))
    assert (
        settings["default_config"]["builder"]["builder_boost_factor"]
        == TEKU_BOOST_FACTOR_UINT64_MAX
    )
    # Hoodi is still pre-Glamsterdam, so the sidecar stays until complete.
    assert "18550" in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")


def test_prysm_rejects_hostname_less_builder_url(tmp_path: Path) -> None:
    """Prysm v7.2.1 rejects builder URLs with an empty hostname."""
    fs = _fs(tmp_path)
    _prysm_version(fs)
    bad = [{"name": "bad", "url": "https://:8080"}]
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", bad))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service("mainnet", SYNC, JWT, "5052", "9000", "9001", "100"),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet", "ep", "--beacon-rest-api-provider=http://127.0.0.1:5052"
        ),
    )
    with pytest.raises(EpbsError, match="no hostname"):
        prepare(fs, apply=True)


def test_teku_sepolia_sets_explicit_200m_gas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Teku on Sepolia still needs an explicit 200M gas limit."""
    monkeypatch.delenv(TEKU_EPBS_BUILD_FACTOR_ENV, raising=False)
    monkeypatch.delenv(EPBS_BUILD_FACTOR_ENV, raising=False)
    fs = _fs(tmp_path)
    fs.run_version = lambda _argv: "teku/v26.11.0/linux-x86_64/openjdk-java-25\n"
    _write(fs, "mevboost", generate_mevboost_service("sepolia", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_teku_bn_service(
            "sepolia", SYNC, JWT, "5052", "9000", "100",
            mev_parameters="--builder-endpoint=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_teku_vc_service(
            "sepolia",
            "ep",
            "--beacon-node-api-endpoint=http://127.0.0.1:5052",
            extra_parameters="--validators-builder-registration-default-enabled=true",
        ),
    )
    plan = prepare(fs, apply=True)
    args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(args, TEKU_SEPOLIA_GAS_FLAG) == "200000000"
    assert any("200000000" in w for w in plan.warnings)
    again = prepare(fs, apply=True)
    assert not any("sets" in w and "200000000" in w for w in again.warnings)


def test_lodestar_build_factor_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lodestar maps the shared build factor onto selection flags."""
    monkeypatch.setenv(EPBS_BUILD_FACTOR_ENV, "local")
    fs = _fs(tmp_path)
    fs.run_help = lambda _argv: "--builder.urls --builder.minBid\n"
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_lodestar_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--builder --builder.urls http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_lodestar_vc_service(
            "mainnet",
            "ep",
            "--beaconNodes=http://127.0.0.1:5052",
            extra_parameters="--builder",
        ),
    )
    prepare(fs, apply=True)
    args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(args, "--builder.selection") == "executionalways"
    assert not has_flag(args, "--builder.boostFactor")


def test_eth_min_bid_to_gwei() -> None:
    """MEV-Boost ETH min-bid becomes Lodestar integer Gwei."""
    assert eth_min_bid_to_gwei("0") == "0"
    assert eth_min_bid_to_gwei("0.006") == "6000000"
    assert eth_min_bid_to_gwei("0.01") == "10000000"
    assert eth_min_bid_to_gwei("1") == "1000000000"
    with pytest.raises(EpbsError, match="Cannot convert"):
        eth_min_bid_to_gwei("not-a-number")
    with pytest.raises(EpbsError, match="non-negative"):
        eth_min_bid_to_gwei("-0.1")


def test_prysm_charon_prepare_and_complete(tmp_path: Path) -> None:
    """Charon DVT: prepare skips VC relays; complete strips --builder-api + BN."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "charon",
        generate_charon_service(
            "mainnet",
            "http://127.0.0.1:5052",
            builder_api=True,
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:3600",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )

    prep = prepare(fs, apply=True)
    assert prep.applied
    ch = Path(fs.unit_path("charon")).read_text(encoding="utf-8")
    assert charon_has_builder_api(ch)
    assert any("unchanged: keep --builder-api" in a.detail for a in prep.actions)
    assert any("Charon DVT owns builder path" in a.detail for a in prep.actions)
    assert CHARON_EPBS_NOTE in prep.warnings
    # Must not bypass Charon with a VC relay list.
    assert "--proposer-settings-file" not in Path(fs.unit_path("validator")).read_text(
        encoding="utf-8"
    )
    assert not Path(fs.prysm_settings_path).exists()

    done = complete(fs, apply=True)
    assert done.applied
    ch_after = Path(fs.unit_path("charon")).read_text(encoding="utf-8")
    assert not charon_has_builder_api(ch_after)
    assert "charon" in done.services_to_restart
    assert CHARON_EPBS_NOTE in done.warnings
    assert "18550" not in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    hint = complete_rollback_hint(fs)
    assert hint in done.format_text()
    assert "charon.service.bak.epbs" in hint
    assert "restart consensus charon validator" in hint
    st = status(fs)
    assert "Charon: installed" in st
    assert "builder-api=no" in st
    assert "already removed (or never set)" in st
    assert "Complete: refused" not in st


def test_lodestar_charon_prepare_skips_vc_builder_urls(tmp_path: Path) -> None:
    """Charon + Lodestar: prepare must not write --builder.urls on the VC."""
    fs = _fs(tmp_path)
    fs.run_help = lambda _argv: "--builder.urls --builder.minBid\n"
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.01", RELAYS))
    _write(
        fs,
        "consensus",
        generate_lodestar_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--builder --builder.urls http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "charon",
        generate_charon_service(
            "mainnet",
            "http://127.0.0.1:5052",
            builder_api=True,
        ),
    )
    _write(
        fs,
        "validator",
        generate_lodestar_vc_service(
            "mainnet",
            "ep",
            "--beaconNodes=http://127.0.0.1:3600",
            fee_parameters=f"--suggestedFeeRecipient={FEE}",
            extra_parameters="--builder --distributed",
        ),
    )
    prep = prepare(fs, apply=True)
    assert prep.applied
    assert any("Charon DVT owns builder path" in a.detail for a in prep.actions)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert not has_flag(vc_args, "--builder.urls")
    assert not has_flag(vc_args, "--builder.minBid")
    assert has_flag(vc_args, "--builder")

    done = complete(fs, apply=True)
    assert done.applied
    assert not charon_has_builder_api(
        Path(fs.unit_path("charon")).read_text(encoding="utf-8")
    )
    bn_args = _args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8"))
    assert not has_flag(bn_args, "--builder.urls")


def test_complete_rollback_hint_omits_missing_units(tmp_path: Path) -> None:
    """Rollback hint only names units that exist on the filesystem."""
    fs = _fs(tmp_path)
    _write(fs, "consensus", "[Service]\nExecStart=/bin/true\n")
    hint = complete_rollback_hint(fs)
    assert "consensus.service.bak.epbs" in hint
    assert "restart consensus" in hint
    assert "charon" not in hint
    assert "validator" not in hint
    assert "mevboost" not in hint


def test_strip_charon_builder_api() -> None:
    unit = generate_charon_service("mainnet", "http://127.0.0.1:5052", builder_api=True)
    assert charon_has_builder_api(unit)
    stripped = strip_charon_builder_api(unit)
    assert not charon_has_builder_api(stripped)


def test_lodestar_prepare_adds_builder_urls(tmp_path: Path) -> None:
    """Lodestar prepare adds ``--builder.urls`` when ``--help`` lists the flag."""
    fs = _fs(tmp_path)
    fs.run_help = lambda _argv: "--builder.urls --builder.minBid\n"
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.01", RELAYS))
    _write(
        fs,
        "consensus",
        generate_lodestar_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--builder --builder.urls http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_lodestar_vc_service(
            "mainnet",
            "ep",
            "--beaconNodes=http://127.0.0.1:5052",
            fee_parameters=f"--suggestedFeeRecipient={FEE}",
            extra_parameters="--builder",
        ),
    )
    plan = prepare(fs, apply=True)
    assert plan.support == "full"
    args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert has_flag(args, "--builder")
    urls = get_flag_value(args, "--builder.urls")
    assert "boost-relay.flashbots.net" in urls
    assert get_flag_value(args, "--builder.minBid") == "10000000"  # 0.01 ETH → Gwei

    complete(fs, apply=True)
    bn_args = _args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8"))
    assert not has_flag(bn_args, "--builder.urls")
    assert not has_flag(bn_args, "--builder")
    # VC still has relays
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert has_flag(vc_args, "--builder.urls")


def _lighthouse_fs(tmp_path: Path, version: str) -> EpbsFilesystem:
    """Filesystem whose Lighthouse version probe returns *version*."""
    fs = _fs(tmp_path)
    fs.lighthouse_builders_path = str(tmp_path / "builder_definitions.yml")

    def run_version(argv: list[str]) -> str:
        if argv and argv[-1] == "--version":
            return f"Lighthouse {version}\n"
        return ""

    fs.run_version = run_version
    return fs


def _write_lighthouse_stack(
    fs: EpbsFilesystem, network: str = "mainnet", extra: str = "--builder-proposals"
) -> None:
    """Write MEV, Lighthouse BN sidecar, and VC units."""
    _write(fs, "mevboost", generate_mevboost_service(network, "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_lighthouse_bn_service(
            network, SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--builder http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_lighthouse_vc_service(
            network,
            "ep",
            "--beacon-nodes=http://127.0.0.1:5052",
            extra_parameters=extra,
        ),
    )


def test_lighthouse_prepare_writes_builder_definitions(tmp_path: Path) -> None:
    """Lighthouse v8.3.0 prepare writes builders[].url and keeps the sidecar."""
    import yaml

    fs = _lighthouse_fs(tmp_path, "v8.3.0")
    _write_lighthouse_stack(fs, network="sepolia")
    # Sibling of builder_definitions.yml so gas-limit override detection works
    (tmp_path / "validator_definitions.yml").write_text(
        '---\n- enabled: true\n  voting_public_key: "0xabc"\n  gas_limit: 30000000\n',
        encoding="utf-8",
    )
    plan = prepare(fs, apply=True)
    assert plan.support == "full"
    assert any("200000000" in w for w in plan.warnings)
    assert any("overrides" in w for w in plan.warnings)
    vc = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    assert "--builder-proposals" in vc
    assert "--gas-limit" not in vc
    assert "boost-relay.flashbots.net" not in vc
    raw = Path(fs.lighthouse_builders_path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert data["min_bid"] == 6000000
    assert data["builder_boost_factor"] == 100
    urls = [entry["url"] for entry in data["builders"]]
    assert len(urls) == 2
    assert all(entry["enabled"] is True for entry in data["builders"])
    assert all(entry["max_execution_payment"] == 0 for entry in data["builders"])
    assert "gas_limit" not in raw
    assert "18550" in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")

    again = prepare(fs, apply=True)
    assert any("nothing to change" in w for w in again.warnings)
    assert "validator" not in again.services_to_restart

    complete(fs, apply=True)
    bn = Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert "18550" not in bn
    assert Path(fs.lighthouse_builders_path).read_text(encoding="utf-8") == raw
    assert "--builder-proposals" in Path(fs.unit_path("validator")).read_text(encoding="utf-8")


def test_lighthouse_prepare_preserves_validator_configs(tmp_path: Path) -> None:
    """Existing per-validator builder config and boost factor survive prepare."""
    import yaml

    fs = _lighthouse_fs(tmp_path, "v8.3.0")
    _write_lighthouse_stack(fs)
    Path(fs.lighthouse_builders_path).write_text(
        "min_bid: 1\nbuilder_boost_factor: 120\nbuilders: []\n"
        "validator_configs:\n  '0xabc':\n    builders: []\n",
        encoding="utf-8",
    )
    plan = prepare(fs, apply=True)
    assert plan.support == "full"
    assert not any("200000000" in w for w in plan.warnings)
    data = yaml.safe_load(Path(fs.lighthouse_builders_path).read_text(encoding="utf-8"))
    assert data["builder_boost_factor"] == 120
    assert data["validator_configs"]["0xabc"]["builders"] == []
    assert data["min_bid"] == 6000000
    assert len(data["builders"]) == 2


def test_lighthouse_old_binary_prepare_is_noop(tmp_path: Path) -> None:
    """Lighthouse before v8.3.0 does not write builder_definitions.yml."""
    fs = _lighthouse_fs(tmp_path, "v8.2.3")
    _write_lighthouse_stack(fs)
    plan = prepare(fs, apply=True)
    assert plan.support == "full"
    assert not Path(fs.lighthouse_builders_path).exists()
    vc = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    assert "--builder-proposals" in vc
    assert any("no-op on this Lighthouse build" in w for w in plan.warnings)
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)

    done = complete(fs, apply=True, force=True)
    assert any("relay list" in w.lower() for w in done.warnings)
    bn = Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert "18550" not in bn
    assert "--builder-proposals" in Path(fs.unit_path("validator")).read_text(encoding="utf-8")


def test_default_write_data_chowns_validators_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare must own …/validators so Lighthouse can create logs and definitions.

    Integration failure mode: sudo mkdir created a root-owned validators dir,
    then only the YAML file was chowned — VC hit Permission denied on
    validator_definitions.yml / logs/.
    """
    import manage.epbs as epbs_mod

    validators_dir = tmp_path / "lighthouse_validator" / "validators"
    target = validators_dir / "builder_definitions.yml"
    calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):  # noqa: ANN001, ARG001
        calls.append(list(argv))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(epbs_mod.subprocess, "run", fake_run)
    _default_write_data(str(target), "builders: []\n")

    chowns = [c for c in calls if c[:3] == ["sudo", "chown", "validator:validator"]]
    owned_paths = {c[3] for c in chowns}
    assert str(validators_dir) in owned_paths, calls
    assert str(target) in owned_paths, calls
    assert any(c[:3] == ["sudo", "mkdir", "-p"] and c[3] == str(validators_dir) for c in calls)


def test_lodestar_prepare_skips_tagged_release_without_builder_urls(
    tmp_path: Path,
) -> None:
    """Tagged Lodestar without ``--builder.urls`` in ``--help`` is a prepare no-op."""
    fs = _fs(tmp_path)
    fs.run_help = lambda _argv: "Usage: lodestar validator [options]\n"
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.01", RELAYS))
    _write(
        fs,
        "consensus",
        generate_lodestar_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--builder --builder.urls http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_lodestar_vc_service(
            "mainnet",
            "ep",
            "--beaconNodes=http://127.0.0.1:5052",
            fee_parameters=f"--suggestedFeeRecipient={FEE}",
            extra_parameters="--builder",
        ),
    )
    before = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    plan = prepare(fs, apply=True)
    assert plan.applied
    assert Path(fs.unit_path("validator")).read_text(encoding="utf-8") == before
    assert any("no --builder.urls" in a.detail for a in plan.actions)
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)


def _nimbus_version(fs: EpbsFilesystem, version: str = "26.10.2") -> None:
    """Mark the Nimbus binary as *version* (v26.10.2 banner shape)."""
    fs.run_version = (
        lambda _argv: f"Nimbus validator client v{version}-0abc0dd0-stateofus\n"
    )


def _nimbus_stack(
    fs: EpbsFilesystem,
    *,
    network: str = "mainnet",
    relays: list | None = None,
    version: str = "26.10.2",
    vc_extra: str = "--payload-builder=true",
) -> None:
    """Write a Nimbus BN + VC + MEV-Boost stack.

    Args:
        fs: Test filesystem.
        network: Network name stored on the MEV-Boost unit.
        relays: Relay dicts. Defaults to one non-sidecar URL.
        version: Version text returned by ``--version``.
        vc_extra: Extra validator flags.
    """
    _nimbus_version(fs, version)
    chosen = relays if relays is not None else [RELAYS[0]]
    _write(fs, "mevboost", generate_mevboost_service(network, "0.006", chosen))
    _write(
        fs,
        "consensus",
        generate_nimbus_bn_service(
            network, JWT, "5052", "9000", "9001", "100",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            mev_parameters="--payload-builder=true --payload-builder-url=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_nimbus_vc_service(
            network,
            "ep",
            "--beacon-node=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters=vc_extra,
        ),
    )


def test_nimbus_prepare_writes_builder_urls_and_complete_keeps_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nimbus v26.10.2 prepare installs relay URLs and the bid flags."""
    monkeypatch.setenv(EPBS_BUILD_FACTOR_ENV, "maxprofit")
    fs = _fs(tmp_path)
    _nimbus_stack(fs)
    vc_before = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    assert nimbus_supports_epbs(fs, vc_before)

    dry = prepare(fs, apply=False)
    assert dry.support == "full"
    assert dry.client == "Nimbus"
    assert "--payload-builder-url=https://" not in Path(
        fs.unit_path("validator")
    ).read_text(encoding="utf-8")

    plan = prepare(fs, apply=True)
    assert plan.applied
    assert "validator" in plan.services_to_restart
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    urls = get_flag_values(vc_args, "--payload-builder-url")
    assert urls == [RELAYS[0]["url"]]
    assert "18550" not in urls[0]
    assert get_flag_value(vc_args, "--payload-builder") == "true"
    assert get_flag_value(vc_args, "--builder-min-bid") == "6000000"
    assert get_flag_value(vc_args, "--builder-max-execution-payment") == "0"
    assert get_flag_value(vc_args, "--builder-boost-factor") == "100"
    bn_text = Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert "--payload-builder-url=http://127.0.0.1:18550" in bn_text
    assert nimbus_has_builder_url(
        Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    )

    again = prepare(fs, apply=True)
    assert any("nothing to change" in w for w in again.warnings)

    done = complete(fs, apply=True)
    assert done.applied
    assert done.disable_mevboost is True
    bn_args = _args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8"))
    assert not has_flag(bn_args, "--payload-builder-url")
    assert not has_flag(bn_args, "--payload-builder")
    kept = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_values(kept, "--payload-builder-url") == [RELAYS[0]["url"]]
    assert get_flag_value(kept, "--builder-boost-factor") == "100"
    after = status(fs)
    assert "VC relays: yes" in after
    assert "already removed" in after


def test_nimbus_prepare_writes_repeated_urls_for_multiple_relays(
    tmp_path: Path,
) -> None:
    """Two relays become two ``--payload-builder-url`` flags (v26.10.2)."""
    fs = _fs(tmp_path)
    _nimbus_stack(fs, relays=RELAYS)
    plan = prepare(fs, apply=True)
    assert plan.support == "full"
    assert plan.applied
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    urls = get_flag_values(vc_args, "--payload-builder-url")
    assert urls == [RELAYS[0]["url"], RELAYS[1]["url"]]
    assert all("18550" not in url for url in urls)
    assert nimbus_has_builder_url(
        Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    )
    done = complete(fs, apply=True)
    assert done.applied
    kept = get_flag_values(
        _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8")),
        "--payload-builder-url",
    )
    assert kept == [RELAYS[0]["url"], RELAYS[1]["url"]]


def test_nimbus_prepare_skips_binary_older_than_v26_10_2(tmp_path: Path) -> None:
    """Nimbus before 26.10.2 is a prepare no-op; complete stays refused."""
    fs = _fs(tmp_path)
    _nimbus_stack(fs, version="26.10.1")
    before = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    assert not nimbus_supports_epbs(fs, before)
    plan = prepare(fs, apply=True)
    assert plan.support == "full"
    assert Path(fs.unit_path("validator")).read_text(encoding="utf-8") == before
    assert any(NIMBUS_EPBS_MIN_VERSION in a.detail for a in plan.actions)
    st = status(fs)
    assert "older than" in st
    assert "Prepare is a no-op" in st
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)

    complete(fs, apply=True, force=True)
    bn_args = _args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8"))
    assert not has_flag(bn_args, "--payload-builder-url")
    assert not has_flag(bn_args, "--payload-builder")
    vc = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    assert "--payload-builder=true" in vc
    assert "--payload-builder-url=" not in vc


def test_nimbus_sepolia_sets_explicit_200m_gas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sepolia proposer preferences need an explicit 200M gas limit."""
    monkeypatch.delenv(EPBS_BUILD_FACTOR_ENV, raising=False)
    fs = _fs(tmp_path)
    _nimbus_stack(fs, network="sepolia")
    prepare(fs, apply=True)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(vc_args, NIMBUS_SUGGESTED_GAS_FLAG) == "200000000"
    assert not has_flag(vc_args, "--builder-boost-factor")
    assert get_flag_value(vc_args, "--builder-min-bid") == "6000000"

    preset = _fs(tmp_path / "preset")
    _nimbus_stack(
        preset,
        network="sepolia",
        vc_extra="--payload-builder=true --suggested-gas-limit=45000000",
    )
    prepare(preset, apply=True)
    preset_args = _args(Path(preset.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(preset_args, "--suggested-gas-limit") == "45000000"


def test_nimbus_rejects_hostname_less_builder_url(tmp_path: Path) -> None:
    """get_default_auth_data drops a URL with an empty hostname."""
    fs = _fs(tmp_path)
    bad = [{"name": "bad", "url": "https://:8080"}]
    _nimbus_stack(fs, relays=bad)
    with pytest.raises(EpbsError, match="no hostname"):
        prepare(fs, apply=True)


def _nimbus_synthetic_relays(count: int, *, url_byte_len: int = 0) -> list[dict]:
    """Build *count* unique relay dicts for Nimbus limit tests.

    Args:
        count: Number of distinct builder URLs.
        url_byte_len: If >0, pad each URL to exactly this many UTF-8 bytes
            (ASCII). Host remains ``builderN.example``.

    Returns:
        Relay dicts with ``name`` / ``url`` keys.
    """
    relays: list[dict] = []
    for i in range(count):
        host = f"builder{i}.example"
        base = f"https://{host}"
        if url_byte_len:
            if url_byte_len < len(base):
                raise AssertionError(
                    f"url_byte_len {url_byte_len} shorter than base {base!r}"
                )
            # Pad path so total UTF-8 length matches (ASCII = byte length).
            pad = "x" * (url_byte_len - len(base) - 1)
            url = f"{base}/{pad}"
            assert len(url.encode("utf-8")) == url_byte_len
        else:
            url = base
        relays.append({"name": f"b{i}", "url": url})
    return relays


def test_nimbus_prepare_accepts_max_builder_entries(
    tmp_path: Path,
) -> None:
    """Exactly MAX_BUILDER_ENTRIES URLs are written (Nimbus limit is inclusive)."""
    fs = _fs(tmp_path)
    relays = _nimbus_synthetic_relays(NIMBUS_MAX_BUILDER_ENTRIES)
    _nimbus_stack(fs, relays=relays)
    plan = prepare(fs, apply=True)
    assert plan.applied
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    urls = get_flag_values(vc_args, "--payload-builder-url")
    assert len(urls) == NIMBUS_MAX_BUILDER_ENTRIES
    assert urls == [r["url"] for r in relays]


def test_nimbus_prepare_rejects_over_max_builder_entries(
    tmp_path: Path,
) -> None:
    """More than MAX_BUILDER_ENTRIES would be silently truncated by Nimbus."""
    fs = _fs(tmp_path)
    relays = _nimbus_synthetic_relays(NIMBUS_MAX_BUILDER_ENTRIES + 1)
    _nimbus_stack(fs, relays=relays)
    before = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    with pytest.raises(
        EpbsError,
        match=rf"at most {NIMBUS_MAX_BUILDER_ENTRIES}.*MAX_BUILDER_ENTRIES",
    ):
        prepare(fs, apply=True)
    assert Path(fs.unit_path("validator")).read_text(encoding="utf-8") == before


def test_nimbus_prepare_accepts_max_builder_url_size(
    tmp_path: Path,
) -> None:
    """A URL of exactly MAX_BUILDER_URL_SIZE bytes is kept (Nimbus uses ``>``)."""
    fs = _fs(tmp_path)
    relays = _nimbus_synthetic_relays(1, url_byte_len=NIMBUS_MAX_BUILDER_URL_SIZE)
    _nimbus_stack(fs, relays=relays)
    plan = prepare(fs, apply=True)
    assert plan.applied
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_values(vc_args, "--payload-builder-url") == [relays[0]["url"]]
    assert len(relays[0]["url"].encode("utf-8")) == NIMBUS_MAX_BUILDER_URL_SIZE


def test_nimbus_prepare_rejects_over_max_builder_url_size(
    tmp_path: Path,
) -> None:
    """URLs longer than MAX_BUILDER_URL_SIZE are skipped silently by Nimbus."""
    fs = _fs(tmp_path)
    relays = _nimbus_synthetic_relays(
        1, url_byte_len=NIMBUS_MAX_BUILDER_URL_SIZE + 1
    )
    _nimbus_stack(fs, relays=relays)
    before = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    with pytest.raises(
        EpbsError,
        match=rf"exceeds {NIMBUS_MAX_BUILDER_URL_SIZE} bytes.*MAX_BUILDER_URL_SIZE",
    ):
        prepare(fs, apply=True)
    assert Path(fs.unit_path("validator")).read_text(encoding="utf-8") == before


def test_nimbus_invalid_build_factor_still_writes_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad boost-factor token warns and does not block the builder URL."""
    fs = _fs(tmp_path)
    _nimbus_stack(fs)
    monkeypatch.setenv(EPBS_BUILD_FACTOR_ENV, "nope")
    plan = prepare(fs, apply=True)
    assert any("invalid" in w for w in plan.warnings)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(vc_args, "--payload-builder-url") == RELAYS[0]["url"]
    assert not has_flag(vc_args, "--builder-boost-factor")


def test_nimbus_prepare_keeps_existing_boost_factor_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator-set boost factor stays when EPBS_BUILD_FACTOR is unset."""
    monkeypatch.delenv(EPBS_BUILD_FACTOR_ENV, raising=False)
    fs = _fs(tmp_path)
    _nimbus_stack(fs, vc_extra="--payload-builder=true --builder-boost-factor=90")
    prepare(fs, apply=True)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(vc_args, "--builder-boost-factor") == "90"
    assert get_flag_value(vc_args, "--payload-builder-url") == RELAYS[0]["url"]

    dropped = _fs(tmp_path / "drop-zero")
    _nimbus_stack(dropped, vc_extra="--payload-builder=true --builder-boost-factor=0")
    prepare(dropped, apply=True)
    dropped_args = _args(Path(dropped.unit_path("validator")).read_text(encoding="utf-8"))
    assert not has_flag(dropped_args, "--builder-boost-factor")
    assert get_flag_value(dropped_args, "--payload-builder-url") == RELAYS[0]["url"]


def test_nimbus_charon_prepare_skips_vc_builder_url(tmp_path: Path) -> None:
    """Charon + Nimbus: prepare must not write a VC builder URL."""
    fs = _fs(tmp_path)
    _nimbus_version(fs)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", [RELAYS[0]]))
    _write(
        fs,
        "consensus",
        generate_nimbus_bn_service(
            "mainnet", JWT, "5052", "9000", "9001", "100",
            mev_parameters="--payload-builder=true --payload-builder-url=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "charon",
        generate_charon_service("mainnet", "http://127.0.0.1:5052", builder_api=True),
    )
    _write(
        fs,
        "validator",
        generate_nimbus_vc_service(
            "mainnet",
            "ep",
            "--beacon-node=http://127.0.0.1:3600",
            extra_parameters="--payload-builder=true --distributed",
        ),
    )
    prep = prepare(fs, apply=True)
    assert any("Charon DVT owns builder path" in a.detail for a in prep.actions)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert not has_flag(vc_args, "--payload-builder-url")
    done = complete(fs, apply=True)
    assert done.applied
    bn_args = _args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8"))
    assert not has_flag(bn_args, "--payload-builder-url")
    assert not has_flag(bn_args, "--payload-builder")


def test_nimbus_import_applies_builder_urls(tmp_path: Path) -> None:
    """Import writes Nimbus builder URLs from a migration file."""
    mev_fs = _fs(tmp_path / "mev")
    _write(mev_fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    out = tmp_path / "mig.ethpillar.epbs-migration"
    export_migration(mev_fs, output=str(out), hostname="bn")

    vc_fs = _fs(tmp_path / "vc")
    _nimbus_version(vc_fs)
    _write(
        vc_fs,
        "validator",
        generate_nimbus_vc_service(
            "mainnet",
            "ep",
            "--beacon-node=http://10.0.0.2:5052",
            extra_parameters="--payload-builder=true",
        ),
    )
    plan = import_migration(str(out), vc_fs, apply=True)
    assert plan.applied
    assert plan.client == "Nimbus"
    vc_args = _args(Path(vc_fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_values(vc_args, "--payload-builder-url") == [
        RELAYS[0]["url"],
        RELAYS[1]["url"],
    ]
    assert get_flag_value(vc_args, "--builder-max-execution-payment") == "0"


def _teku_stack(fs: EpbsFilesystem, *, bn_extra: str = "", vc_extra: str = "") -> None:
    """Write a Teku BN + VC + MEV-Boost stack and mark the binary as v26.11.0."""
    fs.run_version = lambda _argv: "teku/v26.11.0/linux-x86_64/openjdk-java-25\n"
    mev = (
        "--validators-builder-registration-default-enabled=true "
        "--builder-endpoint=http://127.0.0.1:18550"
    )
    if bn_extra:
        mev = f"{mev} {bn_extra}"
    vc_flags = "--validators-builder-registration-default-enabled=true"
    if vc_extra:
        vc_flags = f"{vc_flags} {vc_extra}"
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_teku_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "100",
            fee_parameters=f"--validators-proposer-default-fee-recipient={FEE}",
            mev_parameters=mev,
        ),
    )
    _write(
        fs,
        "validator",
        generate_teku_vc_service(
            "mainnet",
            "ep",
            "--beacon-node-api-endpoint=http://127.0.0.1:5052",
            fee_parameters=f"--validators-proposer-default-fee-recipient={FEE}",
            extra_parameters=vc_flags,
        ),
    )


def test_teku_builder_boost_factor_mapping() -> None:
    """eth-docker build-factor tokens become Teku ``--builder-boost-factor``."""
    assert teku_builder_boost_factor("") == (None, None)
    assert teku_builder_boost_factor("   ") == (None, None)
    assert teku_builder_boost_factor("0") == ("0", None)
    assert teku_builder_boost_factor("000") == ("0", None)
    assert teku_builder_boost_factor("local") == ("0", None)
    assert teku_builder_boost_factor("always") == (TEKU_BOOST_FACTOR_UINT64_MAX, None)
    assert teku_builder_boost_factor("BUILDER_ALWAYS") == (TEKU_BOOST_FACTOR_UINT64_MAX, None)
    assert teku_builder_boost_factor("maxprofit") == ("100", None)
    assert teku_builder_boost_factor("100") == ("100", None)
    assert teku_builder_boost_factor("0100") == ("100", None)
    assert teku_builder_boost_factor("90") == ("90", None)
    capped, warning = teku_builder_boost_factor("18446744073709551616")
    assert capped == TEKU_BOOST_FACTOR_UINT64_MAX
    assert warning and "capping" in warning
    omitted, invalid = teku_builder_boost_factor("nope")
    assert omitted is None
    assert invalid and "invalid" in invalid
    # Keywords are case-sensitive, matching eth-docker.
    omitted, invalid = teku_builder_boost_factor("Always")
    assert omitted is None
    assert invalid


def test_teku_prepare_writes_xbuilder_flags_and_complete_keeps_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Teku v26.11.0 prepare installs builder URLs, min-bid, and boost factor."""
    fs = _fs(tmp_path)
    _teku_stack(fs)
    monkeypatch.setenv(TEKU_EPBS_BUILD_FACTOR_ENV, "maxprofit")
    assert teku_supports_epbs(fs, Path(fs.unit_path("validator")).read_text(encoding="utf-8"))

    dry = prepare(fs, apply=False)
    assert dry.support == "full"
    assert dry.client == "Teku"
    assert not any("--builder-urls" in line for line in Path(fs.unit_path("validator")).read_text(encoding="utf-8").splitlines())

    plan = prepare(fs, apply=True)
    assert plan.applied
    assert "validator" in plan.services_to_restart
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    urls = get_flag_value(vc_args, "--builder-urls")
    assert "boost-relay.flashbots.net" in urls
    assert "relay.ultrasound.money" in urls
    assert "18550" not in urls
    assert "," in urls and " " not in urls.split("=", 1)[-1]
    assert get_flag_value(vc_args, "--builder-min-bid") == "6000000"
    assert get_flag_value(vc_args, "--p2p-builder-min-bid") == "6000000"
    assert get_flag_value(vc_args, "--builder-boost-factor") == "100"
    assert get_flag_value(vc_args, "--p2p-builder-boost-factor") == "100"
    assert not has_flag(vc_args, "--Xbuilder-urls")
    assert has_flag(vc_args, "--validators-builder-registration-default-enabled")
    bn_text = Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert "--builder-endpoint=http://127.0.0.1:18550" in bn_text

    again = prepare(fs, apply=True)
    assert any("nothing to change" in w for w in again.warnings)

    done = complete(fs, apply=True)
    assert done.applied
    assert done.disable_mevboost is True
    bn_args = _args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8"))
    assert not has_flag(bn_args, "--builder-endpoint")
    kept = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert "boost-relay.flashbots.net" in get_flag_value(kept, "--builder-urls")
    assert get_flag_value(kept, "--builder-boost-factor") == "100"
    assert get_flag_value(kept, "--p2p-builder-boost-factor") == "100"
    assert has_flag(kept, "--validators-builder-registration-default-enabled")
    after = status(fs)
    assert "VC relays: yes" in after
    assert "already removed" in after


def test_teku_prepare_reads_bn_compare_factor_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BN ``BUILDER_ALWAYS`` becomes uint64 max; unset factor omits the flag."""
    monkeypatch.delenv(TEKU_EPBS_BUILD_FACTOR_ENV, raising=False)
    monkeypatch.delenv(EPBS_BUILD_FACTOR_ENV, raising=False)
    fs = _fs(tmp_path)
    _teku_stack(fs, bn_extra="--builder-bid-compare-factor=BUILDER_ALWAYS")
    prepare(fs, apply=True)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(vc_args, "--builder-boost-factor") == TEKU_BOOST_FACTOR_UINT64_MAX
    assert get_flag_value(vc_args, "--p2p-builder-boost-factor") == TEKU_BOOST_FACTOR_UINT64_MAX

    plain = _fs(tmp_path / "plain")
    _teku_stack(plain)
    prepare(plain, apply=True)
    plain_args = _args(Path(plain.unit_path("validator")).read_text(encoding="utf-8"))
    assert not has_flag(plain_args, "--builder-boost-factor")
    assert not has_flag(plain_args, "--p2p-builder-boost-factor")
    assert has_flag(plain_args, "--builder-urls")
    assert get_flag_value(plain_args, "--builder-min-bid") == "6000000"
    assert get_flag_value(plain_args, "--p2p-builder-min-bid") == "6000000"


def test_teku_prepare_skips_binary_older_than_xbuilder_release(tmp_path: Path) -> None:
    """Teku 26.10.1 (last --Xbuilder-* release) is a prepare no-op."""
    fs = _fs(tmp_path)
    fs.run_version = lambda _argv: "teku/v26.10.1/linux-x86_64/openjdk-java-25\n"
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_teku_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "100",
            mev_parameters="--builder-endpoint=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_teku_vc_service(
            "mainnet",
            "ep",
            "--beacon-node-api-endpoint=http://127.0.0.1:5052",
            extra_parameters="--validators-builder-registration-default-enabled=true",
        ),
    )
    before = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    assert not teku_supports_epbs(fs, before)
    plan = prepare(fs, apply=True)
    assert plan.support == "full"
    assert plan.applied
    assert Path(fs.unit_path("validator")).read_text(encoding="utf-8") == before
    assert any(TEKU_EPBS_MIN_VERSION in a.detail for a in plan.actions)
    st = status(fs)
    assert "older than" in st
    assert "Prepare is a no-op" in st
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)


def test_teku_invalid_build_factor_still_writes_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad boost-factor token warns and does not block builder URLs."""
    fs = _fs(tmp_path)
    _teku_stack(fs)
    monkeypatch.setenv(TEKU_EPBS_BUILD_FACTOR_ENV, "nope")
    plan = prepare(fs, apply=True)
    assert any("invalid" in w for w in plan.warnings)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert has_flag(vc_args, "--builder-urls")
    assert not has_flag(vc_args, "--builder-boost-factor")
    assert not has_flag(vc_args, "--p2p-builder-boost-factor")
    assert not has_flag(vc_args, "--Xbuilder-urls")


def test_teku_prepare_keeps_existing_boost_factor_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator-set boost factor stays when no new source is configured."""
    monkeypatch.delenv(TEKU_EPBS_BUILD_FACTOR_ENV, raising=False)
    monkeypatch.delenv(EPBS_BUILD_FACTOR_ENV, raising=False)
    fs = _fs(tmp_path)
    _teku_stack(fs, vc_extra="--Xbuilder-boost-factor=90")
    prepare(fs, apply=True)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(vc_args, "--builder-boost-factor") == "90"
    assert get_flag_value(vc_args, "--p2p-builder-boost-factor") == "90"
    assert not has_flag(vc_args, "--Xbuilder-boost-factor")
    assert has_flag(vc_args, "--builder-urls")
    assert not has_flag(vc_args, "--Xbuilder-urls")


def test_promote_teku_flags_rewrites_only_on_26_11(tmp_path: Path) -> None:
    """Upgrade rewrite drops --Xbuilder-* on 26.11.0 and leaves 26.10.1 alone."""
    old = _fs(tmp_path / "old")
    old.run_version = lambda _argv: "teku/v26.10.1/linux-x86_64/openjdk-java-25\n"
    _write(
        old,
        "validator",
        generate_teku_vc_service(
            "mainnet",
            "ep",
            "--beacon-node-api-endpoint=http://127.0.0.1:5052",
            extra_parameters=(
                "--Xbuilder-urls=https://boost-relay.flashbots.net "
                "--Xbuilder-min-bid=6000000 "
                "--Xbuilder-boost-factor=90 "
                "--Xbuilder-max-execution-payment=0"
            ),
        ),
    )
    assert promote_installed_teku_flags(old, apply=True) is False
    kept = _args(Path(old.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(kept, "--Xbuilder-boost-factor") == "90"
    assert not has_flag(kept, "--builder-urls")

    new = _fs(tmp_path / "new")
    new.run_version = lambda _argv: "teku/v26.11.0/linux-x86_64/openjdk-java-25\n"
    _write(
        new,
        "validator",
        generate_teku_vc_service(
            "mainnet",
            "ep",
            "--beacon-node-api-endpoint=http://127.0.0.1:5052",
            extra_parameters=(
                "--Xbuilder-urls=https://boost-relay.flashbots.net "
                "--Xbuilder-min-bid=6000000 "
                "--Xbuilder-boost-factor=90 "
                "--Xbuilder-max-execution-payment=0"
            ),
        ),
    )
    assert promote_installed_teku_flags(new, apply=False) is True
    assert has_flag(
        _args(Path(new.unit_path("validator")).read_text(encoding="utf-8")),
        "--Xbuilder-urls",
    )
    assert promote_installed_teku_flags(new, apply=True) is True
    args = _args(Path(new.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(args, "--builder-urls") == "https://boost-relay.flashbots.net"
    assert get_flag_value(args, "--builder-min-bid") == "6000000"
    assert get_flag_value(args, "--p2p-builder-min-bid") == "6000000"
    assert get_flag_value(args, "--builder-boost-factor") == "90"
    assert get_flag_value(args, "--p2p-builder-boost-factor") == "90"
    assert get_flag_value(args, "--builder-max-execution-payment") == "0"
    assert not any(has_flag(args, old_name) for old_name, _news in (
        ("--Xbuilder-urls", None),
        ("--Xbuilder-min-bid", None),
        ("--Xbuilder-boost-factor", None),
        ("--Xbuilder-max-execution-payment", None),
    ))
    assert promote_installed_teku_flags(new, apply=True) is False


def test_teku_charon_prepare_skips_vc_builder_urls(tmp_path: Path) -> None:
    """Charon + Teku: prepare must not write --builder-urls on the signer VC."""
    fs = _fs(tmp_path)
    fs.run_version = lambda _argv: "teku/v26.11.0/linux-x86_64/openjdk-java-25\n"
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_teku_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "100",
            mev_parameters="--builder-endpoint=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "charon",
        generate_charon_service("mainnet", "http://127.0.0.1:5052", builder_api=True),
    )
    _write(
        fs,
        "validator",
        generate_teku_vc_service(
            "mainnet",
            "ep",
            "--beacon-node-api-endpoint=http://127.0.0.1:3600",
            extra_parameters="--validators-builder-registration-default-enabled=true",
        ),
    )
    prep = prepare(fs, apply=True)
    assert any("Charon DVT owns builder path" in a.detail for a in prep.actions)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert not has_flag(vc_args, "--builder-urls")
    assert not has_flag(vc_args, "--Xbuilder-urls")
    done = complete(fs, apply=True)
    assert done.applied
    bn_args = _args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8"))
    assert not has_flag(bn_args, "--builder-endpoint")


def test_teku_import_applies_xbuilder_urls(tmp_path: Path) -> None:
    """Import writes Teku --builder-urls from a migration file."""
    mev_fs = _fs(tmp_path / "mev")
    _write(mev_fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    out = tmp_path / "mig.ethpillar.epbs-migration"
    export_migration(mev_fs, output=str(out), hostname="bn")

    vc_fs = _fs(tmp_path / "vc")
    vc_fs.run_version = lambda _argv: "teku/v26.11.0/linux-x86_64/openjdk-java-25\n"
    _write(
        vc_fs,
        "validator",
        generate_teku_vc_service(
            "mainnet",
            "ep",
            "--beacon-node-api-endpoint=http://10.0.0.1:5052",
            fee_parameters=f"--validators-proposer-default-fee-recipient={FEE}",
        ),
    )
    plan = import_migration(str(out), vc_fs, apply=True)
    assert plan.applied
    assert plan.support == "full"
    args = _args(Path(vc_fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert "boost-relay.flashbots.net" in get_flag_value(args, "--builder-urls")
    assert get_flag_value(args, "--builder-min-bid") == "6000000"
    assert get_flag_value(args, "--p2p-builder-min-bid") == "6000000"
    assert not has_flag(args, "--Xbuilder-urls")


def test_teku_env_overrides_bn_compare_factor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``TEKU_EPBS_BUILD_FACTOR`` wins over the beacon-node compare factor."""
    fs = _fs(tmp_path)
    _teku_stack(fs, bn_extra="--builder-bid-compare-factor=50")
    monkeypatch.setenv(TEKU_EPBS_BUILD_FACTOR_ENV, "local")
    prepare(fs, apply=True)
    vc_args = _args(Path(fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert get_flag_value(vc_args, "--builder-boost-factor") == "0"
    assert get_flag_value(vc_args, "--p2p-builder-boost-factor") == "0"


def test_grandine_integrated_placeholder_and_complete(tmp_path: Path) -> None:
    """Integrated Grandine prepare is a no-op; complete requires ``--force``."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_grandine_bn_service(
            "mainnet",
            SYNC,
            JWT,
            "5052",
            "9000",
            "9001",
            "100",
            mev_parameters="--builder-url=http://127.0.0.1:18550",
            is_integrated_vc=True,
        ),
    )
    plan = prepare(fs, apply=True)
    assert plan.client == "Grandine"
    assert plan.support == "placeholder"
    assert "18550" in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)

    complete(fs, apply=True, force=True)
    bn = Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert "18550" not in bn
    assert "keystore-dir" in bn


def test_prepare_requires_mevboost(tmp_path: Path) -> None:
    """Prepare fails when ``mevboost.service`` is missing."""
    fs = _fs(tmp_path)
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet", "ep", "--beacon-rest-api-provider=http://127.0.0.1:5052"
        ),
    )
    with pytest.raises(EpbsError, match="mevboost.service"):
        prepare(fs, apply=False)


def test_strip_bn_keeps_non_sidecar_builder_url() -> None:
    """BN builder URLs that are not local MEV-Boost survive complete."""
    unit = generate_lighthouse_bn_service(
        "mainnet",
        SYNC,
        JWT,
        "5052",
        "9000",
        "9001",
        "100",
        mev_parameters="--builder https://boost-relay.flashbots.net",
    )
    stripped = strip_bn_sidecar(unit, "Lighthouse")
    assert "boost-relay.flashbots.net" in stripped


def test_cli_prepare_prysm(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``python -m manage.epbs prepare --apply --json`` writes Prysm settings."""
    from manage.epbs import main

    systemd = tmp_path / "systemd"
    systemd.mkdir()
    settings = tmp_path / "proposer-settings.json"
    (systemd / "mevboost.service").write_text(generate_mevboost_service("mainnet", "0.006", RELAYS))
    (systemd / "consensus.service").write_text(
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        )
    )
    (systemd / "validator.service").write_text(
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        )
    )
    rc = main([
        "prepare",
        "--apply",
        "--json",
        "--systemd-dir", str(systemd),
        "--prysm-settings", str(settings),
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["client"] == "Prysm"
    assert out["applied"] is True
    assert settings.is_file()


def test_status_reports_prysm(tmp_path: Path) -> None:
    """Status lists Prysm support, relay count, and BN sidecar presence."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )
    text = status(fs)
    assert "Prysm" in text
    assert "full" in text
    assert "relays=2" in text
    assert "BN sidecar flags: present" in text
    assert "VC relays: no" in text
    assert "Complete: refused until Prepare writes a VC relay list" in text


def test_complete_refused_on_prysm_without_prepare(tmp_path: Path) -> None:
    """Prysm complete without a relay list is refused (TUI and CLI)."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )
    with pytest.raises(EpbsError, match="Complete refused") as exc:
        complete(fs, apply=False)
    assert str(exc.value) == COMPLETE_REFUSED
    assert "18550" in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")


def test_export_import_round_trip_prysm(tmp_path: Path) -> None:
    """Export on MEV host; import applies Prysm relays on VC-only host."""
    mev_fs = _fs(tmp_path / "mev")
    _write(mev_fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        mev_fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    out = tmp_path / "bn-test.ethpillar.epbs-migration"
    path, payload = export_migration(mev_fs, output=str(out), hostname="bn-test")
    assert path == str(out)
    assert out.is_file()
    assert payload["format"] == MIGRATION_FORMAT
    assert payload["version"] == MIGRATION_VERSION
    assert payload["hostname"] == "bn-test"
    assert len(payload["relays"]) == 2
    assert payload["min_bid"] == "0.006"

    loaded = load_migration_file(str(out))
    assert loaded.urls == payload["relays"]
    assert loaded.min_bid == "0.006"

    vc_fs = _fs(tmp_path / "vc")
    _write(
        vc_fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://10.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )
    plan = import_migration(str(out), vc_fs, apply=True)
    assert plan.command == "import"
    assert plan.applied
    settings = json.loads(Path(vc_fs.prysm_settings_path).read_text(encoding="utf-8"))
    builder = settings["default_config"]["builder"]
    assert builder["builders"] == [{"url": r["url"]} for r in RELAYS]
    assert builder["min_bid"] == "6000000"
    assert "relays" not in builder
    vc_text = Path(vc_fs.unit_path("validator")).read_text(encoding="utf-8")
    assert not has_flag(_args(vc_text), "--enable-builder")
    assert has_flag(_args(vc_text), "--proposer-settings-file")
    assert "validator" in plan.services_to_restart


def test_import_lodestar_applies_builder_urls(tmp_path: Path) -> None:
    """Import writes Lodestar --builder.urls from a migration file."""
    mev_fs = _fs(tmp_path / "mev")
    _write(mev_fs, "mevboost", generate_mevboost_service("mainnet", "0.01", RELAYS))
    out = tmp_path / "mig.ethpillar.epbs-migration"
    export_migration(mev_fs, output=str(out), hostname="x")

    vc_fs = _fs(tmp_path / "vc")
    vc_fs.run_help = lambda _argv: "--builder.urls --builder.minBid\n"
    _write(
        vc_fs,
        "validator",
        generate_lodestar_vc_service(
            "mainnet",
            "ep",
            "--beaconNodes=http://10.0.0.1:5052",
            fee_parameters=f"--suggestedFeeRecipient={FEE}",
            extra_parameters="--builder",
        ),
    )
    plan = import_migration(str(out), vc_fs, apply=True)
    assert plan.applied
    args = _args(Path(vc_fs.unit_path("validator")).read_text(encoding="utf-8"))
    assert has_flag(args, "--builder.urls")
    assert get_flag_value(args, "--builder.minBid") == "10000000"


def test_import_refused_when_charon_lacks_epbs_support(tmp_path: Path) -> None:
    """Import refuses on Charon hosts until charon_epbs_supported is True."""
    from manage.epbs import CHARON_IMPORT_REFUSED, charon_epbs_supported

    assert charon_epbs_supported() is False

    mev_fs = _fs(tmp_path / "mev")
    _write(mev_fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    out = tmp_path / "dv.ethpillar.epbs-migration"
    export_migration(mev_fs, output=str(out), hostname="bn")

    vc_fs = _fs(tmp_path / "vc")
    _write(
        vc_fs,
        "charon",
        generate_charon_service("mainnet", "http://10.0.0.1:5052", builder_api=True),
    )
    _write(
        vc_fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:3600",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )
    with pytest.raises(EpbsError, match="Import refused") as exc:
        import_migration(str(out), vc_fs, apply=False)
    assert str(exc.value) == CHARON_IMPORT_REFUSED
    assert not Path(vc_fs.prysm_settings_path).exists()


def test_complete_remote_vc_prepared_without_local_vc(tmp_path: Path) -> None:
    """MEV/CC host complete with --remote-vc-prepared strips BN and stops MEV."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_prysm_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--http-mev-relay=http://127.0.0.1:18550",
        ),
    )
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)
    done = complete(fs, apply=True, remote_vc_prepared=True)
    assert done.applied
    assert done.disable_mevboost
    assert fs.mevboost_disabled is True  # type: ignore[attr-defined]
    assert "18550" not in Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert any("Remote VC prepared" in w for w in done.warnings)
    st = status(fs)
    assert "Split LXC (no local VC)" in st


def test_complete_charon_only_host_strips_builder_api(tmp_path: Path) -> None:
    """VC/Charon host complete strips --builder-api without BN/MEV."""
    fs = _fs(tmp_path)
    _write(
        fs,
        "charon",
        generate_charon_service("mainnet", "http://10.0.0.1:5052", builder_api=True),
    )
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://127.0.0.1:3600",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )
    done = complete(fs, apply=True)
    assert done.applied
    assert done.disable_mevboost is False
    assert not charon_has_builder_api(
        Path(fs.unit_path("charon")).read_text(encoding="utf-8")
    )
    assert "charon" in done.services_to_restart
    st = status(fs)
    assert "Split LXC (VC/Charon host)" in st


def test_complete_solo_vc_host_is_noop(tmp_path: Path) -> None:
    """Solo VC host complete reports nothing local after import."""
    fs = _fs(tmp_path)
    _write(
        fs,
        "validator",
        generate_prysm_vc_service(
            "mainnet",
            "ep",
            "--beacon-rest-api-provider=http://10.0.0.1:5052",
            fee_parameters=f"--suggested-fee-recipient={FEE}",
            extra_parameters="--enable-builder",
        ),
    )
    done = complete(fs, apply=False)
    assert done.disable_mevboost is False
    assert any("nothing local to complete" in a.detail for a in done.actions)


def test_load_migration_file_rejects_bad_format(tmp_path: Path) -> None:
    """Unknown format / empty relays raise EpbsError."""
    bad = tmp_path / "bad.ethpillar.epbs-migration"
    bad.write_text(json.dumps({"format": "nope", "version": 1, "relays": ["x"]}), encoding="utf-8")
    with pytest.raises(EpbsError, match="Unknown migration format"):
        load_migration_file(str(bad))
    empty = tmp_path / "empty.ethpillar.epbs-migration"
    empty.write_text(
        json.dumps({"format": MIGRATION_FORMAT, "version": MIGRATION_VERSION, "relays": []}),
        encoding="utf-8",
    )
    with pytest.raises(EpbsError, match="no relays"):
        load_migration_file(str(empty))
