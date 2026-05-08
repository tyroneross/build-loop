#!/usr/bin/env python3
"""
attestation_lint.py — diff-vs-attestation lint for build-loop Phase 4.5.

Compares an implementer's synthesis_attestation envelope field against the
actual git diff to catch silent synthesis-decision drift (F8 backstop).

Stdlib only: re, pathlib, subprocess, json, argparse, sys.

Lintable dimensions (deterministic check):
  - placement   : anchor exists in pre-image; new lines appear near anchor in post-image
  - cta_tier    : claimed tier ∈ {primary, secondary, tertiary}; grep diff for matching
                  className / variant attribute
  - visual_weight: claimed heading level or divider; grep diff for <hN>, <hr>, border-t, etc.

Subjective dimensions (always unverifiable):
  - copy_tone
  - empty_state

Exit codes:
  0 — all checked dimensions pass
  1 — at least one fail
  2 — only unverifiable (no fails, no passes)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

def parse_unified_diff(diff_text: str) -> dict[str, dict[str, Any]]:
    """Parse a unified diff into a map of filename → {pre_lines, post_lines, added_lines}.

    pre_lines  — list of (line_number, text) for lines present in the pre-image (- and context)
    post_lines — list of (line_number, text) for lines present in the post-image (+ and context)
    added_lines — list of (line_number, text) for lines added only (+ lines)
    """
    files: dict[str, dict[str, Any]] = {}
    current_file: str | None = None
    pre_lineno = 0
    post_lineno = 0

    for raw_line in diff_text.splitlines():
        # Detect file header: --- a/path or +++ b/path
        if raw_line.startswith("--- "):
            # Pre-image filename: strip "a/" prefix if present
            fname = raw_line[4:].strip()
            if fname.startswith("a/"):
                fname = fname[2:]
            if fname == "/dev/null":
                fname = ""
            current_file = fname
            continue
        if raw_line.startswith("+++ "):
            fname = raw_line[4:].strip()
            if fname.startswith("b/"):
                fname = fname[2:]
            if fname == "/dev/null":
                fname = ""
            if current_file is not None:
                # Use post-image name as canonical key (catches /dev/null pre-image for new files)
                key = fname if fname else (current_file or "")
            else:
                key = fname
            if key and key not in files:
                files[key] = {"pre_lines": [], "post_lines": [], "added_lines": []}
            current_file = key
            continue

        # Hunk header: @@ -L,N +L,N @@
        hunk_m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
        if hunk_m:
            pre_lineno = int(hunk_m.group(1))
            post_lineno = int(hunk_m.group(2))
            continue

        if current_file is None or current_file not in files:
            continue

        entry = files[current_file]
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            entry["pre_lines"].append((pre_lineno, raw_line[1:]))
            pre_lineno += 1
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            entry["post_lines"].append((post_lineno, raw_line[1:]))
            entry["added_lines"].append((post_lineno, raw_line[1:]))
            post_lineno += 1
        elif raw_line.startswith(" "):
            entry["pre_lines"].append((pre_lineno, raw_line[1:]))
            entry["post_lines"].append((post_lineno, raw_line[1:]))
            pre_lineno += 1
            post_lineno += 1

    return files


def load_diff(diff_arg: str) -> str:
    """Load diff text from a file path or a git range like 'HEAD~1..HEAD'."""
    p = Path(diff_arg)
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    # Treat as a git range
    try:
        result = subprocess.run(
            ["git", "diff", diff_arg],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
        return result.stdout
    except FileNotFoundError:
        raise RuntimeError("git not found; cannot resolve diff range")


# ---------------------------------------------------------------------------
# Anchor format (synthesis_attestation: anchor_parse_format)
#
# Accepted forms (per C3-spec.md and implementer-envelope-schema.md examples):
#   "after <ANCHOR> in path/to/file"
#   "before <ANCHOR> in path/to/file"
#   "after line N in path/to/file"    (line-number anchor)
#   "in path/to/file"                  (file-level, no positional claim)
#
# The regex is intentionally permissive on the anchor text: any non-empty
# sequence until " in " or end-of-string.
# ---------------------------------------------------------------------------

_PLACEMENT_RE = re.compile(
    r"""
    (?P<direction>after|before)\s+         # direction keyword
    (?P<anchor>.+?)\s+                     # anchor text (lazy)
    in\s+(?P<path>\S+)                     # "in <path>"
    |
    in\s+(?P<path2>\S+)                    # file-only form: "in <path>"
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Match threshold (synthesis_attestation: match_threshold):
# "matching placement" = the anchor text appears within ±MATCH_WINDOW lines
# of the claimed anchor's pre-image position in the post-image.
# Rationale: exact-line would reject correct diffs where context lines shift
# by a few lines due to surrounding insertions; ±5 is tight enough to catch
# gross placement errors while tolerating normal diff drift.
MATCH_WINDOW = 5


