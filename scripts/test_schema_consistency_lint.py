#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for schema_consistency_lint.

The lint's whole value is precision — a schema check that cries wolf gets
muted, and a muted check is worse than none. So these tests pin BOTH
directions: the known-divergent shapes must fire, and the two things that
mimic frontmatter (a function signature, a docstring Args block) must not.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schema_consistency_lint as lint  # noqa: E402


def _repo(tmp: Path, *, template: str | None, field_order: str, assess: str | None = None) -> Path:
    (tmp / "templates").mkdir(parents=True, exist_ok=True)
    (tmp / "scripts").mkdir(parents=True, exist_ok=True)
    if template is not None:
        (tmp / "templates" / "backlog-item.md").write_text(template)
    (tmp / "scripts" / "backlog.py").write_text(field_order)
    if assess is not None:
        (tmp / "scripts" / "backlog").mkdir(exist_ok=True)
        (tmp / "scripts" / "backlog" / "__init__.py").write_text("")
        (tmp / "scripts" / "backlog" / "assess.py").write_text(assess)
    return tmp


ALIGNED_TEMPLATE = "---\nid: x\ntitle: y\nstatus: open\n---\n\nbody\n"
ALIGNED_WRITER = 'FIELD_ORDER = ("id", "title", "status")\n'


class DivergenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_aligned_schemas_produce_no_finding(self) -> None:
        """A check that fires on a correct repo is noise, not a check."""
        _repo(self.root, template=ALIGNED_TEMPLATE, field_order=ALIGNED_WRITER)
        kinds = [f["kind"] for f in lint.scan(self.root)]
        self.assertNotIn("schema_template_writer_divergence", kinds)

    def test_template_only_field_is_reported(self) -> None:
        """The observed defect: template documents a field no writer emits."""
        _repo(
            self.root,
            template="---\nid: x\ntitle: y\nstatus: open\nclassify: SAFE\n---\n\nbody\n",
            field_order=ALIGNED_WRITER,
        )
        hits = [f for f in lint.scan(self.root) if f["kind"] == "schema_template_writer_divergence"]
        self.assertEqual(len(hits), 1)
        self.assertIn("classify", hits[0]["evidence"])
        self.assertEqual(hits[0]["severity"], "HIGH")

    def test_writer_only_field_is_reported(self) -> None:
        """Drift in the other direction is the same defect."""
        _repo(
            self.root,
            template=ALIGNED_TEMPLATE,
            field_order='FIELD_ORDER = ("id", "title", "status", "gated")\n',
        )
        hits = [f for f in lint.scan(self.root) if f["kind"] == "schema_template_writer_divergence"]
        self.assertEqual(len(hits), 1)
        self.assertIn("gated", hits[0]["evidence"])


class SecondWriterPrecisionTests(unittest.TestCase):
    """A function signature and a docstring both look like frontmatter."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _scan_assess(self, assess_src: str) -> list[str]:
        _repo(self.root, template=ALIGNED_TEMPLATE, field_order=ALIGNED_WRITER, assess=assess_src)
        for f in lint.scan(self.root):
            if f["kind"] == "schema_second_writer":
                return f["evidence"]
        return []

    def test_emitted_key_is_caught(self) -> None:
        src = (
            'def build_item(repo):\n'
            '    return "\\n".join([f"repo: {repo}", f"effort: M"])\n'
        )
        ev = self._scan_assess(src)
        self.assertIn("effort", ev)
        self.assertIn("repo", ev)

    def test_signature_annotation_is_not_a_key(self) -> None:
        """`deferral: dict[str, Any],` in the signature is not emitted output."""
        src = (
            'def build_item(deferral: dict, repo: str = "x"):\n'
            '    return f"repo: {repo}"\n'
        )
        ev = self._scan_assess(src)
        self.assertNotIn("deferral", ev)

    def test_docstring_args_block_is_not_a_key(self) -> None:
        """A docstring's `name: description` lines mimic frontmatter exactly."""
        src = (
            'def build_item(repo):\n'
            '    """Render.\n\n'
            '    Args:\n'
            '        notakey: some description\n'
            '    """\n'
            '    return f"repo: {repo}"\n'
        )
        ev = self._scan_assess(src)
        self.assertNotIn("notakey", ev)


class ShadowingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_module_shadowed_by_package_is_reported(self) -> None:
        _repo(self.root, template=ALIGNED_TEMPLATE, field_order=ALIGNED_WRITER, assess="x = 1\n")
        hits = [f for f in lint.scan(self.root) if f["kind"] == "module_shadowed_by_package"]
        self.assertEqual(len(hits), 1)
        self.assertIn("backlog", hits[0]["signal"])

    def test_no_shadowing_when_names_differ(self) -> None:
        _repo(self.root, template=ALIGNED_TEMPLATE, field_order=ALIGNED_WRITER)
        (self.root / "scripts" / "other_pkg").mkdir()
        (self.root / "scripts" / "other_pkg" / "__init__.py").write_text("")
        hits = [f for f in lint.scan(self.root) if f["kind"] == "module_shadowed_by_package"]
        self.assertEqual(hits, [])


class ExitCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_warn_mode_exits_zero_even_with_high_findings(self) -> None:
        """WARN-only until precision is measured — it must not block a commit."""
        _repo(
            self.root,
            template="---\nid: x\nclassify: SAFE\n---\n\nbody\n",
            field_order=ALIGNED_WRITER,
        )
        rc = lint.main(["--workdir", str(self.root)])
        self.assertEqual(rc, 0)

    def test_strict_mode_exits_one_on_high(self) -> None:
        _repo(
            self.root,
            template="---\nid: x\nclassify: SAFE\n---\n\nbody\n",
            field_order=ALIGNED_WRITER,
        )
        rc = lint.main(["--workdir", str(self.root), "--strict"])
        self.assertEqual(rc, 1)

    def test_strict_mode_exits_zero_when_clean(self) -> None:
        _repo(self.root, template=ALIGNED_TEMPLATE, field_order=ALIGNED_WRITER)
        rc = lint.main(["--workdir", str(self.root), "--strict"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
