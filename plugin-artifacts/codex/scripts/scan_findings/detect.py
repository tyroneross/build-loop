# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tier-1 deterministic findings detector.

ZERO LLM dependency. Pure regex + JSON parsing over Claude Code transcript
JSONL. The sibling of ``scan_corrections.detect`` (which scans USER turns for
corrections); this module scans the AGENT-authored surface of the session
transcript — host-assistant text blocks AND ``tool_result`` blocks (where a
dispatched sub-agent's condensed return lands) — for clearly-identified
findings/issues that any agent, audit, or critic surfaced.

What counts as a "clearly-identified finding" (high precision over recall):

  1. **Structured findings payload** — a JSON object/array embedded in a block
     that matches the ``review_finding_gate`` shape: ``{"findings": [{...}]}``,
     a single ``{"severity": ...}`` dict, or a top-level list of severity-bearing
     dicts. This is the highest-precision signal an audit agent can emit.

  2. **Prose severity-labeled finding** — a line that opens with an UPPERCASE
     severity label (``CRITICAL|HIGH|MEDIUM|LOW`` + aliases, normalized through
     ``review_finding_gate.SEVERITY_MAP``) or an explicit ``severity: <level>``
     field, followed by a separator and a concrete clause (>= ~17 chars, not a
     question).

Routing (MECE — exactly one bucket per candidate):

  * a RECOGNIZED severity was extracted          -> route="backlog"
  * a finding SIGNAL but NO recognized severity   -> route="review"
        (structured finding with absent/unknown severity, OR a prose line
         carrying a finding keyword but no severity label)
  * neither                                       -> not emitted (ignored)

A false backlog item is worse than a missed one, so anything short of a
recognized severity is routed to the review queue for human confirmation, never
straight to the backlog.

Dedup: every candidate carries a stable ``finding_hash`` = sha1 of the
severity-stripped normalized clause, so the same issue re-stated (or re-swept on
a later Stop) produces the same hash regardless of which severity it was tagged
with or which agent surfaced it.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

# scripts/ on path so the sibling-module imports below resolve whether this is
# run as `python -m scan_findings` or imported directly by a test.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# DRY: reuse the canonical severity taxonomy (critical/high/medium/low + the
# blocker/major/minor/info aliases) from the existing review gate. A local
# fallback keeps the hook fail-open if the import ever breaks — the sweep must
# never crash a session on an import error.
try:  # pragma: no cover - exercised indirectly; fallback path is defensive
    from review_finding_gate import SEVERITY_MAP as _SEVERITY_MAP  # type: ignore
except Exception:  # noqa: BLE001
    _SEVERITY_MAP = {
        "critical": "critical", "crit": "critical",
        "blocker": "high", "high": "high", "major": "high",
        "medium": "medium", "med": "medium", "minor": "medium",
        "low": "low", "info": "low", "informational": "low",
    }

# DRY: reuse the transcript record-shape primitives from scan_corrections — the
# tolerant role extractor handles both the nested `message.role` and the flat
# `type: user|assistant` Claude Code shapes. We add a findings-specific block
# walker on top (assistant text + tool_result content), rather than copy the
# JSONL parsing a third time.
try:  # pragma: no cover - fallback is defensive
    from scan_corrections.detect import _extract_role as _cc_extract_role  # type: ignore
except Exception:  # noqa: BLE001
    def _cc_extract_role(obj: dict) -> str | None:  # type: ignore
        if isinstance(obj.get("role"), str):
            return obj["role"]
        msg = obj.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("role"), str):
            return msg["role"]
        t = obj.get("type")
        return t if t in ("user", "assistant") else None


# Severity -> backlog priority. critical is the most urgent (P0).
SEVERITY_PRIORITY = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}

# Recognized severity tokens, longest-first so "crit" never shadows "critical".
_SEV_TOKENS = sorted(_SEVERITY_MAP.keys(), key=len, reverse=True)
_SEV_ALT = "|".join(re.escape(t) for t in _SEV_TOKENS)

