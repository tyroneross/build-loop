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
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable

from tool_trace import ingestion_incomplete, summarize_path as summarize_tool_trace

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


from transcript_adapter import _codex_event, iter_events, human_text as _human_text


def _normalize_transcript_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    return _codex_event(rec) if rec.get("type") in {"response_item", "event_msg"} else rec


def _iter_transcript_records(transcript_jsonl):
    """Read the shared canonical event stream, or consume an existing stream."""
    if transcript_jsonl is None:
        return
    if isinstance(transcript_jsonl, (str, Path)):
        yield from iter_events(Path(transcript_jsonl))
    else:
        yield from transcript_jsonl


def _content_blocks(rec: dict[str, Any]) -> list[Any]:
    """Return the record's message content blocks, or [] when not a block list."""
    content = (rec.get("message") or {}).get("content")
    return content if isinstance(content, list) else []



def _iter_user_messages(transcript_jsonl: Path | None) -> Iterable[dict[str, Any]]:
    """Yield each human-authored user message as ``{ts, text}``.

    A user record has ``type == "user"`` and a ``message.content`` block.
    Tool-result-only user records (no human text) are skipped.

    Hook-injected / command-scaffolding / skill-load records are skipped too:
    Claude Code marks these with top-level ``isMeta=true`` even though role
    stays "user". Counting them as human prompts corrupts the "prompted >=2x"
    detection (Stop-hook feedback repeats per turn) and produces bogus
    enforce-candidates. See v0.29.0 retrospective over transcript dfe491e3-...:
    23 raw user records -> 6 isMeta noise (4x Stop-hook feedback + 1 SPDX skill
    body + 1 skill base-dir) + 17 real human prompts.
    """
    for rec in _iter_transcript_records(transcript_jsonl):
        if rec.get("type") != "user" or rec.get("isMeta"):
            continue
        text = _human_text((rec.get("message") or {}).get("content"))
        if text:
            yield {"ts": rec.get("timestamp"), "text": text}


def extract_user_prompts(transcript_jsonl: Path | None) -> list[dict[str, Any]]:
    """Return ordered list of user prompts (skipping tool-result-only turns)."""
    return list(_iter_user_messages(transcript_jsonl))


# ---------------------------------------------------------------------------
# Tool / plugin / automation signal extraction (deterministic — no LLM).
#
# These feed the plugin-observations (§10) and automation-candidate (§11)
# sections plus the app-issue enrichment of §4/§9. Everything here is pure
# regex/counting over the transcript so it runs for FREE at SessionEnd; the
# agent's LLM body only narrates on top of these captured signals.
# ---------------------------------------------------------------------------

# One host-neutral definition shared with the SessionEnd sweep.
from transcript_adapter import GENERIC_TOOLS as _GENERIC_TOOLS

_MCP_PLUGIN_RE = re.compile(r"^mcp__(?:plugin_)?([a-z0-9]+)")
_ISSUE_MARKER_RE = re.compile(
    r"(Traceback \(most recent call last\)|"
    r"\bError:|\bERROR\b|\bFAILED\b|\bFAIL\b|"
    r"AssertionError|Exception|\bnot found\b|command not found|"
    r"exit code [1-9]|non-zero exit|❌)",
)
_ISSUE_SIGNAL_CAP = 8
_AUTOMATION_CANDIDATE_CAP = 6


def _tool_plugin_label(name: str) -> str | None:
    """Best-effort plugin/owner label for a tool name.

    ``mcp__plugin_ibr_ibr__scan`` → ``ibr`` · ``Skill(build-loop:run)`` handled
    by the caller. Returns None for plain core tools (no owning plugin).
    """
    m = _MCP_PLUGIN_RE.match(name or "")
    return m.group(1) if m else None


