"""PR 1 F-criteria tests for memory consolidation read-path tolerance.

Covers F1 (projects dir exists post-bootstrap), F4 parameterized
(derive_slug_from_cwd cases), F5 (unregistered repo), F8
(probe_project_memory graceful_degradation), F10 (audit_memory probes
return only ok|graceful_degradation), F17 (memory_facade.recall merges
global + project + legacy_project with project winning).

Test isolation: each test uses ``tmp_path`` (pytest fixture) and sets the
``BUILD_LOOP_MEMORY_ROOT`` env var so the helpers point at the fixture
tree, not the operator's real ``~/.build-loop/memory/``.
"""
from __future__ import annotations

import importlib
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
    """Create a minimal ``.git/`` so ``derive_slug_from_cwd`` recognizes the dir."""
    (path / ".git").mkdir(parents=True, exist_ok=True)
    # An empty file inside .git makes it look like a real repo to .exists() walks.
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


def _seed_memory_root(root: Path, *, with_projects: bool = True) -> None:
    """Create a fixture memory root: constitution + MEMORY.md + optional projects/."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "constitution.md").write_text("# Constitution (fixture)\n", encoding="utf-8")
    (root / "MEMORY.md").write_text("# MEMORY (fixture)\n", encoding="utf-8")
    if with_projects:
        (root / "projects").mkdir(exist_ok=True)


@pytest.fixture
def patched_env(monkeypatch, tmp_path):
    """Point BUILD_LOOP_MEMORY_ROOT at a tmp fixture and reload modules."""
    mem_root = tmp_path / "memory"
    _seed_memory_root(mem_root)
    monkeypatch.setenv("BUILD_LOOP_MEMORY_ROOT", str(mem_root))
    # Force-reload modules so the new env var takes effect on subsequent
    # imports of _paths / project_resolver / memory_facade.
    for mod in ("_paths", "project_resolver", "memory_facade", "audit_memory_invocation"):
        if mod in sys.modules:
            del sys.modules[mod]
    return mem_root


# ---------- F1 ---------------------------------------------------------------


def test_f1_install_memory_creates_projects_dir(tmp_path):
    """F1 — install_memory.py bootstrap creates the projects/ subtree."""
    dest = tmp_path / "memory"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "install_memory.py"), "--dest", str(dest)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"install_memory failed: {result.stderr}"
    assert (dest / "projects").is_dir(), "projects/ subdir not created"
    assert (dest / "projects" / "README.md").is_file(), "projects/README.md not seeded"
    assert (dest / "constitution.md").is_file()
    assert (dest / "MEMORY.md").is_file()


# ---------- F4 parameterized -------------------------------------------------


def test_f4_slug_from_basic_repo(patched_env, tmp_path):
    """F4a — straightforward repo: slug = basename(repo_root)."""
    from _paths import derive_slug_from_cwd  # type: ignore

    repo = tmp_path / "MyProject"
    repo.mkdir()
    _init_git_repo(repo)
    assert derive_slug_from_cwd(repo) == "myproject"


def test_f4_slug_resolves_symlink(patched_env, tmp_path):
    """F4b — cwd is a symlink to the real repo; slug uses real-path basename."""
    from _paths import derive_slug_from_cwd  # type: ignore

    real = tmp_path / "real-repo"
    real.mkdir()
    _init_git_repo(real)
    link = tmp_path / "linked-name"
    link.symlink_to(real)
    # derived from the resolved path basename, not the symlink name
    assert derive_slug_from_cwd(link) == "real-repo"


def test_f4_slug_no_git_returns_unscoped(patched_env, tmp_path):
    """F4c — no .git ancestor anywhere → _unscoped (strict; does NOT raise)."""
    from _paths import derive_slug_from_cwd  # type: ignore

    # tmp_path on macOS resolves to /private/var/folders/... which is outside
    # any git repo. Strict assertion: the no-.git fallback returns exactly
    # the _unscoped sentinel, never a stray slug from a parent walk.
    nowhere = tmp_path / "no-git-here"
    nowhere.mkdir()
    assert derive_slug_from_cwd(nowhere) == "_unscoped"


def test_f4_slug_workers_subcomponent(patched_env, tmp_path):
    """F4d — cwd under repo/workers/... appends /workers to slug."""
    from _paths import derive_slug_from_cwd  # type: ignore

    repo = tmp_path / "ddc"
    repo.mkdir()
    _init_git_repo(repo)
    workers = repo / "workers"
    workers.mkdir()
    assert derive_slug_from_cwd(workers) == "ddc/workers"
    # nested deeper still resolves to ddc/workers
    nested = workers / "src" / "deep"
    nested.mkdir(parents=True)
    assert derive_slug_from_cwd(nested) == "ddc/workers"


def test_f4_slug_worktree_collapses_to_canonical(patched_env, tmp_path):
    """F4e — a `git worktree` of a repo shares the MAIN checkout's slug.

    Regression for bl-memory-slug-worktree-fragmentation: a worktree's `.git`
    is a file pointing at the canonical repo's gitdir, so a basename-only slug
    gave every worktree (e.g. an `isolation: "worktree"` dispatch) its own split
    memory project. The fix follows `git rev-parse --git-common-dir`.
    """
    import subprocess

    from _paths import derive_slug_from_cwd  # type: ignore

    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-qm", "init"],
        cwd=repo, check=True,
    )
    wt = tmp_path / "wt-feature"
    subprocess.run(["git", "worktree", "add", "-q", str(wt)], cwd=repo, check=True)

    main_slug = derive_slug_from_cwd(repo)
    wt_slug = derive_slug_from_cwd(wt)
    assert main_slug == "myrepo"
    assert wt_slug == main_slug  # collapses — not "wt-feature"


def test_f4_slug_normalizes_uppercase_and_chars(patched_env, tmp_path):
    """F4e — uppercase and unsafe chars normalize; matches _safe_project_tag."""
    from _paths import derive_slug_from_cwd  # type: ignore

    repo = tmp_path / "Build-Loop"  # mixed case
    repo.mkdir()
    _init_git_repo(repo)
    assert derive_slug_from_cwd(repo) == "build-loop"


# ---------- F5 ---------------------------------------------------------------


def test_f5_unregistered_repo_resolves_via_filesystem(patched_env, tmp_path):
    """F5 — repo without a projects.yaml entry still gets a slug from the FS rule."""
    from project_resolver import resolve_project  # type: ignore

    repo = tmp_path / "brand-new-app"
    repo.mkdir()
    _init_git_repo(repo)
    # Even though projects.yaml has no entry for this path, the filesystem
    # rule should return the basename slug.
    assert resolve_project(repo) == "brand-new-app"


# ---------- F8 ---------------------------------------------------------------


def test_f8_probe_project_memory_graceful_degradation_both_missing(patched_env, tmp_path):
    """F8a — both legacy and new paths missing → graceful_degradation, never error."""
    from audit_memory_invocation import probe_project_memory  # type: ignore

    repo = tmp_path / "no-memory-yet"
    repo.mkdir()
    _init_git_repo(repo)
    result = probe_project_memory(repo)
    assert result["invoked"] is True
    assert result["verdict"] == "graceful_degradation"
    assert "error" in result  # diagnostic string present


def test_f8_probe_project_memory_legacy_path_ignored_post_pr3(patched_env, tmp_path):
    """F8b (post-PR-3) — legacy path content is IGNORED.

    PR 3 removed the read shim. The probe no longer checks legacy paths;
    a project with content only at the legacy location returns
    graceful_degradation. Operators with such content should run the
    migration script.
    """
    from audit_memory_invocation import probe_project_memory  # type: ignore

    repo = tmp_path / "legacy-only"
    repo.mkdir()
    _init_git_repo(repo)
    legacy = repo / ".build-loop" / "memory"
    legacy.mkdir(parents=True)
    (legacy / "MEMORY.md").write_text("# Legacy memory\n", encoding="utf-8")
    result = probe_project_memory(repo)
    # Legacy content is invisible post-PR-3
    assert result["verdict"] == "graceful_degradation"
    assert "no project MEMORY.md" in result.get("error", "")


def test_f8_probe_project_memory_new_path_only(patched_env, tmp_path):
    """F8c — new path populated, legacy missing → ok with scope=project."""
    from _paths import project_memory_dir_for_project  # type: ignore
    from audit_memory_invocation import probe_project_memory  # type: ignore

    repo = tmp_path / "new-app"
    repo.mkdir()
    _init_git_repo(repo)
    new_dir = project_memory_dir_for_project("new-app")
    new_dir.mkdir(parents=True)
    (new_dir / "MEMORY.md").write_text("# Project memory\n", encoding="utf-8")
    result = probe_project_memory(repo)
    assert result["verdict"] == "ok"
    assert result["result_sample"]["scope"] == "project"


# ---------- F10 --------------------------------------------------------------


def test_f10_audit_memory_no_error_verdicts(patched_env, tmp_path):
    """F10 — full audit_memory probes yield no `error` verdicts on a fixture workdir."""
    workdir = tmp_path / "audit-fixture"
    workdir.mkdir()
    _init_git_repo(workdir)
    # Exercise each probe directly and assert the verdict contract.
    from audit_memory_invocation import (
        probe_global_memory,
        probe_project_memory,
        probe_runs_tail,
        probe_recall_facade,
        probe_decision_canonical,
    )  # type: ignore

    for probe in (
        probe_global_memory,
        probe_project_memory,
        probe_runs_tail,
        probe_recall_facade,
        probe_decision_canonical,
    ):
        result = probe(workdir)
        assert result["verdict"] in {"ok", "graceful_degradation"}, (
            f"{probe.__name__} returned unexpected verdict {result['verdict']!r}"
        )


# ---------- F17 --------------------------------------------------------------


def test_f17_recall_merges_global_and_project(patched_env, tmp_path):
    """F17 — recall() reads global + project; project wins on collision.

    PR 3 (2026-05-13): legacy_project tier is REMOVED. recall() merges only
    global + project. Files at the legacy per-repo location are invisible.
    """
    from _paths import project_memory_dir_for_project  # type: ignore
    from memory_facade import recall  # type: ignore

    repo = tmp_path / "myapp"
    repo.mkdir()
    _init_git_repo(repo)

    mem_root = Path(os.environ["BUILD_LOOP_MEMORY_ROOT"])

    # Global tier: feedback_global.md (unique to global)
    (mem_root / "feedback_global.md").write_text(
        "---\nname: feedback-global\ndescription: only at global\n---\n# Global lesson\n",
        encoding="utf-8",
    )
    # Same filename in global AND project to test override
    (mem_root / "feedback_shared.md").write_text(
        "---\nname: feedback-shared\ndescription: GLOBAL VERSION\n---\nglobal body\n",
        encoding="utf-8",
    )

    # Project tier: same filename + a unique one
    project_dir = project_memory_dir_for_project("myapp")
    project_dir.mkdir(parents=True)
    (project_dir / "feedback_shared.md").write_text(
        "---\nname: feedback-shared\ndescription: PROJECT VERSION\n---\nproject body\n",
        encoding="utf-8",
    )
    (project_dir / "feedback_project.md").write_text(
        "---\nname: feedback-project\ndescription: only at project\n---\nproject only\n",
        encoding="utf-8",
    )

    # Legacy path with a unique file — should be IGNORED post-PR-3
    legacy = repo / ".build-loop" / "memory"
    legacy.mkdir(parents=True)
    (legacy / "feedback_legacy.md").write_text(
        "---\nname: feedback-legacy\ndescription: invisible post-PR-3\n---\nlegacy body\n",
        encoding="utf-8",
    )

    env = recall(query="", kind="lessons", workdir=repo, limit=50)
    lessons = env["results_by_kind"]["lessons"]

    by_name = {entry["name"]: entry for entry in lessons}

    # Three names — legacy file is invisible
    assert "feedback_global.md" in by_name, f"missing global; got {list(by_name)}"
    assert "feedback_project.md" in by_name
    assert "feedback_shared.md" in by_name
    assert "feedback_legacy.md" not in by_name, (
        f"legacy_project tier should be unread post-PR-3; got {list(by_name)}"
    )

    # Override: project version of feedback_shared.md WINS over global
    shared = by_name["feedback_shared.md"]
    assert shared["_scope"] == "project", (
        f"expected project to win on feedback_shared.md, got _scope={shared['_scope']!r}"
    )

    # Scopes on the others
    assert by_name["feedback_global.md"]["_scope"] == "global"
    assert by_name["feedback_project.md"]["_scope"] == "project"


def test_f17b_legacy_path_invisible_post_pr3(patched_env, tmp_path):
    """F17b (post-PR-3) — content at the legacy per-repo path is invisible.

    Previously (PR 1/2 transition): legacy_project tier was read but
    project-tier entries won on filename collision. PR 3 removed the
    legacy read shim entirely. Re-purposed test now asserts that a
    file present ONLY at the legacy location does NOT appear in recall.
    Operators with such content must migrate it via
    scripts/migrate_project_memory.py.
    """
    from memory_facade import recall  # type: ignore

    repo = tmp_path / "myapp"
    repo.mkdir()
    _init_git_repo(repo)

    legacy = repo / ".build-loop" / "memory"
    legacy.mkdir(parents=True)
    (legacy / "feedback_orphan.md").write_text(
        "---\nname: feedback-orphan\ndescription: should be invisible\n---\norphan body\n",
        encoding="utf-8",
    )

    env = recall(query="", kind="lessons", workdir=repo, limit=50)
    lessons = env["results_by_kind"]["lessons"]
    names = {l["name"] for l in lessons}
    assert "feedback_orphan.md" not in names, (
        f"legacy-only file should be invisible post-PR-3; got names={names}"
    )