# Prose finding line: an opening severity label after optional bullet/number/
# bracket/bold markers, then a separator, then a concrete clause. The matched
# severity token must be UPPERCASE (the label convention) — checked in code — so
# ordinary prose like "low latency on the homepage" never fires.
_PROSE_LABEL_RE = re.compile(
    r"^[\s>*_\-\d.)\[(]*"          # leading bullet / number / bracket markers
    r"(?:\*\*|__)?"                 # optional bold open
    r"(?P<sev>" + _SEV_ALT + r")"   # severity token
    r"(?:\*\*|__)?"                 # optional bold close
    r"\s*[\]\):\.\-–—]\s+"  # separator: ] ) : . - – —
    r"(?P<clause>\S.{15,}?)\s*$",    # concrete clause (>= ~17 chars)
    re.IGNORECASE,
)

# Explicit "severity: high — <clause>" / "severity = high" field, any case.
_SEV_FIELD_RE = re.compile(
    r"\bseverity\s*[:=]\s*(?P<sev>" + _SEV_ALT + r")\b"
    r"[\s\-–—:|,]*(?P<clause>\S.{8,}?)?\s*$",
    re.IGNORECASE,
)

# Finding KEYWORDS — when a line carries one of these but NO recognized severity,
# it is a finding signal of unknown severity -> review queue (never backlog).
# Intentionally narrow: concrete defect/security nouns, not vague hedges.
_FINDING_KEYWORDS = re.compile(
    r"\b("
    r"vulnerabilit(?:y|ies)|injection|command\s+injection|sql\s+injection|"
    r"xss|csrf|ssrf|rce|remote\s+code\s+execution|race\s+condition|deadlock|"
    r"memory\s+leak|data\s+loss|security\s+(?:issue|hole|flaw|bug)|exploit|"
    r"cve-\d|auth(?:entication|orization)?\s+bypass|privilege\s+escalation|"
    r"path\s+traversal|hardcoded\s+(?:secret|credential|password|key)|"
    r"unsanitized|unvalidated\s+input|use-after-free|off-by-one|"
    r"null\s+(?:pointer|deref)|integer\s+overflow|broken\s+access\s+control"
    r")\b",
    re.IGNORECASE,
)

# A defect/technical signal in a clause. The bare UPPERCASE-severity-label route
# requires one of these to keep its severity (-> backlog); a severity label with
# NO defect signal (e.g. "CRITICAL: ship by Friday") is a planning line, not a
# finding, so it is downgraded to an unscored review candidate. High precision:
# a false P0 backlog item is worse than a human-triaged review proposal.
_DEFECT_SIGNAL = re.compile(
    r"("
    r"`[^`]+`"                                          # inline code
    r"|\b[\w./-]+\.(?:py|js|ts|tsx|jsx|mjs|cjs|yml|yaml|json|sh|bash|go|rs|java|rb|"
    r"php|c|cpp|h|swift|kt|sql|toml|ini|cfg|conf|env|lock|md|xml|html|css)\b"  # file path
    r"|\b\w+\([^)]*\)"                                   # function call foo(...)
    r"|\b(?:error|errors|fail(?:s|ed|ure|ing)?|crash(?:es|ed|ing)?|break(?:s|ing)?|broke|broken|"
    r"leak(?:s|ed|ing)?|null|undefined|nan|exception|traceback|regress(?:ion|es|ed)?|"
    r"missing|incorrect|wrong|invalid|unsafe|insecure|unsanitized|unvalidated|expos(?:e|ed|es|ure|ing)?|"
    r"bypass|overflow|underflow|deadlock|hang(?:s|ing)?|timeout|race|injection|vulnerab\w*|exploit|"
    r"denial|collid(?:e|es|ing|ed)|corrupt\w*|mismatch|inconsistent|stale|drift|drop(?:s|ped|ping)?|"
    r"loss|lost|duplicat\w*|defect|bug|flaw|fault|deprecat\w*|panic|segfault|"
    r"unauthor\w*|hardcoded|secret|credential|token|password)\b"
    r")",
    re.IGNORECASE,
)

# Explicit "Bug:/Issue:/Finding:/Defect:" prose prefixes (no severity) — a clear
# finding signal that still routes to review (no severity asserted).
_FINDING_PREFIX_RE = re.compile(
    r"^[\s>*_\-\d.)\[(]*(?:\*\*|__)?"
    r"(?:bug|issue|finding|defect|problem|flaw)"
    r"(?:\*\*|__)?\s*[:\-–—]\s+(?P<clause>\S.{12,}?)\s*$",
    re.IGNORECASE,
)

