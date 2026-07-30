#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/exposure_policy.py — the one exposure rule.

The last two classes are the point of the module: EVERY consumer is asked about
the SAME frontmatter and must never disagree. That is the defect this module
exists to make impossible, and it has now shipped twice —

  * the first draft of `skill_index.py` read a missing `user-invocable` field as
    hidden, the inverse of the harness's `userInvocable ?? true`, and nothing but
    a human eye caught it;
  * `stamp_skill_frontmatter.py` compared the flag case-SENSITIVELY while its two
    peers lowercased, so `user-invocable: False` was a `violation` to one tool
    and `hidden` to the other two.

`TestEveryConsumerAgrees` is the artifact that stops a third: one value matrix,
every consumer, one answer per row.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Ensure scripts/ is importable when run directly via pytest <file>
sys.path.insert(0, str(Path(__file__).resolve().parent))

import exposure_policy  # noqa: E402
import skill_index  # noqa: E402
import stamp_skill_frontmatter  # noqa: E402
import surface_policy  # noqa: E402
import test_agent_surface_policy  # noqa: E402
from exposure_policy import (  # noqa: E402
    DEFAULT_PUBLIC,
    EXPOSURE_CLASSES,
    HIDDEN,
    PUBLIC_JUSTIFIED,
    PUBLIC_UNJUSTIFIED,
    UNDECLARED_CLASSES,
    classify,
    is_excluded_path,
    is_public,
    is_undeclared,
    normalize_flag,
    unquote,
)


# ---------------------------------------------------------------------------
# The rule — one case per class
# ---------------------------------------------------------------------------

class TestClassify(unittest.TestCase):
    def test_absent_field_is_public_by_harness_default(self) -> None:
        """`userInvocable ?? true`. THE case both consumers must agree on.

        Break this — return HIDDEN for None — and every classification in the
        repo inverts. Both consumer suites are wired to fail when it does.
        """
        self.assertEqual(classify(None), DEFAULT_PUBLIC)
        self.assertTrue(is_public(classify(None)))
        self.assertTrue(is_undeclared(classify(None)))

    def test_false_is_hidden(self) -> None:
        self.assertEqual(classify("false"), HIDDEN)
        self.assertFalse(is_public(HIDDEN))
        self.assertFalse(is_undeclared(HIDDEN))

    def test_true_with_justification_is_justified(self) -> None:
        self.assertEqual(classify("true", "sole human entrypoint"), PUBLIC_JUSTIFIED)
        self.assertTrue(is_public(PUBLIC_JUSTIFIED))
        self.assertFalse(is_undeclared(PUBLIC_JUSTIFIED))

    def test_true_without_justification_is_unjustified(self) -> None:
        self.assertEqual(classify("true"), PUBLIC_UNJUSTIFIED)
        self.assertTrue(is_undeclared(PUBLIC_UNJUSTIFIED))

    def test_false_wins_over_a_stale_justification(self) -> None:
        self.assertEqual(classify("false", "leftover from an old decision"), HIDDEN)

    def test_justification_alone_exposes_nothing(self) -> None:
        self.assertEqual(classify(None, "wishful thinking"), DEFAULT_PUBLIC)


class TestUnrecognizedValues(unittest.TestCase):
    """A flag that does not spell the opt-out is never a deliberate opt-in, so it
    cannot buy the justified class — whichever way the harness coerces it.

    The harness reads `yes`/`1` as public and `maybe`/empty as hidden. This repo
    reports all four, a deliberate over-report documented in `exposure_policy`.
    """

    def test_unrecognized_value_is_unjustified_even_with_a_reason(self) -> None:
        for flag in ("yes", "1", "maybe", "TRUE-ish"):
            with self.subTest(flag=flag):
                self.assertEqual(classify(flag, "a reason"), PUBLIC_UNJUSTIFIED)

    def test_unrecognized_value_without_a_reason_is_unjustified(self) -> None:
        self.assertEqual(classify("maybe"), PUBLIC_UNJUSTIFIED)

    def test_empty_value_is_not_the_same_as_an_absent_field(self) -> None:
        """`user-invocable:` (YAML null) is present-but-useless, not absent."""
        self.assertEqual(classify(""), PUBLIC_UNJUSTIFIED)
        self.assertEqual(classify(None), DEFAULT_PUBLIC)


