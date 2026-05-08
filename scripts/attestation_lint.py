#!/usr/bin/env python3
"""
attestation_lint.py — deterministic synthesis-attestation lint for build-loop.

Compares each entry in an implementer envelope's `synthesis_attestation` block
against evidence in the corresponding git diff. Catches silent synthesis-decision
drift: an implementer claims `applied` but the diff disproves the claim.

CLI shape:
    python3 scripts/attestation_lint.py \\
        --diff <unified-diff-file | "HEAD~1..HEAD"> \\
        --envelope <path-to-envelope.json>

Stdlib only: re, json, argparse, subprocess, pathlib, sys.

Style mirrors `scripts/plan_verify.py` (rule functions, JSON output, exit codes).

Exit codes:
    0  all entries pass (every applied claim verified, no fails)
    1  any entry fails (claim disproved by diff)
    2  lint produced ONLY unverifiable results (subjective dims) OR runner error

    Note: `--ignore-dim NAME` suppresses entries from the summary counts and
    never affects the exit code. If every entry is ignored, exit is 0.

Strict mode (--strict-mode):
    Reverts the lint to α-style strict matching, disabling the permissive
    fallbacks grafted in commit 9dba912 for β/Sonnet-path support. When
    --strict-mode is active:

      • Anchor regex accepts ONLY JSX-style anchors (`<Component>`); the
        permissive arbitrary-text fallback (`PLACEMENT_RE_PERMISSIVE`) and
        the file-only fallback (`PLACEMENT_RE_FILE_ONLY`) are disabled.
        Non-JSX anchor claims return status=fail with reason naming
        "strict-mode: permissive anchor fallback disabled".

      • CTA tier accepts ONLY canonical class names {primary, secondary,
        tertiary}; the β-style aliases {ghost, link} are rejected as
        unverifiable with reason naming "strict-mode: alias rejected".

      • Diff parser requires a `diff --git` header; β-format diffs that lead
        with bare `--- a/path` / `+++ b/path` (no `diff --git` line) are
        skipped — files in such hunks never appear in the parsed `files`
        dict, so claims targeting them fail with "claimed file ... not
        present in diff".

      • Envelope wrapper handling is disabled — `{envelope: {...}}` is no
        longer auto-unwrapped. Only flat `{synthesis_attestation: ...}` is
        accepted. Wrapped envelopes surface as malformed (exit 2).

    Default is OFF (permissive). Strict is opt-in via `--strict-mode`.
    `--self-test` runs in permissive mode unless `--strict-mode` is also
    passed; both inline self-test (9 cases) and the separate test file
    (17 cases) keep passing under default-off.

Verifiable dimensions:
    - placement     — "after `<Anchor>` in <path>" — anchor must exist in pre-image,
                      new lines must appear after anchor's line position in post-image
    - cta_tier      — claimed class in {primary, secondary, tertiary} — diff must
                      show a matching className / variant / tier attribute
    - visual_weight — claimed heading level / divider — diff must contain
                      <h\\d>, <hr>, border-t, divide-, or matching tailwind weight

Unverifiable dimensions (returned as status=unverifiable, never failing):
    - copy_tone
    - empty_state (beyond mere presence)
    - any dimension the lint doesn't recognize

Envelope shapes accepted:
    1. Canonical (object, bare strings):
        {"synthesis_attestation": {
            "placement_NewsBanner": "applied",
            "cta_tier_save_button": "applied",
            "copy_tone_settings": "applied"
        }}
       Without claim text, every entry returns unverifiable. The lint only adds
       value when the envelope carries the claim detail (form 2).

    2. Extended (object, rich values):
        {"synthesis_attestation": {
            "placement_NewsBanner": {
                "status": "applied",
                "dimension": "placement",
                "claim": "after `<NewsCard>` in app/components/Feed.tsx"
            }
        }}

    3. Array of records:
        {"synthesis_attestation": [
            {"name": "placement_NewsBanner",
             "dimension": "placement",
             "applied": "after `<NewsCard>` in app/components/Feed.tsx",
             "status": "applied"}
        ]}

Result record (per attestation entry):
    {
      "name": str,
      "dimension": str,
      "claim": str | null,
      "claimed_status": "applied" | "deviated" | "n/a" | str,
      "status": "pass" | "fail" | "unverifiable" | "ignored",
      "reason": str,
      "evidence": {"file": str | null, "line": int | null, "snippet": str | null}
    }
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

DIFF_GIT_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def load_diff(spec: str) -> str:
    """Return unified-diff text. `spec` is either a file path or a git revspec
    like 'HEAD~1..HEAD'."""
    p = Path(spec)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    # Treat as a revspec — shell out to `git diff <spec>`.
    try:
        r = subprocess.run(
            ["git", "diff", "--unified=3", spec],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"git diff {spec} failed (exit {r.returncode}): {r.stderr.strip()}"
            )
        return r.stdout
    except FileNotFoundError as e:  # git not installed
        raise RuntimeError(f"git not available: {e}") from e


class FileDiff:
    """Per-file slice of a unified diff.

    Tracks:
      added_lines   — list of (post_line_no, content) for '+' lines
      removed_lines — list of (pre_line_no,  content) for '-' lines
      context_lines — list of (pre_line_no, post_line_no, content) for ' ' lines
      raw           — the full per-file diff text (header + hunks)
    """

    def __init__(self, path_a: str, path_b: str) -> None:
        self.path_a = path_a
        self.path_b = path_b
        self.added_lines: list[tuple[int, str]] = []
        self.removed_lines: list[tuple[int, str]] = []
        self.context_lines: list[tuple[int, int, str]] = []
        self.raw_chunks: list[str] = []

    @property
    def path(self) -> str:
        # Prefer the post-image path; falls back to pre-image for deletions.
        return self.path_b if self.path_b not in ("/dev/null", "") else self.path_a

    @property
    def raw(self) -> str:
        return "\n".join(self.raw_chunks)

    def added_text(self) -> str:
        return "\n".join(c for _, c in self.added_lines)

    def removed_text(self) -> str:
        return "\n".join(c for _, c in self.removed_lines)


def parse_unified_diff(diff_text: str, strict: bool = False) -> dict[str, FileDiff]:
    """Parse a unified-diff blob into {post-image-path: FileDiff}.

    When `strict` is True, the β-style fallback (open a FileDiff on bare
    `--- a/path` / `+++ b/path` pairs lacking a `diff --git` header) is
    disabled — files in such hunks are skipped entirely.
    """
    files: dict[str, FileDiff] = {}
    current: FileDiff | None = None
    pre_lineno = 0
    post_lineno = 0
    in_hunk = False

    pending_pre_path: str | None = None  # for diffs lacking "diff --git" header
    for line in diff_text.splitlines():
        m = DIFF_GIT_HEADER_RE.match(line)
        if m:
            current = FileDiff(m.group(1), m.group(2))
            files[current.path] = current
            in_hunk = False
            current.raw_chunks.append(line)
            pending_pre_path = None
            continue
        # β-style fallback: "--- a/path" / "+++ b/path" without a preceding
        # "diff --git" header. Open a FileDiff on the +++ line.
        # Disabled in strict mode — α convention requires the diff --git header.
        if line.startswith("--- "):
            stripped = line[4:].strip()
            pending_pre_path = stripped[2:] if stripped.startswith("a/") else stripped
            if current is not None:
                current.raw_chunks.append(line)
            continue
        if line.startswith("+++ "):
            stripped = line[4:].strip()
            post_path = stripped[2:] if stripped.startswith("b/") else stripped
            if current is None or (current and current.path_b != post_path):
                # Only auto-open if no diff --git header gave us this file.
                if not strict and pending_pre_path is not None and post_path:
                    current = FileDiff(pending_pre_path, post_path)
                    files[current.path] = current
                    in_hunk = False
                    current.raw_chunks.append(line)
                    pending_pre_path = None
                    continue
                if strict and pending_pre_path is not None and post_path and current is None:
                    # Strict: skip this file entirely. Reset the hunk state so
                    # subsequent @@ hunks don't accidentally attach to a stale
                    # `current` from an earlier `diff --git` block.
                    pending_pre_path = None
                    in_hunk = False
                    continue
            if current is not None:
                current.raw_chunks.append(line)
            pending_pre_path = None
            continue
        if current is None:
            continue
        # Skip metadata lines (index, similarity, etc.) — record raw.
        if line.startswith(("index ", "new file", "deleted file",
                            "similarity ", "rename ", "copy ", "Binary ")):
            current.raw_chunks.append(line)
            continue
        m = HUNK_RE.match(line)
        if m:
            pre_lineno = int(m.group(1))
            post_lineno = int(m.group(3))
            in_hunk = True
            current.raw_chunks.append(line)
            continue
        if not in_hunk:
            continue
        current.raw_chunks.append(line)
        if line.startswith("+") and not line.startswith("+++"):
            current.added_lines.append((post_lineno, line[1:]))
            post_lineno += 1
        elif line.startswith("-") and not line.startswith("---"):
            current.removed_lines.append((pre_lineno, line[1:]))
            pre_lineno += 1
        elif line.startswith(" "):
            current.context_lines.append((pre_lineno, post_lineno, line[1:]))
            pre_lineno += 1
            post_lineno += 1
        elif line.startswith("\\"):
            # "\ No newline at end of file" — skip
            continue

    return files


# ---------------------------------------------------------------------------
# Envelope normalization
# ---------------------------------------------------------------------------

KNOWN_DIMENSION_KEYWORDS = {
    "placement": "placement",
    "cta_tier": "cta_tier",
    "cta-tier": "cta_tier",
    "ctatier": "cta_tier",
    "visual_weight": "visual_weight",
    "visual-weight": "visual_weight",
    "visualweight": "visual_weight",
    "copy_tone": "copy_tone",
    "copy-tone": "copy_tone",
    "copytone": "copy_tone",
    "empty_state": "empty_state",
    "empty-state": "empty_state",
    "emptystate": "empty_state",
}

VERIFIABLE_DIMENSIONS = {"placement", "cta_tier", "visual_weight"}
UNVERIFIABLE_DIMENSIONS = {"copy_tone", "empty_state"}


def infer_dimension(name: str, explicit: str | None = None) -> str:
    """Map an attestation entry's name (or explicit `dimension` field) to a
    canonical dimension kind. Returns "unknown" when nothing matches."""
    if explicit:
        norm = explicit.strip().lower().replace("-", "_").replace(" ", "_")
        if norm in VERIFIABLE_DIMENSIONS or norm in UNVERIFIABLE_DIMENSIONS:
            return norm
    lower = name.lower()
    for keyword, canonical in KNOWN_DIMENSION_KEYWORDS.items():
        if keyword in lower:
            return canonical
    return "unknown"


def normalize_attestation(envelope: dict[str, Any], strict: bool = False) -> list[dict[str, Any]]:
    """Return a list of attestation records, one per dimension entry.

    Each record carries: name, dimension, claim (or None), claimed_status.
    Accepts envelopes in either flat shape ({"synthesis_attestation": ...})
    or β-style wrapped shape ({"envelope": {"synthesis_attestation": ...}}).

    When `strict` is True, the wrapped shape is rejected — only flat envelopes
    are accepted. Wrapped envelopes return [] and surface as malformed at
    the CLI layer.
    """
    # β-style wrapper: unwrap if the outer dict only carries "envelope".
    # Disabled in strict mode.
    if not strict and "synthesis_attestation" not in envelope and isinstance(envelope.get("envelope"), dict):
        inner = envelope["envelope"]
        if "synthesis_attestation" in inner:
            envelope = inner
    raw = envelope.get("synthesis_attestation")
    if raw is None:
        return []
    out: list[dict[str, Any]] = []

    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("dimension_name") or entry.get("id") or ""
            dim_explicit = entry.get("dimension") or entry.get("kind")
            dim = infer_dimension(name, dim_explicit)
            # "applied" is the canonical claim field per the task brief.
            claim = entry.get("applied") or entry.get("claim") or entry.get("value")
            status = entry.get("status")
            if status is None:
                # Bare "applied" / "deviated" / "n/a" might sit at top-level.
                status = "applied" if claim else "applied"
            out.append({
                "name": str(name),
                "dimension": dim,
                "claim": claim if isinstance(claim, str) else None,
                "claimed_status": str(status),
            })
        return out

    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, str):
                # Disambiguate by content: canonical keywords are statuses
                # (form 1, bare-string canonical), anything else is a claim
                # string (form 4, flat-claim shape).
                if value.lower() in {"applied", "deviated", "n/a"}:
                    out.append({
                        "name": str(name),
                        "dimension": infer_dimension(name),
                        "claim": None,
                        "claimed_status": value,
                    })
                else:
                    out.append({
                        "name": str(name),
                        "dimension": infer_dimension(name),
                        "claim": value,
                        "claimed_status": "applied",
                    })
            elif isinstance(value, dict):
                dim_explicit = value.get("dimension") or value.get("kind")
                dim = infer_dimension(name, dim_explicit)
                status = value.get("status") or value.get("applied_status") or "applied"
                claim = (
                    value.get("claim")
                    or value.get("applied")
                    or value.get("value")
                    or value.get("deviation_reason")
                )
                out.append({
                    "name": str(name),
                    "dimension": dim,
                    "claim": claim if isinstance(claim, str) else None,
                    "claimed_status": str(status),
                })
        return out

    return []


# ---------------------------------------------------------------------------
# Verifiers (one per dimension kind)
# ---------------------------------------------------------------------------

# Anchor + path extractor — parses claims like:
#   "after `<NewsCard>` in app/components/Feed.tsx"     — JSX-component anchor (α-style)
#   "before <Header /> in src/Header.tsx"
#   "after import React from 'react' in src/Button.tsx" — arbitrary-text anchor (β-style)
#   "in path/to/file"                                    — file-only fallback
#
# Two regexes tried in order: strict JSX-anchor first (preserves α's specificity
# when the claim is JSX-shaped), then permissive arbitrary-text fallback (covers
# β's looser conventions for non-JSX commits like imports, code lines).
PLACEMENT_RE = re.compile(
    r"\b(?P<rel>after|before|inside|within)\b\s*"
    r"`?<?(?P<anchor>[A-Za-z_][\w.-]*)\s*/?>?`?"
    r"\s+in\s+`?(?P<path>[\w./@-]+)`?",
    re.IGNORECASE,
)
# β-style permissive fallback: any non-empty anchor text up to " in <path>".
# Accepts quoted strings, line numbers, full code lines as anchors.
PLACEMENT_RE_PERMISSIVE = re.compile(
    r"(?P<rel>after|before)\s+(?P<anchor>.+?)\s+in\s+(?P<path>\S+)",
    re.IGNORECASE,
)
# File-only form: "in <path>" with no positional anchor — accept the diff
# touching the file as evidence. No before/after to verify.
PLACEMENT_RE_FILE_ONLY = re.compile(
    r"\bin\s+`?(?P<path>[\w./@-]+)`?",
    re.IGNORECASE,
)


def verify_placement(record: dict[str, Any], files: dict[str, FileDiff],
                     strict: bool = False) -> dict[str, Any]:
    claim = record.get("claim") or ""
    # Try strict JSX-anchor regex first (α-style: `<Component>`).
    m = PLACEMENT_RE.search(claim)
    anchor_is_jsx = m is not None
    if m is None and not strict:
        # Fallback to β-style permissive: arbitrary anchor text up to " in <path>".
        m = PLACEMENT_RE_PERMISSIVE.search(claim)
    if m is None:
        if strict:
            # Strict mode: no permissive or file-only fallback. JSX-only.
            return {
                "status": "fail",
                "reason": ("strict-mode: permissive anchor fallback disabled — "
                           "claim must use JSX-style `<Component>` anchor "
                           "(e.g. \"after `<NewsCard>` in path/to/file\")"),
                "evidence": {"file": None, "line": None, "snippet": None},
            }
        # Last fallback: file-only ("in path/to/file"). No positional check —
        # passes if the diff touches the file at all.
        fm = PLACEMENT_RE_FILE_ONLY.search(claim)
        if fm:
            path_claim = fm.group("path")
            for fpath in files:
                if fpath.endswith(path_claim) or path_claim.endswith(fpath) or fpath == path_claim:
                    return {
                        "status": "pass",
                        "reason": f"file-only claim — diff touches `{path_claim}`",
                        "evidence": {"file": fpath, "line": None, "snippet": None},
                    }
            return {
                "status": "fail",
                "reason": f"file-only claim — diff does not touch `{path_claim}`",
                "evidence": {"file": None, "line": None, "snippet": None},
            }
        return {
            "status": "unverifiable",
            "reason": "claim text missing recognizable 'after/before <Anchor> in <path>' pattern",
            "evidence": {"file": None, "line": None, "snippet": None},
        }
    rel = m.group("rel").lower()
    anchor = m.group("anchor").strip().strip("`'\"")
    path_claim = m.group("path").strip("`'\"")

    # Match the path loosely — the diff's path may include a repo prefix.
    target: FileDiff | None = None
    for fpath, fdiff in files.items():
        if fpath.endswith(path_claim) or path_claim.endswith(fpath) or fpath == path_claim:
            target = fdiff
            break
    if target is None:
        return {
            "status": "fail",
            "reason": f"claimed file `{path_claim}` not present in diff",
            "evidence": {"file": None, "line": None, "snippet": None},
        }

    # Look for the anchor in the pre-image: context lines + removed lines.
    # JSX-style anchors get strict `<X` matching; β-style permissive anchors
    # get substring matching (the entire anchor text must appear on the line).
    if anchor_is_jsx:
        anchor_pat = re.compile(rf"<\s*{re.escape(anchor)}\b", re.IGNORECASE)
    else:
        anchor_pat = re.compile(re.escape(anchor), re.IGNORECASE)
    anchor_pre_line: int | None = None
    anchor_pre_snippet: str | None = None
    for pre_ln, _post_ln, txt in target.context_lines:
        if anchor_pat.search(txt):
            anchor_pre_line = pre_ln
            anchor_pre_snippet = txt.strip()
            break
    if anchor_pre_line is None:
        for pre_ln, txt in target.removed_lines:
            if anchor_pat.search(txt):
                anchor_pre_line = pre_ln
                anchor_pre_snippet = txt.strip()
                break
    if anchor_pre_line is None:
        # Anchor may be NEW (added in same diff) — accept if it appears in added lines
        # AND there are added lines after it (handled below via post-image search).
        for post_ln, txt in target.added_lines:
            if anchor_pat.search(txt):
                anchor_pre_line = post_ln
                anchor_pre_snippet = txt.strip()
                break
    if anchor_pre_line is None:
        return {
            "status": "fail",
            "reason": f"anchor `<{anchor}>` not found in {target.path} pre-image or diff context",
            "evidence": {"file": target.path, "line": None, "snippet": None},
        }

    # Find the anchor's POST-image line (use context line that contained it,
    # else assume same as pre).
    anchor_post_line: int | None = None
    for pre_ln, post_ln, txt in target.context_lines:
        if anchor_pat.search(txt):
            anchor_post_line = post_ln
            break
    if anchor_post_line is None:
        # If anchor was added, it has a post line directly.
        for post_ln, txt in target.added_lines:
            if anchor_pat.search(txt):
                anchor_post_line = post_ln
                break
    if anchor_post_line is None:
        anchor_post_line = anchor_pre_line  # best-effort fallback

    # Verify added lines fall on the correct side of the anchor.
    if not target.added_lines:
        return {
            "status": "fail",
            "reason": f"no added lines in {target.path} to verify placement against",
            "evidence": {"file": target.path, "line": anchor_pre_line, "snippet": anchor_pre_snippet},
        }
    after_anchor = [ln for ln, _ in target.added_lines if ln > anchor_post_line]
    before_anchor = [ln for ln, _ in target.added_lines if ln < anchor_post_line]

    if rel == "after":
        if after_anchor:
            return {
                "status": "pass",
                "reason": f"{len(after_anchor)} added line(s) after anchor `<{anchor}>` (post-line {anchor_post_line}) in {target.path}",
                "evidence": {"file": target.path, "line": anchor_post_line, "snippet": anchor_pre_snippet},
            }
        return {
            "status": "fail",
            "reason": f"claim says 'after <{anchor}>' but no added lines follow anchor (post-line {anchor_post_line}) in {target.path}",
            "evidence": {"file": target.path, "line": anchor_post_line, "snippet": anchor_pre_snippet},
        }
    if rel == "before":
        if before_anchor:
            return {
                "status": "pass",
                "reason": f"{len(before_anchor)} added line(s) before anchor `<{anchor}>` (post-line {anchor_post_line}) in {target.path}",
                "evidence": {"file": target.path, "line": anchor_post_line, "snippet": anchor_pre_snippet},
            }
        return {
            "status": "fail",
            "reason": f"claim says 'before <{anchor}>' but no added lines precede anchor (post-line {anchor_post_line}) in {target.path}",
            "evidence": {"file": target.path, "line": anchor_post_line, "snippet": anchor_pre_snippet},
        }
    # inside/within — accept if any added lines are within ~20 lines of anchor.
    near = [ln for ln, _ in target.added_lines if abs(ln - anchor_post_line) <= 20]
    if near:
        return {
            "status": "pass",
            "reason": f"{len(near)} added line(s) within 20 lines of anchor `<{anchor}>` in {target.path}",
            "evidence": {"file": target.path, "line": anchor_post_line, "snippet": anchor_pre_snippet},
        }
    return {
        "status": "fail",
        "reason": f"claim says '{rel} <{anchor}>' but no added lines near anchor in {target.path}",
        "evidence": {"file": target.path, "line": anchor_post_line, "snippet": anchor_pre_snippet},
    }


CTA_TIER_CLASSES = {"primary", "secondary", "tertiary"}
# β-style aliases — common in shadcn/Radix/Mantine where ghost/link variants
# function as tertiary buttons. Adding them as aliases keeps the dimension
# matchable when implementers name what they actually shipped.
CTA_TIER_ALIASES = {"ghost": "tertiary", "link": "tertiary"}
CTA_TIER_ALL = CTA_TIER_CLASSES | set(CTA_TIER_ALIASES.keys())
# Match common ways a CTA tier is encoded:
#   className="...primary..."  variant="primary"  tier="primary"
#   class="btn-primary"        data-tier="primary"
#   variant="ghost"            variant="link"     (β-style aliases)
CTA_PATTERNS = [
    re.compile(r"""(?:className|class|variant|tier|data-tier)\s*=\s*["']([^"']+)["']""", re.IGNORECASE),
    # Standalone token (e.g. tailwind: bg-primary, btn-primary)
    re.compile(r"\b(?:btn-|bg-|border-|text-)?(primary|secondary|tertiary|ghost|link)\b", re.IGNORECASE),
]


def verify_cta_tier(record: dict[str, Any], files: dict[str, FileDiff],
                    strict: bool = False) -> dict[str, Any]:
    claim = (record.get("claim") or "").strip().lower()
    # Pull a tier-class token from the claim. Accept canonical names AND
    # β-style aliases (ghost, link → tertiary). Strict mode rejects aliases.
    candidate_classes = CTA_TIER_CLASSES if strict else CTA_TIER_ALL
    tier: str | None = None
    for cls in candidate_classes:
        if cls in claim:
            tier = cls
            break
    if tier is None:
        # In strict mode, surface that aliases were rejected (if the claim
        # does name a ghost/link alias). Otherwise generic unverifiable.
        if strict and any(alias in claim for alias in CTA_TIER_ALIASES):
            return {
                "status": "unverifiable",
                "reason": ("strict-mode: alias rejected — only canonical "
                           "{primary, secondary, tertiary} accepted "
                           "(ghost/link aliases disabled)"),
                "evidence": {"file": None, "line": None, "snippet": None},
            }
        accepted = "primary|secondary|tertiary" if strict else "primary|secondary|tertiary|ghost|link"
        return {
            "status": "unverifiable",
            "reason": f"claim does not name a known cta_tier class ({accepted})",
            "evidence": {"file": None, "line": None, "snippet": None},
        }
    # Search added lines across all files for the tier token in a className /
    # variant attribute or as a standalone class fragment.
    token_re = re.compile(rf"\b{re.escape(tier)}\b", re.IGNORECASE)
    for fpath, fdiff in files.items():
        for post_ln, txt in fdiff.added_lines:
            if token_re.search(txt):
                # Extra confidence boost: at least one of the CTA patterns matches.
                strong = any(p.search(txt) for p in CTA_PATTERNS)
                return {
                    "status": "pass",
                    "reason": f"tier token `{tier}` found in added line of {fpath}"
                              + ("" if strong else " (loose match — no className/variant attr)"),
                    "evidence": {"file": fpath, "line": post_ln, "snippet": txt.strip()},
                }
    return {
        "status": "fail",
        "reason": f"tier token `{tier}` not found in any added line of the diff",
        "evidence": {"file": None, "line": None, "snippet": None},
    }


# Visual-weight signals: heading levels, dividers, tailwind separators.
VISUAL_WEIGHT_PATTERNS = {
    "heading": re.compile(r"<\s*h([1-6])\b", re.IGNORECASE),
    "hr":      re.compile(r"<\s*hr\b", re.IGNORECASE),
    "border_t": re.compile(r"\bborder-t\b", re.IGNORECASE),
    "divide":  re.compile(r"\bdivide-(?:y|x)-\d+\b", re.IGNORECASE),
}
# Heading-level extractor in the CLAIM text: "h2", "heading level 3", "<h2>"
HEADING_CLAIM_RE = re.compile(r"\b(?:<\s*h|heading\s+level\s+|level\s+|h)\s*([1-6])\b", re.IGNORECASE)
DIVIDER_CLAIM_RE = re.compile(r"\b(divider|hr|border-t|divide-[xy])\b", re.IGNORECASE)


def verify_visual_weight(record: dict[str, Any], files: dict[str, FileDiff]) -> dict[str, Any]:
    claim = (record.get("claim") or "").strip()
    if not claim:
        return {
            "status": "unverifiable",
            "reason": "no claim text — cannot determine claimed weight",
            "evidence": {"file": None, "line": None, "snippet": None},
        }
    # Heading claim?
    m = HEADING_CLAIM_RE.search(claim)
    if m:
        level = m.group(1)
        pat = re.compile(rf"<\s*h{level}\b", re.IGNORECASE)
        for fpath, fdiff in files.items():
            for post_ln, txt in fdiff.added_lines:
                if pat.search(txt):
                    return {
                        "status": "pass",
                        "reason": f"heading <h{level}> found in added line of {fpath}",
                        "evidence": {"file": fpath, "line": post_ln, "snippet": txt.strip()},
                    }
        return {
            "status": "fail",
            "reason": f"claimed heading <h{level}> not present in any added line",
            "evidence": {"file": None, "line": None, "snippet": None},
        }
    # Divider claim?
    if DIVIDER_CLAIM_RE.search(claim):
        for fpath, fdiff in files.items():
            for post_ln, txt in fdiff.added_lines:
                if (VISUAL_WEIGHT_PATTERNS["hr"].search(txt)
                        or VISUAL_WEIGHT_PATTERNS["border_t"].search(txt)
                        or VISUAL_WEIGHT_PATTERNS["divide"].search(txt)):
                    return {
                        "status": "pass",
                        "reason": f"divider signal found in added line of {fpath}",
                        "evidence": {"file": fpath, "line": post_ln, "snippet": txt.strip()},
                    }
        return {
            "status": "fail",
            "reason": "claimed divider not present in any added line (no <hr>, border-t, or divide-)",
            "evidence": {"file": None, "line": None, "snippet": None},
        }
    return {
        "status": "unverifiable",
        "reason": "claim does not name a recognizable heading level or divider keyword",
        "evidence": {"file": None, "line": None, "snippet": None},
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _normalize_ignore_token(token: str) -> str:
    """Lower-case + collapse hyphens/spaces to underscores for ignore-list match."""
    return token.strip().lower().replace("-", "_").replace(" ", "_")


def is_ignored(record: dict[str, Any], ignore_set: set[str]) -> bool:
    """A record is ignored if any of these match a token in `ignore_set`
    (case-insensitive, hyphen/space → underscore):
      - the entry name (envelope key)
      - the inferred canonical dimension (placement / cta_tier / ...)
    Tokens that don't match anything are silently no-ops — see help text."""
    if not ignore_set:
        return False
    name_norm = _normalize_ignore_token(str(record.get("name", "")))
    dim_norm = _normalize_ignore_token(str(record.get("dimension", "")))
    return name_norm in ignore_set or dim_norm in ignore_set