# A clause ending in a question mark is a question, not a finding.
_TRAILING_Q = re.compile(r"\?\s*$")

# Cap per-block scan size to keep the hook cheap on a giant pasted log.
_MAX_BLOCK_CHARS = 200_000


def normalize_severity(value: Any) -> str | None:
    """Map a raw severity token to critical|high|medium|low, or None if it is
    not a recognized token.

    Unlike ``review_finding_gate.normalize_severity`` (which DEFAULTS unknown
    values to "high"), this returns None for unrecognized input so the router
    can distinguish "recognized severity -> backlog" from "no/unknown severity
    -> review queue".
    """
    if not isinstance(value, str):
        return None
    return _SEVERITY_MAP.get(value.strip().lower())


def _normalize_core(text: str) -> str:
    """Severity-stripped, lowercased, whitespace-collapsed clause used for the
    dedup hash. Removes leading bullet/markup and a leading severity label so the
    same finding tagged HIGH or CRITICAL (or re-stated) hashes identically."""
    s = (text or "").strip().lower()
    # Strip a leading severity label + separator (e.g. "high: ", "[critical] ").
    s = re.sub(
        r"^[\s>*_\-\d.)\[(]*(?:\*\*|__)?(?:" + _SEV_ALT + r")(?:\*\*|__)?"
        r"\s*[\]\):\.\-–—]\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"[*_`>#]+", " ", s)        # drop markdown emphasis chars
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" .;,:-–—")
    return s


def _finding_hash(core: str) -> str:
    return hashlib.sha1(core.encode("utf-8")).hexdigest()[:16]


def _clean_title(clause: str) -> str:
    """A backlog-ready one-line title from a finding clause."""
    t = re.sub(r"[*_`]+", "", clause or "").strip()
    t = re.sub(r"\s+", " ", t)
    t = t.strip(" .;,:-–—")
    return t[:160]


@dataclass
class FindingCandidate:
    """One detected finding.

    ``finding_hash`` is a stable SHA1[:16] of the severity-stripped normalized
    clause, so re-running the sweep on the same transcript (or a later session
    re-stating the same issue) yields the same hash — the dedup key.
    """

    severity: str | None        # normalized critical|high|medium|low, or None
    title: str                  # one-line backlog title
    evidence: str               # the verbatim clause / evidence span
    route: str                  # "backlog" (severity known) | "review" (no severity)
    source_kind: str            # "assistant_text" | "tool_result" | "structured_json"
    agent: str                  # originating agent (best-effort) | "session"
    finding_hash: str = ""
    extras: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.finding_hash:
            self.finding_hash = _finding_hash(_normalize_core(self.evidence or self.title))

    @property
    def priority(self) -> str:
        """Backlog priority from severity (P2 fallback for the review route)."""
        return SEVERITY_PRIORITY.get(self.severity or "", "P2")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["priority"] = self.priority
        return d


# ---------------------------------------------------------------------------
# JSON-findings extraction
# ---------------------------------------------------------------------------

_TITLE_FIELDS = ("title", "message", "issue", "description", "name", "finding", "summary", "claim_text")
_EVIDENCE_FIELDS = ("evidence", "snippet", "observed", "detail", "details", "proof")


def _iter_json_values(text: str) -> Iterable[Any]:
    """Yield every top-level JSON object/array embedded in ``text``.

    Uses ``json.JSONDecoder().raw_decode`` from each ``{``/``[`` position, so a
    fenced ```json block, an inline payload, or prose-wrapped JSON all parse.
    Best-effort: positions that don't start a valid JSON value are skipped.
    """
    if not text:
        return
    decoder = json.JSONDecoder()
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{" or ch == "[":
            try:
                value, end = decoder.raw_decode(text, i)
            except ValueError:
                i += 1
                continue
            yield value
            i = max(end, i + 1)
            continue
        i += 1


def _findings_from_json_value(value: Any) -> list[tuple[str | None, str, str]]:
    """Return (severity|None, title, evidence) tuples from a parsed JSON value
    that looks like a findings payload. Empty list if it isn't one."""
    items: list[dict] = []
    if isinstance(value, dict):
        f = value.get("findings")
        if isinstance(f, list):
            items = [x for x in f if isinstance(x, dict)]
        elif "severity" in value:
            items = [value]
    elif isinstance(value, list):
        items = [x for x in value if isinstance(x, dict) and "severity" in x]

    out: list[tuple[str | None, str, str]] = []
    for it in items:
        sev = normalize_severity(it.get("severity"))
        title = ""
        for k in _TITLE_FIELDS:
            v = it.get(k)
            if isinstance(v, str) and v.strip():
                title = v.strip()
                break
        evidence = ""
        for k in _EVIDENCE_FIELDS:
            v = it.get(k)
            if isinstance(v, str) and v.strip():
                evidence = v.strip()
                break
            if isinstance(v, dict):
                # review_finding_gate evidence sometimes nests {file,line,snippet}.
                snip = v.get("snippet") or v.get("observed") or v.get("text")
                if isinstance(snip, str) and snip.strip():
                    evidence = snip.strip()
                    break
        if not title and not evidence:
            continue
        out.append((sev, title or evidence, evidence or title))
    return out


# ---------------------------------------------------------------------------
# Prose extraction
# ---------------------------------------------------------------------------

def _prose_findings_from_text(text: str) -> list[tuple[str | None, str, str]]:
    """Scan ``text`` line-by-line for prose findings.

    Returns (severity|None, title, evidence) tuples. A recognized UPPERCASE
    severity label (or an explicit ``severity:`` field) yields severity != None;
    a finding-keyword / Bug:/Issue: prefix without a severity yields None.
    """
    out: list[tuple[str | None, str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        # 1. Opening severity label (must be UPPERCASE to count as a label).
        m = _PROSE_LABEL_RE.match(line)
        if m and m.group("sev").isupper():
            clause = m.group("clause").strip()
            if not _TRAILING_Q.search(clause):
                sev = normalize_severity(m.group("sev"))
                # A severity label alone is not a finding. Keep the severity (->
                # backlog) only when the clause carries a defect/technical signal;
                # otherwise drop the severity so it routes to review for human
                # triage rather than auto-landing as a false P0/P1 backlog item.
                if not (_DEFECT_SIGNAL.search(clause) or _FINDING_KEYWORDS.search(clause)):
                    sev = None
                out.append((sev, _clean_title(clause), clause))
                continue

        # 2. Explicit "severity: <level>" field (any case).
        mf = _SEV_FIELD_RE.search(line)
        if mf:
            clause = (mf.group("clause") or "").strip()
            if clause and not _TRAILING_Q.search(clause):
                sev = normalize_severity(mf.group("sev"))
                out.append((sev, _clean_title(clause), clause))
                continue

        # 3. Bug:/Issue:/Finding: prefix without a severity -> review route.
        mp = _FINDING_PREFIX_RE.match(line)
        if mp:
            clause = mp.group("clause").strip()
            if not _TRAILING_Q.search(clause):
                out.append((None, _clean_title(clause), clause))
                continue

        # 4. A finding keyword on a concrete (non-question) line -> review route.
        if _FINDING_KEYWORDS.search(line) and not _TRAILING_Q.search(line.strip()):
            clause = line.strip(" \t>-*_").strip()
            if len(clause) >= 20:
                out.append((None, _clean_title(clause), clause))
    return out


# ---------------------------------------------------------------------------
# Transcript walking — assistant text + tool_result blocks
# ---------------------------------------------------------------------------

def _content_of(obj: dict) -> Any:
    msg = obj.get("message", obj)
    return msg.get("content") if isinstance(msg, dict) else obj.get("content")


def _agent_from_tool_use(block: dict) -> str | None:
    """Best-effort originating-agent name from a Task/Agent tool_use block."""
    if not isinstance(block, dict):
        return None
    if block.get("type") not in ("tool_use", "tool_call"):
        return None
    name = (block.get("name") or "").lower()
    if name not in ("task", "agent"):
        return None
    inp = block.get("input") or {}
    if isinstance(inp, dict):
        for k in ("subagent_type", "subagentType", "agent", "description"):
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return name or None


def iter_finding_blocks_from_jsonl(transcript_path: Path) -> Iterable[tuple[str, str, str]]:
    """Yield (block_text, source_kind, agent) for every agent-authored block.

    source_kind: "assistant_text" (host assistant prose) or "tool_result"
    (a tool/sub-agent return re-injected into the transcript). The most recent
    Task/Agent tool_use's ``subagent_type`` is carried forward and attributed to
    the next tool_result, so a dispatched audit agent's findings keep their
    provenance. ``isMeta`` records (hook injections, skill loads) are skipped.
    """
    if not transcript_path or not transcript_path.exists():
        return
    try:
        text = transcript_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    pending_agent: str | None = None
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or not raw.startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if obj.get("isMeta"):
            continue

        content = _content_of(obj)
        role = _cc_extract_role(obj)

        if isinstance(content, str):
            # Flat string content only carries real prose on assistant turns.
            if role == "assistant" and content.strip():
                yield content[:_MAX_BLOCK_CHARS], "assistant_text", "session"
            continue
        if not isinstance(content, list):
            continue

        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and role == "assistant":
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    text_parts.append(t)
            elif btype in ("tool_use", "tool_call"):
                agent = _agent_from_tool_use(block)
                if agent:
                    pending_agent = agent
            elif btype == "tool_result":
                tr = _tool_result_text(block.get("content"))
                if tr.strip():
                    yield tr[:_MAX_BLOCK_CHARS], "tool_result", (pending_agent or "session")
                # A result consumes the pending attribution.
                pending_agent = None
        if text_parts:
            yield ("\n".join(text_parts))[:_MAX_BLOCK_CHARS], "assistant_text", "session"


def _tool_result_text(content: Any) -> str:
    """Flatten a tool_result's content (string or list of text/blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------

def _candidates_from_block(text: str, source_kind: str, agent: str) -> list[FindingCandidate]:
    """All findings in one block: structured JSON first, then prose."""
    if not text or len(text) > _MAX_BLOCK_CHARS:
        text = (text or "")[:_MAX_BLOCK_CHARS]
    out: list[FindingCandidate] = []

    # Structured JSON payloads (highest precision).
    for value in _iter_json_values(text):
        for sev, title, evidence in _findings_from_json_value(value):
            out.append(FindingCandidate(
                severity=sev,
                title=_clean_title(title),
                evidence=evidence,
                route="backlog" if sev else "review",
                source_kind="structured_json",
                agent=agent,
            ))

    # Prose findings.
    for sev, title, evidence in _prose_findings_from_text(text):
        out.append(FindingCandidate(
            severity=sev,
            title=title,
            evidence=evidence,
            route="backlog" if sev else "review",
            source_kind=source_kind,
            agent=agent,
        ))
    return out


def detect_findings(
    transcript_path: Path | str | None = None,
    *,
    text_blocks: list[str] | list[tuple[str, str, str]] | None = None,
) -> list[FindingCandidate]:
    """High-level entry point.

    Either ``transcript_path`` (JSONL on disk) or ``text_blocks`` (testing
    surface: a list of plain strings, or (text, source_kind, agent) tuples).
    Returns findings deduped by ``finding_hash`` (earliest kept).
    """
    results: list[FindingCandidate] = []

    if text_blocks is not None:
        for blk in text_blocks:
            if isinstance(blk, tuple):
                text, source_kind, agent = (list(blk) + ["assistant_text", "session"])[:3]
            else:
                text, source_kind, agent = blk, "assistant_text", "session"
            results.extend(_candidates_from_block(text, source_kind, agent))
        return _dedup(results)

    if transcript_path is None:
        return []
    p = Path(transcript_path)
    for text, source_kind, agent in iter_finding_blocks_from_jsonl(p):
        results.extend(_candidates_from_block(text, source_kind, agent))
    return _dedup(results)


def _dedup(candidates: list[FindingCandidate]) -> list[FindingCandidate]:
    """Drop duplicate candidates by finding_hash, keeping the earliest. When the
    same hash appears at multiple severities, keep the most urgent (lowest
    priority rank) so a HIGH+MEDIUM restatement lands the HIGH."""
    best: dict[str, FindingCandidate] = {}
    order: list[str] = []
    rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    for c in candidates:
        h = c.finding_hash
        if h not in best:
            best[h] = c
            order.append(h)
            continue
        # Prefer a backlog route over review, then the more urgent priority.
        cur = best[h]
        if cur.route != "backlog" and c.route == "backlog":
            best[h] = c
        elif cur.route == c.route and rank.get(c.priority, 9) < rank.get(cur.priority, 9):
            best[h] = c
    return [best[h] for h in order]
