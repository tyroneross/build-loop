#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Build the full Codex plugin artifact behind one public entrypoint.

Codex indexes the manifest-declared ``codex-skills`` directory. The artifact
therefore keeps exactly one wrapper in that directory while packaging the
canonical Build Loop runtime at its existing source-relative paths.
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
# Name-based, matched against EVERY path component (see
# ``is_ignored_source_path``) and against every entry name during the copy (see
# ``ignore_generated``). ``.build`` is the SwiftPM output directory under
# ``skills/native-ax-driver/swift/bl-ax-driver/`` — ~64MB of local compiler
# output on any machine that has built the native AX driver. It belongs here,
# not in ``IGNORED_DIR_SUFFIXES``: that set is ``endswith``-matched, so it would
# also swallow a legitimately-named ``foo.build`` directory, and exact-name
# membership is what both the scan and the copy already agree on.
IGNORED_NAMES = {".DS_Store", "__pycache__", ".pytest_cache", ".build"}
IGNORED_SUFFIXES = {".pyc"}
IGNORED_DIR_SUFFIXES = {".egg-info"}
TOP_LEVEL_FILES = (
    ".gitattributes",
    ".npmignore",
    ".npmrc",
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "KNOWN-ISSUES.md",
    "LICENSE",
    "NOTICE",
    "README.md",
    "REUSE.toml",
    "conftest.py",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "tsconfig.json",
    "uv.lock",
)
RUNTIME_FILES = (
    Path(".agents") / "plugins" / "marketplace.json",
    Path(".claude-plugin") / "marketplace.json",
    Path(".claude-plugin") / "plugin.json",
)
RUNTIME_DIRS = (
    "agents",
    "architecture",
    "assets",
    "bin",
    "cli",
    "codex-skills",
    "commands",
    "dist",
    "docs",
    "evals",
    "hooks",
    "launchd",
    "references",
    "scripts",
    "skills",
    "src",
    "templates",
    "tests",
    "vendor",
)
PUBLIC_SKILL_ROOT = Path("codex-skills")
PUBLIC_SKILLS = ("build-loop",)
REQUIRED_RUNTIME_PATHS = (
    Path(".agents") / "plugins" / "marketplace.json",
    Path(".claude-plugin") / "plugin.json",
    Path("agents") / "build-orchestrator.md",
    Path("commands") / "run.md",
    Path("hooks") / "hooks.json",
    Path("scripts") / "self_mod_verify.py",
    Path("scripts") / "groundwork_exchange.py",
    Path("scripts") / "sync_plugin_cache.py",
    Path("skills") / "build-loop" / "SKILL.md",
    Path("skills") / "debug-loop" / "SKILL.md",
    Path("src") / "build_loop" / "__init__.py",
    Path("skills") / "build-loop" / "templates" / "codex-worker-prompt.md",
)

