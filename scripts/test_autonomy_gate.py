#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for autonomy_gate.py. Zero deps. Run: python3 test_autonomy_gate.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "autonomy_gate.py"


def run(workdir: Path, action: str, command: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workdir",
            str(workdir),
            "--action",
            action,
            "--command",
            command,
            "--json",
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def envelope(result: subprocess.CompletedProcess) -> dict:
    return json.loads(result.stdout)


class AutonomyGateDefaultsTests(unittest.TestCase):
    """Case 1: each of the 7 default confirmFor patterns should exit 1."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _assert_confirm(self, command: str) -> None:
        result = run(self.workdir, "test", command)
        data = envelope(result)
        self.assertEqual(
            data["action"],
            "confirm",
            msg=f"Expected confirm for {command!r}, got {data['action']!r}. Full: {data}",
        )
        self.assertEqual(
            result.returncode,
            1,
            msg=f"Expected exit 1 for {command!r}, got {result.returncode}",
        )

    def test_npm_publish(self) -> None:
        self._assert_confirm("npm publish")

    def test_npm_publish_with_args(self) -> None:
        self._assert_confirm("npm publish --access public")

    def test_git_push_force_main_blocks(self) -> None:
        # Catastrophic-never: refused, not confirmed (AGENTS.md autonomy gate).
        result = run(self.workdir, "test", "git push --force origin main")
        self.assertEqual(envelope(result)["action"], "block")
        self.assertEqual(result.returncode, 2)

    def test_git_push_feature_branch_confirms(self) -> None:
        # Owner rule 2026-11-19: every push asks unless autoPush is set.
        self._assert_confirm("git push origin feature/x")

    def test_git_push_with_dash_c_confirms(self) -> None:
        self._assert_confirm("git -C /tmp/repo push origin feature/x")

    def test_auto_push_config_waives_push_confirm(self) -> None:
        cfg = self.workdir / ".build-loop"
        cfg.mkdir()
        (cfg / "config.json").write_text('{"autonomy": {"autoPush": true}}')
        result = run(self.workdir, "test", "git push origin feature/x")
        self.assertEqual(envelope(result)["action"], "auto", msg=str(envelope(result)))
        # A production branch still confirms through deployment policy.
        self._assert_confirm("git push origin main")

    def test_git_push_main(self) -> None:
        self._assert_confirm("git push origin main")

    def test_git_push_master(self) -> None:
        self._assert_confirm("git push origin master")

    def test_production_deploy(self) -> None:
        self._assert_confirm("production deploy v1.2.3")

    def test_drop_table(self) -> None:
        self._assert_confirm("DROP TABLE users")

    def test_rm_rf_non_catastrophic_confirms(self) -> None:
        # A delete of a real path confirms (archive is the auto alternative).
        self._assert_confirm("rm -rf ./build-output")

    def _assert_block(self, command: str) -> None:
        result = run(self.workdir, "test", command)
        data = envelope(result)
        self.assertEqual(
            data["action"], "block",
            msg=f"Expected block for {command!r}, got {data['action']!r}. Full: {data}",
        )
        self.assertEqual(result.returncode, 2, msg=f"Expected exit 2 for {command!r}, got {result.returncode}")

    def test_rm_rf_root_blocks(self) -> None:
        # Catastrophic-never: auto-refused (block), not confirmed.
        self._assert_block("rm -rf /")

    def test_rm_rf_home_blocks(self) -> None:
        self._assert_block("rm -rf ~")


class AutonomyGateAutoTests(unittest.TestCase):
    """Case 2: normal-looking commands should exit 0 with action=auto."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_edit_file(self) -> None:
        result = run(self.workdir, "guidance fix", "edit scripts/foo.py")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0)

    def test_cache_resync(self) -> None:
        result = run(self.workdir, "cache resync", "rsync ... codex cache")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0)

    def test_lint_cleanup(self) -> None:
        result = run(self.workdir, "lint fix", "npx eslint --fix src/")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0)


