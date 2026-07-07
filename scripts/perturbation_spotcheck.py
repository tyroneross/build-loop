#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Isomorphic-perturbation spot-check for outcome-based verify gates. WARN-only.

Why this exists
---------------
Outcome-only verifiers get **Goodharted**: a gamed implementation can produce
"obfuscated enumeration" that passes the check without learning the rule, and a
plain pass/fail oracle cannot see it. The documented way to catch this is
*isomorphic perturbation testing* — re-run the same check under a
structure-preserving transformation (rename identifiers / reorder independent
inputs) and flag when the pass/fail flips. A legitimate outcome check is
invariant under an isomorphic perturbation; a gamed one is not. See
`build-loop-memory/research/2026-07-06-ai-coding-fundamentals-and-harness-claims.md`
(Claim 5 — false-success arXiv:2606.09863 + RLVR obfuscated-enumeration
arXiv:2604.15149; "execution is only as reliable as the oracle attached to it").

Scope
-----
Wired into Phase-4 Review-B as an advisory gate (outcome-based grader +
triggers.riskSurfaceChange). Self-contained, stdlib-only.
WARN-only by contract: a detected flip is advisory (it can be a false alarm when
the check legitimately depends on the perturbed dimension), so the default exit
code is 0. Pass ``--strict`` for a CI/test caller that wants exit 1 on a flip.

Two ways to use it
------------------
1. **Library** (deterministic, no subprocess) — import ``spotcheck`` /
   ``spotcheck_all`` with a ``Callable[[Any], bool]`` check and a value. Used by
   the colocated test and by any in-process advisory call site.
2. **CLI** — wrap an external check command. ``--check-cmd`` is a template with a
   ``{input}`` placeholder replaced by a path to the (original, then perturbed)
   input file; exit 0 from the command == pass. Reports any flip.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

# Identifier-like tokens; a conservative reserved set so renaming stays structure
# preserving for common source/text (renaming a keyword would change meaning).
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESERVED = frozenset(
    {
        # Python-ish / common language keywords + literals — never rename these.
        "def", "class", "return", "if", "elif", "else", "for", "while", "in",
        "and", "or", "not", "is", "None", "True", "False", "import", "from",
        "as", "with", "try", "except", "finally", "raise", "lambda", "pass",
        "break", "continue", "yield", "global", "nonlocal", "assert", "del",
        "function", "const", "let", "var", "new", "this", "void", "int", "str",
        "bool", "float", "list", "dict", "set", "self", "print",
    }
)


def rename_identifiers(text: str, *, reserved: Iterable[str] | None = None) -> str:
    """Consistently rename identifier tokens to fresh ``id_N`` names (isomorphic).

    Deterministic: identifiers are numbered in first-appearance order, and every
    occurrence of a name maps to the same replacement, so structure/wiring is
    preserved and only the names change. Reserved keywords are left untouched.
    """
    reserved_set = _RESERVED | frozenset(reserved or ())
    mapping: dict[str, str] = {}

    def repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        if tok in reserved_set:
            return tok
        if tok not in mapping:
            mapping[tok] = f"id_{len(mapping)}"
        return mapping[tok]

    return _IDENT_RE.sub(repl, text)


def reorder_sequence(items: list[Any], *, strategy: str = "reverse") -> list[Any]:
    """Reorder an independent-input sequence (order should not change the outcome).

    strategy: ``reverse`` (default) or ``rotate`` (move the first item to the end).
    A single-element or empty sequence is returned unchanged — nothing to perturb.
    """
    if len(items) < 2:
        return list(items)
    if strategy == "rotate":
        return items[1:] + items[:1]
    if strategy == "reverse":
        return list(reversed(items))
    raise ValueError(f"unknown reorder strategy: {strategy!r}")