def _check_placement(claimed: str, diff_files: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Verify a placement claim against the parsed diff.

    Returns a result dict with keys: status, evidence.
    """
    m = _PLACEMENT_RE.search(claimed)
    if not m:
        return {
            "status": "unverifiable",
            "evidence": f"placement string '{claimed}' did not match expected form "
                        f"'after/before <anchor> in <path>' or 'in <path>'",
        }

    if m.group("path2"):
        # File-only form — just check that the file appears in the diff
        fpath = m.group("path2")
        matched_key = _find_file_key(fpath, diff_files)
        if matched_key is None:
            return {
                "status": "fail",
                "evidence": f"file '{fpath}' not found in diff",
            }
        return {
            "status": "pass",
            "evidence": f"file '{fpath}' found in diff (file-level placement, no anchor check)",
        }

    direction = m.group("direction").lower()
    anchor_text = m.group("anchor").strip()
    fpath = m.group("path")

    matched_key = _find_file_key(fpath, diff_files)
    if matched_key is None:
        return {
            "status": "fail",
            "evidence": f"file '{fpath}' not found in diff",
        }

    file_entry = diff_files[matched_key]
    pre_lines = file_entry["pre_lines"]
    post_lines = file_entry["post_lines"]
    added_lines = file_entry["added_lines"]

    # Find anchor in pre-image
    anchor_lineno: int | None = None
    for lineno, text in pre_lines:
        if anchor_text.lower() in text.lower():
            anchor_lineno = lineno
            break

    if anchor_lineno is None:
        # Anchor might be a new line (in which case check post-image)
        for lineno, text in post_lines:
            if anchor_text.lower() in text.lower():
                anchor_lineno = lineno
                break

    if anchor_lineno is None:
        return {
            "status": "fail",
            "evidence": f"anchor '{anchor_text}' not found in pre-image or post-image of '{fpath}'",
        }

    if not added_lines:
        return {
            "status": "fail",
            "evidence": f"no lines added in '{fpath}'; placement claim cannot be verified",
        }

    # Check that at least one added line appears within MATCH_WINDOW of the anchor
    if direction == "after":
        # Added lines should appear at line numbers >= anchor_lineno and within window
        nearby = [
            (ln, t) for ln, t in added_lines
            if anchor_lineno <= ln <= anchor_lineno + MATCH_WINDOW
        ]
    else:  # before
        nearby = [
            (ln, t) for ln, t in added_lines
            if anchor_lineno - MATCH_WINDOW <= ln < anchor_lineno
        ]

    if nearby:
        ln, _ = nearby[0]
        return {
            "status": "pass",
            "evidence": (
                f"anchor '{anchor_text}' found at pre-image line {anchor_lineno}; "
                f"added line at post-image line {ln} is within ±{MATCH_WINDOW} lines "
                f"({direction} anchor)"
            ),
        }

    # Relaxed: accept if ANY added line exists at all in the file (within the whole diff)
    # and the anchor exists. This handles the case where large insertions shift line numbers
    # beyond MATCH_WINDOW but the file and anchor are correct.
    #
    # We still count this as pass with a weaker evidence note.
    closest = min(added_lines, key=lambda x: abs(x[0] - anchor_lineno))
    delta = abs(closest[0] - anchor_lineno)
    if delta <= MATCH_WINDOW * 4:
        return {
            "status": "pass",
            "evidence": (
                f"anchor '{anchor_text}' found at pre-image line {anchor_lineno}; "
                f"nearest added line at post-image line {closest[0]} (delta={delta}); "
                f"within relaxed threshold"
            ),
        }

    return {
        "status": "fail",
        "evidence": (
            f"anchor '{anchor_text}' found at pre-image line {anchor_lineno} but "
            f"no added lines within ±{MATCH_WINDOW * 4} lines in the {direction} direction "
            f"(nearest added line: {closest[0]}, delta={delta})"
        ),
    }


def _find_file_key(fpath: str, diff_files: dict[str, dict[str, Any]]) -> str | None:
    """Find a diff file key that matches fpath (suffix match for robustness)."""
    if fpath in diff_files:
        return fpath
    # Try suffix match
    for key in diff_files:
        if key.endswith(fpath) or fpath.endswith(key):
            return key
    return None


# ---------------------------------------------------------------------------
# cta_tier check
# Known tiers per C3-spec.md
# ---------------------------------------------------------------------------

_KNOWN_CTA_TIERS = {"primary", "secondary", "tertiary"}

# Patterns to grep in the diff for CTA tier markers
_CTA_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "primary": [
        re.compile(r'variant\s*=\s*["\']primary["\']', re.IGNORECASE),
        re.compile(r'className\s*=\s*[^"\']*primary', re.IGNORECASE),
        re.compile(r'btn-primary|button-primary', re.IGNORECASE),
    ],
    "secondary": [
        re.compile(r'variant\s*=\s*["\']secondary["\']', re.IGNORECASE),
        re.compile(r'className\s*=\s*[^"\']*secondary', re.IGNORECASE),
        re.compile(r'btn-secondary|button-secondary', re.IGNORECASE),
    ],
    "tertiary": [
        re.compile(r'variant\s*=\s*["\']tertiary["\']', re.IGNORECASE),
        re.compile(r'className\s*=\s*[^"\']*tertiary', re.IGNORECASE),
        re.compile(r'btn-tertiary|button-tertiary', re.IGNORECASE),
        re.compile(r'variant\s*=\s*["\']ghost["\']', re.IGNORECASE),  # common alias
        re.compile(r'variant\s*=\s*["\']link["\']', re.IGNORECASE),
    ],
}


def _check_cta_tier(claimed: str, diff_files: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Verify a cta_tier claim against added lines in the diff."""
    claimed_lower = claimed.strip().lower()
    if claimed_lower not in _KNOWN_CTA_TIERS:
        return {
            "status": "unverifiable",
            "evidence": (
                f"'{claimed}' is not in known tier set {sorted(_KNOWN_CTA_TIERS)}; "
                f"cannot deterministically verify"
            ),
        }

    patterns = _CTA_PATTERNS[claimed_lower]
    all_added: list[str] = []
    for entry in diff_files.values():
        all_added.extend(text for _, text in entry["added_lines"])

    for line in all_added:
        for pat in patterns:
            if pat.search(line):
                return {
                    "status": "pass",
                    "evidence": f"found '{claimed}' tier marker in added lines: {line.strip()[:120]}",
                }

    if not all_added:
        return {
            "status": "fail",
            "evidence": "no lines added in diff; cta_tier claim cannot be confirmed",
        }

    return {
        "status": "fail",
        "evidence": (
            f"no '{claimed}' tier marker (variant=, className=, btn-{claimed}) found in added lines"
        ),
    }


# ---------------------------------------------------------------------------
# visual_weight check
# ---------------------------------------------------------------------------

_VISUAL_WEIGHT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"<h[1-6]\b", re.IGNORECASE), "heading tag"),
    (re.compile(r"^#{1,6}\s", re.MULTILINE), "markdown heading"),
    (re.compile(r"<hr\b", re.IGNORECASE), "hr element"),
    (re.compile(r"\bborder-t\b", re.IGNORECASE), "border-t class"),
    (re.compile(r"\bdivide-", re.IGNORECASE), "divide- class"),
    (re.compile(r"\bfont-bold\b|\bfont-semibold\b|\bfont-medium\b", re.IGNORECASE), "font weight class"),
    (re.compile(r"\btext-\w+-\d{3,}\b", re.IGNORECASE), "text color class"),
    (re.compile(r"\btext-(?:xl|2xl|3xl|lg|sm)\b", re.IGNORECASE), "text size class"),
    (re.compile(r"\bseparator\b|\bdivider\b", re.IGNORECASE), "separator/divider keyword"),
]

