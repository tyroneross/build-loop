#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Build the slim Codex plugin artifact.

The source repo keeps Claude's full internal skill tree at ``skills/``. Codex
auto-discovers root ``skills/*/SKILL.md`` entries, so the Codex marketplace must
install from an artifact that exposes only the approved public skills.
"""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Iterable


DEFAULT_TARGET = Path("plugin-artifacts/codex")
IGNORED_NAMES = {".DS_Store", "__pycache__", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc"}
TOP_LEVEL_FILES = ("AGENTS.md", "README.md", "LICENSE")
ASSET_FILES = (Path("assets") / "build-loop-plugin-icon.png",)
PUBLIC_SKILLS = ("build-loop", "repo-closeout")

# Bundle markdown points readers at ``references/<file>.md`` (root-relative).
# The Claude source resolves that logical namespace across the repo's top-level
# ``references/`` plus each skill's own ``references/``. The slim Codex bundle
# ships only approved public skills, so those pointers dangle unless we mirror
# every cited reference into ONE top-level ``references/`` dir at the bundle
# root — the natural resolution point for both AGENTS.md (at root) and any
# deeper file an LLM reads. Search order resolves drift deterministically: the
# skill's own copy wins (it's the one already shipped in the skill tree), then
# the repo root, then the ui-design skill.
REFERENCE_SOURCE_DIRS = (
    Path("skills") / "build-loop" / "references",
    Path("skills") / "repo-closeout" / "references",
    Path("references"),
    Path("skills") / "ui-design" / "references",
)
# A bare ``references/X.md`` pointer. Anchored to a path boundary so we don't
# match foreign-skill prose like ``build-loop:deepagents`` references — those
# are always written with the skill name in front (``The skill's
# `references/anti-patterns.md```) and resolve inside that other skill, which
# the slim bundle does not ship.
_REFERENCE_POINTER_RE = re.compile(r"(?:^|[\s`(\[])references/([A-Za-z0-9_.-]+\.md)")
# Pointers that name another skill's references dir in prose, not a build-loop
# bundle file. They have no build-loop source and must not fail the check.
FOREIGN_SKILL_REFERENCES = frozenset(
    {
        "anti-patterns.md",  # build-loop:building-with-deepagents
        "stack-templates.md",  # build-loop:logging-tracer
        "ios-notification-alarm-playbook.md",  # build-loop:debugging-memory
    }
)
# Pre-existing, deliberate forward-reference placeholders in the source tree
# (marked TBD in prose). They have no file anywhere — in the Claude tree either.
# Not codex-introduced; fixing them is out of scope for the artifact builder, so
# they are tolerated rather than blocking every bundle build. Keep this list
# tight: a NEW unresolvable pointer on the primary surface must still fail.
KNOWN_TBD_REFERENCES = frozenset(
    {
        "brief-filters.md",  # references/implementer-envelope-schema.md: "(TBD)"
    }
)


def cited_reference_basenames(*roots: Path) -> set[str]:
    """Every bare ``references/<file>.md`` pointer across the given markdown roots."""
    cited: set[str] = set()
    for root in roots:
        for md in root.rglob("*.md"):
            cited.update(
                _REFERENCE_POINTER_RE.findall(
                    md.read_text(encoding="utf-8", errors="ignore")
                )
            )
    return cited


def resolve_reference_source(source: Path, basename: str) -> Path | None:
    """Locate a cited reference file in the build-loop source search paths."""
    for rel_dir in REFERENCE_SOURCE_DIRS:
        candidate = source / rel_dir / basename
        if candidate.is_file():
            return candidate
    return None


def mirror_references(source: Path, bundle_root: Path) -> None:
    """Copy the transitive closure of cited references into ``<bundle>/references/``.

    Iterates to a fixpoint: a mirrored reference file may itself cite further
    ``references/X.md``, so re-scan after each copy until no new resolvable
    pointer appears. Makes every root-relative ``references/X.md`` pointer in the
    bundle resolve at the bundle root. Foreign-skill and known-TBD pointers are
    skipped (no build-loop source); a genuinely unexpected unresolvable pointer
    is left for ``check_reference_pointers`` to adjudicate by surface.
    """
    out_dir = bundle_root / "references"
    copied: set[str] = set()
    while True:
        cited = cited_reference_basenames(bundle_root)
        new_resolvable = False
        for basename in sorted(cited - copied):
            copied.add(basename)
            if basename in FOREIGN_SKILL_REFERENCES or basename in KNOWN_TBD_REFERENCES:
                continue
            src_file = resolve_reference_source(source, basename)
            if src_file is not None:
                copy_file(src_file, out_dir / basename)
                new_resolvable = True
        if not new_resolvable:
            break


def check_reference_pointers(bundle_root: Path) -> None:
    """Assert every ``references/X.md`` pointer on the bundle's PRIMARY surface resolves.

    Primary surface = the files a Codex user actually lands on: top-level
    AGENTS.md / README.md and every approved public skill tree. Resolution
    target is the mirrored top-level ``<bundle>/references/``. Foreign-skill and
    known-TBD pointers are allowlisted. A dangling primary-surface pointer is a
    real regression and fails the build. Pointers that appear ONLY inside
    transitively-mirrored reference files (often forward TBDs) are tolerated —
    fixing unrelated source dead-links is not the artifact builder's job.
    """
    refs_dir = bundle_root / "references"
    surface_roots = [bundle_root / "skills" / name for name in PUBLIC_SKILLS]
    surface_roots += [
        bundle_root / name for name in ("AGENTS.md", "README.md") if (bundle_root / name).is_file()
    ]
    # rglob on a file path yields nothing; scan top-level files explicitly.
    cited: set[str] = set()
    for root in surface_roots:
        if root.is_dir():
            cited |= cited_reference_basenames(root)
        elif root.is_file():
            cited |= set(
                _REFERENCE_POINTER_RE.findall(root.read_text(encoding="utf-8", errors="ignore"))
            )
    dangling = sorted(
        b
        for b in cited
        if b not in FOREIGN_SKILL_REFERENCES
        and b not in KNOWN_TBD_REFERENCES
        and not (refs_dir / b).is_file()
    )
    if dangling:
        raise ArtifactError(
            "dangling reference pointers on the primary surface (no file at "
            "bundle references/): " + ", ".join(dangling)
        )


class ArtifactError(RuntimeError):
    pass


def ignore_generated(_dir: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in IGNORED_NAMES:
            ignored.add(name)
            continue
        if name.startswith("test_") and name.endswith(".py"):
            ignored.add(name)
            continue
        if any(name.endswith(suffix) for suffix in IGNORED_SUFFIXES):
            ignored.add(name)
    return ignored


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise ArtifactError(f"missing required file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise ArtifactError(f"missing required directory: {source}")
    shutil.copytree(source, target, ignore=ignore_generated)


def write_codex_manifest(source: Path, target: Path) -> None:
    manifest_path = source / ".codex-plugin" / "plugin.json"
    if not manifest_path.is_file():
        raise ArtifactError(f"missing required file: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skills"] = "./skills"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def write_notice(target: Path) -> None:
    target.write_text(
        "\n".join(
            [
                "# Codex Build Loop Artifact",
                "",
                "Generated by `python3 scripts/build_codex_plugin_artifact.py`.",
                "This artifact is the Codex marketplace install surface.",
                "It exposes two public skills: `build-loop` and `repo-closeout`.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def skill_files(root: Path) -> list[Path]:
    return sorted(root.rglob("SKILL.md"))


def validate_artifact(target: Path) -> None:
    manifest_path = target / ".codex-plugin" / "plugin.json"
    skills_root = target / "skills"
    if not manifest_path.is_file():
        raise ArtifactError(f"artifact missing {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("skills") != "./skills":
        raise ArtifactError("artifact Codex manifest must use skills=./skills")

    files = skill_files(skills_root)
    rel_files = [str(path.relative_to(target)) for path in files]
    expected = [f"skills/{name}/SKILL.md" for name in PUBLIC_SKILLS]
    if rel_files != expected:
        raise ArtifactError(f"artifact public skill set differs; expected={expected}, got={rel_files}")

    for rel_path in ASSET_FILES:
        if not (target / rel_path).is_file():
            raise ArtifactError(f"artifact missing asset: {rel_path}")

    check_reference_pointers(target)


def build_artifact(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target or target in source.parents:
        raise ArtifactError(f"refusing to write artifact above source: {target}")

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".codex-artifact-", dir=str(parent)) as tmp_raw:
        tmp = Path(tmp_raw)
        write_codex_manifest(source, tmp / ".codex-plugin" / "plugin.json")
        for name in TOP_LEVEL_FILES:
            copy_file(source / name, tmp / name)
        for rel_path in ASSET_FILES:
            copy_file(source / rel_path, tmp / rel_path)
        for name in PUBLIC_SKILLS:
            copy_tree(source / "skills" / name, tmp / "skills" / name)
        copy_file(source / "docs" / "agent-surface-policy.md", tmp / "docs" / "agent-surface-policy.md")
        write_notice(tmp / "BUILD-ARTIFACT.md")
        # Mirror cited references AFTER the skill tree + top-level files exist so
        # the pointer scan sees every citing file.
        mirror_references(source, tmp)
        validate_artifact(tmp)

        if target.exists():
            shutil.rmtree(target)
        os.replace(tmp, target)


def iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def assert_same_tree(expected: Path, actual: Path) -> None:
    expected_files = {path.relative_to(expected) for path in iter_files(expected)}
    actual_files = {path.relative_to(actual) for path in iter_files(actual)}
    if expected_files != actual_files:
        missing = sorted(str(path) for path in expected_files - actual_files)
        extra = sorted(str(path) for path in actual_files - expected_files)
        raise ArtifactError(f"artifact file set differs; missing={missing}, extra={extra}")
    for rel in sorted(expected_files):
        if not filecmp.cmp(expected / rel, actual / rel, shallow=False):
            raise ArtifactError(f"artifact file differs: {rel}")


def check_artifact(source: Path, target: Path) -> None:
    parent = target.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".codex-artifact-check-", dir=str(parent)) as tmp_raw:
        tmp = Path(tmp_raw) / "codex"
        build_artifact(source, tmp)
        assert_same_tree(tmp, target.resolve())
    validate_artifact(target.resolve())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=".", help="Build Loop source repo root.")
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="Codex artifact output directory.")
    parser.add_argument("--check", action="store_true", help="Fail if the artifact is missing or stale.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(args.source).expanduser().resolve()
    target = Path(args.target).expanduser()
    if not target.is_absolute():
        target = source / target
    try:
        if args.check:
            check_artifact(source, target)
            print(f"codex artifact up to date: {target}")
        else:
            build_artifact(source, target)
            print(f"codex artifact written: {target}")
        return 0
    except ArtifactError as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
