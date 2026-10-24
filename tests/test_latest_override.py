"""Unit tests for Upgrade-case LATEST→RC override (integration-test scoped)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import deploy.common as common
from tests.integration.latest_override import (
    clear_override,
    forced_rc_tag,
    install_github_release_hook,
    is_consensus_downgrade,
    is_execution_downgrade,
    is_harness_downgrade,
    load_override,
    load_seeds_manifest,
    main as latest_override_main,
    normalize_deploy_clients,
    prepare_rc_overrides,
    remap_latest_tag,
    write_override,
    write_seeds_manifest,
)


def test_remap_latest_tag_noop_without_file(tmp_path):
    path = tmp_path / "override.json"
    assert remap_latest_tag("sigp/lighthouse", "LATEST", path=str(path)) == "LATEST"
    assert remap_latest_tag("sigp/lighthouse", "v7.0.0", path=str(path)) == "v7.0.0"
    assert forced_rc_tag("lighthouse", path=str(path)) is None


def test_write_clear_and_remap_lifecycle(tmp_path):
    path = str(tmp_path / "override.json")
    payload = write_override({"Lighthouse": "v7.1.0-rc.0", "geth": ""}, path=path)

    assert payload["clients"]["lighthouse"] == "v7.1.0-rc.0"
    assert "geth" not in payload["clients"]
    assert payload["repos"]["sigp/lighthouse"] == "v7.1.0-rc.0"

    assert remap_latest_tag("sigp/lighthouse", "LATEST", path=path) == "v7.1.0-rc.0"
    assert remap_latest_tag("sigp/lighthouse", "latest", path=path) == "v7.1.0-rc.0"
    assert remap_latest_tag("sigp/lighthouse", "v7.0.0", path=path) == "v7.0.0"
    assert remap_latest_tag("paradigmxyz/reth", "LATEST", path=path) == "LATEST"
    assert forced_rc_tag("Lighthouse", path=path) == "v7.1.0-rc.0"
    assert forced_rc_tag("reth", path=path) is None

    on_disk = load_override(path)
    assert on_disk["clients"]["lighthouse"] == "v7.1.0-rc.0"

    clear_override(path)
    assert remap_latest_tag("sigp/lighthouse", "LATEST", path=path) == "LATEST"
    assert load_override(path) == {"clients": {}, "repos": {}, "kinds": {}}


def test_normalize_deploy_clients_skips_caplin_and_empties():
    assert normalize_deploy_clients("Reth", "Lighthouse") == ["reth", "lighthouse"]
    assert normalize_deploy_clients("Erigon", "Caplin") == ["erigon"]
    assert normalize_deploy_clients("Geth", "", mev=True, charon=True) == [
        "geth",
        "mevboost",
        "charon",
    ]
    assert normalize_deploy_clients("same as cc") == []


def test_prepare_writes_only_differing_rcs(tmp_path):
    path = str(tmp_path / "override.json")

    def fake_find_rc(client: str, _repo):
        if client == "lighthouse":
            return {
                "client": "lighthouse",
                "rc_tag": "v7.1.0-rc.0",
                "latest": "v7.0.1",
                "status": "ok",
                "reason": "prerelease resolvable via release_info",
            }
        if client == "geth":
            return {
                "client": "geth",
                "rc_tag": None,
                "latest": "v1.16.2",
                "status": "skip",
                "reason": "Geth downloads page serves stables only; no RC via release_info",
            }
        return {
            "client": client,
            "rc_tag": "v1.0.0",
            "latest": "v1.0.0",
            "status": "ok",
            "reason": "same as latest",
        }

    result = prepare_rc_overrides(
        ["lighthouse", "geth", "reth"],
        path=path,
        find_rc_fn=fake_find_rc,
    )
    assert result["clients"] == {"lighthouse": "v7.1.0-rc.0"}
    assert result["repos"]["sigp/lighthouse"] == "v7.1.0-rc.0"
    assert remap_latest_tag("sigp/lighthouse", "LATEST", path=path) == "v7.1.0-rc.0"
    assert remap_latest_tag("ethereum/go-ethereum", "LATEST", path=path) == "LATEST"


def test_prepare_clears_leftover_override_before_discovery(tmp_path):
    path = str(tmp_path / "override.json")
    write_override({"lighthouse": "v7.1.0-rc.0"}, path=path)

    def find_rc(client: str, _repo):
        assert not Path(path).exists(), "leftover override must be cleared before find_rc"
        return {
            "client": client,
            "rc_tag": "v7.1.0-rc.0",
            "latest": "v7.0.1",
            "status": "ok",
            "reason": "prerelease resolvable via release_info",
        }

    result = prepare_rc_overrides(["lighthouse"], path=path, find_rc_fn=find_rc)
    assert result["clients"]["lighthouse"] == "v7.1.0-rc.0"
    assert Path(path).exists()


def test_prepare_clears_file_when_no_rc(tmp_path):
    path = str(tmp_path / "override.json")
    write_override({"lighthouse": "v7.1.0-rc.0"}, path=path)

    def all_skip(client: str, _repo):
        return {
            "client": client,
            "rc_tag": None,
            "latest": "v1.2.3",
            "status": "skip",
            "reason": "no resolvable RC/prerelease with assets in recent releases",
        }

    result = prepare_rc_overrides(["geth"], path=path, find_rc_fn=all_skip)
    assert result["clients"] == {}
    assert not Path(path).exists()


def test_prepare_reuses_find_upgrade_seed(tmp_path):
    path = str(tmp_path / "override.json")
    with patch("find_client_rc.find_upgrade_seed") as find_seed:
        find_seed.return_value = {
            "client": "teku",
            "rc_tag": "25.9.0-rc1",
            "seed_tag": "25.9.0-rc1",
            "seed_kind": "rc",
            "latest": "25.8.0",
            "status": "ok",
            "reason": "prerelease resolvable via release_info",
        }
        result = prepare_rc_overrides(["teku"], path=path)
    find_seed.assert_called_once()
    assert find_seed.call_args[0][0] == "teku"
    assert result["clients"]["teku"] == "25.9.0-rc1"
    assert result["kinds"]["teku"] == "rc"


def test_prepare_soft_skips_pre_gloas_seed_on_sepolia(tmp_path):
    path = str(tmp_path / "override.json")

    def fake_find_rc(client: str, _repo):
        if client == "lodestar":
            return {
                "client": "lodestar",
                "rc_tag": None,
                "seed_tag": "v1.48.0",
                "seed_kind": "stable",
                "latest": "v1.49.0",
                "status": "ok",
                "reason": "previous stable",
            }
        if client == "teku":
            return {
                "client": "teku",
                "rc_tag": None,
                "seed_tag": "26.9.0",
                "seed_kind": "stable",
                "latest": "26.9.1",
                "status": "ok",
                "reason": "previous stable",
            }
        return {
            "client": client,
            "status": "skip",
            "reason": "unused",
            "latest": None,
            "seed_tag": None,
            "rc_tag": None,
        }

    result = prepare_rc_overrides(
        ["lodestar", "teku"],
        path=path,
        find_rc_fn=fake_find_rc,
        network="SEPOLIA",
    )
    assert result["clients"] == {}
    assert "Gloas" in result["skipped"]["lodestar"]
    assert "Gloas" in result["skipped"]["teku"]
    assert not Path(path).exists()


def test_prepare_keeps_lighthouse_rc_when_seed_meets_gloas_floor(tmp_path):
    """Lighthouse-specific RC→pre-Gloas-LATEST soft-skip was removed post-Gloas.

    Only the general Sepolia minimum-version floor still rejects seeds.
    """
    path = str(tmp_path / "override.json")

    def fake_find_rc(client: str, _repo):
        return {
            "client": "lighthouse",
            "rc_tag": "v8.3.1-rc.0",
            "seed_tag": "v8.3.1-rc.0",
            "seed_kind": "rc",
            "latest": "v8.2.3",
            "status": "ok",
            "reason": "prerelease resolvable via release_info",
        }

    kept = prepare_rc_overrides(
        ["lighthouse"],
        path=path,
        find_rc_fn=fake_find_rc,
        network="sepolia",
    )
    assert kept["clients"]["lighthouse"] == "v8.3.1-rc.0"


def test_install_github_release_hook_remaps_latest(tmp_path):
    path = str(tmp_path / "override.json")
    write_override({"nimbus": "v25.9.0-rc1"}, path=path)
    original = common.get_github_release
    captured: dict[str, str] = {}

    def inner(repo: str, version_tag: str) -> dict:
        captured["repo"] = repo
        captured["tag"] = version_tag
        return {"tag_name": version_tag}

    try:
        common.get_github_release = inner
        with patch.dict("os.environ", {"ETHPILLAR_INTEGRATION_LATEST_OVERRIDE": path}):
            assert install_github_release_hook()
            result = common.get_github_release("status-im/nimbus-eth2", "LATEST")
            assert captured["tag"] == "v25.9.0-rc1"
            assert result["tag_name"] == "v25.9.0-rc1"
            result_exact = common.get_github_release("status-im/nimbus-eth2", "v25.8.0")
            assert captured["tag"] == "v25.8.0"
            assert result_exact["tag_name"] == "v25.8.0"
    finally:
        common.get_github_release = original


def test_sitecustomize_installs_override_hook():
    text = Path("tests/integration/sitecustomize.py").read_text(encoding="utf-8")
    assert "install_github_release_hook" in text
    assert "latest_override" in text
    assert "geth.get_release_info" in text


def test_check_client_versions_script_reads_clients_map():
    text = Path("tests/integration/check_client_versions.sh").read_text(encoding="utf-8")
    assert "ETHPILLAR_INTEGRATION_LATEST_OVERRIDE" in text
    assert ".clients[$k]" in text
    assert "forced seed" in text
    assert "matches_forced_seed" in text


def test_consensus_downgrade_when_rc_seed_is_newer_than_latest(tmp_path):
    """Lighthouse v8.3.0-rc.0 vs LATEST v8.2.3 is the Nightly schema-downgrade case."""
    manifest = tmp_path / "seeds.json"
    write_seeds_manifest(
        {"lighthouse": "v8.3.0-rc.0", "reth": "v1.9.0-rc.1", "teku": "25.9.0"},
        {"lighthouse": "rc", "reth": "rc", "teku": "stable"},
        {},
        path=str(manifest),
        latest={
            "lighthouse": "v8.2.3",
            "reth": "v1.8.0",
            "teku": "25.9.3",
        },
    )
    loaded = load_seeds_manifest(str(manifest))
    assert loaded["latest"]["lighthouse"] == "v8.2.3"
    assert is_consensus_downgrade("lighthouse", path=str(manifest))
    assert is_consensus_downgrade("Lighthouse", path=str(manifest))
    # EL RC newer than LATEST is not a beacon-DB downgrade.
    assert not is_consensus_downgrade("reth", path=str(manifest))
    # Same seed is an execution downgrade: wipe EL chaindata, not the beacon DB.
    assert is_execution_downgrade("reth", path=str(manifest))
    assert is_execution_downgrade("Reth", path=str(manifest))
    assert not is_execution_downgrade("lighthouse", path=str(manifest))
    assert is_harness_downgrade("lighthouse", path=str(manifest))
    assert is_harness_downgrade("reth", path=str(manifest))
    # Previous stable is older than LATEST: keep the DB and upgrade forward.
    assert not is_consensus_downgrade("teku", path=str(manifest))
    assert not is_execution_downgrade("teku", path=str(manifest))
    assert not is_consensus_downgrade("nimbus", path=str(manifest))
    assert not is_consensus_downgrade("lighthouse", path=str(tmp_path / "missing.json"))
    assert not is_execution_downgrade("reth", path=str(tmp_path / "missing.json"))


def test_prepare_records_official_latest_for_downgrade_check(tmp_path, capsys):
    override = str(tmp_path / "override.json")
    seeds = str(tmp_path / "seeds.json")

    def fake_find_seed(client: str, _repo):
        if client == "lighthouse":
            return {
                "client": client,
                "rc_tag": "v8.3.0-rc.0",
                "seed_tag": "v8.3.0-rc.0",
                "seed_kind": "rc",
                "latest": "v8.2.3",
                "status": "ok",
                "reason": "prerelease newer than LATEST",
            }
        return {
            "client": client,
            "rc_tag": None,
            "seed_tag": "v1.2.2",
            "seed_kind": "stable",
            "latest": "v1.2.3",
            "status": "ok",
            "reason": "previous stable older than LATEST",
        }

    result = prepare_rc_overrides(
        ["lighthouse", "reth"],
        path=override,
        find_rc_fn=fake_find_seed,
        seeds_dest=seeds,
    )
    assert result["latest"]["lighthouse"] == "v8.2.3"
    assert result["latest"]["reth"] == "v1.2.3"
    assert is_consensus_downgrade("lighthouse", path=seeds)
    assert not is_consensus_downgrade("reth", path=seeds)
    assert not is_execution_downgrade("reth", path=seeds)
    logged = capsys.readouterr().out
    assert "binary downgrade vs LATEST" in logged
    assert "wipe the beacon DB" in logged
    assert "wipe execution chaindata" not in logged


def test_prepare_logs_execution_chaindata_wipe(tmp_path, capsys):
    override = str(tmp_path / "override.json")
    seeds = str(tmp_path / "seeds.json")

    def fake_find_seed(client: str, _repo):
        return {
            "client": client,
            "rc_tag": "v1.9.0-rc.1",
            "seed_tag": "v1.9.0-rc.1",
            "seed_kind": "rc",
            "latest": "v1.8.0",
            "status": "ok",
            "reason": "prerelease newer than LATEST",
        }

    prepare_rc_overrides(
        ["reth", "geth"],
        path=override,
        find_rc_fn=fake_find_seed,
        seeds_dest=seeds,
    )
    assert is_execution_downgrade("reth", path=seeds)
    assert is_execution_downgrade("geth", path=seeds)
    assert not is_consensus_downgrade("reth", path=seeds)
    logged = capsys.readouterr().out
    assert "wipe execution chaindata" in logged
    assert "wipe the beacon DB" not in logged


def test_execution_previous_stable_is_not_a_downgrade(tmp_path):
    manifest = tmp_path / "seeds.json"
    write_seeds_manifest(
        {"geth": "v1.16.3", "besu": "25.7.0"},
        {"geth": "stable", "besu": "stable"},
        {},
        path=str(manifest),
        latest={"geth": "v1.16.4", "besu": "25.9.0"},
    )
    assert not is_execution_downgrade("geth", path=str(manifest))
    assert not is_execution_downgrade("besu", path=str(manifest))
    assert not is_harness_downgrade("mevboost", path=str(manifest))


def test_downgrade_cli_prints_yes_or_no(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "seeds.json"
    write_seeds_manifest(
        {"lodestar": "v1.49.0-rc.0"},
        {"lodestar": "rc"},
        {},
        path=str(manifest),
        latest={"lodestar": "v1.48.0"},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["latest_override.py", "downgrade", "lodestar", "--seeds", str(manifest)],
    )
    assert latest_override_main() == 0
    assert capsys.readouterr().out.strip() == "yes"

    monkeypatch.setattr(
        "sys.argv",
        ["latest_override.py", "downgrade", "lodestar", "--seeds", str(tmp_path / "absent.json")],
    )
    assert latest_override_main() == 0
    assert capsys.readouterr().out.strip() == "no"

    el_manifest = tmp_path / "el-seeds.json"
    write_seeds_manifest(
        {"reth": "v1.9.0-rc.1", "mevboost": "v1.10.0-rc.0"},
        {"reth": "rc", "mevboost": "rc"},
        {},
        path=str(el_manifest),
        latest={"reth": "v1.8.0", "mevboost": "v1.9.0"},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["latest_override.py", "downgrade", "reth", "--seeds", str(el_manifest)],
    )
    assert latest_override_main() == 0
    assert capsys.readouterr().out.strip() == "yes"
    monkeypatch.setattr(
        "sys.argv",
        ["latest_override.py", "downgrade", "mevboost", "--seeds", str(el_manifest)],
    )
    assert latest_override_main() == 0
    assert capsys.readouterr().out.strip() == "no"


def test_test_updates_script_two_phase_and_install_gate():
    text = Path("tests/integration/test_updates.sh").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "list-unit-files" not in code
    assert "unit_installed" in code
    assert "/etc/systemd/system/" in code
    assert "already up to date" in code
    assert "check-updates" in code
    assert "Soft-skipping real-upgrade assert" in text
    assert "ethpillar-integration-upgrade-seeds.json" in text
    assert "ETHPILLAR_CHECK_ROLES" in text
    assert "binary_downgrade_answer" in text
    assert "wipe_consensus_beacon_for_downgrade" in text
    assert "wipe_execution_datadir_for_downgrade" in text
    assert '"$target" == "consensus"' in text
    assert '"$target" == "execution"' in text
    wipe = Path("tests/integration/wipe_consensus_beacon.sh").read_text(encoding="utf-8")
    assert "beacon_datadir_for_client" in wipe
    assert "wipe_beacon_datadir" in wipe
    assert "systemctl stop consensus" in wipe
    assert "systemctl start" not in wipe
    assert "systemctl restart" not in wipe
    el_wipe = Path("tests/integration/wipe_execution_datadir.sh").read_text(encoding="utf-8")
    for path in (
        "/var/lib/nethermind",
        "/var/lib/besu",
        "/var/lib/geth",
        "/var/lib/erigon",
        "/var/lib/ethrex",
        "/var/lib/reth",
    ):
        assert path in el_wipe
    assert "getExecutionDatadir" in el_wipe
    assert "getExecutionStaticFiles" in el_wipe
    assert "systemctl stop execution" in el_wipe
    assert "systemctl start" not in el_wipe
    assert "systemctl restart" not in el_wipe
    assert "resync_execution.sh" in el_wipe
    check = Path("tests/integration/check_client_versions.sh").read_text(encoding="utf-8")
    assert "ETHPILLAR_CHECK_ROLES" in check
    assert "should_check_role" in check


def test_install_geth_release_hook_remaps_latest(tmp_path):
    path = str(tmp_path / "override.json")
    write_override({"geth": "v1.16.3"}, path=path)
    import deploy.geth as geth

    original = geth.get_release_info
    captured: dict[str, object] = {}

    def inner(version_tag: str, arch_amd64: bool) -> dict:
        captured["tag"] = version_tag
        captured["arch"] = arch_amd64
        return {"version": version_tag}

    try:
        geth.get_release_info = inner
        with patch.dict("os.environ", {"ETHPILLAR_INTEGRATION_LATEST_OVERRIDE": path}):
            assert install_github_release_hook()
            result = geth.get_release_info("LATEST", True)
            assert captured["tag"] == "v1.16.3"
            assert result["version"] == "v1.16.3"
            result_exact = geth.get_release_info("v1.16.4", False)
            assert captured["tag"] == "v1.16.4"
            assert result_exact["version"] == "v1.16.4"
    finally:
        geth.get_release_info = original
