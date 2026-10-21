"""Behavioral coverage of automatic capture and the real run-writer actuator."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest
from retrospective import cadence
from retrospective.__main__ import main as retro_main


def seed(root, ids, **overrides):
    rows = [dict(run_id=rid, outcome="pass", review_scope="build",
                 lessons=["lesson-" + rid], failures=["failure-" + rid], **overrides) for rid in ids]
    target = root / ".build-loop/state.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"runs": rows}))
    return rows


def test_batch_covers_every_member_once_without_claiming_transcript_evidence(tmp_path):
    seed(tmp_path, ["one", "two"])
    assert cadence.automatic(tmp_path)["pending_builds"] == 2
    seed(tmp_path, ["one", "two", "three"])
    result = cadence.automatic(tmp_path)
    assert result["status"] == "captured"
    body = Path(result["active_path"]).read_text()
    for rid in result["run_ids"]:
        assert "lesson-" + rid in body and "failure-" + rid in body
        assert f"`{rid}`" in body
    assert "NO TRANSCRIPT" in body and "recorded build" in body
    assert cadence.automatic(tmp_path)["pending_builds"] == 0
    assert cadence.automatic(tmp_path, trigger="review")["pending_builds"] == 0
    assert len([p for p in (tmp_path / ".build-loop/retrospectives").glob("*/auto-*.md") if not p.name.endswith(".summary.md")]) == 1


def test_explicit_review_flushes_early_but_partial_chunk_and_legacy_do_not_count(tmp_path, capsys):
    rows = seed(tmp_path, ["ready"])
    rows += [{"run_id": "partial", "outcome": "partial", "review_scope": "build"},
             {"run_id": "chunk", "outcome": "pass", "review_scope": "chunk"},
             {"run_id": "legacy", "outcome": "pass"}]
    (tmp_path / ".build-loop/state.json").write_text(json.dumps({"runs": rows}))
    assert retro_main(["--automatic", "--trigger", "review", "--workdir", str(tmp_path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["run_ids"] == ["ready"]


@pytest.mark.parametrize("threshold", [True, False, 0, -1, "3", 101])
def test_invalid_cadence_does_not_consume_evidence(tmp_path, threshold):
    seed(tmp_path, ["one", "two", "three"])
    (tmp_path / ".build-loop/config.json").write_text(json.dumps({"retrospective": {"every_builds": threshold}}))
    assert cadence.automatic(tmp_path)["status"] == "degraded"
    assert not (tmp_path / ".build-loop/retrospectives/cadence.json").exists()


def test_custom_cadence_and_duplicate_ids(tmp_path):
    rows = seed(tmp_path, ["one", "one"])
    (tmp_path / ".build-loop/config.json").write_text(json.dumps({"retrospective": {"every_builds": 2}}))
    assert cadence.automatic(tmp_path)["pending_builds"] == 1
    rows.append(dict(run_id="two", outcome="fail", review_scope="build", failures=["terminal failure"]))
    (tmp_path / ".build-loop/state.json").write_text(json.dumps({"runs": rows}))
    assert cadence.automatic(tmp_path)["run_ids"] == ["one", "two"]


@pytest.mark.parametrize("failure", [RuntimeError("unexpected writer failure"), TimeoutError("synthesis timed out")])
def test_failure_keeps_batch_pending_and_retry_uses_same_source(tmp_path, failure):
    seed(tmp_path, ["one", "two", "three"])
    with patch.object(cadence, "synthesize", side_effect=failure):
        assert cadence.automatic(tmp_path)["status"] == "degraded"
    assert not (tmp_path / ".build-loop/retrospectives/cadence.json").exists()
    assert cadence.automatic(tmp_path)["status"] == "captured"


def test_concurrent_requests_capture_one_cohort(tmp_path):
    seed(tmp_path, ["one", "two", "three"])
    original = cadence.synthesize
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def slow(*args, **kwargs):
        calls.append(kwargs["run_ids"])
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    with patch.object(cadence, "synthesize", side_effect=slow), ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(cadence.automatic, tmp_path)
        assert entered.wait(5)
        peers = [pool.submit(cadence.automatic, tmp_path) for _ in range(3)]
        assert all(job.result()["status"] == "deferred" for job in peers)
        release.set()
        assert first.result()["status"] == "captured"
    assert len(calls) == 1
    assert cadence.automatic(tmp_path)["pending_builds"] == 0


def test_actual_run_writer_actuates_without_explicit_retro_command(tmp_path):
    script = Path(__file__).resolve().parents[1] / "write_run_entry/__main__.py"
    for number in range(3):
        result = subprocess.run([sys.executable, str(script), "--workdir", str(tmp_path),
            "--run-id", f"build-{number}", "--goal", "fixture build", "--outcome", "pass", "--scope", "build"],
            capture_output=True, text=True, env={**os.environ, "BUILD_LOOP_MEMORY_STORE_ROOT": str(tmp_path / "memory")})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"build-{number}"
    checkpoint = json.loads((tmp_path / ".build-loop/retrospectives/cadence.json").read_text())
    assert sorted(checkpoint["covered_versions"]) == ["build-0", "build-1", "build-2"]
    assert Path(checkpoint["last_batch"]["active_path"]).is_file()


def test_automatic_cli_rejects_mixed_source_selection(tmp_path):
    with pytest.raises(SystemExit) as error:
        retro_main(["--workdir", str(tmp_path), "--automatic", "--run-id", "other"])
    assert error.value.code == 2


def test_record_versions_keep_late_findings_eligible_without_counting_date_only_upserts(tmp_path):
    rows = seed(tmp_path, ["one", "two", "three"])
    first = cadence.automatic(tmp_path)
    rows[0]["date"] = "changed bookkeeping date"
    state = tmp_path / ".build-loop/state.json"
    state.write_text(json.dumps({"runs": rows}))
    assert cadence.automatic(tmp_path, trigger="review")["pending_builds"] == 0
    rows[0]["lessons"].append("new source finding")
    state.write_text(json.dumps({"runs": rows}))
    assert cadence.automatic(tmp_path)["pending_builds"] == 1
    second = cadence.automatic(tmp_path, trigger="review")
    assert second["run_id"] != first["run_id"]
    assert second["run_ids"] == ["one"]
    assert "new source finding" in Path(second["active_path"]).read_text()


def test_build_arriving_during_capture_is_retained_for_next_event(tmp_path):
    seed(tmp_path, ["one", "two", "three"])
    original = cadence.synthesize
    def arriving(*args, **kwargs):
        result = original(*args, **kwargs)
        seed(tmp_path, ["one", "two", "three", "four"])
        assert cadence.automatic(tmp_path)["status"] == "deferred"
        return result
    with patch.object(cadence, "synthesize", side_effect=arriving):
        assert cadence.automatic(tmp_path)["status"] == "captured"
    assert cadence.automatic(tmp_path)["pending_builds"] == 1
    assert cadence.automatic(tmp_path, trigger="review")["run_ids"] == ["four"]


def test_real_self_review_actuates_and_dry_run_does_not_write(tmp_path):
    seed(tmp_path, ["one"])
    script = Path(__file__).resolve().parents[1] / "self_review/__main__.py"
    command = [sys.executable, str(script), "--mode", "light", "--workdir", str(tmp_path), "--json"]
    env = {**os.environ, "BUILD_LOOP_MEMORY_STORE_ROOT": str(tmp_path / "memory")}
    (tmp_path / "memory").mkdir()
    dry = subprocess.run([*command, "--dry-run"], capture_output=True, text=True, env=env, timeout=30)
    assert dry.returncode == 0, dry.stderr
    assert not (tmp_path / ".build-loop/retrospectives/cadence.json").exists()
    real = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30, cwd=tmp_path)
    assert real.returncode == 0, real.stderr
    result = json.loads(real.stdout)
    assert result["retrospective"]["status"] == "captured", result
    assert result["retrospective"]["run_ids"] == ["one"]


@pytest.mark.parametrize("source,payload", [
    ("config.json", {"retrospective": []}),
    ("state.json", {"runs": False}),
    ("state.json", []),
    ("retrospectives/cadence.json", {"schema": cadence.SCHEMA, "covered_versions": []}),
])
def test_malformed_source_is_unknown_not_success(tmp_path, source, payload):
    seed(tmp_path, ["one", "two", "three"])
    path = tmp_path / ".build-loop" / source
    path.parent.mkdir(parents=True, exist_ok=True)
    before = json.dumps(payload)
    path.write_text(before)
    assert cadence.automatic(tmp_path)["status"] == "degraded"
    assert path.read_text() == before


def test_checkpoint_write_failure_does_not_lose_pending_evidence(tmp_path):
    seed(tmp_path, ["one", "two", "three"])
    with patch.object(cadence, "atomic_write_bytes", side_effect=OSError("disk unavailable")):
        assert cadence.automatic(tmp_path)["status"] == "degraded"
    assert not (tmp_path / ".build-loop/retrospectives/cadence.json").exists()
    first = list((tmp_path / ".build-loop/retrospectives").glob("*/auto-*.md"))
    assert cadence.automatic(tmp_path)["status"] == "captured"
    assert list((tmp_path / ".build-loop/retrospectives").glob("*/auto-*.md")) == first


def test_batch_keeps_run_provenance_for_identical_findings(tmp_path):
    rows = seed(tmp_path, ["one", "two", "three"])
    for row in rows:
        row["lessons"] = ["same lesson"]
        row["judge_decisions"] = [{"judge_id": "audit", "verdict": "nay", "variances": ["same finding"]},
                                  {"judge_id": "audit", "verdict": "yay"}]
        row["planned_wins"] = ["same earned win"]
        row["unplanned_wins"] = ["same lucky win"]
    state = tmp_path / ".build-loop/state.json"
    original = json.dumps({"runs": rows})
    state.write_text(original)
    result = cadence.automatic(tmp_path)
    body = Path(result["active_path"]).read_text()
    for rid in ["one", "two", "three"]:
        assert f"`{rid}`: same lesson" in body
        assert rid + ": audit" in body
        assert f"`{rid}`: audit approved" in body
        assert f"`{rid}`: same earned win" in body
        assert f"`{rid}`: same lucky win" in body
    assert state.read_text() == original


def test_late_positive_evidence_is_not_hidden_by_prior_coverage(tmp_path):
    rows = seed(tmp_path, ["one", "two", "three"])
    assert cadence.automatic(tmp_path)["status"] == "captured"
    rows[0]["planned_wins"] = ["new positive evidence"]
    (tmp_path / ".build-loop/state.json").write_text(json.dumps({"runs": rows}))
    result = cadence.automatic(tmp_path, trigger="review")
    assert result["status"] == "captured"
    assert "new positive evidence" in Path(result["active_path"]).read_text()


def test_actual_npm_payload_runs_the_build_writer_and_batch(tmp_path):
    import shutil
    import tarfile
    if not shutil.which("npm"):
        pytest.skip("npm is required for the actual distribution check")
    source = Path(__file__).resolve().parents[2]
    packed = subprocess.run(["npm", "pack", "--ignore-scripts", "--json", "--pack-destination", str(tmp_path)],
                            cwd=source, capture_output=True, text=True, timeout=60)
    assert packed.returncode == 0, packed.stderr
    metadata = json.loads(packed.stdout)[0]
    stage = tmp_path / "extracted"
    stage.mkdir()
    with tarfile.open(tmp_path / metadata["filename"]) as archive:
        for member in archive:
            if not member.isfile():
                continue
            target = (stage / member.name).resolve()
            assert target.is_relative_to(stage.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    memory = tmp_path / "memory"
    memory.mkdir()
    env = {**os.environ, "BUILD_LOOP_MEMORY_STORE_ROOT": str(memory),
           "BUILD_LOOP_MEMORY_ROOT": str(memory), "AGENT_MEMORY_ROOT": str(memory)}
    env.pop("PYTHONPATH", None)
    writer = stage / "package/scripts/write_run_entry/__main__.py"
    assert (stage / "package/scripts/retrospective/cadence.py").is_file()
    for i in range(3):
        result = subprocess.run([sys.executable, "-I", str(writer), "--workdir", str(consumer),
            "--goal", "packaged fixture", "--run-id", f"pack-{i}", "--scope", "build", "--outcome", "pass"],
            cwd=consumer, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    receipt = json.loads((consumer / ".build-loop/retrospectives/cadence.json").read_text())
    assert sorted(receipt["covered_versions"]) == ["pack-0", "pack-1", "pack-2"]
    assert Path(receipt["last_batch"]["active_path"]).is_file()
