#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for detect_plugin_distribution.py — the SHAPE must decide the POLICY."""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detect_plugin_distribution as D


def mkrepo(tmp, *, root=".", claude=None, codex=None, mkt=None, pkg=None):
    base = os.path.join(tmp, root)
    os.makedirs(os.path.join(base, ".claude-plugin"), exist_ok=True)
    json.dump(claude or {"name": "demo", "description": "d"},
              open(os.path.join(base, ".claude-plugin", "plugin.json"), "w"))
    if codex is not None:
        os.makedirs(os.path.join(base, ".codex-plugin"), exist_ok=True)
        json.dump(codex, open(os.path.join(base, ".codex-plugin", "plugin.json"), "w"))
    if mkt is not None:
        json.dump(mkt, open(os.path.join(base, ".claude-plugin", "marketplace.json"), "w"))
    if pkg is not None:
        json.dump(pkg, open(os.path.join(base, "package.json"), "w"))
    return tmp


def hubfile(tmp, source):
    p = os.path.join(tmp, "hub.json")
    json.dump({"plugins": [{"name": "demo", "source": source}]}, open(p, "w"))
    return p


class ShapeDecidesPolicy(unittest.TestCase):
    def test_git_source_recommends_auto_sha(self):
        with tempfile.TemporaryDirectory() as t:
            mkrepo(t)
            r = D.detect(t, hubfile(t, {"source": "github", "repo": "x/y"}))
            self.assertEqual(r["shape"], "git-sourced-plugin")
            self.assertEqual(r["recommended_version_policy"], "auto-sha")

    def test_app_companion_follows_the_app(self):
        with tempfile.TemporaryDirectory() as t:
            mkrepo(t, root="plugin", pkg={"name": "app", "private": True, "version": "1.0.0"})
            r = D.detect(t, hubfile(t, {"source": "github", "repo": "x/y"}))
            self.assertEqual(r["shape"], "app-companion")
            self.assertEqual(r["recommended_version_policy"], "follow-app")

    def test_auto_sha_repo_with_pinned_version_is_told_to_omit_it(self):
        with tempfile.TemporaryDirectory() as t:
            mkrepo(t, claude={"name": "demo", "description": "d", "version": "1.2.3"})
            r = D.detect(t, hubfile(t, {"source": "github", "repo": "x/y"}))
            self.assertTrue(any("omit `version`" in a for a in r["actions"]))


class HardRuleConsistency(unittest.TestCase):
    """Policy is DETECTED; consistency is a HARD RULE regardless of policy."""

    def test_masking_across_hosts_is_flagged(self):
        with tempfile.TemporaryDirectory() as t:
            mkrepo(t,
                   claude={"name": "demo", "description": "d"},              # no version
                   codex={"name": "demo", "description": "d", "version": "9.9.9"})  # masks it
            r = D.detect(t, hubfile(t, {"source": "github", "repo": "x/y"}))
            self.assertFalse(r["consistent"])
            self.assertTrue(any("HARD RULE" in a for a in r["actions"]))

    def test_consistent_omission_passes(self):
        with tempfile.TemporaryDirectory() as t:
            mkrepo(t, codex={"name": "demo", "description": "d"})
            r = D.detect(t, hubfile(t, {"source": "github", "repo": "x/y"}))
            self.assertTrue(r["consistent"])


class NotAPlugin(unittest.TestCase):
    def test_plain_repo_is_not_classified(self):
        with tempfile.TemporaryDirectory() as t:
            r = D.detect(t)
            self.assertEqual(r["shape"], "not-a-plugin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
