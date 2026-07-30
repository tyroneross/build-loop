#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the deterministic `user-invocable` SKILL.md stamper.

The load-bearing assertion is `RealRepoFileTests`: all 50 in-tree build-loop
skills already carry `user-invocable: false`, so a correct stamper changes
ZERO bytes when run over copies of them. Anything else is a regression.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import stamp_skill_frontmatter as stamper  # noqa: E402
import test_agent_surface_policy as policy  # noqa: E402


class TempFileCase(unittest.TestCase):
    """Base case with a scratch dir and byte-level helpers."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, content: str, name: str = "SKILL.md") -> Path:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        return path

    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        """Invoke ``main(argv)`` with stdout captured; returns (exit_code, stdout)."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = stamper.main(argv)
        return code, buffer.getvalue()


# ---------------------------------------------------------------------------
# Contract: the four decision branches
# ---------------------------------------------------------------------------

class FieldAbsentTests(TempFileCase):
    SOURCE = "---\nname: demo\ndescription: A demo skill.\n---\n\n# Demo\n\nBody.\n"

    def test_apply_inserts_the_field_as_the_last_frontmatter_line(self) -> None:
        path = self.write(self.SOURCE)
        result = stamper.stamp_file(path, apply=True)
        self.assertEqual(result.status, stamper.STATUS_STAMPED)
        self.assertTrue(result.changed)
        self.assertEqual(
            path.read_bytes().decode("utf-8"),
            "---\nname: demo\ndescription: A demo skill.\nuser-invocable: false\n---\n\n# Demo\n\nBody.\n",
        )

    def test_check_reports_would_stamp_and_writes_nothing(self) -> None:
        path = self.write(self.SOURCE)
        before = path.read_bytes()
        result = stamper.stamp_file(path, apply=False)
        self.assertEqual(result.status, stamper.STATUS_WOULD_STAMP)
        self.assertFalse(result.changed)
        self.assertEqual(path.read_bytes(), before)

    def test_stamped_file_reads_as_false_through_the_policy_test_parser(self) -> None:
        path = self.write(self.SOURCE)
        stamper.stamp_file(path, apply=True)
        self.assertEqual(policy.read_user_invocable(path), "false")
        self.assertEqual(policy.read_name(path), "demo")

    def test_field_order_and_comments_are_preserved(self) -> None:
        source = (
            "---\n"
            "# leading comment\n"
            "name: demo\n"
            "zzz: last-alphabetically-but-first-positionally\n"
            "aaa: 1\n"
            "# trailing comment\n"
            "---\nbody\n"
        )
        path = self.write(source)
        stamper.stamp_file(path, apply=True)
        self.assertEqual(
            path.read_bytes().decode("utf-8"),
            source.replace("# trailing comment\n---\n",
                           "# trailing comment\nuser-invocable: false\n---\n"),
        )


class AlreadyFalseTests(TempFileCase):
    def test_apply_is_a_byte_identical_no_op(self) -> None:
        source = "---\nname: demo\nuser-invocable: false\n---\nbody\n"
        path = self.write(source)
        before = path.read_bytes()
        mtime_before = path.stat().st_mtime_ns
        result = stamper.stamp_file(path, apply=True)
        self.assertEqual(result.status, stamper.STATUS_COMPLIANT)
        self.assertFalse(result.changed)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime_before)

    def test_quoted_value_is_accepted(self) -> None:
        path = self.write('---\nname: demo\nuser-invocable: "false"\n---\nbody\n')
        self.assertEqual(stamper.stamp_file(path, apply=True).status,
                         stamper.STATUS_COMPLIANT)

    def test_case_variants_are_compliant_and_never_rewritten(self) -> None:
        """`False` is hidden by the harness, so this script must not call it a
        violation — the case-SENSITIVE reading here used to disagree with
        `surface_policy.py` / `skill_index.py` about the very same file.

        Compliant means untouched: `--apply` normalizes nothing, because the
        value is already a valid opt-out and rewriting it would edit a field
        someone deliberately typed.
        """
        for value in ("False", "FALSE", '"False"', "'FALSE'"):
            with self.subTest(value=value):
                path = self.write(
                    f"---\nname: cased\nuser-invocable: {value}\n---\nbody\n",
                    name=f"cased-{value.strip(chr(34)).strip(chr(39))}-{len(value)}/SKILL.md")
                before = path.read_bytes()
                result = stamper.stamp_file(path, apply=True)
                self.assertEqual(result.status, stamper.STATUS_COMPLIANT)
                self.assertEqual(result.value, "false", "value is reported normalized")
                self.assertEqual(path.read_bytes(), before)

    def test_uppercase_true_follows_the_true_rules(self) -> None:
        justified = self.write(
            "---\nname: t\nuser-invocable: TRUE\npublic-justification: entrypoint\n---\nb\n",
            name="cased-true-just/SKILL.md")
        self.assertEqual(stamper.stamp_file(justified, apply=True).status,
                         stamper.STATUS_APPROVED_EXCEPTION)
        bare = self.write("---\nname: t\nuser-invocable: True\n---\nb\n",
                          name="cased-true-bare/SKILL.md")
        self.assertEqual(stamper.stamp_file(bare, apply=True).status,
                         stamper.STATUS_VIOLATION)


