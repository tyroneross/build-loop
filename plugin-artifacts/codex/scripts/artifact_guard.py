#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""artifact_guard.py — keep checked-in generated artifacts in sync at commit time.

Named, observed failure this control earns its place against: checked-in
artifacts silently drift from source because regeneration is a manual step and
only a CI gate (not the commit flow) catches it. On 2026-06-27/28 this reddened
main three times in one session — the architecture diagram twice
(``scripts/architecture_diagram/generate.py --check``) and the Codex plugin
artifact once (``scripts/build_codex_plugin_artifact.py --check``), each needing
a manual follow-up regen commit.

Design (systems-not-discipline, DRY)
------------------------------------
ONE engine over a registry of artifacts. Each artifact declares its watched
source prefixes, a ``--check`` command (fresh → exit 0; stale/missing →
non-zero), a regen command, and the output paths to re-stage. Adding a future
checked-in generated file is a one-entry edit to ``ARTIFACTS`` below.

Modes
-----
``--staged`` (pre-commit): for each artifact whose watched paths intersect the
  staged set, run its check; on drift, REGENERATE and ``git add`` the outputs so
  the commit ships fresh. Unrelated commits touch nothing. ``BL_ARTIFACT_ADVISORY=1``
  downgrades to a warning (no regen, no block). Blocks (exit 1) only when regen
  itself fails — the message carries the exact manual command.
``--check`` / ``--all`` (CI / manual): run every artifact's check unconditionally,
  print the exact regen command on drift, exit 1 if any stale. Read-only.
``--regen [NAME|all]``: run regen commands.
``--list`` / ``--json``: introspect the registry.
``--install-hook`` / ``--uninstall-hook`` / ``--hook-status``: install (chained,
  coexisting with other pre-commit segments) the local guard.

Exit codes: 0 ok · 1 drift unfixable / regen failed · 2 usage/registry error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Artifact:
    name: str
    # Watched source prefixes. An entry ending in "/" matches any path under
    # that directory; otherwise it is an exact repo-relative file path.
    watch: tuple[str, ...]
    # argv suffix (after the python interpreter) for the freshness check.
    check_argv: tuple[str, ...]
    # argv suffix (after the python interpreter) to regenerate the artifact.
    regen_argv: tuple[str, ...]
    # Repo-relative output paths to ``git add`` after a regen (files or dirs).
    outputs: tuple[str, ...]
    why: str = ""


