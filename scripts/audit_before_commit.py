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
import sqlite3
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration

MAX_DIFF_LINES = 200
MAX_TEXT_CHARS = 500
MAX_PRD_CHARS = 1000
README_HEAD_LINES = 50
TRAJECTORY_FRESH_MIN = 30
RESEARCH_DIR = Path.home() / "dev" / "research"
API_REGISTRY_DB = Path.home() / ".api-registry" / "registry.db"
API_REGISTRY_STALENESS = Path.home() / ".api-registry" / "staleness.json"

# Risk-gated escalation (learn/risk-gated-commit-audit). High-risk signal
# thresholds — deliberately generous so "high" stays a meaningful minority of
# commits, not the default classification.
FILE_COUNT_RISK_THRESHOLD = 8
MODULE_BOUNDARY_DIR_THRESHOLD = 3
# Recency window for a recorded verdict to satisfy the opt-in hard block —
# small enough that a stale/unrelated audit can't accidentally cover a new
# diff, generous enough that recording the verdict a few minutes after the
# packet was emitted doesn't require perfect timing.
# TODO(risk-gate v2): this window lets a prior audit vouch for a later SUPERSET
# diff (same risky_files, more added since) as long as it's within the window —
# tighten via diff-hash binding instead of a time window + subset check.
RISK_VERDICT_RECENCY_MIN = 60

# Verdict taxonomy is yay/nay/suggest/look-again (see module docstring and
# audit_record_verdict.py's --verdict choices). Only "yay" is an approval —
# a recorded nay/suggest/look-again must not satisfy the risk-gate opt-in block.
_APPROVE_VERDICTS = frozenset({"yay"})

# Per arXiv:2604.16790 (Bias in the Loop) + 2410.21819 (Self-Preference Bias):
# explicit prompt-side mitigation. Single source of truth — both the audit
# packet and `agents/independent-auditor.md` reference this verbatim.
ANTI_BIAS_BLOCK = (
    "Ignore diff length when judging. Do not favor code in a style you would have written. "
    "If this diff resembles your own past output, hold it to a stricter standard, not a more lenient one. "
    "Challenge your first impression before emitting a verdict. "
    "Cite the specific intent or research-context entry your verdict turns on."
)
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

# Persisted-data write signal (filename + added-content, deterministic).
_PERSISTED_DATA_FILE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\.xcdatamodeld", re.IGNORECASE),
    re.compile(r"\bmigrations?\b", re.IGNORECASE),
    re.compile(r"\bschema\b", re.IGNORECASE),
    re.compile(r"\.sql$", re.IGNORECASE),
    re.compile(r"\bcoredata\b", re.IGNORECASE),
    re.compile(r"\bswiftdata\b", re.IGNORECASE),
    re.compile(r"(^|/)models?/", re.IGNORECASE),
    re.compile(r"models?\.(py|ts|swift|kt|java)$", re.IGNORECASE),
    re.compile(r"\bprisma\b", re.IGNORECASE),
    re.compile(r"\.prisma$", re.IGNORECASE),
    re.compile(r"(^|/)db/", re.IGNORECASE),           # common DB/ORM dir
    re.compile(r"(^|/)entities?/", re.IGNORECASE),    # TypeORM/Room-style entity dirs
    re.compile(r"(^|/)dao/", re.IGNORECASE),          # Room/JPA DAO dirs
)
_PERSISTED_DATA_CONTENT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"@Model\b"),                          # SwiftData
    re.compile(r"\bNSManagedObject\b"),                # CoreData
    re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bALTER\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+\w+\s+SET\b", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
    re.compile(r"class\s+\w+\(models\.Model\)"),       # Django ORM
    re.compile(r"@Entity\b"),                          # JPA / TypeORM / Room
    re.compile(r"@Dao\b"),                              # Room DAO
    re.compile(r"class\s+\w+\(Base\)"),                 # SQLAlchemy declarative model
    re.compile(r"\bdeclarative_base\b"),                # SQLAlchemy
    re.compile(r"\b(pgTable|sqliteTable|mysqlTable)\s*\("),  # Drizzle ORM
    re.compile(r"\bnew\s+Schema\s*\("),                 # Mongoose
    re.compile(r"\bmongoose\.model\s*\("),              # Mongoose
    re.compile(r"\bUserDefaults\b"),                    # iOS on-device store
    re.compile(r"\blocalStorage\b"),                    # web on-device store
    re.compile(r"\bkeychain\b", re.IGNORECASE),         # on-device secret/credential store
    re.compile(r"\bFileManager\.default\.(write|createFile)"),  # on-device file write
)