class AutonomyGateRepoOverrideTests(unittest.TestCase):
    """Case 3: repo override adds a custom confirmFor pattern."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_config(self, autonomy: dict) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"autonomy": autonomy}))

    def test_custom_confirm_pattern_matches(self) -> None:
        """Case 3: custom confirmFor from repo config exits 1."""
        self._write_config({"confirmFor": ["wipe database*"]})
        result = run(self.workdir, "ops", "wipe database staging")
        data = envelope(result)
        self.assertEqual(data["action"], "confirm", msg=str(data))
        self.assertEqual(data["list_source"], "config")
        self.assertEqual(result.returncode, 1)

    def test_empty_confirm_for_disables_all_defaults(self) -> None:
        """Case 4: empty confirmFor replaces defaults — previously-confirm commands now auto.

        Note: uses 'DROP TABLE users' rather than 'npm publish' because npm publish
        triggers the deployment_policy delegation path (which is higher precedence than
        the repo confirmFor override). DROP TABLE is in the 7 defaults but is not
        deployment-flavored, so it correctly exercises the REPLACE semantics.
        """
        self._write_config({"confirmFor": []})
        # DROP TABLE is in the 7 defaults; with empty override it should now auto
        result = run(self.workdir, "db-op", "DROP TABLE users")
        data = envelope(result)
        self.assertEqual(
            data["action"],
            "auto",
            msg=f"Expected auto (defaults replaced), got {data['action']}. Full: {data}",
        )
        self.assertEqual(result.returncode, 0)

    def test_repo_block_for(self) -> None:
        """Case 5: repo blockFor pattern exits 2."""
        self._write_config({"blockFor": ["rm -rf *"]})
        result = run(self.workdir, "cleanup", "rm -rf /home/user")
        data = envelope(result)
        self.assertEqual(data["action"], "block", msg=str(data))
        self.assertEqual(result.returncode, 2)


class AutonomyGateDeploymentPolicyTests(unittest.TestCase):
    """Case 6: deployment-flavored commands route through deployment_policy."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_vercel_deploy_prod_routes_via_deployment_policy(self) -> None:
        """vercel deploy --prod should resolve via deployment_policy (list_source=deployment_policy)."""
        result = run(self.workdir, "deploy", "vercel deploy --prod")
        data = envelope(result)
        # The key assertion: it was routed through deployment_policy
        self.assertEqual(
            data["list_source"],
            "deployment_policy",
            msg=f"Expected list_source=deployment_policy, got {data['list_source']!r}. Full: {data}",
        )
        # deployment_policy classifies vercel deploy --prod as production -> confirm
        self.assertEqual(data["action"], "confirm", msg=str(data))
        self.assertEqual(result.returncode, 1)

    def test_vercel_preview_routes_via_deployment_policy(self) -> None:
        """vercel deploy (no --prod) should route via deployment_policy and be auto."""
        result = run(self.workdir, "preview deploy", "vercel deploy")
        data = envelope(result)
        self.assertEqual(
            data["list_source"],
            "deployment_policy",
            msg=f"Expected list_source=deployment_policy, got {data['list_source']!r}. Full: {data}",
        )
        self.assertEqual(data["action"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0)


class AutonomyGateMalformedConfigTests(unittest.TestCase):
    """Case 7: malformed config.json falls back to defaults gracefully (no crash)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_raw_config(self, content: str) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(content)

    def test_malformed_json_falls_back_to_defaults(self) -> None:
        self._write_raw_config("{ not valid json }")
        # Should NOT crash — must fall back to defaults
        result = run(self.workdir, "test", "edit scripts/foo.py")
        self.assertEqual(result.returncode, 0, msg=f"Crash! stderr: {result.stderr}")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))

    def test_malformed_json_still_applies_defaults_on_confirm_command(self) -> None:
        self._write_raw_config("this is not json at all")
        result = run(self.workdir, "publish", "npm publish")
        self.assertEqual(result.returncode, 1, msg=f"stderr: {result.stderr}")
        data = envelope(result)
        self.assertEqual(data["action"], "confirm", msg=str(data))

    def test_truncated_json_falls_back(self) -> None:
        self._write_raw_config('{"autonomy": {"confirmFor": [')
        result = run(self.workdir, "test", "edit scripts/bar.py")
        self.assertNotIn("Traceback", result.stderr, msg="Should not crash with traceback")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))


class AutonomyGateWarnTests(unittest.TestCase):
    """Case 8: repo warnFor patterns exit 0 with action=warn."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_config(self, autonomy: dict) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"autonomy": autonomy}))

    def test_warn_pattern_exits_zero(self) -> None:
        """warnFor pattern exits 0 with action=warn."""
        self._write_config({"warnFor": ["touch-prod-config*"]})
        result = run(self.workdir, "ops", "touch-prod-config /etc/foo")
        data = envelope(result)
        self.assertEqual(data["action"], "warn", msg=str(data))
        self.assertEqual(result.returncode, 0, msg=f"Expected exit 0, got {result.returncode}")

    def test_warn_appears_in_envelope(self) -> None:
        """warnFor match surfaces action=warn, list_source=config, and matched_rule in envelope."""
        self._write_config({"warnFor": ["touch-prod-config*"]})
        result = run(self.workdir, "ops", "touch-prod-config /etc/foo")
        data = envelope(result)
        self.assertEqual(data["action"], "warn", msg=str(data))
        self.assertEqual(data["list_source"], "config", msg=str(data))
        self.assertEqual(data["matched_rule"], "touch-prod-config*", msg=str(data))

    def test_confirm_for_wins_over_warn_for_on_tie(self) -> None:
        """When a command matches both confirmFor and warnFor, confirmFor wins (stricter verdict)."""
        self._write_config({"confirmFor": ["foo*"], "warnFor": ["foo*"]})
        result = run(self.workdir, "ops", "foo bar")
        data = envelope(result)
        self.assertEqual(
            data["action"],
            "confirm",
            msg=f"Expected confirm (stricter wins on tie), got {data['action']!r}. Full: {data}",
        )
        self.assertEqual(result.returncode, 1, msg=f"Expected exit 1, got {result.returncode}")


class AutonomyGateCommitAutoTests(unittest.TestCase):
    """Regression: git commit must always classify as auto (exit 0).

    This pins the contract so that a future confirmFor change cannot silently
    start gating plain commits.  Committing validated work is non-gated by
    design — the only commit-adjacent stops are push/deploy verdicts.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _assert_auto(self, command: str) -> None:
        result = run(self.workdir, "single-writer commit", command)
        data = envelope(result)
        self.assertEqual(
            data["action"],
            "auto",
            msg=f"Expected auto for {command!r}, got {data['action']!r}. Full: {data}",
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Expected exit 0 for {command!r}, got {result.returncode}",
        )

    def test_plain_git_commit(self) -> None:
        self._assert_auto('git commit -m "feat: add foo"')

    def test_git_commit_with_sign_off(self) -> None:
        self._assert_auto('git commit -s -m "fix: correct bar"')

    def test_git_commit_amend(self) -> None:
        # --amend is still a local-only mutation — not a push or deploy
        self._assert_auto("git commit --amend --no-edit")

    def test_git_commit_no_edit(self) -> None:
        self._assert_auto('git commit --no-edit -m "chore: bump version"')


class AutonomyGateSelfTestTests(unittest.TestCase):
    """Verify --self-test exits 0."""

    def test_self_test_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--self-test"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"--self-test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )
        self.assertIn("passed", result.stdout.lower(), msg=result.stdout)


class AutonomyGateFlagsTests(unittest.TestCase):
    """Verify flags are surfaced in the envelope."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_flags_default_to_true(self) -> None:
        result = run(self.workdir, "test", "edit scripts/foo.py")
        data = envelope(result)
        self.assertIn("flags", data)
        flags = data["flags"]
        self.assertTrue(flags["autoFixGuidance"])
        self.assertTrue(flags["autoExecuteOpenRecs"])

    def test_flags_read_from_config(self) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({
                "autonomy": {
                    "autoFixGuidance": False,
                    "autoExecuteOpenRecs": True,
                }
            })
        )
        result = run(self.workdir, "test", "edit scripts/foo.py")
        data = envelope(result)
        flags = data["flags"]
        self.assertFalse(flags["autoFixGuidance"])
        self.assertTrue(flags["autoExecuteOpenRecs"])




