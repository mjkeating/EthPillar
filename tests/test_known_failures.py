"""Unit tests for the known-failures registry and classification helpers.

Behavior tests load ``tests/fixtures/known_failures.yaml`` (synthetic ids).
The shipped registry is checked for shape only: parsing, required fields,
unique ids, and compilable regexes. Those checks must keep passing when
entries are added or removed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from tests.integration.known_failures import (
    NOTICE_PREFIX,
    REGISTRY_PATH,
    REGISTRY_PATH_ENV,
    _emit_gha_annotation,
    all_clear_notice_payload,
    classify_failed_cell,
    issue_state_for_entry,
    known_upstream_version_mismatch,
    load_registry,
    main,
    match_log_known_failure,
    notice_all_clear,
    parse_notice_lines,
    resolve_registry_path,
    version_mismatch_is_known,
    write_ci_notices_artifact,
    CiNotice,
)

FIXTURE_REGISTRY = Path(__file__).resolve().parent / "fixtures" / "known_failures.yaml"
FIXTURE_CELL = "FixtureCL-FixtureEL_FIXTURENET"
SUBSTRING_ID = "fixture-log-substring-marker"
REGEX_ID = "fixture-log-regex-marker"
VERSION_ID = "fixture-version-closed-unfixed"
SUBSTRING_PATTERN = "fixture-signature: marker-reached"
_MATCH_KINDS = {"log_substring", "log_regex", "version_mismatch"}
_SCOPE_KEYS = ("cells", "clients", "networks")


def _fixture_entries():
    return load_registry(FIXTURE_REGISTRY)


def _scoped(item: dict) -> bool:
    for key in _SCOPE_KEYS:
        values = item.get(key) or []
        if isinstance(values, list) and any(
            isinstance(value, str) and value.strip() for value in values
        ):
            return True
    return False


def _assert_registry_schema(path: Path) -> None:
    """Schema checks that stay valid for any well-formed registry contents."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), path
    version = raw.get("version")
    assert isinstance(version, int) and not isinstance(version, bool) and version >= 1
    if "entries" in raw and raw["entries"] is not None:
        assert isinstance(raw["entries"], list)
        entries = raw["entries"]
    else:
        entries = []

    ids: list[str] = []
    for index, item in enumerate(entries):
        where = f"{path.name} entries[{index}]"
        assert isinstance(item, dict), where
        entry_id = item.get("id")
        assert isinstance(entry_id, str) and entry_id.strip() == entry_id, where
        ids.append(entry_id)
        assert isinstance(item.get("added"), str) and item["added"].strip(), where
        assert isinstance(item.get("reason"), str) and item["reason"].strip(), where
        match = item.get("match")
        assert isinstance(match, dict), where
        kind = match.get("kind")
        assert kind in _MATCH_KINDS, where
        if kind in ("log_substring", "log_regex"):
            pattern = match.get("pattern")
            assert isinstance(pattern, str) and pattern, where
            if kind == "log_regex":
                re.compile(pattern)
            assert _scoped(item), where
        else:
            installed = match.get("installed")
            expected = match.get("expected")
            assert isinstance(installed, str) and installed.strip(), where
            assert isinstance(expected, str) and expected.strip(), where
            clients = item.get("clients")
            assert isinstance(clients, list) and clients, where
            assert all(isinstance(client, str) and client.strip() for client in clients)
        if "issue" in item:
            assert isinstance(item["issue"], str), where
        if "issue_closed_unfixed" in item:
            assert isinstance(item["issue_closed_unfixed"], bool), where
        for key in _SCOPE_KEYS:
            if key not in item:
                continue
            values = item[key]
            assert isinstance(values, list), where
            assert all(isinstance(value, str) and value.strip() for value in values), where

    assert len(ids) == len(set(ids)), f"duplicate ids in {path.name}: {ids}"
    loaded = load_registry(path)
    assert [entry.id for entry in loaded] == ids
    for entry in loaded:
        assert entry.id and entry.match_kind in _MATCH_KINDS
        if entry.match_kind == "log_regex":
            re.compile(entry.pattern)
            assert entry.pattern


def test_shipped_registry_schema():
    _assert_registry_schema(REGISTRY_PATH)


def test_fixture_registry_schema_and_ids_differ_from_shipped():
    _assert_registry_schema(FIXTURE_REGISTRY)
    shipped = {entry.id for entry in load_registry(REGISTRY_PATH)}
    fixture = {entry.id for entry in load_registry(FIXTURE_REGISTRY)}
    assert fixture
    assert shipped.isdisjoint(fixture)


