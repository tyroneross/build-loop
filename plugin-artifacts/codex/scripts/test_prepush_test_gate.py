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
  - KNOWN-RED BASELINE (2026-07-30): newly-red blocks; baseline-red warns with owner
    + days left; an expired entry blocks even on a green tree; a now-passing entry is
    reported stale; and — the property that decides whether any of this is worth
    anything — a missing/malformed/unparseable baseline BLOCKS, never passes.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import textwrap
import types
from datetime import date
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


# ===========================================================================
# KNOWN-RED BASELINE — "pre-existing" as a bounded, accountable state
# ===========================================================================
#
# ROOT CAUSE this closes (2026-07-26): scripts/test_agent_surface_policy.py went
# red, the gate fired correctly, and the commit shipped anyway under "pre-existing
# failures untouched". "Pre-existing" was unbounded, unowned, and unrecorded — so
# EVERY check in this repo was bypassable by the same sentence.
#
# The behavior change that matters: a failing test NOT in the recorded baseline
# BLOCKS. Everything else here is the accountability scaffolding around that.

TODAY = date(2026, 7, 30)


def _write_baseline(workdir: Path, entries, *, raw: str | None = None) -> Path:
    """Write a baseline at the REAL relpath so the REAL loader is exercised."""
    path = workdir / gate.BASELINE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps({"version": 1, "entries": entries}))
    return path


def _entry(test, *, owner="tyroneross", reason="pre-existing", expires="2026-08-13"):
    return {"test": test, "reason": reason, "owner": owner, "expires": expires}


def _classify_gate(workdir: Path, relname: str, body: str):
    """A classify-enabled gate running REAL pytest on a file inside workdir.

    pytest's cwd is workdir, so node ids come back repo-relative (``t.py::test_x``)
    exactly as they do for the real named-pytest-gates spec.
    """
    (workdir / relname).write_text(body)
    return {
        "name": "tmp-pytest",
        "argv": [sys.executable, "-m", "pytest", relname,
                 "-p", "no:cacheprovider", "-q", "--no-header", "-rfE"],
        "requires": ["pytest"],
        "open_codes": (3, 4, 5),
        "timeout": 60,
        "classify": True,
        "targets": [relname],
    }


RED_BODY = "def test_alpha():\n    assert False\n"
GREEN_BODY = "def test_alpha():\n    assert True\n"


# --- 1. newly-red BLOCKS (the behavior change) -----------------------------

def test_newly_red_test_blocks(tmp_path):
    """A failing test that is NOT in the baseline blocks the push. This is the
    exact case that shipped on 2026-07-26 and must not ship again."""
    _write_baseline(tmp_path, [_entry("some/other_test.py::test_unrelated")])
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", RED_BODY)])
    assert v["action"] == "block"
    assert v["exit_code"] == 1
    assert "newly-red" in v["reason"]
    assert "t.py::test_alpha" in v["gate_results"][0]["detail"]


def test_mixed_newly_and_baseline_red_still_blocks(tmp_path):
    """One suppressed failure does not launder an unsuppressed one."""
    _write_baseline(tmp_path, [_entry("t.py::test_alpha")])
    body = "def test_alpha():\n    assert False\n\ndef test_beta():\n    assert False\n"
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", body)])
    assert v["action"] == "block"
    assert "t.py::test_beta" in v["gate_results"][0]["detail"]


# --- 2. baseline-red WARNS, naming owner + days remaining ------------------

def test_baseline_red_warns_with_owner_and_days_remaining(tmp_path):
    _write_baseline(tmp_path, [_entry("t.py::test_alpha", owner="ada", expires="2026-08-13")])
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", RED_BODY)])
    assert v["action"] == "allow"
    assert v["gate_results"][0]["status"] == "warn"
    warn = "\n".join(v["warnings"])
    assert "t.py::test_alpha" in warn
    assert "owner=ada" in warn
    assert "14d left" in warn          # 2026-07-30 -> 2026-08-13


def test_file_scoped_entry_suppresses_that_file_only(tmp_path):
    """A bare file path is a legitimate coarse suppression (unstable node ids), but
    it must not leak into a different file."""
    _write_baseline(tmp_path, [_entry("t.py")])
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", RED_BODY)])
    assert v["action"] == "allow" and v["gate_results"][0]["status"] == "warn"

    v2 = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                       gates=[_classify_gate(tmp_path, "u.py", RED_BODY)])
    assert v2["action"] == "block"