class TestNormalization(unittest.TestCase):
    def test_quoted_and_padded_forms_normalize(self) -> None:
        for raw in ('"false"', "'false'", "  false  ", "FALSE", "False"):
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw), HIDDEN)

    def test_case_variants_are_hidden_because_the_harness_hides_them(self) -> None:
        """The settled answer to the case question — see `exposure_policy`'s
        docstring for the decoded harness coercion table that decides it.

        Both YAML readings converge: `False` parses to boolean false under YAML
        1.2 core, and lowercases into the harness's falsy set if it arrives as a
        string. No consumer may call this exposed.
        """
        for raw in ("False", "FALSE", "FaLsE", '"FALSE"'):
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw), HIDDEN)
                self.assertFalse(is_public(classify(raw)))
        for raw in ("True", "TRUE"):
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw, "a reason"), PUBLIC_JUSTIFIED)
                self.assertEqual(classify(raw), PUBLIC_UNJUSTIFIED)

    def test_quoted_true_plus_quoted_justification(self) -> None:
        self.assertEqual(classify("'true'", '"quoted forms count"'), PUBLIC_JUSTIFIED)

    def test_whitespace_only_justification_does_not_count(self) -> None:
        self.assertEqual(classify("true", "   "), PUBLIC_UNJUSTIFIED)
        self.assertEqual(classify("true", ""), PUBLIC_UNJUSTIFIED)

    def test_normalize_flag_is_idempotent(self) -> None:
        once = normalize_flag(' "TRUE" ')
        self.assertEqual(once, "true")
        self.assertEqual(normalize_flag(once), once)
        self.assertIsNone(normalize_flag(None))

    def test_unquote_strips_quotes_and_space(self) -> None:
        self.assertEqual(unquote('  "why"  '), "why")


class TestClassSet(unittest.TestCase):
    def test_every_input_lands_in_a_declared_class(self) -> None:
        inputs = [None, "false", "true", "yes", "", "  ", "False"]
        for flag in inputs:
            for reason in (None, "", "because"):
                with self.subTest(flag=flag, reason=reason):
                    self.assertIn(classify(flag, reason), EXPOSURE_CLASSES)

    def test_undeclared_classes_are_the_public_ones_without_a_reason(self) -> None:
        self.assertEqual(set(UNDECLARED_CLASSES), {DEFAULT_PUBLIC, PUBLIC_UNJUSTIFIED})
        for klass in UNDECLARED_CLASSES:
            self.assertIn(klass, EXPOSURE_CLASSES)
            self.assertTrue(is_public(klass))

    def test_hidden_is_the_only_non_public_class(self) -> None:
        self.assertEqual([c for c in EXPOSURE_CLASSES if not is_public(c)], [HIDDEN])


# ---------------------------------------------------------------------------
# Shared path exclusion
# ---------------------------------------------------------------------------

class TestExcludedPaths(unittest.TestCase):
    def test_worktree_copies_are_excluded(self) -> None:
        for rel in (
            ("skills", ".build-loop", "worktrees", "run-1", "skills", "a", "SKILL.md"),
            (".claude", "worktrees", "run-2", "skills", "a", "SKILL.md"),
            ("skills", "node_modules", "pkg", "SKILL.md"),
            ("plugin-artifacts", "codex", "skills", "a", "SKILL.md"),
        ):
            with self.subTest(rel=rel):
                self.assertTrue(is_excluded_path(rel))

    def test_a_skill_named_after_worktrees_survives(self) -> None:
        """Segment matching, never substring: `data-plane-worktrees` is a skill."""
        self.assertFalse(
            is_excluded_path(("skills", "data-plane-worktrees", "SKILL.md"))
        )

    def test_split_segments_do_not_match(self) -> None:
        self.assertFalse(
            is_excluded_path(("skills", ".build-loop", "x", "worktrees", "SKILL.md"))
        )

    def test_ordinary_skill_path_is_kept(self) -> None:
        self.assertFalse(is_excluded_path(("skills", "architecture", "scan", "SKILL.md")))


