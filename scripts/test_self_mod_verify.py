#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for self_mod_verify.py.  Run: uv run pytest scripts/test_self_mod_verify.py -q"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "self_mod_verify.py"


def _payload(stdout: str) -> dict:
    """Parse the result JSON out of stdout.

    stdout carries the indent=2 JSON object followed (on a revert) by the
    human-readable `reverted <path> -> blob <sha>` recovery lines, so a bare
    json.loads over the whole stream would choke on the trailing text.
    """
    obj, _end = json.JSONDecoder().raw_decode(stdout[stdout.index("{"):])
    return obj


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=cwd,
    )


def _init_git_repo(d: Path) -> None:
    """Initialise a minimal git repo in d with an initial commit."""
    subprocess.run(["git", "-C", str(d), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "config", "user.email", "test@test.local"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    # Need at least one commit so HEAD exists
    dummy = d / "README.txt"
    dummy.write_text("test repo\n")
    subprocess.run(["git", "-C", str(d), "add", "README.txt"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-m", "init"],
                   check=True, capture_output=True)


def _write_passing_test(scripts_dir: Path, name: str = "test_sample.py") -> Path:
    p = scripts_dir / name
    p.write_text(
        "def test_always_passes():\n"
        "    assert 1 + 1 == 2\n"
    )
    return p


def _write_failing_test(scripts_dir: Path, name: str = "test_fail.py") -> Path:
    p = scripts_dir / name
    p.write_text(
        "def test_always_fails():\n"
        "    assert False, 'intentional failure'\n"
    )
    return p


# ---------------------------------------------------------------------------
# Original tests (preserved)
# ---------------------------------------------------------------------------

class TestVerdictPass(unittest.TestCase):
    """A repo with only a passing test → verdict pass, exit 0."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _write_passing_test(scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_passing_suite_verdict_pass(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "pass")
        self.assertGreater(payload["passed"], 0)
        self.assertEqual(payload["failed"], 0)
        self.assertFalse(payload["reverted"])

    def test_json_shape_complete(self) -> None:
        """JSON output has exactly the expected keys; no meta_modification / meta_files."""
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        payload = _payload(r.stdout)
        expected_keys = {
            "scope", "ran", "passed", "failed", "failed_tests", "reverted",
            "verdict", "timed_out", "errors", "effective_scope", "error_reason",
        }
        for key in expected_keys:
            self.assertIn(key, payload, f"missing key {key!r}")
        # Removed keys must not be present
        self.assertNotIn("meta_modification", payload)
        self.assertNotIn("meta_files", payload)
        self.assertIsInstance(payload["ran"], list)
        self.assertIsInstance(payload["failed_tests"], list)
        self.assertIsInstance(payload["timed_out"], bool)
        # error_reason is None on a clean pass
        self.assertIsNone(payload["error_reason"])


class TestVerdictFail(unittest.TestCase):
    """A repo with a failing test → verdict fail, exit 1."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _write_failing_test(self.scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_failing_suite_verdict_fail(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        self.assertEqual(r.returncode, 1, msg=f"Expected exit 1; stderr: {r.stderr}")
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "fail")
        self.assertGreater(payload["failed"], 0)

    def test_failing_suite_populates_failed_tests(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        payload = _payload(r.stdout)
        # failed_tests should name the failing test
        self.assertGreater(len(payload["failed_tests"]), 0,
                           msg="failed_tests should be non-empty on failure")


class TestAutoRevert(unittest.TestCase):
    """--auto-revert with a failing test restores the changed file."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_auto_revert_restores_file(self) -> None:
        # Write a good implementation file and commit it
        impl = self.scripts_dir / "mymod.py"
        impl.write_text("ORIGINAL = True\n")
        subprocess.run(
            ["git", "-C", str(self.workdir), "add", str(impl)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.workdir), "commit", "-m", "add impl"],
            check=True, capture_output=True,
        )

        # Now create a failing test that will trigger revert
        _write_failing_test(self.scripts_dir, "test_mymod.py")
        subprocess.run(
            ["git", "-C", str(self.workdir), "add", str(self.scripts_dir / "test_mymod.py")],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.workdir), "commit", "-m", "add failing test"],
            check=True, capture_output=True,
        )

        # Modify the implementation file (this is the "self-modification" we want reverted)
        impl.write_text("ORIGINAL = False  # broken\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(impl),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(r.returncode, 1, msg=f"Expected exit 1: {r.stderr}")
        self.assertEqual(payload["verdict"], "fail")
        self.assertTrue(payload["reverted"],
                        msg="reverted should be True after auto-revert on failure")
        # The file should be back to its committed state
        content = impl.read_text()
        self.assertIn("ORIGINAL = True", content,
                      msg="File should be restored to original content after revert")

    def test_auto_revert_without_changed_files_is_a_usage_error(self) -> None:
        """(a) --auto-revert with no --changed-files exits non-zero, reverts nothing,
        and leaves a PLANTED dirty file byte-identical.

        Regression BUIL-TOOLING-m2b7cts2d0gqn1d1j6q56: the gate used to derive the
        revert set from git status and `git restore --staged --worktree` every dirty
        tracked file in the checkout, destroying a peer session's unstaged edits.
        """
        # A committed file that a CONCURRENT session has since edited (unstaged),
        # carrying a mapped test that FAILS — the exact 2026-09-12 shape: the peer's
        # work-in-progress pulls its own test into the derived scope, that test
        # fails, and the revert then deletes the edits it just judged.
        peer = self.scripts_dir / "peer_wip.py"
        peer.write_text("PEER = 'committed'\n")
        _write_failing_test(self.scripts_dir, "test_peer_wip.py")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        peer_wip = "PEER = 'uncommitted work from another session'\n"
        peer.write_text(peer_wip)

        # --scope auto is the path that used to derive the change set from git.
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertNotEqual(r.returncode, 0,
                            msg="an unsafe --auto-revert invocation must not exit 0")
        self.assertEqual(r.returncode, 2, msg=f"expected usage error exit 2: {r.stderr}")
        self.assertEqual(payload["verdict"], "error", msg=payload)
        self.assertEqual(payload["error_reason"], "auto_revert_requires_changed_files")
        self.assertFalse(payload["reverted"],
                         msg="reverted must be False when no --changed-files given")
        self.assertEqual(payload["reverted_blobs"], [])
        # The gate ran NOTHING and touched NOTHING.
        self.assertEqual(payload["ran"], [])
        self.assertEqual(peer.read_text(), peer_wip,
                         msg="a dirty file this run never listed must be untouched")

    def test_auto_revert_prints_recoverable_blob(self) -> None:
        """(b) A reverted file's PRE-REVERT bytes are recoverable byte-identical
        from the printed blob sha."""
        impl = self.scripts_dir / "mymod.py"
        impl.write_text("ORIGINAL = True\n")
        _write_failing_test(self.scripts_dir, "test_mymod.py")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)

        # Snapshot BEFORE the edit → impl is clean, so it is absent from the baseline.
        baseline = self.workdir / "baseline.json"
        snap = _run(["snapshot", "--workdir", str(self.workdir),
                     "--out", str(baseline), "--json"])
        self.assertEqual(snap.returncode, 0, msg=snap.stderr)

        pre_revert = "ORIGINAL = False  # this run's broken self-mod\n"
        impl.write_text(pre_revert)

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/mymod.py",
            "--baseline", str(baseline),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "fail", msg=payload)
        self.assertTrue(payload["reverted"], msg=payload)
        # The file went back to HEAD (it was clean at snapshot time).
        self.assertIn("ORIGINAL = True", impl.read_text())

        # ...and the destroyed content is addressable.
        blobs = {e["path"]: e for e in payload["reverted_blobs"]}
        self.assertIn("scripts/mymod.py", blobs, msg=payload["reverted_blobs"])
        sha = blobs["scripts/mymod.py"]["blob"]
        self.assertTrue(sha, msg="a reverted file must carry a blob sha")
        self.assertIn(f"reverted scripts/mymod.py -> blob {sha}", r.stdout,
                      msg="the path -> blob sha line must be on stdout")
        # Execute the emitted command rather than string-matching it: the
        # command IS the safety property, so a shell-broken one (unquoted path,
        # wrong cat-file form) must fail the test, not satisfy an assertEqual.
        subprocess.run(
            blobs["scripts/mymod.py"]["recover"],
            shell=True, cwd=str(self.workdir), check=True, capture_output=True,
        )
        self.assertEqual(impl.read_text(), pre_revert,
                         msg="the emitted recover command must restore the exact bytes")
        # The backup is anchored under a ref, so `git gc --prune` cannot reclaim it.
        ref = blobs["scripts/mymod.py"].get("ref")
        self.assertTrue(ref, msg=f"backup blob must be anchored: {blobs}")
        resolved = subprocess.run(
            ["git", "-C", str(self.workdir), "rev-parse", ref],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(resolved, sha)

    def test_pre_existing_dirt_restores_to_baseline_not_head(self) -> None:
        """(c) A file dirty BEFORE the run, listed in --changed-files with a
        baseline, is restored to the baseline content, not to HEAD."""
        impl = self.scripts_dir / "shared.py"
        impl.write_text("VALUE = 'head'\n")
        _write_failing_test(self.scripts_dir, "test_shared.py")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)

        # Pre-existing dirt (another session's unstaged edit), THEN the snapshot.
        pre_run = "VALUE = 'peer work in progress'\n"
        impl.write_text(pre_run)
        baseline = self.workdir / "baseline.json"
        snap = _run(["snapshot", "--workdir", str(self.workdir),
                     "--out", str(baseline), "--json"])
        self.assertEqual(snap.returncode, 0, msg=snap.stderr)
        self.assertIn("scripts/shared.py", _payload(snap.stdout)["files"])

        # This run then edits the same file and the suite fails.
        impl.write_text("VALUE = 'this run, broken'\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/shared.py",
            "--baseline", str(baseline),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "fail", msg=payload)
        self.assertTrue(payload["reverted"], msg=payload)
        self.assertEqual(impl.read_text(), pre_run,
                         msg="pre-existing dirt must be restored to baseline, not HEAD")
        self.assertNotIn("VALUE = 'head'", impl.read_text())
        self.assertEqual(payload["baseline_used"], str(baseline))

    def test_unlisted_dirty_file_never_touched_on_failure(self) -> None:
        """(d) A dirty file NOT in --changed-files is untouched even when the
        suite fails and the listed file is reverted."""
        mine = self.scripts_dir / "mine.py"
        mine.write_text("MINE = 'head'\n")
        theirs = self.scripts_dir / "theirs.py"
        theirs.write_text("THEIRS = 'head'\n")
        _write_failing_test(self.scripts_dir, "test_mine.py")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)

        mine.write_text("MINE = 'broken by this run'\n")
        peer_wip = "THEIRS = 'uncommitted peer work'\n"
        theirs.write_text(peer_wip)

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/mine.py",
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "fail", msg=payload)
        self.assertTrue(payload["reverted"], msg=payload)
        self.assertIn("MINE = 'head'", mine.read_text())
        self.assertEqual(theirs.read_text(), peer_wip,
                         msg="an unlisted dirty file must never be reverted")
        touched = {e["path"] for e in payload["reverted_blobs"]}
        self.assertNotIn("scripts/theirs.py", touched, msg=payload["reverted_blobs"])

    def _commit_impl_and_failing_test(self, name: str = "shared.py") -> Path:
        impl = self.scripts_dir / name
        impl.write_text("VALUE = 'head'\n")
        _write_failing_test(self.scripts_dir, f"test_{name}")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        return impl

    def test_unusable_baseline_refuses_rather_than_reverting_to_head(self) -> None:
        """A --baseline that was REQUESTED but cannot be loaded must refuse.

        Degrading silently to the no-baseline path is strictly worse than never
        passing the flag: the caller believes attribution is on, and the file
        gets reverted to HEAD anyway — exactly the loss the flag prevents.
        """
        impl = self._commit_impl_and_failing_test()
        pre_run = "VALUE = 'peer work in progress'\n"
        impl.write_text(pre_run)

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/shared.py",
            "--baseline", str(self.workdir / "does-not-exist.json"),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "fail", msg=payload)
        self.assertFalse(payload["reverted"], msg=payload)
        self.assertEqual(payload["reverted_blobs"], [])
        self.assertEqual(impl.read_text(), pre_run,
                         msg="an unusable baseline must not fall back to a HEAD revert")
        joined = "\n".join(payload["errors"])
        self.assertIn("--baseline unreadable", joined, msg=joined)
        self.assertIn("refusing to revert", joined, msg=joined)

    def test_stale_baseline_is_refused(self) -> None:
        """A baseline recorded against a different HEAD would restore bytes that
        predate the intervening commits. Refuse it."""
        impl = self._commit_impl_and_failing_test()
        impl.write_text("VALUE = 'dirty at snapshot'\n")
        baseline = self.workdir / "baseline.json"
        _run(["snapshot", "--workdir", str(self.workdir), "--out", str(baseline), "--json"])

        # HEAD moves after the snapshot.
        (self.scripts_dir / "unrelated.py").write_text("X = 1\n")
        subprocess.run(["git", "-C", str(self.workdir), "add", "scripts/unrelated.py"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "move HEAD"],
                       check=True, capture_output=True)

        current = "VALUE = 'this run'\n"
        impl.write_text(current)
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/shared.py",
            "--baseline", str(baseline),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertFalse(payload["reverted"], msg=payload)
        self.assertEqual(impl.read_text(), current)
        self.assertIn("--baseline stale", "\n".join(payload["errors"]))

    def test_baseline_without_a_recorded_head_is_refused(self) -> None:
        """A baseline that cannot prove which commit it describes is not trusted.

        Accepting a null/missing head let a foreign or undated snapshot through
        on a technicality; the identity check has to fail closed.
        """
        impl = self._commit_impl_and_failing_test()
        pre_run = "VALUE = 'peer work'\n"
        impl.write_text(pre_run)
        baseline = self.workdir / "baseline.json"
        baseline.write_text(json.dumps({
            "schema_version": 1, "head": None, "workdir": str(self.workdir),
            "files": {"scripts/shared.py": {"blob": "0" * 40, "deleted": False}}}))

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/shared.py",
            "--baseline", str(baseline),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertFalse(payload["reverted"], msg=payload)
        self.assertEqual(impl.read_text(), pre_run)
        self.assertIn("--baseline unverifiable", "\n".join(payload["errors"]))

    def test_foreign_baseline_workdir_is_refused(self) -> None:
        """Blob shas from a different checkout describe different content."""
        impl = self._commit_impl_and_failing_test()
        pre_run = "VALUE = 'peer work'\n"
        impl.write_text(pre_run)
        head = subprocess.run(
            ["git", "-C", str(self.workdir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        baseline = self.workdir / "baseline.json"
        baseline.write_text(json.dumps({
            "schema_version": 1, "head": head, "workdir": "/some/other/repo",
            "files": {"scripts/shared.py": {"blob": "0" * 40, "deleted": False}}}))

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/shared.py",
            "--baseline", str(baseline),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertFalse(payload["reverted"], msg=payload)
        self.assertEqual(impl.read_text(), pre_run)
        self.assertIn("--baseline foreign", "\n".join(payload["errors"]))

    def test_non_blob_sha_in_baseline_does_not_write_garbage(self) -> None:
        """`cat-file -p` pretty-prints a commit or tree and would write that
        metadata over the file. The typed `cat-file blob` form must reject it."""
        impl = self._commit_impl_and_failing_test()
        pre_run = "VALUE = 'peer work'\n"
        impl.write_text(pre_run)
        head = subprocess.run(
            ["git", "-C", str(self.workdir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        baseline = self.workdir / "baseline.json"
        # A hand-edited/foreign baseline pointing at a COMMIT, not a blob.
        baseline.write_text(json.dumps({
            "schema_version": 1, "head": head, "files": {
                "scripts/shared.py": {"blob": head, "deleted": False}}}))

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/shared.py",
            "--baseline", str(baseline),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(impl.read_text(), pre_run,
                         msg="a non-blob baseline sha must never be written to the file")
        self.assertFalse(payload["reverted"], msg=payload)
        self.assertIn("baseline restore failed", "\n".join(payload["errors"]))

    def test_baseline_deletion_is_not_resurrected(self) -> None:
        """A file DELETED before the run must stay deleted, not come back from HEAD."""
        impl = self._commit_impl_and_failing_test()
        impl.unlink()
        baseline = self.workdir / "baseline.json"
        snap = _run(["snapshot", "--workdir", str(self.workdir),
                     "--out", str(baseline), "--json"])
        recorded = _payload(snap.stdout)["files"]["scripts/shared.py"]
        self.assertTrue(recorded["deleted"], msg=recorded)

        # This run recreates it (the change under test), then the suite fails.
        impl.write_text("VALUE = 'recreated by this run'\n")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/shared.py",
            "--baseline", str(baseline),
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertTrue(payload["reverted"], msg=payload)
        self.assertFalse(impl.exists(),
                         msg="a pre-run deletion must be re-applied, not undone")

    def test_glob_pathspec_cannot_reach_an_unlisted_file(self) -> None:
        """A caller path is a LITERAL path, never a git pathspec.

        `git restore -- 'scripts/*'` expands the glob and restores every match.
        Verified destructive on 2026-09-12: without --literal-pathspecs it wiped
        the uncommitted edits of two files when one was listed. That is the same
        defect class as the original incident, reached through a different door.
        """
        import importlib.util  # noqa: PLC0415 - local to this regression test

        spec = importlib.util.spec_from_file_location("smv_glob", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        peer = self.scripts_dir / "peer.py"
        peer.write_text("PEER = 'head'\n")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        peer_wip = "PEER = 'uncommitted peer work'\n"
        peer.write_text(peer_wip)

        errors: list[str] = []
        blobs: list[dict] = []
        # Layer 1: exact-membership in _partition_tracked keeps a glob out of the
        # revert set entirely.
        mod._revert_files(self.workdir, ["scripts/*"], errors, reverted_blobs=blobs)
        self.assertEqual(peer.read_text(), peer_wip,
                         msg="a glob pathspec must never reach an unlisted file")
        self.assertEqual(blobs, [])

        # Layer 2: even handed straight to the restore helper, the glob must be
        # treated as a literal filename and match nothing. This is the layer
        # --literal-pathspecs provides; without it git expands the glob here and
        # wipes peer.py's uncommitted edit.
        mod._restore_to_head(self.workdir, "scripts/*", errors)
        self.assertEqual(peer.read_text(), peer_wip,
                         msg="git must treat the caller path as literal, not as a glob")

    def test_symlink_is_not_followed_to_an_unlisted_target(self) -> None:
        """Listing a tracked symlink must not revert its referent.

        Path.resolve() normalises `alias.py` to `target.py`, which would back up
        and restore a file the caller never named.
        """
        import importlib.util  # noqa: PLC0415 - local to this regression test

        spec = importlib.util.spec_from_file_location("smv_link", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        target = self.scripts_dir / "target.py"
        target.write_text("T = 'head'\n")
        alias = self.scripts_dir / "alias.py"
        alias.symlink_to("target.py")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        target_wip = "T = 'uncommitted work on the TARGET'\n"
        target.write_text(target_wip)

        errors: list[str] = []
        blobs: list[dict] = []
        mod._revert_files(self.workdir, ["scripts/alias.py"], errors, reverted_blobs=blobs)
        self.assertEqual(target.read_text(), target_wip,
                         msg="the symlink's referent was never listed and must not be reverted")
        self.assertNotIn("scripts/target.py", {b["path"] for b in blobs})

    def test_content_changed_after_backup_is_not_destroyed(self) -> None:
        """If a peer rewrites the file between backup and restore, skip it.

        Restoring anyway destroys bytes no backup holds, which is precisely the
        unrecoverable-loss shape this whole change exists to remove.
        """
        import importlib.util  # noqa: PLC0415 - local to this regression test

        spec = importlib.util.spec_from_file_location("smv_race", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        impl = self.scripts_dir / "raced.py"
        impl.write_text("V = 'head'\n")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        impl.write_text("V = 'this run'\n")

        # Simulate the peer write landing between the backup and the restore.
        real = mod._hash_object
        seen: list[str] = []
        peer_write = "V = 'PEER WROTE THIS AFTER THE BACKUP'\n"

        def racing(workdir, rel):
            result = real(workdir, rel)
            if rel == "scripts/raced.py" and not seen:
                seen.append(rel)
                impl.write_text(peer_write)
            return result

        mod._hash_object = racing
        errors: list[str] = []
        blobs: list[dict] = []
        reverted = mod._revert_files(
            self.workdir, ["scripts/raced.py"], errors, reverted_blobs=blobs,
        )
        self.assertFalse(reverted, msg=errors)
        self.assertEqual(impl.read_text(), peer_write,
                         msg="content written after the backup must not be destroyed")
        self.assertIn("changed after the backup", "\n".join(errors))

    def test_failed_index_removal_is_reported_not_swallowed(self) -> None:
        """`git rm --cached` failing must surface, not read as a clean deletion.

        Ignoring its return code let the caller report reverted:true while the
        index still held the file — a success claim over a half-applied change.
        """
        import importlib.util  # noqa: PLC0415 - local to this regression test

        spec = importlib.util.spec_from_file_location("smv_rmfail", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # A file present on disk but absent from the index: `git rm --cached`
        # exits non-zero with "did not match any files".
        stray = self.scripts_dir / "not_in_index.py"
        stray.write_text("X = 1\n")

        err = mod._delete_worktree_file(self.workdir, "scripts/not_in_index.py")
        self.assertIsNotNone(err, msg="a failed index removal must return an error")
        self.assertIn("git rm --cached", err)
        self.assertIn("the index still holds it", err)

    def test_recover_command_round_trips_a_path_with_a_space(self) -> None:
        """The emitted recovery command must WORK for a path containing a space.

        Unquoted, `git cat-file blob <sha> > scripts/with space.py` redirects to
        `scripts/with` and passes `space.py` as an argument, so the operator's
        recovery silently writes the wrong file and the content stays lost.
        Driving _revert_files directly keeps the fixture off pytest's collector,
        which cannot import a test module whose filename contains a space.
        """
        import importlib.util  # noqa: PLC0415 - local to this regression test

        spec = importlib.util.spec_from_file_location("smv_quoting", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        spaced = self.scripts_dir / "with space.py"
        spaced.write_text("V = 'head'\n")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        pre_revert = "V = 'this run, about to be reverted'\n"
        spaced.write_text(pre_revert)

        errors: list[str] = []
        blobs: list[dict] = []
        self.assertTrue(mod._revert_files(
            self.workdir, ["scripts/with space.py"], errors, reverted_blobs=blobs,
        ), msg=errors)
        self.assertEqual(spaced.read_text(), "V = 'head'\n")

        entry = next(e for e in blobs if e["path"] == "scripts/with space.py")
        subprocess.run(entry["recover"], shell=True, cwd=str(self.workdir),
                       check=True, capture_output=True)
        self.assertEqual(spaced.read_text(), pre_revert,
                         msg=f"recover command did not round-trip: {entry['recover']!r}")

    def test_backup_failure_abandons_the_whole_revert(self) -> None:
        """'A revert that cannot write the blob does not proceed' — including for
        the files whose backup DID succeed."""
        import importlib.util  # noqa: PLC0415 - local to this regression test

        spec = importlib.util.spec_from_file_location("smv_backupfail", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        a = self.scripts_dir / "a.py"
        b = self.scripts_dir / "b.py"
        a.write_text("A = 'head'\n")
        b.write_text("B = 'head'\n")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        a_dirty, b_dirty = "A = 'edited'\n", "B = 'edited'\n"
        a.write_text(a_dirty)
        b.write_text(b_dirty)

        calls: list[str] = []
        real = mod._hash_object

        def flaky(workdir, rel):
            calls.append(rel)
            if rel == "scripts/b.py":
                return None, f"blob backup failed for {rel}: planted"
            return real(workdir, rel)

        mod._hash_object = flaky
        errors: list[str] = []
        blobs: list[dict] = []
        reverted = mod._revert_files(
            self.workdir, ["scripts/a.py", "scripts/b.py"], errors, reverted_blobs=blobs,
        )
        self.assertFalse(reverted, msg=errors)
        self.assertEqual(blobs, [], msg="no blob is reported when the revert is abandoned")
        self.assertEqual(a.read_text(), a_dirty,
                         msg="a file whose backup SUCCEEDED must still not be reverted")
        self.assertEqual(b.read_text(), b_dirty)
        self.assertIn("revert ABANDONED", "\n".join(errors))

    def test_snapshot_records_quoted_and_spaced_paths(self) -> None:
        """snapshot must record a dirty file whose path git would QUOTE.

        `git diff --name-only` escapes non-ASCII paths as "caf\\303\\251.py", so a
        newline-split reader silently drops them from the baseline — and a file
        missing from the baseline is treated as clean and reverted to HEAD.
        """
        weird = self.scripts_dir / "café.py"
        spaced = self.scripts_dir / "with space.py"
        weird.write_text("A = 1\n")
        spaced.write_text("B = 1\n")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        weird.write_text("A = 2  # pre-run dirt\n")
        spaced.write_text("B = 2  # pre-run dirt\n")

        snap = _run(["snapshot", "--workdir", str(self.workdir), "--json"])
        self.assertEqual(snap.returncode, 0, msg=snap.stderr)
        recorded = _payload(snap.stdout)["files"]
        self.assertIn("scripts/café.py", recorded, msg=sorted(recorded))
        self.assertIn("scripts/with space.py", recorded, msg=sorted(recorded))

    def test_staged_dirt_without_baseline_is_refused(self) -> None:
        """Without a --baseline the gate cannot attribute STAGED dirt to this run,
        so it refuses to revert that file and says so."""
        impl = self.scripts_dir / "staged.py"
        impl.write_text("VALUE = 'head'\n")
        _write_failing_test(self.scripts_dir, "test_staged.py")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)

        staged_content = "VALUE = 'staged by someone'\n"
        impl.write_text(staged_content)
        subprocess.run(["git", "-C", str(self.workdir), "add", "scripts/staged.py"],
                       check=True, capture_output=True)

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", "scripts/staged.py",
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "fail", msg=payload)
        self.assertFalse(payload["reverted"], msg=payload)
        self.assertEqual(impl.read_text(), staged_content)
        joined = "\n".join(payload["errors"])
        self.assertIn("refusing to revert scripts/staged.py", joined, msg=joined)
        self.assertIn("no --baseline recorded", joined, msg=joined)


class TestScopeChanged(unittest.TestCase):
    """--scope changed only runs test_foo.py for changed scripts/foo.py."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scope_changed_maps_impl_to_test(self) -> None:
        # Write a passing test for impl.py and a failing test for other.py
        impl = self.scripts_dir / "impl.py"
        impl.write_text("pass\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")
        _write_failing_test(self.scripts_dir, "test_other.py")
        # Commit all new files so git HEAD is valid
        subprocess.run(
            ["git", "-C", str(self.workdir), "add", str(self.scripts_dir)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.workdir), "commit", "-m", "add test files"],
            check=True, capture_output=True,
        )

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = _payload(r.stdout)
        # Only test_impl.py runs → passes; test_other.py is NOT run
        self.assertEqual(payload["verdict"], "pass",
                         msg="Only test_impl.py should run; test_other.py must be excluded")
        # Confirm the right test was in ran[]
        ran_names = [Path(f).name for f in payload["ran"]]
        self.assertIn("test_impl.py", ran_names)
        self.assertNotIn("test_other.py", ran_names)

    def test_scope_changed_no_mapped_test_gives_no_tests(self) -> None:
        """An impl file with no matching test file → verdict no_tests, exit 3.

        no_tests is NOT green: the gate exercised nothing, so it must not read as
        pass. Exit 3 (inconclusive) is distinct from a real fail (1) or error (2).
        """
        impl = self.scripts_dir / "orphan.py"
        impl.write_text("pass\n")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests")
        self.assertEqual(r.returncode, 3,
                         msg="no_tests must be non-green (exit 3), never exit 0")

    def test_changed_test_named_markdown_doc_is_not_run_as_pytest(self) -> None:
        """A changed doc whose basename starts with `test_` (the per-script doc
        convention, e.g. docs/scripts/test_foo.md) must NOT be handed to pytest.

        Regression: pytest exits 4 on a .md path, which the gate surfaced as
        verdict=error and falsely blocked a docs-only commit. A non-.py change
        with no sibling .py test maps to no target → verdict no_tests (exit 3,
        inconclusive — the doc change is not a green pass, but it is not a real
        test failure/error either)."""
        docs = self.workdir / "docs" / "scripts"
        docs.mkdir(parents=True)
        doc = docs / "test_thing.md"
        doc.write_text("# test_thing.py\n\nDocumentation, not a test.\n")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(doc),
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests", msg=f"stderr={r.stderr!r}")
        self.assertEqual(r.returncode, 3, msg=f"stderr={r.stderr!r}")
        self.assertEqual(payload["ran"], [])


    def test_mirror_copy_under_a_generated_tree_is_never_a_gate_target(self) -> None:
        """A test file under a generated mirror tree must not be collected.

        Regression: a generated mirror of `scripts/` gives every one of its
        `test_*.py` files a basename shared with its source twin. When git
        reported both copies as changed, the gate handed both to pytest, which
        aborted collection with "import file mismatch" and surfaced verdict=error
        — the gate failing on a change that was itself green. Discovery now drops
        anything under a generated tree, so only the `scripts/` copy runs.
        """
        scripts = self.workdir / "scripts"
        scripts.mkdir(exist_ok=True)
        source_test = scripts / "test_mirrored_thing.py"
        source_test.write_text("def test_ok(): assert True\n")
        mirror = self.workdir / "dist" / "codex" / "scripts"
        mirror.mkdir(parents=True)
        mirror_test = mirror / "test_mirrored_thing.py"
        mirror_test.write_text("def test_ok(): assert True\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(source_test), str(mirror_test),
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "pass", msg=f"stderr={r.stderr!r}")
        self.assertEqual(
            [Path(p).resolve() for p in payload["ran"]],
            [source_test.resolve()],
            msg=f"mirror copy leaked into gate targets: {payload['ran']}",
        )

    def test_mirror_copy_alone_maps_to_no_gate_target(self) -> None:
        """A change confined to the generated mirror has nothing to verify."""
        mirror = self.workdir / "dist" / "codex" / "scripts"
        mirror.mkdir(parents=True)
        mirror_test = mirror / "test_only_in_mirror.py"
        mirror_test.write_text("def test_ok(): assert True\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(mirror_test),
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests", msg=f"stderr={r.stderr!r}")
        self.assertEqual(payload["ran"], [])


class TestNoGateTreePredicate(unittest.TestCase):
    """`_in_non_gate_tree` classifies mirror trees without touching the disk."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_every_named_tree_is_excluded(self) -> None:
        for tree in self.mod._NON_GATE_TREES:
            path = self.workdir / tree / "scripts" / "test_x.py"
            self.assertTrue(
                self.mod._in_non_gate_tree(path, self.workdir), tree
            )

    def test_ordinary_scripts_path_is_not_excluded(self) -> None:
        path = self.workdir / "scripts" / "test_x.py"
        self.assertFalse(self.mod._in_non_gate_tree(path, self.workdir))

    def test_substring_match_does_not_falsely_exclude(self) -> None:
        path = self.workdir / "scripts" / "distribution" / "test_x.py"
        self.assertFalse(self.mod._in_non_gate_tree(path, self.workdir))

    def test_workdir_inside_a_mirror_tree_still_gates_its_own_scripts(self) -> None:
        """Running the gate FROM inside the artifact must not exclude everything."""
        inner = self.workdir / "dist" / "codex"
        path = inner / "scripts" / "test_x.py"
        self.assertFalse(self.mod._in_non_gate_tree(path, inner))


class TestNoPytest(unittest.TestCase):
    """When no tests are found, verdict = no_tests, exit 3 (inconclusive)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        # Scripts dir with a test file, but we'll point to an empty workdir
        # We can't remove pytest from the system — instead test the logic via
        # a workdir with no scripts/ dir (no test files found → no_tests)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_scripts_dir_gives_no_tests(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests")
        self.assertEqual(r.returncode, 3,
                         msg=f"no_tests must be non-green (exit 3); stderr: {r.stderr}")
        self.assertEqual(payload["ran"], [])


# ---------------------------------------------------------------------------
# Tests: --scope auto (file-count / core-path rules only)
# ---------------------------------------------------------------------------

class TestScopeAuto(unittest.TestCase):
    """Tests for --scope auto blast-radius selection."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_one_source_file_uses_changed_scope(self) -> None:
        """1 source file → effective_scope = changed."""
        impl = self.scripts_dir / "impl.py"
        impl.write_text("pass\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["scope"], "auto")
        self.assertEqual(payload["effective_scope"], "changed")

    def test_five_files_uses_broad_scope(self) -> None:
        """5+ source files → effective_scope = broad."""
        changed = []
        for i in range(5):
            f = self.scripts_dir / f"module_{i}.py"
            f.write_text("pass\n")
            changed.append(str(f))
        _write_passing_test(self.scripts_dir, "test_module_0.py")

        r = _run(
            ["--workdir", str(self.workdir), "--scope", "auto", "--json"]
            + ["--changed-files"] + changed
        )
        payload = _payload(r.stdout)
        self.assertEqual(payload["scope"], "auto")
        self.assertEqual(payload["effective_scope"], "broad")


# ---------------------------------------------------------------------------
# Regression: gate/test files are NOT special-cased (no needs_human)
# ---------------------------------------------------------------------------

class TestPytestTimeoutProbe(unittest.TestCase):
    """`--timeout` is a PLUGIN flag; passing it without the plugin exits pytest 4.

    That exit was classified as verdict=error/"Timeout", so on a venv provisioned
    without the `test` extra the self-modification gate hard-errored on a healthy
    tree — 6 failing tests here, and a gate that verified nothing. The probe asks
    the RESOLVED runner (which may be a different venv than this process) whether
    pytest-timeout is loaded, and the caller drops the flag when it is not.
    """

    def setUp(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import self_mod_verify  # noqa: PLC0415 - late import keeps module-path setup local

        self.mod = self_mod_verify

    def test_probe_reports_plugin_presence_for_the_real_runner(self) -> None:
        repo = Path(__file__).resolve().parent.parent
        runner = self.mod._find_runner(repo)
        if runner is None:
            self.skipTest("no pytest runner available")
        answer = self.mod._runner_has_pytest_timeout(runner, cwd=repo)
        self.assertIsInstance(answer, bool)
        # Cross-check against the runner itself so the probe cannot silently
        # invert. `--version` must be DOUBLED: modern pytest prints only
        # "pytest <N>" for a single flag and reserves the registered-plugin list
        # for the doubled form. This cross-check used the single flag and so
        # agreed with the probe's own blind spot — both reported "no plugin"
        # against a venv that had it, and the gate ran without hang protection.
        r = subprocess.run(
            [*runner, "--version", "--version", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo),
        )
        expected = "timeout" in (r.stdout + r.stderr).lower()
        self.assertEqual(answer, expected)

    def test_probe_sees_a_plugin_that_the_runner_really_loaded(self) -> None:
        """Ground truth, not self-consistency: if the resolved runner loads
        pytest-timeout, the probe must say True.

        The sibling test above only asserts probe == cross-check, so a probe and
        a cross-check that share a blind spot agree while both are wrong. This
        one reads the plugin list directly and requires the probe to match it.
        """
        repo = Path(__file__).resolve().parent.parent
        runner = self.mod._find_runner(repo)
        if runner is None:
            self.skipTest("no pytest runner available")
        listing = subprocess.run(
            [*runner, "--version", "--version", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo),
        )
        if "pytest-timeout" not in (listing.stdout + listing.stderr):
            self.skipTest("resolved runner has no pytest-timeout installed")
        self.assertTrue(
            self.mod._runner_has_pytest_timeout(runner, cwd=repo),
            msg="runner loads pytest-timeout but the probe reported it missing; "
                "per-test hang protection would be silently dropped",
        )

    def test_probe_answers_for_the_directory_the_suite_will_run_in(self) -> None:
        """The probe must be asked in the run's working directory.

        `uv run pytest` resolves its virtualenv from the working directory, so a
        probe taken in the plugin repo answered "pytest-timeout installed" while
        the run, executed in a temp workdir, reached a venv without it and exited
        4 — surfacing verdict=error on a healthy tree. Probing the temp workdir
        must therefore be allowed to disagree with probing the repo, and the
        production call site passes cwd=workdir.
        """
        repo = Path(__file__).resolve().parent.parent
        runner = self.mod._find_runner(repo)
        if runner is None:
            self.skipTest("no pytest runner available")
        with tempfile.TemporaryDirectory() as elsewhere:
            # Both answers must be booleans obtained WITHOUT raising; the point
            # is that cwd is a real input to the probe, not that they differ on
            # every machine.
            here = self.mod._runner_has_pytest_timeout(runner, cwd=repo)
            there = self.mod._runner_has_pytest_timeout(runner, cwd=elsewhere)
            self.assertIsInstance(here, bool)
            self.assertIsInstance(there, bool)
        # The production path must pass the workdir through, not probe blind.
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("_runner_has_pytest_timeout(runner_base, cwd=workdir)", source)

    def test_probe_fails_closed_on_a_broken_runner(self) -> None:
        # A probe that cannot answer must say False: dropping the flag degrades
        # cleanly, while passing it on a plugin-less runner exits pytest 4.
        self.assertFalse(
            self.mod._runner_has_pytest_timeout(
                [sys.executable, "-c", "import sys; sys.exit(3)"]
            )
        )
        self.assertFalse(
            self.mod._runner_has_pytest_timeout(["definitely-not-a-real-binary-xyz"])
        )


class TestNoMetaHalt(unittest.TestCase):
    """Passing scripts/self_mod_verify.py or test_*.py in --changed-files must
    never yield needs_human.  The gate runs mapped tests and returns pass/fail
    based on the test result only."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_self_mod_verify_changed_not_needs_human(self) -> None:
        """Listing scripts/self_mod_verify.py as changed → verdict is pass or
        no_tests (based on mapped test result), never needs_human."""
        # Write a passing test that maps to self_mod_verify.py
        (self.scripts_dir / "test_self_mod_verify.py").write_text(
            "def test_ok(): assert True\n"
        )
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", "scripts/self_mod_verify.py",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertNotEqual(payload["verdict"], "needs_human",
                            msg=f"Gate file must not trigger needs_human: {payload}")
        self.assertIn(payload["verdict"], ("pass", "no_tests"),
                      msg=f"Expected pass or no_tests, got: {payload['verdict']}")
        # pass is green (0); no_tests is inconclusive (3) — never needs_human.
        self.assertEqual(r.returncode, 0 if payload["verdict"] == "pass" else 3)

    def test_test_file_changed_not_needs_human(self) -> None:
        """Listing a test_*.py file as changed → verdict reflects test outcome,
        not file identity.  When the test passes, verdict = pass."""
        test_file = self.scripts_dir / "test_something.py"
        test_file.write_text("def test_ok(): assert 1 == 1\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(test_file),
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertNotEqual(payload["verdict"], "needs_human",
                            msg=f"Test file must not trigger needs_human: {payload}")
        # test_something.py is included directly (it IS a test file), runs and passes
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"Passing test file should yield pass, got: {payload['verdict']}")
        self.assertEqual(r.returncode, 0)

    def test_gate_file_failing_tests_yields_fail_not_needs_human(self) -> None:
        """When the gate file is changed and its mapped test FAILS, verdict=fail
        (exit 1), not needs_human."""
        (self.scripts_dir / "test_self_mod_verify.py").write_text(
            "def test_broken(): assert False, 'intentional'\n"
        )
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", "scripts/self_mod_verify.py",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertNotEqual(payload["verdict"], "needs_human",
                            msg=f"Gate file with failing tests must give fail, not needs_human: {payload}")
        self.assertEqual(payload["verdict"], "fail",
                         msg=f"Failing tests must yield fail: {payload['verdict']}")
        self.assertEqual(r.returncode, 1)

    def test_no_meta_modification_key_in_output(self) -> None:
        """The meta_modification and meta_files keys are gone from the JSON output."""
        _write_passing_test(self.scripts_dir)
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        payload = _payload(r.stdout)
        self.assertNotIn("meta_modification", payload)
        self.assertNotIn("meta_files", payload)


# ---------------------------------------------------------------------------
# Tests: JSON stdout purity
# ---------------------------------------------------------------------------

class TestJsonStdoutPurity(unittest.TestCase):
    """--json stdout must parse as pure JSON with no leading human text."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _write_passing_test(scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stdout_is_valid_json(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        try:
            payload = _payload(r.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout is not valid JSON: {exc}\nstdout={r.stdout!r}")
        self.assertIsInstance(payload, dict)

    def test_stderr_contains_human_summary(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        self.assertIn("verdict=", r.stderr,
                      msg="Human summary should appear on stderr, not stdout")

    def test_stdout_does_not_contain_human_prefix(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        # Ensure stdout starts with '{' (JSON object), not human text
        stripped = r.stdout.strip()
        self.assertTrue(
            stripped.startswith("{"),
            msg=f"stdout should start with '{{', got: {stripped[:80]!r}",
        )

    def test_json_parses_on_pass_verdict(self) -> None:
        """Pass path also emits pure JSON to stdout."""
        scripts_dir = self.workdir / "scripts"
        test_file = scripts_dir / "test_something.py"
        test_file.write_text("def test_ok(): pass\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(test_file),
            "--json",
        ])
        try:
            payload = _payload(r.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout not valid JSON: {exc}\nstdout={r.stdout!r}")
        self.assertIn(payload["verdict"], ("pass", "no_tests"))


# ---------------------------------------------------------------------------
# Tests: timeout flag plumbing
# ---------------------------------------------------------------------------

class TestTimeoutFlagPlumbing(unittest.TestCase):
    """Verify timed_out flag is set and verdict is not falsely pass on timeout."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        # Write a test that will time out when timeout=0 is passed to subprocess
        # We can't reliably force a 0s timeout in subprocess, so we unit-test
        # the flag plumbing by monkey-patching subprocess.run
        _write_passing_test(scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_timeout_result_has_timed_out_false_on_fast_run(self) -> None:
        """Normal fast run sets timed_out=False."""
        result, exit_code = self.mod.verify(
            workdir=self.workdir,
            scope="full",
            changed_files=[],
            auto_revert=False,
            timeout=300,
        )
        self.assertFalse(result["timed_out"])

    def test_timeout_flag_plumbing_unit(self) -> None:
        """Simulate TimeoutExpired and verify timed_out=True, verdict=no_tests."""
        import unittest.mock as mock

        original_run = subprocess.run

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            # Let ALL runner-detection probes through (`uv run pytest --version`
            # AND the `python3 -m pytest --version` fallback — _find_runner may
            # make either or both depending on whether uv resolves here). Only
            # the real test-run invocation (no `--version`) simulates a timeout.
            if "--version" in cmd:
                return original_run(cmd, **kwargs)
            raise subprocess.TimeoutExpired(cmd, 1)

        with mock.patch("subprocess.run", side_effect=mock_run):
            result, exit_code = self.mod.verify(
                workdir=self.workdir,
                scope="full",
                changed_files=[],
                auto_revert=False,
                timeout=1,
            )

        self.assertTrue(result["timed_out"], msg=f"timed_out should be True; result={result}")
        # verdict must not be pass on timeout
        self.assertNotEqual(result["verdict"], "pass",
                            msg="A timed-out run must never report verdict=pass")
        # Timeout at the subprocess layer → verdict "error" (infrastructure failure)
        self.assertEqual(result["verdict"], "error",
                         msg=f"Timed-out subprocess must be verdict=error; got {result['verdict']}")
        self.assertIsNotNone(result.get("error_reason"),
                             msg="error_reason must be set on verdict=error")
        # exit_code is 2 (error — infrastructure failure, not a test failure)
        self.assertEqual(exit_code, 2)

    def test_cli_timeout_argument_accepted(self) -> None:
        """--timeout flag is parsed without error."""
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "full",
            "--timeout", "600",
            "--json",
        ])
        # Should not error due to unrecognised argument
        payload = _payload(r.stdout)
        self.assertIn("verdict", payload)


