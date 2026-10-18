"""Real Git/CLI regressions for optimizer scope and evaluator protection."""
import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import optimize_loop as loop


class OptimizeScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture Owner")
        self.git("config", "user.email", "fixture@example.invalid")
        for name, body in {"src/app.py": "value = 1\n", "src/metric.py": "print(1)\n",
                           "tests/check.py": "assert True\n", ".gitignore": ".build-loop/\n"}.items():
            self.write(name, body)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture baseline")
        self.init()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def init(self, **overrides):
        config = {"target": "fixture", "scope": "src/**", "protected": ["src/metric.py", "tests/**"],
                  "metric_cmd": "printf 1", "guard_cmd": "true", **overrides}
        loop.init_experiment(self.root, config)

    def metric(self, cmd="printf 2"):
        experiment = loop.load_experiment(self.root)
        experiment["metric_cmd"] = cmd
        loop._save_experiment(self.root, experiment)

    def test_scoped_improvement_is_kept(self):
        self.write("src/app.py", "value = 2\n")
        self.metric()
        self.assertEqual(loop.run_iteration(self.root, 1).status, "keep")

    def test_out_of_scope_tracked_change_is_discarded_before_metric(self):
        self.write("tests/check.py", "assert False\n")
        self.metric("touch metric-was-run; printf 2")
        result = loop.run_iteration(self.root, 1)
        self.assertEqual(result.status, "discard")
        self.assertIn("protected", result.description)
        self.assertFalse((self.root / "metric-was-run").exists())

    def test_protected_file_inside_scope_is_discarded(self):
        self.write("src/metric.py", "print(100)\n")
        self.metric()
        self.assertIn("protected", loop.run_iteration(self.root, 1).description)

    def test_untracked_and_staged_and_committed_changes_are_checked(self):
        for state in ["untracked", "staged", "committed"]:
            with self.subTest(state=state):
                self.write("outside.py", "outside\n")
                if state != "untracked":
                    self.git("add", "outside.py")
                if state == "committed":
                    self.git("commit", "-qm", "outside edit")
                self.assertIn("out-of-scope", loop.run_iteration(self.root, 1).description)
                if state == "committed":
                    self.git("reset", "--hard", "HEAD~1")
                else:
                    self.git("reset", "-q")
                    (self.root / "outside.py").unlink()

    def test_renaming_protected_file_cannot_hide_its_deletion(self):
        self.git("mv", "src/metric.py", "src/renamed.py")
        self.assertIn("protected", loop.run_iteration(self.root, 1).description)

    def test_metric_that_changes_evaluator_is_discarded_after_execution(self):
        self.metric("printf 'print(100)' > src/metric.py; printf 2")
        result = loop.run_iteration(self.root, 1)
        self.assertEqual(result.status, "discard")
        self.assertFalse(result.guard_passed)
        self.assertIn("protected", result.description)
        self.assertEqual(loop.load_experiment(self.root)["best_value"], 1)

    def test_guard_that_changes_scope_is_discarded(self):
        experiment = loop.load_experiment(self.root)
        experiment["guard_cmd"] = "printf changed > tests/check.py"
        loop._save_experiment(self.root, experiment)
        self.metric()
        self.assertEqual(loop.run_iteration(self.root, 1).status, "discard")

    def test_minimum_delta_higher_lower_boundary(self):
        for direction, command, minimum, expected in [
            ("higher", "printf 1.1", 0.5, "discard"),
            ("higher", "printf 1.5", 0.5, "keep"),
            ("lower", "printf 0.5", 0.5, "keep"),
            ("higher", "printf 1", 0, "discard"),
        ]:
            with self.subTest(direction=direction, command=command):
                self.init(direction=direction, min_delta=minimum)
                self.metric(command)
                self.assertEqual(loop.run_iteration(self.root, 1).status, expected)

    def test_invalid_policy_is_rejected_before_baseline_command(self):
        for bad in [-1, float("nan"), float("inf"), True]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.init(min_delta=bad, metric_cmd="touch bad-baseline; printf 1")
        for bad in ["../outside", "/absolute", "src/../tests/**", ""]:
            with self.subTest(scope=bad), self.assertRaises(ValueError):
                self.init(scope=bad, metric_cmd="touch bad-baseline; printf 1")
        self.assertFalse((self.root / "bad-baseline").exists())

    def test_dirty_baseline_is_rejected_before_metric(self):
        self.write("src/app.py", "dirty")
        with self.assertRaisesRegex(ValueError, "clean Git baseline"):
            self.init(metric_cmd="touch bad-baseline; printf 1")
        self.assertFalse((self.root / "bad-baseline").exists())

    def test_baseline_metric_cannot_modify_evaluator(self):
        with self.assertRaisesRegex(ValueError, "baseline metric changed"):
            self.init(metric_cmd="printf changed > src/metric.py; printf 1")

    def test_logging_delta_is_recomputed_from_current_best(self):
        args = argparse.Namespace(iteration=1, commit="test", metric=1.1, delta=100,
                                  status="keep", description="invented gain", hypothesis="")
        loop._cmd_log(args, self.root)
        self.assertAlmostEqual(float(loop.load_results(self.root)[-1]["delta"]), 0.1)

    def test_logged_keeps_advance_best_and_convergence_counters(self):
        args = argparse.Namespace(iteration=1, commit="test", metric=2, delta=1,
                                  status="keep", description="gain", hypothesis="")
        loop._cmd_log(args, self.root)
        experiment = loop.load_experiment(self.root)
        self.assertEqual(experiment["best_value"], 2)
        self.assertEqual(experiment["iterations_total"], 1)
        self.assertEqual(experiment["iterations_kept"], 1)
        args.metric, args.delta, args.iteration = 1.5, -0.5, 2
        with self.assertRaises(SystemExit):
            loop._cmd_log(args, self.root)
        self.assertEqual(len(loop.load_results(self.root)), 3)

    def test_globs_respect_segment_boundaries_and_zero_directory_globstar(self):
        self.assertTrue(loop._matches("src/app.py", "src/**/*.py"))
        self.assertTrue(loop._matches("src/deep/app.py", "src/**/*.py"))
        self.assertFalse(loop._matches("src/deep/app.py", "src/*.py"))
        self.assertTrue(loop._matches("app.test.ts", "**/*.test.*"))

    def test_changed_symlink_escape_is_refused(self):
        (self.root / "src/escape.py").symlink_to(self.root.parent / "external.py")
        self.assertIn("escapes", loop.run_iteration(self.root, 1).description)

    def test_missing_baseline_ref_cannot_keep(self):
        experiment = loop.load_experiment(self.root)
        experiment["baseline_commit"] = "not-a-commit"
        self.assertIsNotNone(loop.scope_violation(self.root, experiment))

    def test_agent_logging_path_cannot_bypass_scope(self):
        self.write("tests/check.py", "assert False\n")
        command = ["python3", str(Path(loop.__file__)), "--log", "--workdir", str(self.root),
                   "--iteration", "1", "--commit", "test", "--metric", "2", "--delta", "1",
                   "--status", "keep", "--description", "claimed improvement"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected", result.stderr + result.stdout)
        self.assertEqual(len(loop.load_results(self.root)), 2)
        self.assertEqual(loop.load_results(self.root)[-1]["status"], "discard")
        self.assertEqual(loop.load_experiment(self.root)["iterations_total"], 1)

    def test_logging_path_rejects_below_threshold(self):
        self.init(min_delta=0.5)
        args = argparse.Namespace(iteration=1, commit="test", metric=1.1, delta=0.1,
                                  status="keep", description="tiny gain", hypothesis="")
        with self.assertRaises(SystemExit):
            loop._cmd_log(args, self.root)

    def test_coverage_optimization_requires_explicit_evaluator_and_scope(self):
        self.write("package.json", json.dumps({"scripts": {"test": "test"}}))
        self.assertNotIn("optimize-tests", [t["target"] for t in loop.detect_targets(self.root)])

    def test_hidden_index_flags_are_refused(self):
        for flag in ["assume-unchanged", "skip-worktree"]:
            with self.subTest(flag=flag):
                self.git("update-index", "--" + flag, "tests/check.py")
                self.write("tests/check.py", "hidden edit")
                self.assertIn("index entries", loop.run_iteration(self.root, 1).description)
                self.git("update-index", "--no-" + flag, "tests/check.py")
                self.git("restore", "tests/check.py")


if __name__ == "__main__":
    unittest.main()