# --- Deploy shapes carrying no literal `deploy` token -------------------------
# Measured 2026-08-21: five of ten real production-deploy commands classified
# `auto`, including `vercel --prod` — the canonical Vercel production deploy, and
# the one CLAUDE.md names explicitly as requiring confirmation. The default glob
# `* deploy * --prod*` only matches commands containing the word "deploy".

class DeployWithoutDeployTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.wd = Path(self._tmp.name)

    def _action(self, command: str) -> str:
        """Runs the real CLI in an EMPTY workdir, so the DEFAULT lists are what is
        under test — a repo config would replace them and hide the defect."""
        return envelope(run(self.wd, "bash", command))["action"]

    def test_canonical_vercel_prod_deploy_confirms(self):
        self.assertEqual(self._action("vercel --prod"), "confirm")

    def test_npx_wrapped_vercel_prod_confirms(self):
        """A wrapper prefix must not defeat the gate."""
        self.assertEqual(self._action("npx vercel --prod"), "confirm")

    def test_deploy_shapes_without_the_word_deploy_confirm(self):
        for cmd in ("fly deploy", "flyctl deploy", "wrangler deploy",
                    "wrangler pages deploy", "eas submit --platform ios",
                    "railway up"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._action(cmd), "confirm", cmd)

    def test_shapes_that_already_worked_still_confirm(self):
        """Regression guard: widening the list must not displace existing rules."""
        for cmd in ("vercel deploy --prod", "netlify deploy --prod",
                    "npm publish", "gh release create v1"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._action(cmd), "confirm", cmd)

    def test_local_dev_server_is_not_a_deploy(self):
        """The false-positive direction. A gate that prompts on `vercel dev` gets
        bypassed, and a bypassed gate protects nothing."""
        self.assertEqual(self._action("vercel dev"), "auto")

    def test_a_commit_whose_message_mentions_deploy_stays_auto(self):
        """The originally-filed defect, which did NOT reproduce. Pinned so the
        widening above cannot introduce it."""
        self.assertEqual(
            self._action("git commit -m 'fix: correct the deploy classifier'"), "auto")

    def test_ordinary_commands_stay_auto(self):
        for cmd in ("npm test", "ls", "git status", "python3 -m pytest"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._action(cmd), "auto", cmd)