class ApprovedExceptionTests(TempFileCase):
    SOURCE = (
        "---\n"
        "name: wrapper\n"
        "user-invocable: true\n"
        "public-justification: Codex has no commands surface; this wrapper is the entrypoint.\n"
        "---\nbody\n"
    )

    def test_true_with_justification_is_untouched_and_reported_as_exception(self) -> None:
        path = self.write(self.SOURCE)
        before = path.read_bytes()
        result = stamper.stamp_file(path, apply=True)
        self.assertEqual(result.status, stamper.STATUS_APPROVED_EXCEPTION)
        self.assertTrue(result.ok)
        self.assertTrue(result.justified)
        self.assertFalse(result.changed)
        self.assertEqual(path.read_bytes(), before)

    def test_exception_exits_zero(self) -> None:
        path = self.write(self.SOURCE)
        self.assertEqual(self.run_cli(["--apply", str(path)])[0], 0)


class ViolationTests(TempFileCase):
    SOURCE = "---\nname: leaky\nuser-invocable: true\n---\nbody\n"

    def test_true_without_justification_fails_check_without_writing(self) -> None:
        path = self.write(self.SOURCE)
        before = path.read_bytes()
        result = stamper.stamp_file(path, apply=False)
        self.assertEqual(result.status, stamper.STATUS_VIOLATION)
        self.assertEqual(path.read_bytes(), before)

    def test_apply_refuses_to_flip_a_deliberate_public_decision(self) -> None:
        path = self.write(self.SOURCE)
        before = path.read_bytes()
        result = stamper.stamp_file(path, apply=True)
        self.assertEqual(result.status, stamper.STATUS_VIOLATION)
        self.assertFalse(result.changed)
        self.assertEqual(path.read_bytes(), before, "apply must not rewrite a violation")
        self.assertEqual(self.run_cli(["--apply", str(path)])[0], 1)

    def test_check_exits_one(self) -> None:
        path = self.write(self.SOURCE)
        self.assertEqual(self.run_cli(["--check", str(path)])[0], 1)

    def test_unrecognized_value_is_a_violation_not_a_silent_pass(self) -> None:
        # Values the harness coerces (`yes`/`1` truthy, `no`/`0` falsy) and ones
        # it rejects outright (`maybe`) are all reported here: this repo demands
        # the canonical literal so a reader never replays the coercion table.
        # Case variants of `false`/`true` are NOT in this list — see
        # `AlreadyFalseTests.test_case_variants_are_compliant_and_never_rewritten`.
        for value in ("yes", "1", "no", "0", "maybe", "TRUE-ish", ""):
            with self.subTest(value=value):
                path = self.write(f"---\nname: odd\nuser-invocable: {value}\n---\nbody\n",
                                  name=f"odd-{value or 'empty'}/SKILL.md")
                before = path.read_bytes()
                result = stamper.stamp_file(path, apply=True)
                self.assertEqual(result.status, stamper.STATUS_VIOLATION)
                self.assertEqual(path.read_bytes(), before)

    def test_empty_value_is_never_double_stamped(self) -> None:
        # `user-invocable:` with no value is YAML null. The field is PRESENT, so
        # inserting a second one would produce a duplicate key — reported, never
        # stamped. (The harness coerces null to hidden; this repo still demands
        # the literal, so the verdict is `violation`, not `compliant`.)
        path = self.write("---\nname: odd\nuser-invocable:\n---\nbody\n")
        stamper.stamp_file(path, apply=True)
        self.assertEqual(path.read_bytes().decode("utf-8").count("user-invocable"), 1)


