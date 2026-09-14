#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/premise_revalidation.py.

Uses unittest (not pytest) per the module's own house convention for this
plugin's newer scripts. Real tmpdir repos, `git init` where git is needed
(SkipTest if git absent from PATH) — mirrors scripts/test_collapse_run.py's
"build a real repo via subprocess" style, applied to unittest's TestCase +
tempfile.TemporaryDirectory instead of pytest's tmp_path fixture.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import premise_revalidation as pr  # noqa: E402

_GIT_AVAILABLE = shutil.which("git") is not None


def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _init_git_repo(repo: Path) -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


def _days_ago(days: int, today: _dt.date | None = None) -> str:
    base = today or _dt.date.today()
    return (base - _dt.timedelta(days=days)).isoformat()


def _write_item(
    path: Path,
    *,
    created: str,
    validated: str | None = None,
    status: str = "open",
    title: str = "Test item",
    body: str = "## Problem\nSomething is broken.\n",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"title: {title}", f"status: {status}", f"created: {created}"]
    if validated is not None:
        lines.append(f"validated: {validated}")
    lines.append("---")
    text = "\n".join(lines) + "\n\n" + body
    path.write_text(text, encoding="utf-8")


class BaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()

    def item_path(self, queue: str, name: str) -> Path:
        if queue == "issues":
            return self.repo / ".build-loop" / "issues" / name
        if queue == "backlog":
            return self.repo / ".build-loop" / "backlog" / name
        if queue == "followup":
            return self.repo / ".build-loop" / "followup" / name
        raise ValueError(queue)


class TestStaleItemRefusedAtGate(BaseTestCase):
    """Conviction #1: without the gate, the drain would have scheduled this."""

    def test_stale_item_is_refused_at_the_gate(self) -> None:
        item = self.item_path("issues", "old-item.md")
        _write_item(item, created=_days_ago(30))

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "stale_needs_revalidation")
        self.assertEqual(result["reason_code"], "stale_needs_revalidation")
        self.assertEqual(result["exit_code"], 1)


class TestFreshByConstruction(BaseTestCase):
    """The known-wrong version treats every NULL `validated` as stale
    (Operations Center's suite failed 9 tests that way) — the created_at
    fallback must NOT do that for a brand-new item."""

    def test_fresh_by_construction_is_not_refused(self) -> None:
        item = self.item_path("issues", "new-item.md")
        _write_item(item, created=_days_ago(0))

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["freshness_source"], "created")


class TestResolvedPremiseCaught(BaseTestCase):
    """A cited path that is genuinely gone (no relocation candidate anywhere
    in the repo) is premise_broken, not silently fresh."""

    def test_resolved_premise_is_caught(self) -> None:
        item = self.item_path("issues", "cites-gone-file.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nSee `scripts/gone.py` for the broken helper.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "premise_broken")
        self.assertEqual(result["exit_code"], 1)
        broken = [b["path"] for b in result["anchors"]["broken_paths"]]
        self.assertIn("scripts/gone.py", broken)


class TestRelocatedFileNotCalledBroken(BaseTestCase):
    """Conviction #2: encodes the operator's own stale-disproof error — a
    relocated file must route to needs_human_recheck with the candidate
    named, never to premise_broken."""

    def test_relocated_file_is_not_called_broken(self) -> None:
        # The cited path no longer exists...
        item = self.item_path("issues", "cites-moved-file.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nSee `old/dir/thing.py` for the broken helper.\n",
        )
        # ...but a same-basename file exists elsewhere in the repo.
        moved = self.repo / "other" / "dir" / "thing.py"
        moved.parent.mkdir(parents=True, exist_ok=True)
        moved.write_text("# relocated\n", encoding="utf-8")

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "needs_human_recheck")
        self.assertNotEqual(result["verdict"], "premise_broken")
        self.assertEqual(result["exit_code"], 1)  # still refused — a human must re-check
        relocated = result["anchors"]["relocated_paths"]
        self.assertEqual(len(relocated), 1)
        self.assertEqual(relocated[0]["path"], "old/dir/thing.py")
        self.assertIn("other/dir/thing.py", relocated[0]["candidates"])
        self.assertEqual(result["anchors"]["broken_paths"], [])


