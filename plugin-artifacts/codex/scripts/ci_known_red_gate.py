#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""ci_known_red_gate.py — apply the known-red baseline to the CI test run.

WHY
---
``scripts/prepush_test_gate.py`` classifies a failing pytest run against
``scripts/known_red_baseline.json`` (newly-red BLOCKS, baselined-and-unexpired
warns, expired BLOCKS, unreadable baseline BLOCKS). That gate is LOCAL and
bypassable (``git push --no-verify``, ``BL_SKIP_PREPUSH_TESTS=1``, a fresh clone
with no hooks installed). ``.github/workflows/pytest.yml`` ran the same suite and
knew nothing about the baseline, so a red test could still reach main through CI
— which is exactly how the 2026-07-26 "pre-existing failures untouched" drift
shipped. This wrapper closes that hole: CI now applies the SAME classification.

REUSE, NOT A FORK
-----------------
Every classification decision is imported from ``prepush_test_gate``:
``load_baseline`` (schema + fail-safe), ``expired_entries``, ``classify_failures``
(which owns ``parse_failed_ids`` / node-id matching), and ``stale_entries``. This
module contributes only the CI shell: run pytest, stream its output, map the
result onto a CI exit code, and render GitHub annotations + a step summary. If the
classification rules change in ``prepush_test_gate``, CI inherits them with no
edit here.

DELIBERATE DIVERGENCE FROM PREPUSH: FAIL-CLOSED ON ENV
-----------------------------------------------------
``prepush_test_gate`` fails OPEN on env trouble (pytest exit 3/4/5, missing
modules) so a broken local env can never wedge a developer's push. CI has the
opposite duty and no such hazard: the runner env is provisioned by
``uv sync --extra test`` in the step immediately before, so "pytest could not
run" or "no tests collected" is a real defect in CI, not a local hiccup. Every
non-zero pytest exit therefore fails the job here unless the failures are proven
baselined. The classification logic is shared; only this exit-code policy differs,
and it differs in the safe direction.

BEHAVIOR (the four required cases)
----------------------------------
  newly-red (failing test not in the baseline)   -> exit 1  (CI fails)
  baselined + unexpired failures only            -> exit 0  (CI passes, REPORTED
                                                    as ::warning:: + step summary)
  ANY baseline entry past its expiry             -> exit 2  (CI fails, checked
                                                    BEFORE the suite runs, so it
                                                    fires on a green tree too)
  baseline missing / malformed / schema-violating-> exit 2  (CI fails, fail-SAFE)
  pytest exit 1 whose failures could not be parsed-> exit 1 (unclassifiable is not
                                                    "no failures")
  a baselined test that now PASSES               -> reported as a stale
                                                    suppression (non-blocking)

USAGE
-----
::

    ci_known_red_gate.py [--workdir PATH] [--target scripts/ --target tests/] \
        -- <pytest args...>

    # classify a previously captured run instead of executing one (used by the
    # colocated tests and for local reproduction)
    ci_known_red_gate.py --pytest-output run.txt --pytest-exit-code 1

Exit codes: 0 = pass (green or fully baselined), 1 = test-side block,
2 = baseline-side block. Any non-zero fails the CI job.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
import re
from pathlib import Path
from typing import Any, Iterable

# Import the classification logic rather than reimplementing it. prepush_test_gate
# lives next to this file; it guards its own optional imports, so importing it is
# side-effect-free beyond a sys.path insert of this same directory.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from prepush_test_gate import (  # noqa: E402  — path insert must precede import
    BASELINE_RELPATH,
    _BASELINE_REQUIRED_FIELDS,
    _today,
    classify_failures,
    expired_entries,
    load_baseline,
    parse_skipped_files,
    stale_entries,
)

EXIT_PASS = 0
EXIT_BLOCK_TESTS = 1
EXIT_BLOCK_BASELINE = 2

DEFAULT_TARGETS = ("scripts/", "tests/")