def lint_one(record: dict[str, Any], files: dict[str, FileDiff],
             ignore_set: set[str] | None = None,
             strict: bool = False) -> dict[str, Any]:
    base = {
        "name": record["name"],
        "dimension": record["dimension"],
        "claim": record.get("claim"),
        "claimed_status": record.get("claimed_status"),
    }
    # Ignore filter runs first — envelope is still parsed (so the entry appears
    # in `results` with status=ignored), but the verifier never runs and the
    # summary excludes it from pass/fail/unverifiable.
    if ignore_set and is_ignored(record, ignore_set):
        return {**base,
                "status": "ignored",
                "reason": "suppressed by --ignore-dim",
                "evidence": {"file": None, "line": None, "snippet": None}}
    # n/a and deviated entries are not graded — they're an explicit non-claim.
    if record.get("claimed_status") in ("n/a", "deviated"):
        return {**base,
                "status": "unverifiable",
                "reason": f"claimed_status={record['claimed_status']} — not a verifiable assertion",
                "evidence": {"file": None, "line": None, "snippet": None}}

    dim = record["dimension"]
    if dim in UNVERIFIABLE_DIMENSIONS:
        return {**base,
                "status": "unverifiable",
                "reason": f"dimension `{dim}` is subjective and not graded by this lint",
                "evidence": {"file": None, "line": None, "snippet": None}}
    if dim == "unknown":
        return {**base,
                "status": "unverifiable",
                "reason": "dimension not recognized — cannot route to a verifier",
                "evidence": {"file": None, "line": None, "snippet": None}}

    if record.get("claim") is None:
        return {**base,
                "status": "unverifiable",
                "reason": "envelope used bare-string form — no claim text to check against the diff",
                "evidence": {"file": None, "line": None, "snippet": None}}

    if dim == "placement":
        result = verify_placement(record, files, strict=strict)
    elif dim == "cta_tier":
        result = verify_cta_tier(record, files, strict=strict)
    elif dim == "visual_weight":
        result = verify_visual_weight(record, files)
    else:  # defensive — should be unreachable given dim checks above
        result = {"status": "unverifiable",
                  "reason": "no verifier registered for dimension",
                  "evidence": {"file": None, "line": None, "snippet": None}}
    return {**base, **result}


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "fail": 0, "unverifiable": 0, "ignored": 0,
               "total": len(results)}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return summary


