#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
Tests for verification_claim_probe.py.

The convicting mutant: a subagent claimed "verified by reproducing the
auditor's exact attack (exit 2, store still 0)" while the shipped code
actually exits 0. test_claimed_exit_2_but_actually_exit_0_is_contradicted
reproduces that exact failure shape and asserts the probe calls it
`contradicted`, not `executed`.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "verification_claim_probe.py"

# Import the module under test
sys.path.insert(0, str(HERE))
import audit_git  # noqa: E402
from verification_claim_probe import _is_safe_to_reexecute, extract_claims, probe  # noqa: E402


def _write_exit_script(tmpdir: Path, name: str, code: int) -> Path:
    path = tmpdir / name
    path.write_text(textwrap.dedent(f"""\
        import sys
        sys.exit({code})
        """))
    return path


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Byte-and-mode snapshot of every file under root.

    This is the filesystem witness: if a refused command had actually run,
    the tree it targets would differ.
    """
    out: dict[str, tuple[bytes, int]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_dir():
            out[rel + "/"] = (b"", stat.S_IMODE(p.stat().st_mode))
        elif p.is_file():
            out[rel] = (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
    return out


class TestContradictedClaimMutant(unittest.TestCase):
    """The acceptance criterion: a false 'exit 2' claim must be contradicted."""

    def test_claimed_exit_2_but_actually_exit_0_is_contradicted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=0)

            claims = [{
                "claim": (
                    "Fixed — guard refuses the live store; verified by "
                    "reproducing the auditor's exact attack (exit 2, store still 0)."
                ),
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]

            results = probe(claims, cwd=str(tmpdir), timeout=10)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "contradicted")
            self.assertEqual(results[0]["actual"]["returncode"], 0)

            counts = {"executed": 0, "contradicted": 0, "cited": 0, "error": 0}
            for r in results:
                counts[r["status"]] += 1
            self.assertEqual(counts["contradicted"], 1)

            verdict = "contradicted_claims_present" if counts["contradicted"] else "clean"
            self.assertEqual(verdict, "contradicted_claims_present")

    def test_cli_exit_code_1_on_contradicted_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=0)

            claims_path = tmpdir / "claims.json"
            claims_path.write_text(json.dumps([{
                "claim": "verified by reproducing the auditor's exact attack (exit 2, store still 0)",
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]))

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--claims-file", str(claims_path), "--cwd", str(tmpdir)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["verdict"], "contradicted_claims_present")
            self.assertEqual(payload["counts"]["contradicted"], 1)


class TestGenuinelyVerifiedClaim(unittest.TestCase):
    """Mirror case: command genuinely exits as claimed → executed, exit 0."""

    def test_claimed_exit_2_and_actually_exit_2_is_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=2)

            claims = [{
                "claim": "verified by reproducing the auditor's exact attack (exit 2, store still 0)",
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]

            results = probe(claims, cwd=str(tmpdir), timeout=10)

            self.assertEqual(results[0]["status"], "executed")
            self.assertEqual(results[0]["actual"]["returncode"], 2)

    def test_cli_exit_code_0_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=2)

            claims_path = tmpdir / "claims.json"
            claims_path.write_text(json.dumps([{
                "claim": "verified by reproducing the auditor's exact attack (exit 2, store still 0)",
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]))

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--claims-file", str(claims_path), "--cwd", str(tmpdir)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["verdict"], "clean")
            self.assertEqual(payload["counts"]["executed"], 1)


class TestExtraction(unittest.TestCase):
    """extract_claims pulls the command + expectation out of free prose."""

    def test_extracts_command_and_exit_2_from_literal_sentence(self) -> None:
        text = (
            "Fixed — guard refuses the live store; verified by reproducing the "
            "auditor's exact attack (exit 2, store still 0) via "
            "`python3 attack.py --store /tmp/store`."
        )

        claims = extract_claims(text)

        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(claim["command"], "python3 attack.py --store /tmp/store")
        self.assertEqual(claim["expected"], {"returncode": 2})
        self.assertEqual(claim["source_line"], 1)

    def test_command_without_verification_verb_is_not_a_claim(self) -> None:
        text = "See `python3 attack.py --store /tmp/store` for the harness entrypoint."
        claims = extract_claims(text)
        self.assertEqual(claims, [])

    def test_extracts_from_multiline_report(self) -> None:
        text = textwrap.dedent("""\
            ## Fix summary

            Guard now refuses writes to the live store.

            Verified by running `pytest tests/test_guard.py -k live_store` — 3 passed.
            """)
        claims = extract_claims(text)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["command"], "pytest tests/test_guard.py -k live_store")
        self.assertEqual(claims[0]["source_line"], 5)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.email=probe@test.invalid",
         "-c", "user.name=probe",
         "-c", "commit.gpgsign=false",
         *args],
        capture_output=True, text=True, check=True,
    )


def _make_repo(root: Path) -> Path:
    """A throwaway git repo on branch `main` with committed, dirty, and
    untracked files — so a real `git checkout` / `reset` / `clean` / `stash`
    would visibly change the tree."""
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/main"],
                   capture_output=True, check=True)
    (repo / "tracked.txt").write_text("committed content\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "seed")
    # Dirty the tree: checkout/reset/restore/stash would all wipe these.
    (repo / "tracked.txt").write_text("UNCOMMITTED EDIT — must survive\n")
    (repo / "untracked_sentinel.txt").write_text("git clean would delete me\n")
    _git(repo, "add", "untracked_sentinel.txt")
    (repo / "really_untracked.txt").write_text("git clean would delete me too\n")
    return repo


class TestDestructiveCommandsAreNeverExecuted(unittest.TestCase):
    """Every command the old deny-list let through is now `cited`.

    Each case carries a filesystem witness: the whole tmpdir tree is
    snapshotted before and after, and must be byte-and-mode identical. If the
    probe had executed the command, the snapshot would differ.
    """

    # (label, command template, needs_git_repo)
    CASES = (
        # --- the ten commands an auditor CONFIRMED the deny-list allowed ---
        ("append_redirect", "pytest -q >> {sentinel}", False),
        ("git_checkout", "git checkout main", True),
        ("git_restore_staged", "git restore --staged untracked_sentinel.txt", True),
        ("git_reset", "git reset", True),
        ("git_stash", "git stash", True),
        ("truncate", "truncate -s 0 {sentinel}", False),
        ("chmod", "chmod 777 {sentinel}", False),
        ("psql_drop", 'psql -c "drop table t"', False),
        ("docker_compose_down", "docker compose down -v", False),
        ("npm_install", "npm install", False),
        # --- and the ones it already refused; they must stay refused ---
        ("sudo_rm_rf_root", "sudo rm -rf /", False),
        ("git_push", "git push origin main", True),
        ("git_clean", "git clean -fd", True),
    )

    def test_destructive_commands_are_never_executed(self) -> None:
        has_git = shutil.which("git") is not None
        for label, template, needs_repo in self.CASES:
            with self.subTest(command=label):
                if needs_repo and not has_git:
                    self.skipTest("git not installed")
                with tempfile.TemporaryDirectory() as tmp:
                    tmpdir = Path(tmp)
                    sentinel = tmpdir / "sentinel.txt"
                    sentinel.write_text("SENTINEL — untouched\n")
                    sentinel.chmod(0o600)

                    cwd = _make_repo(tmpdir) if needs_repo else tmpdir
                    command = template.format(sentinel=sentinel)

                    before = _snapshot(tmpdir)
                    results = probe(
                        [{"claim": f"verified by running {command}: exit 0",
                          "command": command,
                          "expected": {"returncode": 0}}],
                        cwd=str(cwd), timeout=20,
                    )
                    after = _snapshot(tmpdir)

                    self.assertEqual(results[0]["status"], "cited", results[0])
                    self.assertIn("not_safely_re-executable", results[0]["reason"])
                    # actual is None <=> subprocess.run was never reached
                    self.assertIsNone(results[0]["actual"])
                    # Filesystem witness.
                    self.assertEqual(after, before, f"{command} mutated the tree")

    def test_append_redirection_does_not_grow_the_target_file(self) -> None:
        """The `>>` case explicitly: the old regex `(?<![>\\d])>(?!>)` exempted
        append, so `pytest -q >> ~/.zshrc` was ALLOWED. Assert no growth."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            profile = tmpdir / "fake_zshrc"
            profile.write_text("# original profile\n")
            size_before = profile.stat().st_size

            results = probe(
                [{"claim": "verified by running the suite: 3 passed",
                  "command": f"pytest -q >> {profile}",
                  "expected": {"stdout_contains": "3 passed"}}],
                cwd=str(tmpdir), timeout=20,
            )

            self.assertEqual(results[0]["status"], "cited")
            self.assertIn("append redirection", results[0]["reason"])
            self.assertEqual(profile.stat().st_size, size_before)
            self.assertEqual(profile.read_text(), "# original profile\n")

    def test_allow_flag_is_the_only_escape_hatch(self) -> None:
        """--allow stays an exact-string operator opt-in; near misses refuse."""
        self.assertEqual(_is_safe_to_reexecute("npm install")[0], False)
        results = probe(
            [{"claim": "verified: exit 0", "command": "npm install", "expected": {"returncode": 0}}],
            cwd=".", timeout=5, allow=["npm  install"],  # near miss (double space)
        )
        self.assertEqual(results[0]["status"], "cited")