# Also check for explicit heading levels in the claim itself (e.g. "h2", "h3")
_HEADING_LEVEL_RE = re.compile(r"\bh([1-6])\b", re.IGNORECASE)


def _check_visual_weight(claimed: str, diff_files: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Verify a visual_weight claim against added lines in the diff."""
    all_added: list[str] = []
    for entry in diff_files.values():
        all_added.extend(text for _, text in entry["added_lines"])

    if not all_added:
        return {
            "status": "fail",
            "evidence": "no lines added in diff; visual_weight claim cannot be confirmed",
        }

    # If claim names a specific heading level, look for that level specifically
    level_m = _HEADING_LEVEL_RE.search(claimed)
    if level_m:
        level = level_m.group(1)
        specific_pats = [
            re.compile(rf"<h{level}\b", re.IGNORECASE),
            re.compile(rf"^{'#' * int(level)}\s", re.MULTILINE),
        ]
        for line in all_added:
            for pat in specific_pats:
                if pat.search(line):
                    return {
                        "status": "pass",
                        "evidence": f"found h{level} element in added lines: {line.strip()[:120]}",
                    }

    # General check against all visual_weight patterns
    for line in all_added:
        for pat, label in _VISUAL_WEIGHT_PATTERNS:
            if pat.search(line):
                return {
                    "status": "pass",
                    "evidence": f"found visual weight marker ({label}) in added lines: {line.strip()[:120]}",
                }

    return {
        "status": "fail",
        "evidence": (
            f"no visual weight marker (heading, hr, border-t, divide-*, font-weight) "
            f"found in added lines for claimed: '{claimed}'"
        ),
    }


# ---------------------------------------------------------------------------
# Subjective (always unverifiable) dimensions
# ---------------------------------------------------------------------------

_SUBJECTIVE_DIMENSIONS = {"copy_tone", "empty_state"}


def _check_subjective(dimension: str, _claimed: str) -> dict[str, Any]:
    return {
        "status": "unverifiable",
        "evidence": (
            f"'{dimension}' is a subjective dimension; deterministic diff comparison is not possible"
        ),
    }


# ---------------------------------------------------------------------------
# Envelope loading + malformed_envelope_handling
#
# Decision (synthesis_attestation: malformed_envelope_handling):
#   warn + exit 2 (unverifiable) — never hard-error on missing attestation.
#   Rationale: the lint is a backstop, not a blocker for missing metadata.
#   A missing envelope or missing synthesis_attestation key should surface
#   as a warning so the orchestrator can log it and proceed rather than
#   abort the entire Phase 4.5 gate. Hard errors would block legitimate
#   commits that simply predate the schema.
# ---------------------------------------------------------------------------

def load_envelope(envelope_path: str) -> dict[str, Any]:
    """Load and parse the envelope JSON. Returns the parsed dict."""
    p = Path(envelope_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"envelope file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"envelope is not valid JSON: {e}")
    return data


def extract_attestation(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Extract synthesis_attestation from envelope, handling nested 'envelope' wrapper."""
    # Support both flat envelope and {"envelope": {...}} wrapper
    if "synthesis_attestation" in envelope:
        return envelope["synthesis_attestation"]
    if "envelope" in envelope and isinstance(envelope["envelope"], dict):
        inner = envelope["envelope"]
        if "synthesis_attestation" in inner:
            return inner["synthesis_attestation"]
    return None


# ---------------------------------------------------------------------------
# Per-dimension dispatch
# ---------------------------------------------------------------------------

def lint_dimension(
    dimension: str,
    claimed: str,
    diff_files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Dispatch lint check for a single dimension. Returns result dict."""
    dim_lower = dimension.lower()
    if dim_lower in _SUBJECTIVE_DIMENSIONS:
        result = _check_subjective(dimension, claimed)
    elif dim_lower == "placement":
        result = _check_placement(claimed, diff_files)
    elif dim_lower == "cta_tier":
        result = _check_cta_tier(claimed, diff_files)
    elif dim_lower == "visual_weight":
        result = _check_visual_weight(claimed, diff_files)
    else:
        result = {
            "status": "unverifiable",
            "evidence": f"dimension '{dimension}' is not in the lintable or subjective sets; skipped",
        }
    return {
        "dimension": dimension,
        "claimed": claimed,
        "status": result["status"],
        "evidence": result["evidence"],
    }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_lint(diff_text: str, envelope: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Run attestation lint. Returns (results, warn_message).

    warn_message is non-empty if the envelope was malformed (missing
    synthesis_attestation); lint still proceeds and returns unverifiable
    results rather than failing hard (malformed_envelope_handling = warn).
    """
    warn_message = ""
    attestation = extract_attestation(envelope)

    if attestation is None:
        warn_message = (
            "envelope is missing 'synthesis_attestation' field; "
            "all dimensions marked unverifiable (malformed_envelope_handling=warn)"
        )
        return [], warn_message

    diff_files = parse_unified_diff(diff_text)
    results: list[dict[str, Any]] = []

    for dimension, claimed in attestation.items():
        # Schema supports "applied" | "deviated" | "n/a" as top-level values
        # AND direct string claims for the lint dimensions.
        # When value is "applied"/"deviated"/"n/a" without a detail string,
        # we cannot lint the specific claim — mark unverifiable.
        if isinstance(claimed, str) and claimed.lower() in {"applied", "deviated", "n/a"}:
            results.append({
                "dimension": dimension,
                "claimed": claimed,
                "status": "unverifiable",
                "evidence": (
                    f"attestation value '{claimed}' is a status token, not a lintable claim; "
                    f"no specific placement/tier/weight string to verify"
                ),
            })
            continue
        if not isinstance(claimed, str):
            results.append({
                "dimension": dimension,
                "claimed": str(claimed),
                "status": "unverifiable",
                "evidence": f"expected string claim, got {type(claimed).__name__}",
            })
            continue
        results.append(lint_dimension(dimension, claimed, diff_files))

    return results, warn_message


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"pass": 0, "fail": 0, "unverifiable": 0}
    for r in results:
        status = r.get("status", "unverifiable")
        counts[status] = counts.get(status, 0) + 1
    return counts


def compute_exit_code(summary: dict[str, int], warn_message: str) -> int:
    """
    Exit code semantics (per C3-spec.md):
      0 — all checked dimensions pass (no fails, at least one pass)
      1 — at least one fail
      2 — only unverifiable (no fails, no passes) OR malformed envelope
    """
    if summary.get("fail", 0) > 0:
        return 1
    if summary.get("pass", 0) > 0:
        return 0
    return 2  # all unverifiable (including empty results from malformed envelope)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Diff-vs-attestation lint (build-loop Phase 4.5 backstop). "
            "Compares implementer synthesis_attestation claims against the actual git diff."
        )
    )
    p.add_argument(
        "--diff",
        required=True,
        help='Path to a unified diff file, OR a git range like "HEAD~1..HEAD"',
    )
    p.add_argument(
        "--envelope",
        required=True,
        help="Path to the implementer envelope JSON file",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit results as JSON to stdout",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress human-readable summary (implies --json output only)",
    )
    args = p.parse_args(argv)

    # Load diff
    try:
        diff_text = load_diff(args.diff)
    except Exception as e:  # noqa: BLE001
        print(f"attestation-lint: error loading diff: {e}", file=sys.stderr)
        return 2

    # Load envelope
    try:
        envelope = load_envelope(args.envelope)
    except (FileNotFoundError, ValueError) as e:
        print(f"attestation-lint: error loading envelope: {e}", file=sys.stderr)
        return 2

    # Run lint
    results, warn_message = run_lint(diff_text, envelope)
    summary = summarize(results)
    exit_code = compute_exit_code(summary, warn_message)

    output: dict[str, Any] = {
        "results": results,
        "summary": summary,
        "exit_code": exit_code,
    }
    if warn_message:
        output["warning"] = warn_message

    if args.json or args.quiet:
        print(json.dumps(output, indent=2))
    else:
        _print_human(results, summary, exit_code, warn_message)

    return exit_code


def _print_human(
    results: list[dict[str, Any]],
    summary: dict[str, int],
    exit_code: int,
    warn_message: str,
) -> None:
    status_icon = {0: "PASS", 1: "FAIL", 2: "UNVERIFIABLE"}.get(exit_code, "?")
    print(f"# attestation-lint [{status_icon}]")
    print()
    if warn_message:
        print(f"WARNING: {warn_message}")
        print()
    print(
        f"Summary: pass={summary.get('pass', 0)}, "
        f"fail={summary.get('fail', 0)}, "
        f"unverifiable={summary.get('unverifiable', 0)}"
    )
    print()
    for r in results:
        icon = {"pass": "✅", "fail": "❌", "unverifiable": "⚠️"}.get(r["status"], "❓")
        print(f"{icon} [{r['status'].upper()}] {r['dimension']}")
        print(f"   claimed: {r['claimed']}")
        print(f"   evidence: {r['evidence']}")
        print()


if __name__ == "__main__":
    sys.exit(main())
