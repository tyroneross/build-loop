#!/usr/bin/env python3
"""End-of-session sweep for tier-3 (inferred / assumed) decisions.

Runs from a Claude Code Stop hook. Reads the session transcript JSONL,
asks a local Ollama model (`qwen3:8b-q4_K_M` by default) to extract
implicit decisions using design-ref §12 Prompt C (batch consolidation),
and writes results into `.episodic/decisions/`:

  - `confidence: explicit`  → trusted (`.episodic/decisions/`) via write_decision.py
  - `confidence: confirmed` → trusted via write_decision.py
  - `confidence: inferred`  → quarantine (`.episodic/decisions/_review/`) for user promotion
  - `confidence: assumed`   → quarantine (`.episodic/decisions/_review/`)

Dedup against existing `semantic_facts` is best-effort and uses the same
embedding pipeline as write_decision.py. Threshold ≥0.85 → IGNORE; <0.85 → INSERT.

Hook contract: never fail the session. Any error logs and exits 0.

Usage:
  scan_transcript_for_decisions.py --transcript <path>
  scan_transcript_for_decisions.py --transcript <path> --mock-llm-output <json-file>
  scan_transcript_for_decisions.py --transcript $CLAUDE_TRANSCRIPT_PATH

Exit codes: always 0 unless --strict is passed (CI testing).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Lazy imports from write_decision so test fixtures share the same primitives.
from write_decision import (  # type: ignore  # noqa: E402
    LockedFile,
    atomic_write_bytes,
    emit_frontmatter,
    log,
    next_id,
    ollama_embed,
    parse_frontmatter,
    render_madr,
    slugify,
)

DEFAULT_LLM_MODEL = "qwen3:8b-q4_K_M"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_LLM_TIMEOUT_S = 120
DEDUP_THRESHOLD = 0.85
WRITE_DECISION_SCRIPT = HERE / "write_decision.py"


# ---------- transcript reading ----------


def read_transcript(path: Path, max_chars: int = 60_000) -> str:
    """Read JSONL transcript and produce a compact text rendering for the LLM.

    Robust to malformed lines (skips them). Truncates from the head if
    the rendered text exceeds `max_chars` so we always send recent turns.
    """
    if not path.exists():
        log(f"scan: transcript not found at {path}; skipping")
        return ""
    parts: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rendered = _render_turn(obj)
        if rendered:
            parts.append(rendered)
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[-max_chars:]
        # Snap to the next newline so we don't start mid-line.
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1 :]
    return text


def _render_turn(obj: dict) -> str:
    """Render one transcript line as `ROLE: content`. Tolerant of shape variants.

    Handles both shapes seen in Claude Code transcripts:
      {"type": "user", "message": {"role": "user", "content": "..."}}
      {"type": "assistant", "message": {"role": "assistant", "content": [{"type":"text", "text":"..."}, ...]}}
    """
    msg = obj.get("message") or {}
    role = (msg.get("role") or obj.get("type") or "").lower()
    content = msg.get("content")
    if content is None:
        content = obj.get("content", "")
    if isinstance(content, list):
        text_parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    text_parts.append(str(c.get("text", "")))
                elif "text" in c:
                    text_parts.append(str(c["text"]))
            elif isinstance(c, str):
                text_parts.append(c)
        content_text = "\n".join(text_parts).strip()
    elif isinstance(content, str):
        content_text = content.strip()
    else:
        content_text = ""
    if not content_text or not role:
        return ""
    return f"{role.upper()}: {content_text}"


# ---------- existing-decision context loader ----------


def load_prior_decisions_summary(workdir: Path, limit: int = 20) -> str:
    decisions_dir = workdir / ".episodic" / "decisions"
    if not decisions_dir.exists():
        return ""
    files = sorted(decisions_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))[-limit:]
    rows: list[str] = []
    for f in files:
        try:
            fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        rows.append(
            f"- {fm.get('id','?')} ({fm.get('confidence','?')}) "
            f"[{fm.get('primary_tag','?')}/{fm.get('entity','?')}] {fm.get('title','')}"
        )
    return "\n".join(rows)


# ---------- prompt C builder ----------


def _load_allowed_tags(workdir: Path) -> list[str]:
    """Read TAXONOMY.md §1 to surface the allowed primary_tag set to the LLM."""
    tax = workdir / ".semantic" / "TAXONOMY.md"
    if not tax.exists():
        return []
    text = tax.read_text(encoding="utf-8")
    tags: list[str] = []
    in_tags = False
    for line in text.splitlines():
        if line.startswith("## 1.") or "Decision tags" in line and line.startswith("## "):
            in_tags = True
            continue
        if in_tags and line.startswith("## "):
            break
        if in_tags:
            m = re.match(r"^- `([a-z][a-z0-9-]*)`", line)
            if m:
                tags.append(m.group(1))
    return tags


def build_prompt_c(transcript_text: str, prior_decisions: str, allowed_tags: list[str] | None = None) -> str:
    """Design-ref §12 Prompt C (batch consolidation).

    `allowed_tags`, when provided, is interpolated into the prompt so the
    LLM picks from the project's taxonomy instead of inventing
    primary_tags like `framework` or `test_framework` that the writer
    will reject.
    """
    tags_clause = ""
    if allowed_tags:
        tags_clause = (
            "- primary_tag MUST be exactly one of: "
            + ", ".join(f"`{t}`" for t in allowed_tags)
            + ". If none fit, choose the closest match.\n"
        )
    return (
        "Scan the conversation transcript. Identify decisions the user made "
        "or strongly implied. Output ONLY a JSON array (no prose, no code "
        "fences, no commentary).\n\n"
        "Each item shape:\n"
        "  {\n"
        '    "decision": "<one-sentence decision>",\n'
        '    "evidence": "<exact quote OR turn description>",\n'
        '    "confidence": "explicit | confirmed | inferred | assumed",\n'
        '    "primary_tag": "<single primary tag>",\n'
        '    "entity": "<scope, e.g. project name or module>",\n'
        '    "tags": ["<tag1>", "<tag2>"],\n'
        '    "context": "<1-3 sentences>",\n'
        '    "alternatives": "<alternatives considered, if any>",\n'
        '    "rationale": "<why; must have textual evidence for explicit/confirmed>"\n'
        "  }\n\n"
        "Rules:\n"
        "- Only capture if there is textual signal. No speculation without evidence.\n"
        "- explicit = direct verbal marker (\"let's go with X\", \"use Y\")\n"
        "- confirmed = action accepted or topic moved past proposal without objection\n"
        "- inferred = topic-coherent inference; user did not object but did not endorse\n"
        "- assumed = pure pattern-match from prior conversation, weak evidence\n"
        + tags_clause +
        "- Do NOT output anything outside the JSON array.\n"
        "- Empty array is acceptable when the transcript has no decisions.\n\n"
        f"Existing decisions (do not duplicate):\n{prior_decisions or '(none)'}\n\n"
        "Transcript:\n"
        f"{transcript_text}\n"
    )


# ---------- ollama call ----------


def call_ollama(prompt: str, model: str, timeout_s: int = DEFAULT_LLM_TIMEOUT_S) -> str | None:
    """Call ollama via the local HTTP API. Returns the model's response
    text (with the `thinking` field stripped server-side) or None on
    failure. We prefer HTTP over `ollama run <model>` because the CLI
    emits TTY-aware streaming output (cursor-back / erase-line escape
    codes) even when stdout is piped — those sequences corrupt JSON
    spans inside the response.

    Returns None when:
      - ollama daemon is unreachable (e.g. PATH lookup of `ollama` fails
        or the local API refuses connections)
      - the request errors out
      - the timeout fires
    """
    # Cheap probe: if the CLI isn't installed AND PATH is constrained
    # (e.g. the test_ollama_unreachable_no_op fixture sets PATH=/nonexistent-bin),
    # we want to bail fast without hitting the network. Use shutil.which
    # as a proxy for "ollama is plausibly installed on this machine".
    if not shutil.which("ollama"):
        log("scan: ollama CLI not on PATH; treating as no-op")
        return None
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            # Reasoning models can chew CPU on `<think>` for unrelated work.
            # The HTTP API respects `think: false` for qwen-style models.
            "think": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        log(f"scan: ollama HTTP unreachable: {e}")
        return None
    except (TimeoutError, OSError) as e:
        log(f"scan: ollama HTTP timed out / network error: {e}")
        return None
    except Exception as e:  # noqa: BLE001
        log(f"scan: ollama HTTP error: {e}")
        return None
    return payload.get("response") or ""


# ---------- result parsing ----------


_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
# ollama's CLI emits ANSI cursor-back / erase-line sequences even when
# stdout is a pipe (the TTY heuristic is loose). Strip them before parse.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# qwen3 (and other reasoning models) emit "Thinking...\n...\n...done thinking.\n"
# blocks before the final answer. Strip the entire block.
_THINK_BLOCK_RE = re.compile(r"^Thinking\.\.\..*?\.\.\.done thinking\.\s*", re.DOTALL)


def _strip_llm_noise(raw: str) -> str:
    """Remove ANSI escape codes and reasoning-trace blocks from LLM output."""
    out = _ANSI_RE.sub("", raw)
    out = _THINK_BLOCK_RE.sub("", out)
    return out.strip()


def parse_llm_output(raw: str) -> list[dict]:
    """Extract JSON array from possibly-noisy LLM output. Best-effort."""
    if not raw or not raw.strip():
        return []
    raw = _strip_llm_noise(raw)
    # Strip markdown code fences if present.
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n", "", raw)
        raw = re.sub(r"\n```\s*$", "", raw)
    # Try direct parse first.
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except json.JSONDecodeError:
        pass
    m = _JSON_ARRAY_RE.search(raw)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


# ---------- dedup against semantic_facts (best-effort) ----------


def is_duplicate(text: str, embed_model: str, schema: str = "build_loop_memory") -> bool:
    """Return True if `text` cosine-similar to any existing semantic_facts row at ≥ DEDUP_THRESHOLD.

    Best-effort. Returns False on any error so we err toward writing.
    Uses the persistent psycopg connection from `db.py`.
    """
    try:
        embedding = ollama_embed(text, embed_model)
        if embedding is None:
            return False
        if not re.match(r"^[a-z][a-z0-9_]*$", schema):
            return False
        from db import query_one, vector_literal  # type: ignore  # noqa: PLC0415

        emb = vector_literal(embedding)
        sql = (
            "SELECT 1 - (embedding <=> %s::vector) AS sim "
            f"FROM {schema}.semantic_facts "
            "WHERE status = 'active' "
            "ORDER BY embedding <=> %s::vector "
            "LIMIT 1"
        )
        row = query_one(sql, (emb, emb))
        if row is None or row.get("sim") is None:
            return False
        sim = float(row["sim"])
        return sim >= DEDUP_THRESHOLD
    except Exception as e:  # noqa: BLE001
        log(f"scan: dedup check failed (continuing as new): {e}")
        return False


# ---------- write paths ----------


def write_trusted(workdir: Path, item: dict, db: bool) -> tuple[bool, str]:
    """Shell out to write_decision.py for explicit/confirmed captures.

    Returns (success, decision_id_or_error).
    """
    title = (item.get("decision") or item.get("decision_title") or "(untitled)").strip()
    decision_text = (item.get("decision") or "").strip()
    primary_tag = (item.get("primary_tag") or "process").strip()
    entity = (item.get("entity") or "build-loop").strip()
    tags = item.get("tags") or [primary_tag]
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    confidence = (item.get("confidence") or "inferred").strip()
    source = "auto-confirmed" if confidence == "confirmed" else "auto-explicit"
    args = [
        sys.executable, str(WRITE_DECISION_SCRIPT),
        "--workdir", str(workdir),
        "--title", title[:200],
        "--decision", decision_text[:1000],
        "--context", (item.get("context") or "")[:1000],
        "--alternatives", (item.get("alternatives") or "")[:1000],
        "--consequences", (item.get("rationale") or "")[:1000],
        "--tags", ",".join(tags),
        "--primary-tag", primary_tag,
        "--entity", entity,
        "--confidence", confidence,
        "--source", source,
        "--captured-turn-excerpt", (item.get("evidence") or "")[:200],
    ]
    if not db:
        args.append("--no-db")
    cp = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if cp.returncode != 0:
        log(f"scan: write_decision.py rejected ({title!r}): {cp.stderr.strip()[:300]}")
        return False, cp.stderr.strip()
    return True, cp.stdout.strip()


def write_review(workdir: Path, item: dict) -> tuple[bool, str]:
    """Write a tier-3 (inferred / assumed) capture into _review/ for user promotion.

    File-only — no event emitted, no DB write, no INDEX entry. The user
    promotes by `mv` out of `_review/` (or runs a future /knowledge:review).
    """
    review_dir = workdir / ".episodic" / "decisions" / "_review"
    review_dir.mkdir(parents=True, exist_ok=True)

    # Allocate ID space distinct from trusted decisions to avoid clashes if user moves files.
    decisions_dir = workdir / ".episodic" / "decisions"
    history_dir = decisions_dir / "_history"
    used: set[int] = set()
    for d in (decisions_dir, history_dir, review_dir):
        if d.exists():
            for f in d.iterdir():
                m = re.match(r"^(\d{4})-", f.name)
                if m:
                    used.add(int(m.group(1)))
    next_n = (max(used) + 1) if used else 1
    new_id = f"{next_n:04d}"

    title = (item.get("decision") or "(untitled)").strip()
    slug = slugify(title)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"{new_id}-{date}-{slug}.md"

    primary_tag = (item.get("primary_tag") or "process").strip()
    entity = (item.get("entity") or "build-loop").strip()
    tags = item.get("tags") or [primary_tag]
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if primary_tag not in tags:
        tags = [primary_tag] + list(tags)

    confidence = (item.get("confidence") or "inferred").strip()
    if confidence not in {"inferred", "assumed"}:
        # Anything coming through write_review is by definition tier-3; clamp.
        confidence = "inferred"

    fm: dict[str, Any] = {
        "id": new_id,
        "slug": slug,
        "title": title,
        "type": "decision",
        "status": "proposed",
        "confidence": confidence,
        "date": date,
        "tags": tags,
        "primary_tag": primary_tag,
        "entity": entity,
        "source": "auto-inferred" if confidence == "inferred" else "auto-assumed",
        "review_origin": "stop-hook-batch",
        "captured_turn_excerpt": (item.get("evidence") or "")[:200],
    }
    body = {
        "context": item.get("context") or "",
        "decision": item.get("decision") or "",
        "alternatives": item.get("alternatives") or "",
        "consequences": item.get("rationale") or "",
        "notes": "Auto-captured tier-3 entry. Promote by moving out of `_review/`, or revoke with `revoke_decision.py --id <id>`.",
    }
    text = render_madr(fm, body)
    atomic_write_bytes(review_dir / filename, text.encode("utf-8"))
    log(f"scan: queued review entry {new_id} → {review_dir / filename}")
    return True, new_id


# ---------- main ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stop-hook end-of-session decision sweep.")
    p.add_argument("--workdir", default=".", help="Project root containing .episodic/")
    p.add_argument(
        "--transcript",
        default=os.environ.get("CLAUDE_TRANSCRIPT_PATH", ""),
        help="Path to Claude Code session transcript (.jsonl). Defaults to $CLAUDE_TRANSCRIPT_PATH.",
    )
    p.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    p.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    p.add_argument("--llm-timeout-s", type=int, default=DEFAULT_LLM_TIMEOUT_S)
    p.add_argument(
        "--mock-llm-output",
        default=None,
        help="Path to a file containing canned LLM JSON output (testing only).",
    )
    p.add_argument(
        "--db",
        dest="db",
        action="store_true",
        default=True,
        help="Allow write_decision.py to dual-write to Postgres (default).",
    )
    p.add_argument("--no-db", dest="db", action="store_false")
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on any error. Default is to swallow errors (hook-friendly).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        # Hooks must not fail the session.
        return 1 if e.code else 0

    workdir = Path(args.workdir).resolve()
    episodic = workdir / ".episodic"
    if not episodic.exists():
        log(f"scan: no .episodic/ at {episodic}; skipping (project does not opt in)")
        return 0

    transcript_path = Path(args.transcript) if args.transcript else None
    transcript_text = read_transcript(transcript_path) if transcript_path else ""
    if not transcript_text and not args.mock_llm_output:
        log("scan: empty/missing transcript and no --mock-llm-output; nothing to do")
        return 0

    # Get LLM output: mock file or live ollama
    raw: str | None
    if args.mock_llm_output:
        try:
            raw = Path(args.mock_llm_output).read_text(encoding="utf-8")
        except FileNotFoundError as e:
            log(f"scan: mock LLM output file not found: {e}")
            return 1 if args.strict else 0
    else:
        prior = load_prior_decisions_summary(workdir)
        allowed_tags = _load_allowed_tags(workdir)
        prompt = build_prompt_c(transcript_text, prior, allowed_tags=allowed_tags)
        raw = call_ollama(prompt, args.llm_model, args.llm_timeout_s)
        if raw is None:
            log("scan: no LLM output available; exiting cleanly (no-op)")
            return 0

    items = parse_llm_output(raw)
    if not items:
        log("scan: LLM returned no items")
        return 0

    log(f"scan: LLM returned {len(items)} candidate(s)")

    trusted_count = 0
    review_count = 0
    skipped_dup = 0
    for item in items:
        confidence = (item.get("confidence") or "").strip()

        # Dedup check on the decision text — only when we have DB access.
        text_for_dedup = (item.get("decision") or "") + " " + (item.get("rationale") or "")
        if args.db and is_duplicate(text_for_dedup, args.embed_model):
            log(f"scan: SKIP duplicate ({item.get('decision', '')[:60]!r})")
            skipped_dup += 1
            continue

        if confidence in ("explicit", "confirmed"):
            ok, _ = write_trusted(workdir, item, db=args.db)
            if ok:
                trusted_count += 1
            else:
                # write_decision.py rejected — typically a taxonomy
                # mismatch. Fall through to the review-queue tier rather
                # than dropping the captured signal entirely. Stamp the
                # confidence down to `inferred` since the item failed
                # vocab validation at the trusted tier.
                fallback = dict(item)
                fallback["confidence"] = "inferred"
                ok2, _ = write_review(workdir, fallback)
                if ok2:
                    review_count += 1
                    log(f"scan: trusted write rejected; queued to _review/ as fallback")
        elif confidence in ("inferred", "assumed"):
            ok, _ = write_review(workdir, item)
            if ok:
                review_count += 1
        else:
            log(f"scan: skipping item with unknown confidence={confidence!r}")

    log(
        f"scan: done — trusted={trusted_count}, review_queue={review_count}, "
        f"skipped_dup={skipped_dup}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"scan: unexpected error (swallowed for hook safety): {e}")
        sys.exit(0)
