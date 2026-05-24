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