def determine_exit(summary: dict[str, int]) -> int:
    # Ignored entries never affect exit code (they were explicitly suppressed).
    if summary["fail"] > 0:
        return 1
    if summary["pass"] == 0 and summary["unverifiable"] > 0:
        return 2
    # If everything was ignored (or envelope was empty), exit 0.
    return 0


def run_lint(diff_text: str, envelope: dict[str, Any],
             ignore_dims: list[str] | None = None,
             strict: bool = False) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    files = parse_unified_diff(diff_text, strict=strict)
    records = normalize_attestation(envelope, strict=strict)
    ignore_set = {_normalize_ignore_token(t) for t in (ignore_dims or []) if t}
    results = [lint_one(r, files, ignore_set, strict=strict) for r in records]
    summary = summarize(results)
    return results, summary, determine_exit(summary)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

# Synthetic diff: two added lines after <NewsCard> in app/components/Feed.tsx,
# one button with className="btn-primary", and an <h2> heading.
SELF_TEST_DIFF = """diff --git a/app/components/Feed.tsx b/app/components/Feed.tsx
index abc1234..def5678 100644
--- a/app/components/Feed.tsx
+++ b/app/components/Feed.tsx
@@ -10,6 +10,9 @@ export function Feed() {
   return (
     <section>
       <NewsCard story={lead} />
+      <NewsBanner kind="breaking" />
+      <h2>Latest</h2>
+      <button className="btn-primary">Read more</button>
       <Sidebar />
     </section>
   );
"""

