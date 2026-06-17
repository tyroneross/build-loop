# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/fact_v1.py — ARP-ingestible fact.v1 emitter.

  - schema field is exactly "agent-rally.fact.v1" (migrate-legacy skips otherwise)
  - kind mapping delegates to post._native_kind (no second table; lead-*/commit → artifact)
  - ref is the wire name (not ref_id); optionals omitted when None
  - event_id deterministic + stable across calls with same content (dedup precondition)
  - build-loop-relevant kinds never produce read/receipt
  - write_fact_v1_line round-trips through json.loads to a valid fact
  - INDEPENDENCE: emit + read works with the rally binary ABSENT (env -i PATH=/usr/bin:/bin)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import fact_v1 as fv  # noqa: E402
import post as _post  # noqa: E402


@pytest.fixture()
def chan(tmp_path: Path) -> Path:
    d = tmp_path / "chan"
    d.mkdir()
    return d


def test_schema_field_exact():
    f = fv.to_fact_v1(kind="handoff", tool="claude", model="opus", run_id="r1",
                      app_slug="app", payload={"subject": "hi"})
    assert f["schema"] == "agent-rally.fact.v1"


def test_kind_map_delegates_to_native_kind():
    # The emitter must NOT keep a second mapping table — it must agree with
    # post._native_kind for every build-loop kind.
    for kind in ("commit", "dep-change", "phase", "arch-scan-complete", "feedback",
                 "handoff", "message", "lead-claim", "lead-renew", "lead-transfer",
                 "lead-relinquish", "escalation", "standby", "wake"):
        assert fv.map_kind(kind) == _post._native_kind(kind)


def test_lead_and_commit_map_to_artifact():
    # Confirms the verified-live behavior: lead-* and commit fall through to artifact.
    for kind in ("commit", "lead-claim", "lead-renew", "lead-transfer", "lead-relinquish"):
        assert fv.map_kind(kind) == "artifact"
    assert fv.map_kind("phase") == "presence"
    assert fv.map_kind("escalation") == "risk"
    assert fv.map_kind("handoff") == "handoff"


def test_ref_is_wire_name_not_ref_id():
    f = fv.to_fact_v1(kind="handoff", tool="t", model="m", run_id="r",
                      app_slug="a", payload={"ref_id": "evt_99", "subject": "x"})
    assert f.get("ref") == "evt_99"
    assert "ref_id" not in f


def test_optionals_omitted_when_none():
    f = fv.to_fact_v1(kind="handoff", tool="t", model="m", run_id="r",
                      app_slug="a", payload={"subject": "x"})
    for opt in ("summary", "target", "ref", "status", "severity", "uri",
                "session", "from_session_id"):
        assert opt not in f, f"optional {opt} should be omitted when absent"


def test_event_id_deterministic_and_stable():
    args = dict(kind="handoff", tool="t", model="m", run_id="r",
                app_slug="a", payload={"subject": "same"}, created_at="2026-06-17T00:00:00Z")
    a = fv.to_fact_v1(**args)
    b = fv.to_fact_v1(**args)
    assert a["event_id"] == b["event_id"]  # dedup precondition
    # Different content → different id
    c = fv.to_fact_v1(kind="handoff", tool="t", model="m", run_id="r",
                      app_slug="a", payload={"subject": "different"}, created_at="2026-06-17T00:00:00Z")
    assert c["event_id"] != a["event_id"]


def test_read_receipt_coerced_not_emitted_as_kind():
    # Build-loop fallback must never emit read/receipt; a stray call coerces to artifact.
    for kind in ("read", "receipt"):
        f = fv.to_fact_v1(kind=kind, tool="t", model="m", run_id="r",
                          app_slug="a", payload={"subject": "x"})
        assert f["kind"] == "artifact"


def test_bl_revision_carried():
    f = fv.to_fact_v1(kind="handoff", tool="t", model="m", run_id="r",
                      app_slug="a", payload={"subject": "x"}, revision=42)
    assert f["bl_revision"] == 42
    assert f["seq"] == 0  # store-assigned; emitter writes 0


def test_scope_and_evidence_always_lists():
    f = fv.to_fact_v1(kind="handoff", tool="t", model="m", run_id="r",
                      app_slug="a", payload={"path": "file.py", "subject": "x"})
    assert isinstance(f["scope"], list) and f["scope"] == ["file.py"]
    assert isinstance(f["evidence"], list)


def test_write_and_json_roundtrip(chan: Path):
    f = fv.to_fact_v1(kind="handoff", tool="claude", model="opus", run_id="r1",
                      app_slug="app", payload={"subject": "hello", "to": "codex"}, revision=3)
    fv.write_fact_v1_line(chan, f)
    lines = (chan / "changes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["schema"] == "agent-rally.fact.v1"
    assert parsed["kind"] == "handoff"
    assert parsed["target"] == "codex"
    assert parsed["bl_revision"] == 3


def test_independence_no_rally_binary():
    # Emit + read with a stripped environment proving no rally binary is needed.
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "import fact_v1 as fv\n"
        "import json, tempfile, pathlib\n"
        "d = pathlib.Path(tempfile.mkdtemp())\n"
        "f = fv.to_fact_v1(kind='handoff', tool='t', model='m', run_id='r', app_slug='a',"
        " payload={'subject':'indep'}, revision=7)\n"
        "fv.write_fact_v1_line(d, f)\n"
        "line = (d / 'changes.jsonl').read_text().strip()\n"
        "obj = json.loads(line)\n"
        "assert obj['schema'] == 'agent-rally.fact.v1'\n"
        "assert obj['bl_revision'] == 7\n"
        "print('INDEPENDENCE_OK')\n"
    ) % str(_HERE)
    proc = subprocess.run(
        ["/usr/bin/env", "python3", "-c", script],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert "INDEPENDENCE_OK" in proc.stdout