ARTIFACTS: tuple[Artifact, ...] = (
    Artifact(
        name="architecture-diagram",
        watch=("agents/", "skills/", "scripts/", "architecture/",
               "hooks/hooks.json", "docs/build-loop-flow-mockup.html"),
        check_argv=("scripts/architecture_diagram/generate.py", "--check"),
        regen_argv=("scripts/architecture_diagram/generate.py",),
        outputs=("architecture/model.json",
                 "architecture/ARCHITECTURE.md",
                 "docs/build-loop-flow-mockup.html"),
        why="auto-discovered components + authored flow → model.json + mockup",
    ),
    Artifact(
        name="codex-plugin-artifact",
        watch=("skills/", "references/", "AGENTS.md", "README.md", "LICENSE"),
        check_argv=("scripts/build_codex_plugin_artifact.py", "--check"),
        regen_argv=("scripts/build_codex_plugin_artifact.py",),
        outputs=("plugin-artifacts/codex",),
        why="slim Codex bundle mirrored from the full skill tree + references",
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_repo(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    here = Path(__file__).resolve().parent
    for parent in [here] + list(here.parents):
        if (parent / ".git").exists():
            return parent
    return Path.cwd().resolve()


def _git(repo: Path, *args: str,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, env=env)


def _clean_git_env() -> dict[str, str]:
    """Environment with git's hook-injected location vars removed. During a
    ``git commit`` the pre-commit hook inherits ``GIT_DIR`` / ``GIT_INDEX_FILE``
    / ``GIT_WORK_TREE`` etc. pointing at the committing repo; a git subprocess
    that inherits them binds to THAT index instead of the throwaway worktree we
    point it at. Stripping every ``GIT_*`` var makes ``git -C <path>`` resolve
    from the path, which is what isolation needs."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _staged_files(repo: Path) -> list[str]:
    cp = _git(repo, "diff", "--cached", "--name-only")
    if cp.returncode != 0:
        return []
    return [ln for ln in cp.stdout.splitlines() if ln.strip()]


def _matches(artifact: Artifact, staged: list[str]) -> bool:
    for path in staged:
        for entry in artifact.watch:
            if entry.endswith("/"):
                if path == entry.rstrip("/") or path.startswith(entry):
                    return True
            elif path == entry:
                return True
    return False


def _script_present(repo: Path, artifact: Artifact) -> bool:
    """Whether this artifact's generator/check script exists in the repo.

    Lets the guard self-disable for artifacts whose tooling isn't present
    (e.g. the chained hook landing in a repo that lacks generate.py), instead
    of misreading a "script not found" subprocess error as drift.
    """
    return (repo / artifact.check_argv[0]).exists()


def _run_check(repo: Path, artifact: Artifact) -> tuple[bool, str]:
    """Return (fresh, detail). fresh=True when the artifact is up to date."""
    cp = subprocess.run([sys.executable, *artifact.check_argv],
                        cwd=str(repo), capture_output=True, text=True)
    detail = (cp.stdout + cp.stderr).strip()
    return cp.returncode == 0, detail


def _unstaged_files(repo: Path) -> list[str]:
    """Files with UNSTAGED modifications (working tree vs index)."""
    cp = _git(repo, "diff", "--name-only")
    if cp.returncode != 0:
        return []
    return [ln for ln in cp.stdout.splitlines() if ln.strip()]


def _copy_outputs(src_root: Path, dst_root: Path, outputs: tuple[str, ...]) -> None:
    for rel in outputs:
        src, dst = src_root / rel, dst_root / rel
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        elif src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _regen_isolated(repo: Path, artifact: Artifact) -> tuple[bool, str] | None:
    """Regenerate against the STAGED INDEX in a throwaway worktree, then copy
    outputs back into ``repo``. Returns (ok, detail), or None when isolation
    could not be set up (caller falls back). The isolated tree is exactly what
    is being committed (HEAD + staged changes) with NO unstaged edits, so a
    generator that reads whole files (the Codex mirror) can never bundle another
    agent's uncommitted work into this commit."""
    # write-tree inherits git's env so it captures the EXACT index being
    # committed (git may point GIT_INDEX_FILE at a temp index for a partial
    # commit). The result is a content-addressed SHA, portable to the worktree.
    tree = _git(repo, "write-tree")
    if tree.returncode != 0 or not tree.stdout.strip():
        return None
    tree_sha = tree.stdout.strip()
    # Everything below must bind to the throwaway worktree, not the committing
    # repo — run it with git's hook-injected location vars stripped.
    env = _clean_git_env()
    tmp = Path(tempfile.mkdtemp(prefix="artifact-guard-"))
    wt = tmp / "wt"
    try:
        if _git(repo, "worktree", "add", "--detach", "-q", str(wt), "HEAD",
                env=env).returncode != 0:
            return None
        try:
            if (_git(wt, "read-tree", tree_sha, env=env).returncode != 0
                    or _git(wt, "checkout-index", "-a", "-f", env=env).returncode != 0):
                return None
            cp = subprocess.run([sys.executable, *artifact.regen_argv],
                                cwd=str(wt), capture_output=True, text=True, env=env)
            detail = (cp.stdout + cp.stderr).strip()
            if cp.returncode != 0:
                return (False, detail)
            # Verify freshness in the SAME staged-index frame the regen used —
            # NOT against the live working tree (which may carry unstaged edits
            # that are not being committed and would falsely read as drift).
            chk = subprocess.run([sys.executable, *artifact.check_argv],
                                 cwd=str(wt), capture_output=True, text=True, env=env)
            if chk.returncode != 0:
                return (False, "regen ran but the artifact is still stale vs the "
                               "staged sources: " + (chk.stdout + chk.stderr).strip())
            _copy_outputs(wt, repo, artifact.outputs)
            return (True, detail)
        finally:
            _git(repo, "worktree", "remove", "--force", str(wt), env=env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_regen(repo: Path, artifact: Artifact) -> tuple[bool, str]:
    """Regenerate ``artifact``, leaving its outputs in ``repo`` ready to stage.

    Sources from the staged index in isolation (never the live working tree) so
    a committer can't bundle another agent's uncommitted edits into a derived
    artifact — the concurrent-checkout leak class. If isolation is unavailable,
    fall back to in-place regen ONLY when no unstaged change touches this
    artifact's watched paths; otherwise fail closed with a clear message rather
    than risk absorbing foreign uncommitted content."""
    isolated = _regen_isolated(repo, artifact)
    if isolated is not None:
        return isolated
    if _matches(artifact, _unstaged_files(repo)):
        return (False,
                "regen isolation unavailable AND unstaged changes touch this "
                "artifact's sources — refusing to regenerate from the working "
                "tree (would risk bundling uncommitted third-party content). "
                "Stage or stash those changes, or regenerate manually.")
    # Safe in-place fallback: no unstaged change touches this artifact's sources,
    # so the working tree == the staged index for what matters here.
    cp = subprocess.run([sys.executable, *artifact.regen_argv],
                        cwd=str(repo), capture_output=True, text=True)
    detail = (cp.stdout + cp.stderr).strip()
    if cp.returncode != 0:
        return (False, detail)
    chk = subprocess.run([sys.executable, *artifact.check_argv],
                         cwd=str(repo), capture_output=True, text=True)
    if chk.returncode != 0:
        return (False, "regen ran but the artifact is still stale: "
                       + (chk.stdout + chk.stderr).strip())
    return (True, detail)


def _regen_cmd_str(artifact: Artifact) -> str:
    return "python3 " + " ".join(artifact.regen_argv)


def _git_add(repo: Path, outputs: tuple[str, ...]) -> list[str]:
    added: list[str] = []
    for rel in outputs:
        if (repo / rel).exists():
            cp = _git(repo, "add", "--", rel)
            if cp.returncode == 0:
                added.append(rel)
    return added


def _advisory() -> bool:
    return os.environ.get("BL_ARTIFACT_ADVISORY", "0") == "1"


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def mode_staged(repo: Path) -> int:
    """Pre-commit: regenerate + restage drifted artifacts whose sources staged."""
    staged = _staged_files(repo)
    if not staged:
        return 0
    advisory = _advisory()
    failed = False
    for artifact in ARTIFACTS:
        if not _matches(artifact, staged):
            continue
        if not _script_present(repo, artifact):
            continue
        fresh, _ = _run_check(repo, artifact)
        if fresh:
            continue
        if advisory:
            sys.stderr.write(
                f"⚠ {artifact.name} is stale (advisory mode — not regenerating). "
                f"Run: {_regen_cmd_str(artifact)}\n")
            continue
        ok, detail = _run_regen(repo, artifact)
        if not ok:
            failed = True
            sys.stderr.write(
                f"✖ {artifact.name} drifted and could not be regenerated.\n"
                f"  Run manually: {_regen_cmd_str(artifact)}\n"
                f"  (or set BL_ARTIFACT_ADVISORY=1 to commit without regenerating, "
                f"then fix before CI)\n")
            if detail:
                sys.stderr.write("  " + detail.replace("\n", "\n  ") + "\n")
            continue
        # _run_regen already verified freshness in the reference frame it used
        # (the staged index under isolation, or the working tree in the safe
        # fallback) — a working-tree re-check here would falsely flag drift when
        # unstaged edits exist, so trust the verified result and restage.
        added = _git_add(repo, artifact.outputs)
        sys.stderr.write(
            f"↻ {artifact.name}: regenerated and re-staged "
            f"{', '.join(added) if added else '(no output changes)'}\n")
    return 1 if failed else 0


def mode_check(repo: Path, *, as_json: bool) -> int:
    """CI / manual: read-only freshness gate over all artifacts."""
    results: list[dict[str, Any]] = []
    stale_any = False
    for artifact in ARTIFACTS:
        if not _script_present(repo, artifact):
            results.append({
                "name": artifact.name, "fresh": True, "available": False,
                "regen_command": _regen_cmd_str(artifact), "detail": "",
            })
            continue
        fresh, detail = _run_check(repo, artifact)
        if not fresh:
            stale_any = True
        results.append({
            "name": artifact.name,
            "fresh": fresh,
            "available": True,
            "regen_command": _regen_cmd_str(artifact),
            "detail": detail if not fresh else "",
        })
    if as_json:
        sys.stdout.write(json.dumps({"stale": stale_any, "artifacts": results},
                                    indent=2, sort_keys=True) + "\n")
    else:
        for r in results:
            if r["fresh"]:
                sys.stdout.write(f"✓ {r['name']} fresh\n")
            else:
                sys.stderr.write(
                    f"✖ {r['name']} STALE — run: {r['regen_command']}\n")
                if r["detail"]:
                    sys.stderr.write("  " + r["detail"].replace("\n", "\n  ") + "\n")
    return 1 if stale_any else 0


def mode_regen(repo: Path, which: str) -> int:
    failed = False
    for artifact in ARTIFACTS:
        if which not in ("all", artifact.name):
            continue
        if not _script_present(repo, artifact):
            sys.stderr.write(f"· {artifact.name} skipped (generator not present)\n")
            continue
        ok, detail = _run_regen(repo, artifact)
        if ok:
            sys.stdout.write(f"↻ {artifact.name} regenerated\n")
        else:
            failed = True
            sys.stderr.write(f"✖ {artifact.name} regen failed: {detail}\n")
    return 1 if failed else 0


def mode_list(repo: Path, *, as_json: bool) -> int:
    data = [{
        "name": a.name, "watch": list(a.watch),
        "check": "python3 " + " ".join(a.check_argv),
        "regen": _regen_cmd_str(a), "outputs": list(a.outputs), "why": a.why,
    } for a in ARTIFACTS]
    if as_json:
        sys.stdout.write(json.dumps({"artifacts": data}, indent=2) + "\n")
    else:
        for d in data:
            sys.stdout.write(f"{d['name']}\n  watch:  {', '.join(d['watch'])}\n"
                             f"  regen:  {d['regen']}\n  why:    {d['why']}\n")
    return 0


# ---------------------------------------------------------------------------
# Pre-commit hook installer (chained segment; coexists with other segments)
# ---------------------------------------------------------------------------

_MARKER = "# --- BEGIN build-loop artifact-guard pre-commit ---"
_MARKER_END = "# --- END build-loop artifact-guard pre-commit ---"
_SEGMENT_RE = re.compile(re.escape(_MARKER) + r".*?" + re.escape(_MARKER_END) + r"\n?",
                         re.DOTALL)
_SOURCE_HOOK = ("hooks", "git", "pre-commit")


def _hooks_dir(repo: Path) -> Path | None:
    cp = _git(repo, "rev-parse", "--git-path", "hooks")
    if cp.returncode != 0:
        return None
    p = Path(cp.stdout.strip())
    if not p.is_absolute():
        p = (repo / p).resolve()
    return p


def _source_segment(repo: Path) -> str | None:
    """Extract the marked segment from the committed source hook (DRY source)."""
    src = repo.joinpath(*_SOURCE_HOOK)
    if not src.exists():
        return None
    m = _SEGMENT_RE.search(src.read_text(encoding="utf-8"))
    return m.group(0).rstrip("\n") + "\n" if m else None


def _make_exec(path: Path) -> None:
    import stat
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_hook(repo: Path) -> dict[str, Any]:
    hooks_dir = _hooks_dir(repo)
    if hooks_dir is None:
        return {"installed": False, "reason": "not a git repo / no hooks dir"}
    src = repo.joinpath(*_SOURCE_HOOK)
    segment = _source_segment(repo)
    if segment is None:
        return {"installed": False, "reason": f"source segment not found in {src}"}
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-commit"
    fresh = not hook.exists()
    # Fresh base is a bare shebang — NOT the standalone source's trailing
    # ``exit 0``. Emitting a trailing ``exit 0`` would turn any later-APPENDED
    # segment (the rally-point private-slug guard appends) into dead code,
    # silently disabling it. The chainable hook therefore carries no trailing
    # ``exit 0``; each segment exits non-zero only on its own failure and the
    # hook's success status is the last segment's 0. (The standalone
    # hooks/git/pre-commit keeps ``exit 0`` for manual single-segment use; the
    # installer only ever extracts the marked segment, never that line.)
    body = "#!/bin/sh\n" if fresh else hook.read_text(encoding="utf-8")
    if not fresh and _MARKER in body:
        # Idempotent re-sync: replace our segment in place.
        new = _SEGMENT_RE.sub(segment, body)
        if new != body:
            hook.write_text(new, encoding="utf-8")
        _make_exec(hook)
        return {"installed": True, "action": "resynced", "path": str(hook)}
    # Insert our segment right after the shebang so it runs before any
    # later-appended segment AND before a trailing ``exit 0`` left by an
    # earlier installer (order-independent with the rally-point segment,
    # whichever installs first).
    lines = body.splitlines(keepends=True)
    at = 1 if lines and lines[0].startswith("#!") else 0
    new = "".join(lines[:at] + ["\n", segment] + lines[at:])
    hook.write_text(new, encoding="utf-8")
    _make_exec(hook)
    return {"installed": True, "action": "created" if fresh else "chained",
            "path": str(hook)}


def uninstall_hook(repo: Path) -> dict[str, Any]:
    hooks_dir = _hooks_dir(repo)
    if hooks_dir is None:
        return {"removed": False, "reason": "not a git repo"}
    hook = hooks_dir / "pre-commit"
    if not hook.exists():
        return {"removed": False, "reason": "no pre-commit hook"}
    body = hook.read_text(encoding="utf-8")
    if _MARKER not in body:
        return {"removed": False, "reason": "artifact-guard segment not present"}
    hook.write_text(_SEGMENT_RE.sub("", body), encoding="utf-8")
    return {"removed": True, "path": str(hook)}


def hook_status(repo: Path) -> dict[str, Any]:
    hooks_dir = _hooks_dir(repo)
    if hooks_dir is None:
        return {"installed": False, "reason": "not a git repo"}
    hook = hooks_dir / "pre-commit"
    present = hook.exists() and _MARKER in hook.read_text(encoding="utf-8")
    return {"installed": present, "path": str(hook)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=None, help="Repo root (default: detect).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true",
                       help="Pre-commit mode: regen+restage drifted artifacts.")
    group.add_argument("--check", "--all", dest="check", action="store_true",
                       help="Read-only freshness gate over all artifacts (CI).")
    group.add_argument("--regen", metavar="NAME", nargs="?", const="all",
                       help="Regenerate NAME (or all).")
    group.add_argument("--list", action="store_true", help="List the registry.")
    group.add_argument("--install-hook", action="store_true")
    group.add_argument("--uninstall-hook", action="store_true")
    group.add_argument("--hook-status", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo = _detect_repo(args.repo)

    if args.staged:
        return mode_staged(repo)
    if args.check:
        return mode_check(repo, as_json=args.json)
    if args.regen is not None:
        return mode_regen(repo, args.regen)
    if args.list:
        return mode_list(repo, as_json=args.json)
    if args.install_hook:
        r = install_hook(repo)
        sys.stdout.write(json.dumps(r, sort_keys=True) + "\n")
        return 0 if r.get("installed") else 1
    if args.uninstall_hook:
        r = uninstall_hook(repo)
        sys.stdout.write(json.dumps(r, sort_keys=True) + "\n")
        return 0
    if args.hook_status:
        r = hook_status(repo)
        sys.stdout.write(json.dumps(r, sort_keys=True) + "\n")
        return 0
    # Default: read-only check (safe, informative).
    return mode_check(repo, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
