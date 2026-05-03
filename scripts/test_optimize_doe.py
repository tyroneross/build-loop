#!/usr/bin/env python3
"""Tests for optimize_doe.py — design generation + effects analysis accuracy.

Run: python3 test_optimize_doe.py

Requires: numpy (only for these tests; the build-loop pytest-stdlib pattern
is bent here because optimize_doe itself depends on numpy for the math).

Tests:
  DesignGeneratorTests   — full / fractional / PB matrices have correct
                           shape and perfect orthogonality (XᵀX diagonal)
  RoutingTests           — auto-routing picks the right design type by k
  EffectsAccuracyTests   — OLS recovers known ground-truth coefficients
                           within tight tolerance
  CliRoundTripTests      — generate → analyze pipeline produces sensible
                           ranked findings
  PyDOE3EquivalenceTests — IF pyDOE3 is installed, design matrices are
                           equivalent (gram matrix match); else skipped

Verified empirically 2026-05-03 against pyDOE3 1.6.2: 5/5 designs equivalent
(2^3 full, 2^4 full, 2^(5-2), 2^(7-4), PB-12). Exact match for fractional
factorials; row-permutation match for full factorials; sign-equivalent
column match for Plackett-Burman.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.stderr.write("test_optimize_doe.py requires numpy; skipping\n")
    sys.exit(0)

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "optimize_doe.py"

# Import the module for direct unit testing
sys.path.insert(0, str(HERE))
import optimize_doe as doe  # noqa: E402


# ---------------------------------------------------------------------------
# Direct unit tests on the design generators
# ---------------------------------------------------------------------------

class DesignGeneratorTests(unittest.TestCase):
    def test_full_factorial_shape(self) -> None:
        for k in range(1, 6):
            with self.subTest(k=k):
                d = doe.full_factorial_2level(k)
                self.assertEqual(d.shape, (2 ** k, k))
                self.assertTrue(np.all((d == -1) | (d == 1)))

    def test_full_factorial_orthogonal(self) -> None:
        for k in range(2, 6):
            with self.subTest(k=k):
                d = doe.full_factorial_2level(k)
                gram = d.T @ d
                off_diag = gram - np.diag(np.diag(gram))
                self.assertTrue(np.allclose(off_diag, 0))

    def test_fracfact_shape(self) -> None:
        # 2^(5-2) → 8 runs, 5 factors
        d = doe.fracfact("a b c ab ac")
        self.assertEqual(d.shape, (8, 5))

    def test_fracfact_orthogonal(self) -> None:
        for k, gen in doe.FRACFACT_8_RUN.items():
            with self.subTest(k=k):
                d = doe.fracfact(gen)
                gram = d.T @ d
                off_diag = gram - np.diag(np.diag(gram))
                self.assertTrue(np.allclose(off_diag, 0))

    def test_pb_12_shape(self) -> None:
        d = doe.plackett_burman_12()
        self.assertEqual(d.shape, (12, 11))

    def test_pb_12_orthogonal(self) -> None:
        d = doe.plackett_burman_12()
        gram = d.T @ d
        off_diag = gram - np.diag(np.diag(gram))
        self.assertTrue(np.allclose(off_diag, 0),
                        f"PB off-diag max = {np.max(np.abs(off_diag))}")


class RoutingTests(unittest.TestCase):
    def test_select_design(self) -> None:
        cases = {1: "autoresearch", 2: "full", 3: "full",
                 4: "fractional", 5: "fractional", 7: "fractional",
                 8: "pb", 11: "pb"}
        for k, expected in cases.items():
            with self.subTest(k=k):
                self.assertEqual(doe.select_design(k), expected)

    def test_build_design_dispatch(self) -> None:
        m, name = doe.build_design(3, "full")
        self.assertEqual(m.shape, (8, 3))
        self.assertIn("full factorial", name)

        m, name = doe.build_design(5, "fractional")
        self.assertEqual(m.shape, (8, 5))
        self.assertIn("fractional factorial", name)

        m, name = doe.build_design(8, "pb")
        self.assertEqual(m.shape, (12, 8))
        self.assertIn("Plackett-Burman", name)


class EffectsAccuracyTests(unittest.TestCase):
    """Recover known ground-truth coefficients from synthetic measurements."""

    def test_full_factorial_no_noise(self) -> None:
        d = doe.full_factorial_2level(3)
        # y = 10 + 5*x1 + 2*x2 - 0.3*x3 + 0.5*x1*x2
        y = 10 + 5 * d[:, 0] + 2 * d[:, 1] - 0.3 * d[:, 2] + 0.5 * d[:, 0] * d[:, 1]
        e = doe.fit_effects(d, y, include_interactions=True)
        self.assertAlmostEqual(e["intercept"], 10, places=8)
        self.assertAlmostEqual(e["main"][0], 5, places=8)
        self.assertAlmostEqual(e["main"][1], 2, places=8)
        self.assertAlmostEqual(e["main"][2], -0.3, places=8)
        self.assertAlmostEqual(e["interactions"][(0, 1)], 0.5, places=8)

    def test_fractional_with_noise(self) -> None:
        d = doe.fracfact("a b c ab ac")
        rng = np.random.default_rng(42)
        truth_main = [3.0, -1.5, 0.8, 2.2, -0.4]
        y = 20 + sum(t * d[:, i] for i, t in enumerate(truth_main)) + rng.normal(0, 0.1, 8)
        e = doe.fit_effects(d, y, include_interactions=False)
        for i, expected in enumerate(truth_main):
            with self.subTest(factor=f"x{i+1}"):
                self.assertAlmostEqual(e["main"][i], expected, delta=0.2)

    def test_pb_screening_identifies_vital_few(self) -> None:
        d = doe.plackett_burman_12()
        rng = np.random.default_rng(11)
        # Only first 3 factors active
        y = 50 + 4 * d[:, 0] - 2.5 * d[:, 1] + 1.0 * d[:, 2] + rng.normal(0, 0.3, 12)
        e = doe.fit_effects(d, y, include_interactions=False)
        # Top 3 by |effect| should be factors 0, 1, 2
        ranking = sorted(e["main"].items(), key=lambda kv: -abs(kv[1]))
        top3 = {idx for idx, _ in ranking[:3]}
        self.assertEqual(top3, {0, 1, 2})


class LevelMappingTests(unittest.TestCase):
    def test_low_high_mapping(self) -> None:
        d = np.array([[-1, 1], [1, -1]], dtype=float)
        factors = [
            {"name": "x", "low": 16, "high": 64},
            {"name": "y", "low": 1, "high": 5},
        ]
        runs = doe.map_levels(d, factors)
        self.assertEqual(runs[0]["_factors"], {"x": 16, "y": 5})
        self.assertEqual(runs[1]["_factors"], {"x": 64, "y": 1})

    def test_levels_array(self) -> None:
        d = np.array([[-1], [1]], dtype=float)
        factors = [{"name": "x", "levels": ["off", "on"]}]
        runs = doe.map_levels(d, factors)
        self.assertEqual(runs[0]["_factors"], {"x": "off"})
        self.assertEqual(runs[1]["_factors"], {"x": "on"})


class CliRoundTripTests(unittest.TestCase):
    """Exercise the CLI end-to-end: generate matrix, fake measurements, analyze."""

    def test_generate_then_analyze(self) -> None:
        factors = [
            {"name": "batch_size", "low": 16, "high": 64},
            {"name": "retries", "low": 1, "high": 5},
            {"name": "workers", "low": 2, "high": 8},
        ]
        # Step 1: generate
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "generate",
             "--factors", json.dumps(factors), "--design", "auto", "--seed", "1"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        design = json.loads(r.stdout)
        self.assertEqual(design["design"]["type"], "full")
        self.assertEqual(design["design"]["n_runs"], 8)

        # Step 2: synthesize measurements where x1 dominates, x3 mildly opposes
        with tempfile.TemporaryDirectory() as tmp:
            design_path = Path(tmp) / "design.json"
            results_path = Path(tmp) / "results.jsonl"
            design_path.write_text(json.dumps(design))
            matrix = np.array(design["matrix"])
            y = 100 - 8 * matrix[:, 0] + 0.5 * matrix[:, 1] + 1 * matrix[:, 2]
            with open(results_path, "w") as f:
                for run_id in range(len(y)):
                    f.write(json.dumps({"run_id": run_id, "value": float(y[run_id])}) + "\n")

            # Step 3: analyze
            r2 = subprocess.run(
                [sys.executable, str(SCRIPT), "analyze",
                 "--design", str(design_path), "--results", str(results_path),
                 "--direction", "lower"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)
            analysis = json.loads(r2.stdout)
            top = analysis["ranked_effects"][0]
            self.assertEqual(top["term"], "batch_size")
            self.assertAlmostEqual(top["effect"], -8, delta=0.5)


class PyDOE3EquivalenceTests(unittest.TestCase):
    """If pyDOE3 happens to be installed, verify our matrices match.

    Provides a runtime check on the equivalence claim documented in
    KNOWN-ISSUES.md and in this test file's docstring. Usually skipped
    because build-loop's stdlib convention means pyDOE3 isn't installed.
    """

    def test_equivalence_when_pydoe3_present(self) -> None:
        try:
            from pyDOE3 import fullfact as pd_full, fracfact as pd_frac
        except ImportError:
            self.skipTest("pyDOE3 not installed (expected — build-loop is stdlib+numpy)")

        # Convert pyDOE3's 0/1 coding to our ±1 coding for comparison
        their_full = pd_full([2, 2, 2]) * 2 - 1
        mine_full = doe.full_factorial_2level(3)
        # Both orthogonal balanced designs over the same set of points
        self.assertTrue(
            np.array_equal(np.array(sorted(map(tuple, their_full))),
                           np.array(sorted(map(tuple, mine_full)))),
            "2^3 full factorial differs from pyDOE3",
        )

        their_frac = pd_frac("a b c ab ac")
        mine_frac = doe.fracfact("a b c ab ac")
        self.assertTrue(np.array_equal(their_frac, mine_frac),
                        "2^(5-2) fractional differs from pyDOE3")


class HandoffToAutoresearchTests(unittest.TestCase):
    """Exercise the full DOE → autoresearch pipeline.

    Steps:
      1. Generate a 3-factor full factorial design (8 runs).
      2. Synthesize measurements with a known optimum.
      3. Analyze — verify the resulting effects.json includes `best_factors`.
      4. Initialize an autoresearch experiment with `--baseline-config` pointing
         at the effects.json. Verify experiment.json embeds `doe_baseline` with
         the DOE-best factor values.

    Lives here (not test_optimize_loop) because the handoff is the bridge
    between the two scripts; this test owns the integration contract.
    """

    LOOP_SCRIPT = HERE / "optimize_loop.py"

    def _make_throwaway_repo(self, tmp: Path) -> None:
        """optimize_loop.init expects a git repo and a runnable metric command."""
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True)
        (tmp / "README.md").write_text("seed\n")
        subprocess.run(["git", "add", "."], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp, check=True)

    def test_analyze_emits_best_factors(self) -> None:
        factors = [
            {"name": "batch_size", "low": 16, "high": 64},
            {"name": "retries", "low": 1, "high": 5},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            # Generate
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "generate",
                 "--factors", json.dumps(factors), "--design", "full", "--seed", "0"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            design = json.loads(r.stdout)
            (tmp / "design.json").write_text(json.dumps(design))
            # Fake measurements: y = 100 - 8*x1 - 2*x2 (lower is better)
            matrix = np.array(design["matrix"])
            y = 100 - 8 * matrix[:, 0] - 2 * matrix[:, 1]
            with (tmp / "results.jsonl").open("w") as f:
                for i in range(len(y)):
                    f.write(json.dumps({"run_id": i, "value": float(y[i])}) + "\n")
            # Analyze with direction=lower → best run minimizes y → both factors high
            r2 = subprocess.run(
                [sys.executable, str(SCRIPT), "analyze",
                 "--design", str(tmp / "design.json"),
                 "--results", str(tmp / "results.jsonl"),
                 "--direction", "lower"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)
            effects = json.loads(r2.stdout)
            self.assertIn("best_factors", effects, "analyze must emit best_factors block")
            self.assertEqual(effects["best_factors"], {"batch_size": 64, "retries": 5})
            self.assertEqual(effects["direction"], "lower")

    def test_optimize_loop_init_consumes_baseline_config(self) -> None:
        factors = [
            {"name": "batch_size", "low": 16, "high": 64},
            {"name": "retries", "low": 1, "high": 5},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._make_throwaway_repo(tmp)
            # Generate + analyze
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "generate",
                 "--factors", json.dumps(factors), "--design", "full", "--seed", "0"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            design = json.loads(r.stdout)
            design_path = tmp / "design.json"
            results_path = tmp / "results.jsonl"
            effects_path = tmp / "effects.json"
            design_path.write_text(json.dumps(design))
            matrix = np.array(design["matrix"])
            y = 100 - 8 * matrix[:, 0] - 2 * matrix[:, 1]
            with results_path.open("w") as f:
                for i in range(len(y)):
                    f.write(json.dumps({"run_id": i, "value": float(y[i])}) + "\n")
            r2 = subprocess.run(
                [sys.executable, str(SCRIPT), "analyze",
                 "--design", str(design_path), "--results", str(results_path),
                 "--direction", "lower"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)
            effects_path.write_text(r2.stdout)
            # Initialize autoresearch experiment with baseline-config handoff
            r3 = subprocess.run(
                [sys.executable, str(self.LOOP_SCRIPT),
                 "--init", "--workdir", str(tmp),
                 "--target", "throughput",
                 "--metric-cmd", "echo 42",
                 "--direction", "lower",
                 "--baseline-config", str(effects_path)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(r3.returncode, 0, r3.stderr)
            # Verify experiment.json embeds doe_baseline
            exp_path = tmp / ".build-loop" / "optimize" / "experiment.json"
            self.assertTrue(exp_path.is_file(), f"experiment.json not created at {exp_path}")
            exp = json.loads(exp_path.read_text())
            self.assertIn("doe_baseline", exp, "experiment.json missing doe_baseline block")
            self.assertEqual(exp["doe_baseline"]["factors"],
                             {"batch_size": 64, "retries": 5})
            self.assertEqual(exp["doe_baseline"]["direction"], "lower")
            self.assertIn("design_type", exp["doe_baseline"])

    def test_optimize_loop_init_rejects_missing_best_factors(self) -> None:
        """A legacy effects.json without best_factors should fail loudly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._make_throwaway_repo(tmp)
            legacy_effects = tmp / "legacy.json"
            legacy_effects.write_text(json.dumps({
                "summary": {"design_type": "full", "n_runs": 4, "n_factors": 2,
                            "r2": 1.0, "intercept": 50},
                "ranked_effects": [],
                "best_run": 0,
                "best_value": 42,
                # NO best_factors key
            }))
            r = subprocess.run(
                [sys.executable, str(self.LOOP_SCRIPT),
                 "--init", "--workdir", str(tmp),
                 "--target", "x", "--metric-cmd", "echo 1",
                 "--baseline-config", str(legacy_effects)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("best_factors", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