SELF_TEST_ENVELOPE_PASS = {
    "synthesis_attestation": {
        "placement_NewsBanner": {
            "status": "applied",
            "dimension": "placement",
            "claim": "after `<NewsCard>` in app/components/Feed.tsx",
        },
        "cta_tier_read_more": {
            "status": "applied",
            "dimension": "cta_tier",
            "claim": "primary",
        },
        "visual_weight_section_header": {
            "status": "applied",
            "dimension": "visual_weight",
            "claim": "h2 heading",
        },
        "copy_tone_banner": "applied",
    }
}

SELF_TEST_ENVELOPE_FAIL = {
    "synthesis_attestation": {
        "placement_NewsBanner": {
            "status": "applied",
            "dimension": "placement",
            # Claim says "before" but diff places it after — should fail.
            "claim": "before `<NewsCard>` in app/components/Feed.tsx",
        },
        "cta_tier_read_more": {
            "status": "applied",
            "dimension": "cta_tier",
            "claim": "tertiary",   # diff has primary, not tertiary — fail
        },
    }
}


def run_self_test(strict: bool = False) -> int:
    """Inline self-test. Returns 0 on success, 1 on any failure.

    When `strict` is True, all 9 cases are run with strict=True. The 9
    canned cases use JSX-style anchors and canonical tier names, so they
    pass cleanly under strict — the flag is forward-compat / smoke check.
    """
    failures: list[str] = []

    # Pass case
    results, summary, code = run_lint(SELF_TEST_DIFF, SELF_TEST_ENVELOPE_PASS, strict=strict)
    placement = next(r for r in results if r["dimension"] == "placement")
    cta = next(r for r in results if r["dimension"] == "cta_tier")
    weight = next(r for r in results if r["dimension"] == "visual_weight")
    tone = next(r for r in results if r["dimension"] == "copy_tone")
    if placement["status"] != "pass":
        failures.append(f"placement should pass, got {placement['status']}: {placement['reason']}")
    if cta["status"] != "pass":
        failures.append(f"cta_tier should pass, got {cta['status']}: {cta['reason']}")
    if weight["status"] != "pass":
        failures.append(f"visual_weight should pass, got {weight['status']}: {weight['reason']}")
    if tone["status"] != "unverifiable":
        failures.append(f"copy_tone should be unverifiable, got {tone['status']}")
    if code != 0:
        failures.append(f"pass-case exit code should be 0, got {code} (summary={summary})")

    # Fail case
    results, summary, code = run_lint(SELF_TEST_DIFF, SELF_TEST_ENVELOPE_FAIL, strict=strict)
    placement = next(r for r in results if r["dimension"] == "placement")
    cta = next(r for r in results if r["dimension"] == "cta_tier")
    if placement["status"] != "fail":
        failures.append(f"placement should fail (claim 'before' but diff is 'after'), got {placement['status']}")
    if cta["status"] != "fail":
        failures.append(f"cta_tier should fail (claim 'tertiary' not in diff), got {cta['status']}")
    if code != 1:
        failures.append(f"fail-case exit code should be 1, got {code} (summary={summary})")

    # Unverifiable-only case
    results, summary, code = run_lint(SELF_TEST_DIFF, {
        "synthesis_attestation": {
            "copy_tone_x": "applied",
            "empty_state_x": "applied",
        }
    }, strict=strict)
    if summary["fail"] != 0 or summary["pass"] != 0 or summary["unverifiable"] != 2:
        failures.append(f"unverifiable-only: bad summary {summary}")
    if code != 2:
        failures.append(f"unverifiable-only exit code should be 2, got {code}")

    # Empty envelope
    results, summary, code = run_lint(SELF_TEST_DIFF, {"synthesis_attestation": {}}, strict=strict)
    if summary["total"] != 0 or code != 0:
        failures.append(f"empty envelope: total should be 0 with exit 0, got total={summary['total']} code={code}")

    # Array form
    results, summary, code = run_lint(SELF_TEST_DIFF, {
        "synthesis_attestation": [
            {"name": "placement_NewsBanner",
             "dimension": "placement",
             "applied": "after `<NewsCard>` in app/components/Feed.tsx"},
        ]
    }, strict=strict)
    if summary["pass"] != 1 or code != 0:
        failures.append(f"array form: should pass once, got summary={summary} code={code}")

    # --ignore-dim by canonical dimension: ignore all `placement` entries.
    # Uses the FAIL envelope which has a placement+cta_tier; ignoring placement
    # leaves only the cta_tier fail → exit 1.
    results, summary, code = run_lint(SELF_TEST_DIFF, SELF_TEST_ENVELOPE_FAIL,
                                      ignore_dims=["placement"], strict=strict)
    placement = next(r for r in results if r["dimension"] == "placement")
    if placement["status"] != "ignored":
        failures.append(f"ignore by dim 'placement' should mark ignored, got {placement['status']}")
    if summary.get("ignored") != 1:
        failures.append(f"ignore by dim: summary.ignored should be 1, got {summary}")
    if summary["fail"] != 1:
        failures.append(f"ignore by dim: cta_tier fail should remain, got summary={summary}")
    if code != 1:
        failures.append(f"ignore by dim: exit should still be 1 (cta_tier fails), got {code}")

    # --ignore-dim by entry name: suppress every entry, exit 0.
    results, summary, code = run_lint(SELF_TEST_DIFF, SELF_TEST_ENVELOPE_FAIL,
                                      ignore_dims=["placement_NewsBanner",
                                                   "cta_tier_read_more"],
                                      strict=strict)
    if summary.get("ignored") != 2 or summary["fail"] != 0:
        failures.append(f"ignore-all by name: bad summary {summary}")
    if code != 0:
        failures.append(f"ignore-all by name: exit should be 0, got {code}")

    # --ignore-dim with unknown token: silent no-op (case sensitivity normalized).
    results, summary, code = run_lint(SELF_TEST_DIFF, SELF_TEST_ENVELOPE_PASS,
                                      ignore_dims=["does-not-exist"], strict=strict)
    if summary.get("ignored") != 0:
        failures.append(f"ignore unknown: should be silent no-op, got summary={summary}")
    if code != 0:
        failures.append(f"ignore unknown: pass envelope should still exit 0, got {code}")

    # --ignore-dim case + hyphen normalization: 'CTA-Tier' should match dim cta_tier.
    results, summary, code = run_lint(SELF_TEST_DIFF, SELF_TEST_ENVELOPE_FAIL,
                                      ignore_dims=["CTA-Tier"], strict=strict)
    cta = next(r for r in results if r["dimension"] == "cta_tier")
    if cta["status"] != "ignored":
        failures.append(f"ignore normalization: 'CTA-Tier' should match cta_tier, got {cta['status']}")

    if failures:
        mode_label = " [strict]" if strict else ""
        print(f"attestation_lint self-test FAILED{mode_label}:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    mode_label = " [strict-mode]" if strict else ""
    print(f"attestation_lint self-test PASS (9 cases){mode_label}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Compare an implementer envelope's synthesis_attestation entries against a git diff.",
    )
    p.add_argument("--diff", help="Path to a unified-diff file OR a git revspec like 'HEAD~1..HEAD'.")
    p.add_argument("--envelope", help="Path to the implementer envelope JSON.")
    p.add_argument("--quiet", "--json", dest="quiet", action="store_true", help="Emit JSON only; suppress human summary on stdout.")
    p.add_argument(
        "--ignore-dim",
        dest="ignore_dim",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Suppress lint results for a dimension. Repeatable. NAME matches "
            "either an envelope entry name (e.g. `placement_NewsBanner`) OR a "
            "canonical dimension (`placement`, `cta_tier`, `visual_weight`, "
            "`copy_tone`, `empty_state`); case + hyphens normalized. "
            "Unknown tokens are silent no-ops. Ignored entries appear in the "
            "results JSON with status=ignored but are excluded from "
            "pass/fail/unverifiable counts and never affect the exit code. "
            "Has no effect when --self-test is used."
        ),
    )
    p.add_argument("--self-test", action="store_true", help="Run the inline self-test and exit.")
    p.add_argument(
        "--strict-mode",
        dest="strict_mode",
        action="store_true",
        default=False,
        help=(
            "Disable permissive fallbacks added in 9dba912 — revert to "
            "α-style strict matching. Specifically: "
            "(1) anchor regex accepts only JSX-style `<Component>` (no "
            "permissive arbitrary-text or file-only fallback); "
            "(2) cta_tier accepts only canonical {primary,secondary,tertiary} "
            "(ghost/link aliases rejected as unverifiable); "
            "(3) diff parser requires a `diff --git` header (β-format diffs "
            "with bare `--- a/path`/`+++ b/path` are skipped); "
            "(4) envelope wrapper `{envelope: {...}}` is no longer auto-unwrapped "
            "(only flat `{synthesis_attestation: ...}` accepted). "
            "Default OFF — opt in for α-conformance gating. "
            "Combine with --self-test to run the inline 9-case suite under strict."
        ),
    )
    p.add_argument(
        "--check-ledger",
        dest="check_ledger",
        action="store_true",
        default=False,
        help=(
            "Run the decision_ledger coverage check against the envelope. "
            "Requires --envelope. --plan is optional: when provided, the check "
            "reads the plan to detect whether synthesis_dimensions are present "
            "and enforces that ledger is non-absent; when omitted, only the "
            "shape of any present ledger entries is validated. "
            "Exit codes: 0 = pass, 1 = any error finding, 2 = runner error. "
            "Warnings (e.g. null evidence_file with owner=plan) do not change "
            "exit code. May be combined with --diff/--envelope for a combined run."
        ),
    )
    p.add_argument(
        "--plan",
        dest="plan",
        default=None,
        metavar="PATH",
        help=(
            "Path to the originating plan markdown file. Used by --check-ledger "
            "to detect whether the plan has a synthesis_dimensions block. When "
            "absent, ledger-presence enforcement is skipped (shape-only check)."
        ),
    )
    args = p.parse_args(argv)

    if args.self_test:
        # --self-test runs against canned envelopes; --ignore-dim is intentionally
        # NOT applied so the deterministic self-test stays deterministic.
        # --strict-mode IS propagated when both flags are passed.
        synth_code = run_self_test(strict=args.strict_mode)
        ledger_code = _run_ledger_self_test()
        return max(synth_code, ledger_code)

    # --check-ledger mode: envelope-only ledger validation.
    if args.check_ledger:
        if not args.envelope:
            p.error("--check-ledger requires --envelope")
            return 2
        try:
            envelope = json.loads(Path(args.envelope).expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"attestation-lint: failed to load envelope: {e}", file=sys.stderr)
            return 2
        if not isinstance(envelope, dict):
            print("attestation-lint: envelope JSON must be an object", file=sys.stderr)
            return 2

        plan_path: Path | None = Path(args.plan).expanduser() if args.plan else None
        findings, ledger_exit = run_ledger_check(envelope, plan_path=plan_path)

        payload: dict[str, Any] = {
            "envelope": args.envelope,
            "plan": args.plan,
            "ledger_findings": [
                {"level": f.level, "message": f.message} for f in findings
            ],
            "exit_code": ledger_exit,
        }
        print(json.dumps(payload, indent=2))

        if not args.quiet:
            errors = [f for f in findings if f.level == "error"]
            warnings = [f for f in findings if f.level == "warning"]
            if errors:
                print(
                    f"attestation-lint(ledger): {len(errors)} error(s), "
                    f"{len(warnings)} warning(s)",
                    file=sys.stderr,
                )
                for f in errors:
                    print(f"  ERROR: {f.message}", file=sys.stderr)
            if warnings and not errors:
                for f in warnings:
                    print(f"  WARN: {f.message}", file=sys.stderr)

        return ledger_exit

    if not args.diff or not args.envelope:
        p.error("--diff and --envelope are required (or use --self-test / --check-ledger)")
        return 2  # unreachable; argparse exits

    try:
        diff_text = load_diff(args.diff)
    except Exception as e:  # noqa: BLE001 — runner-error -> exit 2
        print(f"attestation-lint: failed to load diff: {e}", file=sys.stderr)
        return 2

    try:
        envelope = json.loads(Path(args.envelope).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"attestation-lint: failed to load envelope: {e}", file=sys.stderr)
        return 2
    if not isinstance(envelope, dict):
        print("attestation-lint: envelope JSON must be an object", file=sys.stderr)
        return 2

    # Detect malformed envelope (missing synthesis_attestation in both flat
    # and β-wrapped shapes) — exit 2 with warning. β-style behavior surfaces
    # the gap rather than silently passing.
    # In strict mode, the wrapped form is also rejected as malformed — only
    # flat `{synthesis_attestation: ...}` is accepted.
    has_flat = "synthesis_attestation" in envelope
    has_wrapped = (
        isinstance(envelope.get("envelope"), dict)
        and "synthesis_attestation" in envelope["envelope"]
    )
    warning: str | None = None
    if args.strict_mode:
        if not has_flat:
            if has_wrapped:
                warning = (
                    "strict-mode: envelope wrapper `{envelope: {...}}` rejected — "
                    "only flat `{synthesis_attestation: ...}` accepted; exit 2"
                )
            else:
                warning = (
                    "envelope JSON is missing 'synthesis_attestation' field — "
                    "no claims to verify; exit 2 (malformed_envelope_handling=warn)"
                )
    else:
        if not (has_flat or has_wrapped):
            warning = (
                "envelope JSON is missing 'synthesis_attestation' field — "
                "no claims to verify; exit 2 (malformed_envelope_handling=warn)"
            )

    try:
        results, summary, exit_code = run_lint(diff_text, envelope,
                                               ignore_dims=args.ignore_dim,
                                               strict=args.strict_mode)
    except Exception as e:  # noqa: BLE001
        print(f"attestation-lint: error: {e}", file=sys.stderr)
        return 2

    if warning:
        exit_code = 2

    payload = {
        "diff": args.diff,
        "envelope": args.envelope,
        "ignore_dim": args.ignore_dim,
        "strict_mode": args.strict_mode,
        "summary": summary,
        "results": results,
        "exit_code": exit_code,
    }
    if warning:
        payload["warning"] = warning
    print(json.dumps(payload, indent=2))

    if not args.quiet and exit_code != 0:
        # Mirror plan_verify's stderr-on-fail nudge.
        if exit_code == 1:
            print(f"attestation-lint: {summary['fail']} fail / "
                  f"{summary['pass']} pass / {summary['unverifiable']} unverifiable",
                  file=sys.stderr)
        elif exit_code == 2:
            print(f"attestation-lint: only {summary['unverifiable']} unverifiable result(s) — "
                  f"no graded assertions in this envelope", file=sys.stderr)

    return exit_code


