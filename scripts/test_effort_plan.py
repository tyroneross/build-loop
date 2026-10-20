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
        plan = ep.build_plan(CHUNKS, self.lookups)
        self.assertEqual(plan["status"], "unavailable")
        self.assertIn("e2e", plan["reason"])
        self.assertNotIn("critical_path", plan)



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


class RecordedPlan(unittest.TestCase):
    def test_empty_store_is_unavailable(self):
        plan = ep.build_plan(CHUNKS, {c["id"]: {"n": 0, "scope": "all"} for c in CHUNKS})
        self.assertEqual(plan["status"], "unavailable")
        self.assertNotIn("serial_total", plan)

    def test_record_and_close_use_same_estimate_once_for_exact_run(self):
        with tempfile.TemporaryDirectory() as d:
            chunks = Path(d) / "chunks.json"
            chunks.write_text(json.dumps(CHUNKS))
            estimates = {"schema": lk(10,20), "api": lk(5,8), "ui": lk(20,30), "e2e": lk(4,6)}
            with mock.patch.object(ep, "EFFORT_SCRIPT", Path(__file__)), mock.patch.object(ep, "lookup", side_effect=lambda c: estimates[c["id"]]), mock.patch.object(ep, "run_effort") as remote, mock.patch("builtins.print"), mock.patch.object(ep.time, "time", return_value=100):
                self.assertEqual(ep.main(["plan", "--chunks", str(chunks), "--workdir", d, "--run-id", "run-one", "--record", "--gut", "40"]), 0)
                remote.assert_not_called()
            with mock.patch("builtins.print"), mock.patch.object(ep.time, "time", return_value=700):
                self.assertEqual(ep.main(["close", "--workdir", d, "--run-id", "run-one"]), 0)
            result = Path(d) / ".build-loop/runs/run-one/effort-result.json"
            original = result.read_bytes()
            data = json.loads(original)
            self.assertEqual(data["lookup_min"], 34)
            self.assertEqual(data["actual_min"], 10)
            with mock.patch("builtins.print"), mock.patch.object(ep.time, "time", return_value=1300):
                ep.main(["close", "--workdir", d, "--run-id", "run-one"])
                ep.main(["close", "--workdir", d, "--run-id", "run-two"])
            self.assertEqual(result.read_bytes(), original)
            self.assertFalse((Path(d) / ".build-loop/runs/run-two/effort-result.json").exists())

    def test_lookup_timeout_degrades(self):
        with mock.patch.object(ep, "EFFORT_SCRIPT", Path(__file__)), mock.patch.object(ep.subprocess, "run", side_effect=ep.subprocess.TimeoutExpired("lookup",10)):
            self.assertIsNone(ep.run_effort(["lookup"]))


class RepairBoundaries(unittest.TestCase):
    def test_replan_prints_plan_and_preserves_first_prediction(self):
        with tempfile.TemporaryDirectory() as d:
            chunks = Path(d) / "chunks.json"
            chunks.write_text(json.dumps(CHUNKS))
            argv = ["plan", "--chunks", str(chunks), "--workdir", d, "--run-id", "replan", "--record", "--gut", "20", "--json"]
            with mock.patch.object(ep, "EFFORT_SCRIPT", Path(__file__)), mock.patch.object(ep, "lookup", return_value=lk(2,3)), mock.patch("builtins.print") as out:
                self.assertEqual(ep.main(argv), 0)
                state = Path(d) / ".build-loop/runs/replan/effort.json"
                original = state.read_bytes()
                self.assertEqual(ep.main(argv), 0)
                self.assertEqual(json.loads(out.call_args[0][0])["status"], "ok")
                self.assertEqual(state.read_bytes(), original)

    def test_failed_publication_leaves_no_partial_target(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "result.json"
            with mock.patch.object(ep.os, "link", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError): ep.publish_once(path, {"payload": "x" * 50000})
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_concurrent_publication_keeps_one_complete_result(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "result.json"
            def write(value):
                try: ep.publish_once(path, {"payload": value * 50000})
                except FileExistsError: return "lost"
                return "won"
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(write, ("a", "b")))
            self.assertEqual(sorted(outcomes), ["lost", "won"])
            self.assertIn(json.loads(path.read_text())["payload"], ("a" * 50000, "b" * 50000))
            self.assertEqual(len(list(Path(d).iterdir())), 1)

    def test_invalid_saved_json_degrades(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / ".build-loop/runs/bad/effort.json"
            state.parent.mkdir(parents=True); state.write_text("{")
            with mock.patch("builtins.print") as out:
                self.assertEqual(ep.main(["close", "--workdir", d, "--run-id", "bad"]), 0)
                self.assertEqual(json.loads(out.call_args[0][0])["status"], "unavailable")

    def test_malformed_chunks_and_lookup_replies_degrade(self):
        for reply in ([], {"n": "five"}, {"n": 2, "scope": None}, {"n": 2, "scope": "all", "typical_min": True, "slow_min": 3}):
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as d:
                chunks = Path(d) / "chunks.json"; chunks.write_text(json.dumps(CHUNKS))
                with mock.patch.object(ep, "EFFORT_SCRIPT", Path(__file__)), mock.patch.object(ep, "lookup", return_value=reply), mock.patch("builtins.print") as out:
                    self.assertEqual(ep.main(["plan", "--chunks", str(chunks), "--json"]), 0)
                    self.assertEqual(json.loads(out.call_args[0][0])["status"], "unavailable")
        with tempfile.TemporaryDirectory() as d:
            chunks = Path(d) / "chunks.json"; chunks.write_text('[{"id":"one","lines":"invalid"}]')
            with mock.patch("builtins.print") as out:
                self.assertEqual(ep.main(["plan", "--chunks", str(chunks)]), 0)
                self.assertEqual(json.loads(out.call_args[0][0])["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
