#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for install_self_review.py.

Run: uv run pytest scripts/test_install_self_review.py -q

Scope: plist XML generation + config parsing only.
Does NOT call launchctl or write to ~/Library (uses tmp dirs via --plist-dir override).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

# Import the installer directly (no subprocess needed for unit tests)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_self_review as installer


def _make_repo(tmp: Path, config: dict | None = None) -> Path:
    """Create a minimal repo layout inside tmp."""
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "scripts").mkdir()
    if config is not None:
        bl = repo / ".build-loop"
        bl.mkdir()
        (bl / "config.json").write_text(json.dumps({"selfReview": config}))
    return repo


def _parse_plist(xml_text: str) -> dict:
    """Parse plist XML and return a flat {key: value} dict for the top-level <dict>."""
    root = ET.fromstring(xml_text)
    top_dict = root.find("dict")
    assert top_dict is not None, "no <dict> in plist"

    result: dict = {}
    children = list(top_dict)
    i = 0
    while i < len(children):
        key_elem = children[i]
        assert key_elem.tag == "key", f"expected <key>, got <{key_elem.tag}>"
        k = key_elem.text
        val_elem = children[i + 1]
        result[k] = val_elem
        i += 2
    return result


class TestPlistGeneration(unittest.TestCase):
    """XML generation for daily (light) and weekly (deep) plists."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _daily_plist_xml(self, repo: Path) -> str:
        return installer._plist_xml(
            label=installer.LABEL_LIGHT,
            mode="light",
            repo=repo,
            cadence="daily",
            plist_dir=self.tmp,
            log_suffix="light",
        )

    def _weekly_plist_xml(self, repo: Path) -> str:
        return installer._plist_xml(
            label=installer.LABEL_DEEP,
            mode="deep",
            repo=repo,
            cadence="weekly",
            plist_dir=self.tmp,
            log_suffix="deep",
        )

    def test_daily_plist_hour_9(self) -> None:
        repo = _make_repo(self.tmp)
        xml = self._daily_plist_xml(repo)
        top = _parse_plist(xml)

        cal = top["StartCalendarInterval"]
        assert cal.tag == "dict"
        cal_items = {
            list(cal)[i * 2].text: int(list(cal)[i * 2 + 1].text)
            for i in range(len(list(cal)) // 2)
        }
        self.assertEqual(cal_items["Hour"], 9)
        self.assertEqual(cal_items["Minute"], 0)
        self.assertNotIn("Weekday", cal_items, "daily plist must not have Weekday")

    def test_daily_plist_program_arguments(self) -> None:
        repo = _make_repo(self.tmp)
        xml = self._daily_plist_xml(repo)
        top = _parse_plist(xml)

        array = top["ProgramArguments"]
        self.assertEqual(array.tag, "array")
        args = [e.text for e in array]
        self.assertEqual(args[0], "/bin/bash")
        self.assertIn("self_review_run.sh", args[1])
        self.assertEqual(args[2], "light")

    def test_daily_plist_repo_env_var(self) -> None:
        repo = _make_repo(self.tmp)
        xml = self._daily_plist_xml(repo)
        top = _parse_plist(xml)

        env_dict = top["EnvironmentVariables"]
        self.assertEqual(env_dict.tag, "dict")
        env_items = list(env_dict)
        keys = [env_items[i].text for i in range(0, len(env_items), 2)]
        vals = [env_items[i + 1].text for i in range(0, len(env_items), 2)]
        env = dict(zip(keys, vals))
        self.assertIn("BUILDLOOP_SELF_REVIEW_REPO", env)
        self.assertEqual(env["BUILDLOOP_SELF_REVIEW_REPO"], str(repo))

    def test_weekly_plist_weekday_0(self) -> None:
        repo = _make_repo(self.tmp)
        xml = self._weekly_plist_xml(repo)
        top = _parse_plist(xml)

        cal = top["StartCalendarInterval"]
        cal_items = {
            list(cal)[i * 2].text: int(list(cal)[i * 2 + 1].text)
            for i in range(len(list(cal)) // 2)
        }
        self.assertEqual(cal_items["Weekday"], 0, "weekly plist must schedule on Sunday (0)")
        self.assertEqual(cal_items["Hour"], 3)
        self.assertEqual(cal_items["Minute"], 0)

    def test_weekly_plist_mode_argument(self) -> None:
        repo = _make_repo(self.tmp)
        xml = self._weekly_plist_xml(repo)
        top = _parse_plist(xml)
        args = [e.text for e in top["ProgramArguments"]]
        self.assertEqual(args[2], "deep")

    def test_absolute_paths_baked_in(self) -> None:
        """ProgramArguments[1] (script path) and StandardOutPath must be absolute."""
        repo = _make_repo(self.tmp)
        xml = self._daily_plist_xml(repo)
        top = _parse_plist(xml)
        # ProgramArguments array children: [/bin/bash, <script>, light]
        args = [e.text for e in top["ProgramArguments"]]
        script_path = args[1]
        self.assertTrue(
            script_path.startswith("/"),
            f"script path must be absolute; got {script_path!r}",
        )
        log_path = top["StandardOutPath"].text
        self.assertTrue(
            log_path.startswith("/"),
            f"log path must be absolute; got {log_path!r}",
        )

    def test_label_correct(self) -> None:
        repo = _make_repo(self.tmp)
        xml_light = self._daily_plist_xml(repo)
        xml_deep = self._weekly_plist_xml(repo)
        self.assertIn(installer.LABEL_LIGHT, xml_light)
        self.assertIn(installer.LABEL_DEEP, xml_deep)


class TestConfigParsing(unittest.TestCase):
    """_load_config reads the selfReview block and applies defaults."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_defaults_when_no_config_file(self) -> None:
        repo = _make_repo(self.tmp)
        config = installer._load_config(repo)
        self.assertTrue(config["enabled"])
        self.assertEqual(config["autonomy"], "apply_push")
        self.assertEqual(config["light"], "daily")
        self.assertEqual(config["deep"], "weekly")

    def test_enabled_false_from_config(self) -> None:
        repo = _make_repo(self.tmp, config={"enabled": False})
        config = installer._load_config(repo)
        self.assertFalse(config["enabled"])

    def test_autonomy_override(self) -> None:
        repo = _make_repo(self.tmp, config={"autonomy": "propose"})
        config = installer._load_config(repo)
        self.assertEqual(config["autonomy"], "propose")

    def test_cadence_disabled(self) -> None:
        repo = _make_repo(self.tmp, config={"light": "disabled"})
        config = installer._load_config(repo)
        self.assertEqual(config["light"], "disabled")


class TestInstallDisabledNoOp(unittest.TestCase):
    """install subcommand does nothing when enabled:false."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_plists_when_disabled(self) -> None:
        plist_dir = self.tmp / "launchagents"
        plist_dir.mkdir()
        repo = _make_repo(self.tmp, config={"enabled": False})

        # Patch launchctl calls so they never fire in tests
        import unittest.mock as mock

        with mock.patch.object(installer, "_bootout", return_value=(True, "ok")), \
             mock.patch.object(installer, "_load", return_value=(True, "ok")):
            args = argparse.Namespace(plist_dir=str(plist_dir), repo=str(repo))
            result = installer.cmd_install(args)

        self.assertEqual(result["status"], "noop")
        plists = list(plist_dir.glob("*.plist"))
        self.assertEqual(plists, [], "no plists should be written when enabled=false")


import argparse  # noqa: E402  (imported here for the test above to find it)


if __name__ == "__main__":
    unittest.main()