# New UI presentation surface signal (new file matching a presentation suffix,
# a Next.js app-router filename, or added content that defines a new
# sheet/screen/route construct).
_UI_FILENAME_RE = re.compile(
    r"(?:(Sheet|Screen|View|Route|Page)\.(swift|tsx|jsx|ts|vue|kt)$"
    r"|(?:^|/)(page|layout|route|template|default)\.(tsx|jsx|ts|js)$)",
    re.IGNORECASE,
)
_UI_CONTENT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\.sheet\("),
    re.compile(r"\bNavigationLink\b"),
    re.compile(r"<Route\b"),
    re.compile(r"\bnew\s+Route\("),
    re.compile(r"struct\s+\w+\s*:\s*View\b"),                         # new SwiftUI view
    re.compile(r"export\s+default\s+function\s+\w+(Page|Screen)\b"),  # new page/screen component
)


# ---------------------------------------------------------------------------
# Helpers


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=4)
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
# Risk classification (learn/risk-gated-commit-audit)
#
# NOTE — scope: this classifies the STAGED DIFF only. Scanning a subagent's
# *returned claims* for unverified caveats ("should work", "likely fixes")
# is a related but separate surface, deliberately deferred — future work.


def _new_files_from_diff(diff_body: str) -> set[str]:
    """Return post-image paths of newly-added files, derived from the diff body
    itself (``new file mode`` header line) — no extra git shell-out, so this
    stays consistent with the (files, diff_body) inputs the rest of this
    module's classification helpers already work from."""
    new_files: set[str] = set()
    header_re = re.compile(r"^diff --git a/(.+?) b/(.+)$")
    current: str | None = None
    for line in diff_body.splitlines():
        m = header_re.match(line)
        if m:
            current = m.group(2)
        elif line.startswith("new file mode") and current:
            new_files.add(current)
    return new_files


def _diff_by_file(diff_body: str) -> dict[str, str]:
    """Split a `git diff --cached` body into per-file (post-image path) chunks."""
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    header_re = re.compile(r"^diff --git a/(.+?) b/(.+)$")
    for line in diff_body.splitlines():
        m = header_re.match(line)
        if m:
            if current is not None:
                sections[current] = "\n".join(lines)
            current = m.group(2)
            lines = [line]
        else:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines)
    return sections


def _added_lines(section: str) -> str:
    return "\n".join(
        ln for ln in section.splitlines() if ln.startswith("+") and not ln.startswith("+++")
    )


def _find_persisted_data_files(files: list[str], diff_sections: dict[str, str]) -> list[str]:
    hits: list[str] = []
    for f in files:
        if any(pat.search(f) for pat in _PERSISTED_DATA_FILE_PATTERNS):
            hits.append(f)
            continue
        added = _added_lines(diff_sections.get(f, ""))
        if any(pat.search(added) for pat in _PERSISTED_DATA_CONTENT_PATTERNS):
            hits.append(f)
    return hits


def _find_new_ui_surface_files(
    files: list[str], new_files: set[str], diff_sections: dict[str, str]
) -> list[str]:
    hits: list[str] = []
    for f in files:
        if f in new_files and _UI_FILENAME_RE.search(f):
            hits.append(f)
            continue
        added = _added_lines(diff_sections.get(f, ""))
        if any(pat.search(added) for pat in _UI_CONTENT_PATTERNS):
            hits.append(f)
    return hits