# ---------------------------------------------------------------------------
# Byte preservation
# ---------------------------------------------------------------------------

class BytePreservationTests(TempFileCase):
    def test_body_containing_dash_sequences_is_not_corrupted(self) -> None:
        body = (
            "\n# Demo\n\n"
            "---\n\n"
            "A horizontal rule above, and a fenced YAML block below:\n\n"
            "```yaml\n---\nname: not-the-real-frontmatter\nuser-invocable: true\n---\n```\n\n"
            "--- \n"
            "trailing.\n"
        )
        path = self.write("---\nname: demo\n---" + body)
        stamper.stamp_file(path, apply=True)
        after = path.read_bytes().decode("utf-8")
        self.assertEqual(after, "---\nname: demo\nuser-invocable: false\n---" + body)
        self.assertEqual(after.count("not-the-real-frontmatter"), 1)
        # Only the frontmatter gained a field; the body's fake one is untouched.
        self.assertEqual(policy.read_user_invocable(path), "false")

    def test_crlf_is_preserved_end_to_end(self) -> None:
        source = "---\r\nname: demo\r\ndescription: d\r\n---\r\n\r\n# Body\r\n"
        path = self.write(source)
        stamper.stamp_file(path, apply=True)
        after = path.read_bytes()
        self.assertEqual(
            after.decode("utf-8"),
            "---\r\nname: demo\r\ndescription: d\r\nuser-invocable: false\r\n---\r\n\r\n# Body\r\n",
        )
        self.assertNotIn(b"\n\n", after.replace(b"\r\n", b"\r"))
        self.assertEqual(after.count(b"\r\n"), after.count(b"\n"), "no bare LF introduced")
        self.assertEqual(policy.read_user_invocable(path), "false")

    def test_missing_trailing_newline_in_body_is_preserved(self) -> None:
        path = self.write("---\nname: demo\n---\nno trailing newline")
        stamper.stamp_file(path, apply=True)
        self.assertEqual(path.read_bytes().decode("utf-8"),
                         "---\nname: demo\nuser-invocable: false\n---\nno trailing newline")

    def test_empty_body_after_frontmatter_is_preserved(self) -> None:
        path = self.write("---\nname: demo\n---\n")
        stamper.stamp_file(path, apply=True)
        self.assertEqual(path.read_bytes().decode("utf-8"),
                         "---\nname: demo\nuser-invocable: false\n---\n")

    def test_unicode_body_round_trips(self) -> None:
        body = "\nEm dash — arrow → emoji 🚀 and a U+2028 line separator.\n"
        path = self.write("---\nname: demo\n---" + body)
        stamper.stamp_file(path, apply=True)
        self.assertEqual(path.read_bytes().decode("utf-8"),
                         "---\nname: demo\nuser-invocable: false\n---" + body)

    def test_file_mode_is_preserved(self) -> None:
        path = self.write("---\nname: demo\n---\nbody\n")
        path.chmod(0o644)
        stamper.stamp_file(path, apply=True)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_exotic_unicode_line_breaks_do_not_move_the_insertion_point(self) -> None:
        # str.splitlines() breaks on \v, \f, \x1c-\x1e, U+2028 and U+2029. A
        # splitter using those would read a description containing one as the end
        # of the frontmatter and insert the field mid-value.
        for sep in ("\v", "\f", "\x1c", "\u2028", "\u2029"):
            with self.subTest(sep=repr(sep)):
                source = f"---\nname: demo\ndescription: a{sep}---\n---\nbody\n"
                path = self.write(source, name=f"sep-{ord(sep)}/SKILL.md")
                stamper.stamp_file(path, apply=True)
                self.assertEqual(
                    path.read_bytes().decode("utf-8"),
                    f"---\nname: demo\ndescription: a{sep}---\n"
                    "user-invocable: false\n---\nbody\n",
                )

    def test_split_lines_keepends_is_lossless(self) -> None:
        for sample in ("", "a", "a\n", "a\r\nb", "\n\n\n", "x y\n", "\r\n"):
            with self.subTest(sample=sample):
                self.assertEqual("".join(stamper.split_lines_keepends(sample)), sample)


