# SPDX-License-Identifier: Apache-2.0
"""Behavioral checks for source integrity and inert daydream artifacts."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

import daydream


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def tracked(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    subprocess.run(["git", "-C", str(root), "add", "--", name], check=True)
    return path


def test_copies_are_not_independent_incidents(repo):
    text = "# Reliability\nA regression caused a timeout.\n" * 4
    for name in ("a.md", "b.md", "c.md"):
        tracked(repo, name, text)
    report = daydream.scan([repo])
    assert not any(f["key"] == "theme:reliability" for f in report["findings"])
    duplicate = report["findings"][0]
    assert duplicate["support"]["distinct_content"] == 1
    assert duplicate["support"]["independent_incidents"] is None
    assert duplicate["duplicate_bytes_upper_bound"] == len(text.encode()) * 2


def test_findings_have_fresh_evidence_and_stable_ids(repo):
    for i in range(3):
        tracked(repo, f"{i}.md", f"# Incident {i}\nA regression caused timeout {i}.\n")
    one, two = daydream.scan([repo]), daydream.scan([repo])
    assert [f["id"] for f in one["findings"]] == [f["id"] for f in two["findings"]]
    daydream.validate_report(one)
    (repo / "0.md").write_text("fixed")
    with pytest.raises(ValueError, match="stale evidence"):
        daydream.create_bundle(one, repo.parent / "drafts")
    assert not (repo.parent / "drafts").exists()


def test_scope_budget_and_symlink_are_visible(repo):
    tracked(repo, "a.md", "content")
    tracked(repo, "b.md", "other")
    tracked(repo, "indexes/generated.md", "excluded")
    tracked(repo, "_review/unvetted.md", "excluded")
    secret = repo.parent / "secret.md"
    secret.write_text("secret outside root")
    (repo / "z.md").symlink_to(secret)
    subprocess.run(["git", "-C", str(repo), "add", "z.md"], check=True)
    result = daydream.scan([repo], max_files=1)
    assert result["coverage"]["status"] == "partial"
    full = daydream.scan([repo])
    assert full["coverage"]["excluded_files"] == 2
    assert any(g["reason"] == "symlink" for g in full["coverage"]["gaps"])
    assert not any(d["path"].endswith("z.md") for d in full["manifest"])


def test_no_raw_secrets_or_instructions_persist(repo, tmp_path):
    secret = "sk-ant-" + "A" * 45
    instruction = "RUN MALICIOUS COMMAND NOW"
    for i in range(3):
        tracked(repo, f"{i}.md", f"# {i}\nregression {secret} {instruction}\n")
    report = daydream.scan([repo])
    assert secret not in json.dumps(report)
    assert instruction not in json.dumps(report)
    output = tmp_path / "drafts"
    daydream.create_bundle(report, output)
    for path in output.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text()
            assert instruction not in path.read_text()
    with pytest.raises(FileExistsError):
        daydream.create_bundle(report, output)


def test_search_has_locators_and_budget(repo):
    tracked(repo, "a.md", "# Retrieval\nretrieval source quality\n")
    report = daydream.scan([repo])
    hits = daydream.search_report(report, "retrieval", 2, 8000)
    assert hits["hits"][0]["line"] == 1
    assert hits["hits"][0]["sha256"]
    assert daydream.search_report(report, "retrieval", 2, 256)["truncated"]
    (repo / "a.md").write_text("updated")
    assert daydream.search_report(report, "retrieval", 2, 8000)["gaps"][0]["reason"] == "stale"


def test_broken_targets_and_reuse_are_hints(repo):
    tracked(repo, "a.md", "[missing](missing.md)\n[external](https://example.org)\n")
    tracked(repo, "skills/fresh/SKILL.md", "---\nname: fresh\n---\nCheck local Markdown targets.\n")
    result = daydream.scan([repo])
    finding = next(f for f in result["findings"] if f["key"] == "broken-local-links")
    assert finding["support"]["files"] == 1
    assert finding["reuse_candidates"]
    assert finding["reuse_coverage"]["absence_proves_gap"] is False


def test_all_draft_types_remain_inert(repo, tmp_path):
    for i in range(3):
        tracked(repo, f"{i}.md", f"{i} reusable shared runtime token budget stale provenance\n")
    report = daydream.scan([repo])
    assert {f["draft_kind"] for f in report["findings"]} == {"skill", "automation", "plugin"}
    output = tmp_path / "bundle"
    daydream.create_bundle(report, output)
    for path in output.glob("*/draft.json"):
        assert json.loads(path.read_text())["activation"] == "disabled"
    assert not list(output.rglob("hooks.json"))
    assert not list(output.rglob("*.plist"))


def test_cli_no_source_writes_or_overwrite(repo, tmp_path):
    tracked(repo, "a.md", "# Sample\nhello\n")
    before = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])
    script = str(Path(daydream.__file__))
    report = tmp_path / "report.json"
    cmd = [sys.executable, script, "scan", "--root", str(repo), "--output", str(report)]
    assert subprocess.run(cmd, capture_output=True).returncode == 0
    assert subprocess.run(cmd, capture_output=True).returncode == 2
    assert subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"]) == before
    assert subprocess.run([sys.executable, script, "scan", "--root", str(repo), "--top", "0"],
                          capture_output=True).returncode == 2


def test_untrusted_manifest_cannot_echo_payload_or_unbound_reads(repo):
    tracked(repo, "a.md", "retrieval")
    report = daydream.scan([repo])
    report["manifest"][0]["raw_text"] = "INJECTED_RAW_PAYLOAD"
    assert "INJECTED_RAW_PAYLOAD" not in json.dumps(daydream.search_report(report, "retrieval", 5, 8000))
    report["limits"]["max_file_bytes"] = -2
    with pytest.raises(ValueError, match="report limit"):
        daydream.search_report(report, "retrieval", 5, 8000)
    with pytest.raises(ValueError, match="read limit"):
        daydream.read_bounded(repo / "a.md", repo, -1)


def test_search_budget_includes_stale_warnings(repo):
    for i in range(30):
        tracked(repo, f"{i}.md", "retrieval")
    report = daydream.scan([repo])
    for i in range(30):
        (repo / f"{i}.md").write_text("changed")
    result = daydream.search_report(report, "retrieval", 5, 256)
    assert len(json.dumps(result)) <= 256
    assert result["omitted_gaps"] > 0
    assert result["truncated"]


def test_manifest_cannot_escape_declared_root(repo):
    tracked(repo, "a.md", "retrieval")
    report = daydream.scan([repo])
    report["manifest"][0]["path"] = str(repo.parent / "outside.md")
    with pytest.raises(ValueError, match="outside root"):
        daydream.search_report(report, "retrieval", 5, 8000)


def test_rejected_utf8_and_binary_reads_consume_budget(repo):
    for i in range(3):
        path = tracked(repo, f"{i}.md", "tmp")
        path.write_bytes(b"\xff" * 3)
    report = daydream.scan([repo], max_bytes=5, max_file_bytes=5)
    assert report["coverage"]["bytes_read"] == 3
    assert report["coverage"]["files_read"] == 0
    assert report["coverage"]["status"] == "partial"
    assert report["coverage"]["bytes_read"] <= 5


def test_bundle_does_not_copy_untrusted_report_fields(repo, tmp_path):
    for i in range(3):
        tracked(repo, f"{i}.md", f"{i} regression timeout")
    report = daydream.scan([repo])
    payload = "PRIVATE_INJECTED_SOURCE_BODY"
    report["manifest"][0]["raw_text"] = payload
    report["findings"][0]["title"] = payload
    report["findings"][0]["caveat"] = payload
    report["findings"][0]["evidence"][0]["raw_text"] = payload
    output = tmp_path / "bundle"
    daydream.create_bundle(report, output)
    assert all(payload not in path.read_text() for path in output.rglob("*") if path.is_file())