class TestGitArmDelegatesToAuditGit(unittest.TestCase):
    """The git arm calls audit_git.classify(). One table, two scripts, no drift."""

    MUTATING = ("git checkout main", "git restore f", "git stash", "git reset")
    READ_ONLY = ("git log --oneline -1", "git status --short", "git diff HEAD")

    def test_git_arm_agrees_with_audit_git(self) -> None:
        for command in self.MUTATING + self.READ_ONLY:
            with self.subTest(command=command):
                expected_allowed = audit_git.classify(command.split()[1:])["allowed"]
                actual_allowed = _is_safe_to_reexecute(command)[0]
                self.assertEqual(
                    actual_allowed, expected_allowed,
                    f"probe and audit_git disagree about {command!r}",
                )

    def test_mutating_git_commands_are_cited(self) -> None:
        for command in self.MUTATING:
            with self.subTest(command=command):
                safe, why = _is_safe_to_reexecute(command)
                self.assertFalse(safe)
                self.assertIn("audit_git", why)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_read_only_git_commands_actually_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            claims = [{"claim": f"verified by running {c}: exit 0", "command": c,
                       "expected": {"returncode": 0}} for c in self.READ_ONLY]

            results = probe(claims, cwd=str(repo), timeout=20)

            for c, r in zip(self.READ_ONLY, results):
                self.assertEqual(r["status"], "executed", f"{c}: {r}")
                self.assertIsNotNone(r["actual"])

    def test_delegation_actually_happens(self) -> None:
        """Spy on audit_git.classify — if the probe ever re-implemented the git
        table locally instead of delegating, this fails."""
        real = audit_git.classify
        with mock.patch.object(audit_git, "classify", side_effect=real) as spy:
            _is_safe_to_reexecute("git checkout main")
            _is_safe_to_reexecute("git status --short")
        self.assertEqual(spy.call_count, 2)
        self.assertEqual(spy.call_args_list[0].args[0], ["checkout", "main"])
        self.assertEqual(spy.call_args_list[1].args[0], ["status", "--short"])

    def test_git_refused_when_audit_git_cannot_be_loaded(self) -> None:
        """Import failure must refuse, never fall back to permissive."""
        with mock.patch.dict(sys.modules, {"audit_git": None}):
            safe, why = _is_safe_to_reexecute("git status --short")
        self.assertFalse(safe, "unavailable allowlist must refuse, not allow")
        self.assertIn("audit_git", why)


