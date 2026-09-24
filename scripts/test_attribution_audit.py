# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for attribution_audit.py — applicability, auto-apply, and backlog filing."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attribution_audit as aa  # noqa: E402

PROFILE = {
    "github_owners": ["owner"],
    "brand_name": "Example Labs",
    "brand_url": "https://example.test",
    "copyright_holder": "Pat Example",
}
APACHE = "Apache License\nVersion 2.0, January 2004\n"


def make_repo(tmp: Path, name: str = "proj", origin: str | None = "git@github.com:owner/proj.git") -> Path:
    repo = tmp / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", origin], check=True)
    return repo


def gh(visibility: str = "PUBLIC", **extra):
    return lambda _slug: {"visibility": visibility, "isFork": False, "isArchived": False,
                         "homepageUrl": "", "repositoryTopics": [], **extra}


def profile(tmp: Path) -> dict:
    p = tmp / "profile.json"
    p.write_text(json.dumps(PROFILE))
    loaded = aa.load_profile(p)
    assert loaded is not None
    return loaded


def by_id(report: dict) -> dict:
    return {i["id"]: i for i in report["items"]}


def test_missing_profile_does_nothing(tmp_path):
    repo = make_repo(tmp_path)
    out = aa.assess(repo, aa.load_profile(tmp_path / "absent.json"), gh())
    assert out == {"repo": str(repo.resolve()), "applies": False, "reason": "profile_missing", "profile": None, "items": []}


@pytest.mark.parametrize(
    "origin,meta,reason",
    [
        ("git@github.com:someone-else/proj.git", {}, "third_party_origin:someone-else"),
        (None, {}, "no_github_origin"),
        ("https://github.com/owner/proj.git", {"isFork": True}, "fork"),
        ("https://github.com/owner/proj.git", {"isArchived": True}, "archived"),
    ],
)
def test_foreign_forked_archived_and_unpublished_repos_are_skipped(tmp_path, origin, meta, reason):
    repo = make_repo(tmp_path, origin=origin)
    out = aa.assess(repo, profile(tmp_path), gh(**meta))
    assert out["applies"] is False
    assert out["reason"] == reason


def test_private_repo_only_checks_the_web_app_footer(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "next.config.js").write_text("module.exports = {}\n")
    (repo / "package.json").write_text(json.dumps({"name": "proj", "private": True}))
    items = by_id(aa.assess(repo, profile(tmp_path), gh("PRIVATE")))
    assert items["web-app-footer"]["status"] == "missing"
    assert items["web-app-footer"]["disposition"] == "planned"
    for iid in ("license", "notice", "readme-credit", "citation-cff", "github-metadata"):
        assert items[iid]["status"] == "not_applicable", iid


def test_unknown_visibility_is_not_guessed(tmp_path):
    repo = make_repo(tmp_path)
    items = by_id(aa.assess(repo, profile(tmp_path), lambda _slug: None))
    assert items["readme-credit"]["status"] == "unknown"
    assert items["license"]["status"] == "unknown"


def test_public_repo_gaps_and_dispositions(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "LICENSE").write_text(APACHE)
    (repo / "README.md").write_text("# proj\n")
    (repo / "package.json").write_text(json.dumps({"name": "proj", "author": "Pat"}, indent=2) + "\n")
    items = by_id(aa.assess(repo, profile(tmp_path), gh()))
    assert items["license"]["status"] == "present"
    assert (items["notice"]["status"], items["notice"]["disposition"]) == ("missing", "auto")
    assert (items["readme-credit"]["status"], items["readme-credit"]["disposition"]) == ("missing", "auto")
    assert items["package-json"]["detail"] == "missing: homepage, funding"
    assert items["github-metadata"]["disposition"] == "decision"
    assert "gh repo edit owner/proj" in items["github-metadata"]["fix"]
    assert items["web-app-footer"]["status"] == "not_applicable"


def test_missing_license_is_a_decision_never_auto(tmp_path):
    repo = make_repo(tmp_path)
    items = by_id(aa.assess(repo, profile(tmp_path), gh()))
    assert (items["license"]["status"], items["license"]["disposition"]) == ("missing", "decision")
    assert items["notice"]["status"] == "not_applicable"  # NOTICE is an Apache-2.0 mechanism


