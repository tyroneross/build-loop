#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the cross-agent public/helper skill surface policy."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

sys.path.insert(0, str(HERE))  # importable when run directly via pytest <file>

from exposure_policy import (  # noqa: E402
    JUSTIFICATION_FIELD,
    USER_INVOCABLE_FIELD,
    classify,
    is_undeclared,
)
CODEX_PLUGIN_JSON = REPO_ROOT / ".codex-plugin" / "plugin.json"
CODEX_SKILLS_DIR = REPO_ROOT / "codex-skills"
CODEX_ARTIFACT_DIR = REPO_ROOT / "plugin-artifacts" / "codex"
SKILLS_DIR = REPO_ROOT / "skills"

# Codex has NO commands surface — it can only reach a plugin through a skill.
# So its wrapper skill in `codex-skills/` is legitimately `user-invocable: true`
# while every skill in `skills/` stays hidden behind `/build-loop:run`. That is a
# real host difference, NOT drift: do not "reconcile" this set to empty, and do
# not add a Claude-side twin of it. Claude-side exposure is declared per-skill via
# `public-justification:` (see `surface_violation` below), never by a list here.
CODEX_PUBLIC_ENTRYPOINTS = {"build-loop"}

# Prose copies of the surface policy. The codex copy is GENERATED verbatim by
# `scripts/build_codex_plugin_artifact.py` (`docs` is in its RUNTIME_DIRS), so it
# is asserted byte-identical rather than checked twice.
POLICY_DOC = "docs/agent-surface-policy.md"
GENERATED_POLICY_DOC = "plugin-artifacts/codex/docs/agent-surface-policy.md"
CURSOR_SURFACE_RULE = ".cursor/rules/build-loop-surface.mdc"
# Names these docs used to advertise as public entrypoints. Commit 7c4cf57
# (2026-07-26) collapsed the human surface to a single `/build-loop:run` and the
# prose was not updated, so agents on other hosts were told the opposite of the
# code for days. Re-listing any of them is the drift this assertion catches.
RETIRED_PUBLIC_ENTRYPOINT_NAMES = (
    "debug-loop",
    "optimize",
    "research",
    "knowledge",
    "handoff",
    "repo-closeout",
    "repo-maintenance",
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
USER_INVOCABLE_RE = re.compile(rf"^{USER_INVOCABLE_FIELD}:\s*(.+?)\s*$", re.MULTILINE)
PUBLIC_JUSTIFICATION_RE = re.compile(rf"^{JUSTIFICATION_FIELD}:\s*(.+?)\s*$", re.MULTILINE)


def read_frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise AssertionError(f"missing frontmatter: {path}")
    return match.group(1)


def read_name(path: Path) -> str:
    frontmatter = read_frontmatter(path)
    match = NAME_RE.search(frontmatter)
    if match is None:
        raise AssertionError(f"missing name: {path}")
    name = match.group(1).strip().strip('"').strip("'")
    return name.split(":", 1)[1] if ":" in name else name


def _field(frontmatter: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(frontmatter)
    if match is None:
        return None
    return match.group(1).strip().strip('"').strip("'").strip()


def read_user_invocable(path: Path) -> str | None:
    return _field(read_frontmatter(path), USER_INVOCABLE_RE)


def surface_violation(label: str, frontmatter: str) -> str | None:
    """The whole Claude-side surface policy, applied to one frontmatter block.

    Every plugin-owned skill is hidden (`user-invocable: false`) UNLESS its own
    frontmatter carries a non-empty `public-justification:`. The declaration and
    the exposing field live in the SAME artifact, so there is no second list to
    drift out of sync — which is exactly how the old hardcoded
    `CLAUDE_PUBLIC_ENTRYPOINTS` set ended up demanding the opposite of the
    shipped policy for days after commit 7c4cf57.

    The DETERMINATION is `exposure_policy.classify` — the same call
    `surface_policy.py`, `skill_index.py`, and `stamp_skill_frontmatter.py` make.
    This function only extracts the two fields and renders the message; it used
    to restate the rule inline, comparing the literal `'false'` case-sensitively
    while its peers lowercased, so `user-invocable: False` got a different answer
    depending on which tool read the file.

    Returns a violation string, or None when the file is compliant.
    """
    flag = _field(frontmatter, USER_INVOCABLE_RE)
    exposure = classify(flag, _field(frontmatter, PUBLIC_JUSTIFICATION_RE))
    if not is_undeclared(exposure):
        return None
    reason = (
        f"no `{USER_INVOCABLE_FIELD}` field (the harness default is PUBLIC)"
        if flag is None
        else f"{USER_INVOCABLE_FIELD}={flag!r} ({exposure})"
    )
    return (
        f"{label}: {reason} — expected `{USER_INVOCABLE_FIELD}: false`, or `true` "
        f"plus a non-empty `{JUSTIFICATION_FIELD}:` line in this same frontmatter"
    )


class CodexSurfaceTests(unittest.TestCase):
    def test_codex_manifest_uses_public_skill_root(self) -> None:
        data = json.loads(CODEX_PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertEqual(data.get("skills"), "./codex-skills")

    def test_codex_source_wrappers_are_exact_entrypoint_set(self) -> None:
        names = {
            read_name(path)
            for path in sorted(CODEX_SKILLS_DIR.glob("*/SKILL.md"))
        }
        self.assertEqual(names, CODEX_PUBLIC_ENTRYPOINTS)

    def test_codex_marketplace_points_to_full_artifact(self) -> None:
        data = json.loads((REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        entries = {entry["name"]: entry for entry in data.get("plugins", [])}
        self.assertEqual(entries["build-loop"].get("source"), "./plugin-artifacts/codex")

    def test_codex_artifact_exposes_approved_public_skills(self) -> None:
        data = json.loads((CODEX_ARTIFACT_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(data.get("skills"), "./codex-skills")
        skill_paths = sorted(
            str(path.relative_to(CODEX_ARTIFACT_DIR))
            for path in (CODEX_ARTIFACT_DIR / "codex-skills").rglob("SKILL.md")
        )
        self.assertEqual(
            skill_paths,
            [
                "codex-skills/build-loop/SKILL.md",
            ],
        )
        wrapper = CODEX_ARTIFACT_DIR / "codex-skills" / "build-loop" / "SKILL.md"
        self.assertEqual(read_name(wrapper), "build-loop")
        self.assertEqual(read_user_invocable(wrapper), "true")
        self.assertEqual(
            (wrapper.parent / ".." / ".." / "skills" / "build-loop" / "SKILL.md")
            .resolve()
            .read_text(encoding="utf-8"),
            (SKILLS_DIR / "build-loop" / "SKILL.md").read_text(encoding="utf-8"),
        )

    def test_codex_artifact_is_included_in_npm_package_files(self) -> None:
        data = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertIn("plugin-artifacts/codex", data.get("files", []))
        self.assertIn(".agents/plugins", data.get("files", []))
        self.assertNotIn(".agents", data.get("files", []))

    def test_repo_maintenance_documents_audit_limits(self) -> None:
        text = (SKILLS_DIR / "repo-maintenance" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("source-tree evidence only", text)
        self.assertIn("does not prove reproducibility", text)
        self.assertIn("recursive inventory", text)
        self.assertIn("release-artifact", text)
        self.assertIn("active-missing-artifact", text)

    def test_repo_closeout_is_compatibility_alias(self) -> None:
        text = (SKILLS_DIR / "repo-closeout" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("retained for one release", text.lower())
        self.assertIn("../repo-maintenance/SKILL.md", text)


class ClaudeSurfaceTests(unittest.TestCase):
    def test_claude_skills_are_internal_to_the_single_run_command(self) -> None:
        paths = sorted(SKILLS_DIR.rglob("SKILL.md"))
        self.assertNotEqual(paths, [], "no SKILL.md found — the scan is vacuous")

        violations = [
            violation
            for path in paths
            if (
                violation := surface_violation(
                    str(path.relative_to(REPO_ROOT)), read_frontmatter(path)
                )
            )
            is not None
        ]

        self.assertEqual(violations, [], "\n".join(violations))

    def test_public_justification_is_the_only_exception_path(self) -> None:
        """Fixture-driven, because today ZERO real skills are public.

        Without these the exception branch would ship untested and the flat
        invariant would be indistinguishable from a blanket "always false".
        """
        compliant = [
            "name: x\nuser-invocable: false",
            # true + justification: frontmatter-local opt-in, the only way out.
            "name: x\nuser-invocable: true\npublic-justification: sole human entry",
            "user-invocable: 'true'\npublic-justification: \"quoted forms count\"",
            # false wins regardless of a leftover justification line.
            "name: x\nuser-invocable: false\npublic-justification: stale leftover",
        ]
        for frontmatter in compliant:
            with self.subTest(frontmatter=frontmatter):
                self.assertIsNone(surface_violation("fixture", frontmatter))

        violating = [
            # The harness default is fail-open: no field means PUBLIC.
            "name: x\ndescription: y",
            "name: x\nuser-invocable: true",
            "name: x\nuser-invocable: true\npublic-justification:",
            # A justification alone does not expose anything.
            "name: x\npublic-justification: wishful thinking",
            # An unparseable flag is not an opt-in either.
            "name: x\nuser-invocable: maybe\npublic-justification: nonsense flag",
        ]
        for frontmatter in violating:
            with self.subTest(frontmatter=frontmatter):
                self.assertIsNotNone(surface_violation("fixture", frontmatter))


class OtherAgentSurfaceTests(unittest.TestCase):
    def test_host_neutral_policy_and_cursor_rule_exist(self) -> None:
        self.assertTrue((REPO_ROOT / POLICY_DOC).is_file())
        self.assertTrue((REPO_ROOT / CURSOR_SURFACE_RULE).is_file())

    def test_policy_prose_never_claims_a_source_skill_is_public(self) -> None:
        """Prose is a policy copy, so it can drift from code. This is the gate.

        The codex doc SHIPS TO CODEX: when it disagreed with the code, agents on
        that host were instructed to load skills the harness hides.
        """
        for rel in (POLICY_DOC, GENERATED_POLICY_DOC, CURSOR_SURFACE_RULE):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            with self.subTest(doc=rel):
                # Deliberately a blunt substring ban, so it cannot be evaded by
                # rephrasing. Prose that must DESCRIBE the opt-in mechanism says
                # "flips `user-invocable` to true" instead of spelling the pair.
                self.assertNotIn(
                    "user-invocable: true",
                    text,
                    f"{rel} states a skill is publicly invocable; the code hides "
                    "every skills/**/SKILL.md",
                )
                self.assertIn("user-invocable: false", text, rel)
                self.assertIn("/build-loop:run", text, rel)
                for name in RETIRED_PUBLIC_ENTRYPOINT_NAMES:
                    self.assertNotIn(
                        f"`{name}`",
                        text,
                        f"{rel} re-advertises retired public entrypoint {name!r}",
                    )

    def test_codex_policy_doc_is_the_generated_copy(self) -> None:
        self.assertEqual(
            (REPO_ROOT / GENERATED_POLICY_DOC).read_text(encoding="utf-8"),
            (REPO_ROOT / POLICY_DOC).read_text(encoding="utf-8"),
            f"{GENERATED_POLICY_DOC} is generated verbatim from {POLICY_DOC} by "
            "scripts/build_codex_plugin_artifact.py — edit the source and rebuild, "
            "never hand-edit the artifact",
        )

    def test_agent_role_taxonomy_is_discoverable(self) -> None:
        taxonomy = REPO_ROOT / "references" / "agent-role-taxonomy.md"
        self.assertTrue(taxonomy.is_file())
        taxonomy_text = taxonomy.read_text(encoding="utf-8")
        self.assertIn("The lead is the session holding the current valid Rally Point leadership lease", taxonomy_text)
        self.assertIn("Build-loop already has a dedicated coder subagent: `implementer`", taxonomy_text)

        index_text = (REPO_ROOT / "references" / "INDEX.md").read_text(encoding="utf-8")
        orchestrator_text = (REPO_ROOT / "agents" / "build-orchestrator.md").read_text(encoding="utf-8")
        skill_text = (REPO_ROOT / "skills" / "build-loop" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("agent-role-taxonomy.md", index_text)
        self.assertIn("agent-role-taxonomy.md", orchestrator_text)
        self.assertIn("agent-role-taxonomy.md", skill_text)

    def test_rally_coordination_boundary_is_current(self) -> None:
        instruction_paths = [
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "CLAUDE.md",
            CODEX_ARTIFACT_DIR / "AGENTS.md",
        ]

        for path in instruction_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("rally codex --human", text, str(path))
            self.assertNotIn("rally start", text, str(path))
            self.assertNotIn("--session-id", text, str(path))
            self.assertIn("Rally is coordination metadata", text, str(path))

        for path in [REPO_ROOT / "README.md", CODEX_ARTIFACT_DIR / "README.md"]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("rally codex --human", text, str(path))
            self.assertNotIn("rally start", text, str(path))

        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        coordination_text = (REPO_ROOT / "references" / "coordination-rules.md").read_text(encoding="utf-8")
        skill_text = (REPO_ROOT / "skills" / "build-loop" / "SKILL.md").read_text(encoding="utf-8")
        # README documents the Rally evidence-boundary in plain-copy style (no
        # "X, not Y" antithesis in public-facing copy); internal docs below keep
        # the canonical phrase.
        self.assertIn("Rally verifies nothing on its own", readme_text)
        self.assertIn("Evidence boundary (Rally is not a verifier)", coordination_text)
        self.assertIn("not verification evidence", skill_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
