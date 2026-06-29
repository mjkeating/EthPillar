"""Unit tests for ``extract_and_install`` in deploy.common."""

from __future__ import annotations

import io
import os
import stat
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from deploy import common
from tests.integration.extract_cache import (
    get_extracted_cache_key,
    parse_tar_invocation,
    parse_unzip_invocation,
)


def make_dummy_result(returncode: int = 0) -> object:
    class R:
        def __init__(self, rc: int) -> None:
            self.returncode = rc
            self.stdout = b""
            self.stderr = b""

    return R(returncode)


def _extract_tar(archive_path: str, dest_dir: str, strip_components: int = 0) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as handle:
        for member in handle.getmembers():
            parts = member.name.split("/")
            if strip_components >= len(parts):
                continue
            member.name = "/".join(parts[strip_components:])
            if not member.name or member.name.endswith("/"):
                continue
            handle.extract(member, dest_dir)


def _extract_zip(archive_path: str, dest_dir: str) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(archive_path) as handle:
        handle.extractall(dest_dir)


def build_fake_run(calls: list) -> object:
    """Simulate sudo/tar/unzip/mv/chmod/chown/find used by install helpers."""

    def fake_run(cmd, check=False, **kwargs):
        calls.append(list(cmd))
        cmd_list = list(cmd)
        if cmd_list and cmd_list[0] == "sudo":
            cmd_list = cmd_list[1:]

        if not cmd_list:
            return make_dummy_result()

        cmd0 = cmd_list[0]
        if cmd0 == "mkdir":
            os.makedirs(cmd_list[-1], exist_ok=True)
            return make_dummy_result()
        if cmd0 == "rm":
            target = cmd_list[-1]
            if "-rf" in cmd_list:
                shutil.rmtree(target, ignore_errors=True)
            else:
                try:
                    os.remove(target)
                except FileNotFoundError:
                    pass
            return make_dummy_result()
        if cmd0 == "tar" and len(cmd_list) > 2 and cmd_list[1] == "xzf":
            archive = cmd_list[2]
            dest = cmd_list[cmd_list.index("-C") + 1]
            strip = 0
            for arg in cmd_list:
                if arg.startswith("--strip-components="):
                    strip = int(arg.split("=", 1)[1])
            _extract_tar(archive, dest, strip)
            return make_dummy_result()
        if cmd0 == "unzip":
            archive = next(arg for arg in cmd_list if arg.endswith(".zip"))
            dest = cmd_list[cmd_list.index("-d") + 1]
            _extract_zip(archive, dest)
            return make_dummy_result()
        if cmd0 == "mv":
            src, dst = cmd_list[1], cmd_list[2]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                os.replace(src, dst)
            except OSError:
                shutil.move(src, dst)
            return make_dummy_result()
        if cmd0 == "chmod":
            mode = cmd_list[1]
            path = cmd_list[2]
            if mode.startswith("+"):
                st = os.stat(path)
                os.chmod(path, st.st_mode | 0o111)
            else:
                os.chmod(path, int(mode, 8))
            return make_dummy_result()
        if cmd0 == "chown":
            return make_dummy_result()
        if cmd0 == "find":
            dest_dir = cmd_list[1]
            if "-type" in cmd_list and "d" in cmd_list:
                for root, dirs, _files in os.walk(dest_dir):
                    for name in dirs:
                        os.chmod(os.path.join(root, name), 0o755)
            if "-type" in cmd_list and "f" in cmd_list:
                if "-perm" in cmd_list and "/111" in cmd_list and "!" not in cmd_list:
                    for root, _dirs, files in os.walk(dest_dir):
                        for name in files:
                            path = os.path.join(root, name)
                            if os.stat(path).st_mode & 0o111:
                                os.chmod(path, 0o755)
                else:
                    for root, _dirs, files in os.walk(dest_dir):
                        for name in files:
                            path = os.path.join(root, name)
                            if not (os.stat(path).st_mode & 0o111):
                                os.chmod(path, 0o644)
            return make_dummy_result()

        return make_dummy_result()

    return fake_run


def _make_tgz(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as handle:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            handle.addfile(info, fileobj=io.BytesIO(content))


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as handle:
        for name, content in members.items():
            handle.writestr(name, content)


REPO_ROOT = Path(__file__).resolve().parents[1]
UNIFIED_DEPLOY_CLIENTS = (
    "besu",
    "erigon",
    "geth",
    "lighthouse",
    "lodestar",
    "mevboost",
    "nethermind",
    "reth",
    "teku",
)


@pytest.fixture
def install_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list = []
    monkeypatch.setattr(common.subprocess, "run", build_fake_run(calls))
    install_dir = tmp_path / "bin"
    monkeypatch.setattr(common, "INSTALL_DIR", str(install_dir))
    return calls, install_dir


@pytest.mark.parametrize("client", UNIFIED_DEPLOY_CLIENTS)
def test_deploy_module_calls_extract_and_install(client: str) -> None:
    source = (REPO_ROOT / "deploy" / f"{client}.py").read_text(encoding="utf-8")
    assert "extract_and_install(" in source


def test_extract_binary_from_nested_tar(install_env, tmp_path: Path) -> None:
    calls, install_dir = install_env
    archive = tmp_path / "geth.tar.gz"
    _make_tgz(archive, {"geth-1.0/bin/geth": b"#!/bin/sh\necho geth\n"})

    dest = str(install_dir / "geth")
    common.extract_and_install(str(archive), "geth", dest, "binary", strip_components=1)

    assert os.path.isfile(dest)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(dest).st_mode) == 0o755
    else:
        assert any("chmod" in " ".join(map(str, c)) for c in calls)
    assert not archive.exists()
    assert any(cmd[:2] == ["tar", "xzf"] for cmd in calls)