# ---------------------------------------------------------------------------
# GitHub Actions output helpers (no-ops outside Actions)
# ---------------------------------------------------------------------------

def _annotate(level: str, message: str, *, out=None) -> None:
    """Emit a GitHub workflow annotation. Plain text elsewhere — same content."""
    stream = out if out is not None else sys.stdout
    # Annotations are single-line; newlines are escaped per the Actions spec.
    flat = message.replace("\r", "").replace("\n", "%0A")
    stream.write(f"::{level}::{flat}\n")


def _summary(lines: Iterable[str]) -> None:
    """Append to $GITHUB_STEP_SUMMARY when present. Never fatal."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# pytest execution
# ---------------------------------------------------------------------------

def run_pytest(pytest_args: list[str], *, workdir: Path, out=None) -> tuple[int, str]:
    """Run pytest under the CURRENT interpreter, streaming + capturing output.

    Streaming matters in CI: a 10-minute silent step is unreadable, and the log
    must show the same output the bare `pytest` step used to show.
    """
    stream = out if out is not None else sys.stdout
    argv = [sys.executable, "-m", "pytest", *pytest_args]
    stream.write(f"$ {' '.join(argv)}\n")
    stream.flush()
    chunks: list[str] = []
    proc = subprocess.Popen(
        argv,
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        chunks.append(line)
        stream.write(line)
        stream.flush()
    proc.wait()
    return proc.returncode, "".join(chunks)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def probe_outcomes(
    entries: list[dict[str, Any]],
    *,
    workdir: Path,
    runner: Any = None,
) -> dict[str, str]:
    """Run each candidate baseline test alone and record what actually happened.

    A suite run tells us which tests FAILED, never which ones passed, so a
    baselined test that skipped is indistinguishable from one that was fixed.
    Re-running the candidates alone is the cheapest source of positive
    evidence, and there are only ever a handful: this costs seconds, which is
    what the "a stale baseline must fail in seconds" contract requires.

    Returns node id -> "passed" | "skipped" | "failed" | "missing".
    """
    run = runner or _probe_one
    out: dict[str, str] = {}
    for entry in entries:
        node = entry.get("test", "")
        if node:
            out[node] = run(node, workdir)
    return out


def _probe_one(node: str, workdir: Path) -> str:
    """Execute one node id and classify its outcome from pytest's own counters."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", node, "-q", "--no-header",
             "-p", "no:cacheprovider", "-rs"],
            cwd=str(workdir), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=300,
        )
    except Exception:  # noqa: BLE001 — an unprobeable test is not a passing test
        return "missing"
    text = proc.stdout
    if proc.returncode == 5 or "no tests ran" in text:
        return "missing"
    if re.search(r"\b\d+ failed", text) or re.search(r"\b\d+ error", text):
        return "failed"
    if re.search(r"\b\d+ skipped", text) and not re.search(r"\b\d+ passed", text):
        return "skipped"
    if re.search(r"\b\d+ passed", text):
        return "passed"
    return "missing"


