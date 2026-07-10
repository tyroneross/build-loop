#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
security_scan.py — deterministic pre-push security scanner for build-loop.

Deterministic complement to the LLM-pinned security-reviewer agent. Catches
the common, greppable 80/20 class of issues (e.g. console.log leaking an
OAuth access_token — the class missed by Fable). No LLM. No external deps.
Stdlib only. Runs anywhere, fast.

Canon references (findings cite rows from these):
  skills/security-methodology/references/owasp-web-top-10.md
  skills/security-methodology/references/owasp-llm-top-10.md
  skills/security-methodology/references/owasp-agentic-top-10.md
  skills/security-methodology/references/cross-source-matrix.md

Checks
  A. Secrets in source       HIGH   · A07/LLM06
  B. Secret-in-logs          HIGH   · A09/LLM06
  C. Injection               HIGH   · A03/LLM02/ASI05
  D. SSRF                    MEDIUM · A10
  E. Missing rate limit       MEDIUM · A01/LLM04
  F. Missing security headers LOW    · A05
  G. Prompt injection /      MEDIUM · LLM01/LLM08/ASI02
     excessive agency

Suppression: add `# nosec: <reason>` or `// nosec: <reason>` to a line.

Exit codes: 0 = nothing at/above threshold, 1 = something at/above threshold,
            2 = scanner error.

Known limitation (working-tree vs pushed-blob, f5):
  --diff scopes the scan to the FILES NAMED in ``<ref>..HEAD``, but reads their
  CURRENT WORKING-TREE content, not the exact blob at HEAD or the sequence of
  pushed commits. Consequences:
    - A secret committed and then removed later in the same push range escapes
      (the net working-tree content no longer shows it).
    - A secret dirty-edited out of the working tree before the push escapes.
  This is shared with the pre-delta whole-tree gate (not a regression), so the
  gate is best described as "scans the working-tree content of every file
  touched in the range", not "inspects every pushed blob". A future follow-up
  could scan pushed blobs directly (``git diff <ref>..HEAD -U0`` / per-commit
  ``git show``) — tracked as a backlog item, not required here.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS: set[str] = {
    # package managers / deps
    "node_modules", ".pnpm-store", ".yarn", ".npm", ".bun",
    # build output
    "dist", "build", "out", ".output", ".next", ".nuxt", ".svelte-kit",
    ".astro", ".parcel-cache", ".turbo", ".vercel", ".netlify",
    ".wrangler", "__pycache__",
    # vcs
    ".git", ".svn",
    # python envs
    "venv", ".venv", "env", ".tox", ".eggs", "eggs",
    ".mypy_cache", ".ruff_cache", ".pytest_cache",
    # other tool caches / agent tooling (vendored skills + tool state, not app source)
    ".cache", ".playground", ".codex", ".cursor", ".bookmark", ".claude",
    ".build-loop", ".navgator", ".ibr", ".mockup-gallery", ".rally",
    # compiled
    "target", "vendor", "coverage",
}

SKIP_FILE_RES: list[re.Pattern[str]] = [
    re.compile(r"\.min\.(js|css|mjs|cjs)$", re.IGNORECASE),
    re.compile(r"(package-lock|yarn\.lock|pnpm-lock|Cargo\.lock|Gemfile\.lock|poetry\.lock|composer\.lock)$"),
    re.compile(r"\.(ico|png|jpg|jpeg|gif|svg|webp|woff2?|ttf|eot|pdf|zip|gz|tar|bin|pyc|class|so|dll|exe|wasm|map)$", re.IGNORECASE),
]

SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

NOSEC_RE = re.compile(r"(#|//)\s*nosec\s*:", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Finding helper
# ---------------------------------------------------------------------------

def _finding(
    severity: str,
    owasp_ids: str,
    file_path: Path,
    line_no: int,
    message: str,
    snippet: str,
    fix: str,
    check_id: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "owasp_ids": owasp_ids,
        "file": str(file_path),
        "line": line_no,
        "message": message,
        "snippet": snippet.rstrip(),
        "fix": fix,
        "check_id": check_id,
    }

# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _is_binary(path: Path) -> bool:
    try:
        return b"\x00" in path.read_bytes()[:8192]
    except OSError:
        return True

def _is_test_file(path: Path) -> bool:
    name = path.name.lower()
    parts_lower = [p.lower() for p in path.parts]
    return (
        "test" in parts_lower
        or "__tests__" in parts_lower
        or "spec" in parts_lower
        or "fixtures" in parts_lower
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.js")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.js")
        or name.endswith(".spec.tsx")
    )

def _is_content_file(path: Path) -> bool:
    """Documentation / prose files — skip code-execution checks (still secret-scanned).

    Includes markdown/text plus anything under a top-level `docs/` tree: design
    mockups and doc snippets are documentation, not deployed request handlers.
    """
    if path.suffix.lower() in {".md", ".mdx", ".rst", ".txt"}:
        return True
    return "docs" in path.parts

def _skip_file_name(name: str) -> bool:
    for pat in SKIP_FILE_RES:
        if pat.search(name):
            return True
    return False

def _git_tracked_files(root: Path) -> set[Path] | None:
    """Absolute paths of git-tracked files, or None if not a usable git repo.

    A pre-push gate should scan what is actually being pushed — the tracked
    tree — not gitignored build/test artifacts (playwright-report, coverage,
    tool snapshots) that live on disk but never reach the remote. Returns None
    (→ caller falls back to full-tree walk) when git is unavailable or the path
    is not a repo, so non-git consumers keep working.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    tracked = {
        (root / rel).resolve()
        for rel in result.stdout.split("\0")
        if rel.strip()
    }
    return tracked or None


def _git_diff_files(root: Path, ref: str) -> set[Path] | None:
    """Resolved abs paths of files changed in ``<ref>..HEAD``, else None.

    None is the FAIL-SAFE signal — the caller falls back to a full scan (we
    never silently scan less than intended). Returned on any failure: not a git
    repo, an invalid ``<ref>``, or a git error. An EMPTY set is distinct from
    None: it means the range is empty (nothing changed) → scan nothing.

    Uses ``-z`` (NUL-delimited) so non-ASCII / quote / backslash filenames are
    NOT octal-escaped-and-quoted by git's default ``core.quotepath=true`` — a
    quoted name would ``resolve()`` to a path that matches nothing on disk and
    be silently dropped from the delta (the f1 false-negative). Mirrors
    ``_git_tracked_files``. ``--relative`` makes the emitted paths relative to
    the ``-C`` dir, so scanning a subdirectory (``--path <repo>/pkg``) joins
    correctly instead of prefixing repo-root-relative paths onto the subdir
    (the f2 false-negative); it also confines the delta to that subdir, matching
    a full scan rooted there.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", "-z", "--relative",
             f"{ref}..HEAD"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {
        (root / rel).resolve()
        for rel in result.stdout.split("\0")
        if rel.strip()
    }


