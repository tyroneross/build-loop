#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""self_mod_verify.py — correctness gate for any self-modification of build-loop's own code.

Runs the test suite, parses pass/fail, and optionally auto-reverts changed files
if the suite fails.  This is the load-bearing guardrail that prevents a self-
simplification from being committed when it breaks existing behaviour.

The gate measures correctness via tests only.  It does NOT special-case which
files were changed (gate files, test files, self-improvement scripts, etc.) —
those run the same way as any other change.  Oversight moves to post-hoc review
and end-of-run readback; it does not live here.

CLI::

    python3 scripts/self_mod_verify.py
        --workdir <repo>
        [--scope full|changed|auto]
        [--changed-files f1 f2 ...]
        [--auto-revert]              # REQUIRES --changed-files
        [--baseline <snapshot.json>]
        [--timeout SECONDS]
        --json

    python3 scripts/self_mod_verify.py snapshot
        --workdir <repo> [--out <snapshot.json>] --json

Revert safety (BUIL-TOOLING-m2b7cts2d0gqn1d1j6q56, two files lost 2026-09-12):
  * ``--auto-revert`` with an empty ``--changed-files`` is a USAGE ERROR
    (verdict "error", exit 2) — no tests run, nothing is touched. The revert set
    is never derived from git status, because a derived set spans every dirty
    file in the checkout including another session's uncommitted work.
  * Every reverted file's working-tree bytes are written to the object DB FIRST
    (``git hash-object -w``); the sha and its ``git cat-file blob <sha> > <path>``
    recovery command are printed and returned in ``reverted_blobs``. A file whose
    backup cannot be written is not reverted, and neither is any other file.
  * ``--baseline`` (from the ``snapshot`` subcommand) records pre-run blob shas,
    so a file that was ALREADY dirty is restored to its pre-run content, never to
    HEAD. With no baseline, a listed file carrying STAGED changes is refused
    outright — the gate cannot attribute that dirt to this run.

Output JSON::

    {
      "scope":           "full" | "changed" | "auto",
      "effective_scope": "full" | "changed" | "broad",   # resolved from auto
      "ran":             [str, ...],     # test files that were discovered and run
      "passed":          int,
      "failed":          int,
      "failed_tests":    [str, ...],    # short names of failing tests
      "reverted":        bool,
      "verdict":         "pass" | "fail" | "no_tests" | "no_changes" | "error",
      "timed_out":       bool,
      "error_reason":    str | null,    # present (non-null) only when verdict="error"
      "errors":          [str, ...],
      "derived_from_git": bool,         # True when the change set was auto-derived
                                        # from git (no --changed-files given)
      "reverted_blobs":  [{"path": str, "blob": str|null, "recover": str|null}],
      "baseline_used":   str | null     # path of the --baseline actually applied
    }

Exit codes:
  0  — verdict "pass"       (the ONLY green result — tests ran and passed)
  1  — verdict "fail"       (real test failures)
  2  — verdict "error"      (infrastructure error: collection failure, timeout, worker crash)
  3  — verdict "no_tests" | "no_changes"  (INCONCLUSIVE — the gate verified nothing)

"no_tests" and "no_changes" are NOT green.  A self-modification safety gate that
ran zero tests has verified nothing, so it must not read as pass — it exits 3
(distinct from a real fail/error) so a caller can tell "verified pass" from
"nothing verified" and, if it genuinely wants soft behaviour, opt into it
explicitly (`|| [ $? -eq 3 ]`).  Historically these exited 0, which made the
then-documented invocation (`--scope auto --auto-revert` with no --changed-files)
gate nothing while looking green.  That invocation is now itself a usage error;
the documented form passes the run's own --changed-files.

Change-set derivation:
  When --changed-files is omitted under scope auto/changed, the gate derives the
  change set from git — `git diff --name-only HEAD` (tracked staged+unstaged)
  PLUS `git ls-files --others --exclude-standard` (untracked NEW files, so a
  self-mod that adds foo.py+test_foo.py still runs test_foo.py).  If git shows
  no changes at all → verdict "no_changes", exit 3.

When pytest exits non-zero AND no "N passed"/"N failed" summary line was parsed
(collection error, INTERNALERROR, timeout at the pytest layer, etc.), the gate
returns verdict="error" with an error_reason field so callers know WHY rather
than silently reporting 0/0/fail.

Scope selection:
  full     — whole scripts/ test suite, serial, -m "not live" to exclude live-
             service tests, --timeout=120 --timeout-method=thread so any future
             hang fails cleanly rather than blocking the gate.
  changed  — only the mapped test files for --changed-files
  auto     — recommended default:
               1–3 source files    → changed  (mapped tests only)
               4+ files OR a core/orchestration-path file → broad  (mapped + area tests)
               else                → changed
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Exit codes. 0 is the ONLY green result. no_tests / no_changes are inconclusive
# (the gate verified nothing) and get their own code so they never read as pass.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2
EXIT_INCONCLUSIVE = 3


# ---------------------------------------------------------------------------
# Orchestration / core-path heuristic for "broad" scope
# ---------------------------------------------------------------------------

_CORE_PREFIXES = (
    "scripts/self_",
    "scripts/autonomy",
    "scripts/classify",
    "scripts/deploy",
    "scripts/audit",
    "scripts/worktree",
    "scripts/coordination",
    "scripts/rally",
    "scripts/state",
    "scripts/plan_verify",
    "scripts/review_",
    "scripts/build_acp",
)


def _is_core_path(rel_posix: str) -> bool:
    return any(rel_posix.startswith(p) for p in _CORE_PREFIXES)


# Generated / vendored trees that MIRROR scripts/ and tests/. Their test files
# are build output, never gate targets: a mirrored tree carries `test_*.py`
# files whose basenames collide with their `scripts/` twins, and pytest aborts
# the whole collection with "import file mismatch" the moment both copies land
# in one invocation. Discovery therefore never returns a path under these roots
# — excluding them at the source beats de-duplicating downstream.
_NON_GATE_TREES = (
    "dist",
    "build",
    "node_modules",
    "vendor",
    ".venv",
)


def _in_non_gate_tree(path: Path, workdir: Path) -> bool:
    """True when ``path`` lives under a generated/vendored mirror tree."""
    try:
        parts = path.resolve().relative_to(Path(workdir).resolve()).parts
    except (ValueError, OSError):
        parts = path.parts
    return any(part in _NON_GATE_TREES for part in parts)


