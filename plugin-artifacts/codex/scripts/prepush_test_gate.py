#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""prepush_test_gate.py — deterministic pre-push test gate (composes with deploy-HOLD).

WHY
---
The repo's verification was DETECT-AFTER-MERGE: the full deterministic suite — the
only thing that catches the repo-wide integration-gate class
(``test_categories_are_known``, ``test_agent_surface_policy``, artifact/diagram
freshness, import-vs-manifest lint) — ran ONLY in CI, post-push, on an unprotected
branch. No choke point ran it BEFORE code reached origin/main, so >=3 red commits
landed on main in one session (L2 RCA 2026-06-29).

This module is the prevent-before-merge actuator. It is invoked by
``hooks/git/pre-push`` as a SECOND stage AFTER ``push_hold.evaluate_push`` (the
deploy-HOLD gate). Either stage blocking stops the push.

COVERAGE-VS-LATENCY DESIGN
--------------------------
The full deterministic suite is ~100s (CI) / ~300s (local) — too slow for a per-push
tax (it trains developers into habitual ``--no-verify``, defeating the gate). Every
observed escape was a FAST repo-wide invariant gate, not a slow integration test. So
the DEFAULT gate is the fast subset that reproduces 100% of the named escape class in
~5-8s:

  1. the two named pytest gates (RUN, not collect-only)
  2. whole-suite pytest collection (import safety) — reuses ``pytest_collect_gate.py``
  3. import-vs-manifest lint
  4. hook budget + hygiene lints
  5. methodology-drift lint (--strict)
  6. architecture-diagram freshness/drift gate

``BL_PREPUSH_FULL=1`` swaps gates 1-2 for the full CI pytest invocation (exact parity).

FAIL-CLOSED vs FAIL-OPEN (the whole point)
------------------------------------------
A broken gate must NEVER permanently wedge the user's ability to push (matches the
pre-push hook's try/except contract), but a REAL test failure MUST block. The
discriminator is a per-gate PROBE: before running a gate, we confirm its required
modules import under the resolved interpreter. If the probe fails (pytest/pyyaml not
installed, env not synced) -> SKIP that gate, log, continue (fail-OPEN). If the gate
RUNS and returns a failure exit -> BLOCK (fail-CLOSED). The probe means "pytest not
installed" can never masquerade as "tests failed", and once pytest is proven
importable any non-zero exit on existing target files is a real defect.

``evaluate()`` NEVER raises — it mirrors ``push_hold.evaluate_push``; the caller (the
hook) prints ``reason`` and exits with ``exit_code``.

Exit-code -> action table (the discriminator, made explicit so neither a real
failure leaks out as fail-open nor an env hiccup wedges the push):

  pytest gates (named subset + full suite):
    0           -> pass
    1 (failed)  -> BLOCK  (a test failed)
    2 (interrupted / collection error in a target file) -> BLOCK  (import/syntax
                   defect — exactly the named escape class)
    3 (internal error) / 4 (usage error) -> SKIP  (pytest/plugin/env hiccup, not a
                   proven test failure -> fail-open)
    5 (no tests collected) -> SKIP  (nothing to verify; the standing guard test
                   ``test_named_targets_exist_and_collect`` catches a vanished/renamed
                   target so this can never silently disarm the named class)
  pytest-collection (reuses pytest_collect_gate.py): 0 pass/skip, 1 BLOCK, 2 SKIP.
  lints / diagram (after the required-module probe): 0 pass, any non-zero BLOCK.

OVERRIDE (escapable but visible)
--------------------------------
- ``BL_SKIP_PREPUSH_TESTS=1`` -> bypass the test gate, append one logged line to
  ``.build-loop/audit-log.md``. Kept SEPARATE from ``BUILDLOOP_PUSH_HOLD_BYPASS`` so an
  emergency deploy-hold override does not silently also skip tests.
- ``git push --no-verify`` -> native git, skips the whole hook.

CLI
---
::

    prepush_test_gate.py [--workdir PATH] [--target main] [--full] [--json] [--dry-run]

Exit codes: 0 = allow/bypass/skip, 1 = block (a real gate failure).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Reuse push_hold's push-line parsing + protected-branch detection (DRY — one
# source of truth for "what counts as a protected target").
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

try:  # pragma: no cover — exercised indirectly
    import push_hold  # type: ignore
except Exception:  # noqa: BLE001 — degrade gracefully; evaluate() handles None
    push_hold = None  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_ENV = "BL_SKIP_PREPUSH_TESTS"
