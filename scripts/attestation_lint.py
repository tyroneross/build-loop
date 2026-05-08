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
      "status": "pass" | "fail" | "unverifiable",
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


def parse_unified_diff(diff_text: str) -> dict[str, FileDiff]:
    """Parse a unified-diff blob into {post-image-path: FileDiff}."""
    files: dict[str, FileDiff] = {}
    current: FileDiff | None = None
    pre_lineno = 0
    post_lineno = 0
    in_hunk = False

    for line in diff_text.splitlines():
        m = DIFF_GIT_HEADER_RE.match(line)
        if m:
            current = FileDiff(m.group(1), m.group(2))
            files[current.path] = current
            in_hunk = False
            current.raw_chunks.append(line)
            continue
        if current is None:
            continue
        # Skip metadata lines (index, ---, +++, similarity, etc.) — record raw.
        if line.startswith(("index ", "--- ", "+++ ", "new file", "deleted file",
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


def normalize_attestation(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of attestation records, one per dimension entry.

    Each record carries: name, dimension, claim (or None), claimed_status."""
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
#   "after `<NewsCard>` in app/components/Feed.tsx"
#   "before <Header /> in src/Header.tsx"
PLACEMENT_RE = re.compile(
    r"\b(?P<rel>after|before|inside|within)\b\s*"
    r"`?<?(?P<anchor>[A-Za-z_][\w.-]*)\s*/?>?`?"
    r"\s+in\s+`?(?P<path>[\w./@-]+)`?",
    re.IGNORECASE,
)


def verify_placement(record: dict[str, Any], files: dict[str, FileDiff]) -> dict[str, Any]:
    claim = record.get("claim") or ""
    m = PLACEMENT_RE.search(claim)
    if not m:
        return {
            "status": "unverifiable",
            "reason": "claim text missing recognizable 'after/before <Anchor> in <path>' pattern",
            "evidence": {"file": None, "line": None, "snippet": None},
        }
    rel = m.group("rel").lower()
    anchor = m.group("anchor")
    path_claim = m.group("path")

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
    anchor_pat = re.compile(rf"<\s*{re.escape(anchor)}\b", re.IGNORECASE)
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
# Match common ways a CTA tier is encoded:
#   className="...primary..."  variant="primary"  tier="primary"
#   class="btn-primary"        data-tier="primary"
CTA_PATTERNS = [
    re.compile(r"""(?:className|class|variant|tier|data-tier)\s*=\s*["']([^"']+)["']""", re.IGNORECASE),
    # Standalone token (e.g. tailwind: bg-primary, btn-primary)
    re.compile(r"\b(?:btn-|bg-|border-|text-)?(primary|secondary|tertiary)\b", re.IGNORECASE),
]


def verify_cta_tier(record: dict[str, Any], files: dict[str, FileDiff]) -> dict[str, Any]:
    claim = (record.get("claim") or "").strip().lower()
    # Pull a tier-class token from the claim.
    tier: str | None = None
    for cls in CTA_TIER_CLASSES:
        if cls in claim:
            tier = cls
            break
    if tier is None:
        return {
            "status": "unverifiable",
            "reason": "claim does not name a known cta_tier class (primary|secondary|tertiary)",
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


def lint_one(record: dict[str, Any], files: dict[str, FileDiff]) -> dict[str, Any]:
    base = {
        "name": record["name"],
        "dimension": record["dimension"],
        "claim": record.get("claim"),
        "claimed_status": record.get("claimed_status"),
    }
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
        result = verify_placement(record, files)
    elif dim == "cta_tier":
        result = verify_cta_tier(record, files)
    elif dim == "visual_weight":
        result = verify_visual_weight(record, files)
    else:  # defensive — should be unreachable given dim checks above
        result = {"status": "unverifiable",
                  "reason": "no verifier registered for dimension",
                  "evidence": {"file": None, "line": None, "snippet": None}}
    return {**base, **result}


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "fail": 0, "unverifiable": 0, "total": len(results)}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return summary


def determine_exit(summary: dict[str, int]) -> int:
    if summary["fail"] > 0:
        return 1
    if summary["pass"] == 0 and summary["unverifiable"] > 0:
        return 2
    return 0


def run_lint(diff_text: str, envelope: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    files = parse_unified_diff(diff_text)
    records = normalize_attestation(envelope)
    results = [lint_one(r, files) for r in records]
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


def run_self_test() -> int:
    """Inline self-test. Returns 0 on success, 1 on any failure."""
    failures: list[str] = []

    # Pass case
    results, summary, code = run_lint(SELF_TEST_DIFF, SELF_TEST_ENVELOPE_PASS)
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
    results, summary, code = run_lint(SELF_TEST_DIFF, SELF_TEST_ENVELOPE_FAIL)
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
    })
    if summary["fail"] != 0 or summary["pass"] != 0 or summary["unverifiable"] != 2:
        failures.append(f"unverifiable-only: bad summary {summary}")
    if code != 2:
        failures.append(f"unverifiable-only exit code should be 2, got {code}")

    # Empty envelope
    results, summary, code = run_lint(SELF_TEST_DIFF, {"synthesis_attestation": {}})
    if summary["total"] != 0 or code != 0:
        failures.append(f"empty envelope: total should be 0 with exit 0, got total={summary['total']} code={code}")

    # Array form
    results, summary, code = run_lint(SELF_TEST_DIFF, {
        "synthesis_attestation": [
            {"name": "placement_NewsBanner",
             "dimension": "placement",
             "applied": "after `<NewsCard>` in app/components/Feed.tsx"},
        ]
    })
    if summary["pass"] != 1 or code != 0:
        failures.append(f"array form: should pass once, got summary={summary} code={code}")

    if failures:
        print("attestation_lint self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("attestation_lint self-test PASS (5 cases)")
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
    p.add_argument("--self-test", action="store_true", help="Run the inline self-test and exit.")
    args = p.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.diff or not args.envelope:
        p.error("--diff and --envelope are required (or use --self-test)")
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

    try:
        results, summary, exit_code = run_lint(diff_text, envelope)
    except Exception as e:  # noqa: BLE001
        print(f"attestation-lint: error: {e}", file=sys.stderr)
        return 2

    payload = {
        "diff": args.diff,
        "envelope": args.envelope,
        "summary": summary,
        "results": results,
        "exit_code": exit_code,
    }
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


if __name__ == "__main__":
    sys.exit(main())