class TestValidateRequiresEvidence(BaseTestCase):
    def test_validate_requires_evidence(self) -> None:
        item = self.item_path("issues", "old-item.md")
        _write_item(item, created=_days_ago(30))
        original_text = item.read_text(encoding="utf-8")

        # No note -> refused, file untouched.
        result_missing = pr.validate(item, None)
        self.assertFalse(result_missing["ok"])
        self.assertEqual(item.read_text(encoding="utf-8"), original_text)

        # Empty/whitespace note -> also refused, file untouched.
        result_empty = pr.validate(item, "   ")
        self.assertFalse(result_empty["ok"])
        self.assertEqual(item.read_text(encoding="utf-8"), original_text)

        # A real note -> stamps validated: AND writes the receipt section.
        result_ok = pr.validate(item, "re-checked 2026-08-07, repo confirmed 0 ahead")
        self.assertTrue(result_ok["ok"])
        new_text = item.read_text(encoding="utf-8")
        self.assertIn("validated:", new_text)
        self.assertIn("## Premise validated", new_text)
        self.assertIn("re-checked 2026-08-07, repo confirmed 0 ahead", new_text)

        # Subsequent gate call sees it as fresh.
        gate_result = pr.gate(item, repo=self.repo, window_days=7)
        self.assertEqual(gate_result["verdict"], "fresh")
        self.assertEqual(gate_result["exit_code"], 0)
        self.assertEqual(gate_result["freshness_source"], "validated")


class TestDoneItemsExcluded(BaseTestCase):
    def test_done_items_excluded(self) -> None:
        done_item = self.item_path("issues", "done-item.md")
        _write_item(done_item, created=_days_ago(400), status="done")
        open_item = self.item_path("issues", "open-item.md")
        _write_item(open_item, created=_days_ago(400), status="open")

        result = pr.stale(self.repo, window_days=7)

        paths = [it["path"] for it in result["items"]]
        self.assertNotIn("done-item.md".replace("done-item.md", ".build-loop/issues/done-item.md"), paths)
        self.assertTrue(any("open-item.md" in p for p in paths))
        self.assertFalse(any("done-item.md" in p for p in paths))