# ---------------------------------------------------------------------------
# Test-runner discovery
# ---------------------------------------------------------------------------

def _runner_has_pytest_timeout(runner_base: list[str],
                               cwd: Path | str | None = None) -> bool:
    """True when the RESOLVED runner interpreter has the pytest-timeout plugin.

    Asks the same interpreter that will run the suite, rather than importing
    ``pytest_timeout`` here: ``_find_runner`` may select ``uv run pytest`` (a
    different venv from this process), so a local import would answer for the
    wrong environment. Fails CLOSED to False — a probe that cannot answer drops
    the flag, which degrades cleanly, instead of passing a flag that would make
    pytest exit 4 on an unrecognized option.

    ``--version`` is passed TWICE on purpose. Modern pytest prints only
    ``pytest <N>`` for a single ``--version`` and reserves the registered-plugin
    list for the doubled form, so a single-flag probe reported "not installed"
    against a venv that had pytest-timeout — and every full-scope gate run
    silently lost per-test hang protection while saying so in a warning nobody
    actioned. The doubled flag is also accepted by older pytest, which printed
    the plugin list either way.

    ``cwd`` must be the SAME directory the suite will run in. ``uv run pytest``
    resolves its virtualenv from the working directory, so probing in one
    directory and running in another answers for the wrong environment — the
    probe said "installed" from the plugin repo while the run, executed in a
    temp workdir, hit a venv without the plugin and exited 4.
    """
    try:
        r = subprocess.run(
            [*runner_base, "--version", "--version", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(cwd) if cwd is not None else None,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    return "timeout" in (r.stdout + r.stderr).lower()


def _find_runner(workdir: Path) -> list[str] | None:
    """Return the command list for pytest, or None if unavailable.

    Preference: ``uv run pytest`` (respects project virtualenv),
    fallback: ``python3 -m pytest``.

    Full scope always runs serially — no xdist.
    """
    # Try uv run pytest
    try:
        r = subprocess.run(
            ["uv", "run", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(workdir),
        )
        if r.returncode == 0:
            return ["uv", "run", "pytest"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: python3 -m pytest
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(workdir),
        )
        if r.returncode == 0:
            return [sys.executable, "-m", "pytest"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


# ---------------------------------------------------------------------------
# Test file resolution
# ---------------------------------------------------------------------------

def _tests_for_changed(workdir: Path, changed_files: list[str]) -> list[str]:
    """Map changed implementation files to their test counterparts.

    ``foo.py`` → ``test_foo.py``. A package file ``.../<pkg>/X.py`` →
    ``test_<pkg>.py`` (the capability's test). Tests are searched RECURSIVELY
    under both ``scripts/`` and ``tests/``, so nested suites (e.g.
    ``tests/architecture/test_scanner.py``) and folder-per-capability packages
    anywhere in the tree stay gate-able. Files already named ``test_*.py`` are
    included directly.
    """
    discovered: list[str] = []
    seen: set[str] = set()

    # Index every test file by basename, recursively under scripts/ and tests/.
    test_index: dict[str, list[Path]] = {}
    for root in (workdir / "scripts", workdir / "tests"):
        if root.is_dir():
            for tf in root.rglob("test_*.py"):
                test_index.setdefault(tf.name, []).append(tf)

    def _add_by_name(name: str) -> None:
        for tf in test_index.get(name, []):
            if str(tf) not in seen:
                discovered.append(str(tf))
                seen.add(str(tf))

    for raw in changed_files:
        p = Path(raw)
        if not p.is_absolute():
            p = workdir / p
        p = p.resolve()

        # Only Python files are pytest targets. A changed doc whose basename
        # happens to start with `test_` (e.g. docs/scripts/test_foo.md, the
        # per-script doc convention) must NOT be handed to pytest — pytest
        # exits 4 ("file or directory not found"/usage) on a .md path, which
        # the gate would surface as verdict=error. Non-.py changes map to no
        # test target (verdict no_tests, exit 0) unless a sibling test exists.
        if p.suffix != ".py":
            continue

        # A mirror copy under a generated tree (dist/, build/, vendor/, ...) is
        # build output, not a gate target — and collecting it alongside its
        # scripts/ twin aborts the run with "import file mismatch".
        if _in_non_gate_tree(p, workdir):
            continue

        if p.name.startswith("test_"):
            if p.exists() and str(p) not in seen:
                discovered.append(str(p))
                seen.add(str(p))
            continue

        # The file's own name, plus — for a package file — its containing
        # capability folder name.
        _add_by_name(f"test_{p.name}")           # foo.py → test_foo.py
        parent = p.parent.name
        if parent and parent not in ("scripts", "tests", "src"):
            _add_by_name(f"test_{parent}.py")    # <pkg>/X.py → test_<pkg>.py

    return discovered


def _broad_tests_for_changed(workdir: Path, changed_files: list[str]) -> list[str]:
    """Return mapped tests + area-adjacent test files (best-effort bounded).

    Adds test files whose name shares a stem prefix with any changed file,
    covering the module neighbourhood without running the full suite.
    """
    scripts_dir = workdir / "scripts"
    base = _tests_for_changed(workdir, changed_files)
    seen: set[str] = set(base)

    # Collect stem prefixes from changed files
    prefixes: set[str] = set()
    for raw in changed_files:
        stem = Path(raw).stem
        # strip leading "test_" if present
        if stem.startswith("test_"):
            stem = stem[5:]
        # use up to the first underscore as area prefix (e.g. "coordination_bootstrap" → "coordination")
        area = stem.split("_")[0] if "_" in stem else stem
        if area:
            prefixes.add(area)

    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.glob("test_*.py")):
            if str(f) in seen:
                continue
            # Include if name matches any area prefix
            stem_no_test = f.stem[5:]  # strip "test_"
            area = stem_no_test.split("_")[0] if "_" in stem_no_test else stem_no_test
            if area in prefixes:
                seen.add(str(f))
                base.append(str(f))

    return base


def _all_script_tests(workdir: Path) -> list[str]:
    """Return all test files under scripts/."""
    scripts_dir = workdir / "scripts"
    if not scripts_dir.is_dir():
        return []
    return sorted(str(f) for f in scripts_dir.glob("test_*.py"))


def _git_changed_files(workdir: Path) -> tuple[list[str], list[str]]:
    """Derive the changed-file set from git when --changed-files was omitted.

    Returns ``(files, derivation_errors)`` where ``files`` is repo-relative paths
    for BOTH:
      * tracked files with staged OR unstaged modifications
        (``git diff --name-only HEAD``), and
      * untracked NEW files (``git ls-files --others --exclude-standard``).

    The untracked arm is load-bearing: a self-modification that ADDS a new
    ``foo.py`` + ``test_foo.py`` is invisible to ``git diff`` (which only sees
    tracked changes), so without it the gate would map to no tests and report a
    green-looking no_tests — the exact false-negative logged as BUIL-SELFMOD-001.

    ``derivation_errors`` records any git arm that FAILED (exception OR nonzero
    exit). Previously a per-arm ``continue`` swallowed those failures silently:
    a failed ``git diff --name-only HEAD`` arm with a succeeding ``ls-files``
    arm yielded a TRUNCATED change set that could pass green with
    ``derived_from_git=True`` and nothing recorded. The caller now appends these
    into ``result["errors"]``, and — when the ``tracked-diff`` arm specifically
    failed — escalates to ``verdict=error`` (a green over a knowingly-partial
    set is the false-green class this gate exists to kill).

    Fail-soft on the collection itself: a missing/erroring arm never raises; it
    is recorded and skipped. An all-arms-empty result maps to the caller's
    explicit ``no_changes`` verdict.
    """
    out: list[str] = []
    seen: set[str] = set()
    derivation_errors: list[str] = []
    arms = (
        ("tracked-diff", ["git", "-C", str(workdir), "diff", "--name-only", "-z", "HEAD"]),
        ("untracked", ["git", "-C", str(workdir), "ls-files", "--others",
                       "--exclude-standard", "-z"]),
    )
    for label, cmd in arms:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            derivation_errors.append(f"git change-set arm '{label}' errored: {exc}")
            continue
        if r.returncode != 0:
            detail = r.stderr.strip() or f"exit {r.returncode}"
            derivation_errors.append(f"git change-set arm '{label}' failed: {detail}")
            continue
        # -z: git QUOTES non-ASCII paths in default output, so a newline split
        # would mangle them. NUL-separated output is verbatim.
        for line in r.stdout.split("\0"):
            name = line.strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out, derivation_errors


# ---------------------------------------------------------------------------
# Scope resolution for "auto"
# ---------------------------------------------------------------------------

def _resolve_auto_scope(
    workdir: Path,
    changed_files: list[str],
) -> tuple[str, list[str]]:
    """Resolve 'auto' scope to an effective scope + test file list.

    Rules (file-count / core-path only — no file-identity special-casing):
      1–3 source files and no core-path file → changed (mapped tests only)
      4+ files OR a core/orchestration-path file → broad (mapped + area tests)

    Returns (effective_scope, test_files) where effective_scope is one of
    "changed" or "broad" (never "auto" or "full").
    """
    n = len(changed_files)

    has_core = False
    for raw in changed_files:
        p = Path(raw)
        if not p.is_absolute():
            p = workdir / p
        try:
            rel = p.resolve().relative_to(workdir.resolve()).as_posix()
        except ValueError:
            continue
        if _is_core_path(rel):
            has_core = True
            break

    if n >= 4 or has_core:
        return "broad", _broad_tests_for_changed(workdir, changed_files)
    else:
        return "changed", _tests_for_changed(workdir, changed_files)


# ---------------------------------------------------------------------------
# Parse pytest output
# ---------------------------------------------------------------------------

# Patterns for summary line: "5 passed" / "2 failed" / "1 passed, 1 failed"
_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
# Pattern for individual FAILED lines: "FAILED scripts/test_foo.py::TestBar::test_baz"
_FAILED_ITEM_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)


def _parse_pytest_output(stdout: str, stderr: str) -> tuple[int, int, list[str], bool]:
    """Return (passed, failed, failed_test_names, has_summary) from pytest output.

    ``has_summary`` is True when at least one of "N passed" or "N failed" was
    found, meaning pytest produced a proper result line.  False indicates a
    collection error, INTERNALERROR, or similar infrastructure failure.
    """
    combined = stdout + "\n" + stderr
    passed = 0
    failed = 0

    m = _PASSED_RE.search(combined)
    if m:
        passed = int(m.group(1))
    m = _FAILED_RE.search(combined)
    if m:
        failed = int(m.group(1))

    failed_tests = _FAILED_ITEM_RE.findall(combined)
    # In -q (quiet) mode pytest may not print a "N passed, N failed" footer
    # but it DOES print "FAILED <test>" lines for failures.  When the footer
    # count is absent, derive `failed` from the explicit FAILED lines.
    if failed == 0 and failed_tests:
        failed = len(failed_tests)

    # has_summary: True when pytest produced any real result indicators.
    # We treat either a count-summary OR explicit FAILED items as evidence
    # that pytest ran to completion (not a collection/infra failure).
    has_summary = bool(
        _PASSED_RE.search(combined)
        or _FAILED_RE.search(combined)
        or failed_tests  # explicit FAILED lines = real test output
    )
    return passed, failed, failed_tests, has_summary


def _classify_error_reason(stdout: str, stderr: str) -> str:
    """Return a short error_reason string when pytest produced no summary.

    Grep stdout+stderr for known infrastructure-failure keywords and return
    the first match.  Falls back to "unknown — see stderr".
    """
    combined = (stdout + "\n" + stderr).lower()
    if "timeout" in combined:
        return "Timeout"
    if "internalerror" in combined:
        return "INTERNALERROR"
    if "no tests ran" in combined or "no tests were run" in combined:
        return "no tests ran"
    if "error" in combined:
        return "errors — see stderr"
    return "unknown — see stderr"


# ---------------------------------------------------------------------------
# Revert helper
# ---------------------------------------------------------------------------

def _partition_tracked(
    workdir: Path, changed_files: list[str]
) -> tuple[list[str], list[str]]:
    """Split ``changed_files`` into (tracked, untracked) via ``git ls-files``.

    Each path is normalised to repo-relative posix form (absolute paths inside
    the repo are accepted). A path present in ``git ls-files`` is tracked;
    anything else — a new/untracked file, or a path outside the repo — is
    treated as untracked. Fail-soft: a git error yields everything-untracked so
    the caller reports rather than blindly restores.
    """
    rels: list[str] = []
    for raw in changed_files:
        p = Path(raw)
        if not p.is_absolute():
            p = workdir / p
        try:
            rel = p.resolve().relative_to(workdir.resolve()).as_posix()
        except ValueError:
            rel = raw  # outside the repo → falls through to untracked
        rels.append(rel)

    tracked_set: set[str] = set()
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "ls-files", "-z", "--"] + rels,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0:
            # -z so a quoted non-ASCII path matches the rels built above;
            # a mismatch would silently classify the file as untracked.
            tracked_set = {ln.strip() for ln in r.stdout.split("\0") if ln.strip()}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        tracked_set = set()

    tracked = [rel for rel in rels if rel in tracked_set]
    untracked = [rel for rel in rels if rel not in tracked_set]
    return tracked, untracked


def _hash_object(workdir: Path, rel: str) -> tuple[str | None, str | None]:
    """Write the WORKING-TREE content of ``rel`` into the object DB as a blob.

    Returns ``(sha, error)``. Exactly one is non-None, except for a file that is
    absent from the working tree, which returns ``(None, None)`` — there is no
    content to preserve, so nothing is lost by restoring it.

    This is the recoverability primitive. ``git restore --staged --worktree``
    destroys unstaged working-tree content, which has no git object behind it
    and is therefore unrecoverable (BUIL-TOOLING-m2b7cts2d0gqn1d1j6q56, two
    files lost 2026-09-12). Writing the blob FIRST converts every revert from
    destructive to recoverable: ``git cat-file blob <sha> > <path>`` restores the
    exact pre-revert bytes.
    """
    abs_path = workdir / rel
    if not abs_path.exists():
        return None, None
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "hash-object", "-w", "--", str(abs_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return None, f"blob backup failed for {rel}: {exc}"
    if r.returncode != 0:
        detail = r.stderr.strip() or f"exit {r.returncode}"
        return None, f"blob backup failed for {rel}: {detail}"
    sha = r.stdout.strip()
    if not sha:
        return None, f"blob backup failed for {rel}: git hash-object returned no sha"
    return sha, None


def _has_staged_changes(workdir: Path, rel: str) -> bool | None:
    """True when ``rel``'s INDEX content differs from HEAD. None when unknown."""
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "diff", "--cached", "--name-only", "HEAD", "--", rel],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return bool(r.stdout.strip())


def _restore_blob_to_worktree(workdir: Path, rel: str, blob: str) -> str | None:
    """Write blob ``blob``'s bytes over ``rel``, and point the index at it too.

    Returns an error string, or None on success.

    ``cat-file blob`` (not ``-p``): ``-p`` pretty-prints BY OBJECT TYPE, so a
    baseline whose sha names a commit or tree would succeed and write commit
    metadata over the file. The typed form exits non-zero on a non-blob, which
    lands in the error path below with the file untouched.

    The INDEX is moved to the same blob via ``update-index --cacheinfo``. Writing
    only the worktree leaves the failed run's content STAGED while the result
    reports ``reverted: true``, so a peer's plain ``git commit`` would commit the
    change this gate just rejected. ``git restore --source`` cannot be used here
    (it needs a tree-ish, not a blob), but ``update-index`` takes a blob directly.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "cat-file", "blob", blob],
            capture_output=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return f"baseline restore failed for {rel}: {exc}"
    if r.returncode != 0:
        detail = r.stderr.decode("utf-8", "replace").strip() or f"exit {r.returncode}"
        return f"baseline restore failed for {rel}: {detail}"
    try:
        (workdir / rel).write_bytes(r.stdout)
    except OSError as exc:
        return f"baseline restore failed for {rel}: {exc}"

    mode = _index_mode(workdir, rel) or "100644"
    try:
        u = subprocess.run(
            ["git", "-C", str(workdir), "update-index", "--cacheinfo",
             f"{mode},{blob},{rel}"],
            capture_output=True, text=True, timeout=15,
        )
        if u.returncode != 0:
            return (
                f"{rel}: worktree restored to the baseline but the INDEX could not be "
                f"moved with it ({u.stderr.strip() or u.returncode}); the failed change "
                f"may still be staged. Inspect with `git diff --cached -- {rel}`."
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return f"{rel}: index update failed after baseline restore: {exc}"
    return None


def _index_mode(workdir: Path, rel: str) -> str | None:
    """Current index file mode for ``rel`` (e.g. ``100644``/``100755``)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "ls-files", "--stage", "-z", "--", rel],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.split()[0]


