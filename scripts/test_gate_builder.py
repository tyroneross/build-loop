"""Tests for gate_builder.scaffold — Prevention-Pattern spec -> DRAFT inert gate."""
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate_builder as gb  # noqa: E402


def _spec(**kw):
    base = {
        "encoding_target": "gate",
        "prevention_pattern": "When marking done, the system must run the full suite, "
                              "enforced by a verify/merge gate, verified by a regression check.",
        "condition": "marking work done or merging",
        "required_behavior": "run the full suite or declare scope",
        "lever": "a verify/merge gate",
        "actuator": "blocks 'done'/merge until the check passes",
        "verification": "a regression check that fails on the prior behavior",
        "evidence": ["7 regressions slipped a folder-scoped run"],
    }
    base.update(kw)
    return base


def _load_check(gate_dir: Path):
    spec = importlib.util.spec_from_file_location("gen_check", gate_dir / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ScaffoldTests(unittest.TestCase):
    def test_creates_three_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            res = gb.scaffold([_spec()], out)
            slug = res["created"][0]["slug"]
            gd = out / slug
            for f in ("gate.md", "check.py", "test_check.py"):
                self.assertTrue((gd / f).exists(), f)

    def test_gate_md_marks_draft_and_requires_approval(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gb.scaffold([_spec()], out)
            md = next(out.glob("*/gate.md")).read_text()
            self.assertIn("status: draft", md)
            self.assertIn("requires_approval: true", md)
            self.assertIn("activated: false", md)
            self.assertIn("run the full suite", md)  # prevention pattern present

    def test_generated_check_is_inert_raises(self):
        # The whole safety point: a draft gate can neither silently pass nor block.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            res = gb.scaffold([_spec()], out)
            gd = Path(res["created"][0]["path"])
            mod = _load_check(gd)
            with self.assertRaises(NotImplementedError):
                mod.check({})

    def test_test_stub_is_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gb.scaffold([_spec()], out)
            t = next(out.glob("*/test_check.py")).read_text()
            self.assertIn("pytest.mark.skip", t)

    def test_idempotent_second_run_skips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gb.scaffold([_spec()], out)
            res2 = gb.scaffold([_spec()], out)
            self.assertEqual(res2["summary"]["created"], 0)
            self.assertEqual(res2["summary"]["skipped"], 1)

    def test_slug_from_required_behavior(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            res = gb.scaffold([_spec(required_behavior="Run ALL the Tests")], out)
            self.assertEqual(res["created"][0]["slug"], "run-all-the-tests")

    def test_accepts_full_learning_to_draft_output_shape(self):
        # _load_specs should pull enforcement_specs out of a full converter result.
        import io
        payload = '{"proposals": [], "enforcement_specs": [%s], "summary": {}}' % __import__("json").dumps(_spec())
        sys.stdin = io.StringIO(payload)
        try:
            specs = gb._load_specs("-")
        finally:
            sys.stdin = sys.__stdin__
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["encoding_target"], "gate")


if __name__ == "__main__":
    unittest.main()