def _iter_assistant_tool_uses(transcript_jsonl: Path | None) -> Iterable[dict[str, Any]]:
    """Yield each assistant ``tool_use`` block: ``{kind: "use", id, name, input}``.

    Also yields synthetic records for the ``tool_result`` error side (from user
    records) tagged ``{"kind": "error", "tool_use_id": ...}`` so the caller can
    attribute failures back to the tool that produced them.
    """
    for rec in _iter_transcript_records(transcript_jsonl):
        rtype = rec.get("type")
        for blk in _content_blocks(rec):
            if not isinstance(blk, dict):
                continue
            btype = blk.get("type")
            if rtype == "assistant" and btype == "tool_use":
                yield {
                    "kind": "use",
                    "id": blk.get("id"),
                    "name": blk.get("name") or "",
                    "input": blk.get("input") or {},
                }
            elif rtype == "user" and btype == "tool_result" and blk.get("is_error"):
                yield {"kind": "error", "tool_use_id": blk.get("tool_use_id")}


def _canonical_tool_label(rec: dict[str, Any]) -> str:
    """Human-readable label for a tool_use record.

    ``Skill(build-loop:run)`` · ``Task(implementer)`` · ``ibr:scan`` (MCP) ·
    else the bare tool name. Used both for usage counts and sequence tokens.
    """
    name = rec.get("name") or ""
    inp = rec.get("input") if isinstance(rec.get("input"), dict) else {}
    if name == "Skill":
        skill = inp.get("skill") or inp.get("command") or ""
        return f"Skill({skill})" if skill else "Skill"
    if name in ("Task", "Agent"):
        sub = inp.get("subagent_type") or inp.get("description") or ""
        return f"{name}({sub})" if sub else name
    plugin = _tool_plugin_label(name)
    if plugin:
        tool = name.split("__")[-1]
        return f"{plugin}:{tool}"
    return name


class _ToolUsage:
    """Retain counts and correlation IDs, never the original tool payloads."""
    def __init__(self):
        self.tools, self.plugins, self.subagents = Counter(), Counter(), Counter()
        self.ids, self.error_ids = {}, []

    def consume(self, rec):
        if rec.get("kind") == "error":
            if rec.get("tool_use_id"):
                self.error_ids.append(rec["tool_use_id"])
            return
        label = _canonical_tool_label(rec)
        self.tools[label] += 1
        if rec.get("id"):
            self.ids[rec["id"]] = label
        name = rec.get("name") or ""
        inp = rec.get("input") if isinstance(rec.get("input"), dict) else {}
        if name == "Skill":
            skill = inp.get("skill") or inp.get("command") or ""
            if skill:
                self.plugins[str(skill).split(":", 1)[0]] += 1
        elif name in ("Task", "Agent"):
            sub = str(inp.get("subagent_type") or "")
            if sub:
                self.subagents[sub] += 1
                if ":" in sub:
                    self.plugins[sub.split(":", 1)[0]] += 1
        else:
            plugin = _tool_plugin_label(name)
            if plugin:
                self.plugins[plugin] += 1

    def result(self):
        errors = Counter(self.ids[eid] for eid in self.error_ids if eid in self.ids)
        return {"tools": dict(self.tools.most_common()), "plugins": dict(self.plugins.most_common()),
                "subagents": dict(self.subagents.most_common()), "errored": dict(errors.most_common()),
                "total_uses": sum(self.tools.values())}


class _ToolSequences:
    def __init__(self, window=3, min_repeat=3):
        if window < 1:
            raise ValueError("sequence window must be positive")
        self.window, self.min_repeat = window, min_repeat
        self.recent, self.grams = deque(maxlen=window), Counter()

    def consume(self, rec):
        if rec.get("kind") != "use":
            return
        self.recent.append(_canonical_tool_label(rec))
        if len(self.recent) == self.window:
            self.grams[tuple(self.recent)] += 1

    def result(self):
        results = []
        for gram, count in self.grams.most_common():
            if count >= self.min_repeat and not all(label in _GENERIC_TOOLS for label in gram):
                results.append({"sequence": list(gram), "count": count})
                if len(results) >= _AUTOMATION_CANDIDATE_CAP:
                    break
        return results


def extract_tool_usage(transcript_jsonl):
    usage = _ToolUsage()
    for item in _iter_assistant_tool_uses(transcript_jsonl):
        usage.consume(item)
    return usage.result()