# ---------------------------------------------------------------------------
# Ledger coverage check
# ---------------------------------------------------------------------------

VALID_ON_NEW_DECISION = {"block", "flag", "absorb"}
REQUIRED_LEDGER_FIELDS = {"dimension", "owner", "locked_value", "alternatives_rejected",
                           "evidence_file", "on_new_decision"}


class Finding:
    """A single lint finding from check_ledger_coverage."""

    def __init__(self, level: str, message: str) -> None:
        # level: "error" | "warning"
        self.level = level
        self.message = message

    def __repr__(self) -> str:
        return f"Finding({self.level!r}, {self.message!r})"


def _has_synthesis_dimensions(plan_path: "Path") -> bool:
    """Return True if the plan file contains a synthesis_dimensions: block.

    Tries to import count_synthesis_dimensions from plan_verify.py.
    Falls back to a tiny inline YAML-ish parser when the import fails (e.g.
    when running in an isolated environment).
    """
    try:
        import sys as _sys
        _scripts_dir = str(plan_path.parent.parent / "scripts")
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        # Also try the scripts dir relative to this file's location.
        _own_scripts = str(Path(__file__).parent)
        if _own_scripts not in _sys.path:
            _sys.path.insert(0, _own_scripts)
        from plan_verify import count_synthesis_dimensions  # type: ignore[import]
        return count_synthesis_dimensions(plan_path) > 0
    except (ImportError, Exception):
        # Inline fallback: scan for a `synthesis_dimensions:` header.
        _SYNTH_HDR = re.compile(r"^\s*synthesis_dimensions\s*:\s*$", re.IGNORECASE)
        try:
            text = plan_path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if _SYNTH_HDR.match(line):
                    return True
        except OSError:
            pass
        return False


