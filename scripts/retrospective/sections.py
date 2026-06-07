# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""sections.py — assemble the 9 retrospective sections.

This is the core synthesis step: read the run's transcript JSONL + state.json
+ intent + plan, derive each of the 9 named sections, return them as a dict
the writer module turns into the markdown body.

Pure stdlib. Deterministic where possible; reasoning sections include the
underlying signals so the agent's prompt can elaborate when invoked as an
LLM-driven retrospective. When invoked headlessly (e.g. from the CLI without
a model), the sections emit their captured signals verbatim.

Section keys (exactly these 9, in order):
  1. lessons_learned
  2. key_takeaways
  3. recommendations
  4. what_could_be_better
  5. what_went_well
  6. what_went_well_by_accident   (split: planned-and-earned vs lucky)
  7. what_should_be_enforced       (drives enforce-candidate emissions)
  8. user_prompts_and_repeats      (lists user prompts + flags repeated-≥2×)
  9. issues_with_causal_tree       (each issue → 5-whys-style chain)
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Transcript reading + user-prompt extraction.
# ---------------------------------------------------------------------------

# Words we strip when normalizing a user prompt for repetition clustering.
_NORMALIZE_STRIP_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")
_NORMALIZE_LEN = 100  # first N chars of the normalized text used for clustering


def _normalize(text: str) -> str:
    """Lowercase + strip punctuation + collapse whitespace; return first
    ``_NORMALIZE_LEN`` chars. Used as the clustering key for prompted-≥2×."""
    s = (text or "").lower()
    s = _NORMALIZE_STRIP_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s[:_NORMALIZE_LEN]