FULL_ENV = "BL_PREPUSH_FULL"
LOG_RELPATH = Path(".build-loop") / "audit-log.md"

# Fallback protected set (only used if push_hold is unavailable).
_FALLBACK_PROTECTED = {
    "main", "master", "production", "prod", "release", "stable", "trunk", "live",
}

# The two named pytest escapes (RUN, not collect). Relative to repo root.
_NAMED_PYTEST_TARGETS = (
    "tests/test_capability_registry.py",       # test_categories_are_known
    "scripts/test_agent_surface_policy.py",    # test_agent_surface_policy
)

# Full-mode deselect list — mirrors .github/workflows/pytest.yml. Duplicated by
# necessity (opt-in path only); drift is fail-toward-safe (a stale deselect just
# runs an extra test that, if red, correctly blocks the push).
_FULL_DESELECT = (
    "scripts/rally_point/test_post_commit.py::test_plain_commit_writes_one_record",
    "scripts/rally_point/test_post_commit.py::test_manifest_commit_also_writes_dep_change",
    "scripts/rally_point/test_acceptance_stage1.py::test_stage1_acceptance",
    "scripts/rally_point/test_presence.py::test_stale_presence_regression_across_presence_status_and_lifecycle",
    "scripts/semantic_index/test_hybrid.py::test_read_semantic_passes_embed_fn_through_to_query_facts",
)


# ---------------------------------------------------------------------------
# Time / IO helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(workdir: Path, line: str) -> None:
    """Best-effort one-line audit log; never fatal."""
    try:
        log = workdir / LOG_RELPATH
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"- {_utcnow_iso()} {line}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Interpreter resolution + module probe
# ---------------------------------------------------------------------------

