import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import effort_plan as ep  # noqa: E402


def lk(typical, slow, n=20, scope="platform+kind+size"):
    return {"n": n, "scope": scope, "typical_min": typical, "slow_min": slow}


CHUNKS = [
    {"id": "schema", "platform": "database", "lines": 80, "depends_on": []},
    {"id": "api", "platform": "node", "lines": 300, "depends_on": ["schema"]},
    {"id": "ui", "platform": "web", "lines": 400, "depends_on": ["schema"]},
    {"id": "e2e", "platform": "web", "lines": 120, "depends_on": ["api", "ui"]},
]


class Waves(unittest.TestCase):
    def test_levels_follow_dependencies(self):
        self.assertEqual(ep.waves(CHUNKS), [["schema"], ["api", "ui"], ["e2e"]])

    def test_cycle_and_unknown_dependency_raise(self):
        with self.assertRaises(ValueError):
            ep.waves([{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}])
        with self.assertRaises(ValueError):
            ep.waves([{"id": "a", "depends_on": ["ghost"]}])


class Plan(unittest.TestCase):
    def setUp(self):
        self.lookups = {"schema": lk(10, 20), "api": lk(5, 8), "ui": lk(20, 30), "e2e": lk(4, 6)}

    def test_critical_path_takes_the_longest_chain(self):
        plan = ep.build_plan(CHUNKS, self.lookups)
        self.assertEqual(plan["critical_path"]["ids"], ["schema", "ui", "e2e"])
        self.assertEqual(plan["critical_path"]["typical_min"], 34.0)
        self.assertEqual(plan["critical_path"]["slow_min"], 56.0)
        self.assertEqual(plan["serial_total"]["typical_min"], 39.0)

    def test_notes_flag_lopsided_wave_and_integration_checkpoint(self):
        notes = ep.build_plan(CHUNKS, self.lookups)["notes"]
        self.assertTrue(any("wave 2: ui" in n and "dispatch it first" in n for n in notes), notes)
        self.assertTrue(any("integrate and run tests before wave 3" in n for n in notes), notes)

    def test_low_confidence_and_missing_estimates_are_named(self):
        self.lookups["api"] = lk(5, 8, n=2)
        self.lookups["e2e"] = None
        notes = ep.build_plan(CHUNKS, self.lookups)["notes"]
        self.assertTrue(any(n.startswith("api: low confidence") for n in notes), notes)
        self.assertTrue(any(n.startswith("e2e: no past jobs") for n in notes), notes)


class Degrade(unittest.TestCase):
    def test_missing_tool_reports_unavailable_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            chunks = Path(d) / "c.json"
            chunks.write_text(json.dumps(CHUNKS))
            with mock.patch.object(ep, "EFFORT_SCRIPT", Path(d) / "missing.py"), \
                    mock.patch("builtins.print") as out:
                self.assertEqual(ep.main(["plan", "--chunks", str(chunks)]), 0)
            self.assertIn("unavailable", out.call_args[0][0])

    def test_record_requires_a_gut_estimate(self):
        with tempfile.TemporaryDirectory() as d:
            chunks = Path(d) / "c.json"
            chunks.write_text(json.dumps(CHUNKS))
            fake = {"n": 20, "scope": "platform", "typical_min": 5, "slow_min": 9}
            with mock.patch.object(ep, "lookup", return_value=fake), \
                    mock.patch.object(ep, "EFFORT_SCRIPT", Path(__file__)), \
                    mock.patch("builtins.print"):
                self.assertEqual(ep.main(["plan", "--chunks", str(chunks), "--record"]), 2)


if __name__ == "__main__":
    unittest.main()