def _matches_exclude(root: Path, path: Path, exclude_globs: list[str]) -> bool:
    """True when ``path``'s repo-relative posix path matches any fnmatch glob."""
    if not exclude_globs:
        return False
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    return any(fnmatch.fnmatch(rel, g) for g in exclude_globs)


def walk_source_files(
    root: Path,
    diff_set: set[Path] | None = None,
    exclude_globs: list[str] | None = None,
    stats: dict[str, int] | None = None,
):
    """Yield (path, lines) for non-binary text source files.

    Uses os.walk with directory pruning so we never descend into SKIP_DIRS.
    This is essential for large repos (pnpm-store, node_modules, dist). In a git
    repo, additionally restricts to tracked + not-ignored files so gitignored
    artifacts (that will never be pushed) don't produce phantom findings.

    Optional filters (default None/None → byte-for-byte unchanged behavior):
      - ``diff_set``: when not None, yield only files whose resolved path is in
        the set (delta scan — restrict to what's being pushed).
      - ``exclude_globs``: skip files whose repo-relative path matches any glob.
      - ``stats``: when a dict is passed, ``stats['excluded']`` is incremented
        for every real scan candidate (passed skip-name + diff + tracked) that
        an exclude glob removed — so an over-broad glob (e.g. ``*``) is
        surfaced instead of silently bypassing the whole scan (f4).
    """
    tracked = _git_tracked_files(root)  # None → not a git repo, scan everything
    for dirpath_str, dirnames, filenames in os.walk(str(root), topdown=True):
        # Prune SKIP_DIRS in-place so os.walk doesn't descend into them
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        dirnames.sort()

        for fname in sorted(filenames):
            if _skip_file_name(fname):
                continue
            path = Path(dirpath_str) / fname
            if diff_set is not None and path.resolve() not in diff_set:
                continue
            if tracked is not None and path.resolve() not in tracked:
                continue
            # Exclude is the LAST filter so stats['excluded'] counts only files
            # that would otherwise have been scanned (real candidates removed).
            # The yielded set is order-independent (all are AND/continue gates),
            # so this reorder is behavior-preserving vs the prior placement.
            if exclude_globs and _matches_exclude(root, path, exclude_globs):
                if stats is not None:
                    stats["excluded"] = stats.get("excluded", 0) + 1
                continue
            if _is_binary(path):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            # Skip minified/generated bundles (long single lines) even when the
            # extension isn't `.min.js` — committed playwright/trace/webpack
            # bundles produce phantom pattern matches, not reviewable source.
            if any(len(ln) > 2000 for ln in lines):
                continue
            yield path, lines

# ---------------------------------------------------------------------------
# String stripping utility (for check B)
# ---------------------------------------------------------------------------

_DQUOTE_RE = re.compile(r'"[^"\n\\]*(?:\\.[^"\n\\]*)*"')
_SQUOTE_RE = re.compile(r"'[^'\n\\]*(?:\\.[^'\n\\]*)*'")

def _strip_string_literals(line: str) -> str:
    """Replace string literal content with empty quotes. Preserves template ${} refs."""
    line = _DQUOTE_RE.sub('""', line)
    line = _SQUOTE_RE.sub("''", line)
    return line

# ---------------------------------------------------------------------------
# Check A: Secrets in source
# ---------------------------------------------------------------------------