class MigrationAndPrMergeConfirmTests(unittest.TestCase):
    """Migration-apply and PR-merge shapes must confirm, not auto-execute.

    Measured 2026-09-04 against the pre-fix gate: `npx prisma migrate deploy`
    returned action=auto with reason "no pattern matched; safe to execute", so
    the loop would have applied a production migration with no operator in the
    loop. `gh pr merge` did the same, reaching the protected branch that a plain
    `git push origin main` already confirms on.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _action(self, command: str) -> dict:
        return envelope(run(self.workdir, "run", command))

    def test_migration_apply_shapes_confirm(self) -> None:
        for cmd in (
            "npx prisma migrate deploy",
            "prisma migrate resolve --applied 20260904090000_x",
            "npx prisma db push",
            "supabase db push",
            "supabase migration up",
            "alembic upgrade head",
            "atlas migrate apply --env prod",
            "flyway -url=x migrate",
            "dbmate up",
            "knex migrate:latest",
            "sqlx migrate run",
            "goose postgres x up",
        ):
            with self.subTest(cmd=cmd):
                data = self._action(cmd)
                self.assertEqual(data["action"], "confirm", msg=str(data))

    def test_pr_merge_confirms(self) -> None:
        data = self._action("gh pr merge 24 --squash --delete-branch")
        self.assertEqual(data["action"], "confirm", msg=str(data))

    def test_gh_api_writes_confirm_and_reads_stay_auto(self) -> None:
        """Measured 2026-11-19: `gh api -X DELETE ...` returned auto as read-only."""
        for cmd in (
            "gh api -X DELETE repos/a/b/git/refs/heads/x",
            "gh api --method PUT repos/a/b/pulls/5/merge",
            "gh api repos/a/b/labels -f name=x",
            "gh repo delete a/b --yes",
            "gh release delete v1 -y",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._action(cmd)["action"], "confirm")
        for cmd in ("gh api repos/a/b", "gh api -X GET search/issues -f q=x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._action(cmd)["action"], "auto")

    def test_local_and_rehearsal_shapes_stay_auto(self) -> None:
        """The widening must not gate local development or unrelated commands."""
        for cmd in (
            "npx prisma migrate dev --name add_col",
            "npx prisma generate",
            "npm test",
            "gh pr view 24",
            "gh pr create --title x",
        ):
            with self.subTest(cmd=cmd):
                data = self._action(cmd)
                self.assertEqual(data["action"], "auto", msg=str(data))


class BranchDeletionConfirmTests(unittest.TestCase):
    """Deleting a remote ref must confirm, not classify as a preview deploy.

    Measured 2026-09-04: `git push origin --delete <branch>` returned
    action=auto, matched_rule="preview", list_source="deployment_policy".
    deployment_policy classified it by its target branch, so a non-production
    branch read as a preview push and it never reached DEFAULT_CONFIRM_FOR's
    `git push * --delete *` pattern, which exists for exactly this command.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _env(self, command: str) -> dict:
        return envelope(run(self.workdir, "run", command))

    def test_delete_flag_confirms_and_bypasses_deployment_policy(self) -> None:
        data = self._env("git push origin --delete ui/brief-market-map-polish")
        self.assertEqual(data["action"], "confirm", msg=str(data))
        self.assertNotEqual(data["list_source"], "deployment_policy", msg=str(data))

    def test_colon_syntax_delete_confirms(self) -> None:
        data = self._env("git push origin :old-branch")
        self.assertEqual(data["action"], "confirm", msg=str(data))

    def test_ordinary_pushes_are_unchanged(self) -> None:
        """Every push confirms (owner rule 2026-11-19); src:dst refspecs are not deletes."""
        for cmd, expected in (
            ("git push origin feature/x", "confirm"),
            ("git push origin HEAD:refs/heads/scratch", "confirm"),
            ("git push origin main", "confirm"),
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._env(cmd)["action"], expected)