def _delete_worktree_file(workdir: Path, rel: str) -> str | None:
    """Re-apply a baseline-recorded DELETION: drop the file and its index entry."""
    try:
        (workdir / rel).unlink(missing_ok=True)
    except OSError as exc:
        return f"baseline deletion failed for {rel}: {exc}"
    try:
        subprocess.run(
            ["git", "-C", str(workdir), "rm", "--cached", "--quiet", "--", rel],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return f"baseline deletion: index entry for {rel} not removed: {exc}"
    return None


def _restore_to_head(workdir: Path, rel: str, errors: list[str]) -> bool:
    """``git restore --staged --worktree`` one file, with a legacy fallback."""
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "restore", "--staged", "--worktree", "--", rel],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0:
            return True
        if r.stderr.strip():
            errors.append(f"git restore failed for {rel}: {r.stderr.strip()}")
        r2 = subprocess.run(
            ["git", "-C", str(workdir), "checkout", "--", rel],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r2.returncode != 0:
            if r2.stderr.strip():
                errors.append(f"git checkout fallback failed for {rel}: {r2.stderr.strip()}")
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        errors.append(f"revert failed for {rel}: {exc}")
        return False


def _load_baseline(
    path: str | None, errors: list[str], *, workdir: Path | None = None
) -> dict[str, dict] | None:
    """Load a ``snapshot`` baseline into ``{repo-relative path: {blob, deleted}}``.

    Returns None when no baseline was requested OR the file is unusable —
    unreadable, malformed, or recorded against a DIFFERENT commit than the one
    checked out now.

    The HEAD check matters because the documented invocation names a fixed,
    reusable path: without it, a baseline taken days ago would "restore" a file
    to bytes that predate every commit since, which is a clobber wearing a
    revert's clothes. An unusable baseline never degrades silently to the looser
    no-baseline path — the caller asked for attribution, so ``_revert_files``
    refuses rather than reverting to HEAD without it.
    """
    if not path:
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"--baseline unreadable ({path}): {exc}")
        return None
    files = raw.get("files")
    if not isinstance(files, dict):
        errors.append(f"--baseline malformed ({path}): no files object")
        return None
    if workdir is not None:
        recorded_head = raw.get("head")
        try:
            r = subprocess.run(
                ["git", "-C", str(workdir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=15,
            )
            current_head = r.stdout.strip() if r.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            current_head = None
        if current_head and recorded_head and recorded_head != current_head:
            errors.append(
                f"--baseline stale ({path}): recorded against {recorded_head[:12]} but "
                f"HEAD is {current_head[:12]}. Restoring its blobs would write content "
                "that predates the intervening commits. Re-run `snapshot`."
            )
            return None
    out: dict[str, dict] = {}
    for rel, entry in files.items():
        if not isinstance(entry, dict):
            continue
        blob = entry.get("blob")
        deleted = bool(entry.get("deleted"))
        if isinstance(blob, str) or deleted:
            out[rel] = {"blob": blob if isinstance(blob, str) else None,
                        "deleted": deleted}
    return out


def _revert_files(
    workdir: Path,
    changed_files: list[str],
    errors: list[str],
    *,
    derived_from_git: bool = False,
    baseline: dict[str, dict] | None = None,
    baseline_path: str | None = None,
    reverted_blobs: list[dict] | None = None,
) -> bool:
    """Revert the TRACKED files EXPLICITLY listed in ``changed_files``.

    Return True only when a tracked file was actually restored.

    Three structural guarantees, in order — each closes a way the old revert
    destroyed work it did not own (BUIL-TOOLING-m2b7cts2d0gqn1d1j6q56):

    1. NEVER A DERIVED SET. ``derived_from_git=True`` means the change set came
       from ``git diff``/``ls-files``, i.e. every dirty file in the checkout
       including a peer session's. That set is REFUSED outright. The caller
       already rejects ``--auto-revert`` without ``--changed-files`` before any
       test runs, so this is defence in depth, not the primary gate.
    2. ALWAYS RECOVERABLE. Every file's working-tree content is written to the
       object DB as a blob BEFORE anything is restored, and the sha plus its
       one-line recovery command are reported. If ANY backup fails, the whole
       revert is abandoned — a revert that cannot be undone does not proceed.
    3. NEVER REVERT PRE-EXISTING DIRT TO HEAD. With ``--baseline`` (from the
       ``snapshot`` subcommand), a file dirty before the run is restored to its
       PRE-RUN blob, not to HEAD. Without a baseline the gate cannot attribute
       dirt, so it refuses any listed file carrying STAGED changes (a deliberate
       act this run did not perform) and says so.

    Untracked files are reported (``untracked, not reverted: <f>``) and never
    deleted.
    """
    blobs = reverted_blobs if reverted_blobs is not None else []

    if not changed_files:
        errors.append(
            "--auto-revert requested but no --changed-files given; refusing to revert. "
            "Pass the files THIS run changed."
        )
        return False

    if derived_from_git:
        errors.append(
            "refusing to revert: the change set was derived from git, not from an "
            "explicit --changed-files list. A derived set spans every dirty file in "
            "the checkout, including concurrent work owned by another session. "
            "Re-run with --changed-files naming only this run's files."
        )
        return False

    if baseline is None and baseline_path:
        # A baseline that was REQUESTED but is unusable is strictly worse than
        # none: the caller believes attribution is on. Degrading silently to the
        # revert-to-HEAD path would destroy exactly the pre-existing dirt the
        # flag exists to protect. Refuse instead; the reason is already in
        # errors[] from _load_baseline.
        errors.append(
            f"refusing to revert: --baseline {baseline_path} was requested but could "
            "not be used, so pre-run dirt cannot be attributed. Re-run `snapshot`, or "
            "drop --baseline to accept the stricter no-baseline rules."
        )
        return False

    tracked, untracked = _partition_tracked(workdir, changed_files)

    for f in untracked:
        errors.append(f"untracked, not reverted: {f}")

    if not tracked:
        return False

    # --- Attribution: decide the restore target per file (or refuse it) ---
    targets: list[tuple[str, str, str | None]] = []  # (rel, kind, blob)
    for rel in tracked:
        if baseline is not None:
            entry = baseline.get(rel)
            if entry is None:
                targets.append((rel, "head", None))
            elif entry.get("deleted"):
                targets.append((rel, "baseline-deleted", None))
                errors.append(
                    f"{rel} was DELETED in the working tree before the run; "
                    "re-applying that deletion instead of restoring it from HEAD"
                )
            elif entry.get("blob"):
                targets.append((rel, "baseline", entry["blob"]))
                errors.append(
                    f"{rel} was already dirty before the run "
                    f"(baseline {entry['blob'][:12]}); restoring to that pre-run "
                    "content, NOT to HEAD"
                )
            else:
                errors.append(
                    f"refusing to revert {rel}: its baseline entry records neither a "
                    "blob nor a deletion, so its pre-run state is unknown"
                )
            continue
        staged = _has_staged_changes(workdir, rel)
        if staged is None:
            errors.append(
                f"refusing to revert {rel}: no --baseline recorded and its staged state "
                "could not be read, so pre-existing dirt cannot be ruled out"
            )
            continue
        if staged:
            errors.append(
                f"refusing to revert {rel}: no --baseline recorded and the file carries "
                "STAGED changes this run cannot attribute. Record a baseline with "
                "`self_mod_verify.py snapshot --workdir <repo> --out <file>` before the run."
            )
            continue
        errors.append(
            f"{rel}: no --baseline recorded, so pre-run dirt cannot be distinguished from "
            "this run's edits; restoring to HEAD with a recoverable blob backup"
        )
        targets.append((rel, "head", None))

    if not targets:
        return False

    # --- Recoverability: back up EVERY target before touching any of them ---
    staged_backups: list[dict] = []
    for rel, _kind, _blob in targets:
        sha, err = _hash_object(workdir, rel)
        if err:
            errors.append(err)
            errors.append(
                "revert ABANDONED: a working-tree backup could not be written, so the "
                "revert would be unrecoverable. Nothing was reverted."
            )
            return False
        if sha is None:
            staged_backups.append({
                "path": rel,
                "blob": None,
                "recover": None,
                "note": "absent from working tree; no content to preserve",
            })
        else:
            staged_backups.append({
                "path": rel,
                "blob": sha,
                # shlex.quote: an unquoted `> scripts/with space.py` redirects to
                # `scripts/with` and drops the content the operator is trying to
                # recover. The recovery command IS the safety property here.
                "recover": f"git cat-file blob {sha} > {shlex.quote(rel)}",
            })

    # Anchor the backups under a ref so they survive `git gc --prune`. An
    # unreferenced loose object is recoverable only until the next prune, which
    # would make "every revert is recoverable" true for a window rather than
    # true. Refs may point directly at blobs.
    _anchor_backup_blobs(workdir, staged_backups, errors)

    blobs.extend(staged_backups)

    # --- Apply ---
    any_restored = False
    for rel, kind, blob in targets:
        if kind == "baseline" and blob:
            err = _restore_blob_to_worktree(workdir, rel, blob)
            if err:
                errors.append(err)
                continue
            any_restored = True
        elif kind == "baseline-deleted":
            err = _delete_worktree_file(workdir, rel)
            if err:
                errors.append(err)
                continue
            any_restored = True
        elif kind == "head" and _restore_to_head(workdir, rel, errors):
            any_restored = True
    return any_restored


def _anchor_backup_blobs(workdir: Path, backups: list[dict], errors: list[str]) -> None:
    """Point a ref at each backup blob so `git gc --prune` cannot reclaim it.

    Refs live under ``refs/self-mod-verify/backup/<utc-stamp>/<n>``; prune them
    with ``git for-each-ref --format='%(refname)' refs/self-mod-verify | xargs -n1
    git update-ref -d``. Best-effort: a failure to anchor is reported, never
    fatal — the blob still exists, just on the default prune clock.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for n, entry in enumerate(backups):
        sha = entry.get("blob")
        if not sha:
            continue
        ref = f"refs/self-mod-verify/backup/{stamp}/{n}"
        try:
            r = subprocess.run(
                ["git", "-C", str(workdir), "update-ref", ref, sha],
                capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            errors.append(f"backup blob {sha[:12]} not anchored ({exc}); prune-window only")
            continue
        if r.returncode != 0:
            errors.append(
                f"backup blob {sha[:12]} not anchored "
                f"({r.stderr.strip() or r.returncode}); recover before the next git gc"
            )
            continue
        entry["ref"] = ref


def snapshot(workdir: Path) -> dict:
    """Record the pre-run state of every dirty tracked file as a git blob.

    The output feeds ``verify --baseline <file>``: a file already dirty when the
    snapshot was taken is restored to THAT content on revert, never to HEAD, so
    a revert can never silently discard work that predates the run.

    Shape::

        {"schema_version": 1, "workdir": str, "head": str|null,
         "created": iso8601, "files": {rel: {"blob": sha}},
         "untracked": [rel, ...], "errors": [str, ...]}
    """
    errors: list[str] = []

    def _git(args: list[str]) -> tuple[int, str]:
        try:
            r = subprocess.run(
                ["git", "-C", str(workdir)] + args,
                capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            errors.append(f"git {' '.join(args[:2])} errored: {exc}")
            return 1, ""
        if r.returncode != 0:
            errors.append(
                f"git {' '.join(args[:2])} failed: {r.stderr.strip() or r.returncode}"
            )
        return r.returncode, r.stdout

    # -z on both listing arms: git QUOTES and backslash-escapes any path with a
    # non-ASCII or control character in its default output, so a newline-split
    # would hand `"scripts/caf\303\251.py"` to hash-object and silently drop the
    # file from the baseline. NUL-separated output is verbatim.
    _, head_out = _git(["rev-parse", "HEAD"])
    rc_dirty, dirty_out = _git(["diff", "--name-only", "-z", "HEAD"])
    _, untracked_out = _git(["ls-files", "--others", "--exclude-standard", "-z"])

    files: dict[str, dict] = {}
    if rc_dirty == 0:
        for line in dirty_out.split("\0"):
            rel = line.strip()
            if not rel:
                continue
            sha, err = _hash_object(workdir, rel)
            if err:
                errors.append(err)
                continue
            if sha is None:
                # Deleted in the worktree. Record the DELETION explicitly: an
                # omitted entry reads as "clean at snapshot", and the revert
                # would then resurrect the file from HEAD, silently undoing
                # whoever deleted it.
                files[rel] = {"blob": None, "deleted": True}
                continue
            files[rel] = {"blob": sha, "deleted": False}

    return {
        "schema_version": 1,
        "workdir": str(workdir),
        "head": head_out.strip() or None,
        "created": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "untracked": [ln.strip() for ln in untracked_out.split("\0") if ln.strip()],
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Main verify routine
# ---------------------------------------------------------------------------

def verify(
    *,
    workdir: Path,
    scope: str,
    changed_files: list[str],
    auto_revert: bool,
    timeout: int = 300,
    baseline_path: str | None = None,
) -> tuple[dict, int]:
    """Public gate entry. Rejects an unsafe --auto-revert BEFORE anything runs.

    ``--auto-revert`` with an empty ``--changed-files`` is a hard usage error
    (verdict "error", exit 2) rather than a run whose revert set gets derived
    from git. A derived set spans every dirty file in the checkout, so the old
    behaviour let a failing test delete a concurrent session's uncommitted work
    (BUIL-TOOLING-m2b7cts2d0gqn1d1j6q56). Rejecting at the door means no test
    runs, no file is touched, and the wrong invocation is inexpressible rather
    than merely warned about.
    """
    if auto_revert and not changed_files:
        result = {
            "scope": scope,
            "effective_scope": scope if scope != "auto" else "changed",
            "ran": [],
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "error",
            "timed_out": False,
            "error_reason": "auto_revert_requires_changed_files",
            "errors": [
                "--auto-revert requires an explicit non-empty --changed-files list. "
                "Deriving the revert set from git status would sweep every dirty file "
                "in the checkout, including another session's uncommitted work. "
                "Nothing was run and nothing was reverted."
            ],
            "derived_from_git": False,
            "reverted_blobs": [],
            "baseline_used": None,
        }
        return result, EXIT_ERROR

    result, exit_code = _verify_inner(
        workdir=workdir,
        scope=scope,
        changed_files=changed_files,
        auto_revert=auto_revert,
        timeout=timeout,
        baseline_path=baseline_path,
    )
    result.setdefault("reverted_blobs", [])
    result.setdefault("baseline_used", None)
    return result, exit_code


def _verify_inner(
    *,
    workdir: Path,
    scope: str,
    changed_files: list[str],
    auto_revert: bool,
    timeout: int = 300,
    baseline_path: str | None = None,
) -> tuple[dict, int]:
    """Run the verification gate. Return (result_dict, exit_code).

    Exit codes:
      0  — verdict "pass"  (the ONLY green result: tests ran and passed)
      1  — verdict "fail"  (real test failures; failed > 0 OR returncode != 0
                             with a parseable summary)
      2  — verdict "error" (infrastructure failure: no summary parsed despite
                             non-zero exit — collection error, INTERNALERROR,
                             per-test timeout, worker crash, etc.)
      3  — verdict "no_tests" | "no_changes" (INCONCLUSIVE: the gate verified
                             nothing — no mapped tests, no pytest, or no diff.
                             Not green; a caller wanting soft behaviour opts in.)
    """
    errors: list[str] = []
    reverted = False
    derived_from_git = False
    reverted_blobs: list[dict] = []
    baseline = _load_baseline(baseline_path, errors, workdir=workdir)

    # --- Runner discovery ---
    runner_base = _find_runner(workdir)
    if runner_base is None:
        # A safety gate that cannot run pytest has verified nothing → inconclusive,
        # not green. Callers key on verdict=="pass"; the non-zero exit keeps a
        # naive `if verify; then commit` shell wrapper from treating it as a pass.
        result = {
            "scope": scope,
            "effective_scope": scope if scope != "auto" else "changed",
            "ran": [],
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "timed_out": False,
            "error_reason": None,
            "errors": ["pytest not available (uv run pytest and python3 -m pytest both failed)"],
            "derived_from_git": False,
        }
        return result, EXIT_INCONCLUSIVE

    # --- Change-set derivation fallback (TEST SCOPE ONLY) ---
    # A read-only `--scope auto` run with no --changed-files would otherwise
    # resolve to an empty test set → a green-looking no_tests that gates NOTHING,
    # so derive the real change set from git (tracked diff + untracked new files).
    # This set selects WHICH TESTS RUN and nothing else: --auto-revert can never
    # reach here, because verify() rejects --auto-revert without an explicit
    # --changed-files list before any of this runs, and _revert_files refuses a
    # derived set outright. A derived set spans every dirty file in the checkout,
    # including a peer session's uncommitted work
    # (BUIL-TOOLING-m2b7cts2d0gqn1d1j6q56). Only auto/changed consume
    # changed_files for test selection; full runs the whole suite regardless.
    if not changed_files and scope in ("auto", "changed"):
        changed_files, derivation_errors = _git_changed_files(workdir)
        derived_from_git = bool(changed_files)
        errors.extend(derivation_errors)
        if any("'tracked-diff'" in e for e in derivation_errors):
            # The tracked-modified arm failed → the derived set is knowingly
            # partial. A green (or even a real fail) over a partial set is the
            # false-green this gate exists to kill: escalate to verdict=error.
            result = {
                "scope": scope,
                "effective_scope": scope if scope != "auto" else "changed",
                "ran": [],
                "passed": 0,
                "failed": 0,
                "failed_tests": [],
                "reverted": False,
                "verdict": "error",
                "timed_out": False,
                "error_reason": "git change-set derivation failed (tracked-diff arm)",
                "errors": errors,
                "derived_from_git": derived_from_git,
            }
            return result, EXIT_ERROR
        if not changed_files:
            # Nothing staged, modified, or untracked → the gate verified nothing.
            # This is NOT pass: a self-mod gate with no diff is a caller mistake
            # or a lost working tree, never a green light. Distinct verdict so the
            # caller can tell it apart from a real no_tests.
            result = {
                "scope": scope,
                "effective_scope": scope if scope != "auto" else "changed",
                "ran": [],
                "passed": 0,
                "failed": 0,
                "failed_tests": [],
                "reverted": False,
                "verdict": "no_changes",
                "timed_out": False,
                "error_reason": None,
                "errors": [
                    "no --changed-files given and git shows no staged/unstaged/"
                    "untracked changes; gate verified nothing"
                ],
                "derived_from_git": False,
            }
            return result, EXIT_INCONCLUSIVE

    # --- Scope resolution ---
    effective_scope = scope
    if scope == "auto":
        effective_scope, test_files = _resolve_auto_scope(workdir, changed_files)
    elif scope == "changed":
        test_files = _tests_for_changed(workdir, changed_files)
    else:  # full
        test_files = _all_script_tests(workdir)

    if not test_files:
        result = {
            "scope": scope,
            "effective_scope": effective_scope,
            "ran": [],
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "timed_out": False,
            "error_reason": None,
            "errors": errors,
            "derived_from_git": derived_from_git,
        }
        return result, EXIT_INCONCLUSIVE

    # --- Build pytest command ---
    # Full scope: serial (no xdist), per-test timeout so hangs fail cleanly,
    # and -m "not live" to exclude tests that require a live external service.
    # auto/changed scope: fast path — no extra flags needed.
    # `-rf` emits the "FAILED <nodeid>" short-summary lines that _FAILED_ITEM_RE
    # parses into failed_tests[]; `--color=no` strips ANSI so the `^FAILED` regex
    # matches (pytest can force color even when output is captured, e.g. PY_COLORS
    # or a consumer repo's `color=yes`; a CLI flag overrides both). Without these,
    # `-q` printed the count ("N failed") but failed_tests came back empty.
    cmd = runner_base + ["-q", "-rf", "--color=no", "-p", "no:cacheprovider", "--tb=short"]
    if scope == "full":
        # `--timeout` comes from the pytest-timeout PLUGIN, not pytest itself.
        # It is declared in pyproject's `test` extra, so CI always has it — but a
        # dev venv provisioned without the extra does not, and pytest then exits
        # 4 (usage error) on the unrecognized flag. That exit was classified as
        # verdict=error/"Timeout", so the self-modification safety gate reported
        # a hard error on a healthy tree and blocked its own dogfooding.
        # Degrade instead of hard-failing: keep the hang protection when the
        # plugin is present, drop it (with a warning) when it is not. A gate that
        # cannot run because an OPTIONAL plugin is missing has verified nothing,
        # which is strictly worse than running without the hang guard.
        if _runner_has_pytest_timeout(runner_base, cwd=workdir):
            cmd += ["--timeout=120", "--timeout-method=thread"]
        else:
            print(
                "self_mod_verify: pytest-timeout not installed in the runner "
                "interpreter; proceeding WITHOUT per-test hang protection. "
                "Install it with `uv sync --extra test` to restore the guard.",
                file=sys.stderr,
            )
        cmd += ["-m", "not live"]
    cmd += test_files

    # --- Run pytest ---
    timed_out = False
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        errors.append(f"pytest process timed out after {timeout}s")
        result = {
            "scope": scope,
            "effective_scope": effective_scope,
            "ran": test_files,
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "error",
            "timed_out": True,
            "error_reason": "Timeout",
            "errors": errors,
            "derived_from_git": derived_from_git,
        }
        return result, EXIT_ERROR
    except (FileNotFoundError, OSError) as exc:
        errors.append(f"pytest runner error: {exc}")
        result = {
            "scope": scope,
            "effective_scope": effective_scope,
            "ran": test_files,
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "timed_out": False,
            "error_reason": None,
            "errors": errors,
            "derived_from_git": derived_from_git,
        }
        return result, EXIT_INCONCLUSIVE

    passed, failed, failed_tests, has_summary = _parse_pytest_output(r.stdout, r.stderr)

    # --- Determine verdict from test results only ---
    if r.returncode == 0 and failed == 0:
        verdict = "pass"
        exit_code = EXIT_PASS
    elif r.returncode == 5:
        # pytest exit 5 = no tests collected. Inconclusive, not green (exit 3).
        verdict = "no_tests"
        exit_code = EXIT_INCONCLUSIVE
    elif not has_summary and r.returncode != 0:
        # Non-zero exit with no parseable summary → infrastructure failure.
        # This is the "0 passed / 0 failed / verdict=fail" silent-failure case.
        # Surface it as "error" with a reason so the caller knows WHY.
        reason = _classify_error_reason(r.stdout, r.stderr)
        errors.append(f"pytest exited {r.returncode} with no summary line: {reason}")
        verdict = "error"
        exit_code = EXIT_ERROR
    else:
        verdict = "fail"
        exit_code = EXIT_FAIL
        if auto_revert:
            reverted = _revert_files(
                workdir,
                changed_files,
                errors,
                derived_from_git=derived_from_git,
                baseline=baseline,
                baseline_path=baseline_path,
                reverted_blobs=reverted_blobs,
            )

    error_reason: str | None = None
    if verdict == "error":
        error_reason = _classify_error_reason(r.stdout, r.stderr)

    result = {
        "scope": scope,
        "effective_scope": effective_scope,
        "ran": test_files,
        "passed": passed,
        "failed": failed,
        "failed_tests": failed_tests,
        "reverted": reverted,
        "verdict": verdict,
        "timed_out": timed_out,
        "error_reason": error_reason,
        "errors": errors,
        "derived_from_git": derived_from_git,
        "reverted_blobs": reverted_blobs,
        "baseline_used": baseline_path if baseline is not None else None,
    }
    return result, exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--workdir",
        required=True,
        help="Repo root to verify (must be the build-loop repo itself)",
    )
    p.add_argument(
        "--scope",
        choices=["full", "changed", "auto"],
        default="auto",
        help=(
            "auto (default) = smart scope based on blast radius; "
            "changed = only tests for --changed-files; "
            "full = whole scripts/ suite, serial, live tests excluded, "
            "per-test timeout 120s"
        ),
    )
    p.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        metavar="FILE",
        help="Changed files (required when --scope changed; used for revert scope)",
    )
    p.add_argument(
        "--auto-revert",
        action="store_true",
        help=(
            "If the suite fails, revert --changed-files. REQUIRES a non-empty "
            "--changed-files list; the revert set is never derived from git status"
        ),
    )
    p.add_argument(
        "--baseline",
        default=None,
        metavar="JSON",
        help=(
            "Pre-run snapshot from `self_mod_verify.py snapshot`. A listed file that "
            "was already dirty at snapshot time is restored to THAT content, not HEAD"
        ),
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Subprocess timeout in seconds for the pytest process as a whole (default 300)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit result JSON to stdout (always implied; kept for compatibility)",
    )
    return p.parse_args(argv)


def parse_snapshot_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="self_mod_verify.py snapshot",
        description=(
            "Record the pre-run blob sha of every dirty tracked file so a later "
            "--auto-revert restores pre-existing dirt to its own content, not HEAD."
        ),
    )
    p.add_argument("--workdir", required=True, help="Repo root to snapshot")
    p.add_argument("--out", default=None, metavar="JSON", help="Write the baseline here")
    p.add_argument("--json", action="store_true", help="Emit baseline JSON to stdout")
    return p.parse_args(argv)


def _main_snapshot(argv: list[str]) -> int:
    args = parse_snapshot_args(argv)
    workdir = Path(args.workdir).resolve()
    payload = snapshot(workdir)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(
        f"self_mod_verify snapshot: {len(payload['files'])} dirty tracked file(s) recorded"
        + (f" -> {args.out}" if args.out else ""),
        file=sys.stderr,
    )
    return 0 if not payload["errors"] else 2


def main(argv: list[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]
    if raw and raw[0] == "snapshot":
        return _main_snapshot(raw[1:])

    args = parse_args(raw)
    workdir = Path(args.workdir).resolve()

    result, exit_code = verify(
        workdir=workdir,
        scope=args.scope,
        changed_files=args.changed_files or [],
        auto_revert=args.auto_revert,
        timeout=args.timeout,
        baseline_path=args.baseline,
    )

    # Result JSON to stdout, then the recovery block (also stdout, so a human
    # reading the console and a script reading the pipe see the same shas).
    print(json.dumps(result, indent=2))
    blobs = result.get("reverted_blobs") or []
    for entry in blobs:
        if entry.get("blob"):
            print(f"reverted {entry['path']} -> blob {entry['blob']}")
            print(f"  recover: {entry['recover']}")
        else:
            print(
                f"reverted {entry['path']} -> blob (none: "
                f"{entry.get('note', 'no content')})"
            )
    blob_suffix = f" reverted_blobs={len(blobs)}" if blobs else ""
    err_suffix = (
        f" error_reason={result['error_reason']!r}"
        if result.get("error_reason") is not None
        else ""
    )
    print(
        f"self_mod_verify: verdict={result['verdict']} "
        f"passed={result['passed']} failed={result['failed']} "
        f"reverted={result['reverted']} scope={result['scope']} "
        f"effective_scope={result['effective_scope']} "
        f"timed_out={result['timed_out']}"
        f"{blob_suffix}{err_suffix}",
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