_PROVIDER_KEY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"xkeysib-[A-Za-z0-9_\-]{20,}"), "Brevo/Sendinblue API key", "rotate immediately and move to env var"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}"), "OpenAI API key", "rotate at platform.openai.com/api-keys"),
    (re.compile(r"\bre_[A-Za-z0-9]{16,}\b"), "Resend API key", "rotate at resend.com/api-keys"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"), "GitHub PAT (classic)", "revoke at github.com/settings/tokens"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"), "GitHub fine-grained PAT", "revoke at github.com/settings/tokens"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key ID", "deactivate in IAM and rotate"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "Google API key", "delete in GCP console and rotate"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "Slack token", "revoke at api.slack.com/apps"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"), "GitLab PAT", "revoke in GitLab user settings"),
    (re.compile(r"\bey[A-Za-z0-9_\-]{40,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "JWT token", "do not commit JWTs; regenerate"),
]

_PEM_RE = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")

_GENERIC_SECRET_RE = re.compile(
    r"""(?:^|[\s,({])"""
    r"""(?:api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|auth[_-]?token|"""
    r"""private[_-]?key|db[_-]?pass(?:word)?|database[_-]?pass(?:word)?|"""
    r"""client[_-]?secret|webhook[_-]?secret|signing[_-]?secret|"""
    r"""jwt[_-]?secret|session[_-]?secret|encryption[_-]?key)\s*[=:]\s*"""
    r"""["']([^"']{8,})["']""",
    re.IGNORECASE,
)

_PLACEHOLDER_RE = re.compile(
    r"""^(x{3,}|<[^>]+>|\.{3,}|your[_\-]?|my[_\-]?key|example|"""
    r"""secure[_\-]?random|random[_\-]?string|s3cr3t|super[_\-]?secret|generated[_\-]?|"""
    r"""test[_\-]?|fake|dummy|mock|sample|changeme|replace|placeholder|"""
    r"""insert|todo|fixme|process\.env|os\.environ|getenv|env\.|env\(|\$\{|"""
    r"""config\.|secrets?\.|bun\.env|import\.meta\.env|"""
    r"""[A-Z][A-Z0-9_]{3,}(?:_SECRET|_KEY|_TOKEN|_PASSWORD|_PASS)\b)""",
    re.IGNORECASE,
)

_ENV_LOOKUP_RE = re.compile(
    r"(?:process\.env|os\.environ|getenv|Bun\.env|import\.meta\.env|"
    r"env\.\w|secrets\.\w|config\.\w|env\(|\$\{)",  # env(VAR) / ${VAR} config-reference syntax
    re.IGNORECASE,
)

_ENV_FILE_RE = re.compile(r"^(\.env[.\w]*|\.dev\.vars)$", re.IGNORECASE)

def check_A_secrets(path: Path, lines: list[str], root_path: Path | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    # .env and .dev.vars files are expected to hold secrets — caught only via
    # the git-tracking check (check_A_tracked_env_files), not content scanning.
    if _ENV_FILE_RE.match(path.name):
        return findings
    is_env_example = bool(re.search(r"\.env\.(example|sample|template|test)$", path.name, re.IGNORECASE))

    for lineno, raw in enumerate(lines, 1):
        if NOSEC_RE.search(raw):
            continue
        # Skip single-line comments
        stripped = raw.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue

        # Provider key patterns
        for pat, label, fix in _PROVIDER_KEY_PATTERNS:
            m = pat.search(raw)
            if m:
                val = m.group(0).strip()
                # Skip obvious test/example values or env lookups
                if _PLACEHOLDER_RE.search(val):
                    continue
                if _ENV_LOOKUP_RE.search(raw[:m.start() + 30]):
                    continue
                findings.append(_finding(
                    severity="HIGH",
                    owasp_ids="A07/LLM06",
                    file_path=path,
                    line_no=lineno,
                    message=f"Hardcoded {label}",
                    snippet=raw[:120],
                    fix=fix,
                    check_id="A",
                ))

        # PEM private key
        if _PEM_RE.search(raw):
            findings.append(_finding(
                severity="HIGH",
                owasp_ids="A07/LLM06",
                file_path=path,
                line_no=lineno,
                message="PEM private key in source",
                snippet=raw[:120],
                fix="move private key to a secrets manager; never commit PEM keys",
                check_id="A",
            ))

        # Generic KEY/SECRET/TOKEN/PASSWORD = "value" (skip env examples)
        if not is_env_example:
            m = _GENERIC_SECRET_RE.search(raw)
            if m:
                val = m.group(1)
                if not _PLACEHOLDER_RE.search(val) and not _ENV_LOOKUP_RE.search(raw):
                    findings.append(_finding(
                        severity="HIGH",
                        owasp_ids="A07/LLM06",
                        file_path=path,
                        line_no=lineno,
                        message="Hardcoded secret/key/token literal",
                        snippet=raw[:120],
                        fix="move to environment variable; check git history with `git log -S <value>`",
                        check_id="A",
                    ))

    return findings

def check_A_tracked_env_files(root: Path) -> list[dict[str, Any]]:
    """Check if .env or .dev.vars files are tracked in git."""
    findings: list[dict[str, Any]] = []
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=root, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return findings
        for tracked in result.stdout.splitlines():
            tracked = tracked.strip()
            # Placeholder templates carry no real secrets by definition — don't flag them.
            if re.search(r"\.(example|sample|template|dist)$", tracked, re.IGNORECASE):
                continue
            if re.match(r"^(\.env[.\w]*|\.dev\.vars)$", tracked, re.IGNORECASE):
                findings.append(_finding(
                    severity="HIGH",
                    owasp_ids="A07/LLM06",
                    file_path=root / tracked,
                    line_no=1,
                    message=f"Env file {tracked!r} is tracked by git — secrets may be in git history",
                    snippet=f"git ls-files: {tracked}",
                    fix=f"git rm --cached {tracked} && echo '{tracked}' >> .gitignore; rotate any exposed secrets",
                    check_id="A",
                ))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return findings

# ---------------------------------------------------------------------------
# Check B: Secret-in-logs
# ---------------------------------------------------------------------------

_LOG_CALL_RE = re.compile(
    r"\b(?:console\.(?:log|warn|error|info|debug|trace)|"
    r"logger\.(?:log|warn|error|info|debug|trace)|"
    r"log\.(?:warn|error|info|debug)|"
    r"print(?:ln)?|logging\.(?:warning|error|info|debug|critical))\s*\(",
    re.IGNORECASE,
)

# After stripping string literals, sensitive variable names in log args
_SENSITIVE_VAR_RE = re.compile(
    r"\b(?:access_?token|refresh_?token|accessToken|refreshToken|"
    r"token_?data|tokenData|auth_?token|authToken|"
    r"api_?key|apiKey|secret_?key|secretKey|"
    r"private_?key|privateKey|password|passwd|"
    r"credentials?|authorization|bearerToken|oauth_?token|"
    r"oauthToken|tokenRes(?:ult|ponse)?|tokenResponse)\b",
    re.IGNORECASE,
)

# Log call with sensitive string label AND a non-string second argument
# Catches: console.log('Token response body:', text)
_LOG_SENSITIVE_LABEL_RE = re.compile(
    r"(?:console\.\w+|logger\.\w+|log\.\w+|print(?:ln)?)\s*\("
    r"""["'][^"'\n]*\b(?:token|oauth|secret|password|api[_\-]?key|credential|access_token)[^"'\n]*["']"""
    r"\s*,\s*"
    r"(?!(?:err(?:or)?|e(?:\b|\s*[,)])|exception|null|undefined|true|false|\d))"
    r"([^\"'\s,)]{2,})",
    re.IGNORECASE,
)

def check_B_secret_in_logs(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, raw in enumerate(lines, 1):
        if NOSEC_RE.search(raw):
            continue
        stripped = raw.strip()
        # Skip commented-out lines
        if stripped.startswith("//") or stripped.startswith("#") or stripped.startswith("*"):
            continue
        if not _LOG_CALL_RE.search(raw):
            continue

        # Signal 1: sensitive var name after stripping string literals
        cleaned = _strip_string_literals(raw)
        if _SENSITIVE_VAR_RE.search(cleaned):
            findings.append(_finding(
                severity="HIGH",
                owasp_ids="A09/LLM06",
                file_path=path,
                line_no=lineno,
                message="Log call includes a sensitive variable (token/key/password/credential)",
                snippet=raw[:120],
                fix="remove the sensitive variable from the log argument; log only safe metadata",
                check_id="B",
            ))
            continue

        # Signal 2: string label with "token/secret/oauth" + non-string second arg
        if _LOG_SENSITIVE_LABEL_RE.search(raw):
            findings.append(_finding(
                severity="HIGH",
                owasp_ids="A09/LLM06",
                file_path=path,
                line_no=lineno,
                message="Log call has a token/secret label with a variable argument — likely logging a secret value",
                snippet=raw[:120],
                fix="remove the variable argument; log only non-sensitive metadata (status codes, IDs)",
                check_id="B",
            ))

    return findings

# ---------------------------------------------------------------------------
# Check C: Injection
# ---------------------------------------------------------------------------

# SQL via template literals: .query(`SELECT ... ${`) — require an actual SQL
# statement verb inside the backtick so Redis/other command DSLs
# (`.execute(`GET "${key}"`)`) are not mislabeled as SQL injection.
_SQL_TEMPLATE_RE = re.compile(
    r"\.(?:prepare|query|exec(?:ute)?|run|all)\s*\(\s*`[^`]*"
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE|UPSERT)\b"
    r"[^`]*\$\{",
    re.IGNORECASE,
)

# A real SQL statement: a verb PLUS a clause/structural token. Guards f-string /
# .format() SQL checks so English prose ("Failed to update cache: {e}") that
# merely contains a keyword is not flagged.
_SQL_STMT_RE = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE)\b"
    r"[^\n]*?\b(?:FROM|INTO|SET|WHERE|VALUES|TABLE|JOIN|COLUMN|INDEX|DATABASE|SCHEMA|CONFLICT)\b",
    re.IGNORECASE,
)