def _resolve_interpreter(workdir: Path) -> str:
    """Pick the python interpreter that drives every gate.

    Priority: workdir ``.venv``/``venv`` python (the uv-synced env, == CI deps) ->
    ``sys.executable``. We deliberately do NOT shell out to ``uv run`` per gate:
    one resolved interpreter keeps every gate's env identical and avoids per-gate
    subprocess overhead.
    """
    for candidate in (
        workdir / ".venv" / "bin" / "python",
        workdir / "venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python3"


def _module_available(interp: str, module: str, *, workdir: Path) -> bool:
    """Return True iff ``import <module>`` succeeds under ``interp``.

    This is the fail-open discriminator: a missing pytest/pyyaml means the env is
    not set up, so the gate is SKIPPED rather than treated as a failure.
    """
    try:
        proc = subprocess.run(
            [interp, "-c", f"import {module}"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return proc.returncode == 0
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# Gate runner
# ---------------------------------------------------------------------------

def _is_shallow_clone(workdir: Path) -> bool:
    """True if this is a shallow git clone. A shallow clone lacks full file
    history, so any gate that derives state from `git log` (e.g. the diagram's
    git_last_updated dates) computes wrong values and would false-positive.
    CI test jobs (pytest.yml) check out shallow; real local pre-push is full."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(workdir), "rev-parse", "--is-shallow-repository"],
            stderr=subprocess.DEVNULL, text=True).strip()
        return out == "true"
    except Exception:  # noqa: BLE001 — can't tell -> don't skip (safer to run)
        return False


def _run_gate(
    name: str,
    argv: list[str],
    *,
    workdir: Path,
    requires: Iterable[str],
    interp: str,
    open_codes: Iterable[int] = (),
    extra_path: str | None = None,
    timeout: int = 600,
    skip_if_shallow: bool = False,
) -> dict[str, Any]:
    """Run one gate. Returns ``{name, status, exit_code, detail}``.

    status in {``pass``, ``fail``, ``skip``}:
      - probe of a required module fails / runner unspawnable -> ``skip`` (fail-OPEN)
      - exit 0 -> ``pass``
      - exit in ``open_codes`` (env/no-collect, NOT a real failure) -> ``skip``
      - any other non-zero -> ``fail`` (fail-CLOSED -> caller blocks)
    """
    if skip_if_shallow and _is_shallow_clone(workdir):
        return {
            "name": name,
            "status": "skip",
            "exit_code": None,
            "detail": "shallow clone — git-history-derived check can't verify reliably — fail-open skip",
        }

    for mod in requires:
        if not _module_available(interp, mod, workdir=workdir):
            return {
                "name": name,
                "status": "skip",
                "exit_code": None,
                "detail": f"required module '{mod}' unavailable under {interp} — fail-open skip",
            }

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # match the suite's env -u PYTHONPATH discipline
    if extra_path:
        env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"

    try:
        proc = subprocess.run(
            argv,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return {
            "name": name,
            "status": "skip",
            "exit_code": None,
            "detail": f"runner not found ({exc}) — fail-open skip",
        }
    except subprocess.TimeoutExpired:
        # A hung gate is an environment problem, not a proven test failure -> skip.
        return {
            "name": name,
            "status": "skip",
            "exit_code": None,
            "detail": f"gate timed out after {timeout}s — fail-open skip",
        }
    except OSError as exc:
        return {
            "name": name,
            "status": "skip",
            "exit_code": None,
            "detail": f"could not run gate ({exc}) — fail-open skip",
        }

    rc = proc.returncode
    if rc == 0:
        return {"name": name, "status": "pass", "exit_code": 0, "detail": "ok"}
    if rc in set(open_codes):
        return {
            "name": name,
            "status": "skip",
            "exit_code": rc,
            "detail": f"exit {rc} treated as env/no-collect — fail-open skip",
        }
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-12:]
    return {
        "name": name,
        "status": "fail",
        "exit_code": rc,
        "detail": "\n".join(tail) or f"exit {rc}",
    }


def _build_gates(workdir: Path, interp: str, *, full: bool) -> list[dict[str, Any]]:
    """Build the ordered gate spec list for the active mode.

    Each spec is a kwargs dict consumed by ``_run_gate``.
    """
    interp_parent = Path(interp).parent
    venv_bin = str(interp_parent) if interp_parent.name == "bin" else None
    gates: list[dict[str, Any]] = []

    if full:
        # Exact CI parity — the full deterministic suite.
        argv = [
            interp, "-m", "pytest", "scripts/", "tests/",
            "-p", "no:cacheprovider", "-q", "--no-header", "-rfE",
            "-m", "not integration",
            "--timeout=60", "--timeout-method=thread",
        ]
        for d in _FULL_DESELECT:
            argv += ["--deselect", d]
        gates.append({
            "name": "full-deterministic-suite",
            "argv": argv,
            "requires": ["pytest"],
            # Block on 1 (failed) + 2 (collection error). 3/4/5 = env/no-collect.
            "open_codes": (3, 4, 5),
            "timeout": 600,
        })
    else:
        # Fast subset (default). Named pytest gates RUN (not collect-only).
        gates.append({
            "name": "named-pytest-gates",
            "argv": [
                interp, "-m", "pytest", *_NAMED_PYTEST_TARGETS,
                "-p", "no:cacheprovider", "-q", "--no-header",
            ],
            "requires": ["pytest"],
            # Block on 1 (failed) + 2 (collection error in a named file). 3/4/5 =
            # env/no-collect -> fail-open (see the exit-code table in the docstring).
            "open_codes": (3, 4, 5),
            "timeout": 120,
        })
        # Whole-suite collection (import safety) — reuse the existing gate script.
        # Its exit codes: 0 pass/skip, 1 collection failure (block), 2 runner error.
        gates.append({
            "name": "pytest-collection",
            "argv": [interp, "scripts/pytest_collect_gate.py", "--workdir", str(workdir)],
            # Whole-suite collection imports pyyaml at module top-level in at least
            # one test module, so collection hard-requires it. Probe BOTH so a
            # not-fully-synced env (no `test` extra) fails-open-skips rather than
            # wedging the push on a missing dep; a synced env (CI / real checkout)
            # runs and a REAL collection error (exit 1) blocks.
            "requires": ["pytest", "yaml"],
            "open_codes": (2,),  # 2 = runner error -> fail-open
            "timeout": 120,
        })

    # Repo-wide invariant lints (run in both modes) — stdlib only, so NO module
    # probe is needed (requires=[]): they can never crash on a missing third-party
    # dep and wrongly fail-open. Do not add a yaml probe here (per audit f1).
    gates.append({
        "name": "import-manifest-lint",
        "argv": [interp, "scripts/import_manifest_lint.py"],
        "requires": [],
        "timeout": 60,
    })
    gates.append({
        "name": "hook-budget-lint",
        "argv": [interp, "scripts/hook_budget_lint.py", "--hooks", "hooks/hooks.json"],
        "requires": [],
        "timeout": 60,
    })
    gates.append({
        "name": "hook-hygiene-lint",
        "argv": [interp, "scripts/hook_hygiene_lint.py", "--hooks", "hooks/hooks.json"],
        "requires": [],
        "timeout": 60,
    })
    gates.append({
        "name": "methodology-drift-lint",
        "argv": [interp, "scripts/methodology_drift_lint.py", "--strict"],
        "requires": [],
        "timeout": 60,
    })
    # Architecture-diagram freshness/drift (the artifact-freshness escape). check.sh
    # resolves `python3` from PATH, so prepend the venv bin dir + probe yaml so a
    # pyyaml-less system python can't masquerade a crash as drift.
    gates.append({
        "name": "artifact-freshness",
        "argv": ["bash", "scripts/architecture_diagram/check.sh"],
        "requires": ["yaml"],
        "extra_path": venv_bin,
        "timeout": 120,
        # generate.py --check derives git_last_updated from `git log`; a shallow
        # clone (CI pytest job) lacks the history, so it false-positives STALE.
        # Real pre-push runs in a full clone where this is reliable. The diagram
        # is independently gated by architecture-diagram.yml (fetch-depth:0).
        "skip_if_shallow": True,
    })
    return gates


# ---------------------------------------------------------------------------
# Protected-branch detection
# ---------------------------------------------------------------------------

def _protected_targets(
    workdir: Path,
    stdin_lines: Iterable[str],
    protected_branches: Iterable[str] | None,
) -> list[str]:
    """Return the protected branch names this push targets (reuses push_hold)."""
    if protected_branches is not None:
        protected = {b.lower() for b in protected_branches}
    elif push_hold is not None:
        try:
            from deployment_policy import load_protected_branches  # type: ignore
            protected = {b.lower() for b in load_protected_branches(workdir)}
        except Exception:  # noqa: BLE001
            protected = set(_FALLBACK_PROTECTED)
    else:
        protected = set(_FALLBACK_PROTECTED)

    targets: list[str] = []
    if push_hold is not None:
        refs = push_hold._parse_push_lines(stdin_lines)  # noqa: SLF001 — DRY reuse
        for _, _, remote_ref, _ in refs:
            branch = push_hold._branch_from_ref(remote_ref)  # noqa: SLF001
            if branch and branch.lower() in protected:
                targets.append(branch)
    else:
        # Minimal fallback parser if push_hold is unavailable.
        for raw in stdin_lines:
            parts = raw.strip().split()
            if len(parts) < 4:
                continue
            ref = parts[2]
            branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else None
            if branch and branch.lower() in protected:
                targets.append(branch)
    return targets


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def evaluate(
    workdir: Path,
    stdin_lines: Iterable[str],
    *,
    env: dict[str, str] | None = None,
    gates: list[dict[str, Any]] | None = None,
    protected_branches: Iterable[str] | None = None,
    force_run: bool = False,
) -> dict[str, Any]:
    """Decide whether the pre-push test gate blocks this push. NEVER raises.

    Returns ``{"action": "allow"|"block"|"bypass", "exit_code": int, "reason": str,
    "failing_gate": str|None, "mode": "fast"|"full", "protected_targets": [...],
    "gate_results": [...]}``.

    ``gates`` (a pre-built spec list) and ``force_run`` are test seams.
    """
    env_map = env if env is not None else os.environ
    stdin_lines = list(stdin_lines)

    try:
        targets = _protected_targets(workdir, stdin_lines, protected_branches)
        if not targets and not force_run:
            return {
                "action": "allow",
                "exit_code": 0,
                "reason": "no protected refs in push — test gate not applicable",
                "failing_gate": None,
                "mode": "fast",
                "protected_targets": [],
                "gate_results": [],
            }

        if str(env_map.get(SKIP_ENV, "")).strip() == "1":
            _log(
                workdir,
                f"prepush_test_gate BYPASS via {SKIP_ENV}=1 (targets={','.join(targets) or '-'})",
            )
            return {
                "action": "bypass",
                "exit_code": 0,
                "reason": f"BYPASS via {SKIP_ENV}=1",
                "failing_gate": None,
                "mode": "fast",
                "protected_targets": targets,
                "gate_results": [],
            }

        full = str(env_map.get(FULL_ENV, "")).strip() == "1"
        mode = "full" if full else "fast"
        interp = _resolve_interpreter(workdir)
        specs = gates if gates is not None else _build_gates(workdir, interp, full=full)

        results: list[dict[str, Any]] = []
        for spec in specs:
            res = _run_gate(
                spec["name"],
                spec["argv"],
                workdir=workdir,
                requires=spec.get("requires", ()),
                interp=interp,
                open_codes=spec.get("open_codes", ()),
                extra_path=spec.get("extra_path"),
                timeout=spec.get("timeout", 600),
                skip_if_shallow=spec.get("skip_if_shallow", False),
            )
            results.append(res)
            if res["status"] == "fail":
                _log(
                    workdir,
                    f"prepush_test_gate BLOCK gate={res['name']} "
                    f"exit={res['exit_code']} targets={','.join(targets)}",
                )
                return {
                    "action": "block",
                    "exit_code": 1,
                    "reason": f"deterministic gate failed: {res['name']}",
                    "failing_gate": res["name"],
                    "mode": mode,
                    "protected_targets": targets,
                    "gate_results": results,
                }

        return {
            "action": "allow",
            "exit_code": 0,
            "reason": f"all {mode} gates passed or skipped (fail-open)",
            "failing_gate": None,
            "mode": mode,
            "protected_targets": targets,
            "gate_results": results,
        }
    except Exception as exc:  # noqa: BLE001 — NEVER raise; fail-open on internal error
        return {
            "action": "allow",
            "exit_code": 0,
            "reason": f"internal error — fail-open allow: {exc!r}",
            "failing_gate": None,
            "mode": "fast",
            "protected_targets": [],
            "gate_results": [],
        }


def format_block_message(verdict: dict[str, Any]) -> str:
    """Operator-facing block banner (printed to stderr by the hook)."""
    gate = verdict.get("failing_gate") or "(unknown)"
    targets = ",".join(verdict.get("protected_targets") or []) or "(unknown)"
    detail = ""
    for r in verdict.get("gate_results", []):
        if r.get("status") == "fail":
            detail = r.get("detail") or ""
            break
    detail_block = ""
    if detail:
        indented = "\n".join("    " + ln for ln in detail.splitlines()[-12:])
        detail_block = (
            "  failing output (tail):\n"
            + indented
            + "\n  ---------------------------------------------------------------\n"
        )
    return (
        "\n"
        "===============================================================\n"
        "  BUILD-LOOP PRE-PUSH TEST GATE — push BLOCKED\n"
        "===============================================================\n"
        f"  protected target(s): {targets}\n"
        f"  failing gate       : {gate}\n"
        "  ---------------------------------------------------------------\n"
        + detail_block
        + "  A deterministic gate that CI also runs failed. Fix it before\n"
        "  this reaches origin (this is the prevent-before-merge control).\n"
        "    - re-run locally:   python3 scripts/prepush_test_gate.py --target main\n"
        "    - exact CI parity:  BL_PREPUSH_FULL=1 python3 scripts/prepush_test_gate.py --target main\n"
        "\n"
        "  EMERGENCY override (logged to .build-loop/audit-log.md):\n"
        f"    {SKIP_ENV}=1 git push <remote> <branch>\n"
        "    (or: git push --no-verify   — skips the whole pre-push hook)\n"
        "===============================================================\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the build-loop deterministic pre-push test gate.",
    )
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--target", type=str, default="main",
        help="Simulate a push to this branch (default: main). Use to run the gate "
             "out of band; the real hook passes git's stdin instead.",
    )
    parser.add_argument("--full", action="store_true", help="Run the full CI suite (sets BL_PREPUSH_FULL=1).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="List the gates that would run; do not execute.")
    args = parser.parse_args(argv)

    workdir = args.workdir.resolve()
    env = dict(os.environ)
    if args.full:
        env[FULL_ENV] = "1"

    if args.dry_run:
        interp = _resolve_interpreter(workdir)
        specs = _build_gates(workdir, interp, full=args.full)
        out = {
            "action": "dry_run",
            "mode": "full" if args.full else "fast",
            "interpreter": interp,
            "gates": [{"name": s["name"], "argv": s["argv"]} for s in specs],
        }
        print(json.dumps(out, indent=2) if args.json else "\n".join(g["name"] for g in specs))
        return 0

    # Synthesize a git pre-push stdin line targeting the requested branch.
    stdin_line = f"refs/heads/{args.target} 0000000 refs/heads/{args.target} 0000000\n"
    verdict = evaluate(workdir, [stdin_line], env=env)

    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        print(f"action={verdict['action']} mode={verdict['mode']} reason={verdict['reason']}")
        for r in verdict.get("gate_results", []):
            print(f"  [{r['status']:4s}] {r['name']}")
        if verdict["action"] == "block":
            sys.stderr.write(format_block_message(verdict))

    return int(verdict.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