def test_extract_binary_prefix_match(install_env, tmp_path: Path) -> None:
    calls, install_dir = install_env
    archive = tmp_path / "reth.tar.gz"
    _make_tgz(archive, {"reth-v1.2.3-linux/reth-linux-amd64": b"#!/bin/sh\necho reth\n"})

    dest = str(install_dir / "reth")
    common.extract_and_install(str(archive), "reth", dest, "binary", strip_components=0, binary_name="reth")

    assert os.path.isfile(dest)


def test_extract_directory_with_strip(install_env, tmp_path: Path) -> None:
    calls, install_dir = install_env
    archive = tmp_path / "besu.tar.gz"
    _make_tgz(
        archive,
        {
            "besu-26.0/bin/besu": b"#!/bin/sh\necho besu\n",
            "besu-26.0/lib/helper.jar": b"jar",
        },
    )

    dest = str(install_dir / "besu")
    common.extract_and_install(str(archive), "besu", dest, "directory", strip_components=1)

    assert os.path.isdir(dest)
    assert os.path.isfile(os.path.join(dest, "bin", "besu"))
    assert not archive.exists()


def test_extract_zip_directory(install_env, tmp_path: Path) -> None:
    calls, install_dir = install_env
    archive = tmp_path / "nethermind.zip"
    _make_zip(
        archive,
        {
            "Nethermind.Runner": b"#!/bin/sh\necho nm\n",
            "Nethermind.dll": b"dll",
        },
    )

    dest = str(install_dir / "nethermind")
    common.extract_and_install(str(archive), "nethermind", dest, "directory", strip_components=0)

    assert os.path.isdir(dest)
    assert os.path.isfile(os.path.join(dest, "Nethermind.Runner"))


def test_extract_raises_when_binary_missing(install_env, tmp_path: Path) -> None:
    _calls, install_dir = install_env
    archive = tmp_path / "empty.tar.gz"
    _make_tgz(archive, {"readme.txt": b"no binary here\n"})

    with pytest.raises(FileNotFoundError, match="Could not find binary"):
        common.extract_and_install(
            str(archive), "geth", str(install_dir / "geth"), "binary", strip_components=0
        )


def test_extract_raises_on_invalid_target_type(install_env, tmp_path: Path) -> None:
    _calls, install_dir = install_env
    archive = tmp_path / "bad.tar.gz"
    _make_tgz(archive, {"bin/tool": b"data\n"})

    with pytest.raises(ValueError, match="Unknown target_type"):
        common.extract_and_install(
            str(archive), "geth", str(install_dir / "tool"), "tree", strip_components=0
        )


def test_extract_tar_invocation_matches_integration_cache_parser(
    install_env, tmp_path: Path
) -> None:
    """Tar args from extract_and_install must align with extract_cache.py parsing."""
    calls, install_dir = install_env
    archive = tmp_path / "geth.tar.gz"
    _make_tgz(archive, {"geth-1.0/geth": b"#!/bin/sh\n"})

    archive_path = str(archive)
    dest_dir = "/tmp/geth_extract"
    strip = 1
    cache_key = get_extracted_cache_key(archive_path, dest_dir, strip)

    common.extract_and_install(
        archive_path, "geth", str(install_dir / "geth"), "binary", strip_components=strip
    )

    tar_cmd = next(
        cmd for cmd in calls if len(cmd) >= 4 and cmd[0] == "tar" and cmd[1] == "xzf"
    )
    parsed_archive, parsed_dest, parsed_strip = parse_tar_invocation(tar_cmd[1:])
    assert parsed_archive == archive_path
    assert parsed_dest == dest_dir
    assert parsed_strip == strip
    assert cache_key


def test_extract_unzip_invocation_matches_integration_cache_parser(
    install_env, tmp_path: Path
) -> None:
    calls, install_dir = install_env
    archive = tmp_path / "nethermind.zip"
    _make_zip(archive, {"runner": b"bin\n"})

    archive_path = str(archive)
    dest_dir = "/tmp/nethermind_extract"
    strip = 0
    cache_key = get_extracted_cache_key(archive_path, dest_dir, strip)

    common.extract_and_install(
        archive_path, "nethermind", str(install_dir / "nethermind"), "directory", strip_components=strip
    )

    unzip_cmd = next(cmd for cmd in calls if len(cmd) >= 2 and cmd[0] == "unzip")
    parsed_archive, parsed_dest, parsed_strip = parse_unzip_invocation(unzip_cmd[1:])
    assert parsed_archive == archive_path
    assert parsed_dest == dest_dir
    assert parsed_strip == strip
    assert cache_key


def test_extract_and_install_cli_help() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "deploy.common", "extract_and_install", "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "binary-name" in result.stdout
    assert "directory" in result.stdout
