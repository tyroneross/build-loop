#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for project_resolver and _paths.

Runs entirely in-memory + tmp_path; no Postgres, no network. Designed to
pass with no env vars set (legacy compatibility) and with each env var
set in turn (override behavior).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _paths  # type: ignore  # noqa: E402
import project_resolver  # type: ignore  # noqa: E402


class PathsResolverTests(unittest.TestCase):
    def test_fresh_install_uses_neutral_default(self) -> None:
        """No env override + no legacy dir on disk → neutral fresh-install root.

        Drives a clean isolated HOME so the resolver cannot see the author's
        real legacy store. Asserts the invariant (neutral root), not the
        author's mutable filesystem state.
        """
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}, clear=False):
                os.environ.pop("AGENT_MEMORY_ROOT", None)
                os.environ.pop("BUILD_LOOP_MEMORY_ROOT", None)
                os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
                root = _paths.memory_store_root()
                self.assertEqual(
                    root,
                    Path(os.path.expanduser(_paths.NEUTRAL_MEMORY_STORE_ROOT)),
                )
                # DEFAULT_MEMORY_STORE_ROOT is the back-compat alias for the
                # neutral default.
                self.assertEqual(
                    _paths.DEFAULT_MEMORY_STORE_ROOT,
                    _paths.NEUTRAL_MEMORY_STORE_ROOT,
                )

    def test_legacy_personal_root_detected_when_present(self) -> None:
        """No env override + legacy personal dir EXISTS → resolves to legacy.

        This is the zero-config path that keeps pre-neutral-default machines
        working unchanged.
        """
        with tempfile.TemporaryDirectory() as home:
            legacy = Path(home) / "dev" / "git-folder" / "build-loop-memory"
            legacy.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"HOME": home}, clear=False):
                os.environ.pop("AGENT_MEMORY_ROOT", None)
                os.environ.pop("BUILD_LOOP_MEMORY_ROOT", None)
                os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
                self.assertEqual(_paths.memory_store_root(), legacy)

    def test_root_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"BUILD_LOOP_MEMORY_STORE_ROOT": "/tmp/custom-root"}, clear=False):
            self.assertEqual(_paths.memory_store_root(), Path("/tmp/custom-root"))

    def test_agent_memory_root_is_compat_alias(self) -> None:
        with mock.patch.dict(os.environ, {
            "BUILD_LOOP_MEMORY_STORE_ROOT": "",
            "BUILD_LOOP_MEMORY_ROOT": "",
            "AGENT_MEMORY_ROOT": "/tmp/custom-root",
        }, clear=False):
            self.assertEqual(_paths.agent_memory_root(), Path("/tmp/custom-root"))

    def test_project_decisions_dir_includes_project(self) -> None:
        with mock.patch.dict(os.environ, {
            "BUILD_LOOP_MEMORY_STORE_ROOT": "",
            "BUILD_LOOP_MEMORY_ROOT": "",
            "AGENT_MEMORY_ROOT": "/tmp/x",
        }, clear=False):
            self.assertEqual(
                _paths.project_decisions_dir("build-loop"),
                Path("/tmp/x/projects/build-loop/decisions"),
            )

    def test_project_decisions_dir_empty_project_falls_back_to_unscoped(self) -> None:
        with mock.patch.dict(os.environ, {
            "BUILD_LOOP_MEMORY_STORE_ROOT": "",
            "BUILD_LOOP_MEMORY_ROOT": "",
            "AGENT_MEMORY_ROOT": "/tmp/x",
        }, clear=False):
            self.assertEqual(
                _paths.project_decisions_dir(""),
                Path("/tmp/x/projects/_unscoped/decisions"),
            )

    def test_top_level_lanes_and_indexes(self) -> None:
        with mock.patch.dict(os.environ, {
            "BUILD_LOOP_MEMORY_STORE_ROOT": "",
            "BUILD_LOOP_MEMORY_ROOT": "",
            "AGENT_MEMORY_ROOT": "/tmp/x",
        }, clear=False):
            self.assertEqual(_paths.top_level_lessons_dir(), Path("/tmp/x/lessons"))
            self.assertEqual(_paths.top_level_debugging_dir(), Path("/tmp/x/debugging"))
            self.assertEqual(_paths.top_level_design_dir(), Path("/tmp/x/design"))
            self.assertEqual(_paths.top_level_product_dir(), Path("/tmp/x/product"))
            self.assertEqual(_paths.memory_indexes_dir(), Path("/tmp/x/indexes"))

    def test_legacy_decisions_dir(self) -> None:
        self.assertEqual(
            _paths.legacy_decisions_dir(Path("/repo/build-loop")),
            Path("/repo/build-loop/.episodic/decisions"),
        )

    def test_default_schema_is_personal_memory(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MEMORY_SCHEMA", None)
            self.assertEqual(_paths.default_schema(), "personal_memory")

    def test_default_schema_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_MEMORY_SCHEMA": "tmp_test"}, clear=False):
            self.assertEqual(_paths.default_schema(), "tmp_test")

    def test_legacy_schema_constant(self) -> None:
        self.assertEqual(_paths.legacy_schema(), "build_loop_memory")

    def test_dual_write_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MEMORY_DUAL_WRITE", None)
            self.assertFalse(_paths.dual_write_enabled())

    def test_dual_write_on_when_set_to_1(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_MEMORY_DUAL_WRITE": "1"}, clear=False):
            self.assertTrue(_paths.dual_write_enabled())

    def test_dual_write_off_for_non_1_values(self) -> None:
        for v in ("0", "true", "yes", "", "TRUE"):
            with mock.patch.dict(os.environ, {"AGENT_MEMORY_DUAL_WRITE": v}, clear=False):
                self.assertFalse(_paths.dual_write_enabled(), f"unexpected truthy for {v!r}")

    def test_cutover_lock_detection(self) -> None:
        # Use a temp file path via env-overridden constant probe — patch the
        # module constant for the duration of the test.
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lock"
            with mock.patch.object(_paths, "CUTOVER_LOCK_PATH", str(lock)):
                self.assertFalse(_paths.cutover_lock_active())
                lock.touch()
                self.assertTrue(_paths.cutover_lock_active())


class ProjectResolverParseTests(unittest.TestCase):
    def test_parse_default_only(self) -> None:
        text = "default: my_default\n"
        data = project_resolver._parse_projects_yaml(text)
        self.assertEqual(data["default"], "my_default")
        self.assertEqual(data["projects"], [])

    def test_parse_projects_block(self) -> None:
        text = (
            "default: _unscoped\n"
            "projects:\n"
            "  - path: ~/repos/example-app\n"
            "    project: example-app\n"
            "  - path: ~/repos/another-app\n"
            "    project: another-app\n"
        )
        data = project_resolver._parse_projects_yaml(text)
        self.assertEqual(data["default"], "_unscoped")
        self.assertEqual(len(data["projects"]), 2)
        self.assertEqual(data["projects"][0]["project"], "example-app")

    def test_parse_skips_comments_and_blanks(self) -> None:
        text = (
            "# top comment\n"
            "\n"
            "default: foo\n"
            "  # nested comment line\n"
            "projects:\n"
            "  - path: ~/x\n"
            "    project: x\n"
            "  # another comment\n"
        )
        data = project_resolver._parse_projects_yaml(text)
        self.assertEqual(data["default"], "foo")
        self.assertEqual(len(data["projects"]), 1)


class ResolveProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "memroot"
        (self.root / ".config").mkdir(parents=True)
        (self.root / ".config" / "projects.yaml").write_text(
            "default: _unscoped\n"
            "projects:\n"
            "  - path: /repo/build-loop\n"
            "    project: build-loop\n"
            "  - path: /repo/build-loop/sub\n"
            "    project: build-loop-sub\n"
            "  - path: /repo/example-app\n"
            "    project: example-app\n",
            encoding="utf-8",
        )
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "BUILD_LOOP_MEMORY_STORE_ROOT": "",
                "BUILD_LOOP_MEMORY_ROOT": "",
                "AGENT_MEMORY_ROOT": str(self.root),
            },
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_exact_match(self) -> None:
        self.assertEqual(project_resolver.resolve_project("/repo/build-loop"), "build-loop")

    def test_prefix_match_under_repo(self) -> None:
        self.assertEqual(
            project_resolver.resolve_project("/repo/build-loop/scripts"),
            "build-loop",
        )

    def test_longest_prefix_wins(self) -> None:
        self.assertEqual(
            project_resolver.resolve_project("/repo/build-loop/sub/inner"),
            "build-loop-sub",
        )

    def test_default_when_no_match(self) -> None:
        self.assertEqual(
            project_resolver.resolve_project("/somewhere/else"),
            "_unscoped",
        )

    def test_sibling_paths_do_not_collide(self) -> None:
        # /repo/build-loop-other is NOT under /repo/build-loop because the
        # boundary check requires os.sep, not just startswith.
        self.assertEqual(
            project_resolver.resolve_project("/repo/build-loop-other"),
            "_unscoped",
        )


class ResolveProjectMissingYamlTests(unittest.TestCase):
    def test_missing_yaml_returns_unscoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {
                "BUILD_LOOP_MEMORY_STORE_ROOT": "",
                "BUILD_LOOP_MEMORY_ROOT": "",
                "AGENT_MEMORY_ROOT": tmp,
            }, clear=False):
                self.assertEqual(
                    project_resolver.resolve_project("/anywhere"),
                    "_unscoped",
                )