def _classify_risk(files: list[str], diff_body: str) -> dict:
    """Classify the staged diff as high | medium | low risk.

    High-risk signals (any one fires -> high): persisted-data writes
    (SwiftData/CoreData/model/schema/DB/migration), a new UI presentation
    surface (sheet/screen/route), a large changeset (>= FILE_COUNT_RISK_THRESHOLD
    files), or a diff that crosses a module boundary (touches
    >= MODULE_BOUNDARY_DIR_THRESHOLD distinct top-level directories).

    Medium tier reuses infer_risk_surface.evaluate() (constitution-keyword +
    generic risk-keyword matching) rather than re-deriving that signal.

    Returns {"level": "high"|"medium"|"low", "reasons": [...], "risky_files": [...]}.
    """
    reasons: list[str] = []
    risky: set[str] = set()

    new_files = _new_files_from_diff(diff_body)
    diff_sections = _diff_by_file(diff_body)

    persisted = _find_persisted_data_files(files, diff_sections)
    if persisted:
        reasons.append(
            f"writes to persisted-data surface ({len(persisted)} file(s)): " + ", ".join(persisted[:10])
        )
        risky.update(persisted)

    ui_new = _find_new_ui_surface_files(files, new_files, diff_sections)
    if ui_new:
        reasons.append(
            f"adds UI presentation surface ({len(ui_new)} file(s)): " + ", ".join(ui_new[:10])
        )
        risky.update(ui_new)

    if len(files) >= FILE_COUNT_RISK_THRESHOLD:
        reasons.append(
            f"large changeset: {len(files)} files staged (threshold {FILE_COUNT_RISK_THRESHOLD})"
        )

    module_dirs = sorted({Path(f).parts[0] for f in files if len(Path(f).parts) > 1})
    if len(module_dirs) >= MODULE_BOUNDARY_DIR_THRESHOLD:
        reasons.append(
            f"crosses module boundary: {len(module_dirs)} top-level dirs ({', '.join(module_dirs)})"
        )

    if reasons:
        if not risky:
            risky.update(files[:20])
        return {"level": "high", "reasons": reasons, "risky_files": sorted(risky)[:20]}

    # Medium tier — reuse the existing constitution/generic keyword detector
    # instead of re-deriving keyword risk logic here.
    medium_reasons: list[str] = []
    try:
        import infer_risk_surface as _irs

        result = _irs.evaluate(diff_body, files, set())
        if result.get("risk_surface_change"):
            if result.get("matched_rules"):
                medium_reasons.append("constitution rules matched: " + ", ".join(result["matched_rules"]))
            for hit in result.get("generic_evidence") or []:
                medium_reasons.append(f"risk keyword: {hit.get('label')}")
    except Exception:
        pass

    if medium_reasons:
        return {"level": "medium", "reasons": medium_reasons, "risky_files": []}

    return {"level": "low", "reasons": [], "risky_files": []}


def _enforce_risk_audit_enabled(root: Path) -> bool:
    """Opt-in gate for the risk hard-block. DEFAULT OFF on both paths."""
    if os.environ.get("BUILDLOOP_ENFORCE_RISK_AUDIT") == "1":
        return True
    config_path = root / ".build-loop" / "config.json"
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if (cfg.get("sessionPrefs") or {}).get("enforceRiskAudit") is True:
                return True
        except (ValueError, OSError):
            pass
    return False


