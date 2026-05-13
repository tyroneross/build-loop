"""PR 1.5 F-criteria tests for migrate_project_memory.py.

Covers F11 (idempotency — re-run yields no changes), F12 (provenance
preservation — created_at from filesystem ctime survives), F13 (manifest
written with all required sections), F14 (decisions store untouched),
F16 (executable rollback — sha256 tree pre-migrate == sha256 tree
post-rollback).

Test isolation: each test creates a synthetic source root + a synthetic
build-loop memory root, both under tmp_path. ``BUILD_LOOP_MEMORY_ROOT``
env var is patched per test so the helpers point at the fixture.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# ---------- helpers ----------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    (path / ".git").mkdir(parents=True, exist_ok=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_shas(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                out[str(p.relative_to(root))] = _sha256(p)
            except (OSError, ValueError):
                continue
    return out


def _seed_source_repo(source_root: Path, slug: str, files: dict[str, str]) -> Path:
    """Create a fake repo at source_root/<slug>/ with .git and .build-loop/memory/<files>."""
    repo = source_root / slug
    repo.mkdir(parents=True, exist_ok=True)
    _init_git_repo(repo)
    mem = repo / ".build-loop" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (mem / name).write_text(body, encoding="utf-8")
    return repo


def _seed_subcomponent_memory(repo: Path, files: dict[str, str]) -> Path:
    sub = repo / "workers" / ".build-loop" / "memory"
    sub.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (sub / name).write_text(body, encoding="utf-8")
    return sub


@pytest.fixture
def fixture_env(monkeypatch, tmp_path):
    """Set up a synthetic source root + memory root; patch the env."""
    source_root = tmp_path / "git-folder"
    source_root.mkdir()
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    (memory_root / "projects").mkdir()

    monkeypatch.setenv("BUILD_LOOP_MEMORY_ROOT", str(memory_root))

    # Force reload modules so env applies
    for mod in (
        "_paths", "project_resolver", "memory_facade",
        "audit_memory_invocation", "migrate_project_memory", "memory_writer",
    ):
        if mod in sys.modules:
            del sys.modules[mod]

    return source_root, memory_root


# ---------- F11 idempotency --------------------------------------------------


def test_f11_apply_twice_is_idempotent(fixture_env):
    """F11 — running --apply twice produces no changes on the second run."""
    source_root, memory_root = fixture_env
    repo = _seed_source_repo(source_root, "alpha", {
        "feedback_x.md": "---\nname: x\n---\nbody x\n",
        "pattern_y.md": "---\nname: y\n---\nbody y\n",
    })
    import migrate_project_memory as mpm  # type: ignore  # noqa: PLC0415

    # First apply
    plan1 = mpm.plan_migration(source_root)
    assert plan1["total_files"] == 2
    backup1 = memory_root / "backup1.tgz"
    mpm.write_tarball_backup(
        [Path(d) for d in plan1["discovered_source_dirs"]]
        + [Path(d) for d in plan1["discovered_subcomponent_dirs"]],
        backup1,
    )
    result1 = mpm.apply_migration(plan1, backup_path=backup1)
    assert len(result1["moved"]) == 2
    assert result1["collisions_refused"] == []

    # Snapshot target tree after first apply
    target_dir = memory_root / "projects" / "alpha"
    sha_after_first = _tree_shas(target_dir)
    assert sha_after_first  # at least the migrated files
    # Stub now exists at the legacy path
    stub_path = repo / ".build-loop" / "memory" / ".MOVED.md"
    assert stub_path.is_file()

    # Second apply — sources are still there (we copy, not move). Discovery
    # finds the same files, but since target sha256 matches source sha256,
    # apply_migration classifies them all as skipped_identical with moved=0.
    # That's the idempotency contract: re-running --apply is a no-op when
    # nothing has diverged.
    plan2 = mpm.plan_migration(source_root)
    backup2 = memory_root / "backup2.tgz"
    # Need a different backup filename — first one still on disk
    mpm.write_tarball_backup(
        [Path(d) for d in plan2["discovered_source_dirs"]]
        + [Path(d) for d in plan2["discovered_subcomponent_dirs"]],
        backup2,
    )
    result2 = mpm.apply_migration(plan2, backup_path=backup2)
    assert len(result2["moved"]) == 0, (
        f"second apply should move 0 files; got {len(result2['moved'])}"
    )
    # All 2 source files match target → skipped_identical
    assert len(result2["skipped_identical"]) == 2

    # Target tree unchanged (sha tree identical to after first apply)
    sha_after_second = _tree_shas(target_dir)
    assert sha_after_first == sha_after_second, "target tree changed on second apply"


# ---------- F12 provenance preservation --------------------------------------


def test_f12_provenance_backfill_applied(fixture_env):
    """F12 — files in target carry provenance frontmatter after migration."""
    source_root, memory_root = fixture_env
    repo = _seed_source_repo(source_root, "beta", {
        "feedback_p.md": "---\nname: p\ndescription: original p\n---\noriginal body\n",
    })
    import migrate_project_memory as mpm  # type: ignore  # noqa: PLC0415

    plan = mpm.plan_migration(source_root)
    backup = memory_root / "backup.tgz"
    mpm.write_tarball_backup(
        [Path(d) for d in plan["discovered_source_dirs"]],
        backup,
    )
    result = mpm.apply_migration(
        plan, backup_path=backup, workdir_for_provenance=repo
    )
    assert len(result["moved"]) == 1

    # Target file now has provenance frontmatter (created_at, source_run_id, etc.)
    target = memory_root / "projects" / "beta" / "feedback_p.md"
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    # Required provenance keys per memory_writer.REQUIRED_PROVENANCE_FIELDS
    for key in ("created_at:", "source_workdir:", "source_run_id:", "last_updated_at:"):
        assert key in content, f"missing provenance key {key} in {target}"


# ---------- F13 manifest written --------------------------------------------


def test_f13_manifest_written_with_required_sections(fixture_env):
    """F13 — apply produces a manifest at _migrations/<date>-consolidation.md with all sections."""
    source_root, memory_root = fixture_env
    _seed_source_repo(source_root, "gamma", {
        "feedback_a.md": "body a\n",
    })
    import migrate_project_memory as mpm  # type: ignore  # noqa: PLC0415

    plan = mpm.plan_migration(source_root)
    backup = memory_root / "backup.tgz"
    mpm.write_tarball_backup(
        [Path(d) for d in plan["discovered_source_dirs"]], backup
    )
    result = mpm.apply_migration(plan, backup_path=backup)
    manifest = memory_root / "_migrations" / "test-manifest.md"
    mpm.write_summary_markdown(result, plan, manifest, backup)

    content = manifest.read_text(encoding="utf-8")
    for section in (
        "Memory consolidation migration —",
        "Tarball backup:",
        "Rollback command:",
        "## Moved",
        "## Skipped (identical content)",
        "## Collisions refused",
        "## Empty source dirs",
        "## Stubs written",
        "## Provenance backfill",
        "## Errors",
        "## Postgres slug reconciliation",
    ):
        assert section in content, f"missing section {section!r} in manifest"


# ---------- F14 decisions store untouched -----------------------------------


def test_f14_decisions_store_untouched(fixture_env, tmp_path, monkeypatch):
    """F14 — running the migration does NOT modify the canonical decisions store."""
    source_root, memory_root = fixture_env
    # Set up a synthetic decisions store separate from the memory root
    decisions_root = tmp_path / "decisions"
    (decisions_root / "_unscoped").mkdir(parents=True)
    (decisions_root / "build-loop").mkdir(parents=True)
    (decisions_root / "_unscoped" / "0001-2026-05-12-some-decision.md").write_text(
        "---\nid: 0001\n---\nbody\n", encoding="utf-8"
    )
    (decisions_root / "build-loop" / "0002-2026-05-12-other.md").write_text(
        "---\nid: 0002\n---\nbody2\n", encoding="utf-8"
    )
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path))

    # Snapshot pre-migration
    pre = _tree_shas(decisions_root)
    assert pre  # not empty

    _seed_source_repo(source_root, "delta", {"feedback_x.md": "body\n"})
    import migrate_project_memory as mpm  # type: ignore  # noqa: PLC0415

    plan = mpm.plan_migration(source_root)
    backup = memory_root / "backup.tgz"
    mpm.write_tarball_backup(
        [Path(d) for d in plan["discovered_source_dirs"]], backup
    )
    mpm.apply_migration(plan, backup_path=backup)

    # Decisions store unchanged
    post = _tree_shas(decisions_root)
    assert pre == post, f"decisions store changed: {set(pre) ^ set(post)}"


# ---------- F16 rollback executable ----------------------------------------


def test_f16_rollback_restores_tree(fixture_env):
    """F16 — apply followed by rollback yields sha256-identical source tree."""
    source_root, memory_root = fixture_env
    repo = _seed_source_repo(source_root, "epsilon", {
        "feedback_x.md": "---\nname: x\n---\nbody x\n",
        "pattern_y.md": "---\nname: y\n---\nbody y\n",
    })
    sub_dir = _seed_subcomponent_memory(repo, {
        "pattern_w.md": "---\nname: w\n---\nworkers body\n",
    })
    import migrate_project_memory as mpm  # type: ignore  # noqa: PLC0415

    # Pre-migration snapshot
    pre_source_shas = _tree_shas(repo / ".build-loop" / "memory")
    pre_sub_shas = _tree_shas(sub_dir)

    plan = mpm.plan_migration(source_root)
    backup = memory_root / "backup.tgz"
    mpm.write_tarball_backup(
        [Path(d) for d in plan["discovered_source_dirs"]]
        + [Path(d) for d in plan["discovered_subcomponent_dirs"]],
        backup,
    )
    result = mpm.apply_migration(plan, backup_path=backup)
    manifest = memory_root / "_migrations" / "test-rollback.md"
    mpm.write_summary_markdown(result, plan, manifest, backup)

    # Verify migration landed
    assert (memory_root / "projects" / "epsilon" / "feedback_x.md").is_file()
    assert (repo / ".build-loop" / "memory" / ".MOVED.md").is_file()

    # Roll back
    rb = mpm.rollback(manifest)
    assert not rb["errors"], f"rollback errors: {rb['errors']}"

    # Post-rollback: legacy source dirs match pre-migration sha256s
    # (except the .MOVED.md stub which was removed by rollback)
    post_source = _tree_shas(repo / ".build-loop" / "memory")
    post_sub = _tree_shas(sub_dir)
    # Remove the .MOVED.md stub from the post-rollback view if present
    post_source.pop(".MOVED.md", None)
    assert post_source == pre_source_shas, (
        f"source legacy tree differs after rollback: pre={pre_source_shas} post={post_source}"
    )
    assert post_sub == pre_sub_shas, "subcomponent tree differs after rollback"

    # Target tree is empty
    target = memory_root / "projects" / "epsilon"
    # Target dir may or may not exist; what matters is no migrated files remain
    if target.is_dir():
        remaining = [p for p in target.glob("*.md")]
        assert not remaining, f"migrated files still in target after rollback: {remaining}"


# ---------- Smoke: --check exit code ----------------------------------------


def test_check_mode_exit_code_no_collisions(fixture_env):
    """--check on a fresh fixture exits 0 (no collisions)."""
    source_root, _ = fixture_env
    _seed_source_repo(source_root, "zeta", {"feedback_a.md": "body\n"})
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "migrate_project_memory.py"),
         "--check", "--source-root", str(source_root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--check failed: {result.stderr}"
    assert "total files to migrate: 1" in result.stdout


def test_check_mode_collision_exit_code(fixture_env):
    """--check with a content collision exits 2 (operator must --force-overwrite)."""
    source_root, memory_root = fixture_env
    # Source has one file
    _seed_source_repo(source_root, "eta", {"feedback_a.md": "source body\n"})
    # Target already has different content
    target_dir = memory_root / "projects" / "eta"
    target_dir.mkdir(parents=True)
    (target_dir / "feedback_a.md").write_text("DIFFERENT body\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "migrate_project_memory.py"),
         "--check", "--source-root", str(source_root)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BUILD_LOOP_MEMORY_ROOT": str(memory_root)},
    )
    assert result.returncode == 2, (
        f"expected exit 2 on collision; got {result.returncode}\nstdout: {result.stdout}"
    )