class CompoundCommandTests(unittest.TestCase):
    """A compound command gets the scrutiny of its most severe part.

    The gate used to return `auto` for anything whose FIRST word was read-only
    (`ls && rm -rf ~/x`), and its own confirm/block globs are anchored at the
    start of the string, so `echo hi && npm publish` never met `npm publish*`.
    Commands come from the rule tables (see test_classify_action.derived_commands).
    """

    RANK = {"auto": 0, "warn": 1, "confirm": 2, "block": 3}

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(HERE))
        import autonomy_gate
        from test_classify_action import READ_ONLY_PREFIXES, derived_commands

        cls.gate = autonomy_gate
        cls.prefixes = READ_ONLY_PREFIXES
        cls.commands = derived_commands()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.workdir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _action(self, command: str) -> str:
        return self.gate.classify(self.workdir, "PreToolUse:Bash", command)["action"]

    def test_no_read_only_prefix_lowers_the_gate_action(self) -> None:
        rated = [(c, self._action(c)) for c in self.commands]
        rated = [(c, a) for c, a in rated if self.RANK[a] > 0]
        self.assertGreaterEqual(len(rated), 15, rated)
        failures = []
        for command, alone in rated:
            for shape in self.prefixes:
                compound = shape.format(command)
                got = self._action(compound)
                if self.RANK[got] < self.RANK[alone]:
                    failures.append(f"{compound!r}: {got} < {alone} (alone)")
        self.assertEqual(failures, [])

    def test_named_commands(self) -> None:
        cases = {
            "echo hi && npm publish": "confirm",
            "ls && rm -rf ~/Documents/example": "confirm",
            "ls && rm -rf ~": "block",
            "ls; git reset --hard HEAD~5": "auto",   # alone: auto (no rule names it)
            "cat x && git branch -D feature": "auto",  # alone: auto (no rule names it)
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                env = self.gate.classify(self.workdir, "PreToolUse:Bash", command)
                self.assertEqual(env["action"], expected)
                self.assertNotEqual(env["matched_rule"], "read_only")
                self.assertEqual(env["command"], command)

    def test_read_only_compounds_stay_auto(self) -> None:
        for command in ("ls && pwd", "git status; git log --oneline -3", "cat a | grep b", 'echo "a && b"'):
            with self.subTest(command=command):
                env = self.gate.classify(self.workdir, "PreToolUse:Bash", command)
                self.assertEqual((env["action"], env["matched_rule"]), ("auto", "read_only"))

    def test_policy_patterns_apply_to_every_part(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / ".build-loop").mkdir()
            (wd / ".build-loop" / "config.json").write_text(json.dumps(
                {"autonomy": {"blockFor": ["danger-block*"], "confirmFor": ["danger-confirm*"]}}))
            for command, expected in (("echo ok && danger-block now", "block"),
                                      ("ls; danger-confirm x", "confirm"),
                                      ("danger-confirm x && danger-block y", "block")):
                with self.subTest(command=command):
                    self.assertEqual(self.gate.classify(wd, "PreToolUse:Bash", command)["action"], expected)


class ReviewFixGateTests(unittest.TestCase):
    """Deep nesting and huge part counts must not crash or stall the gate."""

    RANK = {"auto": 0, "warn": 1, "confirm": 2, "block": 3}

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(HERE))
        import autonomy_gate

        cls.gate = autonomy_gate
        cls._tmp = tempfile.TemporaryDirectory()
        cls.workdir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_deep_nesting_is_at_least_confirm(self) -> None:
        # main returned `confirm` for both; a crash here left the hook's stdout
        # empty, which it reads as allow.
        for depth in (50, 990, 1200):
            deep = "echo " + "$(" * depth + "true" + ")" * depth
            for head in ("rm -rf ~", "npm publish"):
                with self.subTest(depth=depth, head=head):
                    env = self.gate.classify(self.workdir, "PreToolUse:Bash", f"{head} ; {deep}")
                    self.assertGreaterEqual(self.RANK[env["action"]], self.RANK["confirm"])

    def test_deep_nesting_through_the_cli_prints_an_envelope(self) -> None:
        deep = "echo " + "$(" * 1200 + "true" + ")" * 1200
        result = run(self.workdir, "PreToolUse:Bash", f"rm -rf ~ ; {deep}")
        self.assertEqual(envelope(result)["action"], "confirm")

    def test_five_hundred_parts_is_fast_and_confirms(self) -> None:
        import time
        command = " ; ".join(f"npm run deploy-{i}" for i in range(500))
        start = time.monotonic()
        env = self.gate.classify(self.workdir, "PreToolUse:Bash", command)
        self.assertLess(time.monotonic() - start, 3.0)
        self.assertGreaterEqual(self.RANK[env["action"]], self.RANK["confirm"])

    def test_repeated_parts_are_judged_once(self) -> None:
        from unittest import mock
        calls = []
        real = self.gate._invoke_deployment_policy
        with mock.patch.object(self.gate, "_invoke_deployment_policy",
                               side_effect=lambda w, c: calls.append(c) or real(w, c)):
            self.gate.classify(self.workdir, "PreToolUse:Bash", " ; ".join(["npm run deploy"] * 30))
        self.assertEqual(len(calls), 2)  # whole string + one distinct part


if __name__ == "__main__":
    unittest.main(verbosity=2)
