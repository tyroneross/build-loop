#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for perturbation_spotcheck.py. Zero deps. Run: python3 -m pytest test_perturbation_spotcheck.py"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import perturbation_spotcheck as ps  # noqa: E402

SCRIPT = HERE / "perturbation_spotcheck.py"


class RenameIdentifiersTests(unittest.TestCase):
    def test_rename_is_consistent_and_deterministic(self) -> None:
        text = "foo = bar + foo"
        out = ps.rename_identifiers(text)
        # Same name maps to same replacement everywhere; distinct names differ.
        self.assertEqual(out, "id_0 = id_1 + id_0")
        self.assertEqual(out, ps.rename_identifiers(text))  # deterministic

    def test_reserved_keywords_preserved(self) -> None:
        out = ps.rename_identifiers("def foo(): return None")
        self.assertIn("def", out)
        self.assertIn("return", out)
        self.assertIn("None", out)
        self.assertNotIn("foo", out)  # non-reserved name renamed

    def test_structure_preserved(self) -> None:
        # An identifier-invariant property (token arity) survives the rename.
        text = "alpha beta alpha gamma beta"
        out = ps.rename_identifiers(text)
        self.assertEqual(len(text.split()), len(out.split()))


class ReorderSequenceTests(unittest.TestCase):
    def test_reverse(self) -> None:
        self.assertEqual(ps.reorder_sequence([1, 2, 3]), [3, 2, 1])

    def test_rotate(self) -> None:
        self.assertEqual(ps.reorder_sequence([1, 2, 3], strategy="rotate"), [2, 3, 1])

    def test_short_sequences_unchanged(self) -> None:
        self.assertEqual(ps.reorder_sequence([]), [])
        self.assertEqual(ps.reorder_sequence([1]), [1])


class SpotcheckTests(unittest.TestCase):
    def test_invariant_check_does_not_flip(self) -> None:
        # A legitimate outcome check: "does the text have exactly 3 identifier tokens?"
        # — invariant under identifier renaming.
        def check(t: str) -> bool:
            return len(ps._IDENT_RE.findall(t)) == 3

        results = ps.spotcheck_all(check, "aaa bbb ccc")
        self.assertTrue(all(not r.flipped for r in results))
        self.assertTrue(results[0].original_pass)

    def test_gamed_check_flips_under_rename(self) -> None:
        # A GAMED check that pattern-matches a magic identifier name rather than
        # testing behavior — it passes originally but flips when the name changes.
        def gamed_check(t: str) -> bool:
            return "SECRET_TOKEN" in t

        res = ps.spotcheck(gamed_check, "value = SECRET_TOKEN",
                           perturb=ps.rename_identifiers, label="rename_identifiers")
        self.assertTrue(res.original_pass)
        self.assertFalse(res.perturbed_pass)
        self.assertTrue(res.flipped)

    def test_reorder_perturbation_flips_order_dependent_check(self) -> None:
        # Check claims order-independence but actually depends on first element.
        def first_is_a(items: list[str]) -> bool:
            return bool(items) and items[0] == "a"

        res = ps.spotcheck(first_is_a, ["a", "b", "c"],
                           perturb=ps.reorder_sequence, label="reorder")
        self.assertTrue(res.original_pass)
        self.assertFalse(res.perturbed_pass)
        self.assertTrue(res.flipped)


def _run_cli(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True, text=True, input=stdin,
    )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cli_no_flip_exits_0(self) -> None:
        inp = self.d / "in.txt"
        inp.write_text("alpha beta gamma")
        # Invariant check: file has exactly 3 whitespace tokens (rename-invariant).
        cmd = f"{sys.executable} -c \"import sys;t=open(sys.argv[1]).read();sys.exit(0 if len(t.split())==3 else 1)\" {{input}}"
        r = _run_cli(["--check-cmd", cmd, "--input", str(inp), "--mode", "rename"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("no flip", r.stderr)

    def test_cli_flip_warns_exit_0_default(self) -> None:
        inp = self.d / "in.txt"
        inp.write_text("value = MAGIC")
        # Gamed check: greps for the literal MAGIC identifier — flips under rename.
        cmd = f"{sys.executable} -c \"import sys;t=open(sys.argv[1]).read();sys.exit(0 if 'MAGIC' in t else 1)\" {{input}}"
        r = _run_cli(["--check-cmd", cmd, "--input", str(inp), "--mode", "rename"])
        self.assertEqual(r.returncode, 0, msg="WARN-only: default must not block")
        self.assertIn("WARN", r.stderr)
        self.assertIn("flipped", r.stderr)

    def test_cli_flip_strict_exits_1(self) -> None:
        inp = self.d / "in.txt"
        inp.write_text("value = MAGIC")
        cmd = f"{sys.executable} -c \"import sys;t=open(sys.argv[1]).read();sys.exit(0 if 'MAGIC' in t else 1)\" {{input}}"
        r = _run_cli(["--check-cmd", cmd, "--input", str(inp), "--mode", "rename", "--strict"])
        self.assertEqual(r.returncode, 1, msg=r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
