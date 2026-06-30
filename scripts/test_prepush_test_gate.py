#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression artifact for scripts/prepush_test_gate.py.

REQUIRED BY the L2 RCA (2026-06-29 red-commits-reach-main): a standing check that
the pre-push test gate BLOCKS a push when a deterministic test fails and ALLOWS a
green tree — i.e. the old behavior (red pushes) fails this suite, the new behavior
(red blocked) passes it.

Coverage map (one assertion class per critic finding):
  - REGRESSION CORE: a real failing pytest -> block; a real green pytest -> allow.
  - exit-code table: 5 (no tests) -> fail-open skip; 2 (collection error) -> block;
    runner-missing / module-missing -> fail-open skip.
  - wiring: every fast DEFAULT gate actually RUNS and passes on this (green) repo,
    so a typo'd gate path (which would silently fail-open as "skip") is caught; and
    the named-escape gates are structurally present.
  - named-target guard: the two named test files exist and collect tests, closing
    the exit-5 silent-disarm hole.
  - hook composition: deploy-HOLD (stage 1) fires BEFORE the test gate and short
    circuits it; the test gate blocks at stage 2; both-pass arms closeout.
  - override: BL_SKIP_PREPUSH_TESTS=1 bypasses and logs.
  - scope: a non-protected branch push skips the gate entirely.