# ---------------------------------------------------------------------------
# Tests: conftest.py live-marker skip logic
# ---------------------------------------------------------------------------

class TestConftestLiveSkip(unittest.TestCase):
    """Verify conftest.py skips `live`-marked tests when the probe fails."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_live_test(self) -> Path:
        p = self.scripts_dir / "test_live_service.py"
        p.write_text(
            "import pytest\n"
            "@pytest.mark.live\n"
            "def test_needs_live_service():\n"
            "    # This must be skipped when Ollama is unreachable.\n"
            "    assert False, 'live service required'\n"
        )
        return p

    def test_live_marked_test_skipped_when_probe_fails(self) -> None:
        """A test marked @pytest.mark.live must be skipped (not failed/hung)
        when the conftest Ollama probe monkeypatches to unreachable.

        We test this by writing a conftest.py to the tmp workdir that
        patches _ollama_reachable to return False, then verifies the
        @pytest.mark.live test is skipped (not failed).
        """
        import importlib.util
        import sys as _sys

        # Copy the repo conftest to the tmp workdir so pytest finds it.
        repo_root = HERE.parent
        repo_conftest = repo_root / "conftest.py"

        # Write a local conftest to workdir that forces _ollama_reachable=False
        # so we don't depend on whether the real Ollama service is running.
        (self.workdir / "conftest.py").write_text(
            "import pytest\n"
            "\n"
            "def _ollama_reachable():\n"
            "    return False  # monkeypatched for test isolation\n"
            "\n"
            "def pytest_collection_modifyitems(config, items):\n"
            "    skip_marker = pytest.mark.skip(\n"
            "        reason='live service (Ollama/qwen on 127.0.0.1:11434) unreachable'\n"
            "    )\n"
            "    for item in items:\n"
            "        if item.get_closest_marker('live') is not None:\n"
            "            item.add_marker(skip_marker, append=False)\n"
        )
        # Register the `live` marker so pytest doesn't warn
        (self.workdir / "pytest.ini").write_text(
            "[pytest]\n"
            "markers =\n"
            "    live: requires live external service\n"
        )

        self._write_live_test()

        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short",
             str(self.scripts_dir / "test_live_service.py")],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(self.workdir),
        )
        combined = r.stdout + r.stderr
        # Must NOT fail (the test assertion "assert False" must not run)
        self.assertNotIn("FAILED", combined,
                         msg=f"live test must be skipped, not failed:\n{combined}")
        # Must appear as SKIPPED
        self.assertIn("skipped", combined.lower(),
                      msg=f"live test must appear skipped:\n{combined}")


# ---------------------------------------------------------------------------
# Tests: verdict=error on collection failure / no summary
# ---------------------------------------------------------------------------

class TestVerdictError(unittest.TestCase):
    """When pytest exits non-zero with no summary line, verdict must be
    'error' (not 'fail') so callers see WHY the gate produced 0/0."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _init_git_repo(self.workdir)
        # A test with a syntax error so pytest fails to collect it
        (scripts_dir / "test_bad_syntax.py").write_text(
            "def test_ok():\n"
            "    assert True\n"
            "\n"
            "def broken syntax here:\n"  # intentional SyntaxError
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collection_error_yields_verdict_error(self) -> None:
        """A file that fails to collect (SyntaxError) must produce
        verdict='error' with error_reason set, exit code 2."""
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        # Exit code 2 = error (not 0=pass, not 1=fail)
        self.assertEqual(r.returncode, 2,
                         msg=f"Expected exit 2 on collection error; stderr: {r.stderr}")
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "error",
                         msg=f"Collection failure must be verdict=error; got {payload['verdict']}")
        self.assertIsNotNone(payload.get("error_reason"),
                             msg="error_reason must be set when verdict=error")
        # Passed and failed are both 0 (nothing ran successfully)
        self.assertEqual(payload["passed"], 0)
        self.assertEqual(payload["failed"], 0)

    def test_error_reason_in_stderr_summary(self) -> None:
        """The human summary on stderr must include error_reason when present."""
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        # Only relevant if we actually get verdict=error
        payload = _payload(r.stdout)
        if payload["verdict"] == "error":
            self.assertIn("error_reason", r.stderr,
                          msg=f"stderr summary must include error_reason; stderr={r.stderr!r}")


