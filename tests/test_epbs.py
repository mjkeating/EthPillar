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
    parse_mevboost_relays,
    prepare,
    prysm_has_builder_list,
    status,
    strip_bn_sidecar,
    strip_charon_builder_api,
    support_level,
)
from manage.service_parse import get_flag_value, has_flag, normalize_cli_args, parse_unit

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
    assert support_level("Erigon-Caplin") == "full"
    for client in ("Lighthouse", "Teku", "Nimbus", "Grandine", ""):
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


def test_prysm_sepolia_gas_limit_is_documented_not_written(tmp_path: Path) -> None:
    """Sepolia prepare warns about 200M and does not write gas_limit."""
    fs = _fs(tmp_path)
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


def test_lighthouse_prepare_is_placeholder_complete_strips_bn(tmp_path: Path) -> None:
    """Lighthouse prepare is a no-op; complete requires ``--force`` to strip BN sidecar."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(
        fs,
        "consensus",
        generate_lighthouse_bn_service(
            "mainnet", SYNC, JWT, "5052", "9000", "9001", "100",
            mev_parameters="--builder http://127.0.0.1:18550",
        ),
    )
    _write(
        fs,
        "validator",
        generate_lighthouse_vc_service(
            "mainnet",
            "ep",
            "--beacon-nodes=http://127.0.0.1:5052",
            extra_parameters="--builder-proposals",
        ),
    )
    plan = prepare(fs, apply=True)
    assert plan.support == "placeholder"
    vc = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    assert "--builder-proposals" in vc
    assert "boost-relay.flashbots.net" not in vc
    assert any("no-op on this client" in w for w in plan.warnings)
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)

    done = complete(fs, apply=True, force=True)
    assert any("relay list" in w.lower() for w in done.warnings)
    bn = Path(fs.unit_path("consensus")).read_text(encoding="utf-8")
    assert "18550" not in bn
    assert "--builder-proposals" in Path(fs.unit_path("validator")).read_text(encoding="utf-8")


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


@pytest.mark.parametrize(
    "client,bn_unit,vc_unit,sidecar_token",
    [
        (
            "Teku",
            generate_teku_bn_service(
                "mainnet", SYNC, JWT, "5052", "9000", "100",
                fee_parameters=f"--validators-proposer-default-fee-recipient={FEE}",
                mev_parameters="--validators-builder-registration-default-enabled=true --builder-endpoint=http://127.0.0.1:18550",
            ),
            generate_teku_vc_service(
                "mainnet",
                "ep",
                "--beacon-node-api-endpoint=http://127.0.0.1:5052",
                fee_parameters=f"--validators-proposer-default-fee-recipient={FEE}",
                extra_parameters="--validators-builder-registration-default-enabled=true",
            ),
            "--builder-endpoint",
        ),
        (
            "Nimbus",
            generate_nimbus_bn_service(
                "mainnet", JWT, "5052", "9000", "9001", "100",
                mev_parameters="--payload-builder=true --payload-builder-url=http://127.0.0.1:18550",
            ),
            generate_nimbus_vc_service(
                "mainnet",
                "ep",
                "--beacon-node=http://127.0.0.1:5052",
                extra_parameters="--payload-builder=true",
            ),
            "--payload-builder-url",
        ),
    ],
    ids=["Teku", "Nimbus"],
)
def test_placeholder_clients_complete_strips_sidecar(
    tmp_path: Path,
    client: str,
    bn_unit: str,
    vc_unit: str,
    sidecar_token: str,
) -> None:
    """Teku/Nimbus prepare is a no-op; complete is refused without ``--force``."""
    fs = _fs(tmp_path)
    _write(fs, "mevboost", generate_mevboost_service("mainnet", "0.006", RELAYS))
    _write(fs, "consensus", bn_unit)
    _write(fs, "validator", vc_unit)
    plan = prepare(fs, apply=True)
    assert plan.support == "placeholder"
    assert client.lower() in plan.client.lower() or plan.client == client
    with pytest.raises(EpbsError, match="Complete refused"):
        complete(fs, apply=False)

    complete(fs, apply=True, force=True)
    bn_args = _args(Path(fs.unit_path("consensus")).read_text(encoding="utf-8"))
    assert not has_flag(bn_args, sidecar_token)
    # VC builder-enable flags stay
    vc = Path(fs.unit_path("validator")).read_text(encoding="utf-8")
    if client == "Teku":
        assert "validators-builder-registration-default-enabled" in vc
    if client == "Nimbus":
        assert "payload-builder=true" in vc or "--payload-builder=true" in vc


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