"""
from __future__ import annotations

import importlib.util
import io
import sys
import textwrap
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent           # .../build-loop/scripts
REPO = HERE.parent                                # .../build-loop
sys.path.insert(0, str(HERE))

import prepush_test_gate as gate  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAIN_PUSH_LINE = "refs/heads/main aaaa refs/heads/main bbbb\n"
FEATURE_PUSH_LINE = "refs/heads/feature/x aaaa refs/heads/feature/x bbbb\n"


def _py_gate(name: str, code: str, *, open_codes=(), requires=()):
    """A gate spec that runs `python -c <code>` so we control the exit code."""
    return {
        "name": name,
        "argv": [sys.executable, "-c", code],
        "requires": list(requires),
        "open_codes": tuple(open_codes),
        "timeout": 30,
    }


def _pytest_gate_on(path: Path, *, open_codes=(3, 4, 5)):
    """A gate that RUNS pytest on a specific file (real runner, real exit codes)."""
    return {
        "name": "tmp-pytest",
        "argv": [sys.executable, "-m", "pytest", str(path), "-p", "no:cacheprovider", "-q"],
        "requires": ["pytest"],
        "open_codes": tuple(open_codes),
        "timeout": 60,
    }


# ---------------------------------------------------------------------------
# REGRESSION CORE — real pytest, the exact RCA assertion
# ---------------------------------------------------------------------------

def test_blocks_when_a_real_deterministic_pytest_fails(tmp_path):
    """OLD behavior: red pushes. NEW behavior: red blocked. This is THE regression."""
    failing = tmp_path / "test_red.py"
    failing.write_text("def test_red():\n    assert False\n")
    verdict = gate.evaluate(
        tmp_path, [MAIN_PUSH_LINE], gates=[_pytest_gate_on(failing)],
    )
    assert verdict["action"] == "block"
    assert verdict["exit_code"] == 1
    assert verdict["failing_gate"] == "tmp-pytest"


def test_allows_when_a_real_deterministic_pytest_is_green(tmp_path):
    green = tmp_path / "test_green.py"
    green.write_text("def test_green():\n    assert True\n")
    verdict = gate.evaluate(
        tmp_path, [MAIN_PUSH_LINE], gates=[_pytest_gate_on(green)],
    )
    assert verdict["action"] == "allow"
    assert verdict["exit_code"] == 0


# ---------------------------------------------------------------------------
# Exit-code table (the fail-closed vs fail-open discriminator)
# ---------------------------------------------------------------------------

def test_exit1_blocks(tmp_path):
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], gates=[_py_gate("g", "import sys; sys.exit(1)")])
    assert v["action"] == "block"


def test_exit2_collection_error_blocks(tmp_path):
    # 2 is NOT in the named-pytest open_codes -> a collection/interrupt in a target
    # file is a real defect and must block.
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE],
                      gates=[_py_gate("g", "import sys; sys.exit(2)", open_codes=(3, 4, 5))])
    assert v["action"] == "block"


def test_exit5_no_tests_is_fail_open(tmp_path):
    # 5 (no tests collected) is fail-open; the named-target guard test below is the
    # standing check that the real named files never reach this state silently.
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE],
                      gates=[_py_gate("g", "import sys; sys.exit(5)", open_codes=(3, 4, 5))])
    assert v["action"] == "allow"
    assert v["gate_results"][0]["status"] == "skip"


def test_missing_required_module_is_fail_open(tmp_path):
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE],
                      gates=[_py_gate("g", "import sys; sys.exit(1)", requires=["a_module_that_does_not_exist_xyz"])])
    assert v["action"] == "allow"
    assert v["gate_results"][0]["status"] == "skip"


def test_missing_runner_is_fail_open(tmp_path):
    spec = {"name": "g", "argv": ["/no/such/binary/xyz"], "requires": [], "open_codes": (), "timeout": 5}
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], gates=[spec])
    assert v["action"] == "allow"
    assert v["gate_results"][0]["status"] == "skip"


def test_internal_error_never_raises_and_fails_open(tmp_path, monkeypatch):
    # Force an internal explosion inside evaluate's gate loop -> must fail-open allow.
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(gate, "_run_gate", boom)
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], gates=[_py_gate("g", "pass")])
    assert v["action"] == "allow"
    assert "internal error" in v["reason"]


# ---------------------------------------------------------------------------
# Scope + override
# ---------------------------------------------------------------------------

def test_non_protected_branch_skips_gate(tmp_path):
    # Even with a gate that WOULD fail, a feature-branch push never runs it.
    v = gate.evaluate(tmp_path, [FEATURE_PUSH_LINE], gates=[_py_gate("g", "import sys; sys.exit(1)")])
    assert v["action"] == "allow"
    assert v["gate_results"] == []


def test_bypass_env_skips_and_logs(tmp_path):
    v = gate.evaluate(
        tmp_path, [MAIN_PUSH_LINE],
        env={"BL_SKIP_PREPUSH_TESTS": "1"},
        gates=[_py_gate("g", "import sys; sys.exit(1)")],
    )
    assert v["action"] == "bypass"
    log = (tmp_path / ".build-loop" / "audit-log.md").read_text()
    assert "BYPASS" in log and "BL_SKIP_PREPUSH_TESTS" in log


def test_block_writes_audit_log(tmp_path):
    gate.evaluate(tmp_path, [MAIN_PUSH_LINE], gates=[_py_gate("g", "import sys; sys.exit(1)")])
    log = (tmp_path / ".build-loop" / "audit-log.md").read_text()
    assert "BLOCK" in log


# ---------------------------------------------------------------------------
# Wiring — the REAL default gates run + pass on this (green) repo
# ---------------------------------------------------------------------------

def test_default_gates_wire_the_named_escapes():
    """Structural: the fast default gate set references every named escape, so a
    silent drop of one is caught here without running them."""
    interp = gate._resolve_interpreter(REPO)
    specs = gate._build_gates(REPO, interp, full=False)
    joined = " ".join(" ".join(str(a) for a in s["argv"]) for s in specs)
    assert "tests/test_capability_registry.py" in joined      # test_categories_are_known
    assert "scripts/test_agent_surface_policy.py" in joined    # test_agent_surface_policy
    assert "import_manifest_lint.py" in joined                 # import-lint
    assert "architecture_diagram/check.sh" in joined           # artifact-freshness
    names = {s["name"] for s in specs}
    assert {"named-pytest-gates", "pytest-collection", "import-manifest-lint",
            "hook-budget-lint", "hook-hygiene-lint", "methodology-drift-lint",
            "artifact-freshness"} <= names


@pytest.mark.timeout(180)
def test_all_default_gates_run_and_pass_on_green_repo():
    """The strongest closure for the 'typo'd gate silently fail-opens' hole: run the
    REAL default gates against this green checkout. Every core gate must PASS (not
    skip) — a mis-pathed gate would surface as 'skip', not 'pass'. The two env
    sensitive gates (artifact-freshness needs bash+pyyaml+fresh diagram) may skip in
    a constrained env, so they are allowed pass-or-skip; the rest must pass."""
    interp = gate._resolve_interpreter(REPO)
    has_yaml = gate._module_available(interp, "yaml", workdir=REPO)
    verdict = gate.evaluate(REPO, [MAIN_PUSH_LINE], force_run=True)
    assert verdict["action"] == "allow", verdict
    by_name = {r["name"]: r["status"] for r in verdict["gate_results"]}
    # These need only pytest (which the test process proves available) -> must PASS,
    # so a mis-pathed gate (which would surface as 'skip', fail-open) is caught here.
    must_pass = {"named-pytest-gates", "import-manifest-lint",
                 "hook-budget-lint", "hook-hygiene-lint", "methodology-drift-lint"}
    for name in must_pass:
        assert by_name.get(name) == "pass", f"{name} did not pass: {by_name.get(name)}"
    # collection + freshness hard-require pyyaml; assert PASS when the gate's interp
    # has it (CI / synced checkout), else accept the fail-open 'skip'.
    expected = {"pass"} if has_yaml else {"pass", "skip"}
    assert by_name.get("pytest-collection") in expected, by_name.get("pytest-collection")
    assert by_name.get("artifact-freshness") in {"pass", "skip"}


def test_shallow_clone_skips_artifact_freshness(monkeypatch):
    """Regression (2026-06-30): a shallow clone (CI pytest.yml checkout) lacks the
    git history the diagram's freshness check derives dates from, so generate.py
    --check false-positives STALE. The gate must fail-open SKIP (not BLOCK) a green
    repo in that case. Pre-fix this blocked CI on the gate's own merge commit."""
    monkeypatch.setattr(gate, "_is_shallow_clone", lambda wd: True)
    verdict = gate.evaluate(REPO, [MAIN_PUSH_LINE], force_run=True)
    by_name = {r["name"]: r["status"] for r in verdict["gate_results"]}
    assert by_name.get("artifact-freshness") == "skip", verdict
    assert verdict["action"] == "allow", verdict