def evaluate(
    *,
    rc: int,
    output: str,
    entries: list[dict[str, Any]],
    today: date,
    targets: Iterable[str],
) -> dict[str, Any]:
    """Map a finished pytest run onto a CI verdict. Pure — no IO, no subprocess.

    Returns ``{"exit_code", "reason", "newly_red", "baseline_red", "stale"}``.
    """
    targets = list(targets)
    # A skipped test is absent from the failed list for the same reason a
    # passing one is. Without this, any baselined test whose guard skips it on
    # the runner (no MLX, no Ollama, no GPU) is reported as a stale suppression
    # and the advice is to delete a still-red entry.
    skipped_files = parse_skipped_files(output)

    if rc == 0:
        return {
            "exit_code": EXIT_PASS,
            "reason": "test suite green",
            "newly_red": [],
            "baseline_red": [],
            "stale": stale_entries(entries, [], targets, skipped_files),
        }

    if rc != 1:
        # 2 = collection/interrupt, 3 = internal, 4 = usage, 5 = nothing collected.
        # All are real CI defects (see the module docstring's divergence note).
        return {
            "exit_code": EXIT_BLOCK_TESTS,
            "reason": (
                f"pytest exited {rc} — not a classifiable test failure "
                "(collection error, usage error, or no tests collected). CI treats "
                "this as a defect; the local pre-push gate fails open on it because "
                "only a developer env can produce it there."
            ),
            "newly_red": [],
            "baseline_red": [],
            "stale": [],
        }

    cls = classify_failures(output, entries, today)
    if not cls["failed"]:
        return {
            "exit_code": EXIT_BLOCK_TESTS,
            "reason": (
                "pytest reported failures (exit 1) but none could be parsed from the "
                "short summary, so they cannot be proven baselined. Unclassifiable is "
                "not 'no failures' — blocking."
            ),
            "newly_red": [],
            "baseline_red": [],
            "stale": [],
        }

    stale = stale_entries(entries, cls["failed"], targets, skipped_files)
    if cls["newly_red"]:
        return {
            "exit_code": EXIT_BLOCK_TESTS,
            "reason": (
                f"{len(cls['newly_red'])} newly-red test(s) not in {BASELINE_RELPATH}"
            ),
            "newly_red": cls["newly_red"],
            "baseline_red": cls["baseline_red"],
            "stale": stale,
        }

    return {
        "exit_code": EXIT_PASS,
        "reason": (
            f"{len(cls['baseline_red'])} failure(s), all covered by "
            f"{BASELINE_RELPATH} and unexpired"
        ),
        "newly_red": [],
        "baseline_red": cls["baseline_red"],
        "stale": stale,
    }