# --- 3. expired entry BLOCKS (an expiry you can ignore is not an expiry) ---

def test_expired_baseline_entry_blocks_even_on_a_green_tree(tmp_path):
    _write_baseline(tmp_path, [_entry("t.py::test_alpha", expires="2026-07-29")])
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", GREEN_BODY)])
    assert v["action"] == "block"
    assert v["failing_gate"] == "known-red-baseline-expired"
    assert "t.py::test_alpha" in v["gate_results"][0]["detail"]


def test_entry_expiring_today_is_not_yet_expired(tmp_path):
    """Boundary: expiry is inclusive of its own date."""
    _write_baseline(tmp_path, [_entry("t.py::test_alpha", expires="2026-07-30")])
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", RED_BODY)])
    assert v["action"] == "allow"
    assert "0d left" in "\n".join(v["warnings"])


# --- 4. now-passing entry is REPORTED (stale suppression is its own rot) ---

def test_baseline_entry_whose_test_now_passes_is_reported_stale(tmp_path):
    _write_baseline(tmp_path, [_entry("t.py::test_alpha", owner="ada")])
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", GREEN_BODY)])
    assert v["action"] == "allow"
    warn = "\n".join(v["warnings"])
    assert "STALE SUPPRESSION" in warn and "t.py::test_alpha" in warn and "owner=ada" in warn


def test_entry_for_a_test_that_did_not_run_is_not_called_stale(tmp_path):
    """A test that was never executed is not evidence of anything — no false 'stale'."""
    _write_baseline(tmp_path, [_entry("elsewhere/t.py::test_alpha")])
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", GREEN_BODY)])
    assert v["action"] == "allow"
    assert v["warnings"] == []


# --- 5. FAIL-SAFE: an unusable baseline BLOCKS -----------------------------
# This is the property that decides whether the whole mechanism is worth
# anything. If an unreadable suppression list degraded to "empty" the gate would
# still work; if it degraded to "allow everything" the mechanism would be theatre.

def test_missing_baseline_blocks(tmp_path):
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", GREEN_BODY)])
    assert v["action"] == "block"
    assert v["exit_code"] == 1
    assert v["failing_gate"] == "known-red-baseline"
    assert "missing" in v["reason"]


@pytest.mark.parametrize("raw,needle", [
    ("{ not json at all", "not valid JSON"),
    ("[]", "must be a JSON object"),
    ('{"version": 1}', "missing an 'entries' list"),
    ('{"entries": {"a": 1}}', "missing an 'entries' list"),
    ('{"entries": ["a string"]}', "is not an object"),
    ('{"entries": [{"test": "t.py::x", "reason": "r", "owner": "o"}]}', "'expires'"),
    ('{"entries": [{"test": "t.py::x", "reason": "r", "expires": "2026-08-13"}]}', "'owner'"),
    ('{"entries": [{"test": "t.py::x", "owner": "o", "expires": "2026-08-13"}]}', "'reason'"),
    ('{"entries": [{"reason": "r", "owner": "o", "expires": "2026-08-13"}]}', "'test'"),
    ('{"entries": [{"test": "t.py::x", "reason": "r", "owner": "", "expires": "2026-08-13"}]}', "'owner'"),
    ('{"entries": [{"test": "t.py::x", "reason": "r", "owner": "o", "expires": "soon"}]}', "unparseable 'expires'"),
    ('{"entries": [{"test": "t.py::x", "reason": "r", "owner": "o", "expires": "2026-13-40"}]}', "unparseable 'expires'"),
], ids=["not-json", "root-is-list", "no-entries-key", "entries-not-list",
        "entry-not-object", "no-expires", "no-owner", "no-reason", "no-test",
        "empty-owner", "expires-not-a-date", "expires-impossible-date"])
def test_malformed_baseline_blocks(tmp_path, raw, needle):
    _write_baseline(tmp_path, None, raw=raw)
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", GREEN_BODY)])
    assert v["action"] == "block", v
    assert v["failing_gate"] == "known-red-baseline"
    assert needle in v["reason"], v["reason"]


def test_duplicate_baseline_entries_block(tmp_path):
    """A duplicate hides whichever entry lost — reject rather than silently pick one."""
    _write_baseline(tmp_path, [_entry("t.py::test_alpha"),
                               _entry("t.py::test_alpha", owner="someone-else")])
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", GREEN_BODY)])
    assert v["action"] == "block"
    assert "duplicates" in v["reason"]