class TestLegitimateVerificationCommandsStillExecute(unittest.TestCase):
    """Noisy-gate regression guard: a probe that refuses every real
    verification command is useless."""

    REAL_COMMANDS = ("pytest -q", "cargo test", "npm test", "ruff check .")

    def test_legitimate_verification_commands_are_not_cited(self) -> None:
        for command in self.REAL_COMMANDS + ("python3 check.py", "npm run test",
                                             "go test ./...", "mypy scripts",
                                             "vitest run", "make test"):
            with self.subTest(command=command):
                safe, why = _is_safe_to_reexecute(command)
                self.assertTrue(safe, f"{command!r} refused: {why}")

    def test_legitimate_verification_commands_actually_run(self) -> None:
        """PATH-shimmed real execution: each tool writes a witness line, so a
        refused command would leave the witness file short."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            bindir = tmpdir / "bin"
            bindir.mkdir()
            witness = tmpdir / "witness.log"
            witness.write_text("")

            for tool in ("pytest", "cargo", "npm", "ruff"):
                shim = bindir / tool
                shim.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{witness}"\nexit 0\n')
                shim.chmod(0o755)

            script = tmpdir / "check.py"
            script.write_text(
                "from pathlib import Path\n"
                f"Path({str(witness)!r}).open('a').write('python3 ran\\n')\n"
            )

            commands = list(self.REAL_COMMANDS) + [f"{sys.executable} {script}"]
            claims = [{"claim": f"verified by running {c}: exit 0", "command": c,
                       "expected": {"returncode": 0}} for c in commands]

            env_path = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
            with mock.patch.dict(os.environ, {"PATH": env_path}):
                results = probe(claims, cwd=str(tmpdir), timeout=30)

            for c, r in zip(commands, results):
                self.assertEqual(r["status"], "executed", f"{c}: {r}")

            lines = witness.read_text().splitlines()
            self.assertEqual(
                lines,
                ["pytest -q", "cargo test", "npm test", "ruff check .", "python3 ran"],
            )


class TestNothingExecutedExitsTwo(unittest.TestCase):
    """Exit 0 must mean 'claims were verified', not 'checked nothing'."""

    def _run_cli(self, report: Path, cwd: Path) -> tuple[int, dict]:
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--report-file", str(report), "--cwd", str(cwd)],
            capture_output=True, text=True,
        )
        return r.returncode, json.loads(r.stdout)

    def test_claims_with_no_parseable_expectation_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "guard.py", code=0)
            report = tmpdir / "report.md"
            report.write_text(
                f"Fix landed. Verified by running `{sys.executable} {script}` end to end.\n"
            )

            rc, payload = self._run_cli(report, tmpdir)

            self.assertGreater(len(payload["claims"]), 0, "no claim extracted; test is vacuous")
            self.assertEqual(payload["counts"]["executed"], 0)
            self.assertEqual(payload["verdict"], "nothing_executed")
            self.assertEqual(rc, 2)

    def test_all_claims_refused_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            report = tmpdir / "report.md"
            report.write_text("Verified by running `git push origin main` — exit 0.\n")

            rc, payload = self._run_cli(report, tmpdir)

            self.assertEqual(payload["counts"]["cited"], len(payload["claims"]))
            self.assertEqual(payload["verdict"], "nothing_executed")
            self.assertEqual(rc, 2)

    def test_zero_claims_extracted_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            report = tmpdir / "report.md"
            report.write_text("No commands here, just prose about the fix.\n")

            rc, payload = self._run_cli(report, tmpdir)

            self.assertEqual(payload["claims"], [])
            self.assertEqual(payload["verdict"], "nothing_executed")
            self.assertEqual(rc, 2)

    def test_one_satisfied_expectation_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "guard.py", code=3)
            report = tmpdir / "report.md"
            report.write_text(
                f"Verified by reproducing the attack via `{sys.executable} {script}` — exit 3.\n"
            )

            rc, payload = self._run_cli(report, tmpdir)

            self.assertEqual(payload["counts"]["executed"], 1)
            self.assertEqual(payload["verdict"], "clean")
            self.assertEqual(rc, 0)


class TestTimeoutHandling(unittest.TestCase):
    """A command that exceeds the timeout errors out instead of crashing."""

    def test_timeout_yields_error_status_not_a_crash(self) -> None:
        # NOTE: the command is a script file, not `python3 -c "...; ..."` —
        # `;` is a shell metacharacter and is now refused before execution.
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            slow = tmpdir / "slow.py"
            slow.write_text("import time\ntime.sleep(5)\n")

            claims = [{
                "claim": "verified by running the long check: exit 0",
                "command": f"{sys.executable} {slow}",
                "expected": {"returncode": 0},
            }]

            results = probe(claims, cwd=str(tmpdir), timeout=1)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "error")
            self.assertIn("timeout", results[0]["reason"])


class TestNoStatedExpectation(unittest.TestCase):
    def test_no_expected_runs_but_is_cited(self) -> None:
        claims = [{"claim": "ran the smoke check", "command": f"{sys.executable} -c \"print('ok')\""}]
        results = probe(claims, cwd=".", timeout=10)
        self.assertEqual(results[0]["status"], "cited")
        self.assertEqual(results[0]["reason"], "no_stated_expectation")
        self.assertIsNotNone(results[0]["actual"])
        self.assertEqual(results[0]["actual"]["returncode"], 0)


class TestMarkdownRendering(unittest.TestCase):
    def test_markdown_mode_labels_each_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=0)
            claims_path = tmpdir / "claims.json"
            claims_path.write_text(json.dumps([{
                "claim": "verified by reproducing the auditor's exact attack (exit 2, store still 0)",
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]))

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--claims-file", str(claims_path),
                 "--cwd", str(tmpdir), "--markdown"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("contradicted:", r.stdout)


if __name__ == "__main__":
    unittest.main()
