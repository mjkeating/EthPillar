import json
from unittest.mock import patch

from tests.integration.latest_snapshot import clear_snapshot, write_snapshot


def test_write_and_clear_snapshot(tmp_path):
    snapshot_path = tmp_path / "latest.json"

    with patch("tests.integration.latest_snapshot._release_info_module") as module_factory:
        module_factory.return_value = lambda client, tag: {
            "version": "v8.1.3" if client == "Lighthouse" else "v2.3.0"
        }
        written = write_snapshot(str(snapshot_path))

    assert written["lighthouse"] == "v8.1.3"
    assert written["reth"] == "v2.3.0"
    on_disk = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert on_disk["lighthouse"] == "v8.1.3"

    clear_snapshot(str(snapshot_path))
    assert not snapshot_path.exists()