class TestWindowBoundary(unittest.TestCase):
    """Exactly at the window edge: age_days == window_days is still FRESH
    (the window is a closed interval; only strictly past it is stale)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()

    def test_item_at_exact_window_edge_is_still_fresh(self) -> None:
        today = "2026-08-07"
        created = "2026-07-31"  # exactly 7 days before today
        item = self.repo / ".build-loop" / "issues" / "edge-item.md"
        _write_item(item, created=created)

        result = pr.gate(item, repo=self.repo, window_days=7, today=today)

        self.assertEqual(result["age_days"], 7)
        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["exit_code"], 0)

    def test_item_one_day_past_window_edge_is_stale(self) -> None:
        today = "2026-08-07"
        created = "2026-07-30"  # exactly 8 days before today
        item = self.repo / ".build-loop" / "issues" / "past-edge-item.md"
        _write_item(item, created=created)

        result = pr.gate(item, repo=self.repo, window_days=7, today=today)

        self.assertEqual(result["age_days"], 8)
        self.assertEqual(result["verdict"], "stale_needs_revalidation")
        self.assertEqual(result["exit_code"], 1)


class TestSweepCounts(BaseTestCase):
    def test_sweep_counts_all_verdicts(self) -> None:
        fresh_item = self.item_path("issues", "fresh.md")
        _write_item(fresh_item, created=_days_ago(0))
        stale_item = self.item_path("backlog", "stale.md")
        _write_item(stale_item, created=_days_ago(30))
        followup_item = self.item_path("followup", "fine.md")
        _write_item(followup_item, created=_days_ago(1))

        result = pr.sweep(self.repo, window_days=7, queues="all")

        self.assertEqual(result["counts"]["fresh"], 2)
        self.assertEqual(result["counts"]["stale_needs_revalidation"], 1)
        self.assertEqual(len(result["items"]), 3)


class TestSHAReachability(BaseTestCase):
    """git-backed SHA anchor check. Skipped if git is unavailable."""

    def setUp(self) -> None:
        super().setUp()
        if not _GIT_AVAILABLE:
            self.skipTest("git not available on PATH")
        _init_git_repo(self.repo)
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-m", "initial")
        self.good_sha = _git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def test_reachable_commit_is_fresh(self) -> None:
        item = self.item_path("issues", "cites-good-commit.md")
        _write_item(
            item,
            created=_days_ago(0),
            body=f"## Problem\nFixed in commit `{self.good_sha}`.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["anchors"]["broken_shas"], [])

    def test_unreachable_commit_is_premise_broken(self) -> None:
        item = self.item_path("issues", "cites-bad-commit.md")
        fake_sha = "deadbeefcafefeed1234deadbeefcafefeed1234"
        _write_item(
            item,
            created=_days_ago(0),
            body=f"## Problem\nFixed in commit `{fake_sha}`.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "premise_broken")
        self.assertIn(fake_sha, result["anchors"]["broken_shas"])


class TestDotDirectoryPathsAreNotFalseConvictions(BaseTestCase):
    """F6: `_BARE_PATH_RE` used to open with `\\b`, which sits BETWEEN the
    leading dot and the first letter of a dot-directory citation — every
    `.build-loop/...`, `.github/...`, `.claude-plugin/...` reference was
    extracted with its leading dot stripped, producing a path that can never
    exist and convicting a real, existing file as `premise_broken`."""

    def test_dot_directory_paths_are_not_false_convictions(self) -> None:

        self.assertEqual(
            pr.extract_paths("see `.build-loop/config.json`"),
            [".build-loop/config.json"],
        )
        self.assertEqual(
            pr.extract_paths("edit .github/workflows/x.yml"),
            [".github/workflows/x.yml"],
        )
        self.assertEqual(
            pr.extract_paths("see `.claude-plugin/plugin.json` for the manifest"),
            [".claude-plugin/plugin.json"],
        )

        # End-to-end: a real dot-directory file cited in an item body must
        # come back `fresh`, not `premise_broken`.
        item = self.item_path("issues", "cites-dot-dir.md")
        cited = self.repo / ".build-loop" / "config.json"
        cited.parent.mkdir(parents=True, exist_ok=True)
        cited.write_text("{}\n", encoding="utf-8")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nSee `.build-loop/config.json` for the setting.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["anchors"]["broken_paths"], [])


class TestPytestNodeIdIsNotExtractedAsAPath(BaseTestCase):
    """F6: backtick extraction has no internal `::` boundary check, so a
    pytest node ID citation (`scripts/test_x.py::TestA::test_b`) was captured
    verbatim and convicted broken — the class/function suffix will never
    exist on disk."""

    def test_pytest_node_id_is_not_extracted_as_a_path(self) -> None:

        node_id = "scripts/test_x.py::TestA::test_b"
        self.assertNotIn(node_id, pr.extract_paths(f"run `{node_id}`"))

        item = self.item_path("issues", "cites-node-id.md")
        real_test_file = self.repo / "scripts" / "test_x.py"
        real_test_file.parent.mkdir(parents=True, exist_ok=True)
        real_test_file.write_text("# test\n", encoding="utf-8")
        _write_item(
            item,
            created=_days_ago(0),
            body=f"## Problem\nRegression test: `{node_id}`.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["anchors"]["broken_paths"], [])


class TestHomeRelativePathResolves(BaseTestCase):
    """F6: `~/.codex/hooks.json` is a real global-config path, not a
    repo-relative miss — a leading `~/` must resolve against `$HOME`."""

    def test_home_relative_path_resolves(self) -> None:

        with tempfile.TemporaryDirectory() as fake_home_str:
            fake_home = Path(fake_home_str)
            codex_dir = fake_home / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            (codex_dir / "hooks.json").write_text("{}\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
                item = self.item_path("issues", "cites-home-path.md")
                _write_item(
                    item,
                    created=_days_ago(0),
                    body="## Problem\nInstall into `~/.codex/hooks.json`.\n",
                )

                result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["anchors"]["broken_paths"], [])

    def test_home_relative_path_missing_is_still_broken(self) -> None:
        """A `~/`-anchored citation that genuinely does not exist under
        $HOME must still be caught — resolving `~/` is not a blanket skip."""

        with tempfile.TemporaryDirectory() as fake_home_str:
            fake_home = Path(fake_home_str)  # deliberately empty — no .codex/

            with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
                item = self.item_path("issues", "cites-missing-home-path.md")
                _write_item(
                    item,
                    created=_days_ago(0),
                    body="## Problem\nInstall into `~/.codex/hooks.json`.\n",
                )

                result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "premise_broken")
        broken = [b["path"] for b in result["anchors"]["broken_paths"]]
        self.assertIn("~/.codex/hooks.json", broken)


class TestNoParseableFreshnessDateFailsClosed(BaseTestCase):
    """F9: two fail-open paths used to return `fresh` for an item whose
    freshness was never established — an undated item, and one with a
    malformed date. Both must fail CLOSED (stale), not open (fresh)."""

    def test_item_with_no_date_is_not_fresh(self) -> None:

        item = self.item_path("issues", "no-date-item.md")
        item.parent.mkdir(parents=True, exist_ok=True)
        # Deliberately no `created:`/`validated:` field at all.
        item.write_text(
            "---\ntitle: No date\nstatus: open\n---\n\n## Problem\nNo dates here.\n",
            encoding="utf-8",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "stale_needs_revalidation")
        self.assertEqual(result["exit_code"], 1)
        self.assertIsNone(result["freshness_date"])

    def test_malformed_date_is_not_fresh_and_reports_why(self) -> None:

        item = self.item_path("issues", "malformed-date-item.md")
        _write_item(item, created="2026/08/01")  # not ISO-8601

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "stale_needs_revalidation")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["reason_code"], "no_parseable_freshness_date")
        self.assertIsNotNone(result.get("freshness_error"))
        self.assertIn("2026/08/01", result["freshness_error"])


class TestCLI(BaseTestCase):
    def test_cli_gate_exit_codes(self) -> None:
        stale_item = self.item_path("issues", "cli-stale.md")
        _write_item(stale_item, created=_days_ago(30))

        r = subprocess.run(
            [sys.executable, str(_SCRIPTS / "premise_revalidation.py"),
             "gate", "--item", str(stale_item), "--repo", str(self.repo)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("stale_needs_revalidation", r.stdout)

        fresh_item = self.item_path("issues", "cli-fresh.md")
        _write_item(fresh_item, created=_days_ago(0))

        r2 = subprocess.run(
            [sys.executable, str(_SCRIPTS / "premise_revalidation.py"),
             "gate", "--item", str(fresh_item), "--repo", str(self.repo)],
            capture_output=True, text=True,
        )
        self.assertEqual(r2.returncode, 0)

    def test_cli_validate_requires_note(self) -> None:
        item = self.item_path("issues", "cli-validate.md")
        _write_item(item, created=_days_ago(30))

        r = subprocess.run(
            [sys.executable, str(_SCRIPTS / "premise_revalidation.py"),
             "validate", "--item", str(item)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 1)

        r2 = subprocess.run(
            [sys.executable, str(_SCRIPTS / "premise_revalidation.py"),
             "validate", "--item", str(item), "--note", "confirmed still open"],
            capture_output=True, text=True,
        )
        self.assertEqual(r2.returncode, 0)
        self.assertIn("confirmed still open", item.read_text(encoding="utf-8"))

    def test_cli_sweep_json(self) -> None:
        item = self.item_path("issues", "cli-sweep.md")
        _write_item(item, created=_days_ago(0))

        r = subprocess.run(
            [sys.executable, str(_SCRIPTS / "premise_revalidation.py"),
             "sweep", "--repo", str(self.repo), "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)
        import json
        data = json.loads(r.stdout)
        self.assertEqual(data["counts"]["fresh"], 1)

    def test_documented_cli_invocations_parse(self) -> None:
        """Every invocation shape the docs tell an agent to run must parse.

        Regression guard for a real defect caught by ground-truthing rather than
        by this suite: `gate` and `validate` were documented in
        `references/phase-5-iterate.md` and `agents/build-orchestrator.md` with
        flags argparse did not accept, so the documented command exited 2 with a
        usage error. The rest of the suite stayed green because it called the
        importable functions, never the CLI form the docs ship. Docs are a
        shipped surface; this test lints the CLI against them.
        """
        item = self.item_path("backlog", "cli-surface.md")
        _write_item(item, created=_days_ago(0))
        script = str(_SCRIPTS / "premise_revalidation.py")

        documented = [
            ["gate", "--item", str(item), "--repo", str(self.repo), "--json"],
            ["validate", "--item", str(item), "--note", "re-checked", "--json"],
            ["stale", "--repo", str(self.repo), "--window-days", "7", "--json"],
            ["sweep", "--repo", str(self.repo), "--json"],
        ]
        for argv in documented:
            with self.subTest(command=argv[0]):
                r = subprocess.run(
                    [sys.executable, script, *argv], capture_output=True, text=True
                )
                # 2 is argparse's usage error. Any other code is a real verdict.
                self.assertNotEqual(
                    r.returncode, 2,
                    f"documented invocation failed to parse: {argv}\n{r.stderr}",
                )
                self.assertNotIn("unrecognized arguments", r.stderr)


# ----------------------------------------------------------------------------
# Part 1 — anchor extraction/resolution hardening
# (real strings from the 2026-09-14 `sweep` run against RossLabs Ambient
# Agent: 32 broken_paths reported, >=26 false positives)
# ----------------------------------------------------------------------------


class TestLineLocatorSuffixStripped(BaseTestCase):
    """Real citations from the measured false-positive set: a trailing
    `:N`, `:N,M`, or `:N-M` line locator on an otherwise real path must not
    stop the anchor from resolving."""

    def test_line_locator_suffixes_do_not_convict_real_files(self) -> None:
        (self.repo / "docs").mkdir(parents=True)
        (self.repo / "docs" / "observability.md").write_text("# obs\n", encoding="utf-8")
        (self.repo / "docs" / "architecture.md").write_text("# arch\n", encoding="utf-8")
        (self.repo / "Sources" / "AmbientCore").mkdir(parents=True)
        (self.repo / "Sources" / "AmbientCore" / "WorkStore.swift").write_text(
            "// store\n", encoding="utf-8"
        )

        item = self.item_path("issues", "cites-line-locators.md")
        body = (
            "## Problem\n"
            "See `docs/observability.md:44` and `docs/architecture.md:188,193` "
            "and `Sources/AmbientCore/WorkStore.swift:4362-4407` for context.\n"
        )
        _write_item(item, created=_days_ago(0), body=body)

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["anchors"]["broken_paths"], [])
        checked = result["anchors"]["paths_checked"]
        self.assertIn("docs/observability.md", checked)
        self.assertIn("docs/architecture.md", checked)
        self.assertIn("Sources/AmbientCore/WorkStore.swift", checked)

    def test_gone_file_with_line_locator_is_still_premise_broken(self) -> None:
        """Mutation guard: stripping the line locator must not turn a
        genuinely deleted file into a false pass."""
        item = self.item_path("issues", "cites-gone-file-with-line.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nSee `gone/file.py:12` for the removed helper.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "premise_broken")
        broken = [b["path"] for b in result["anchors"]["broken_paths"]]
        self.assertIn("gone/file.py", broken)


class TestBraceGroupExpansion(BaseTestCase):
    """`.build-loop/evidence/composition-{400,1000}-before.json` — broken
    only if EVERY brace alternative is missing."""

    def test_one_alternative_present_is_not_broken(self) -> None:
        evidence_dir = self.repo / ".build-loop" / "evidence"
        evidence_dir.mkdir(parents=True)
        (evidence_dir / "composition-1000-before.json").write_text("{}\n", encoding="utf-8")

        item = self.item_path("issues", "cites-brace-group.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nSee `.build-loop/evidence/composition-{400,1000}-before.json`.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["anchors"]["broken_paths"], [])

    def test_no_alternative_present_is_broken(self) -> None:
        """Mutation guard for the same brace group: when NEITHER `-400-` nor
        `-1000-` exists, the anchor must still convict."""
        item = self.item_path("issues", "cites-brace-group-missing.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nSee `.build-loop/evidence/composition-{400,1000}-before.json`.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "premise_broken")
        broken = [b["path"] for b in result["anchors"]["broken_paths"]]
        self.assertIn(".build-loop/evidence/composition-{400,1000}-before.json", broken)


class TestNonPathTokensAreNeverExtracted(BaseTestCase):
    """Real false-positive strings that are not paths at all: a numeric
    ratio, a param/value pair, and a templated tmp-dir placeholder."""

    def test_non_path_tokens_are_rejected_by_extract_paths(self) -> None:
        self.assertEqual(pr.extract_paths("ratio was `13.6/-28.6` in the run"), [])
        self.assertEqual(pr.extract_paths("cap is `hardCeilingBytes/16`"), [])
        self.assertEqual(
            pr.extract_paths("tmp dir `/private/tmp/ambient-core-tests-<uuid>` was used"), []
        )

    def test_item_citing_only_non_path_tokens_is_not_premise_broken(self) -> None:
        item = self.item_path("issues", "cites-non-paths.md")
        _write_item(
            item,
            created=_days_ago(0),
            body=(
                "## Problem\n"
                "Ratio `13.6/-28.6`, cap `hardCeilingBytes/16`, "
                "tmp `/private/tmp/ambient-core-tests-<uuid>`.\n"
            ),
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["anchors"]["paths_checked"], [])
        self.assertEqual(result["anchors"]["broken_paths"], [])


class TestBranchNameCitationsAreNotFalsePositives(BaseTestCase):
    """Real branch-name citations from the measured run. With no
    `bl/`/`codex/` directory in the repo, these never look path-shaped and
    are filtered out before the existence check ever runs."""

    def test_branch_names_are_not_extracted_as_paths(self) -> None:
        for tok in ("bl/run-899386", "codex/storage-accounting-integration", "bl/provenance-class"):
            with self.subTest(tok=tok):
                self.assertEqual(pr.extract_paths(f"see `{tok}`", self.repo), [])

    def test_item_citing_live_branch_names_is_fresh(self) -> None:
        if not _GIT_AVAILABLE:
            self.skipTest("git not available on PATH")
        _init_git_repo(self.repo)
        (self.repo / "README.md").write_text("x\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "initial")
        for ref in ("bl/run-899386", "codex/storage-accounting-integration", "bl/provenance-class"):
            _git(self.repo, "branch", ref)
        item = self.item_path("issues", "cites-branch-names.md")
        _write_item(
            item,
            created=_days_ago(0),
            body=(
                "## Problem\nSee `bl/run-899386`, `codex/storage-accounting-integration`, "
                "and `bl/provenance-class`.\n"
            ),
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["anchors"]["broken_paths"], [])
        self.assertEqual(result["anchors"]["unresolved_refs"], [])

    def test_item_citing_deleted_branch_needs_human_recheck(self) -> None:
        """2026-09-14: a card cited `bl/open-items-closeout-20260903` after the
        branch was deleted; the branch filter must not hide it silently."""
        if not _GIT_AVAILABLE:
            self.skipTest("git not available on PATH")
        _init_git_repo(self.repo)
        (self.repo / "README.md").write_text("x\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "initial")
        item = self.item_path("issues", "cites-deleted-branch.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nLanded from `bl/open-items-closeout-20260903`.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "needs_human_recheck")
        self.assertEqual(result["anchors"]["unresolved_refs"], ["bl/open-items-closeout-20260903"])


class TestGitRefRescuesAnchor(BaseTestCase):
    """A token that DOES look path-shaped (its first segment is a real repo
    directory) but resolves as a git ref, not a file, must still come back
    as a valid anchor — never premise_broken."""

    def setUp(self) -> None:
        super().setUp()
        if not _GIT_AVAILABLE:
            self.skipTest("git not available on PATH")
        _init_git_repo(self.repo)
        (self.repo / "bl").mkdir()
        (self.repo / "bl" / "README.md").write_text("placeholder\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "initial")
        _git(self.repo, "branch", "bl/run-899386")

    def test_branch_name_under_a_real_directory_resolves_via_git_ref(self) -> None:
        item = self.item_path("issues", "cites-real-branch.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nFixed on `bl/run-899386`.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["anchors"]["broken_paths"], [])

    def test_nonexistent_ref_under_a_real_directory_is_still_broken(self) -> None:
        """Mutation guard: the git-ref rescue must not swallow a genuinely
        broken citation just because its first segment is a real directory."""
        item = self.item_path("issues", "cites-fake-branch.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nFixed on `bl/does-not-exist-anywhere`.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "premise_broken")
        broken = [b["path"] for b in result["anchors"]["broken_paths"]]
        self.assertIn("bl/does-not-exist-anywhere", broken)


class TestCrateRelativeSuffixResolution(BaseTestCase):
    """`ambient-store/src/lib.rs:1487` cited when the real file lives at
    `engine-rs/crates/ambient-store/src/lib.rs` — resolved via a unique
    tracked-file suffix match, not flagged broken or relocated."""

    def test_unique_suffix_match_resolves(self) -> None:
        real = self.repo / "engine-rs" / "crates" / "ambient-store" / "src" / "lib.rs"
        real.parent.mkdir(parents=True)
        real.write_text("// lib\n", encoding="utf-8")

        item = self.item_path("issues", "cites-crate-relative.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nSee `ambient-store/src/lib.rs:1487` for the bug.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["anchors"]["broken_paths"], [])
        self.assertEqual(result["anchors"]["relocated_paths"], [])

    def test_multiple_suffix_matches_need_human_recheck(self) -> None:
        for prefix in ("vendor/pkgA", "src/pkgB"):
            f = self.repo / prefix / "shared" / "util.py"
            f.parent.mkdir(parents=True)
            f.write_text("# util\n", encoding="utf-8")

        item = self.item_path("issues", "cites-ambiguous-suffix.md")
        _write_item(
            item,
            created=_days_ago(0),
            body="## Problem\nSee `shared/util.py` for the helper.\n",
        )

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "needs_human_recheck")
        self.assertEqual(result["anchors"]["broken_paths"], [])
        relocated = result["anchors"]["relocated_paths"]
        self.assertEqual(len(relocated), 1)
        self.assertEqual(relocated[0]["path"], "shared/util.py")
        self.assertIn("vendor/pkgA/shared/util.py", relocated[0]["candidates"])
        self.assertIn("src/pkgB/shared/util.py", relocated[0]["candidates"])


class TestAbsolutePathWithSpaceCollapsesToRepoRelative(unittest.TestCase):
    """Real title string from the measured run: an absolute in-repo path
    containing a space (`RossLabs Ambient Agent`) split at the space and
    extracted a garbage suffix-only token starting mid-component."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "RossLabs Ambient Agent"
        self.repo.mkdir()

    def test_absolute_path_with_space_resolves_to_the_real_repo_relative_path(self) -> None:
        cited = (
            self.repo / ".build-loop" / "worktrees" / "run-040937" / "App"
            / "Connections" / "WorkspaceRuntimeDrill.swift"
        )
        cited.parent.mkdir(parents=True)
        cited.write_text("// real file\n", encoding="utf-8")

        abs_str = str(self.repo.resolve())
        body = (
            "## Problem\n"
            f"A07/LLM06 {abs_str}/.build-loop/worktrees/run-040937/App/"
            "Connections/WorkspaceRuntimeDrill.swift:178 needs fixing.\n"
        )

        stripped = pr.strip_repo_abs_prefix(body, self.repo)
        self.assertNotIn(abs_str, stripped)

        paths = pr.extract_paths(stripped, self.repo)
        self.assertIn(
            ".build-loop/worktrees/run-040937/App/Connections/WorkspaceRuntimeDrill.swift",
            paths,
        )
        # The old bug produced this garbage suffix-only token instead.
        self.assertNotIn(
            "Agent/.build-loop/worktrees/run-040937/App/Connections/WorkspaceRuntimeDrill.swift",
            paths,
        )

        item = self.repo / ".build-loop" / "issues" / "cites-abs-path-with-space.md"
        item.parent.mkdir(parents=True)
        _write_item(item, created=_days_ago(0), body=body)

        result = pr.gate(item, repo=self.repo, window_days=7)

        self.assertEqual(result["verdict"], "fresh")
        self.assertEqual(result["anchors"]["broken_paths"], [])