# ---------------------------------------------------------------------------
# Tests: git-derived change set + no_changes verdict
# ---------------------------------------------------------------------------
# A read-only `--scope auto` run with NO --changed-files historically resolved to
# an empty test set → a green no_tests that gated nothing. These cover the fix:
# derive the TEST SCOPE from git (tracked diff + untracked new files), and emit an
# explicit non-green no_changes verdict when git shows no changes at all. The
# derived set never feeds a revert — see TestAutoRevert.

class TestGitDerivedChangeSet(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _commit(self, msg: str = "wip") -> None:
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", msg],
                       check=True, capture_output=True)

    def test_explicit_changed_files_bypasses_git_derivation(self) -> None:
        """When --changed-files IS given, the gate uses it verbatim and does not
        touch git (derived_from_git=False)."""
        impl = self.scripts_dir / "impl.py"
        impl.write_text("pass\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")
        self._commit("add impl + test")
        # Modify impl so there IS a git change too — but pass it explicitly.
        impl.write_text("pass  # edited\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"test_impl.py should run and pass: {payload}")
        self.assertFalse(payload["derived_from_git"],
                         msg="explicit --changed-files must NOT set derived_from_git")
        self.assertEqual(r.returncode, 0)

    def test_fallback_derives_tracked_modified_file(self) -> None:
        """No --changed-files + a tracked-but-modified impl.py → the gate derives
        impl.py from `git diff HEAD`, runs its mapped test, and marks
        derived_from_git=True. This is the documented `--scope auto` path."""
        impl = self.scripts_dir / "impl.py"
        impl.write_text("VALUE = 1\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")
        self._commit("baseline")
        # Modify the tracked file WITHOUT staging — the exact self-mod shape.
        impl.write_text("VALUE = 2\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertTrue(payload["derived_from_git"],
                        msg=f"gate must auto-derive the change set from git: {payload}")
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"mapped test_impl.py should run and pass: {payload}")
        ran_names = [Path(f).name for f in payload["ran"]]
        self.assertIn("test_impl.py", ran_names,
                      msg="the git-derived file's mapped test must actually run")
        self.assertEqual(r.returncode, 0)

    def test_fallback_derives_untracked_new_file(self) -> None:
        """Regression BUIL-SELFMOD-001: a self-mod that ADDS a new foo.py +
        test_foo.py (both untracked) must be picked up under --scope auto with no
        --changed-files. `git diff` alone misses untracked files; the
        `ls-files --others` arm closes it. Expect the new test to actually run."""
        (self.scripts_dir / "newmod.py").write_text("def f(): return 42\n")
        (self.scripts_dir / "test_newmod.py").write_text(
            "from newmod import f\n"
            "def test_f(): assert f() == 42\n"
        )
        # Deliberately do NOT commit or stage — both files are untracked.
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--json",
        ], cwd=str(self.scripts_dir))
        payload = _payload(r.stdout)
        self.assertTrue(payload["derived_from_git"],
                        msg=f"untracked new files must be derived: {payload}")
        ran_names = [Path(f).name for f in payload["ran"]]
        self.assertIn("test_newmod.py", ran_names,
                      msg=f"the NEW untracked test must run (BUIL-SELFMOD-001): {payload}")
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"expected pass, not a green no_tests: {payload}")
        self.assertEqual(r.returncode, 0)

    def test_no_changes_verdict_when_clean(self) -> None:
        """No --changed-files + a fully clean tree → verdict no_changes, exit 3.
        A self-mod gate with nothing to verify must NOT read as pass."""
        impl = self.scripts_dir / "impl.py"
        impl.write_text("pass\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")
        self._commit("everything committed, tree clean")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "no_changes",
                         msg=f"clean tree must yield no_changes, not pass/no_tests: {payload}")
        self.assertEqual(r.returncode, 3,
                         msg="no_changes must be non-green (exit 3)")
        self.assertFalse(payload["derived_from_git"])
        self.assertEqual(payload["ran"], [])

    def test_no_changes_is_distinct_from_pass(self) -> None:
        """The no_changes verdict string is distinct from pass — a caller keying
        on verdict=='pass' will not be fooled by an empty run."""
        # _init_git_repo already leaves a clean tree (README committed); no diff.
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertNotEqual(payload["verdict"], "pass")
        self.assertEqual(payload["verdict"], "no_changes")

    def test_full_scope_ignores_git_derivation(self) -> None:
        """--scope full runs the whole suite regardless, so an empty
        --changed-files must NOT trigger the no_changes short-circuit."""
        _write_passing_test(self.scripts_dir, "test_thing.py")
        self._commit("add a test")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "full",
            "--json",
        ])
        payload = _payload(r.stdout)
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"full scope must run the suite, not no_changes: {payload}")
        self.assertFalse(payload["derived_from_git"])