class IdempotencyTests(TempFileCase):
    def test_apply_twice_produces_identical_bytes(self) -> None:
        path = self.write("---\nname: demo\ndescription: d\n---\n\nbody\n")
        first = stamper.stamp_file(path, apply=True)
        snapshot = path.read_bytes()
        second = stamper.stamp_file(path, apply=True)
        third = stamper.stamp_file(path, apply=True)
        self.assertEqual(first.status, stamper.STATUS_STAMPED)
        self.assertEqual(second.status, stamper.STATUS_COMPLIANT)
        self.assertEqual(third.status, stamper.STATUS_COMPLIANT)
        self.assertEqual(path.read_bytes(), snapshot)

    def test_check_passes_after_apply(self) -> None:
        path = self.write("---\nname: demo\n---\nbody\n")
        self.assertEqual(self.run_cli(["--apply", str(path)])[0], 0)
        self.assertEqual(self.run_cli(["--check", str(path)])[0], 0)


# ---------------------------------------------------------------------------
# Malformed input — fail soft, never a partial write
# ---------------------------------------------------------------------------

class MalformedTests(TempFileCase):
    def _assert_malformed_and_unchanged(self, content: str | bytes) -> stamper.StampResult:
        path = self.tmp / "SKILL.md"
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        before = path.read_bytes()
        result = stamper.stamp_file(path, apply=True)
        self.assertEqual(result.status, stamper.STATUS_MALFORMED, result.reason)
        self.assertFalse(result.changed)
        self.assertEqual(path.read_bytes(), before)
        return result

    def test_no_frontmatter_at_all(self) -> None:
        result = self._assert_malformed_and_unchanged("# Just a heading\n\nSome prose.\n")
        self.assertIn("frontmatter", result.reason)

    def test_empty_file(self) -> None:
        self._assert_malformed_and_unchanged("")

    def test_unterminated_frontmatter(self) -> None:
        self._assert_malformed_and_unchanged("---\nname: demo\ndescription: d\n")

    def test_closing_delimiter_without_trailing_newline(self) -> None:
        # policy.FRONTMATTER_RE requires a newline after the closing `---`.
        self._assert_malformed_and_unchanged("---\nname: demo\n---")

    def test_bom_before_frontmatter_is_named(self) -> None:
        result = self._assert_malformed_and_unchanged("﻿---\nname: demo\n---\nbody\n")
        self.assertIn("byte-order mark", result.reason)

    def test_non_utf8_bytes(self) -> None:
        result = self._assert_malformed_and_unchanged(b"---\nname: \xff\xfe\n---\nbody\n")
        self.assertIn("UTF-8", result.reason)

    def test_missing_file(self) -> None:
        result = stamper.stamp_file(self.tmp / "nope" / "SKILL.md", apply=True)
        self.assertEqual(result.status, stamper.STATUS_MALFORMED)

    def test_malformed_exits_one_in_both_modes(self) -> None:
        path = self.write("# no frontmatter\n")
        self.assertEqual(self.run_cli(["--check", str(path)])[0], 1)
        self.assertEqual(self.run_cli(["--apply", str(path)])[0], 1)

    def test_four_dash_line_does_not_close_the_block(self) -> None:
        self._assert_malformed_and_unchanged("---\nname: demo\n----\nbody\n")


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