def test_apply_writes_only_auto_items_and_preserves_existing_fields(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "LICENSE").write_text(APACHE)
    (repo / "README.md").write_text("# proj\n\nIntro.\n")
    (repo / "package.json").write_text(json.dumps({"name": "proj", "author": "Pat"}, indent=4) + "\n")
    prof = profile(tmp_path)

    result = aa.apply(repo, prof, gh=gh())
    assert sorted(result["applied"]) == ["CITATION.cff", "NOTICE", "README.md", "package.json"]

    assert (repo / "README.md").read_text().endswith("---\n\nBuilt by [Example Labs](https://example.test)\n")
    notice = (repo / "NOTICE").read_text()
    assert "Copyright 2025-2026 Pat Example" in notice and "Example Labs: https://example.test" in notice
    assert "Claude" in notice  # shares the stamper's canonical NOTICE template
    pkg_raw = (repo / "package.json").read_text()
    pkg = json.loads(pkg_raw)
    assert pkg["author"] == "Pat"  # never overwritten
    assert pkg["homepage"] == "https://example.test" and pkg["funding"] == "https://example.test"
    assert '\n    "name"' in pkg_raw  # original 4-space indent kept
    cff = (repo / "CITATION.cff").read_text()
    assert 'repository-code: "https://github.com/owner/proj"' in cff and "license: Apache-2.0" in cff

    after = by_id(aa.assess(repo, prof, gh()))
    for iid in ("notice", "readme-credit", "citation-cff", "package-json"):
        assert after[iid]["status"] == "present", iid
    assert aa.apply(repo, prof, gh=gh())["applied"] == []  # idempotent


def test_apply_never_touches_decision_items(tmp_path):
    repo = make_repo(tmp_path)
    result = aa.apply(repo, profile(tmp_path), gh=gh())
    assert not (repo / "LICENSE").exists()
    assert "LICENSE" not in result["applied"]


def test_file_items_dedupes_and_closes_fixed_items(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "LICENSE").write_text(APACHE)
    prof = profile(tmp_path)
    calls: list[list[str]] = []
    items_dir = repo / ".build-loop" / "backlog" / "items"

    def fake_backlog(args):
        calls.append(args)
        if args[0] == "new":
            ref = args[args.index("--provenance-ref") + 1]
            bid = f"BL-{len(calls)}"
            items_dir.mkdir(parents=True, exist_ok=True)
            (items_dir / f"{bid}.md").write_text(
                f"---\nid: {bid}\nstatus: open\nprovenance:\n  source: {aa.PROVENANCE_SOURCE}\n  ref: {ref}\n---\n")
            return {"ok": True, "id": bid}
        if args[0] == "update":
            f = items_dir / f"{args[1]}.md"
            f.write_text(f.read_text().replace("status: open", "status: done"))
            return {"ok": True}
        return {"ok": True}

    first = aa.file_items(repo, prof, gh(), fake_backlog)
    filed = {f["item"]: f["disposition"] for f in first["filed"]}
    assert filed == {"notice": "auto", "readme-credit": "auto", "citation-cff": "auto", "github-metadata": "decision"}
    decision_call = next(c for c in calls if "attribution:github-metadata" in c)
    assert decision_call[decision_call.index("--bucket") + 1] == "decision"

    second = aa.file_items(repo, prof, gh(), fake_backlog)
    assert second["filed"] == [] and sorted(second["kept"]) == sorted(filed)

    (repo / "NOTICE").write_text("proj\n")
    third = aa.file_items(repo, prof, gh(), fake_backlog)
    assert third["closed"] == ["notice"]


def test_sweep_skips_non_repos_and_linked_worktrees(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    make_repo(root, "mine")
    make_repo(root, "theirs", origin="git@github.com:other/theirs.git")
    (root / "notes").mkdir()
    wt = root / "mine-wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: elsewhere\n")
    out = aa.sweep([root], profile(tmp_path), do_file=False, gh=gh())
    names = {r["repo"]: r for r in out["rows"]}
    assert set(names) == {"mine", "theirs"}
    assert names["mine"]["applies"] and not names["theirs"]["applies"]


def test_cli_kill_switch(monkeypatch, capsys):
    monkeypatch.setenv("BUILDLOOP_ATTRIBUTION_AUDIT", "0")
    assert aa.main(["assess", "--repo", "."]) == 0
    assert "BUILDLOOP_ATTRIBUTION_AUDIT=0" in capsys.readouterr().out


def test_file_items_against_the_real_backlog_tool(tmp_path, monkeypatch):
    """The fake above encodes an assumed item format; this pins the real one."""
    monkeypatch.setenv("BUILD_LOOP_MEMORY_STORE_ROOT", str(tmp_path / "memory"))
    repo = make_repo(tmp_path)
    (repo / "LICENSE").write_text(APACHE)
    prof = profile(tmp_path)

    first = aa.file_items(repo, prof, gh())
    assert all(f["ok"] and f["backlog"] for f in first["filed"]), first["filed"]
    assert {f["item"] for f in first["filed"]} == {"notice", "readme-credit", "citation-cff", "github-metadata"}

    second = aa.file_items(repo, prof, gh())
    assert second["filed"] == []

    (repo / "NOTICE").write_text("proj\n")
    third = aa.file_items(repo, prof, gh())
    assert third["closed"] == ["notice"]
    assert aa._existing_items(repo)["notice"][1] == "done"