# ---------------------------------------------------------------------------
# Consumer parity — the reason this module exists
# ---------------------------------------------------------------------------

#: policy class -> the column `skill_index` renders it as.
_INDEX_COLUMN = {
    HIDDEN: skill_index.HIDDEN,
    PUBLIC_JUSTIFIED: skill_index.PUBLIC,
    PUBLIC_UNJUSTIFIED: skill_index.PUBLIC_UNDECLARED,
    DEFAULT_PUBLIC: skill_index.PUBLIC_UNDECLARED,
}

FRONTMATTER_CASES = {
    "hidden": "name: a\ndescription: d\nuser-invocable: false",
    "justified": "name: a\ndescription: d\nuser-invocable: true\npublic-justification: sole entry",
    "unjustified": "name: a\ndescription: d\nuser-invocable: true",
    "absent": "name: a\ndescription: d",
    "quoted-false": 'name: a\ndescription: d\nuser-invocable: "false"',
    "unrecognized": "name: a\ndescription: d\nuser-invocable: yes\npublic-justification: nonsense",
    "stale-justification": "name: a\ndescription: d\nuser-invocable: false\npublic-justification: old",
}


class TestConsumersCannotDisagree(unittest.TestCase):
    """Both CLIs must read the same file the same way, in their own vocabulary."""

    def test_both_consumers_agree_on_every_case(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for slug, frontmatter in FRONTMATTER_CASES.items():
                path = root / "skills" / slug / "SKILL.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"---\n{frontmatter}\n---\n\n# Body\n", encoding="utf-8")

            index_rows = {
                Path(row.path).parent.name: row.exposure
                for row in skill_index.discover(root)
            }
            for record in surface_policy.discover_skills(root):
                slug = Path(record["path"]).parent.name
                with self.subTest(case=slug):
                    self.assertEqual(
                        index_rows[slug],
                        _INDEX_COLUMN[record["class"]],
                        f"{slug}: surface_policy says {record['class']!r}, "
                        f"skill_index says {index_rows[slug]!r}",
                    )

    def test_consumers_import_the_shared_names(self) -> None:
        """Not a re-declaration in each file — the same objects."""
        self.assertIs(surface_policy.HIDDEN, exposure_policy.HIDDEN)
        self.assertIs(surface_policy.DEFAULT_PUBLIC, exposure_policy.DEFAULT_PUBLIC)
        self.assertIs(surface_policy.SKILL_CLASSES, exposure_policy.EXPOSURE_CLASSES)
        self.assertIs(
            skill_index.EXCLUDED_PATH_SEGMENTS, exposure_policy.EXCLUDED_PATH_SEGMENTS
        )

    def test_the_index_covers_every_policy_class(self) -> None:
        self.assertEqual(set(_INDEX_COLUMN), set(EXPOSURE_CLASSES))
        self.assertEqual(set(_INDEX_COLUMN.values()), set(skill_index.EXPOSURE_CLASSES))


# ---------------------------------------------------------------------------
# The cross-tool agreement matrix — the regression artifact
# ---------------------------------------------------------------------------

#: Every `user-invocable` spelling a SKILL.md can plausibly carry, with the two
#: answers that have operational meaning. `hidden` = the user cannot reach it;
#: `rejected` = an enforcing gate refuses the file.
#:
#: `hidden` is asserted against the HARNESS, not against convenience: the values
#: below marked hidden are the ones Claude Code's own coercion resolves to false
#: (see `exposure_policy`'s docstring for the decoded table). Where this repo is
#: deliberately STRICTER than the harness — `no`, `off`, `0`, empty, `maybe` are
#: hidden by the harness but still rejected here — the row says so, and that
#: direction is safe: it over-reports, never under-reports.
#:
#: (slug, user-invocable line or None to omit, justification or None, hidden, rejected)
AGREEMENT_MATRIX = (
    # -- the canonical opt-out, and its case + quoting variants --------------
    ("lower-false",     "false",   None,           True,  False),
    ("title-false",     "False",   None,           True,  False),
    ("upper-false",     "FALSE",   None,           True,  False),
    ("quoted-false",    '"false"', None,           True,  False),
    ("squoted-false",   "'False'", None,           True,  False),
    ("padded-false",    "  false ", None,          True,  False),
    # `false` wins over a leftover justification, in every tool.
    ("false-stale-just", "false",  "old decision", True,  False),
    # -- the deliberate opt-in ----------------------------------------------
    ("true-justified",  "true",    "sole entry",   False, False),
    ("cased-true-just", "True",    "sole entry",   False, False),
    ("quoted-true-just", "'true'", '"quoted"',     False, False),
    # -- exposed with no stated reason --------------------------------------
    ("lower-true",      "true",    None,           False, True),
    ("upper-true",      "TRUE",    None,           False, True),
    ("true-empty-just", "true",    "",             False, True),
    # -- the fail-open default ----------------------------------------------
    ("absent",          None,      None,           False, True),
    ("just-only",       None,      "wishful",      False, True),
    # -- values this repo refuses to interpret ------------------------------
    # The harness reads these as PUBLIC, so rejecting them is also correct there.
    ("yes",             "yes",     None,           False, True),
    ("one",             "1",       None,           False, True),
    ("on",              "on",      "a reason",     False, True),
    # The harness reads THESE as hidden; the repo rejects them anyway and demands
    # the canonical literal. Strictly over-reporting, never under-reporting.
    ("no",              "no",      None,           False, True),
    ("zero",            "0",       None,           False, True),
    ("off",             "off",     None,           False, True),
    ("maybe",           "maybe",   "nonsense",     False, True),
    ("empty",           "",        None,           False, True),
)


def _frontmatter(flag: str | None, justification: str | None) -> str:
    lines = ["name: probe", "description: a probe fixture"]
    if flag is not None:
        lines.append(f"{exposure_policy.USER_INVOCABLE_FIELD}: {flag}")
    if justification is not None:
        lines.append(f"{exposure_policy.JUSTIFICATION_FIELD}: {justification}")
    return "\n".join(lines)


class TestEveryConsumerAgrees(unittest.TestCase):
    """One value matrix through EVERY consumer; one answer per row.

    Four tools read this frontmatter and each renders its own vocabulary —
    `surface_policy` a class, `skill_index` a table column, `stamp_skill_frontmatter`
    an authoring verdict, `test_agent_surface_policy` a violation string. The
    vocabularies differ on purpose. The two facts underneath them must not:
    whether the user can reach the skill, and whether a gate refuses the file.

    This is the test that would have caught the case split. Before 2026-07-30
    `user-invocable: False` was `hidden` to two tools and a `violation` to a
    third, and no test asked them the same question at the same time.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        for slug, flag, justification, _hidden, _rejected in AGREEMENT_MATRIX:
            path = cls.root / "skills" / slug / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"---\n{_frontmatter(flag, justification)}\n---\n\n# Body\n",
                encoding="utf-8",
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _readings(self, slug: str) -> dict[str, tuple[bool, bool]]:
        """Every consumer's (hidden, rejected) verdict for one fixture."""
        path = self.root / "skills" / slug / "SKILL.md"
        text = path.read_text(encoding="utf-8")

        record = surface_policy.classify_skill(path, self.root)
        row = skill_index.build_row(self.root, path, "probe-plugin")
        stamped = stamp_skill_frontmatter.stamp_file(path, apply=False)
        violation = test_agent_surface_policy.surface_violation(
            slug, test_agent_surface_policy.read_frontmatter(path)
        )
        return {
            "surface_policy": (
                not record["public"],
                record["class"] in surface_policy.VIOLATION_CLASSES,
            ),
            "skill_index": (
                row.exposure == skill_index.HIDDEN,
                row.exposure == skill_index.PUBLIC_UNDECLARED,
            ),
            "stamp_skill_frontmatter": (
                stamped.status == stamp_skill_frontmatter.STATUS_COMPLIANT,
                not stamped.ok,
            ),
            "test_agent_surface_policy": (
                # This gate reports only rejection, so it derives `hidden` from
                # the shared rule rather than inventing a second reading.
                classify(*_fields(text)) == HIDDEN,
                violation is not None,
            ),
        }

    def test_every_consumer_returns_one_answer_per_row(self) -> None:
        for slug, flag, _just, hidden, rejected in AGREEMENT_MATRIX:
            readings = self._readings(slug)
            for tool, (got_hidden, got_rejected) in readings.items():
                with self.subTest(value=flag, tool=tool):
                    self.assertEqual(
                        got_hidden, hidden,
                        f"{tool} reads `user-invocable: {flag}` as "
                        f"{'hidden' if got_hidden else 'reachable'}; the shared "
                        f"rule says {'hidden' if hidden else 'reachable'}",
                    )
                    self.assertEqual(
                        got_rejected, rejected,
                        f"{tool} {'rejects' if got_rejected else 'accepts'} "
                        f"`user-invocable: {flag}`; the shared rule says "
                        f"{'reject' if rejected else 'accept'}",
                    )

    def test_the_rule_is_never_more_permissive_than_the_harness(self) -> None:
        """The safety invariant: nothing this repo calls hidden is reachable.

        Every input classified HIDDEN normalizes to the literal `false`, and the
        harness's coercion maps `false` (boolean or string, any case) to hidden.
        So a false negative here — a skill the repo calls hidden that the harness
        exposes — is unreachable by construction. Over-reporting is the only
        error this rule can make.
        """
        for _slug, flag, just, hidden, _rejected in AGREEMENT_MATRIX:
            if hidden:
                with self.subTest(value=flag):
                    self.assertEqual(normalize_flag(flag), "false")
                    self.assertEqual(classify(flag, just), HIDDEN)

    def test_hidden_and_rejected_are_mutually_exclusive(self) -> None:
        """A hidden skill is never rejected, and vice versa — no row is both."""
        for slug, _flag, _just, hidden, rejected in AGREEMENT_MATRIX:
            with self.subTest(case=slug):
                self.assertFalse(hidden and rejected)

    def test_the_matrix_covers_every_shape_the_field_can_take(self) -> None:
        """A row silently dropped from the matrix is a hole in the gate."""
        flags = {flag for _s, flag, _j, _h, _r in AGREEMENT_MATRIX}
        for required in (None, "", "false", "False", "FALSE", "true", "True",
                         "yes", "no", "0", "1", '"false"'):
            self.assertIn(required, flags, f"matrix lost the {required!r} case")


def _fields(text: str) -> tuple[str | None, str | None]:
    """Extract the two fields from a full SKILL.md, the way the gate does."""
    frontmatter = surface_policy.parse_frontmatter(text) or ""
    inv = surface_policy.USER_INVOCABLE_RE.search(frontmatter)
    just = surface_policy.JUSTIFICATION_RE.search(frontmatter)
    return (inv.group(1) if inv else None, just.group(1) if just else None)


if __name__ == "__main__":
    unittest.main()