# Bundle markdown points readers at ``references/<file>.md`` (root-relative).
# The source resolves that logical namespace across the repo's top-level
# ``references/`` plus each skill's own ``references/``. Those pointers can
# still dangle from the public entry surface unless we mirror
# every cited reference into ONE top-level ``references/`` dir at the bundle
# root — the natural resolution point for both AGENTS.md (at root) and any
# deeper file an LLM reads. Search order resolves drift deterministically: the
# skill's own copy wins (it's the one already shipped in the skill tree), then
# the repo root, then the ui-design skill.
REFERENCE_SOURCE_DIRS = (
    Path("skills") / "build-loop" / "references",
    Path("skills") / "repo-maintenance" / "references",
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
    AGENTS.md / README.md, the public wrapper, and the canonical Build Loop
    workflow. Resolution
    target is the mirrored top-level ``<bundle>/references/``. Foreign-skill and
    known-TBD pointers are allowlisted. A dangling primary-surface pointer is a
    real regression and fails the build. Pointers that appear ONLY inside
    transitively-mirrored reference files (often forward TBDs) are tolerated —
    fixing unrelated source dead-links is not the artifact builder's job.
    """
    refs_dir = bundle_root / "references"
    surface_roots = [
        bundle_root / PUBLIC_SKILL_ROOT / "build-loop",
        bundle_root / "skills" / "build-loop",
    ]
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


def ignore_generated(directory: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` ignore callback — generated names AND every symlink.

    Symlinks are dropped rather than followed or preserved. Following one lets
    the artifact silently absorb whatever the link points at, including paths
    outside the source tree that no ignore rule ever inspected — a
    supply-chain-shaped hazard, not merely a size problem (the ~64MB
    ``.build/release -> arm64-apple-macosx/release`` self-link is the mild
    case). Preserving them instead would ship links that dangle or escape the
    bundle on extraction. Dropping keeps the artifact a closed set of regular
    files that were each individually ignore-filtered, and it is what makes
    ``iter_files`` and the copy path agree by construction.
    """
    base = Path(directory)
    ignored = set()
    for name in names:
        if (base / name).is_symlink():
            ignored.add(name)
            continue
        if name in IGNORED_NAMES:
            ignored.add(name)
            continue
        if any(name.endswith(suffix) for suffix in IGNORED_DIR_SUFFIXES):
            ignored.add(name)
            continue
        if any(name.endswith(suffix) for suffix in IGNORED_SUFFIXES):
            ignored.add(name)
    return ignored


def is_ignored_source_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in IGNORED_NAMES for part in relative.parts):
        return True
    if any(
        part.endswith(suffix)
        for part in relative.parts[:-1]
        for suffix in IGNORED_DIR_SUFFIXES
    ):
        return True
    return any(path.name.endswith(suffix) for suffix in IGNORED_SUFFIXES)


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
    if manifest.get("skills") != "./codex-skills":
        raise ArtifactError("source Codex manifest must use skills=./codex-skills")
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
                "It packages the full Build Loop runtime.",
                "It exposes one Codex skill entry: `build-loop`.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_artifact_gitattributes(target: Path) -> None:
    source_text = target.read_text(encoding="utf-8") if target.is_file() else ""
    target.write_text(
        source_text.rstrip()
        + "\n\n"
        + "# The generated artifact preserves canonical source text verbatim.\n"
        + "* -whitespace\n"
        + "* conflict-marker-size=64\n",
        encoding="utf-8",
    )


def skill_files(root: Path) -> list[Path]:
    return sorted(root.rglob("SKILL.md"))


def validate_artifact(target: Path) -> None:
    manifest_path = target / ".codex-plugin" / "plugin.json"
    skills_root = target / PUBLIC_SKILL_ROOT
    if not manifest_path.is_file():
        raise ArtifactError(f"artifact missing {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("skills") != "./codex-skills":
        raise ArtifactError("artifact Codex manifest must use skills=./codex-skills")

    files = skill_files(skills_root)
    rel_files = [str(path.relative_to(target)) for path in files]
    expected = [f"codex-skills/{name}/SKILL.md" for name in PUBLIC_SKILLS]
    if rel_files != expected:
        raise ArtifactError(f"artifact public skill set differs; expected={expected}, got={rel_files}")

    for rel_path in REQUIRED_RUNTIME_PATHS:
        if not (target / rel_path).is_file():
            raise ArtifactError(f"artifact missing runtime path: {rel_path}")

    wrapper = target / "codex-skills" / "build-loop" / "SKILL.md"
    canonical = wrapper.parent / ".." / ".." / "skills" / "build-loop" / "SKILL.md"
    if not canonical.resolve().is_file():
        raise ArtifactError("public wrapper cannot resolve the canonical Build Loop workflow")

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
        for rel_path in RUNTIME_FILES:
            copy_file(source / rel_path, tmp / rel_path)
        for name in RUNTIME_DIRS:
            copy_tree(source / name, tmp / name)
        write_artifact_gitattributes(tmp / ".gitattributes")
        write_notice(tmp / "BUILD-ARTIFACT.md")
        # Mirror cited references AFTER the full runtime exists so
        # the pointer scan sees every citing file.
        mirror_references(source, tmp)
        validate_artifact(tmp)

        if target.exists():
            shutil.rmtree(target)
        os.replace(tmp, target)


def iter_files(root: Path) -> Iterable[Path]:
    """Every regular file under ``root`` in sorted order; symlinks never followed.

    Walks explicitly instead of leaning on ``Path.rglob``: rglob's
    symlink-recursion default is version-dependent, and it yields a
    symlink-to-file as a file. Stating the contract here keeps the source scan
    identical to what ``ignore_generated`` lets through on the copy side —
    their disagreement was the ~64MB ``.build`` leak.
    """
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not (base / name).is_symlink()]
        for name in filenames:
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            files.append(path)
    yield from sorted(files)


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