def extract_tool_sequences(transcript_jsonl, *, window=3, min_repeat=3):
    sequences = _ToolSequences(window, min_repeat)
    for item in _iter_assistant_tool_uses(transcript_jsonl):
        sequences.consume(item)
    return sequences.result()


def _error_result_excerpt(blk: dict[str, Any]) -> str:
    """Return a one-line excerpt of an errored ``tool_result`` block, or "".

    Centres the excerpt on the first issue marker when one is present, so the
    signal is the error itself rather than whatever preamble came first.
    """
    body = blk.get("content")
    if isinstance(body, list):
        body = " ".join(
            b.get("text", "") for b in body
            if isinstance(b, dict) and b.get("type") == "text"
        )
    body = str(body or "").strip().replace("\n", " ")
    m = _ISSUE_MARKER_RE.search(body)
    window = body[max(0, m.start() - 20):m.start() + 100] if m else body[:120]
    return window.strip()


def _iter_error_results(transcript_jsonl: Path | None) -> Iterable[dict[str, Any]]:
    """Yield every ``tool_result`` block flagged ``is_error`` in the transcript."""
    for rec in _iter_transcript_records(transcript_jsonl):
        for blk in _content_blocks(rec):
            if isinstance(blk, dict) and blk.get("type") == "tool_result" and blk.get("is_error"):
                yield blk


def _add_issue(block, signals, seen):
    if len(signals) >= _ISSUE_SIGNAL_CAP:
        return
    excerpt = _error_result_excerpt(block)
    key = excerpt[:60].lower()
    if excerpt and key not in seen:
        seen.add(key)
        signals.append(excerpt)


def extract_issue_signals(transcript_jsonl):
    signals, seen = [], set()
    for block in _iter_error_results(transcript_jsonl):
        _add_issue(block, signals, seen)
        if len(signals) >= _ISSUE_SIGNAL_CAP:
            break
    return signals


def collect_transcript_signals(transcript_jsonl):
    """Feed all four reducers from one canonical stream without materializing it."""
    prompts, issues, seen = [], [], set()
    usage, sequences = _ToolUsage(), _ToolSequences()
    for record in _iter_transcript_records(transcript_jsonl):
        if record.get("type") == "user" and not record.get("isMeta"):
            text = _human_text((record.get("message") or {}).get("content"))
            if text:
                prompts.append({"ts": record.get("timestamp"), "text": text})
        for item in _iter_assistant_tool_uses((record,)):
            usage.consume(item)
            sequences.consume(item)
        for block in _content_blocks(record):
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                _add_issue(block, issues, seen)
    return {"prompts": prompts, "usage": usage.result(), "automation": sequences.result(), "issues": issues}


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
    "plugin_tooling_observations",
    "automation_candidates",
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
    "plugin_tooling_observations": "Plugin & tooling observations",
    "automation_candidates":      "Deterministic-automation candidates",
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
    from rally_point.build_loop_id import resolve_run_identity
    identity = resolve_run_identity({"execution": run})
    if identity.state == "conflicting":
        return None
    return identity.build_run_id or (str(run["id"]) if run.get("id") else None)


