"""Real native-process and pytest lifecycle tests; no live services."""
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verification_progress as progress


def wait_for(predicate, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError, IndexError):
            pass
        time.sleep(0.02)
    raise AssertionError("progress condition did not become true")


class NativeProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = progress.native_runner()
        if cls.binary is None:
            raise unittest.SkipTest("rustc unavailable; portable logging is covered separately")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def native(self, code, timeout=0):
        directory = self.root / "native"
        directory.mkdir()
        command = [str(self.binary), "--log-dir", str(directory), "--cwd", str(self.root),
                   "--timeout-ms", str(timeout), "--", sys.executable, "-c", code]
        return command, directory

    def test_large_output_nonzero_exit_and_no_overwrite(self):
        command, directory = self.native("import os; os.write(1,b'a'*1000000); os.write(2,b'b'*1000000); raise SystemExit(7)")
        result = subprocess.run(command, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual((directory / "stdout.log").read_bytes(), b"a" * 1000000)
        self.assertEqual((directory / "stderr.log").read_bytes(), b"b" * 1000000)
        self.assertEqual(json.loads((directory / "runner-status.json").read_text())["exit_code"], 7)
        again = subprocess.run(command, capture_output=True, timeout=15)
        self.assertEqual(again.returncode, 125)
        self.assertEqual((directory / "stdout.log").stat().st_size, 1000000)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cancellation")
    def test_cancellation_stops_sigterm_resistant_descendants(self):
        marker = self.root / "escaped"
        grandchild = "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(3); pathlib.Path(" + repr(str(marker)) + ").write_text('escaped')"
        code = "import subprocess,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); subprocess.Popen([" + repr(sys.executable) + ",'-c'," + repr(grandchild) + "]); print('ready',flush=True); time.sleep(30)"
        command, directory = self.native(code)
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for(lambda: "ready" in (directory / "stdout.log").read_text())
            process.send_signal(signal.SIGINT)
            self.assertEqual(process.wait(timeout=8), 130)
            self.assertEqual(json.loads((directory / "runner-status.json").read_text())["state"], "cancelled")
            time.sleep(1.2)
            self.assertFalse(marker.exists(), "owned descendant survived cancellation")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            status = directory / "runner-status.json"
            if status.exists():
                try:
                    os.killpg(json.loads(status.read_text())["child_pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_active_test_visible_during_setup_before_completion(self):
        test = self.root / "test_live.py"
        test.write_text("import pathlib,time,pytest\n@pytest.fixture\ndef waiting():\n while not pathlib.Path('release').exists(): time.sleep(.02)\ndef test_wait(waiting): assert True\n")
        script = "from pathlib import Path; import verification_progress as p; p.run_logged(" + repr([sys.executable, "-m", "pytest", "-q", str(test)]) + ",cwd=Path(" + repr(str(self.root)) + "),timeout=None,directory=Path(" + repr(str(self.root / "logs")) + "))"
        env = dict(os.environ, PYTHONPATH=str(progress.HERE))
        process = subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            def current():
                paths = list((self.root / "logs").glob("*/pytest-status.json"))
                state = json.loads(paths[0].read_text())
                return state if state["active_test"] and state["phase"] == "setup" else None
            state = wait_for(current)
            self.assertIn("test_wait", state["active_test"])
            self.assertIsNone(process.poll(), "test must still be in flight")
        finally:
            (self.root / "release").touch()
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
        self.assertEqual(code, 0)
        directory = next((self.root / "logs").iterdir())
        events = [json.loads(line)["event"] for line in (directory / "pytest-events.jsonl").read_text().splitlines()]
        self.assertIn("test_start", events)
        self.assertEqual(events[-1], "session_finish")
        self.assertEqual(json.loads((directory / "pytest-status.json").read_text())["completed"], 1)

    def test_explicit_deadline_preserves_output_and_status(self):
        test = self.root / "test_wait.py"
        test.write_text("import time\ndef test_wait():\n print('started',flush=True)\n time.sleep(30)\n")
        with self.assertRaises(progress.ProgressTimeout) as caught:
            progress.run_logged([sys.executable, "-m", "pytest", "-s", "-q", str(test)], cwd=self.root, timeout=2, directory=self.root / "logs")
        error = caught.exception
        self.assertIn(b"started", error.stdout or b"")
        self.assertEqual(json.loads(Path(error.progress["runner"]).read_text())["state"], "timed_out")
        self.assertIn("test_wait", json.loads(Path(error.progress["tests"]).read_text())["active_test"])

    def test_parallel_runs_have_separate_logs_and_preserve_failure(self):
        good = self.root / "test_good.py"
        bad = self.root / "test_bad.py"
        good.write_text("def test_good(): assert True\n")
        bad.write_text("def test_bad(): assert False\n")
        def run(test):
            return progress.run_logged([sys.executable, "-m", "pytest", "-q", str(test)], cwd=self.root, timeout=None, directory=self.root / "logs")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [good, bad]))
        self.assertEqual([result.returncode for result, _ in results], [0, 1])
        self.assertNotEqual(results[0][1]["directory"], results[1][1]["directory"])

    @unittest.skipUnless(os.name == "posix", "POSIX parent death and process groups")
    def test_public_cli_sigterm_cleans_up_native_work(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        marker = self.root / "escaped"
        child = "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(3); pathlib.Path(" + repr(str(marker)) + ").touch()"
        (scripts / "test_wait.py").write_text(
            "import signal,subprocess,sys,time,pathlib\ndef test_wait():\n"
            " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            " subprocess.Popen([sys.executable,'-c'," + repr(child) + "])\n"
            " pathlib.Path('ready').touch()\n time.sleep(30)\n"
        )
        command = [sys.executable, str(progress.HERE / "self_mod_verify.py"), "--workdir", str(self.root),
                   "--scope", "full", "--progress-dir", str(self.root / "logs"), "--json"]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for(lambda: (self.root / "ready").exists(), seconds=20)
            directory = next((self.root / "logs").iterdir())
            state = json.loads((directory / "pytest-status.json").read_text())
            self.assertEqual(state["phase"], "call")
            self.assertIsNone(state["outcome"])
            status = json.loads((directory / "runner-status.json").read_text())
            self.assertNotEqual(status["supervisor_pid"], status["child_pid"])
            process.terminate()
            self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)
            wait_for(lambda: json.loads((directory / "runner-status.json").read_text())["state"] == "cancelled", seconds=8)
            time.sleep(1.2)
            self.assertFalse(marker.exists(), "work survived termination of public CLI")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for status_file in (self.root / "logs").glob("*/runner-status.json"):
                status = json.loads(status_file.read_text())
                try:
                    os.killpg(status["child_pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_output_tail_is_bounded_and_raw_log_is_complete(self):
        test = self.root / "test_output.py"
        size = progress.TAIL_BYTES + 1024 * 1024
        test.write_text("import os\ndef test_output():\n os.write(1,b'x'*" + str(size) + ")\n")
        result, info = progress.run_logged([sys.executable, "-m", "pytest", "-s", "-q", str(test)], cwd=self.root, timeout=None, directory=self.root / "logs")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(info["output_tail_truncated"])
        self.assertLessEqual(len(result.stdout), progress.TAIL_BYTES)
        self.assertGreaterEqual(Path(info["stdout"]).stat().st_size, size)

    def test_compilation_uses_snapshot_of_hashed_source(self):
        source = self.root / "progress_runner.rs"
        source.write_text('fn main() { println!("original"); }')
        original_run = subprocess.run
        def edit_during_build(command, **kwargs):
            if "--edition=2021" in command:
                source.write_text('fn main() { println!("changed"); }')
            return original_run(command, **kwargs)
        with mock.patch.object(progress, "HERE", self.root), mock.patch.object(Path, "home", return_value=self.root / "home"), mock.patch.object(subprocess, "run", side_effect=edit_during_build):
            binary = progress.native_runner()
        result = original_run([str(binary)], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "original")


class PortableProgressTests(unittest.TestCase):
    def test_interruption_with_partial_summary_does_not_revert(self):
        import self_mod_verify as gate
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts" / "test_sample.py").write_text("def test_ok(): pass\n")
            with mock.patch.object(gate, "_find_runner", return_value=[sys.executable, "-m", "pytest"]), mock.patch.object(gate, "_runner_has_pytest_timeout", return_value=False), mock.patch.object(progress, "run_logged", return_value=(subprocess.CompletedProcess([], 2, "1 passed in 0.1s", "Interrupted"), {"directory": "logs"})), mock.patch.object(gate, "_revert_files") as revert:
                result, code = gate.verify(workdir=root, scope="changed", changed_files=["scripts/test_sample.py"], auto_revert=True, progress_dir=root / "logs")
            self.assertEqual(code, 2)
            self.assertEqual(result["verdict"], "error")
            revert.assert_not_called()

    def test_supervisor_error_is_not_no_tests(self):
        import self_mod_verify as gate
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts" / "test_sample.py").write_text("def test_ok(): pass\n")
            error = progress.ProgressError("native launch failed", {"directory": str(root / "logs")})
            with mock.patch.object(gate, "_find_runner", return_value=[sys.executable, "-m", "pytest"]), mock.patch.object(gate, "_runner_has_pytest_timeout", return_value=False), mock.patch.object(progress, "run_logged", side_effect=error):
                result, code = gate.verify(workdir=root, scope="changed", changed_files=["scripts/test_sample.py"], auto_revert=False, progress_dir=root / "logs")
            self.assertEqual(code, 2)
            self.assertEqual(result["verdict"], "error")
            self.assertEqual(result["error_reason"], "ProgressRunner")
            self.assertEqual(result["progress"], error.progress)

    def test_no_rust_still_writes_output_and_test_events(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(progress, "native_runner", return_value=None):
            root = Path(temporary)
            test = root / "test_example.py"
            test.write_text("def test_example(): assert True\n")
            result, info = progress.run_logged([sys.executable, "-m", "pytest", "-q", str(test)], cwd=root, timeout=None, directory=root / "logs")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(info["backend"], "portable")
            self.assertTrue(Path(info["test_events"]).is_file())
            self.assertIn("1 passed", Path(info["stdout"]).read_text())


if __name__ == "__main__":
    unittest.main()