def _iter_user_messages(transcript_jsonl: Path | None) -> Iterable[dict[str, Any]]:
    """Yield each user message record from a Claude Code transcript JSONL.

    A user record has ``type == "user"`` and a ``message.content`` block.
    Tool-result-only user records (no human text) are skipped. Returns empty
    iter if the transcript is None or unreadable.
    """
    if transcript_jsonl is None:
        return
    try:
        with open(transcript_jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user":
                    continue
                # Skip hook-injected / command-scaffolding / skill-load records.
                # Claude Code marks these with top-level isMeta=true even though
                # role stays "user". Counting them as human prompts corrupts the
                # "prompted ≥2×" detection (Stop-hook feedback repeats per turn)
                # and produces bogus enforce-candidates. See v0.29.0 retrospective
                # over transcript dfe491e3-…: 23 raw user records → 6 isMeta noise
                # (4× Stop-hook feedback + 1 SPDX skill body + 1 skill base-dir)
                # + 17 real human prompts.
                if rec.get("isMeta"):
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                # Content is either a string (legacy) or a list of blocks.
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = []
                    for blk in content:
                        if isinstance(blk, dict):
                            if blk.get("type") == "text" and isinstance(blk.get("text"), str):
                                parts.append(blk["text"])
                            # tool_result blocks are skipped — not human prose.
                        elif isinstance(blk, str):
                            parts.append(blk)
                    text = "\n".join(parts).strip()
                if text.strip():
                    yield {
                        "ts": rec.get("timestamp"),
                        "text": text.strip(),
                    }
    except OSError:
        return


def extract_user_prompts(transcript_jsonl: Path | None) -> list[dict[str, Any]]:
    """Return ordered list of user prompts (skipping tool-result-only turns)."""
    return list(_iter_user_messages(transcript_jsonl))


def cluster_repeated_prompts(
    prompts: list[dict[str, Any]],
    *,
    threshold: int = 2,
) -> list[dict[str, Any]]:
    """Group prompts whose normalized prefix appears ``>= threshold`` times.

    Returns a list of clusters, each:
        {"normalized": str, "count": int, "examples": [first text, last text]}
    Sorted by count descending; ties broken by first-occurrence.
    """
    if not prompts:
        return []
    norm_to_indices: dict[str, list[int]] = {}
    for i, p in enumerate(prompts):
        key = _normalize(p["text"])
        if not key:
            continue
        norm_to_indices.setdefault(key, []).append(i)
    clusters: list[dict[str, Any]] = []
    for key, indices in norm_to_indices.items():
        if len(indices) >= threshold:
            clusters.append({
                "normalized": key,
                "count": len(indices),
                "examples": [prompts[indices[0]]["text"], prompts[indices[-1]]["text"]],
                "first_index": indices[0],
            })
    # Sort by count desc, then first occurrence asc.
    clusters.sort(key=lambda c: (-c["count"], c["first_index"]))
    return clusters


# ---------------------------------------------------------------------------
# Section assembly.
# ---------------------------------------------------------------------------

# Stable order of the 9 named sections.
SECTION_KEYS = [
    "lessons_learned",
    "key_takeaways",
    "recommendations",
    "what_could_be_better",
    "what_went_well",
    "what_went_well_by_accident",
    "what_should_be_enforced",
    "user_prompts_and_repeats",
    "issues_with_causal_tree",
]

# Display titles (used by write.py).
SECTION_TITLES = {
    "lessons_learned":            "Lessons learned",
    "key_takeaways":              "Key takeaways",
    "recommendations":            "Recommendations",
    "what_could_be_better":       "What could be done better",
    "what_went_well":             "What went well",
    "what_went_well_by_accident": "What went well by accident",
    "what_should_be_enforced":    "What should be enforced",
    "user_prompts_and_repeats":   "User prompts this thread (with repeats)",
    "issues_with_causal_tree":    "Issues (with causal tree)",
}

_PASS_VERDICTS = {"yay", "approve", "pass"}
_HARD_FAIL_VERDICTS = {
    "nay",
    "block",
    "fail",
    "blocked",
    "rethink",
    "new_approach",
    "suggest_correction",
    "look-again",
    "look_again",
}
_ACTIONABLE_VERDICTS = _HARD_FAIL_VERDICTS | {"suggest"}


def _append_unique(items: list[str], text: str | None) -> None:
    if text and text not in items:
        items.append(text)


def _run_identifier(run: dict[str, Any]) -> str | None:
    for key in ("run_id", "build_loop_id", "id"):
        value = run.get(key)
        if value:
            return str(value)
    return None


def _runs_for_run_id(state_json: dict[str, Any], run_id: str | None) -> list[dict[str, Any]]:
    """Return run records for this retrospective.

    Prefer run records matching ``run_id``. When none match, fall back to the
    latest run to preserve the historical behavior and support hook-only state.
    """
    runs = [r for r in (state_json.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        return []
    if run_id:
        matched = [r for r in runs if _run_identifier(r) == str(run_id)]
        if matched:
            return matched
    return [runs[-1]]


def _iter_judge_decisions(runs: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for run in runs:
        for judge in run.get("judge_decisions") or []:
            if isinstance(judge, dict):
                yield judge


def _judge_context(judge: dict[str, Any]) -> str:
    jid = judge.get("judge_id") or "judge"
    checkpoint = (judge.get("checkpoint_id") or "").strip()
    return f"**{jid}** at `{checkpoint}`" if checkpoint else f"**{jid}**"


def _variance_summary(variance: Any) -> str | None:
    if isinstance(variance, dict):
        summary = (
            variance.get("why_it_matters")
            or variance.get("summary")
            or variance.get("title")
            or variance.get("id")
        )
        if not summary:
            return None
        severity = variance.get("severity")
        return f"{severity}: {summary}" if severity else str(summary)
    if isinstance(variance, str):
        return variance
    return None


def _meta_guidance(judge: dict[str, Any]) -> list[str]:
    guidance = judge.get("meta_guidance") or []
    if isinstance(guidance, str):
        return [guidance]
    if isinstance(guidance, list):
        return [str(item) for item in guidance if item]
    return []


def _judge_signal_summaries(judge: dict[str, Any]) -> list[str]:
    """Return issue/lesson summaries carried by an actionable judge decision."""
    verdict = (judge.get("verdict") or "").lower()
    variances = [
        summary for summary in (_variance_summary(v) for v in (judge.get("variances") or []))
        if summary
    ]
    if variances:
        return variances
    if verdict in _HARD_FAIL_VERDICTS:
        guidance = _meta_guidance(judge)
        if guidance:
            return guidance
        outcome = judge.get("outcome")
        if outcome:
            return [str(outcome)]
        return [f"judge returned verdict `{verdict}`"]
    return []


def _format_user_prompts_section(prompts: list[dict[str, Any]], clusters: list[dict[str, Any]]) -> str:
    """Build section 8 (user_prompts_and_repeats).

    Lists each user prompt with its turn ordinal, then a 'Repeated ≥N×' block.
    """
    if not prompts:
        return ("_(no user prompts captured — transcript missing or empty)_")
    lines = ["### All user prompts in this thread", ""]
    for i, p in enumerate(prompts, start=1):
        excerpt = p["text"][:200].replace("\n", " ")
        if len(p["text"]) > 200:
            excerpt += "…"
        lines.append(f"{i}. {excerpt}")
    lines.append("")
    if clusters:
        lines.append("### Prompted ≥2× — surfaced for enforce-candidate routing")
        lines.append("")
        for c in clusters:
            lines.append(f"- **{c['count']}×** — _{c['examples'][0][:160]}…_" if len(c['examples'][0]) > 160 else f"- **{c['count']}×** — _{c['examples'][0]}_")
        lines.append("")
    else:
        lines.append("_No prompts repeated this thread._")
    return "\n".join(lines)


def _format_issues_section(state_json: dict[str, Any], run_id: str | None = None) -> str:
    """Build section 9 (issues_with_causal_tree).

    Reads issues from the current run's ``judge_decisions[]`` + any
    ``review_findings``/``failures``/``iterate_failures`` arrays. Each issue
    is rendered with a stub causal-tree the agent's LLM body can elaborate.
    Headless mode shows the captured signals as-is.
    """
    runs = _runs_for_run_id(state_json, run_id)
    candidates: list[str] = []
    for j in _iter_judge_decisions(runs):
        verdict = (j.get("verdict") or "").lower()
        if verdict in _ACTIONABLE_VERDICTS:
            for why in _judge_signal_summaries(j):
                candidates.append(f"- {_judge_context(j)} — {why}")
    for run in runs:
        for f in run.get("review_findings") or run.get("failures") or []:
            candidates.append(f"- {f}")
        for f in run.get("iterate_failures") or []:
            candidates.append(f"- iterate-failure: {f}")
    if not candidates:
        return "_No issues surfaced this run._"
    lines = ["### Issues surfaced", "", *candidates, "",
             "### Causal-tree analysis", "",
             "Each issue above is traced to root cause via a 5-whys / causal-tree pass at "
             "Review-A (independent-auditor). Stub recorded here; the retrospective "
             "synthesizer agent's LLM body elaborates each chain on dispatch. "
             "Headless mode surfaces the captured signal verbatim."]
    return "\n".join(lines)


def _format_simple_bullet_section(items: list[str], empty_msg: str) -> str:
    if not items:
        return f"_{empty_msg}_"
    return "\n".join(f"- {it}" for it in items)


def _enforce_signals(
    clusters: list[dict[str, Any]],
    state_json: dict[str, Any],
    run_id: str | None = None,
) -> list[str]:
    """Surface candidates for the 'what should be enforced' section.

    Two sources:
      - Any prompted-≥2× cluster → "enforce: <normalized>" candidate.
      - Any ``judge_decisions`` with verdict 'nay'/'block' on a recurring
        rule → "enforce: <rule>" candidate.
    """
    out: list[str] = []
    for c in clusters:
        excerpt = c["examples"][0][:120]
        out.append(f"Make this an enforced default instead of user-prompted: _{excerpt}_")
    for j in _iter_judge_decisions(_runs_for_run_id(state_json, run_id)):
        if (j.get("verdict") or "").lower() in _ACTIONABLE_VERDICTS and _judge_signal_summaries(j):
            rule = j.get("checkpoint_id") or j.get("judge_id") or "rule"
            out.append(f"Enforce gate: {rule} (failed this run)")
    return out


def build(
    transcript_jsonl: Path | None,
    state_json: dict[str, Any] | None,
    intent_md: str | None,
    plan_md: str | None,
    run_id: str,
    *,
    prompted_threshold: int = 2,
) -> dict[str, Any]:
    """Build the 9 named sections.

    Returns a dict with keys ``SECTION_KEYS`` (all present) plus two
    metadata keys: ``enforce_candidates`` (list[str]) and ``meta``.

    Args:
        transcript_jsonl: path to the Claude Code transcript JSONL, or None.
        state_json:       parsed contents of ``.build-loop/state.json``, or None.
        intent_md:        contents of ``.build-loop/intent.md``, or None.
        plan_md:          contents of ``.build-loop/plan.md``, or None.
        run_id:           the build-loop run id.
        prompted_threshold: minimum repetition count to surface a cluster
                            (default 2 — matches the spec's "≥2×").

    Determinism:
        - Same inputs → same outputs.
        - Headless mode (no LLM): the reasoning sections (1-7) carry signal
          summaries; sections 8-9 are fully derived.
    """
    state_json = state_json or {}
    intent_md = intent_md or ""
    plan_md = plan_md or ""

    prompts = extract_user_prompts(transcript_jsonl)
    clusters = cluster_repeated_prompts(prompts, threshold=prompted_threshold)
    run_records = _runs_for_run_id(state_json, run_id)
    last_run = run_records[-1] if run_records else {}
    judges = list(_iter_judge_decisions(run_records))
    enforce = _enforce_signals(clusters, state_json, run_id)

    sections: dict[str, Any] = {}

    # 1. lessons_learned — derived from run lessons, judge findings + clusters.
    lessons: list[str] = []
    for run in run_records:
        for lesson in run.get("lessons") or []:
            _append_unique(lessons, str(lesson))
    for judge in judges:
        for summary in _judge_signal_summaries(judge):
            _append_unique(lessons, f"{judge.get('judge_id') or 'judge'}: {summary}")
    for c in clusters:
        _append_unique(lessons, f"Prompted {c['count']}× — _{c['examples'][0][:100]}_")
    sections["lessons_learned"] = _format_simple_bullet_section(
        lessons, "no lessons captured (transcript empty or no prior signals)"
    )

    # 2. key_takeaways — surface the intent's restated line + plan headline.
    takeaways: list[str] = []
    m = re.search(r"^## Restated intent.*?\n+([^\n]+)", intent_md, re.M | re.S)
    if m:
        takeaways.append(f"Intent: {m.group(1).strip()}")
    m2 = re.search(r"^# (.+)$", plan_md, re.M)
    if m2:
        takeaways.append(f"Plan headline: {m2.group(1).strip()}")
    if last_run.get("outcome"):
        takeaways.append(f"Run outcome: {last_run['outcome']}")
    sections["key_takeaways"] = _format_simple_bullet_section(
        takeaways, "no key takeaways captured"
    )

    # 3. recommendations — every enforce-candidate is also a recommendation.
    recs = list(enforce)
    sections["recommendations"] = _format_simple_bullet_section(
        recs, "no recommendations this run"
    )

    # 4. what_could_be_better — pull failures/iterate_failures.
    bads: list[str] = []
    for run in run_records:
        for f in run.get("failures") or []:
            _append_unique(bads, str(f))
        for f in run.get("iterate_failures") or []:
            _append_unique(bads, f"iterate-failure: {f}")
    sections["what_could_be_better"] = _format_simple_bullet_section(
        bads, "no failures captured this run"
    )

    # 5. what_went_well — judges with verdict yay/pass.
    # Dedupe (order-preserving) and only include checkpoint_id when non-empty
    # so we don't render "auditor approved " with a trailing space.
    wins: list[str] = []
    seen_wins: set[str] = set()
    for j in judges:
        verdict = (j.get("verdict") or "").lower()
        if verdict not in _PASS_VERDICTS:
            continue
        judge_id = j.get("judge_id") or "judge"
        ck = (j.get("checkpoint_id") or "").strip()
        win = f"{judge_id} approved {ck}" if ck else f"{judge_id} approved"
        if win in seen_wins:
            continue
        seen_wins.add(win)
        wins.append(win)
    sections["what_went_well"] = _format_simple_bullet_section(
        wins, "no positive judge verdicts captured this run"
    )

    # 6. what_went_well_by_accident — split planned-and-earned vs lucky.
    earned: list[str] = []
    lucky: list[str] = []
    for run in run_records:
        for item in run.get("planned_wins") or []:
            _append_unique(earned, str(item))
        for item in run.get("unplanned_wins") or []:
            _append_unique(lucky, str(item))
    if not earned and not lucky:
        sections["what_went_well_by_accident"] = "_(no signals captured — populate from agent reflection)_"
    else:
        lines = []
        if earned:
            lines.append("**Planned and earned**")
            lines += [f"- {e}" for e in earned]
        if lucky:
            lines.append("")
            lines.append("**Lucky / unplanned good**")
            lines += [f"- {x}" for x in lucky]
        sections["what_went_well_by_accident"] = "\n".join(lines)

    # 7. what_should_be_enforced — enforce_candidates promoted to a section.
    sections["what_should_be_enforced"] = _format_simple_bullet_section(
        enforce, "no enforce-candidates this run"
    )

    # 8. user_prompts_and_repeats — full list + clusters.
    sections["user_prompts_and_repeats"] = _format_user_prompts_section(prompts, clusters)

    # 9. issues_with_causal_tree — judge-flagged issues + stubs.
    sections["issues_with_causal_tree"] = _format_issues_section(state_json, run_id)

    sections["enforce_candidates"] = enforce
    sections["meta"] = {
        "run_id": run_id,
        "prompt_count": len(prompts),
        "cluster_count": len(clusters),
        "judge_decision_count": len(judges),
        "transcript_present": transcript_jsonl is not None,
    }
    return sections
