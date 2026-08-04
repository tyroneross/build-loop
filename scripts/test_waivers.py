# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""test_waivers.py — stdlib unittest suite for scripts/waivers.py."""
from __future__ import annotations

import ast
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_THIS = Path(__file__).resolve()
_WAIVERS_PY = _THIS.parent / "waivers.py"

_STDLIB = {
    "__future__", "argparse", "ast", "contextlib", "datetime", "hashlib", "io",
    "importlib", "json", "pathlib", "re", "secrets", "sys", "tempfile",
    "typing", "unittest",
}


def _load():
    spec = importlib.util.spec_from_file_location("_waivers_under_test", _WAIVERS_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wv = _load()


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "someproj"
        (self.repo / "src").mkdir(parents=True)
        self.covered = self.repo / "src" / "stub.ts"
        self.covered.write_text("const re = /\\-/;\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_waiver(self, **kwargs):
        defaults = dict(
            repo=self.repo,
            rule="no-useless-escape",
            rel_path="src/stub.ts",
            rationale="upstream-generated file; rewrite pending",
            authority="user",
            today="2026-08-04",
        )
        defaults.update(kwargs)
        return wv.new(**defaults)


class TestPurity(unittest.TestCase):
    def test_no_third_party_imports(self):
        tree = ast.parse(_WAIVERS_PY.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        self.assertTrue(roots.issubset(_STDLIB), f"non-stdlib imports: {roots - _STDLIB}")


class TestFrontmatter(_Base):
    def test_round_trip_preserves_every_field(self):
        fields = {key: f"v-{key}" for key in wv.FIELD_ORDER}
        fields["schema_version"] = 1
        parsed = wv.parse_waiver(wv.render_waiver(fields, "body text"))
        for key in wv.FIELD_ORDER:
            self.assertEqual(parsed[key], fields[key], key)

    def test_iso_date_is_quoted_so_it_reads_back_as_text(self):
        rendered = wv.render_waiver({"date": "2026-08-04", "expires": "2026-12-01"})
        self.assertIn('date: "2026-08-04"', rendered)
        self.assertEqual(wv.parse_waiver(rendered)["expires"], "2026-12-01")

    def test_missing_keys_take_tolerant_defaults(self):
        parsed = wv.parse_waiver("---\nrule: lint\n---\n")
        self.assertEqual(parsed["expires"], wv.DEFAULT_EXPIRES)
        self.assertEqual(parsed["status"], wv.STATUS_ACTIVE)
        self.assertEqual(parsed["schema_version"], wv.SCHEMA_VERSION)

    def test_rationale_with_colon_survives(self):
        rendered = wv.render_waiver({"rationale": "reason: vendored file"})
        self.assertEqual(wv.parse_waiver(rendered)["rationale"], "reason: vendored file")


class TestNew(_Base):
    def test_writes_record_with_hash_and_default_expiry(self):
        result = self.write_waiver()
        path = Path(result["written"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, wv.waiver_dir(self.repo))
        fields = result["fields"]
        self.assertEqual(fields["expires"], wv.DEFAULT_EXPIRES)
        self.assertEqual(fields["file_sha256"], wv.file_sha256(self.repo, "src/stub.ts"))
        self.assertEqual(fields["date"], "2026-08-04")
        self.assertTrue(any("defaulted to until-file-changes" in w
                            for w in result["warnings"]))

    def test_explicit_expiry_emits_no_default_warning(self):
        result = self.write_waiver(expires="2026-12-01")
        self.assertEqual(result["fields"]["expires"], "2026-12-01")
        self.assertEqual(result["warnings"], [])

    def test_missing_covered_file_warns_and_leaves_hash_empty(self):
        result = self.write_waiver(rel_path="src/does-not-exist.ts")
        self.assertEqual(result["fields"]["file_sha256"], "")
        self.assertTrue(any("not readable" in w for w in result["warnings"]))

    def test_ids_are_unique_and_slugged_from_the_rule(self):
        first = self.write_waiver()["id"]
        second = self.write_waiver()["id"]
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("WV-no-useless-escape-"))


class TestCheck(_Base):
    def test_no_register_means_not_waived(self):
        result = wv.check(self.repo, "no-useless-escape", "src/stub.ts")
        self.assertFalse(result["waived"])
        self.assertIn("no waiver record", result["reason"])

    def test_matching_identity_is_waived(self):
        self.write_waiver()
        result = wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                          today="2026-08-04")
        self.assertTrue(result["waived"])
        self.assertEqual(result["matches"][0]["authority"], "user")

    def test_rule_match_is_case_insensitive(self):
        self.write_waiver()
        self.assertTrue(wv.check(self.repo, "NO-USELESS-ESCAPE", "src/stub.ts",
                                 today="2026-08-04")["waived"])

    def test_different_rule_or_path_is_not_waived(self):
        self.write_waiver()
        self.assertFalse(wv.check(self.repo, "other-rule", "src/stub.ts")["waived"])
        self.assertFalse(wv.check(self.repo, "no-useless-escape", "src/other.ts")["waived"])

    def test_anchored_waiver_covers_only_its_anchor(self):
        self.write_waiver(anchor="line:369")
        self.assertTrue(wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                                 anchor="line:369", today="2026-08-04")["waived"])
        self.assertFalse(wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                                  anchor="line:12", today="2026-08-04")["waived"])

    def test_unanchored_waiver_covers_any_anchor_in_the_file(self):
        self.write_waiver()
        self.assertTrue(wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                                 anchor="line:999", today="2026-08-04")["waived"])

    def test_retired_waiver_is_ignored(self):
        path = Path(self.write_waiver()["written"])
        path.write_text(path.read_text(encoding="utf-8").replace("status: active",
                                                                 "status: retired"),
                        encoding="utf-8")
        self.assertFalse(wv.check(self.repo, "no-useless-escape", "src/stub.ts")["waived"])

    def test_until_file_changes_expires_when_the_file_changes(self):
        self.write_waiver()
        self.covered.write_text("const re = /-/;\n", encoding="utf-8")
        result = wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                          today="2026-08-04")
        self.assertFalse(result["waived"])
        self.assertEqual(len(result["expired"]), 1)
        self.assertIn("covered file changed", result["expired"][0]["reason"])

    def test_until_file_changes_expires_when_the_file_disappears(self):
        self.write_waiver()
        self.covered.unlink()
        result = wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                          today="2026-08-04")
        self.assertFalse(result["waived"])
        self.assertIn("no longer readable", result["expired"][0]["reason"])

    def test_date_expiry_is_evaluated_against_today(self):
        self.write_waiver(expires="2026-09-01")
        self.assertTrue(wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                                 today="2026-08-04")["waived"])
        self.assertFalse(wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                                  today="2026-09-02")["waived"])

    def test_free_text_expiry_stays_active_but_flags_manual(self):
        self.write_waiver(expires="until upstream ships v3")
        result = wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                          today="2026-08-04")
        self.assertTrue(result["waived"])
        self.assertTrue(result["matches"][0]["manual_expiry"])

    def test_until_file_changes_without_a_hash_flags_manual(self):
        self.write_waiver(rel_path="src/does-not-exist.ts")
        result = wv.check(self.repo, "no-useless-escape", "src/does-not-exist.ts",
                          today="2026-08-04")
        self.assertTrue(result["waived"])
        self.assertTrue(result["matches"][0]["manual_expiry"])

    def test_unreadable_record_is_skipped_not_fatal(self):
        self.write_waiver()
        (wv.waiver_dir(self.repo) / "garbage.md").write_text("not frontmatter\n",
                                                            encoding="utf-8")
        self.assertTrue(wv.check(self.repo, "no-useless-escape", "src/stub.ts",
                                 today="2026-08-04")["waived"])


class TestCli(_Base):
    def _run(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = wv.main(argv)
        return code, buffer.getvalue()

    def test_check_exit_1_when_not_waived_and_0_when_waived(self):
        code, _ = self._run(["check", "--repo", str(self.repo),
                             "--rule", "no-useless-escape", "--path", "src/stub.ts"])
        self.assertEqual(code, 1)
        self._run(["new", "--repo", str(self.repo), "--rule", "no-useless-escape",
                   "--path", "src/stub.ts", "--rationale", "vendored",
                   "--authority", "user", "--expires", "2099-01-01"])
        code, out = self._run(["check", "--repo", str(self.repo),
                               "--rule", "no-useless-escape", "--path", "src/stub.ts",
                               "--json"])
        self.assertEqual(code, 0)
        self.assertIn('"waived": true', out)

    def test_new_prints_the_written_path(self):
        code, out = self._run(["new", "--repo", str(self.repo), "--rule", "lint",
                               "--path", "src/stub.ts", "--rationale", "r",
                               "--authority", "user"])
        self.assertEqual(code, 0)
        self.assertIn("wrote ", out)
        self.assertIn("defaulted to until-file-changes", out)

    def test_unknown_subcommand_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            wv.main(["bogus"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