def test_unparseable_pytest_failure_blocks(tmp_path):
    """rc=1 with no parseable FAILED line means we cannot PROVE the failures are
    baselined — so it blocks. The fail-safe is about proof, not about output shape."""
    _write_baseline(tmp_path, [_entry("t.py::test_alpha")])
    spec = {"name": "tmp-pytest", "requires": [], "open_codes": (3, 4, 5), "timeout": 30,
            "classify": True, "targets": ["t.py"],
            "argv": [sys.executable, "-c", "import sys; print('mystery'); sys.exit(1)"]}
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY, gates=[spec])
    assert v["action"] == "block"
    assert "could not be classified" in v["reason"]


def test_baseline_file_unreadable_blocks(tmp_path):
    """A directory where the file should be: unreadable, therefore blocking."""
    (tmp_path / gate.BASELINE_RELPATH).mkdir(parents=True)
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      gates=[_classify_gate(tmp_path, "t.py", GREEN_BODY)])
    assert v["action"] == "block"
    assert v["failing_gate"] == "known-red-baseline"


# --- 6. blast radius: the layer only attaches to gates that opt in ---------

def test_baseline_is_not_required_when_no_gate_classifies(tmp_path):
    """Every pre-existing caller (and every test above this section) passes specs
    with no `classify` key and must keep its old behavior — no baseline needed."""
    assert not (tmp_path / gate.BASELINE_RELPATH).exists()
    green = tmp_path / "test_green.py"
    green.write_text(GREEN_BODY)
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], gates=[_pytest_gate_on(green)])
    assert v["action"] == "allow"

    v2 = gate.evaluate(tmp_path, [MAIN_PUSH_LINE],
                       gates=[_py_gate("g", "import sys; sys.exit(1)")])
    assert v2["action"] == "block"
    assert v2["failing_gate"] == "g"          # not the baseline


def test_bypass_env_still_wins_over_an_unusable_baseline(tmp_path):
    """The documented emergency override stays reachable and stays logged; the
    baseline layer must not create a state with no escape hatch."""
    v = gate.evaluate(tmp_path, [MAIN_PUSH_LINE], today=TODAY,
                      env={"BL_SKIP_PREPUSH_TESTS": "1"},
                      gates=[_classify_gate(tmp_path, "t.py", RED_BODY)])
    assert v["action"] == "bypass"
    assert "BYPASS" in (tmp_path / ".build-loop" / "audit-log.md").read_text()


# --- 7. wiring + the shipped baseline itself -------------------------------

def test_real_pytest_gates_opt_into_classification():
    """Structural: if someone drops `classify` from the real pytest gates, the
    known-red layer silently disarms. Caught here."""
    interp = gate._resolve_interpreter(REPO)
    for full in (False, True):
        specs = gate._build_gates(REPO, interp, full=full)
        classified = [s for s in specs if s.get("classify")]
        assert classified, f"no classify-enabled gate in {'full' if full else 'fast'} mode"
        for s in classified:
            assert s.get("targets"), f"{s['name']} classifies but declares no targets"


def test_shipped_baseline_is_valid_and_every_entry_is_bounded():
    """The baseline may be empty; every suppression that exists must be bounded."""
    bl = gate.load_baseline(REPO)
    assert bl["ok"], bl["error"]
    assert isinstance(bl["entries"], list)
    for e in bl["entries"]:
        for field in gate._BASELINE_REQUIRED_FIELDS:
            assert e[field].strip(), f"{e['test']} has an empty {field}"
        assert e["expires_date"] is not None


def test_parse_failed_ids_reads_pytest_short_summary():
    out = textwrap.dedent("""\
        =========================== short test summary info ============================
        FAILED scripts/test_a.py::TestX::test_one - AssertionError: 2 != 1
        FAILED scripts/test_a.py::test_two
        ERROR scripts/test_b.py
        FAILED scripts/test_c.py::test_p[a - b] - ValueError
        2 failed, 33 passed in 0.80s
    """)
    assert gate.parse_failed_ids(out) == [
        "scripts/test_a.py::TestX::test_one",
        "scripts/test_a.py::test_two",
        "scripts/test_b.py",
        "scripts/test_c.py::test_p[a - b]",
    ]
    assert gate.parse_failed_ids("") == []
    assert gate.parse_failed_ids("everything passed") == []
