#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests — Phase 1 prior-art digest lands in the bootstrap packet.

The unit tests in ``test_prior_art.py`` cover the engine. THIS file proves
the end-to-end Phase 1 contract:

* ``context_bootstrap.build_packet`` writes a ``prior_art`` block under
  the packet root (the contract Phase 1 Assess reads).
* The seeded multi-project memory root produces the target-scenario
  digest (cross-project impls + decisions) for "build semantic search".
* The ``agent_brief`` carries a compact "Prior Art" pointer line and is
  NOT flooded with the full digest body.
* Disabled via ``BUILD_LOOP_PRIOR_ART=0`` → empty payload, no error.
* Empty memory root → never raises, never blocks Phase 1.

Runnable via ``python3 scripts/test_context_bootstrap_prior_art.py``.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import context_bootstrap  # noqa: E402
from test_prior_art import _seed_target_scenario  # reuse the seeder  # noqa: E402


class BootstrapPriorArtIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls._mem = tempfile.TemporaryDirectory(prefix="bl_bootstrap_mem_")
        cls.memory_root = Path(cls._mem.name)
        _seed_target_scenario(cls.memory_root)

        cls._work = tempfile.TemporaryDirectory(prefix="bl_bootstrap_work_")
        cls.workdir = Path(cls._work.name)
        # Give the workdir a recognizable project name so resolve_project
        # returns something stable; a `.git/HEAD` is enough on most paths,
        # but resolve_project falls back to the directory name when no
        # repo info is found.
        (cls.workdir / ".build-loop").mkdir(parents=True, exist_ok=True)

        os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(cls.memory_root)
        os.environ["AGENT_MEMORY_ROOT"] = str(cls.memory_root)
        os.environ["BUILD_LOOP_MEMORY_ROOT"] = str(cls.memory_root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._mem.cleanup()
        cls._work.cleanup()
        for var in ("BUILD_LOOP_MEMORY_STORE_ROOT", "AGENT_MEMORY_ROOT",
                    "BUILD_LOOP_MEMORY_ROOT", "BUILD_LOOP_PRIOR_ART"):
            os.environ.pop(var, None)

    def test_prior_art_block_in_packet(self) -> None:
        packet = context_bootstrap.build_packet(
            workdir=self.workdir,
            query="build semantic search across the docs site",
            limit=4,
        )
        self.assertIn("prior_art", packet)
        prior = packet["prior_art"]
        self.assertTrue(prior.get("enabled"))
        # The headline scenario: ≥2 of the seeded projects must surface.
        projects = set(prior["stats"]["projects"])
        self.assertGreaterEqual(
            len(projects & {"sample-news", "sample-rag", "sample-assistant"}), 2,
            f"expected cross-project projects, got {projects}",
        )
        # Decisions ("why") and implementations both present.
        self.assertGreater(len(prior["decisions"]), 0)
        self.assertGreater(len(prior["implementations"]), 0)
        # Capability was classified.
        self.assertIn("semantic-search", prior["capabilities"])

    def test_brief_carries_compact_pointer_not_full_digest(self) -> None:
        packet = context_bootstrap.build_packet(
            workdir=self.workdir,
            query="build semantic search across the docs site",
            limit=4,
        )
        brief = packet["agent_brief"]
        # Brief carries the compact pointer line.
        self.assertIn("Prior Art (cross-project)", brief)
        # Brief stays small; the full digest_text is referenced, not inlined.
        # Use an upper bound — the full digest can run >1k chars.
        self.assertLess(len(brief), 4000)
        # Brief should not contain the entire digest_text body.
        self.assertNotIn("## Prior Art Across Projects", brief)

    def test_disabled_via_env_returns_empty_payload(self) -> None:
        os.environ["BUILD_LOOP_PRIOR_ART"] = "0"
        try:
            packet = context_bootstrap.build_packet(
                workdir=self.workdir,
                query="build semantic search across the docs site",
                limit=4,
            )
        finally:
            os.environ.pop("BUILD_LOOP_PRIOR_ART", None)
        prior = packet["prior_art"]
        self.assertFalse(prior.get("enabled"))
        self.assertEqual([], prior["implementations"])
        self.assertEqual([], prior["decisions"])
        self.assertIn("prior_art_disabled_by_env", prior["reasons"])

    def test_unclassifiable_query_does_not_raise(self) -> None:
        packet = context_bootstrap.build_packet(
            workdir=self.workdir,
            query="buy groceries tomorrow morning",
            limit=4,
        )
        prior = packet["prior_art"]
        # No capability classified → empty payload, never an error.
        self.assertEqual([], prior["capabilities"])
        self.assertEqual([], prior["implementations"])


class EmptyMemoryNeverBlocks(unittest.TestCase):
    """Cold install: empty memory root must NOT break Phase 1."""

    def test_empty_memory_root_packet_succeeds(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bl_empty_mem_") as mem, \
             tempfile.TemporaryDirectory(prefix="bl_empty_work_") as work:
            os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = mem
            os.environ["AGENT_MEMORY_ROOT"] = mem
            os.environ["BUILD_LOOP_MEMORY_ROOT"] = mem
            try:
                packet = context_bootstrap.build_packet(
                    workdir=Path(work),
                    query="build semantic search",
                    limit=4,
                )
            finally:
                for v in ("BUILD_LOOP_MEMORY_STORE_ROOT", "AGENT_MEMORY_ROOT",
                          "BUILD_LOOP_MEMORY_ROOT"):
                    os.environ.pop(v, None)
        self.assertIn("prior_art", packet)
        prior = packet["prior_art"]
        # Empty memory → empty digest; reasons explains why; never raises.
        self.assertEqual([], prior["implementations"])
        self.assertEqual([], prior["decisions"])
        self.assertTrue(
            any(r.startswith("missing_projects_root") or r == "no_prior_art_found"
                for r in prior["reasons"]),
            prior["reasons"],
        )
        # The packet otherwise has its normal shape so Phase 1 keeps going.
        self.assertIn("sources", packet)
        self.assertIn("agent_brief", packet)


if __name__ == "__main__":
    unittest.main(verbosity=2)