class ResolveProjectRegistryIntegrationTests(unittest.TestCase):
    """resolve_project wires the v2 registry between derive + fallback.

    derive_slug_from_cwd is patched to isolate the registry step from the
    filesystem/git deriver (which needs a real repo).
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "memroot"
        (self.root / "config").mkdir(parents=True)
        (self.root / "config" / "projects.yaml").write_text(
            "default: _unscoped\n"
            "projects:\n"
            "  - id: rosslabs-ai-assistant\n"
            "    canonical_slug: rosslabs-ai-assistant\n"
            "    paths: [/repo/RossLabs-AI-Assistant]\n"
            "    aliases: [ai-assistant]\n"
            "    derived_from: null\n"
            "    depends_on: []\n"
            "  - id: build-loop\n"
            "    canonical_slug: build-loop\n"
            "    paths: [/repo/build-loop]\n"
            "    aliases: []\n"
            "    derived_from: null\n"
            "    depends_on: []\n",
            encoding="utf-8",
        )
        self._env = mock.patch.dict(os.environ, {
            "BUILD_LOOP_MEMORY_STORE_ROOT": "",
            "BUILD_LOOP_MEMORY_ROOT": "",
            "AGENT_MEMORY_ROOT": str(self.root),
        }, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_old_slug_resolves_to_canonical_via_alias(self) -> None:
        # A repo renamed to RossLabs-AI-Assistant-v3 derives 'ai-assistant'
        # (via a stale pin, say) → alias walk lands on the canonical id.
        with mock.patch.object(project_resolver, "derive_slug_from_cwd",
                               return_value="ai-assistant"):
            self.assertEqual(
                project_resolver.resolve_project("/repo/RossLabs-AI-Assistant-v3"),
                "rosslabs-ai-assistant",
            )

    def test_registered_slug_resolves_to_itself(self) -> None:
        with mock.patch.object(project_resolver, "derive_slug_from_cwd",
                               return_value="build-loop"):
            self.assertEqual(project_resolver.resolve_project("/repo/build-loop"),
                             "build-loop")

    def test_unregistered_repo_is_its_own_id(self) -> None:
        # Zero registry entry → the derived candidate is returned verbatim.
        with mock.patch.object(project_resolver, "derive_slug_from_cwd",
                               return_value="brand-new-repo"):
            self.assertEqual(project_resolver.resolve_project("/repo/brand-new-repo"),
                             "brand-new-repo")

    def test_unscoped_with_no_hit_returns_default(self) -> None:
        with mock.patch.object(project_resolver, "derive_slug_from_cwd",
                               return_value="_unscoped"):
            self.assertEqual(project_resolver.resolve_project("/nowhere"), "_unscoped")


if __name__ == "__main__":
    unittest.main()