class CliTests(TempFileCase):
    def test_workdir_walk_finds_nested_skills(self) -> None:
        for name in ("a", "b", "c"):
            self.write("---\nname: %s\n---\nbody\n" % name, name=f"skills/{name}/SKILL.md")
        found = stamper.discover(self.tmp)
        self.assertEqual(len(found), 3)
        self.assertEqual(self.run_cli(["--apply", "--workdir", str(self.tmp)])[0], 0)
        for path in found:
            self.assertEqual(policy.read_user_invocable(path), "false")

    def test_workdir_walk_skips_git_and_node_modules(self) -> None:
        self.write("---\nname: real\n---\nbody\n", name="skills/real/SKILL.md")
        self.write("---\nname: vendored\n---\nbody\n", name="node_modules/x/SKILL.md")
        self.write("---\nname: gitobj\n---\nbody\n", name=".git/x/SKILL.md")
        found = stamper.discover(self.tmp)
        self.assertEqual([p.name for p in found], ["SKILL.md"])
        self.assertIn("skills/real", str(found[0]))

    def test_workdir_walk_does_not_cross_into_a_git_worktree(self) -> None:
        # `.build-loop/worktrees/` and `.claude/worktrees/` are separate checkouts
        # belonging to other agents; --apply must never write into them.
        self.write("---\nname: real\n---\nbody\n", name="skills/real/SKILL.md")
        isolated = self.write("---\nname: peer\n---\nbody\n",
                              name=".build-loop/worktrees/run-1/skills/peer/SKILL.md")
        before = isolated.read_bytes()
        self.assertEqual([str(p) for p in stamper.discover(self.tmp)],
                         [str(self.tmp / "skills" / "real" / "SKILL.md")])
        self.assertEqual(self.run_cli(["--apply", "--workdir", str(self.tmp)])[0], 0)
        self.assertEqual(isolated.read_bytes(), before)

    def test_json_envelope_shape(self) -> None:
        good = self.write("---\nname: a\nuser-invocable: false\n---\nb\n", name="a/SKILL.md")
        bad = self.write("---\nname: b\nuser-invocable: true\n---\nb\n", name="b/SKILL.md")
        code, out = self.run_cli(["--check", "--json", str(good), str(bad)])
        envelope = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["mode"], "check")
        self.assertEqual(envelope["checked"], 2)
        self.assertEqual(envelope["counts"],
                         {stamper.STATUS_COMPLIANT: 1, stamper.STATUS_VIOLATION: 1})
        self.assertEqual([r["path"] for r in envelope["results"]], [str(good), str(bad)])

    def test_duplicate_paths_are_evaluated_once(self) -> None:
        path = self.write("---\nname: a\nuser-invocable: false\n---\nb\n")
        _, out = self.run_cli(["--check", "--json", str(path), str(path)])
        self.assertEqual(json.loads(out)["checked"], 1)

    def test_no_paths_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()):
            stamper.main(["--check"])
        self.assertEqual(ctx.exception.code, 2)


# ---------------------------------------------------------------------------
# Regression: real in-tree SKILL.md files must be byte-identical no-ops
# ---------------------------------------------------------------------------

class RealRepoFileTests(TempFileCase):
    """All 50 build-loop skills already carry `user-invocable: false`.

    A correct stamper therefore changes NOTHING. This runs against COPIES in a
    tmp dir, so the repo itself is never written to.
    """

    def _copy_real_skills(self) -> list[tuple[Path, bytes]]:
        sources = sorted((REPO_ROOT / "skills").rglob("SKILL.md"))
        self.assertGreaterEqual(len(sources), 40, "expected the in-tree skills/ tree")
        copies: list[tuple[Path, bytes]] = []
        for source in sources:
            target = self.tmp / source.relative_to(REPO_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copies.append((target, source.read_bytes()))
        return copies

    def test_apply_over_real_skills_is_a_byte_identical_no_op(self) -> None:
        copies = self._copy_real_skills()
        results = stamper.stamp_paths([path for path, _ in copies], apply=True)
        self.assertTrue(all(r.status == stamper.STATUS_COMPLIANT for r in results),
                        [r.to_dict() for r in results if r.status != stamper.STATUS_COMPLIANT])
        for path, original in copies:
            self.assertEqual(path.read_bytes(), original, f"byte drift in {path.name}")

    def test_check_over_real_skills_exits_zero(self) -> None:
        copies = self._copy_real_skills()
        self.assertEqual(
            self.run_cli(["--check", "--json", *[str(p) for p, _ in copies]])[0], 0)

    def test_real_skills_stay_compatible_with_the_surface_policy_parser(self) -> None:
        copies = self._copy_real_skills()
        stamper.stamp_paths([path for path, _ in copies], apply=True)
        for path, _ in copies:
            self.assertEqual(policy.read_user_invocable(path), "false", str(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