# Python f-string SQL
_SQL_FSTRING_RE = re.compile(
    r'f["\'].*?(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER)\b.*?\{',
    re.IGNORECASE,
)

# Python % / .format SQL
_SQL_FORMAT_RE = re.compile(
    r'["\'].*?(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b[^"\']*["\'].*?(?:%\s*[\(%]|\.format\s*\()',
    re.IGNORECASE,
)

# Shell execution
_SHELL_EXEC_RE = re.compile(
    r"(?:"
    r"child_process\.exec\s*\([^,)]+[+`]|"           # exec with string concat
    r"execSync\s*\([^,)]+[+`]|"                       # execSync with string concat
    r"subprocess\.\w+\s*\([^)]*shell\s*=\s*True|"    # shell=True
    r"os\.system\s*\("                                 # os.system
    r")",
    re.IGNORECASE,
)

# Code eval
_EVAL_RE = re.compile(r"\beval\(|\bnew\s+Function\(", re.IGNORECASE)

# XSS sinks: dangerous HTML injection
_XSS_RE = re.compile(
    r"dangerouslySetInnerHTML\s*=\s*\{\{|"
    r"\.innerHTML\s*=[^=\n]|"
    r"\bv-html\s*=",
    re.IGNORECASE,
)
# A known HTML sanitizer wraps the value → the sink is the recommended safe
# pattern, not a finding (DOMPurify.sanitize(...), sanitizeHtml(...), xss(...)).
_XSS_SAFE_RE = re.compile(
    r"\b(?:DOMPurify|sanitizeHtml|sanitize_html|sanitizeHTML|sanitize\s*\(|"
    r"purify|xss\s*\(|escapeHtml|escapeHTML|escape\s*\()",
    re.IGNORECASE,
)

# Patterns that indicate innerHTML is receiving a STATIC value (not user-influenced)
_XSS_STATIC_ASSIGN_RE = re.compile(
    r"\.innerHTML\s*=\s*(?:"
    r"[\"'`]|"                              # direct string literal
    r"(?:true|false|null|undefined)\b|"     # boolean/null
    r"\w+\s*\?"                             # ternary (value determined next line)
    r")",
    re.IGNORECASE,
)

_CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".rb", ".go", ".php", ".java", ".cs", ".swift", ".kt", ".rs",
    ".astro", ".vue", ".svelte",
})

_DYNAMIC_ASSIGN_RE = re.compile(r"[=+]\s*[^\"'\s`]")  # assigned something that's not a plain string