def check_ledger_coverage(
    envelope: "dict[str, Any]",
    plan_path: "Path | None" = None,
) -> "list[Finding]":
    """Check that the envelope's decision_ledger is present and well-formed.

    Rules:
      1. If plan_path is provided and the plan has a synthesis_dimensions block:
         - decision_ledger MUST be present and non-null (empty [] is OK only
           when synthesis_attestation is also empty).
         - Every key in synthesis_attestation must have a matching ledger entry
           (matched by the `dimension` field).
      2. If no synthesis_dimensions block (or plan_path is None and no ledger
         present): absence of ledger is fine — no findings.
      3. If decision_ledger IS present (regardless of plan): validate each
         entry's shape: all six fields present, on_new_decision is a valid enum,
         non-null/non-empty values (except evidence_file which may be null).

    Returns a list of Finding objects. Empty list = PASS.
    """
    # Unwrap β-style wrapper for consistency with normalize_attestation.
    if "synthesis_attestation" not in envelope and isinstance(envelope.get("envelope"), dict):
        inner = envelope["envelope"]
        if "synthesis_attestation" in inner:
            envelope = inner

    findings: list[Finding] = []
    ledger = envelope.get("decision_ledger")
    has_ledger_field = "decision_ledger" in envelope

    # Determine whether the plan has synthesis_dimensions.
    plan_has_synth = False
    if plan_path is not None and plan_path.exists():
        plan_has_synth = _has_synthesis_dimensions(plan_path)

    # Collect dimension keys from synthesis_attestation.
    synth_raw = envelope.get("synthesis_attestation")
    synth_keys: list[str] = []
    if isinstance(synth_raw, dict):
        synth_keys = list(synth_raw.keys())
    elif isinstance(synth_raw, list):
        for entry in synth_raw:
            if isinstance(entry, dict):
                key = entry.get("name") or entry.get("dimension_name") or entry.get("id") or ""
                if key:
                    synth_keys.append(str(key))

    # Rule 1: if plan has synthesis_dimensions OR the envelope itself has
    # synthesis_attestation entries, ledger must be present.
    # When plan_path is provided, we use the plan's synthesis_dimensions block
    # as the authoritative signal. When plan_path is absent, we fall back to
    # checking whether synthesis_attestation has any entries (envelope-only mode).
    requires_ledger = plan_has_synth or (plan_path is None and len(synth_keys) > 0)
    if requires_ledger and not has_ledger_field:
        source_hint = (
            "the plan has a synthesis_dimensions block"
            if plan_has_synth
            else "synthesis_attestation has entries"
        )
        findings.append(Finding(
            "error",
            f"decision_ledger field is absent but {source_hint} — "
            "every synthesis dimension must have a ledger entry",
        ))
        return findings  # no point checking shape if field is missing

    # Rule 2: if no synth dims and no ledger, all good.
    if not requires_ledger and not has_ledger_field:
        return findings

    # Rule 3: ledger is present — validate shape.
    if ledger is None:
        # null is treated the same as missing when synth dims exist.
        if plan_has_synth and synth_keys:
            findings.append(Finding(
                "error",
                "decision_ledger is null but synthesis_attestation has dimensions — "
                "provide an array (empty [] only when no synthesis_dimensions in plan)",
            ))
        return findings

    if not isinstance(ledger, list):
        findings.append(Finding("error", "decision_ledger must be an array"))
        return findings

    # Cross-check: every synthesis_attestation key must have a matching ledger entry.
    if plan_has_synth or synth_keys:
        ledger_dims = {
            str(entry.get("dimension", "")) for entry in ledger if isinstance(entry, dict)
        }
        for key in synth_keys:
            if key not in ledger_dims:
                findings.append(Finding(
                    "error",
                    f"synthesis_attestation dimension '{key}' has no matching entry in "
                    "decision_ledger (match by 'dimension' field)",
                ))

    # Per-entry field validation.
    for idx, entry in enumerate(ledger):
        if not isinstance(entry, dict):
            findings.append(Finding("error", f"decision_ledger[{idx}] is not an object"))
            continue
        # Check all six required fields are present.
        missing_fields = REQUIRED_LEDGER_FIELDS - entry.keys()
        if missing_fields:
            findings.append(Finding(
                "error",
                f"decision_ledger[{idx}] (dimension='{entry.get('dimension', '?')}') "
                f"is missing required fields: {sorted(missing_fields)}",
            ))
            continue
        # Non-empty checks (except evidence_file which may be null).
        for field in ("dimension", "owner", "locked_value", "on_new_decision"):
            val = entry.get(field)
            if not val or not str(val).strip():
                findings.append(Finding(
                    "error",
                    f"decision_ledger[{idx}].{field} must be a non-empty string",
                ))
        # alternatives_rejected must be a non-empty array.
        alts = entry.get("alternatives_rejected")
        if not isinstance(alts, list) or len(alts) == 0:
            findings.append(Finding(
                "error",
                f"decision_ledger[{idx}].alternatives_rejected must be a non-empty array "
                "(use [\"none considered\"] if truly no alternative existed)",
            ))
        # on_new_decision enum check.
        ond = entry.get("on_new_decision", "")
        if ond not in VALID_ON_NEW_DECISION:
            findings.append(Finding(
                "error",
                f"decision_ledger[{idx}].on_new_decision='{ond}' is not a valid enum value; "
                f"must be one of: {sorted(VALID_ON_NEW_DECISION)}",
            ))
        # evidence_file: null OK only when owner == "implementer"; warn otherwise.
        ef = entry.get("evidence_file")
        owner = entry.get("owner", "")
        if ef is None and owner == "plan":
            findings.append(Finding(
                "warning",
                f"decision_ledger[{idx}].evidence_file is null but owner='plan' — "
                "plan-owned decisions should reference the file where they manifest",
            ))

    return findings