def render(verdict: dict[str, Any], *, out=None) -> None:
    """Print annotations + append the step summary for a verdict."""
    stream = out if out is not None else sys.stdout
    summary: list[str] = ["### Known-red baseline gate", ""]

    for node in verdict["newly_red"]:
        _annotate("error", f"NEWLY-RED: {node} — not in {BASELINE_RELPATH}. Fix it, "
                           "or add an owned, expiring baseline entry.", out=stream)
        summary.append(f"- **NEWLY-RED** `{node}` — blocks CI")

    for hit in verdict["baseline_red"]:
        e = hit["entry"]
        _annotate(
            "warning",
            f"KNOWN-RED (suppressed): {hit['node']} — owner={e['owner']} "
            f"expires={e['expires']} ({hit['days_left']}d left) — {e['reason']}",
            out=stream,
        )
        summary.append(
            f"- known-red `{hit['node']}` — owner `{e['owner']}`, "
            f"expires `{e['expires']}` ({hit['days_left']}d left)"
        )

    for item in verdict.get("stale_unproven", []):
        e, outcome = item["entry"], item["outcome"]
        _annotate(
            "warning",
            f"BASELINE NOT EXERCISED: {e['test']} did not run ({outcome}) — the "
            f"suppression is neither confirmed nor stale. Whatever the entry "
            f"describes is untested on this runner.",
            out=stream,
        )
        summary.append(f"- baseline `{e['test']}` not exercised ({outcome})")

    for e in verdict["stale"]:
        _annotate(
            "warning",
            f"STALE SUPPRESSION: {e['test']} now PASSES — delete this entry from "
            f"{BASELINE_RELPATH} (owner={e['owner']}, expires={e['expires']})",
            out=stream,
        )
        summary.append(f"- stale suppression `{e['test']}` now passes — delete the entry")

    if len(summary) == 2:
        summary.append("- clean: no failures, no suppressions in play")
    summary += ["", f"**Verdict:** {verdict['reason']}"]
    _summary(summary)
    stream.write(f"\nknown-red gate: {verdict['reason']}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply the known-red baseline to a CI pytest run.",
    )
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--target", action="append", dest="targets", default=None,
        help="Path prefix this run covers (repeatable). Only covered baseline "
             f"entries can be judged stale. Default: {' '.join(DEFAULT_TARGETS)}",
    )
    parser.add_argument(
        "--pytest-output", type=Path, default=None,
        help="Classify a previously captured run instead of executing pytest.",
    )
    parser.add_argument("--pytest-exit-code", type=int, default=None)
    parser.add_argument(
        "--today", type=str, default=None,
        help="Override today's date (YYYY-MM-DD) — test seam.",
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    workdir = args.workdir.resolve()
    targets = args.targets or list(DEFAULT_TARGETS)
    today = date.fromisoformat(args.today) if args.today else _today(workdir)

    if (args.pytest_output is None) != (args.pytest_exit_code is None):
        parser.error("--pytest-output and --pytest-exit-code must be used together")

    # --- baseline first: unusable or expired blocks BEFORE the suite runs ----
    # Same fail-safe posture as prepush_test_gate: a suppression list you cannot
    # read is not a suppression list, and an expiry you can ignore is not an
    # expiry. Checking first also means a stale baseline fails in seconds rather
    # than after a full suite.
    bl = load_baseline(workdir)
    if not bl.get("ok"):
        _annotate("error", f"known-red baseline unusable — {bl.get('error')}")
        print(
            f"known-red baseline unusable: {bl.get('error')}\n"
            f"expected at: {bl.get('path') or (workdir / BASELINE_RELPATH)}\n"
            "Every entry requires: " + ", ".join(_BASELINE_REQUIRED_FIELDS)
        )
        _summary([
            "### Known-red baseline gate",
            "",
            f"- **BLOCKED** baseline unusable: {bl.get('error')}",
        ])
        return EXIT_BLOCK_BASELINE

    entries = bl["entries"]
    expired = expired_entries(entries, today)
    if expired:
        lines = [
            f"{e['test']}  owner={e['owner']}  expired={e['expires']} "
            f"({(today - e['expires_date']).days}d ago)"
            for e in expired
        ]
        for line in lines:
            _annotate("error", f"EXPIRED known-red entry: {line}")
        print(
            f"{len(expired)} known-red baseline entry(ies) past expiry:\n"
            + "\n".join("  " + ln for ln in lines)
            + f"\n\nFix the test and DELETE the entry from {BASELINE_RELPATH}, or "
              "re-date it with a fresh reason and owner."
        )
        _summary(
            ["### Known-red baseline gate", ""]
            + [f"- **EXPIRED** `{ln}`" for ln in lines]
        )
        return EXIT_BLOCK_BASELINE

    # --- run (or ingest) the suite ------------------------------------------
    if args.pytest_output is not None:
        rc = int(args.pytest_exit_code)
        output = args.pytest_output.read_text(encoding="utf-8", errors="replace")
    else:
        pytest_args = list(args.pytest_args)
        if pytest_args and pytest_args[0] == "--":
            pytest_args = pytest_args[1:]
        if not pytest_args:
            parser.error("no pytest arguments given (pass them after `--`)")
        rc, output = run_pytest(pytest_args, workdir=workdir)

    verdict = evaluate(rc=rc, output=output, entries=entries, today=today, targets=targets)

    # evaluate() is pure, so its "stale" list is only "covered by the run and not
    # in the failure list" — which a skipped or uncollected test also satisfies.
    # Probe the candidates for positive evidence before telling anyone to delete
    # a suppression; a skip is not a fix.
    if verdict["stale"]:
        outcomes = probe_outcomes(verdict["stale"], workdir=workdir)
        verdict["stale"] = [
            entry for entry in verdict["stale"]
            if outcomes.get(entry.get("test", "")) == "passed"
        ]
        verdict["stale_unproven"] = [
            {"entry": entry, "outcome": outcomes.get(entry.get("test", ""), "missing")}
            for entry in entries
            if outcomes.get(entry.get("test", "")) not in (None, "passed")
        ]

    render(verdict)
    return int(verdict["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
