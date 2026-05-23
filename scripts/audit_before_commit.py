#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Independent commit auditor — boundary-gated audit packet builder.

Fires from the PreToolUse Bash hook on every `git commit`, regardless of
who initiated the commit (manual user, Codex, build-loop, IDE button).
Deterministically builds a context packet from on-disk intent / goal / PRD /
constitution / trajectory, emits it to stderr for the running Claude session
to interpret, and exit-2's on unambiguous violations (secrets, conflict
markers). No LLM call from inside the hook — the running session renders
the verdict in conversation.

Verdict taxonomy (the running Claude chooses one):
    - yay (approve)
    - nay (reject)
    - suggest correction
    - look again

Exit codes:
    0 — packet emitted, no deterministic block
    2 — deterministic block (secrets file staged, merge-conflict markers)
    1 — reserved

Bypass: env var BUILDLOOP_AUDIT_BYPASS=1 skips all checks and logs to
~/.build-loop/audit-bypass.log.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration

MAX_DIFF_LINES = 200
MAX_TEXT_CHARS = 500
MAX_PRD_CHARS = 1000
README_HEAD_LINES = 50
SECRET_FILENAME_PATTERNS = (
    re.compile(r"(^|/)\.env(\..*)?$"),
    re.compile(r"(^|/)id_rsa(\..*)?$"),
    re.compile(r"(^|/)id_ed25519(\..*)?$"),
    re.compile(r"\.pem$"),
    re.compile(r"\.key$"),
    re.compile(r"\.p12$"),
)
SECRET_CONTENT_PATTERN = re.compile(
    r"(api[_-]?key|secret|password|token)\s*[=:]\s*['\"]?[A-Za-z0-9_\-\.]{8,}",
    re.IGNORECASE,
)
CONFLICT_MARKER = re.compile(r"^[+ ](<<<<<<<|=======|>>>>>>>)( |$)", re.MULTILINE)


# ---------------------------------------------------------------------------
# Helpers


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=4,
        )
        return r.stdout
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        return ""


def _repo_root() -> Path:
    out = _run(["git", "rev-parse", "--show-toplevel"]).strip()
    return Path(out) if out else Path.cwd()


def _read_optional(path: Path, max_chars: int | None = None) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:max_chars] if max_chars else text


def _truncate_lines(text: str, max_lines: int) -> tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    return "\n".join(lines[:max_lines]) + f"\n… ({len(lines) - max_lines} lines elided)", True


def _find_prd(root: Path) -> tuple[Path | None, str]:
    candidates = [
        root / ".build-loop" / "prd.md",
        root / "docs" / "PRD.md",
        root / "docs" / "prd.md",
    ]
    for c in candidates:
        if c.is_file():
            return c, _read_optional(c, MAX_PRD_CHARS)
    # Glob fallback
    prd_dir = root / "docs" / "prd"
    if prd_dir.is_dir():
        for c in sorted(prd_dir.glob("*.md")):
            return c, _read_optional(c, MAX_PRD_CHARS)
    return None, ""


def _constitution_rule_ids(constitution_text: str, files: list[str], diff_body: str) -> list[str]:
    """Keyword-match rule IDs in the constitution that the diff plausibly touches."""
    if not constitution_text:
        return []
    rule_ids = re.findall(r"\bC-[A-Z]+/[a-zA-Z0-9_-]+\b", constitution_text)
    unique = list(dict.fromkeys(rule_ids))
    if not unique:
        return []
    hay = (" ".join(files) + " " + diff_body).lower()
    hits = []
    for rid in unique:
        keyword = rid.split("/", 1)[1].replace("_", " ").lower()
        primary = keyword.split()[0] if keyword else ""
        if primary and primary in hay:
            hits.append(rid)
    return hits[:10]


def _staged_files() -> list[str]:
    out = _run(["git", "diff", "--cached", "--name-only"])
    return [ln for ln in out.splitlines() if ln.strip()]


def _staged_diff() -> str:
    return _run(["git", "diff", "--cached"])


def _staged_stat() -> str:
    return _run(["git", "diff", "--cached", "--stat"])


def _deterministic_block(files: list[str], diff_body: str) -> tuple[bool, str]:
    for f in files:
        for pat in SECRET_FILENAME_PATTERNS:
            if pat.search(f):
                # Only block if the staged content of that file looks secret-y
                content = _run(["git", "show", f":{f}"])
                if SECRET_CONTENT_PATTERN.search(content):
                    return True, f"staged file `{f}` looks like a secrets file with credential-shaped content"
                # filename alone is enough for hard-pattern items
                if pat.pattern.endswith(r"\.pem$") or "id_rsa" in pat.pattern or "id_ed25519" in pat.pattern:
                    return True, f"staged file `{f}` matches a hard secret-filename pattern"
    if CONFLICT_MARKER.search(diff_body):
        return True, "staged diff contains unresolved merge-conflict markers"
    return False, ""


def _log_bypass(reason: str) -> None:
    try:
        log_dir = Path.home() / ".build-loop"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "audit-bypass.log"
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        cwd = os.getcwd()
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts}\t{cwd}\t{reason}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Packet emission


