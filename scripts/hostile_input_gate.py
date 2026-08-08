#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Review gate for commits that claim to close a security/safety finding.

Observed failure this closes: a security guard was written to refuse writing
to a user's live store. Its test asserted exactly three things read off the
implementation's branches -- an in-repo target refused, a default-with-flag-
absent refused, an outside path returned absolute -- and never once called
the guard with the live-store path itself: the literal hostile input the
finding named. A test written by reading the implementation enumerates the
branches that exist; a test written from the threat asks what the attacker
types.

Two checks, composable:

  check   -- assert the finding's literal hostile input(s) actually appear
             in the commit's test files. Fails loud (exit 1) when a test
             suite is green but never exercises the named attack.
  mutate  -- plant a mutant that disables the named guard function and
             confirm the given test command goes red. A guard whose tests
             stay green when the guard is disabled is not tested (exit 1).

Pure stdlib. No network, no third-party imports.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# check_hostile_input_present
# ---------------------------------------------------------------------------

_COMMON_PATH_WORDS = {
    "tmp", "var", "usr", "home", "users", "library", "private", "etc",
    "bin", "local", "opt", "data", "files", "folder", "user",
    "documents", "desktop", "downloads", "application", "support",
}


def _normalize(text: str) -> str:
    """Collapse whitespace and strip a single pair of surrounding quotes."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in "'\"":
        normalized = normalized[1:-1]
    return normalized


def _distinctive_token(hostile_input: str) -> Optional[str]:
    """Longest non-trivial path segment or token (>=6 chars, not a common word)."""
    normalized = _normalize(hostile_input)
    segments = [seg for seg in re.split(r"[\\/]+", normalized) if seg]
    candidates = [
        seg for seg in segments
        if len(seg) >= 6 and seg.lower() not in _COMMON_PATH_WORDS
    ]
    if not candidates:
        tokens = [t for t in re.split(r"[^A-Za-z0-9_]+", normalized) if t]
        candidates = [
            t for t in tokens
            if len(t) >= 6 and t.lower() not in _COMMON_PATH_WORDS
        ]
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def _locate_normalized_line(lines: list[str], normalized_target: str) -> Optional[int]:
    """Best-effort line number for a whole-file normalized match."""
    threshold = min(6, len(normalized_target))
    if threshold <= 0:
        return None
    best_line, best_size = None, 0
    for i, line in enumerate(lines, start=1):
        norm_line = _normalize(line)
        matcher = difflib.SequenceMatcher(None, norm_line, normalized_target)
        match = matcher.find_longest_match(0, len(norm_line), 0, len(normalized_target))
        if match.size > best_size:
            best_size, best_line = match.size, i
    return best_line if best_size >= threshold else None


def _check_single_input(hostile_input: str, test_files: list[str]) -> dict:
    normalized_target = _normalize(hostile_input)
    distinctive = _distinctive_token(hostile_input)

    for test_file in test_files:
        try:
            text = Path(test_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()

        # 1. Literal substring, line-by-line (gives an exact matched_in line).
        for i, line in enumerate(lines, start=1):
            if hostile_input in line:
                return {
                    "input": hostile_input,
                    "present": True,
                    "matched_via": "literal",
                    "matched_in": f"{test_file}:{i}",
                }

        # 2. Normalized whole-file substring (handles split f-strings / quote
        #    style differences that a per-line literal search misses).
        if normalized_target:
            normalized_content = _normalize(text)
            if normalized_target in normalized_content:
                line_no = _locate_normalized_line(lines, normalized_target) or 1
                return {
                    "input": hostile_input,
                    "present": True,
                    "matched_via": "normalized",
                    "matched_in": f"{test_file}:{line_no}",
                }

        # 3. Distinctive path segment / token -- weak match, reported as such.
        if distinctive:
            for i, line in enumerate(lines, start=1):
                if distinctive in line:
                    return {
                        "input": hostile_input,
                        "present": True,
                        "matched_via": "distinctive_token",
                        "matched_in": f"{test_file}:{i}",
                    }

    return {
        "input": hostile_input,
        "present": False,
        "matched_via": None,
        "matched_in": None,
    }


def check_hostile_input_present(hostile_inputs: list[str], test_files: list[str]) -> dict:
    """Assert each hostile input literally (or near-literally) appears in test_files."""
    results = [_check_single_input(hi, test_files) for hi in hostile_inputs]
    absent = [r["input"] for r in results if not r["present"]]
    all_present = not absent
    return {
        "hostile_inputs": results,
        "all_present": all_present,
        "verdict": "hostile_input_covered" if all_present else "hostile_input_absent",
        "absent": absent,
    }


# ---------------------------------------------------------------------------
# mutant_turns_tests_red
# ---------------------------------------------------------------------------

def _first_param_name(params_str: str) -> Optional[str]:
    for raw in params_str.split(","):
        token = raw.strip().lstrip("*")
        token = token.split("=")[0].split(":")[0].strip()
        if token and token not in ("self", "cls"):
            return token
    return None


def _plant_mutant_python(source_text: str, guard_symbol: str) -> Optional[str]:
    lines = source_text.splitlines(keepends=True)
    pattern = re.compile(r"^(?P<indent>[ \t]*)def\s+" + re.escape(guard_symbol) + r"\s*\(")
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        indent = m.group("indent")
        sig_end_idx = i
        while not lines[sig_end_idx].rstrip().endswith(":"):
            sig_end_idx += 1
            if sig_end_idx >= len(lines):
                raise ValueError(f"could not find end of signature for {guard_symbol!r}")
        sig_text = "".join(lines[i:sig_end_idx + 1])
        params_match = re.search(r"\((.*)\)\s*:\s*$", sig_text, re.S)
        params_str = params_match.group(1) if params_match else ""
        first_param = _first_param_name(params_str) if params_str.strip() else None
        permissive = first_param if first_param else "True"
        body_indent = indent + "    "
        insertion = f"{body_indent}return {permissive}  # HOSTILE_INPUT_GATE_MUTANT\n"
        new_lines = lines[:sig_end_idx + 1] + [insertion] + lines[sig_end_idx + 1:]
        return "".join(new_lines)
    return None


def _plant_mutant_js(source_text: str, guard_symbol: str) -> Optional[str]:
    pattern = re.compile(r"function\s+" + re.escape(guard_symbol) + r"\s*\(([^)]*)\)\s*\{")
    m = pattern.search(source_text)
    if not m:
        return None
    first_param = _first_param_name(m.group(1)) if m.group(1).strip() else None
    permissive = first_param if first_param else "true"
    insertion_point = m.end()
    return (
        source_text[:insertion_point]
        + f"\n  return {permissive}; // HOSTILE_INPUT_GATE_MUTANT\n"
        + source_text[insertion_point:]
    )


def _plant_mutant(source_text: str, guard_symbol: str) -> tuple[str, str]:
    """Return (mutated_text, language). Raises ValueError if symbol not found."""
    mutated = _plant_mutant_python(source_text, guard_symbol)
    if mutated is not None:
        return mutated, "python"
    mutated = _plant_mutant_js(source_text, guard_symbol)
    if mutated is not None:
        return mutated, "best_effort"
    raise ValueError(f"guard symbol {guard_symbol!r} not found in guard file")


#: Bound the mutant run. A guard whose test hangs under mutation would otherwise
#: leave the mutant on disk for the length of the hang; the finally-block restore
#: cannot run until the child returns. A timeout converts that into a reported
#: `mutant_run_timeout` with the file already restored.
MUTANT_TEST_TIMEOUT_SEC = 600


def _run_test_cmd(
    test_cmd: str, cwd: str, timeout: int | None = None
) -> subprocess.CompletedProcess:
    """Run the caller's test command for the mutation arm.

    `shell=True` is deliberate and the input is trusted by contract: `test_cmd`
    is the reviewer's own test invocation (`python3 scripts/test_x.py`,
    `cd sub && npm test`), supplied by the orchestrator or a human at the
    command line — the same trust boundary as `staged_content_gate.py --run` and
    `verification_claim_probe.py`, both of which need a shell for `&&`, env
    prefixes, and redirection that a bare argv list cannot express. This is a
    dev-time gate, not a network- or model-facing surface: nothing here parses
    untrusted or LLM-generated text into the command. The command is never built
    by string concatenation with scanned content — it is passed through verbatim.
    """
    # Resolve the module constant at CALL time, not as a default argument: a
    # default binds once at def time, so a caller (or a test) that adjusts
    # MUTANT_TEST_TIMEOUT_SEC would otherwise be silently ignored.
    effective = MUTANT_TEST_TIMEOUT_SEC if timeout is None else timeout
    return subprocess.run(  # nosec: operator-supplied test command; see docstring for the trust boundary
        test_cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=effective
    )


def mutant_turns_tests_red(
    guard_file: str,
    guard_symbol: str,
    test_cmd: str,
    repo: Optional[str] = None,
) -> dict:
    """Plant a permissive mutant of guard_symbol in guard_file and check test_cmd.

    Always restores the original file bytes, even on error, and reports
    whether that restore actually landed.
    """
    guard_path = Path(guard_file)
    original_bytes = guard_path.read_bytes()
    cwd = repo or str(guard_path.resolve().parent)
    result: dict = {"baseline": {}, "mutant": {}, "verdict": None, "restored": False}

    try:
        try:
            baseline_proc = _run_test_cmd(test_cmd, cwd)
        except subprocess.TimeoutExpired:
            # Fail-safe: a run we could not observe is never evidence the guard
            # is tested. Never fall through to `mutant_convicted` on a timeout.
            result["verdict"] = "mutant_run_timeout"
            result["timed_out"] = "baseline"
            return result
        result["baseline"] = {"returncode": baseline_proc.returncode}
        if baseline_proc.returncode != 0:
            result["verdict"] = "baseline_red"
            return result

        mutated_text, language = _plant_mutant(original_bytes.decode("utf-8"), guard_symbol)
        result["language"] = language
        guard_path.write_text(mutated_text, encoding="utf-8")

        try:
            mutant_proc = _run_test_cmd(test_cmd, cwd)
        except subprocess.TimeoutExpired:
            result["verdict"] = "mutant_run_timeout"
            result["timed_out"] = "mutant"
            return result
        result["mutant"] = {"returncode": mutant_proc.returncode}
        result["verdict"] = "mutant_survived" if mutant_proc.returncode == 0 else "mutant_convicted"
        return result
    finally:
        try:
            guard_path.write_bytes(original_bytes)
            restored_bytes = guard_path.read_bytes()
            result["restored"] = restored_bytes == original_bytes
            if not result["restored"]:
                result["restore_failed"] = True
        except OSError:
            result["restored"] = False
            result["restore_failed"] = True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hostile_input_gate.py",
        description="Review gate: a security-finding test must contain the named hostile input.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="Assert hostile inputs appear in the given test files")
    check_p.add_argument("--hostile-input", action="append", dest="hostile_inputs", required=True)
    check_p.add_argument("--test-file", action="append", dest="test_files", required=True)
    check_p.add_argument("--repo", default=None)
    check_p.add_argument("--json", action="store_true")

    mutate_p = sub.add_parser("mutate", help="Plant a permissive mutant and confirm test_cmd goes red")
    mutate_p.add_argument("--guard-file", required=True)
    mutate_p.add_argument("--guard-symbol", required=True)
    mutate_p.add_argument("--test-cmd", required=True)
    mutate_p.add_argument("--repo", default=None)
    mutate_p.add_argument("--json", action="store_true")

    return parser


def _emit(result: dict, as_json: bool) -> None:
    print(json.dumps(result) if as_json else json.dumps(result, indent=2))


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "check":
        test_files = args.test_files
        if args.repo:
            test_files = [
                tf if os.path.isabs(tf) else os.path.join(args.repo, tf)
                for tf in test_files
            ]
        result = check_hostile_input_present(args.hostile_inputs, test_files)
        _emit(result, args.json)
        return 0 if result["all_present"] else 1

    if args.command == "mutate":
        result = mutant_turns_tests_red(
            args.guard_file, args.guard_symbol, args.test_cmd, repo=args.repo
        )
        _emit(result, args.json)
        if result["verdict"] in ("baseline_red", "mutant_run_timeout"):
            # A run we could not observe is not a pass. Exit 0 here would let a
            # hung test suite read as "mutant convicted, finding closed" — the
            # same fail-open shape this gate exists to catch.
            return 2
        if result["verdict"] == "mutant_survived":
            return 1
        return 0

    return 2  # pragma: no cover - argparse enforces a valid subcommand


if __name__ == "__main__":
    sys.exit(main())