def _has_matching_risk_verdict(root: Path, risky_files: list[str]) -> bool:
    """True when a recent recorded verdict (via audit_record_verdict.py) covers
    every staged risky file. Reuses the same runs[].judge_decisions storage
    audit_record_verdict.py writes to — no separate storage introduced."""
    if not risky_files:
        return False
    state_path = root / ".build-loop" / "state.json"
    if not state_path.is_file():
        return False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    runs = data.get("runs") or []
    now = _dt.datetime.now(_dt.timezone.utc)
    staged = set(risky_files)
    for run in runs:
        for entry in run.get("judge_decisions") or []:
            if entry.get("judge_id") != "independent-auditor-hook":
                continue
            verdict = entry.get("verdict")
            if verdict not in _APPROVE_VERDICTS:
                continue
            vts_raw = entry.get("verdict_ts")
            if not vts_raw:
                continue
            try:
                vts = _dt.datetime.fromisoformat(vts_raw.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            age_min = (now - vts).total_seconds() / 60
            if age_min > RISK_VERDICT_RECENCY_MIN:
                continue
            entry_risky = set(entry.get("risky_files") or [])
            if staged <= entry_risky:
                return True
    return False


# ---------------------------------------------------------------------------
# Research-validated upgrades (see ~/dev/research/llm-judge-agents-for-coding-2026-05-23.md)
#   1. Parallel library/research context (IntPro arXiv:2603.03325)
#   2. Process-observation trajectory (Agent-as-a-Judge arXiv:2410.10934)
#   4. Hook-path persistence to runs[] (Verifiability-First arXiv:2512.17259)

_PKG_PATTERNS = (
    re.compile(r'^\+\s*"([@a-z0-9][\w./@-]*)"\s*:\s*"[\^~]?[\d.]', re.MULTILINE),  # npm
    re.compile(r"^\+\s*(?:from\s+(\w+)|import\s+(\w+))", re.MULTILINE),             # python
    re.compile(r'^\+\s*([\w./-]+)\s+v\d', re.MULTILINE),                            # go
)
_STDLIB = frozenset({"os", "sys", "re", "json", "datetime", "pathlib", "subprocess",
                     "typing", "collections", "functools", "itertools", "io", "time",
                     "math", "uuid", "hashlib", "tempfile", "shutil", "argparse",
                     "logging", "sqlite3"})


def _extract_packages(diff_body: str) -> list[str]:
    pkgs: set[str] = set()
    for pat in _PKG_PATTERNS:
        for m in pat.finditer(diff_body):
            name = next((g for g in m.groups() if g), "").split(".")[0]
            if name and len(name) > 1 and not name.startswith("_"):
                pkgs.add(name)
    return sorted(pkgs - _STDLIB)[:10]


def _library_context(diff_body: str) -> str:
    pkgs = _extract_packages(diff_body)
    if not pkgs:
        return "_(no library identifiers in staged diff)_\n"
    if not API_REGISTRY_DB.is_file():
        return "_(api-registry not present — skipping library lookup)_\n"
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)).timestamp()
    staleness: dict = {}
    if API_REGISTRY_STALENESS.is_file():
        try:
            staleness = json.loads(API_REGISTRY_STALENESS.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            staleness = {}
    lines: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{API_REGISTRY_DB}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return "_(api-registry read failed)_\n"
    try:
        for pkg in pkgs:
            try:
                row = conn.execute(
                    "SELECT docs_url, latest_version, deprecated_notes FROM services "
                    "WHERE name = ? OR package_ids LIKE ? LIMIT 1",
                    (pkg, f'%"{pkg}"%'),
                ).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                tag = f"`{pkg}`: {row[0] or '(no docs_url)'}"
                if row[1]:
                    tag += f" · latest {row[1]}"
                if row[2]:
                    tag += f" · DEPRECATED: {row[2][:80]}"
                pkg_staleness = staleness.get(pkg)
                age = pkg_staleness.get("age_days") if isinstance(pkg_staleness, dict) else None
                if isinstance(age, (int, float)) and age > 7:
                    tag += f" · cache stale {age}d"
                lines.append(f"- {tag}")
            else:
                lines.append(f"- `{pkg}`: not in api-registry")
            # Research grep
            if RESEARCH_DIR.is_dir():
                pat = re.compile(re.escape(pkg), re.IGNORECASE)
                hits = 0
                for md in sorted(RESEARCH_DIR.glob("*.md")):
                    try:
                        if md.stat().st_mtime < cutoff:
                            continue
                        text = md.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if pat.search(text):
                        excerpt = next((ln.strip() for ln in text.splitlines() if pat.search(ln)), "")[:140]
                        lines.append(f"  - research: **{md.stem}** — {excerpt}")
                        hits += 1
                        if hits >= 3:
                            break
    finally:
        conn.close()
    return "\n".join(lines) + "\n"


def _recent_trajectory(root: Path) -> str:
    state_path = root / ".build-loop" / "state.json"
    if not state_path.is_file():
        return "_(no active build trajectory)_\n"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return "_(no active build trajectory)_\n"
    runs = data.get("runs") or []
    if not runs:
        return "_(no active build trajectory)_\n"
    last = runs[-1]
    ts_str = last.get("endedAt") or last.get("date") or ""
    try:
        if "T" in ts_str and ts_str.endswith("Z") and "-" not in ts_str:
            run_ts = _dt.datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=_dt.timezone.utc)
        else:
            run_ts = _dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "_(no active build trajectory — timestamp unparseable)_\n"
    age_min = (_dt.datetime.now(_dt.timezone.utc) - run_ts).total_seconds() / 60
    if age_min > TRAJECTORY_FRESH_MIN:
        return f"_(last run {int(age_min)} min old — beyond {TRAJECTORY_FRESH_MIN} min window)_\n"
    chunks = last.get("chunks") or last.get("phases", {}).get("execute", {}).get("chunks") or []
    out = [f"- goal: {(last.get('goal') or '')[:160]}", f"- chunks: {len(chunks)}"]
    decisions = last.get("judge_decisions") or []
    if decisions:
        out.append("- last 3 judge_decisions:")
        for jd in decisions[-3:]:
            brief = (jd.get("brief") or jd.get("reason") or "")[:80]
            out.append(f"  - {jd.get('judge_id', '?')} → {jd.get('verdict', '?')}: {brief}")
    else:
        out.append("- (no judge_decisions yet)")
    return "\n".join(out) + "\n"