def _runs_for_run_id(state_json: dict[str, Any], run_id: str | None) -> list[dict[str, Any]]:
    """Return run records for this retrospective.

    Explicit IDs require an exact match. With no ID, latest-run selection is
    intentional; a missing explicit ID cannot borrow another run's evidence.
    """
    runs = [r for r in (state_json.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        return []
    if run_id:
        matched = [r for r in runs if _run_identifier(r) == str(run_id)]
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


def _format_user_prompts_section(
    prompts: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    transcript_note: str | None = None,
) -> str:
    """Build section 8 (user_prompts_and_repeats).

    Lists each user prompt with its turn ordinal, then a 'Repeated ≥N×' block.
    When the transcript was absent for a verified reason (RCA 2026-07-11: a run with no
    membership-passing transcript), state that reason explicitly rather than implying an
    empty thread.
    """
    if not prompts:
        if transcript_note:
            return f"_{transcript_note}_"
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


def _format_plugin_tooling_section(
    usage: dict[str, Any], transcript_note: str | None = None
) -> str:
    """Build §10 (plugin_tooling_observations).

    Deterministic view of which tools/plugins/skills/subagents ran, how often,
    and which produced errors. The 'errored' line is the objective performance
    signal; the LLM body appends concrete enhancement ideas per plugin.
    """
    tools = usage.get("tools") or {}
    if not tools:
        if transcript_note:
            return f"_{transcript_note}_"
        return "_(no tool activity captured — transcript missing or empty)_"
    lines: list[str] = ["### Tools & plugins exercised this thread", ""]
    top_tools = list(tools.items())[:12]
    for label, n in top_tools:
        lines.append(f"- `{label}` — {n}×")
    plugins = usage.get("plugins") or {}
    if plugins:
        lines += ["", "### By plugin / skill owner", ""]
        for owner, n in list(plugins.items())[:12]:
            lines.append(f"- **{owner}** — {n} call(s)")
    subs = usage.get("subagents") or {}
    if subs:
        lines += ["", "### Subagents dispatched", ""]
        for sub, n in list(subs.items())[:12]:
            lines.append(f"- {sub} — {n}×")
    errored = usage.get("errored") or {}
    if errored:
        lines += ["", "### Tools that returned errors (performance / reliability signal)", ""]
        for label, n in list(errored.items())[:12]:
            lines.append(f"- ⚠️ `{label}` — {n} error(s) — candidate for a fix or a guardrail")
    lines += ["", "_LLM body: for each plugin above, note how it performed and one "
              "concrete enhancement (a missing flag, a flaky path, a better default)._"]
    return "\n".join(lines)


def _format_automation_candidates_section(candidates: list[dict[str, Any]]) -> str:
    """Build §11 (automation_candidates).

    Recurring tool sequences that look like a manual ritual worth turning into
    a script/hook. Each is ALSO emitted as an enforce-candidate (kind:
    automation) so it feeds Phase 6 Learn / self-improvement-architect.
    """
    if not candidates:
        return "_No recurring automatable sequence detected this thread._"
    lines = ["### Recurring sequences worth scripting", ""]
    for c in candidates:
        seq = " → ".join(c.get("sequence", []))
        lines.append(f"- **×{c.get('count', '?')}** · `{seq}` — propose a script/hook to collapse this ritual")
    lines += ["", "_Each candidate is routed to `proposals/enforce-from-retro/` "
              "(kind: automation) for Phase 6 Learn to draft the script._"]
    return "\n".join(lines)


def _enforce_signals(
    clusters: list[dict[str, Any]],
    state_json: dict[str, Any],
    run_id: str | None = None,
) -> list[str]:
    """Surface candidates for the 'what should be enforced' section.

    Two sources:
      - Any prompted-≥2× cluster → "enforce: <normalized>" candidate.
      - Any ``judge_decisions`` with an actionable verdict AND a named
        ``checkpoint_id`` → "Enforce gate: <checkpoint> (failed this run)".

    A judge decision that names no checkpoint produces NO candidate. Until
    2026-09-12 this fell back to the literal word ``rule``, so a decision that
    named nothing still wrote ``Enforce gate: rule (failed this run)`` into
    ``.build-loop/proposals/enforce-from-retro/``. Observed in ross-labs-astro
    on 2026-09-12: two run-ids emitted the byte-identical placeholder, which is
    exactly the ≥2-distinct-run threshold ``enforce_retro_signals.scan`` uses,
    so Phase 6 Learn raised work order ``learn-4290d6ae4098`` against a
    candidate that specified no gate to enforce. ``judge_id`` is the auditor's
    identity rather than a gate name, so it is not a stand-in for the missing
    checkpoint — the real records that triggered this carried
    ``judge: independent-auditor`` and no checkpoint at all.

    KNOWN CONSEQUENCE, stated so nobody reads this as live code: **no producer
    currently writes ``checkpoint_id`` on an actionable verdict**, so this branch
    emits nothing today. Measured 2026-09-12 across build-loop's own 58 runs and
    ~315 judge decisions: exactly 6 carry a checkpoint (``review-g`` ×4,
    ``build``, ``integration-final``) and all 6 are ``approve``. The branch
    stays because the fix belongs at the producer of `judge_decisions` — a judge
    that fails a named gate should record that gate — not here. Until one does,
    every actionable decision still reaches the reader through §1 (lessons) and
    §9 (issues) via ``_judge_context``; only the promoted candidate is withheld.
    Backlog: ``BUIL-RETROSPECTIVE-m2b6y0c909hzfmq8vz2az``.
    """
    out: list[str] = []
    for c in clusters:
        excerpt = c["examples"][0][:120]
        out.append(f"Make this an enforced default instead of user-prompted: _{excerpt}_")
    for j in _iter_judge_decisions(_runs_for_run_id(state_json, run_id)):
        if (j.get("verdict") or "").lower() not in _ACTIONABLE_VERDICTS:
            continue
        if not _judge_signal_summaries(j):
            continue
        gate = str(j.get("checkpoint_id") or "").strip()
        if not gate:
            continue
        out.append(f"Enforce gate: {gate} (failed this run)")
    return out


# ---------------------------------------------------------------------------
# Cost-ledger routing evidence (run-scoped, deterministic, no inference).
# ---------------------------------------------------------------------------

def _nonnegative_int(value: Any) -> int | None:
    """Return an integer telemetry value only when it is an actual count."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _counted_labels(values: dict[str, Any]) -> str:
    """Render a stable compact ``model×count`` list, or an honest absence."""
    labels = [f"{label}×{count}" for label, count in sorted(values.items()) if isinstance(count, int)]
    return ", ".join(labels) if labels else "unreported"


def load_routing_evidence(ledger_path: Path | None, run_id: str) -> dict[str, Any]:
    """Summarize cost-ledger evidence for one run without treating gaps as zero.

    Cost-ledger rows can be emitted at dispatch and enriched after return under
    the same ``task_id``.  The latest non-null value for each field wins, so one
    attempt contributes at most once to token and quality totals.  Heuristic
    estimates remain outside measured-token totals by design.
    """
    attempts: dict[str, dict[str, Any]] = {}
    if ledger_path is None or not ledger_path.is_file():
        return {"attempts": 0, "ledger_available": False}
    try:
        with ledger_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or row.get("run_id") != run_id:
                    continue
                task_id = row.get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    continue
                merged = attempts.setdefault(task_id, {})
                # Append order is ledger chronology.  Preserve only explicit
                # values, so a sparse terminal row cannot erase a dispatch fact.
                for key, value in row.items():
                    if value is not None:
                        merged[key] = value
    except OSError:
        return {"attempts": 0, "ledger_available": False}

    requested_models: Counter[str] = Counter()
    actual_models: Counter[str] = Counter()
    requested_efforts: Counter[str] = Counter()
    actual_efforts: Counter[str] = Counter()
    verifier_verdicts: Counter[str] = Counter()
    measured_total = 0
    measured_attempts = 0
    observed_token_total = 0
    observed_token_attempts = 0
    quality_attempts = 0
    retries_total = 0
    retries_observed = 0
    rework_total = 0
    rework_observed = 0
    escaped_defects_total = 0
    escaped_defects_observed = 0

    for row in attempts.values():
        # Before the additive field existed, ``model`` was the dispatch value.
        # It remains usable as a requested model, never as an actual-model claim.
        requested_model = row.get("requested_model") or row.get("model")
        if isinstance(requested_model, str) and requested_model:
            requested_models[requested_model] += 1
        actual_model = row.get("actual_model")
        if isinstance(actual_model, str) and actual_model:
            actual_models[actual_model] += 1
        requested_effort = row.get("requested_effort")
        if isinstance(requested_effort, str) and requested_effort:
            requested_efforts[requested_effort] += 1
        actual_effort = row.get("actual_effort")
        if isinstance(actual_effort, str) and actual_effort:
            actual_efforts[actual_effort] += 1

        # Historical writers mislabeled output-only subtotals as complete.
        # Reconstruct completeness from the original buckets, never that label.
        buckets = [row.get("input_tokens"), row.get("output_tokens"),
                   row.get("cache_read_input_tokens", 0), row.get("cache_creation_input_tokens", 0)]
        provider_usage = row.get("tokens_source") in {"measured", "usage"}
        complete = all(_nonnegative_int(value) is not None for value in buckets)
        measured = sum(buckets) if provider_usage and complete else None
        observed = None
        if provider_usage:
            reported = [_nonnegative_int(row.get(key)) for key in
                        ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
            if any(value is not None for value in reported):
                observed = sum(value for value in reported if value is not None)
            else:
                observed = _nonnegative_int(row.get("observed_token_total"))
        if observed is not None:
            observed_token_attempts += 1
            observed_token_total += observed
        if measured is not None:
            measured_attempts += 1
            measured_total += measured

        quality_observed = any(
            row.get(key) is not None
            for key in ("verifier_verdict", "downstream_iterate_outcome", "failed", "issue_found")
        )
        if quality_observed:
            quality_attempts += 1
        verdict = row.get("verifier_verdict")
        if isinstance(verdict, str) and verdict:
            verifier_verdicts[verdict] += 1
        retry_count = _nonnegative_int(row.get("retry_count"))
        if retry_count is not None:
            retries_observed += 1
            retries_total += retry_count
        rework_count = _nonnegative_int(row.get("rework_count"))
        if rework_count is not None:
            rework_observed += 1
            rework_total += rework_count
        escaped_defects_count = _nonnegative_int(row.get("escaped_defects_count"))
        if escaped_defects_count is not None:
            escaped_defects_observed += 1
            escaped_defects_total += escaped_defects_count

    return {
        "attempts": len(attempts),
        "ledger_available": True,
        "requested_models": dict(sorted(requested_models.items())),
        "actual_models": dict(sorted(actual_models.items())),
        "requested_efforts": dict(sorted(requested_efforts.items())),
        "actual_efforts": dict(sorted(actual_efforts.items())),
        "measured_token_attempts": measured_attempts,
        "measured_token_total": measured_total if measured_attempts else None,
        "observed_token_attempts": observed_token_attempts,
        "observed_token_total": observed_token_total if observed_token_attempts else None,
        "quality_attempts": quality_attempts,
        "retries_total": retries_total if retries_observed else None,
        "rework_total": rework_total if rework_observed else None,
        "verifier_verdicts": dict(sorted(verifier_verdicts.items())),
        "escaped_defects_total": escaped_defects_total if escaped_defects_observed else None,
    }


def format_routing_evidence(evidence: dict[str, Any]) -> str:
    """Render one compact, evidence-qualified routing line for a retrospective."""
    attempts = _nonnegative_int(evidence.get("attempts")) or 0
    if not attempts:
        return "Routing evidence: no cost-ledger rows matched this run; retain current routing."

    requested_models = evidence.get("requested_models") or {}
    actual_models = evidence.get("actual_models") or {}
    actual_model_count = sum(actual_models.values()) if isinstance(actual_models, dict) else 0
    requested_efforts = evidence.get("requested_efforts") or {}
    actual_efforts = evidence.get("actual_efforts") or {}
    actual_effort_count = sum(actual_efforts.values()) if isinstance(actual_efforts, dict) else 0
    measured_attempts = _nonnegative_int(evidence.get("measured_token_attempts")) or 0
    observed_attempts = _nonnegative_int(evidence.get("observed_token_attempts")) or 0
    quality_attempts = _nonnegative_int(evidence.get("quality_attempts")) or 0
    token_coverage = (
        f"measured tokens {measured_attempts}/{attempts}"
        if measured_attempts else f"measured tokens unreported (0/{attempts})"
    )
    parts = [
        f"Routing evidence: {attempts} dispatches",
        f"requested model {_counted_labels(requested_models)}",
        f"actual model {_counted_labels(actual_models)} ({actual_model_count}/{attempts})",
        f"requested effort {_counted_labels(requested_efforts)}",
        f"actual effort {_counted_labels(actual_efforts)} ({actual_effort_count}/{attempts})",
        token_coverage,
        f"quality outcome {quality_attempts}/{attempts}",
    ]
    total = _nonnegative_int(evidence.get("measured_token_total"))
    if total is not None:
        parts.append(f"{total} measured tokens")
    observed_total = _nonnegative_int(evidence.get("observed_token_total"))
    partial_attempts = max(observed_attempts - measured_attempts, 0)
    if observed_total is not None and partial_attempts > 0:
        parts.append(f"{observed_total} reported-token subtotal across {partial_attempts} partial attempts")
    retries = _nonnegative_int(evidence.get("retries_total"))
    if retries is not None:
        parts.append(f"{retries} retries")
    rework = _nonnegative_int(evidence.get("rework_total"))
    if rework is not None:
        parts.append(f"{rework} rework cycles")
    verdicts = evidence.get("verifier_verdicts") or {}
    if isinstance(verdicts, dict) and verdicts:
        parts.append(f"verifier {_counted_labels(verdicts)}")
    escaped = _nonnegative_int(evidence.get("escaped_defects_total"))
    if escaped is not None:
        parts.append(f"{escaped} escaped defects")
    recommendation = (
        "retain current routing; design a controlled comparison with matched task, agent, and effort cohorts plus measured tokens and quality outcomes"
    )
    return "; ".join(parts) + f". Recommendation: {recommendation}."


def build(
    transcript_jsonl: Path | None,
    state_json: dict[str, Any] | None,
    intent_md: str | None,
    plan_md: str | None,
    run_id: str,
    *,
    prompted_threshold: int = 2,
    transcript_note: str | None = None,
    trace_jsonl: Path | None = None,
    routing_evidence: dict[str, Any] | None = None,
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
    from rally_point.build_loop_id import resolve_run_identity
    identity = resolve_run_identity(state_json)
    # Current mutable files belong only to the matching active execution.
    # A historical row proves identity, not ownership of today's files.
    context_bound = identity.state == "active" and identity.build_run_id == run_id
    intent_md = (intent_md or "") if context_bound else ""
    plan_md = (plan_md or "") if context_bound else ""

    signals = collect_transcript_signals(transcript_jsonl)
    prompts = signals["prompts"]
    clusters = cluster_repeated_prompts(prompts, threshold=prompted_threshold)
    run_records = _runs_for_run_id(state_json, run_id)
    last_run = run_records[-1] if run_records else {}
    judges = list(_iter_judge_decisions(run_records))
    enforce = _enforce_signals(clusters, state_json, run_id)

    # Deterministic transcript signals (free — no LLM). Feed §10, §11, and the
    # app-issue enrichment of §4/§9.
    usage = signals["usage"]
    automation = signals["automation"]
    issue_signals = signals["issues"]
    trace_summary = summarize_tool_trace(trace_jsonl, run_id) if trace_jsonl else {
        "tool_calls": 0, "tool_errors": 0, "error_rate": 0.0,
        "repeated_calls": 0, "provider_429s": 0, "p95_duration_ms": None,
        "tools": {},
    }
    if not usage.get("total_uses") and trace_summary.get("tool_calls"):
        usage = {
            "tools": trace_summary.get("tools") or {},
            "plugins": {},
            "subagents": {},
            "errored": {},
            "total_uses": trace_summary["tool_calls"],
        }
    if ingestion_incomplete(trace_summary):
        issue_signals.append("tool telemetry ingestion is incomplete; zero observed errors cannot establish a clean run")
    if trace_summary.get("tool_errors"):
        issue_signals.append(
            f"tool telemetry recorded {trace_summary['tool_errors']} errors "
            f"across {trace_summary['tool_calls']} calls"
        )
    if trace_summary.get("provider_429s"):
        issue_signals.append(
            f"tool telemetry recorded {trace_summary['provider_429s']} provider rate limits"
        )
    # Automation candidates ARE enforce-candidates — route them into the
    # existing enforce-from-retro → Phase 6 pipeline (kind: automation).
    for c in automation:
        seq = " → ".join(c.get("sequence", []))
        enforce.append(f"Automate recurring ritual (×{c.get('count')}): {seq} — draft a script/hook")
    if trace_summary.get("repeated_calls", 0) >= 3:
        enforce.append(
            f"Reduce repeated identical tool calls (×{trace_summary['repeated_calls']}) "
            "with evidence-aware retry routing"
        )

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

    # 2. key_takeaways — retain the fixed nine-section contract while adding
    # one compact run-scoped routing/calibration signal.
    takeaways: list[str] = []
    m = re.search(r"^## Restated intent.*?\n+([^\n]+)", intent_md, re.M | re.S)
    if m:
        takeaways.append(f"Intent: {m.group(1).strip()}")
    m2 = re.search(r"^# (.+)$", plan_md, re.M)
    if m2:
        takeaways.append(f"Plan headline: {m2.group(1).strip()}")
    if last_run.get("outcome"):
        takeaways.append(f"Run outcome: {last_run['outcome']}")
    if routing_evidence is not None:
        takeaways.append(format_routing_evidence(routing_evidence))
    sections["key_takeaways"] = _format_simple_bullet_section(
        takeaways, "no key takeaways captured"
    )

    # 3. recommendations — every enforce-candidate is also a recommendation.
    recs = list(enforce)
    sections["recommendations"] = _format_simple_bullet_section(
        recs, "no recommendations this run"
    )

    # 4. what_could_be_better — failures/iterate_failures + transcript issue
    #    signals (errored tool calls, tracebacks) + tools that erred.
    bads: list[str] = []
    for run in run_records:
        for f in run.get("failures") or []:
            _append_unique(bads, str(f))
        for f in run.get("iterate_failures") or []:
            _append_unique(bads, f"iterate-failure: {f}")
    for label, n in (usage.get("errored") or {}).items():
        _append_unique(bads, f"`{label}` returned {n} error(s) — friction worth removing")
    for sig in issue_signals:
        _append_unique(bads, f"issue signal: {sig}")
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
    sections["user_prompts_and_repeats"] = _format_user_prompts_section(prompts, clusters, transcript_note)

    # 9. issues_with_causal_tree — judge-flagged issues + transcript signals.
    issues_body = _format_issues_section(state_json, run_id)
    if issue_signals:
        extra = "\n".join(f"- {s}" for s in issue_signals)
        if issues_body.startswith("_No issues"):
            issues_body = "### Issues surfaced (from transcript)\n\n" + extra
        else:
            issues_body += "\n\n### Additional issue signals (from transcript)\n\n" + extra
    sections["issues_with_causal_tree"] = issues_body

    # 10. plugin_tooling_observations — deterministic tool/plugin usage.
    sections["plugin_tooling_observations"] = _format_plugin_tooling_section(usage, transcript_note)

    # 11. automation_candidates — recurring rituals worth scripting.
    sections["automation_candidates"] = _format_automation_candidates_section(automation)

    sections["enforce_candidates"] = enforce
    sections["meta"] = {
        "run_id": run_id,
        "prompt_count": len(prompts),
        "cluster_count": len(clusters),
        "judge_decision_count": len(judges),
        "transcript_present": transcript_jsonl is not None,
        "tool_use_count": usage.get("total_uses", 0),
        "distinct_plugins": len(usage.get("plugins") or {}),
        "automation_candidate_count": len(automation),
        "issue_signal_count": len(issue_signals),
        "transcript_absence_reason": transcript_note,
        "trace_present": bool(trace_jsonl and trace_jsonl.exists()),
        "trace_tool_calls": trace_summary.get("tool_calls", 0),
        "trace_tool_errors": trace_summary.get("tool_errors", 0),
        "trace_repeated_calls": trace_summary.get("repeated_calls", 0),
        "trace_provider_429s": trace_summary.get("provider_429s", 0),
        "trace_p95_duration_ms": trace_summary.get("p95_duration_ms"),
    }
    return sections
