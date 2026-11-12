import json
from tests.hook_parity.failure_evidence import save_failure
from tests.hook_parity.harness import Snapshot


def test_full_snapshot_survives_fixture_reset(tmp_path):
    root = tmp_path / "world"
    root.mkdir()
    binary = tmp_path / "bl"
    binary.write_bytes(b"fixture binary")
    payload = "漢" * 10000
    oracle = Snapshot(0, b"", b"", {"trace": payload}, [], 0.1)
    native = Snapshot(0, b"{}", b"\xff", {}, [], 1.5)
    path = save_failure(root, "unicode", binary, oracle, native)
    root.rmdir()
    record = json.loads(path.read_text())
    assert record["oracle"]["files"]["trace"] == payload
    assert record["native"]["stderr"] == {"hex": "ff"}
    assert record["native"]["elapsed"] == 1.5
    assert len(record["binary_sha256"]) == 64


def test_diagnostics_do_not_hide_other_stderr():
    from tests.hook_parity.failure_evidence import without_diagnostics
    stderr = (b"bl: diagnostic: hook=pre-edit reason=watchdog elapsed_ms=150\n"
              b"bl: diagnostic: unexpected failure\nreal error\n")
    snap = Snapshot(0, b"{}", stderr, {}, [], 0.15)
    normalized = without_diagnostics(snap)
    assert normalized.stderr == b"bl: diagnostic: unexpected failure\nreal error\n"
    assert snap.stderr == stderr
    assert normalized.stdout == b"{}"
