#!/usr/bin/env python3
"""Tests for registry_lag.py.

The verdict logic is exercised through the injection points, so none of these
touch git or a network. The one case that matters most is `ahead`: it is the
quiet one, and a check that only ever looked for "behind" would pass forever
while the registry served code that is not on main.
"""

import unittest

import registry_lag


class VerdictTests(unittest.TestCase):
    def verdict(self, github: str, npm: str | None) -> dict:
        return registry_lag.evaluate(
            package_version=github, published_version=npm
        )

    def test_level_is_fresh(self):
        r = self.verdict("1.5.0", "1.5.0")
        self.assertEqual(r["verdict"], "level")
        self.assertFalse(r["stale"])

    def test_registry_behind_is_stale(self):
        # The real navgator failure: tag and release exist, npm never got it.
        r = self.verdict("0.9.1", "0.9.0")
        self.assertEqual(r["verdict"], "behind")
        self.assertTrue(r["stale"])
        self.assertIn("did not land", r["reason"])

    def test_registry_ahead_is_stale_and_named_distinctly(self):
        # Forbidden by the release model: npm may lag GitHub, never lead it.
        r = self.verdict("0.9.0", "0.9.1")
        self.assertEqual(r["verdict"], "ahead")
        self.assertTrue(r["stale"])
        self.assertIn("ahead of the default branch", r["reason"])

    def test_seven_week_gap_is_caught(self):
        # @tyroneross/build-loop, the case that prompted this check.
        r = self.verdict("0.42.5", "0.36.1")
        self.assertEqual(r["verdict"], "behind")
        self.assertTrue(r["stale"])

    def test_unpublished_package_is_not_stale(self):
        r = registry_lag.evaluate(package_version="0.1.0", published_version=None)
        self.assertEqual(r["verdict"], "unpublished")
        self.assertFalse(r["stale"])


class SemverOrderTests(unittest.TestCase):
    def test_double_digit_minor_outranks_single_digit(self):
        # A string compare calls "0.9.0" newer than "0.10.0". This is the bug
        # that would make the check silently stop firing after ten minors.
        self.assertLess(registry_lag._parts("0.9.0"), registry_lag._parts("0.10.0"))

    def test_double_digit_major(self):
        self.assertLess(registry_lag._parts("9.0.0"), registry_lag._parts("10.0.0"))

    def test_prerelease_sorts_below_its_release(self):
        self.assertLess(
            registry_lag._parts("1.2.0-rc.1"), registry_lag._parts("1.2.0")
        )

    def test_prerelease_still_outranks_earlier_release(self):
        self.assertLess(
            registry_lag._parts("1.1.0"), registry_lag._parts("1.2.0-rc.1")
        )

    def test_short_version_is_padded(self):
        self.assertEqual(
            registry_lag._parts("1.2")[0], registry_lag._parts("1.2.0")[0]
        )


class PrivatePackageTests(unittest.TestCase):
    def test_private_manifest_is_never_stale(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "package.json").write_text(
                json.dumps({"name": "@x/private", "version": "1.0.0", "private": True})
            )
            r = registry_lag.evaluate(workdir=tmp, ref=None)
        self.assertEqual(r["verdict"], "private")
        self.assertFalse(r["stale"])


if __name__ == "__main__":
    unittest.main()