# ----------------------------------------------------------------------------
# Part 2 — `citations` subcommand
# ----------------------------------------------------------------------------


class TestSlashOnlyTokensDoNotCrash(BaseTestCase):
    """2026-09-14: sweep on a real backlog crashed with IndexError when a
    body carried a bare slash token; every token shape must return a verdict."""

    def test_slash_only_tokens_are_not_paths(self) -> None:
        for body in ("see `/` here", "ratio `//` and / alone", "`///`"):
            self.assertEqual(pr.extract_paths(body, self.repo), [])


class TestCitationsCommand(BaseTestCase):
    def _run(self, evidence: list[dict]) -> dict:
        payload = [{"lane": "review", "evidence": evidence}]
        input_path = self.repo / "evidence.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        return pr.citations(self.repo, input_path)

    def test_missing_file_reported(self) -> None:
        result = self._run([{"kind": "cited", "ref": "gone/file.py:12"}])

        self.assertFalse(result["ok"])
        self.assertEqual(result["items"][0]["status"], "missing_file")
        self.assertEqual(result["items"][0]["ref"], "gone/file.py:12")

    def test_line_out_of_range_reported(self) -> None:
        (self.repo / "small.py").write_text("line1\nline2\n", encoding="utf-8")

        result = self._run([{"kind": "cited", "ref": "small.py:50"}])

        self.assertFalse(result["ok"])
        self.assertEqual(result["items"][0]["status"], "line_out_of_range")

    def test_expect_present_at_cited_line_passes(self) -> None:
        """Mutation guard companion #1: `expect` genuinely at the cited
        line must pass, not just the absent case."""
        lines = [f"line {i}" for i in range(1, 21)]
        lines[9] = "TARGET_STRING here"  # line 10
        (self.repo / "target.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self._run(
            [{"kind": "cited", "ref": "target.py:10", "expect": "TARGET_STRING"}]
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["items"][0]["status"], "ok")
        self.assertIsNone(result["items"][0]["nearest_line"])

    def test_expect_43_lines_away_fails_with_correct_nearest_line(self) -> None:
        """Mutation guard companion #2: the same `expect` string present
        only 43 lines from the citation must fail with the real line
        number, not pass silently or report the wrong line."""
        lines = [f"line {i}" for i in range(1, 61)]
        lines[49] = "TARGET_STRING here"  # line 50
        (self.repo / "target.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self._run(
            [{"kind": "cited", "ref": "target.py:7", "expect": "TARGET_STRING"}]
        )

        self.assertFalse(result["ok"])
        item = result["items"][0]
        self.assertEqual(item["status"], "expect_not_found")
        self.assertEqual(item["nearest_line"], 50)

    def test_expect_not_found_anywhere_reports_null_nearest_line(self) -> None:
        (self.repo / "target.py").write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = self._run(
            [{"kind": "cited", "ref": "target.py:2", "expect": "NEVER_PRESENT"}]
        )

        self.assertFalse(result["ok"])
        item = result["items"][0]
        self.assertEqual(item["status"], "expect_not_found")
        self.assertIsNone(item["nearest_line"])

    def test_executed_tag_on_path_line_citation_is_flagged(self) -> None:
        (self.repo / "target.py").write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = self._run([{"kind": "executed", "ref": "target.py:2"}])

        self.assertFalse(result["ok"])
        item = result["items"][0]
        self.assertTrue(item["executed_tag_on_citation"])
        # A failing item must never read status "ok" (2026-09-14: a real lane
        # mis-tag surfaced as status ok while the run failed).
        self.assertEqual(item["status"], "executed_tag_on_citation")

    def test_executed_command_is_not_a_citation_and_passes(self) -> None:
        result = self._run(
            [{"kind": "executed", "ref": "pytest scripts/test_foo.py -q"}]
        )

        self.assertTrue(result["ok"])
        item = result["items"][0]
        self.assertEqual(item["status"], "not_a_citation")
        self.assertFalse(item["executed_tag_on_citation"])

    def test_absolute_and_relative_refs_both_resolve(self) -> None:
        target = self.repo / "abs.py"
        target.write_text("x\n", encoding="utf-8")

        result = self._run(
            [
                {"kind": "cited", "ref": f"{target}:1"},
                {"kind": "cited", "ref": "abs.py:1"},
            ]
        )

        self.assertTrue(result["ok"])
        self.assertTrue(all(it["status"] == "ok" for it in result["items"]))

    def test_cli_citations_exit_codes(self) -> None:
        (self.repo / "small.py").write_text("line1\nline2\n", encoding="utf-8")
        good = self.repo / "good.json"
        good.write_text(
            json.dumps([{"lane": "review", "evidence": [{"kind": "cited", "ref": "small.py:1"}]}]),
            encoding="utf-8",
        )
        bad = self.repo / "bad.json"
        bad.write_text(
            json.dumps([{"lane": "review", "evidence": [{"kind": "cited", "ref": "gone.py:1"}]}]),
            encoding="utf-8",
        )

        r_good = subprocess.run(
            [sys.executable, str(_SCRIPTS / "premise_revalidation.py"),
             "citations", "--repo", str(self.repo), "--input", str(good)],
            capture_output=True, text=True,
        )
        self.assertEqual(r_good.returncode, 0)

        r_bad = subprocess.run(
            [sys.executable, str(_SCRIPTS / "premise_revalidation.py"),
             "citations", "--repo", str(self.repo), "--input", str(bad)],
            capture_output=True, text=True,
        )
        self.assertEqual(r_bad.returncode, 1)
        self.assertIn("missing_file", r_bad.stdout)


if __name__ == "__main__":
    unittest.main()