def _emit_packet(root: Path) -> int:
    files = _staged_files()
    diff_body = _staged_diff()
    diff_stat = _staged_stat()

    if not files:
        # Empty commit or no staged changes — let git handle the error itself.
        return 0

    # Deterministic block first (zero-judgment hard fails)
    blocked, reason = _deterministic_block(files, diff_body)

    # Gather context (each optional, "(none found)" when missing)
    intent = _read_optional(root / ".build-loop" / "intent.md", MAX_TEXT_CHARS)
    goal = _read_optional(root / ".build-loop" / "goal.md", MAX_TEXT_CHARS)
    claude_md = _read_optional(root / "CLAUDE.md", MAX_TEXT_CHARS)
    readme_full = _read_optional(root / "README.md")
    readme_head = "\n".join(readme_full.splitlines()[:README_HEAD_LINES]) if readme_full else ""
    prd_path, prd_body = _find_prd(root)
    constitution = _read_optional(Path.home() / ".build-loop" / "memory" / "constitution.md")
    trajectory = _run(["git", "log", "--oneline", "-5"]).strip()

    rule_ids = _constitution_rule_ids(constitution, files, diff_body)

    diff_display, truncated = _truncate_lines(diff_body, MAX_DIFF_LINES)

    # Write packet to stderr so the running Claude session can render it.
    out = sys.stderr.write
    out("\n")
    out("## Audit packet\n")
    out(f"_emitted by audit_before_commit.py at {_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')}_\n\n")

    if blocked:
        out(f"### DETERMINISTIC BLOCK\n{reason}\n\n")

    out("### Staged diff\n")
    out("```\n")
    out(diff_stat or "(no stat)\n")
    out("```\n\n")
    out(f"Files staged ({len(files)}):\n")
    for f in files[:50]:
        out(f"- `{f}`\n")
    if len(files) > 50:
        out(f"- … and {len(files) - 50} more\n")
    out("\n")
    out("Diff body" + (" (truncated)" if truncated else "") + ":\n")
    out("```diff\n")
    out(diff_display or "(empty)")
    out("\n```\n\n")

    out("### Intent\n")
    out((intent or "_(none found)_") + "\n\n")

    out("### Goal\n")
    out((goal or "_(none found)_") + "\n\n")

    out("### Repo CLAUDE.md (head)\n")
    out((claude_md or "_(none found)_") + "\n\n")

    out("### README (head)\n")
    out((readme_head or "_(none found)_") + "\n\n")

    out("### PRD reference\n")
    if prd_path:
        out(f"From `{prd_path}`:\n\n{prd_body}\n\n")
    else:
        out("_(none found)_\n\n")

    out("### Constitution rules in scope\n")
    if rule_ids:
        for rid in rule_ids:
            out(f"- `{rid}`\n")
        out("\n")
    else:
        out("_(none matched by keyword)_\n\n")

    out("### Trajectory (last 5 commits)\n")
    out("```\n")
    out((trajectory or "(no history)") + "\n")
    out("```\n\n")

    out("### Verdict request\n")
    out("Render ONE of the four verdicts in your next assistant message, naming the verdict explicitly:\n\n")
    out("- **yay (approve)** — packet aligns with intent + constitution; the commit ships as-is.\n")
    out("- **nay (reject)** — packet contradicts intent or trips a constitution rule; the commit should not land.\n")
    out("- **suggest correction** — partial alignment; name specific edits the implementer should make before re-committing.\n")
    out("- **look again** — context insufficient to judge; name the missing artifact (PRD section, prior decision, test result) and gather it.\n\n")
    out("This audit packet is independent of any orchestrator dispatch. The hook fires at the git-commit boundary on every commit.\n\n")

    return 2 if blocked else 0


# ---------------------------------------------------------------------------
# Entry point


def main() -> int:
    if os.environ.get("BUILDLOOP_AUDIT_BYPASS") == "1":
        _log_bypass("BUILDLOOP_AUDIT_BYPASS=1")
        sys.stderr.write("[independent-commit-auditor] BYPASS active (BUILDLOOP_AUDIT_BYPASS=1) — logged.\n")
        return 0

    # Read tool input from stdin (PreToolUse hook contract); tolerate absence.
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except OSError:
        raw = ""

    # The hook matcher already filtered to Bash + git commit, but defensively
    # check the command if we received structured JSON.
    if raw:
        try:
            payload = json.loads(raw)
            cmd = payload.get("tool_input", {}).get("command", "") if isinstance(payload, dict) else ""
            if cmd and not re.search(r"\bgit\s+commit\b", cmd):
                return 0
            # Skip --no-verify / --amend dry-runs and configure-only invocations
            if re.search(r"\bgit\s+commit\b.*--no-verify\b", cmd):
                _log_bypass(f"--no-verify on: {cmd[:120]}")
        except (ValueError, json.JSONDecodeError):
            pass

    root = _repo_root()
    try:
        return _emit_packet(root)
    except Exception as exc:  # noqa: BLE001
        # Never crash a commit. Log and proceed.
        sys.stderr.write(f"[independent-commit-auditor] internal error: {exc!r} — proceeding without packet.\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