@dataclass
class SpotcheckResult:
    """Outcome of one perturbation comparison."""

    perturbation: str
    original_pass: bool
    perturbed_pass: bool
    detail: str = ""

    @property
    def flipped(self) -> bool:
        return self.original_pass != self.perturbed_pass

    def as_dict(self) -> dict:
        return {
            "perturbation": self.perturbation,
            "original_pass": self.original_pass,
            "perturbed_pass": self.perturbed_pass,
            "flipped": self.flipped,
            "detail": self.detail,
        }


def spotcheck(
    check: Callable[[Any], bool],
    value: Any,
    *,
    perturb: Callable[[Any], Any],
    label: str,
) -> SpotcheckResult:
    """Run ``check`` on ``value`` and on ``perturb(value)``; report whether it flips."""
    original = bool(check(value))
    perturbed_value = perturb(value)
    perturbed = bool(check(perturbed_value))
    return SpotcheckResult(perturbation=label, original_pass=original, perturbed_pass=perturbed)


def spotcheck_all(
    check: Callable[[Any], bool],
    value: Any,
    *,
    perturbations: dict[str, Callable[[Any], Any]] | None = None,
) -> list[SpotcheckResult]:
    """Run a suite of perturbations. Default suite = identifier-rename (for str values).

    Returns one SpotcheckResult per perturbation. Callers treat any ``flipped``
    result as a WARN (possible gamed/overfit check), never a hard failure.
    """
    if perturbations is None:
        perturbations = {"rename_identifiers": rename_identifiers}
    return [spotcheck(check, value, perturb=fn, label=name) for name, fn in perturbations.items()]


# ---------------------------------------------------------------------------
# CLI: wrap an external check command
# ---------------------------------------------------------------------------

def _run_check_cmd(cmd_template: str, input_path: Path, timeout: int) -> bool:
    """Return True when the check command exits 0 for the given input file."""
    cmd = cmd_template.replace("{input}", str(input_path))
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def _cli_spotcheck(text: str, mode: str, cmd_template: str, timeout: int) -> SpotcheckResult:
    if mode == "rename":
        perturbed = rename_identifiers(text)
        label = "rename_identifiers"
    elif mode == "reorder":
        # Treat the input as newline-separated independent records.
        perturbed = "\n".join(reorder_sequence(text.splitlines()))
        label = "reorder_lines"
    else:  # pragma: no cover - argparse constrains choices
        raise ValueError(f"unknown mode: {mode!r}")

    with tempfile.TemporaryDirectory() as td:
        orig_path = Path(td) / "original.txt"
        pert_path = Path(td) / "perturbed.txt"
        orig_path.write_text(text, encoding="utf-8")
        pert_path.write_text(perturbed, encoding="utf-8")
        original = _run_check_cmd(cmd_template, orig_path, timeout)
        perturbed_pass = _run_check_cmd(cmd_template, pert_path, timeout)
    return SpotcheckResult(perturbation=label, original_pass=original, perturbed_pass=perturbed_pass)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--check-cmd",
        required=True,
        help="Check command template; '{input}' is replaced by a path to the input file. Exit 0 == pass.",
    )
    p.add_argument("--input", required=True, help="Path to the input file (or '-' for stdin).")
    p.add_argument("--mode", choices=["rename", "reorder"], default="rename",
                   help="Isomorphic perturbation to apply (default: rename identifiers).")
    p.add_argument("--timeout", type=int, default=60, help="Per-run timeout seconds (default 60).")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 on a detected flip (default: WARN-only, exit 0).")
    args = p.parse_args(argv)

    text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    result = _cli_spotcheck(text, args.mode, args.check_cmd, args.timeout)

    if result.flipped:
        sys.stderr.write(
            f"[perturbation_spotcheck] WARN: pass flipped under {result.perturbation} "
            f"(original_pass={result.original_pass}, perturbed_pass={result.perturbed_pass}) — "
            "the check may be gamed/overfit (outcome not invariant under an isomorphic perturbation). "
            "Inspect the oracle before trusting the green.\n"
        )
        return 1 if args.strict else 0
    sys.stderr.write(
        f"[perturbation_spotcheck] ok: no flip under {result.perturbation} "
        f"(both {'pass' if result.original_pass else 'fail'})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
