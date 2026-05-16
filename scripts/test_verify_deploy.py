#!/usr/bin/env python3
"""Tests for verify_deploy.py. Zero third-party deps. Run: python3 test_verify_deploy.py

Strategy:
  - The `vercel` CLI is stubbed with a generated executable shell script placed
    on a temp PATH-shim dir prepended to $PATH. The shim dispatches on argv[1]
    (`ls` vs `inspect`) and emits canned JSON read from env vars, so each test
    controls exactly what `vercel ls` and `vercel inspect` return.
  - HTTP route probing is monkeypatched at verify_deploy._probe so tests don't
    make network calls.
  - The "cli missing" case strips the shim from PATH so shutil.which("vercel")
    returns None.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verify_deploy  # noqa: E402


def _make_vercel_shim(shim_dir: Path, ls_json: str, inspect_json: str) -> None:
    """Write an executable `vercel` shim that returns the given JSON per subcommand."""
    shim = shim_dir / "vercel"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "ls" ] || [ "$1" = "list" ]; then\n'
        f"  cat <<'LSEOF'\n{ls_json}\nLSEOF\n"
        "  exit 0\n"
        'elif [ "$1" = "inspect" ]; then\n'
        f"  cat <<'INSEOF'\n{inspect_json}\nINSEOF\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_failing_vercel_shim(shim_dir: Path) -> None:
    """Write a `vercel` shim that always exits non-zero (auth/link failure)."""
    shim = shim_dir / "vercel"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'echo "Error: Not authorized" >&2\n'
        "exit 1\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class _PathShim:
    """Context manager: prepend a temp dir holding a vercel shim onto PATH."""

    def __init__(self, shim_dir: Path):
        self.shim_dir = shim_dir
        self._orig = None

    def __enter__(self):
        self._orig = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.shim_dir}{os.pathsep}{self._orig}"
        return self

    def __exit__(self, *exc):
        os.environ["PATH"] = self._orig


class _StrippedPath:
    """Context manager: PATH with no `vercel` resolvable (cli-missing case)."""

    def __init__(self):
        self._orig = None

    def __enter__(self):
        self._orig = os.environ.get("PATH", "")
        # An empty-ish PATH that still has nothing named `vercel`.
        os.environ["PATH"] = tempfile.mkdtemp()
        return self

    def __exit__(self, *exc):
        os.environ["PATH"] = self._orig


def _link_vercel(workdir: Path) -> None:
    (workdir / ".vercel").mkdir(parents=True, exist_ok=True)
    (workdir / ".vercel" / "project.json").write_text(
        json.dumps({"projectId": "prj_x", "orgId": "team_x"})
    )


class TestSkippedNoLink(unittest.TestCase):
    def test_no_vercel_link_returns_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            env = verify_deploy.verify(Path(td), [], poll_interval=1, timeout=5)
        self.assertEqual(env["status"], "skipped")
        self.assertEqual(env["reason"], "no vercel link")
        self.assertIsNone(env["deployment_url"])


class TestSkippedCliMissing(unittest.TestCase):
    def test_linked_but_cli_absent_returns_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _link_vercel(wd)
            with _StrippedPath():
                env = verify_deploy.verify(wd, [], poll_interval=1, timeout=5)
        self.assertEqual(env["status"], "skipped")
        self.assertIn("vercel CLI not found", env["reason"])


class TestPassReadyRootOkAuthGate(unittest.TestCase):
    def test_ready_root_200_protected_route_401_is_pass(self):
        ls = json.dumps({"deployments": [{"url": "myapp-abc.vercel.app"}]})
        inspect = json.dumps({"readyState": "READY"})
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _link_vercel(wd)
            shim_dir = Path(tempfile.mkdtemp())
            _make_vercel_shim(shim_dir, ls, inspect)

            # root -> 200, the protected changed route -> 401 (healthy auth gate)
            def fake_probe(url, timeout=20):
                if url.rstrip("/").endswith("vercel.app"):
                    return 200, ""
                if url.endswith("/api/secure"):
                    return 401, ""
                return 200, ""

            orig = verify_deploy._probe
            verify_deploy._probe = fake_probe
            try:
                with _PathShim(shim_dir):
                    env = verify_deploy.verify(
                        wd, ["/api/secure"], poll_interval=1, timeout=5
                    )
            finally:
                verify_deploy._probe = orig

        self.assertEqual(env["status"], "pass", env)
        self.assertEqual(env["state"], "READY")
        # No findings: 401 on a protected route is the encoded healthy heuristic.
        self.assertEqual(env["findings"], [])


class TestFailDeploymentError(unittest.TestCase):
    def test_error_state_is_fail(self):
        ls = json.dumps({"deployments": [{"url": "myapp-bad.vercel.app"}]})
        inspect = json.dumps({"readyState": "ERROR"})
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _link_vercel(wd)
            shim_dir = Path(tempfile.mkdtemp())
            _make_vercel_shim(shim_dir, ls, inspect)
            with _PathShim(shim_dir):
                env = verify_deploy.verify(wd, [], poll_interval=1, timeout=5)
        self.assertEqual(env["status"], "fail")
        self.assertEqual(env["state"], "ERROR")
        self.assertTrue(env["findings"])
        self.assertEqual(env["findings"][0]["render_status"], "ERROR")


class TestFailRoute500(unittest.TestCase):
    def test_ready_but_changed_route_500_is_fail(self):
        ls = json.dumps({"deployments": [{"url": "myapp-r5.vercel.app"}]})
        inspect = json.dumps({"readyState": "READY"})
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _link_vercel(wd)
            shim_dir = Path(tempfile.mkdtemp())
            _make_vercel_shim(shim_dir, ls, inspect)

            def fake_probe(url, timeout=20):
                if url.endswith("/api/broken"):
                    return 500, ""
                return 200, ""  # root ok

            orig = verify_deploy._probe
            verify_deploy._probe = fake_probe
            try:
                with _PathShim(shim_dir):
                    env = verify_deploy.verify(
                        wd, ["/api/broken"], poll_interval=1, timeout=5
                    )
            finally:
                verify_deploy._probe = orig

        self.assertEqual(env["status"], "fail", env)
        self.assertEqual(env["state"], "READY")
        statuses = [f["render_status"] for f in env["findings"]]
        self.assertIn(500, statuses)


class TestSkippedAuthFailure(unittest.TestCase):
    def test_vercel_ls_nonzero_returns_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _link_vercel(wd)
            shim_dir = Path(tempfile.mkdtemp())
            _make_failing_vercel_shim(shim_dir)
            with _PathShim(shim_dir):
                env = verify_deploy.verify(wd, [], poll_interval=1, timeout=5)
        self.assertEqual(env["status"], "skipped")
        self.assertIn("vercel ls failed", env["reason"])


class TestCliEnvelopeShape(unittest.TestCase):
    def test_main_json_dry_on_non_vercel_dir_is_skipped_exit0(self):
        with tempfile.TemporaryDirectory() as td:
            rc = verify_deploy.main(["--workdir", td, "--json"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