def test_registry_path_override(monkeypatch, tmp_path: Path, capsys):
    """Explicit path wins; the env var selects a tmp or fixture registry."""
    monkeypatch.delenv(REGISTRY_PATH_ENV, raising=False)
    assert resolve_registry_path() == REGISTRY_PATH
    assert [entry.id for entry in load_registry()] == [
        entry.id for entry in load_registry(REGISTRY_PATH)
    ]

    monkeypatch.setenv(REGISTRY_PATH_ENV, "  ")
    assert resolve_registry_path() == REGISTRY_PATH

    monkeypatch.setenv(REGISTRY_PATH_ENV, str(FIXTURE_REGISTRY))
    assert resolve_registry_path() == FIXTURE_REGISTRY
    assert resolve_registry_path(REGISTRY_PATH) == REGISTRY_PATH
    assert [entry.id for entry in load_registry()] == [
        entry.id for entry in _fixture_entries()
    ]

    tmp_registry = tmp_path / "known_failures.yaml"
    tmp_registry.write_text(
        "\n".join(
            [
                "version: 1",
                "entries:",
                "  - id: tmp-only-version-pair",
                "    clients: [tmpclient]",
                "    added: '2026-01-01'",
                "    reason: temporary registry row",
                "    match:",
                "      kind: version_mismatch",
                "      installed: '1.2.3'",
                "      expected: '1.2.4'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(REGISTRY_PATH_ENV, str(tmp_registry))
    assert [entry.id for entry in load_registry()] == ["tmp-only-version-pair"]
    assert known_upstream_version_mismatch("TmpClient", "v1.2.3", "v1.2.4")
    assert not known_upstream_version_mismatch("TmpClient", "1.2.3", "1.2.5")
    assert main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "tmp-only-version-pair" in listed
    assert "version_mismatch" in listed
    assert main(["version-mismatch", "tmpclient", "1.2.3", "1.2.4"]) == 0
    assert main(["version-mismatch", "tmpclient", "1.2.3", "9.9.9"]) == 1


def test_fixture_log_substring_matches(monkeypatch):
    monkeypatch.setenv(REGISTRY_PATH_ENV, str(FIXTURE_REGISTRY))
    log = (
        "❌ systemctl start consensus failed: "
        "Job for consensus.service failed because the control process exited "
        "with error code. | "
        f"ERR {SUBSTRING_PATTERN}\n"
    )
    entry = match_log_known_failure(FIXTURE_CELL, log)
    assert entry is not None
    assert entry.id == SUBSTRING_ID
    status, matched = classify_failed_cell(FIXTURE_CELL, log)
    assert status == "KNOWN_FAIL"
    assert matched is not None and matched.id == entry.id


def test_fixture_log_matches_from_failure_summary_alone(tmp_path: Path):
    """Known-failure matching must work from the ❌ summary, not only raw journal."""
    from tests.integration.run_docker_tests import extract_failure_reason
    from tests.integration.service_start import format_systemctl_start_failure

    entries = _fixture_entries()
    journal = f"ERR {SUBSTRING_PATTERN}\n"
    summary = format_systemctl_start_failure(
        "consensus",
        stderr="Job for consensus.service failed.\n",
        journal_text=journal,
    )
    assert SUBSTRING_PATTERN in summary
    log_path = tmp_path / "cell.log"
    log_path.write_text(f"Starting test...\n  ❌ {summary}\n", encoding="utf-8")
    assert SUBSTRING_PATTERN in extract_failure_reason(str(log_path))
    status, matched = classify_failed_cell(
        FIXTURE_CELL, log_path.read_text(encoding="utf-8"), entries=entries
    )
    assert status == "KNOWN_FAIL"
    assert matched is not None
    assert matched.id == SUBSTRING_ID


def test_fixture_other_failure_stays_red():
    entries = _fixture_entries()
    log = "❌ systemctl start consensus failed:\nERR something completely different\n"
    status, matched = classify_failed_cell(FIXTURE_CELL, log, entries=entries)
    assert status == "FAIL"
    assert matched is None


def test_unrelated_cell_not_classified_as_fixture_known():
    entries = _fixture_entries()
    status, matched = classify_failed_cell(
        "Teku-Besu_SEPOLIA", SUBSTRING_PATTERN, entries=entries
    )
    assert status == "FAIL"
    assert matched is None


def test_fixture_log_regex_matches_only_its_cell():
    entries = _fixture_entries()
    log = "ERR fixture-regex: code=42\n"
    entry = match_log_known_failure(FIXTURE_CELL, log, entries=entries)
    assert entry is not None
    assert entry.id == REGEX_ID
    status, matched = classify_failed_cell(FIXTURE_CELL, log, entries=entries)
    assert status == "KNOWN_FAIL"
    assert matched is not None and matched.id == REGEX_ID

    status, matched = classify_failed_cell("Teku-Besu_SEPOLIA", log, entries=entries)
    assert status == "FAIL"
    assert matched is None
    status, matched = classify_failed_cell(
        FIXTURE_CELL, "ERR fixture-regex: code=nope\n", entries=entries
    )
    assert status == "FAIL"
    assert matched is None


def test_fixture_version_mismatch_pair(monkeypatch):
    monkeypatch.setenv(REGISTRY_PATH_ENV, str(FIXTURE_REGISTRY))
    assert known_upstream_version_mismatch("FixtureVM", "9.9.1", "9.9.2")
    assert known_upstream_version_mismatch("fixturevm", "v9.9.1", "v9.9.2")
    assert not known_upstream_version_mismatch("FixtureVM", "9.9.1", "9.9.3")
    assert not known_upstream_version_mismatch("OtherClient", "9.9.1", "9.9.2")
    entry = version_mismatch_is_known("fixturevm", "9.9.1", "9.9.2")
    assert entry is not None
    assert entry.id == VERSION_ID
    assert entry.issue_closed_unfixed is True


def test_version_mismatch_ignores_closed_issue_state(capsys):
    """A closed-unfixed version pair must not look like an upstream fix."""
    entry = next(item for item in _fixture_entries() if item.id == VERSION_ID)
    assert issue_state_for_entry(entry) == ""
    # Even if a caller passes issue_state=closed, the payload must clear it.
    payload = all_clear_notice_payload(entry, client="fixturevm", issue_state="closed")
    assert payload["issue_state"] == ""
    assert payload["issue_closed_unfixed"] is True
    assert "was not observed" in payload["clear_message"]
    assert "upstream may be fixed" not in payload["clear_message"]

    notice = notice_all_clear(entry, client="fixturevm", issue_state="closed")
    assert notice.issue_state == ""
    assert "was not observed" in notice.reason
    assert "upstream may be fixed" not in notice.reason

    _emit_gha_annotation({**payload, "issue_state": "closed"})
    out = capsys.readouterr().out
    assert "::notice::" in out
    assert "was not observed" in out
    assert "linked GitHub issue is closed" not in out
    assert "upstream may be fixed" not in out


def test_log_all_clear_can_still_mention_closed_issue():
    entry = next(item for item in _fixture_entries() if item.id == SUBSTRING_ID)
    assert entry.issue_closed_unfixed is False
    msg = entry.all_clear_message(FIXTURE_CELL)
    assert "did not reproduce" in msg
    assert "upstream may be fixed" in msg
    payload = all_clear_notice_payload(
        entry, cell=FIXTURE_CELL, issue_state="closed"
    )
    assert payload["issue_state"] == "closed"


def test_list_cli_prints_fixture_registry(monkeypatch, capsys):
    monkeypatch.setenv(REGISTRY_PATH_ENV, str(FIXTURE_REGISTRY))
    assert main(["list"]) == 0
    listed = [line.split("\t", 1)[0] for line in capsys.readouterr().out.splitlines()]
    assert listed == [entry.id for entry in _fixture_entries()]
    assert SUBSTRING_ID in listed
    assert REGEX_ID in listed
    assert VERSION_ID in listed


def test_parse_notice_lines_and_artifact(tmp_path: Path):
    blob = "\n".join(
        [
            "noise",
            f'{NOTICE_PREFIX} {{"kind": "soft-skip", "status": "skipped", '
            f'"client": "reth", "reason": "no sane upgrade seed"}}',
            "more noise",
        ]
    )
    notices = parse_notice_lines(blob)
    assert len(notices) == 1
    assert notices[0].kind == "soft-skip"
    assert notices[0].client == "reth"
    out = write_ci_notices_artifact(
        [CiNotice(kind="soft-skip", status="skipped", client="reth", reason="x")],
        tmp_path / "ci-notices.json",
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["notices"][0]["client"] == "reth"