def check_C_injection(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, raw in enumerate(lines, 1):
        if NOSEC_RE.search(raw):
            continue
        stripped = raw.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue

        if _SQL_TEMPLATE_RE.search(raw):
            findings.append(_finding(
                severity="HIGH",
                owasp_ids="A03/LLM02/ASI05",
                file_path=path,
                line_no=lineno,
                message="SQL query built via template literal interpolation — SQL injection risk",
                snippet=raw[:120],
                fix="use parameterized queries (? placeholders + .bind()) instead of string interpolation",
                check_id="C",
            ))
        elif _SQL_FSTRING_RE.search(raw) and _SQL_STMT_RE.search(raw):
            findings.append(_finding(
                severity="HIGH",
                owasp_ids="A03/LLM02/ASI05",
                file_path=path,
                line_no=lineno,
                message="SQL query built via Python f-string — SQL injection risk",
                snippet=raw[:120],
                fix="use parameterized queries with placeholders (%s / ?) and cursor.execute(sql, params)",
                check_id="C",
            ))
        elif _SQL_FORMAT_RE.search(raw) and _SQL_STMT_RE.search(raw):
            findings.append(_finding(
                severity="HIGH",
                owasp_ids="A03/LLM02/ASI05",
                file_path=path,
                line_no=lineno,
                message="SQL query built via string formatting — SQL injection risk",
                snippet=raw[:120],
                fix="use parameterized queries with placeholders",
                check_id="C",
            ))

        if _SHELL_EXEC_RE.search(raw):
            findings.append(_finding(
                severity="HIGH",
                owasp_ids="A03/LLM02/ASI05",
                file_path=path,
                line_no=lineno,
                message="Shell command executed with potentially dynamic input",
                snippet=raw[:120],
                fix="use execFile / subprocess.run with a list (not shell=True); validate/allowlist inputs",
                check_id="C",
            ))

        if _EVAL_RE.search(_strip_string_literals(raw)):
            # Only flag in code files (skip .json, .md, .txt, etc. where "eval" appears in text)
            if path.suffix.lower() in _CODE_EXTENSIONS and not _is_test_file(path):
                findings.append(_finding(
                    severity="HIGH",
                    owasp_ids="A03/LLM02/ASI05",
                    file_path=path,
                    line_no=lineno,
                    message="eval() or new Function() — code injection risk",
                    snippet=raw[:120],
                    fix="replace eval with a safe JSON parser or explicit logic; never eval user input",
                    check_id="C",
                ))

        if _XSS_RE.search(raw):
            # Skip static assignments: direct string, boolean, or multiline ternary
            is_static = _XSS_STATIC_ASSIGN_RE.search(raw)
            # The injected value may sit on the next line(s) for JSX
            # (`dangerouslySetInnerHTML={{` then `__html: ...`), so inspect a
            # small window for a sanitizer wrapper or a <style> (CSS) target.
            window = " ".join(lines[max(0, lineno - 2):lineno + 3])
            is_sanitized = _XSS_SAFE_RE.search(window)
            is_style_css = "<style" in window.lower()
            if not is_static and not is_sanitized and not is_style_css:
                # Also skip if the NEXT line begins a ternary (multiline split: `= var\n  ? 'str'`)
                next_raw = lines[lineno] if lineno < len(lines) else ""  # lineno is 1-based
                is_multiline_ternary = next_raw.strip().startswith("?")
                if not is_multiline_ternary:
                    findings.append(_finding(
                        severity="HIGH",
                        owasp_ids="A03/LLM02",
                        file_path=path,
                        line_no=lineno,
                        message="Direct HTML injection sink (dangerouslySetInnerHTML / innerHTML / v-html)",
                        snippet=raw[:120],
                        fix="sanitize with DOMPurify before injecting HTML, or avoid raw HTML entirely",
                        check_id="C",
                    ))

    return findings

# ---------------------------------------------------------------------------
# Check D: SSRF
# ---------------------------------------------------------------------------

# fetch/requests with a template literal URL containing ${...}
_SSRF_TEMPLATE_RE = re.compile(
    r"(?:fetch|axios\.(?:get|post|put|patch|delete|request)|"
    r"requests?\.(?:get|post|put|patch|delete|request)|"
    r"urllib\.request\.urlopen|http\.(?:get|post|request))\s*\(\s*`[^`\n]*\$\{",
    re.IGNORECASE,
)

def check_D_ssrf(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, raw in enumerate(lines, 1):
        if NOSEC_RE.search(raw):
            continue
        stripped = raw.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue

        if _SSRF_TEMPLATE_RE.search(raw):
            findings.append(_finding(
                severity="MEDIUM",
                owasp_ids="A10",
                file_path=path,
                line_no=lineno,
                message="HTTP request to a dynamic (template-literal) URL — SSRF risk if URL is user-influenced",
                snippet=raw[:120],
                fix="validate/allowlist the URL before fetching; reject private IP ranges; use an egress proxy",
                check_id="D",
            ))
    return findings

# ---------------------------------------------------------------------------
# Check E: Public mutating endpoint without rate limiting
# ---------------------------------------------------------------------------

_API_HANDLER_RE = re.compile(
    r"\bexport\s+(?:const|async\s+function|function)\s+(?:POST|PUT|PATCH|DELETE)\b|"
    r"router\.(?:post|put|patch|delete)\s*\(|"
    r"app\.(?:post|put|patch|delete)\s*\(",
    re.IGNORECASE,
)

_MUTATION_RE = re.compile(
    r"(?:sendEmail|send_email|nodemailer|createTransport|SESClient|"
    r"resend\.|brevo\.|mailgun\.|postmark\.|sparkpost\.|"
    r"\.prepare\s*\(|DB\.\w+\s*\(|db\.\w+\s*\(|"
    r"\bINSERT\s+INTO\b|\bUPDATE\s+\w|\bDELETE\s+FROM\b|"
    r"stripe\.|openai\.|anthropic\.|"
    r"\.send\s*\(|\.sendMail\s*\()",
    re.IGNORECASE,
)

_RATE_LIMIT_RE = re.compile(
    r"(?:rate[\s_\-]?limit|ratelimit|rateLimiter|rate_limiter|"
    r"\blimiter\b|throttle(?:r)?|slowDown|"
    r"express-rate-limit|@upstash/ratelimit|upstash.*ratelimit|"
    r"sliding[\s_\-]?window|fixed[\s_\-]?window|token[\s_\-]?bucket)",
    re.IGNORECASE,
)

_TURNSTILE_RE = re.compile(r"(?:turnstile|siteverify)", re.IGNORECASE)

def _is_api_path(path: Path) -> bool:
    parts_lower = [p.lower() for p in path.parts]
    return (
        "api" in parts_lower
        or "functions" in parts_lower
        or "routes" in parts_lower
    )

def check_E_rate_limiting(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    if not _is_api_path(path):
        return []

    content = "\n".join(lines)

    # Must export a mutating handler
    handler_match = _API_HANDLER_RE.search(content)
    if not handler_match:
        return []

    # Must perform a mutation (email, DB write, paid API)
    if not _MUTATION_RE.search(content):
        return []

    # If rate limiting is present, no finding
    if _RATE_LIMIT_RE.search(content):
        return []

    # Find the handler line for reporting
    handler_line = 1
    handler_snippet = ""
    for i, line in enumerate(lines, 1):
        if _API_HANDLER_RE.search(line):
            handler_line = i
            handler_snippet = line.strip()
            break

    has_turnstile = bool(_TURNSTILE_RE.search(content))
    note = " (Turnstile present as bot gate — not a rate limit)" if has_turnstile else ""

    return [_finding(
        severity="MEDIUM",
        owasp_ids="A01/LLM04",
        file_path=path,
        line_no=handler_line,
        message=f"POST/mutating endpoint performs email send or DB write but has no per-IP rate limiting{note}",
        snippet=handler_snippet,
        fix="add explicit per-IP rate limiting (Cloudflare Rate Limiting rule, KV-based counter, or @upstash/ratelimit)",
        check_id="E",
    )]

# ---------------------------------------------------------------------------
# Check F: Missing security headers (project-level)
# ---------------------------------------------------------------------------

_CSP_CONFIG_RE = re.compile(
    r"(?:content.?security.?policy|ContentSecurityPolicy|csp[\"'\s:,])",
    re.IGNORECASE,
)

def check_F_security_headers(root: Path) -> list[dict[str, Any]]:
    # Look for _headers file outside skip dirs
    headers_file_found = False
    for path in root.rglob("_headers"):
        # Must not be inside a skipped directory
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if not any(part in SKIP_DIRS for part in rel.parts[:-1]):
            headers_file_found = True
            break

    if headers_file_found:
        return []

    # Look for CSP in framework config files
    config_candidates = [
        root / "astro.config.mjs",
        root / "astro.config.ts",
        root / "next.config.js",
        root / "next.config.ts",
        root / "next.config.mjs",
        root / "nuxt.config.ts",
        root / "nuxt.config.js",
        root / "vite.config.ts",
        root / "vite.config.js",
        root / "svelte.config.js",
    ]
    for cfg in config_candidates:
        if cfg.exists():
            try:
                text = cfg.read_text(encoding="utf-8", errors="replace")
                if _CSP_CONFIG_RE.search(text):
                    return []
            except OSError:
                pass

    # Also check public/_headers as a common location
    for candidate in [root / "public" / "_headers", root / "static" / "_headers"]:
        if candidate.exists():
            return []

    return [_finding(
        severity="LOW",
        owasp_ids="A05",
        file_path=root,
        line_no=0,
        message="No _headers file found (outside node_modules/dist) and no CSP in framework config",
        snippet="(project-level finding)",
        fix="add public/_headers with: Content-Security-Policy, X-Frame-Options: DENY, X-Content-Type-Options: nosniff",
        check_id="F",
    )]

# ---------------------------------------------------------------------------
# Check G: Prompt injection / excessive agency
# ---------------------------------------------------------------------------

# User input concatenated into prompt variable
_PROMPT_CONCAT_RE = re.compile(
    r"(?:"
    r"prompt\s*\+="  # prompt += ...
    r"|"
    r"(?:let|const|var)\s+\w*[Pp]rompt\w*\s*=\s*`[^`]*\$\{"  # const myPrompt = `...${
    r"|"
    r"""(?:system|prompt)\s*[=:]\s*["`'][^"`']*\$\{"""  # system = `...${  or prompt: `...${
    r")",
    re.IGNORECASE,
)

# RHS extractor for the `prompt +=` form (first _PROMPT_CONCAT_RE alternative)
_PROMPT_APPEND_RE = re.compile(r"prompt\s*\+=\s*(.*)$", re.IGNORECASE)


def _static_prompt_rhs(lines: list[str], lineno: int) -> bool:
    """True when a `prompt +=` RHS is a fully static literal — a closed plain
    string or a triple-quoted block with no interpolation (`\\(` Swift, `${` JS)
    — so it cannot carry user input. Anything else (identifiers, calls,
    interpolation) stays flagged. Added 2026-07-03 after 4 observed false
    positives on static instruction blocks in a Swift consumer repo.
    """
    m = _PROMPT_APPEND_RE.search(lines[lineno - 1])
    if not m:
        return False
    rhs = m.group(1).strip().rstrip(";").strip()
    if rhs.startswith('"""'):
        rest = rhs[3:]
        if '"""' in rest:  # opened and closed on the same line
            return "\\(" not in rest and "${" not in rest
        for follow in lines[lineno:lineno + 200]:  # scan to the closing delimiter
            if "\\(" in follow or "${" in follow:
                return False
            if '"""' in follow:
                return True
        return False  # unterminated within window — stay conservative, flag
    if len(rhs) >= 2 and rhs[0] in "\"'`" and rhs.endswith(rhs[0]):
        return "\\(" not in rhs and "${" not in rhs
    return False


# Wildcard tool permissions
_WILDCARD_TOOLS_RE = re.compile(
    r"""(?:tools|allowedTools|tool_choice)\s*[=:]\s*["\[]['"]?\s*\*\s*['"]?["\]]""",
    re.IGNORECASE,
)

# Shell/exec tool exposed in agent tool list
_SHELL_TOOL_AGENT_RE = re.compile(
    r"""["'](bash|shell|exec|terminal|run_command|execute_code|computer_use)["']""",
    re.IGNORECASE,
)

def check_G_prompt_injection(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, raw in enumerate(lines, 1):
        if NOSEC_RE.search(raw):
            continue
        stripped = raw.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue

        if _PROMPT_CONCAT_RE.search(raw) and not _static_prompt_rhs(lines, lineno):
            findings.append(_finding(
                severity="MEDIUM",
                owasp_ids="LLM01/LLM08/ASI02",
                file_path=path,
                line_no=lineno,
                message="User/external input concatenated into prompt — prompt injection risk",
                snippet=raw[:120],
                fix="wrap untrusted input in a delimited block (e.g. <user-input>...</user-input>) and add instruction-data separation",
                check_id="G",
            ))

        if _WILDCARD_TOOLS_RE.search(raw):
            findings.append(_finding(
                severity="MEDIUM",
                owasp_ids="LLM08/ASI02",
                file_path=path,
                line_no=lineno,
                message="Wildcard tool permission (*) grants agent excessive capability",
                snippet=raw[:120],
                fix="enumerate only the tools the agent needs for this workflow; remove wildcard",
                check_id="G",
            ))

        if _SHELL_TOOL_AGENT_RE.search(raw):
            findings.append(_finding(
                severity="MEDIUM",
                owasp_ids="LLM08/ASI02/ASI05",
                file_path=path,
                line_no=lineno,
                message="Shell/exec tool exposed in agent tool list — excessive agency if prompt-injectable",
                snippet=raw[:120],
                fix="sandbox the shell tool; add human-in-the-loop approval; scope to read-only if possible",
                check_id="G",
            ))

    return findings

# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

_SEV_COLOR = {
    "CRITICAL": "\033[91m",  # bright red
    "HIGH":     "\033[91m",  # bright red
    "MEDIUM":   "\033[93m",  # yellow
    "LOW":      "\033[94m",  # blue
}
_RESET = "\033[0m"

def _no_color() -> bool:
    return not sys.stdout.isatty() or os.environ.get("NO_COLOR") or os.environ.get("CI")

def _color(severity: str, text: str) -> str:
    if _no_color():
        return text
    return f"{_SEV_COLOR.get(severity, '')}{text}{_RESET}"

def _summary_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return counts

def format_report(
    findings: list[dict[str, Any]],
    threshold: str,
    scan_path: str,
    files_scanned: int,
    diff_info: dict[str, Any] | None = None,
    exclude_info: dict[str, Any] | None = None,
) -> str:
    counts = _summary_counts(findings)
    total = sum(counts.values())

    counts_str = "  ".join(f"{v} {k}" for k, v in counts.items() if v > 0) or "none"
    lines = [
        "══════════════════════════════════════════════════════════════════",
        f"  security_scan.py — {counts_str}",
        f"  path: {scan_path}  |  files: {files_scanned}  |  threshold: {threshold}",
    ]
    if diff_info is not None:
        if diff_info.get("mode") == "delta":
            lines.append(
                f"  scope: delta vs {diff_info['ref']}  |  changed files: {diff_info['changed_files']}"
            )
        else:  # fallback-full-scan
            reason = diff_info.get("fallback_reason", "ref/repo unavailable")
            lines.append(
                f"  scope: full-tree (fell back from --diff {diff_info['ref']}: "
                f"{reason})"
            )
    # f4: surface the active exclude globs + how many candidate files they
    # removed, so an over-broad glob (e.g. `*`, which fnmatch matches on every
    # path) is visible instead of a silent total bypass.
    if exclude_info is not None:
        globs = exclude_info.get("globs", [])
        removed = exclude_info.get("files_removed", 0)
        lines.append(
            f"  exclude: {len(globs)} glob(s) [{', '.join(globs)}] — "
            f"removed {removed} file(s) from scan"
        )
    lines.append("══════════════════════════════════════════════════════════════════")

    if not findings:
        lines.append("")
        lines.append("  No findings. ✓")
        lines.append("")
    else:
        # Group by severity
        by_sev: dict[str, list[dict[str, Any]]] = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}
        for f in findings:
            by_sev.setdefault(f["severity"], []).append(f)

        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            sev_findings = by_sev.get(sev, [])
            if not sev_findings:
                continue
            lines.append("")
            lines.append(_color(sev, f"── {sev} {'─' * (60 - len(sev))}"))
            lines.append("")
            for f in sev_findings:
                loc = f"(project-level)" if f["line"] == 0 else f"{f['file']}:{f['line']}"
                lines.append(_color(sev, f"[{sev}]") + f" {f['owasp_ids']}  {loc}")
                lines.append(f"  {f['message']}")
                if f["snippet"] and f["snippet"] != "(project-level finding)":
                    lines.append(f"  │  {f['snippet'][:100]}")
                lines.append(f"  fix: {f['fix']}")
                lines.append("")

    # Summary table
    threshold_idx = SEVERITY_ORDER.get(threshold, 1)
    breached = any(SEVERITY_ORDER.get(f["severity"], 9) <= threshold_idx for f in findings)
    exit_note = f"exit 1 (findings at/above {threshold})" if breached else f"exit 0 (no findings at/above {threshold})"

    lines.append("══════════════════════════════════════════════════════════════════")
    lines.append(f"  SUMMARY  →  {exit_note}")
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        n = counts.get(sev, 0)
        lines.append(f"    {sev:<10} {n}")
    lines.append(f"    {'Total':<10} {total}")
    lines.append("══════════════════════════════════════════════════════════════════")

    return "\n".join(lines)

def format_json_output(
    findings: list[dict[str, Any]],
    threshold: str,
    scan_path: str,
    files_scanned: int,
    diff_info: dict[str, Any] | None = None,
    exclude_info: dict[str, Any] | None = None,
) -> str:
    counts = _summary_counts(findings)
    threshold_idx = SEVERITY_ORDER.get(threshold, 1)
    breached = any(SEVERITY_ORDER.get(f["severity"], 9) <= threshold_idx for f in findings)
    out: dict[str, Any] = {
        "scan_path": scan_path,
        "files_scanned": files_scanned,
        "threshold": threshold,
        "threshold_breached": breached,
        "findings": findings,
        "summary": {**counts, "total": sum(counts.values())},
    }
    # Only present when --diff was used, so default output stays byte-identical.
    if diff_info is not None:
        out["diff"] = diff_info
    # Only present when --exclude was used, so default output stays byte-identical.
    if exclude_info is not None:
        out["exclude"] = exclude_info
    return json.dumps(out, indent=2)

# ---------------------------------------------------------------------------
# Scan driver (re-runnable so the belt-and-braces fallback can redo a full scan)
# ---------------------------------------------------------------------------

def _perform_scan(
    root: Path,
    diff_set: set[Path] | None,
    diff_active: bool,
    exclude_globs: list[str],
    empty_diff: bool,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    """Run all checks once. Returns (findings, files_scanned, walk_stats).

    Factored out of main() so the f1 belt-and-braces path can re-invoke it for
    a full scan when a delta scan matched zero of its own changed files.
    """
    walk_stats: dict[str, int] = {"excluded": 0}
    all_findings: list[dict[str, Any]] = []
    files_scanned = 0

    # Project-level checks first. In delta mode, keep only findings whose file is
    # in the changed set; the exclude filter applies in every mode.
    if not empty_diff:
        proj_findings = check_A_tracked_env_files(root) + check_F_security_headers(root)
        for f in proj_findings:
            fp = f.get("file")
            if exclude_globs and fp and _matches_exclude(root, Path(str(fp)), exclude_globs):
                continue
            if diff_active and diff_set is not None:
                try:
                    if Path(str(fp)).resolve() not in diff_set:
                        continue
                except OSError:
                    continue
            all_findings.append(f)

    # Per-file checks (walk is pruned by diff_set + exclude_globs)
    for path, lines in walk_source_files(
        root, diff_set if diff_active else None, exclude_globs, walk_stats
    ):
        files_scanned += 1
        is_content = _is_content_file(path)
        is_test = _is_test_file(path)

        secret_findings = check_A_secrets(path, lines, root)
        if not is_content:
            secret_findings.extend(check_B_secret_in_logs(path, lines))
        if is_test:
            # Test fixtures intentionally embed trigger strings (fake keys, sample
            # tokens). Keep them visible for human review but don't let them block
            # the HIGH push-gate — a real leaked value still surfaces at LOW.
            for _f in secret_findings:
                _f["severity"] = "LOW"
        all_findings.extend(secret_findings)

        if not is_content:
            if not is_test:
                all_findings.extend(check_C_injection(path, lines))
            all_findings.extend(check_D_ssrf(path, lines))
            all_findings.extend(check_E_rate_limiting(path, lines))
            all_findings.extend(check_G_prompt_injection(path, lines))

    return all_findings, files_scanned, walk_stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic pre-push security scanner (stdlib only, no LLM)."
    )
    parser.add_argument("--path", default=".", help="Repository root to scan (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human report")
    parser.add_argument(
        "--fail-on",
        choices=["low", "medium", "high", "critical"],
        default="high",
        dest="fail_on",
        help="Exit 1 if any finding at or above this severity (default: high)",
    )
    parser.add_argument(
        "--diff",
        default=None,
        metavar="REF",
        help="Restrict the scan to files changed in <ref>..HEAD. Falls back to a "
             "full scan (never scans less than intended) if <ref>/repo is unusable.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="GLOB",
        help="Skip any file whose repo-relative path matches this fnmatch glob "
             "(repeatable; applies in both full and --diff mode).",
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"error: path does not exist: {root}", file=sys.stderr)
        return 2

    threshold = args.fail_on.upper()

    exclude_globs: list[str] = args.exclude or []

    # Diff scoping: opt-in, fail-safe. diff_set None → full scan (either --diff
    # was not passed, or it was passed but the ref/repo was unusable → fallback).
    diff_set: set[Path] | None = None
    diff_active = False
    diff_info: dict[str, Any] | None = None
    if args.diff is not None:
        resolved = _git_diff_files(root, args.diff)
        if resolved is None:  # fail-safe fallback to full scan
            diff_info = {"ref": args.diff, "mode": "fallback-full-scan", "changed_files": 0}
        else:
            diff_set = resolved
            diff_active = True
            diff_info = {"ref": args.diff, "mode": "delta", "changed_files": len(resolved)}

    # Empty diff range → nothing changed → scan nothing (exit 0).
    empty_diff = diff_active and not diff_set

    all_findings, files_scanned, walk_stats = _perform_scan(
        root, diff_set, diff_active, exclude_globs, empty_diff
    )

    # f1 belt-and-braces: --diff named changed files, yet the walk scanned NONE
    # of them. That means the delta named files the walk could not match —
    # misparsed/quoted names, a bad subdir join, or files moved out of the
    # tracked tree. Never exit 0 on a misparse: fall back to a FULL scan instead
    # of silently scanning nothing. (The general guard for the rc==0-with-
    # misparsed-output class, above and beyond the -z/--relative fixes.)
    if (
        diff_active
        and diff_info is not None
        and diff_info.get("changed_files", 0) > 0
        and files_scanned == 0
    ):
        diff_info = {
            "ref": args.diff,
            "mode": "fallback-full-scan",
            "changed_files": diff_info["changed_files"],
            "fallback_reason": "delta named changed files but the walk matched none",
        }
        diff_set = None
        diff_active = False
        all_findings, files_scanned, walk_stats = _perform_scan(
            root, None, False, exclude_globs, False
        )

    # f4: exclude visibility — carry the active globs + removed count into the
    # report so an over-broad glob (`*` matches every path) cannot silently
    # bypass the whole scan unnoticed.
    exclude_info: dict[str, Any] | None = None
    if exclude_globs:
        removed = walk_stats.get("excluded", 0)
        exclude_info = {"globs": exclude_globs, "files_removed": removed}
        bare_wildcard = any(g in ("*", "**") for g in exclude_globs)
        candidates = files_scanned + removed
        over_half = candidates > 0 and removed > candidates / 2
        if bare_wildcard or over_half:
            print(
                f"warning: --exclude removed {removed} of {candidates} candidate "
                f"file(s) from the scan (globs: {', '.join(exclude_globs)}) — "
                "verify this is intended; a bare `*`/`**` bypasses the entire scan.",
                file=sys.stderr,
            )

    # Sort by severity then file:line
    all_findings.sort(
        key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["file"], f["line"])
    )

    scan_path_str = str(root)

    if args.json:
        print(format_json_output(all_findings, threshold, scan_path_str, files_scanned, diff_info, exclude_info))
    else:
        print(format_report(all_findings, threshold, scan_path_str, files_scanned, diff_info, exclude_info))

    # Exit code
    threshold_idx = SEVERITY_ORDER.get(threshold, 1)
    for f in all_findings:
        if SEVERITY_ORDER.get(f["severity"], 9) <= threshold_idx:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