def _record_runs_judge_entry(
    root: Path, commit_hash: str, status: str, brief: str, risk: dict | None = None
) -> None:
    """Append a synthetic judge_decisions entry to runs[-1]; idempotent on (target, status) within 60s.

    `risk` (optional) is the dict from `_classify_risk` — recording risk_level
    and risky_files here makes a skipped/bypassed high-risk commit detectable
    after the fact, and lets `_has_matching_risk_verdict` later confirm a
    verdict recorded on this entry actually covers the risky files."""
    state_path = root / ".build-loop" / "state.json"
    if not state_path.is_file():
        return
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return
    runs = data.setdefault("runs", [])
    now_dt = _dt.datetime.now(_dt.timezone.utc)

    def _fresh_hook_run() -> dict:
        return {
            "run_id": f"hook_{now_dt.strftime('%Y%m%dT%H%M%SZ')}",
            "date": now_dt.strftime("%Y%m%dT%H%M%SZ"),
            "goal": "(hook-only commit; no orchestrator run)",
            "outcome": "partial", "phases": {}, "filesTouched": [],
            "diagnosticCommands": [], "manualInterventions": [],
            "active_experimental_artifacts": [], "judge_decisions": [],
        }

    if not runs:
        runs.append(_fresh_hook_run())
    else:
        # Membership guard (RCA 2026-07-11): do NOT attach this commit's packet to
        # runs[-1] when the trigger time falls outside that run's own window — a stale
        # runs[-1] from a prior/other session would otherwise absorb today's verdict.
        # Open the packet on a fresh hook-run entry instead. Fail-open: any import/parse
        # error keeps the historical append-to-last behavior.
        attach_to_last = True
        try:
            from temporal_membership import run_window as _rw, is_member as _im

            ws, we = _rw(runs[-1])
            attach_to_last, _reason = _im(now_dt, now_dt, ws, we)
        except Exception:
            attach_to_last = True
        if not attach_to_last:
            runs.append(_fresh_hook_run())
    decisions = runs[-1].setdefault("judge_decisions", [])
    for existing in decisions:
        if existing.get("target") == commit_hash and existing.get("status") == status:
            try:
                ets = _dt.datetime.fromisoformat(existing.get("ts", "").replace("Z", "+00:00"))
                if (now_dt - ets).total_seconds() < 60:
                    return
            except (ValueError, TypeError):
                pass
    decisions.append({
        "judge_id": "independent-auditor-hook", "target": commit_hash,
        "status": status, "verdict": "pending", "brief": brief,
        "ts": now_dt.isoformat(timespec="seconds"),
        "risk_level": (risk or {}).get("level"),
        "risky_files": (risk or {}).get("risky_files") or [],
    })
    tmp = state_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError:
        tmp.unlink(missing_ok=True)


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

    # Risk classification (learn/risk-gated-commit-audit) — always computed so
    # the packet, the recorded runs[] entry, and the opt-in hard block below
    # all see the same verdict.
    risk = _classify_risk(files, diff_body)
    enforce_risk_audit = _enforce_risk_audit_enabled(root)
    risk_blocked = False
    risk_block_reason = ""
    if (
        not blocked
        and risk["level"] == "high"
        and enforce_risk_audit
        and not _has_matching_risk_verdict(root, risk["risky_files"])
    ):
        risk_blocked = True
        risk_block_reason = (
            "high-risk commit without a matching independent audit — run the audit on this "
            "diff, record the verdict, then commit"
        )

    # Gather context (each optional, "(none found)" when missing)
    intent = _read_optional(root / ".build-loop" / "intent.md", MAX_TEXT_CHARS)
    goal = _read_optional(root / ".build-loop" / "goal.md", MAX_TEXT_CHARS)
    claude_md = _read_optional(root / "CLAUDE.md", MAX_TEXT_CHARS)
    readme_head = "\n".join(_read_optional(root / "README.md").splitlines()[:README_HEAD_LINES])
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

    if risk_blocked:
        out(f"### RISK GATE BLOCK\n{risk_block_reason}\n\n")
        out("Risky files:\n")
        for f in risk["risky_files"]:
            out(f"- `{f}`\n")
        out("\n")

    out("### Risk classification\n")
    out(f"- level: **{risk['level']}**\n")
    if risk["reasons"]:
        out("- reasons:\n")
        for r in risk["reasons"]:
            out(f"  - {r}\n")
    else:
        out("- reasons: _(none)_\n")
    out(f"- enforceRiskAudit: {enforce_risk_audit}\n\n")

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

    # Upgrade 1 — parallel research-context path (IntPro arXiv:2603.03325)
    out("### Library / research context\n")
    out(_library_context(diff_body) + "\n")

    # Upgrade 2 — process-observation (Agent-as-a-Judge arXiv:2410.10934)
    out("### Recent trajectory\n")
    out(_recent_trajectory(root) + "\n")

    # Upgrade 4 — persist a synthetic hook-path entry to runs[].judge_decisions[]
    commit_hash = _run(["git", "rev-parse", "--short", "HEAD"]).strip() or "staged"
    if blocked:
        status = "deterministic_block"
    elif risk_blocked:
        status = "risk_block"
    else:
        status = "packet_emitted"
    _record_runs_judge_entry(
        root, commit_hash, status, reason if blocked else f"{len(files)} files staged", risk=risk
    )

    out("### Verdict request\n")
    if risk["level"] == "high":
        out("**THIS DIFF IS HIGH-RISK.** Signals fired:\n")
        for r in risk["reasons"]:
            out(f"- {r}\n")
        out(
            "\nAn independent verdict is REQUIRED before this commit is considered reviewed. "
            "The verdict MUST explicitly cite the specific risky files:\n"
        )
        for f in risk["risky_files"]:
            out(f"- `{f}`\n")
        out("\n")
    out("Render ONE of the four verdicts in your next assistant message, naming the verdict explicitly:\n\n")
    out("- **yay (approve)** — packet aligns with intent + constitution; the commit ships as-is.\n")
    out("- **nay (reject)** — packet contradicts intent or trips a constitution rule; the commit should not land.\n")
    out("- **suggest correction** — partial alignment; name specific edits the implementer should make before re-committing.\n")
    out("- **look again** — context insufficient to judge; name the missing artifact (PRD section, prior decision, test result) and gather it.\n\n")
    out("**Anti-bias instruction (apply before emitting the verdict):**\n")
    out(ANTI_BIAS_BLOCK + "\n\n")
    out("After rendering the verdict, persist it to runs[] with:\n")
    out("`python3 scripts/audit_record_verdict.py --verdict <yay|nay|suggest|look-again> --reason \"<one-line>\" "
        "--oracle-completeness '{\"covered\":\"<what the checks exercise>\",\"uncovered\":\"<paths left unchecked>\",\"coverage\":\"full|partial|thin\"}'`\n\n")
    out("Include `--oracle-completeness` whenever you can judge how much of the changed surface the tests/checks actually cover — "
        "a green gate with a thin oracle is false confidence (arXiv:2606.09863); recording coverage makes it visible. The flag is optional and never blocks.\n\n")
    out("This audit packet is independent of any orchestrator dispatch. The hook fires at the git-commit boundary on every commit.\n\n")

    return 2 if (blocked or risk_blocked) else 0


# ---------------------------------------------------------------------------
# Entry point


def main() -> int:
    if os.environ.get("BUILDLOOP_AUDIT_BYPASS") == "1":
        _log_bypass("BUILDLOOP_AUDIT_BYPASS=1")
        try:
            root = _repo_root()
            commit_hash = _run(["git", "rev-parse", "--short", "HEAD"]).strip() or "staged"
            # Classify risk even on bypass so a skipped high-risk commit is
            # detectable after the fact via runs[].judge_decisions[].risk_level.
            try:
                risk = _classify_risk(_staged_files(), _staged_diff())
            except Exception:  # noqa: BLE001 — bypass must never fail on risk classification
                risk = None
            _record_runs_judge_entry(root, commit_hash, "bypass", "BUILDLOOP_AUDIT_BYPASS=1", risk=risk)
        except Exception:  # noqa: BLE001 — never crash a commit
            pass
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
