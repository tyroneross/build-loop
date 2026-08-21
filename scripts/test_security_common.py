#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for security_common.py — primitives shared by the security scanner.

In a security gate the dangerous direction is a false CLEARANCE, so these grade
that side first: `suppressed` silencing a finding that carries no suppression,
`is_inert_file` declaring a live route unreachable, `is_api_path` refusing to scan
a real handler. A false positive costs a reviewer minutes; a false clearance ships
the vulnerability.

Two behaviours are pinned here because they are NOT what a reader would assume:
  - `suppressed` requires the colon. A bare `# nosec` does NOT suppress.
  - `is_inert_file` matches its markers as SUBSTRINGS of the whole path, so a
    directory merely containing `_archive` makes everything under it inert.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import security_common as sc  # noqa: E402


class TestSuppressed(unittest.TestCase):
    def test_hash_nosec_with_reason_suppresses(self):
        self.assertTrue(sc.suppressed("x = eval(y)  # nosec: reviewed 2026-01-01"))

    def test_slash_nosec_with_reason_suppresses(self):
        self.assertTrue(sc.suppressed("eval(y);  // nosec: reviewed"))

    def test_case_insensitive(self):
        self.assertTrue(sc.suppressed("x = 1  # NoSec: why"))

    def test_bare_nosec_without_colon_does_NOT_suppress(self):
        """Pinned deliberately. The module docstring says a bare `nosec` still
        matches; the regex requires the colon, so it does not. Behaviour is the
        contract — a reader who trusts the prose would think a real finding was
        silenced when it was not."""
        self.assertFalse(sc.suppressed("x = eval(y)  # nosec"))

    def test_unrelated_line_is_not_suppressed(self):
        """The dangerous direction: silencing a finding that never asked to be."""
        self.assertFalse(sc.suppressed("x = eval(user_input)"))

    def test_the_word_nosec_in_prose_does_not_suppress(self):
        self.assertFalse(sc.suppressed('msg = "we should nosec this someday"'))


class TestStripStringLiterals(unittest.TestCase):
    def test_double_quoted_body_is_blanked(self):
        self.assertEqual(sc.strip_string_literals('x = "eval(y)"'), 'x = ""')

    def test_single_quoted_body_is_blanked(self):
        self.assertEqual(sc.strip_string_literals("x = 'eval(y)'"), "x = ''")

    def test_code_outside_quotes_survives(self):
        """Over-stripping would hide real code from every pattern that follows."""
        self.assertIn("eval(", sc.strip_string_literals('eval(x) + "text"'))

    def test_escaped_quote_does_not_end_the_literal_early(self):
        out = sc.strip_string_literals(r'a = "he said \"hi\"" + eval(z)')
        self.assertIn("eval(z)", out, "code after an escaped quote was stripped away")

    def test_unterminated_quote_leaves_the_line_usable(self):
        self.assertIn("eval", sc.strip_string_literals('x = "unterminated + eval(y)'))


class TestIsInertFile(unittest.TestCase):
    def test_live_route_is_not_inert(self):
        """The dangerous direction: a live file declared unreachable is never scanned."""
        self.assertFalse(sc.is_inert_file(Path("/repo/api/route.ts")))

    def test_backup_suffixes_are_inert(self):
        for suffix in (".bak", ".old", ".orig", ".disabled", ".backup", ".save"):
            with self.subTest(suffix=suffix):
                self.assertTrue(sc.is_inert_file(Path(f"/repo/api/route.ts{suffix}")))

    def test_sample_and_template_suffixes_are_inert(self):
        for suffix in (".example", ".sample", ".template", ".tmp"):
            with self.subTest(suffix=suffix):
                self.assertTrue(sc.is_inert_file(Path(f"/repo/config{suffix}")))

    def test_timestamped_backup_marker_is_inert(self):
        self.assertTrue(sc.is_inert_file(Path("/repo/api/route.ts.phase1-backup-20251010-232611")))

    def test_node_modules_is_inert(self):
        self.assertTrue(sc.is_inert_file(Path("/repo/node_modules/pkg/api/route.ts")))

    def test_a_filename_merely_containing_example_is_not_inert(self):
        """`example.py` must not be mistaken for `config.example`."""
        self.assertFalse(sc.is_inert_file(Path("/repo/api/example.py")))

    def test_markers_match_as_substrings_of_the_whole_path(self):
        """PINNED, not endorsed. A directory whose name merely CONTAINS `_archive`
        makes every file under it inert, so a live API route there is silently never
        scanned. Filed as a false-clearance risk; this test documents the behaviour
        so a later narrowing is a deliberate change, not an accident."""
        self.assertTrue(sc.is_inert_file(Path("/repo/my_archive_service/api/route.ts")))


class TestIsApiPath(unittest.TestCase):
    def test_conventional_route_directories_are_api(self):
        for part in ("api", "functions", "routes", "handlers", "endpoints",
                     "controllers", "server", "trpc"):
            with self.subTest(part=part):
                self.assertTrue(sc.is_api_path(Path(f"/repo/src/{part}/thing.ts")))

    def test_matching_is_case_insensitive_on_path_parts(self):
        self.assertTrue(sc.is_api_path(Path("/repo/src/API/thing.ts")))

    def test_non_route_path_is_not_api(self):
        self.assertFalse(sc.is_api_path(Path("/repo/src/components/Button.tsx")))

    def test_inert_file_is_never_api_even_on_a_route_path(self):
        """Inertness wins, so a backup under api/ does not get scanned as a route."""
        self.assertFalse(sc.is_api_path(Path("/repo/api/route.ts.bak")))

    def test_substring_alone_does_not_make_a_path_api(self):
        """`apiary` is not `api` — matching is on whole path PARTS."""
        self.assertFalse(sc.is_api_path(Path("/repo/src/apiary/thing.ts")))


class TestFirstMatchLine(unittest.TestCase):
    def test_returns_one_indexed_line_and_stripped_text(self):
        lines = ["import os\n", "  x = eval(y)  \n", "z = 1\n"]
        self.assertEqual(sc.first_match_line(lines, re.compile(r"eval")), (2, "x = eval(y)"))

    def test_falls_back_to_line_one_when_nothing_matches(self):
        self.assertEqual(sc.first_match_line(["a\n"], re.compile(r"zzz")), (1, ""))

    def test_empty_input_does_not_raise(self):
        self.assertEqual(sc.first_match_line([], re.compile(r"x")), (1, ""))


class TestFinding(unittest.TestCase):
    def _finding(self, **kw):
        base = dict(severity="HIGH", owasp_ids="A01", file_path=Path("/repo/a.py"),
                    line_no=3, message="m", snippet="  code  ", fix="f", check_id="c1")
        base.update(kw)
        return sc.finding(**base)

    def test_carries_every_field_the_reporters_read(self):
        f = self._finding()
        self.assertEqual(set(f), {"severity", "owasp_ids", "file", "line",
                                  "message", "snippet", "fix", "check_id"})

    def test_file_is_serialised_as_a_string(self):
        """Findings are emitted as JSON; a Path would not serialise."""
        self.assertIsInstance(self._finding()["file"], str)

    def test_snippet_is_right_stripped_but_keeps_indentation(self):
        self.assertEqual(self._finding()["snippet"], "  code")

    def test_every_severity_is_orderable(self):
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            with self.subTest(sev=sev):
                self.assertIn(self._finding(severity=sev)["severity"], sc.SEVERITY_ORDER)

    def test_severity_order_ranks_critical_first(self):
        self.assertLess(sc.SEVERITY_ORDER["CRITICAL"], sc.SEVERITY_ORDER["LOW"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