# ---------------------------------------------------------------------------
# f1: --auto-revert partitions tracked vs untracked (no silent no-op revert)
# ---------------------------------------------------------------------------

class TestRevertPartitioning(unittest.TestCase):
    """A revert restores only the TRACKED files the caller explicitly listed,
    reports UNTRACKED ones (never deletes them), and populates errors[].
    Regression: the old code handed a mixed list to one `git restore`, which
    exits 1 on any untracked path having restored NOTHING — a silent no-op."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fail_path_reverts_tracked_reports_untracked(self) -> None:
        # Commit a tracked impl + a FAILING mapped test (fail → revert fires).
        impl = self.scripts_dir / "impl.py"
        impl.write_text("VALUE = 1\n")
        (self.scripts_dir / "test_impl.py").write_text(
            "def test_broken():\n    assert False, 'intentional'\n"
        )
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)

        # Tracked-mod: edit the committed impl (unstaged self-mod).
        impl.write_text("VALUE = 2  # broken self-mod\n")
        # Untracked-new: a brand-new file, part of the git-derived change set.
        extra = self.scripts_dir / "extra.py"
        extra.write_text("EXTRA = True\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", "scripts/impl.py", "scripts/extra.py",
            "--auto-revert",
            "--json",
        ])
        payload = _payload(r.stdout)
        # Explicit list → never derived from git.
        self.assertFalse(payload["derived_from_git"], msg=payload)
        self.assertEqual(payload["verdict"], "fail", msg=payload)
        self.assertEqual(r.returncode, 1, msg=f"stderr={r.stderr!r}")

        # The TRACKED file was restored to its committed content.
        self.assertTrue(payload["reverted"],
                        msg="tracked file must be restored on the fail path")
        restored = impl.read_text()
        self.assertIn("VALUE = 1", restored)
        self.assertNotIn("VALUE = 2", restored)

        # The UNTRACKED file is NOT deleted (concurrent WIP is never swept)...
        self.assertTrue(extra.exists(),
                        msg="untracked file must never be deleted by revert")

        # ...and errors[] is non-empty, reporting the untracked file explicitly.
        self.assertTrue(payload["errors"], "errors[] must be non-empty")
        joined = "\n".join(payload["errors"])
        self.assertIn("untracked, not reverted", joined, msg=joined)
        self.assertIn("extra.py", joined, msg=joined)

    def test_derived_change_set_is_refused_at_the_revert(self) -> None:
        """Defence in depth: even if a derived change set reached the revert, it
        is refused rather than sweeping every dirty file in the checkout."""
        import importlib.util  # noqa: PLC0415 - local to this regression test

        spec = importlib.util.spec_from_file_location("smv_derived", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        impl = self.scripts_dir / "impl.py"
        impl.write_text("VALUE = 1\n")
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        peer_wip = "VALUE = 2  # peer, uncommitted\n"
        impl.write_text(peer_wip)

        errors: list[str] = []
        blobs: list[dict] = []
        reverted = mod._revert_files(
            self.workdir, ["scripts/impl.py"], errors,
            derived_from_git=True, reverted_blobs=blobs,
        )
        self.assertFalse(reverted, msg=errors)
        self.assertEqual(blobs, [])
        self.assertEqual(impl.read_text(), peer_wip,
                         msg="a git-derived change set must never be reverted")
        self.assertIn("refusing to revert", "\n".join(errors))


# ---------------------------------------------------------------------------
# f2: a failing git change-set arm is recorded; tracked-arm failure → error
# ---------------------------------------------------------------------------

class TestGitDerivationErrors(unittest.TestCase):
    """A failing `git diff --name-only HEAD` (tracked) arm must not yield a
    truncated green change set: the failure is recorded in errors[] and the
    verdict escalates to error. Regression: per-arm `continue` swallowed the
    failure and could pass green over a knowingly-partial set."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _write_passing_test(scripts_dir, "test_thing.py")
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_helper_records_tracked_arm_failure(self) -> None:
        import unittest.mock as mock
        original_run = subprocess.run

        def mock_run(cmd, **kwargs):
            if "diff" in cmd and "--name-only" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 128, stdout="", stderr="fatal: bad revision 'HEAD'")
            return original_run(cmd, **kwargs)

        with mock.patch("subprocess.run", side_effect=mock_run):
            files, derivation_errors = self.mod._git_changed_files(self.workdir)

        self.assertTrue(
            any("tracked-diff" in e for e in derivation_errors),
            msg=f"derivation_errors must name the failed arm: {derivation_errors}",
        )

    def test_tracked_arm_failure_escalates_to_verdict_error(self) -> None:
        import unittest.mock as mock
        original_run = subprocess.run

        def mock_run(cmd, **kwargs):
            # Let runner --version probes through so _find_runner succeeds.
            if "--version" in cmd:
                return original_run(cmd, **kwargs)
            if "diff" in cmd and "--name-only" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 128, stdout="", stderr="fatal: bad revision 'HEAD'")
            return original_run(cmd, **kwargs)

        with mock.patch("subprocess.run", side_effect=mock_run):
            result, exit_code = self.mod.verify(
                workdir=self.workdir,
                scope="auto",
                changed_files=[],
                auto_revert=False,
                timeout=60,
            )

        self.assertEqual(result["verdict"], "error", msg=f"result={result}")
        self.assertEqual(exit_code, 2, msg=f"result={result}")
        self.assertTrue(result["errors"], "errors[] must be populated")
        self.assertTrue(
            any("tracked-diff" in e for e in result["errors"]),
            msg=f"errors[] must name the failed arm: {result['errors']}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
