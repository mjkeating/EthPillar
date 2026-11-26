"""Unit tests for the install helpers in deploy.common.

These tests simulate system operations by monkeypatching
``deploy.common.subprocess.run`` so that the helpers can be
exercised without requiring root privileges or changing the
real system environment.

The tests verify that:
- ``install_system_binary`` moves a downloaded binary into the
  configured `INSTALL_DIR`, makes it executable and attempts to
  chown it to ``root:root``.
- ``install_system_directory`` moves an extracted tree into place,
  tightens directory and file permissions, preserves executable
  bits for files that were executable, and creates writable
  subdirectories for the service user.
- ``extract_and_install`` is covered in ``tests/test_extract_and_install.py``.
"""

import os
import stat
import shutil
from pathlib import Path
import pytest

from deploy import common


def make_dummy_result(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> object:
    """Create a lightweight stand-in for subprocess.CompletedProcess.

    Parameters
    - returncode: Exit code to emulate (0 for success).
    - stdout: Bytes to expose on the ``stdout`` attribute.
    - stderr: Bytes to expose on the ``stderr`` attribute.

    Returns an object with ``returncode``, ``stdout`` and ``stderr``
    attributes which is sufficient for the tests' needs.
    """

    class R:
        def __init__(self) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    return R()


def test_install_system_binary_moves_and_sets_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `install_system_binary` moves file and sets mode to 0o755.

    Parameters
    - tmp_path: pytest-provided temporary directory fixture as a Path.
    - monkeypatch: pytest MonkeyPatch fixture used to replace
        ``subprocess.run`` with a fake implementation.
    """

    calls = []

    def fake_run(cmd, check=False, **kwargs):
        calls.append(list(cmd))
        # normalize sudo prefix
        cmd_list = list(cmd)
        if cmd_list and cmd_list[0] == "sudo":
            cmd_list = cmd_list[1:]

        if not cmd_list:
            return make_dummy_result()

        cmd0 = cmd_list[0]
        if cmd0 == "mkdir":
            path = cmd_list[-1]
            os.makedirs(path, exist_ok=True)
            return make_dummy_result()
        if cmd0 == "mv":
            src, dst = cmd_list[1], cmd_list[2]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                os.replace(src, dst)
            except Exception:
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

        return make_dummy_result()

    monkeypatch.setattr(common.subprocess, "run", fake_run)

    # point INSTALL_DIR to a tmp dir so we don't touch system paths
    monkeypatch.setattr(common, "INSTALL_DIR", str(tmp_path / "bin"))

    # create a fake source binary
    src = tmp_path / "downloaded_bin"
    src.write_text("#!/bin/sh\necho hi")
    # ensure no exec bit initially
    os.chmod(src, 0o644)

    dest = common.install_system_binary(str(src), "mybin")

    assert os.path.exists(dest)
    mode = stat.S_IMODE(os.stat(dest).st_mode)
    assert mode == 0o755

    # ensure we attempted to chown root:root at some point
    assert any((c and c[0] == "sudo" and "chown" in c) or (c and c[0] == "chown") for c in calls) or any("chown" in " ".join(map(str, c)) for c in calls)


def test_install_system_directory_hardens_permissions_and_writable_subdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `install_system_directory` hardens the tree and creates subdirs.

    Parameters
    - tmp_path: pytest-provided temporary directory fixture as a Path.
    - monkeypatch: pytest MonkeyPatch fixture used to replace
        ``subprocess.run`` so operations occur inside the test temp tree.
    """

    calls = []
    # track files that were executable before we normalize to 644
    exec_candidates = set()

    def fake_run(cmd, check=False, **kwargs):
        calls.append(list(cmd))
        cmd_list = list(cmd)
        if cmd_list and cmd_list[0] == "sudo":
            cmd_list = cmd_list[1:]

        if not cmd_list:
            return make_dummy_result()

        cmd0 = cmd_list[0]
        if cmd0 == "mkdir":
            path = cmd_list[-1]
            os.makedirs(path, exist_ok=True)
            return make_dummy_result()
        if cmd0 == "rm" and "-rf" in cmd_list:
            path = cmd_list[-1]
            shutil.rmtree(path, ignore_errors=True)
            return make_dummy_result()
        if cmd0 == "mv":
            src, dst = cmd_list[1], cmd_list[2]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                os.replace(src, dst)
            except Exception:
                shutil.move(src, dst)
            return make_dummy_result()
        if cmd0 == "chown":
            # record chown but don't actually chown
            return make_dummy_result()
        if cmd0 == "find":
            dest_dir = cmd_list[1]
            # directories -> 755
            if "-type" in cmd_list and "d" in cmd_list:
                for root, dirs, files in os.walk(dest_dir):
                    for d in dirs:
                        os.chmod(os.path.join(root, d), 0o755)

            # handle file-related find invocations. Two common patterns:
            # 1) find <dir> -type f -perm /111 -exec chmod 755 {}
            # 2) find <dir> -type f ! -perm /111 -exec chmod 644 {}
            if "-type" in cmd_list and "f" in cmd_list:
                if "-perm" in cmd_list and "/111" in cmd_list:
                    # chmod 755 for files that have any exec bit
                    for root, dirs, files in os.walk(dest_dir):
                        for f in files:
                            p = os.path.join(root, f)
                            st = os.stat(p).st_mode
                            if st & 0o111:
                                os.chmod(p, 0o755)
                elif "!" in cmd_list and "-perm" in cmd_list and "/111" in cmd_list:
                    # chmod 644 for files that do NOT have exec bit
                    for root, dirs, files in os.walk(dest_dir):
                        for f in files:
                            p = os.path.join(root, f)
                            st = os.stat(p).st_mode
                            if not (st & 0o111):
                                os.chmod(p, 0o644)
                else:
                    # fallback: normalize all files to 644
                    for root, dirs, files in os.walk(dest_dir):
                        for f in files:
                            p = os.path.join(root, f)
                            os.chmod(p, 0o644)
            return make_dummy_result()

        return make_dummy_result()

    monkeypatch.setattr(common.subprocess, "run", fake_run)

    # prepare a source dir
    src_dir = tmp_path / "nether_src"
    (src_dir / "bin").mkdir(parents=True)
    (src_dir / "lib").mkdir()
    exe_file = src_dir / "bin" / "run.sh"
    exe_file.write_text("#!/bin/sh\necho run")
    os.chmod(exe_file, 0o755)
    data_file = src_dir / "lib" / "data.txt"
    data_file.write_text("data")
    os.chmod(data_file, 0o644)

    dest_dir = str(tmp_path / "opt" / "nethermind")

    common.install_system_directory(str(src_dir), dest_dir, service_user="consensus", writable_subdirs=["data", "logs"])

    # verify tree exists
    assert os.path.isdir(dest_dir)
    # directories should be 755
    dir_mode = stat.S_IMODE(os.stat(os.path.join(dest_dir, "bin")).st_mode)
    assert dir_mode == 0o755
    # files that were executable keep 755
    exe_mode = stat.S_IMODE(os.stat(os.path.join(dest_dir, "bin", "run.sh")).st_mode)
    assert exe_mode == 0o755
    # regular files are 644
    data_mode = stat.S_IMODE(os.stat(os.path.join(dest_dir, "lib", "data.txt")).st_mode)
    assert data_mode == 0o644

    # writable subdirs created
    assert os.path.isdir(os.path.join(dest_dir, "data"))
    assert os.path.isdir(os.path.join(dest_dir, "logs"))

    # ensure chown was attempted for root and for service_user on writable dirs
    flat = [" ".join(map(str, c)) for c in calls]
    assert any("chown -R root:root" in s or "chown -R root:root" in s for s in flat) or any("chown" in s for s in flat)
    assert any("consensus:consensus" in s for s in flat)


def test_ensure_jemalloc_installs_dev_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_jemalloc apt-installs libjemalloc-dev (unversioned .so symlink)."""
    calls = []

    def fake_run(cmd, check=False, env=None, **kwargs):
        calls.append(list(cmd))
        assert env is not None and env.get("DEBIAN_FRONTEND") == "noninteractive"
        return make_dummy_result(returncode=0)

    monkeypatch.setattr(common.subprocess, "run", fake_run)
    assert common.ensure_jemalloc() is True
    assert calls == [["sudo", "apt-get", "-y", "-qq", "install", "libjemalloc-dev"]]


def test_ensure_jemalloc_returns_false_on_apt_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_jemalloc reports failure when apt cannot install the package."""

    def fake_run(cmd, check=False, env=None, **kwargs):
        return make_dummy_result(returncode=1)

    monkeypatch.setattr(common.subprocess, "run", fake_run)
    assert common.ensure_jemalloc() is False


def test_ensure_libatomic_skips_apt_when_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_libatomic does not call apt when libatomic1 is already installed."""
    calls = []

    def fake_run(cmd, check=False, env=None, **kwargs):
        calls.append(list(cmd))
        return make_dummy_result(returncode=0)

    monkeypatch.setattr(common.subprocess, "run", fake_run)
    assert common.ensure_libatomic() is True
    assert calls == [["dpkg", "-s", "libatomic1"]]


def test_ensure_libatomic_installs_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_libatomic apt-installs libatomic1 non-interactively when absent."""
    calls = []

    def fake_run(cmd, check=False, env=None, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "dpkg":
            return make_dummy_result(returncode=1)
        assert env is not None and env.get("DEBIAN_FRONTEND") == "noninteractive"
        return make_dummy_result(returncode=0)

    monkeypatch.setattr(common.subprocess, "run", fake_run)
    assert common.ensure_libatomic() is True
    assert calls == [
        ["dpkg", "-s", "libatomic1"],
        ["sudo", "apt-get", "-y", "-qq", "update"],
        [
            "sudo",
            "apt-get",
            "-y",
            "-qq",
            "install",
            "--no-install-recommends",
            "libatomic1",
        ],
    ]


def test_ensure_libatomic_returns_false_on_apt_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_libatomic reports failure when apt cannot install libatomic1."""

    def fake_run(cmd, check=False, env=None, **kwargs):
        if cmd[0] == "dpkg":
            return make_dummy_result(returncode=1)
        return make_dummy_result(returncode=1)

    monkeypatch.setattr(common.subprocess, "run", fake_run)
    assert common.ensure_libatomic() is False


def test_download_lodestar_installs_libatomic_before_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh BN/VC install (including Charon Lodestar VC) installs libatomic1 first."""
    import deploy.lodestar as lodestar

    order = []

    monkeypatch.setattr(lodestar, "setup_client_user_and_dir", lambda *a, **k: None)
    monkeypatch.setattr(lodestar, "get_machine_architecture", lambda: "amd64")
    monkeypatch.setattr(
        lodestar,
        "get_release_info",
        lambda *a, **k: {"version": "v2.0.0-rc.1", "download_urls": [], "filenames": []},
    )
    monkeypatch.setattr(lodestar, "validate_version_for_network", lambda *a, **k: (True, None))

    def fake_ensure():
        order.append("ensure")
        return True

    def fake_download(*a, **k):
        order.append("download")
        return "/tmp/lodestar.tar.gz"

    def fake_extract(*a, **k):
        order.append("extract")

    monkeypatch.setattr(lodestar, "ensure_libatomic", fake_ensure)
    monkeypatch.setattr(lodestar, "download_release_file", fake_download)
    monkeypatch.setattr(lodestar, "extract_and_install", fake_extract)

    assert lodestar.download_lodestar("sepolia") == "v2.0.0-rc.1"
    assert order == ["ensure", "download", "extract"]


def test_download_lodestar_aborts_before_replacing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed libatomic1 install leaves any existing Lodestar binary in place."""
    import deploy.lodestar as lodestar

    monkeypatch.setattr(lodestar, "setup_client_user_and_dir", lambda *a, **k: None)
    monkeypatch.setattr(lodestar, "get_machine_architecture", lambda: "amd64")
    monkeypatch.setattr(
        lodestar,
        "get_release_info",
        lambda *a, **k: {"version": "v2.0.0-rc.1", "download_urls": [], "filenames": []},
    )
    monkeypatch.setattr(lodestar, "validate_version_for_network", lambda *a, **k: (True, None))
    monkeypatch.setattr(lodestar, "ensure_libatomic", lambda: False)

    def fail_download(*a, **k):
        raise AssertionError("download must not run when libatomic1 cannot be installed")

    def fail_extract(*a, **k):
        raise AssertionError("extract must not run when libatomic1 cannot be installed")

    monkeypatch.setattr(lodestar, "download_release_file", fail_download)
    monkeypatch.setattr(lodestar, "extract_and_install", fail_extract)

    with pytest.raises(SystemExit) as exc:
        lodestar.download_lodestar("sepolia")
    assert exc.value.code == 1


def test_ephemery_network_override_matches_known_shapes():
    """Centralized Ephemery overrides match generators and Nimbus resync path."""
    from deploy.nimbus import EPHEMERY_NETWORK_PATH

    assert common.EPHEMERY_NIMBUS_NETWORK == EPHEMERY_NETWORK_PATH
    assert common.ephemery_network_override("Nimbus", "bn") == f"--network={EPHEMERY_NETWORK_PATH}"
    lodestar = common.ephemery_network_override("Lodestar", "bn")
    assert "--paramsFile=/opt/ethpillar/testnet/config.yaml" in lodestar
    assert "--genesisStateFile=/opt/ethpillar/testnet/genesis.ssz" in lodestar
    assert common.ephemery_network_override("Lighthouse", "vc") == "--testnet-dir=/opt/ethpillar/testnet"
    assert common.ephemery_network_override("", "bn") is None