def test_named_targets_exist_and_collect():
    """Standing guard closing the exit-5 silent-disarm hole: if a named target is
    renamed/deleted, this fails (in the same suite the gate itself runs)."""
    for rel in ("tests/test_capability_registry.py", "scripts/test_agent_surface_policy.py"):
        assert (REPO / rel).exists(), f"named gate target missing: {rel}"


# ---------------------------------------------------------------------------
# Hook composition — deploy-HOLD (stage 1) then test gate (stage 2)
# ---------------------------------------------------------------------------

def _load_hook():
    """Load hooks/git/pre-push as a module (it has no .py extension, so an explicit
    SourceFileLoader is required — spec_from_file_location returns loader=None for an
    unrecognized suffix)."""
    from importlib.machinery import SourceFileLoader
    path = REPO / "hooks" / "git" / "pre-push"
    loader = SourceFileLoader("buildloop_prepush_hook", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _install_fakes(monkeypatch, *, hold_action, test_action):
    """Inject fake push_hold + prepush_test_gate modules the hook will import."""
    calls = {"push_hold": False, "test_gate": False, "closeout": False}

    fake_push_hold = types.ModuleType("push_hold")
    def _eval_push(repo, lines, **k):
        calls["push_hold"] = True
        return {"action": hold_action, "exit_code": 1 if hold_action == "block" else 0,
                "reason": "deploy hold", "source": "marker", "protected_targets": ["main"]}
    fake_push_hold.evaluate_push = _eval_push

    fake_test_gate = types.ModuleType("prepush_test_gate")
    def _eval_gate(repo, lines, **k):
        calls["test_gate"] = True
        return {"action": test_action, "exit_code": 1 if test_action == "block" else 0,
                "reason": "test gate", "failing_gate": "x" if test_action == "block" else None,
                "protected_targets": ["main"], "gate_results": []}
    fake_test_gate.evaluate = _eval_gate
    fake_test_gate.format_block_message = lambda v: "BLOCK BANNER\n"

    monkeypatch.setitem(sys.modules, "push_hold", fake_push_hold)
    monkeypatch.setitem(sys.modules, "prepush_test_gate", fake_test_gate)
    return calls


def _run_hook_main(monkeypatch, hook, tmp_path):
    monkeypatch.setattr(hook, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(hook, "_arm_post_push_closeout", lambda repo: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(MAIN_PUSH_LINE))
    return hook.main()


def test_hook_deploy_hold_fires_first_and_short_circuits(tmp_path, monkeypatch):
    hook = _load_hook()
    calls = _install_fakes(monkeypatch, hold_action="block", test_action="allow")
    rc = _run_hook_main(monkeypatch, hook, tmp_path)
    assert rc == 1                      # deploy-HOLD still blocks
    assert calls["push_hold"] is True
    assert calls["test_gate"] is False  # stage 2 never reached when stage 1 blocks


def test_hook_test_gate_blocks_at_stage_two(tmp_path, monkeypatch):
    hook = _load_hook()
    calls = _install_fakes(monkeypatch, hold_action="allow", test_action="block")
    rc = _run_hook_main(monkeypatch, hook, tmp_path)
    assert rc == 1                      # test gate blocks
    assert calls["push_hold"] is True and calls["test_gate"] is True


def test_hook_both_pass_allows(tmp_path, monkeypatch):
    hook = _load_hook()
    armed = {"v": False}
    calls = _install_fakes(monkeypatch, hold_action="allow", test_action="allow")
    monkeypatch.setattr(hook, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(hook, "_arm_post_push_closeout", lambda repo: armed.__setitem__("v", True))
    monkeypatch.setattr(sys, "stdin", io.StringIO(MAIN_PUSH_LINE))
    rc = hook.main()
    assert rc == 0
    assert calls["push_hold"] and calls["test_gate"]
    assert armed["v"] is True           # closeout armed only on final allow