def run_ledger_check(
    envelope: "dict[str, Any]",
    plan_path: "Path | None" = None,
) -> "tuple[list[Finding], int]":
    """Run the ledger coverage check and return (findings, exit_code).

    exit_code: 0 = pass (no errors, warnings OK), 1 = any error finding.
    Warnings alone do not change exit code.
    """
    findings = check_ledger_coverage(envelope, plan_path)
    has_error = any(f.level == "error" for f in findings)
    return findings, (1 if has_error else 0)


# ---------------------------------------------------------------------------
# Ledger self-test helpers
# ---------------------------------------------------------------------------

def _run_ledger_self_test() -> int:
    """Minimal inline self-test for the ledger check. Returns 0 on success."""
    failures: list[str] = []

    _GOOD_ENTRY_1 = {
        "dimension": "placement_MetricCard",
        "owner": "plan",
        "locked_value": "after `<SummaryRow>` in components/dashboard/MetricCard.tsx",
        "alternatives_rejected": ["before `<SummaryRow>`"],
        "evidence_file": "components/dashboard/MetricCard.tsx",
        "on_new_decision": "flag",
    }
    _GOOD_ENTRY_2 = {
        "dimension": "cta_tier_export_button",
        "owner": "plan",
        "locked_value": "secondary",
        "alternatives_rejected": ["primary — too visually dominant"],
        "evidence_file": "components/dashboard/MetricCard.tsx",
        "on_new_decision": "flag",
    }

    # Case A: envelope with ledger, no plan_path — shape valid → pass.
    env_good = {
        "synthesis_attestation": {
            "placement_MetricCard": "applied",
            "cta_tier_export_button": "applied",
        },
        "decision_ledger": [_GOOD_ENTRY_1, _GOOD_ENTRY_2],
    }
    findings, code = run_ledger_check(env_good, plan_path=None)
    if code != 0:
        failures.append(f"Case A (good envelope, no plan): expected exit 0, got {code}; findings={findings}")

    # Case B: envelope with empty synthesis_attestation and no ledger — absence is fine → pass.
    # (When synthesis_attestation has no entries, ledger is not required.)
    env_no_ledger = {
        "synthesis_attestation": {},
        "novel_decisions": [],
    }
    findings, code = run_ledger_check(env_no_ledger, plan_path=None)
    if code != 0:
        failures.append(f"Case B (empty synth_attestation, no ledger): expected exit 0, got {code}; findings={findings}")

    # Case C: envelope with ledger but missing a required field → error.
    env_missing_field = {
        "synthesis_attestation": {"placement_x": "applied"},
        "decision_ledger": [
            {
                "dimension": "placement_x",
                "owner": "plan",
                # locked_value intentionally omitted
                "alternatives_rejected": ["none considered"],
                "evidence_file": "foo.tsx",
                "on_new_decision": "flag",
            }
        ],
    }
    findings, code = run_ledger_check(env_missing_field, plan_path=None)
    if code != 1:
        failures.append(f"Case C (missing required field): expected exit 1, got {code}; findings={findings}")

    # Case D: bad on_new_decision enum → error.
    env_bad_enum = {
        "synthesis_attestation": {"cta_tier_x": "applied"},
        "decision_ledger": [
            {
                "dimension": "cta_tier_x",
                "owner": "plan",
                "locked_value": "primary",
                "alternatives_rejected": ["secondary"],
                "evidence_file": "foo.tsx",
                "on_new_decision": "INVALID_VALUE",
            }
        ],
    }
    findings, code = run_ledger_check(env_bad_enum, plan_path=None)
    if code != 1:
        failures.append(f"Case D (bad on_new_decision): expected exit 1, got {code}; findings={findings}")

    # Case E: envelope with no synthesis_attestation and no ledger → pass.
    env_empty = {"synthesis_attestation": {}, "novel_decisions": []}
    findings, code = run_ledger_check(env_empty, plan_path=None)
    if code != 0:
        failures.append(f"Case E (empty envelope, no ledger): expected exit 0, got {code}; findings={findings}")

    # Case F: ledger entry has null evidence_file with owner=plan → warning but exit 0.
    env_null_ef = {
        "synthesis_attestation": {"copy_tone_x": "applied"},
        "decision_ledger": [
            {
                "dimension": "copy_tone_x",
                "owner": "plan",
                "locked_value": "professional",
                "alternatives_rejected": ["casual"],
                "evidence_file": None,  # null + owner=plan → warning
                "on_new_decision": "flag",
            }
        ],
    }
    findings, code = run_ledger_check(env_null_ef, plan_path=None)
    if code != 0:
        failures.append(f"Case F (null evidence_file, owner=plan): expected exit 0 (warning only), got {code}; findings={findings}")
    if not any(f.level == "warning" for f in findings):
        failures.append(f"Case F: expected a warning for null evidence_file with owner=plan, got {findings}")

    if failures:
        print("attestation_lint ledger-self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("attestation_lint ledger-self-test PASS (6 cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
