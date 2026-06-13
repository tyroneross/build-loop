#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""hook_hygiene_lint.py — deterministic hook reliability lint for build-loop.

Operationalizes the rules from
``skills/plugin-builder/references/plugin-hygiene-lessons.md`` §17
and ``skills/plugin-builder/references/hooks-reference.md`` "Reliability:
minimal PATH, fail-open, advisory-only" into an enforceable WARN-level check.

Hooks fire in a subprocess with a minimal PATH (``/usr/bin:/bin``). Bare
binaries outside that set fail with exit 127. Under ``set -e`` / ``set -euo
pipefail`` an unguarded binary in a command substitution aborts the whole
script before any later guard runs. Advisory hooks that emit ``deny`` /
``block`` violate the facilitator/never-block charter.

CLI shape::

    python3 scripts/hook_hygiene_lint.py --hooks <path-to-hooks.json|settings.json> [--json]
    python3 scripts/hook_hygiene_lint.py --self-test

Stdlib only: ``re``, ``json``, ``argparse``, ``pathlib``, ``sys``.
Style mirrors ``scripts/attestation_lint.py`` (envelope shape, exit codes,
``--self-test`` mode).

Exit codes:
    0  no findings
    1  one or more findings emitted (WARN-level — advisory; build-loop's
       "judges route, never stop" rule means callers decide how to weight)
    2  runner error (file not found, JSON parse error, malformed structure)

Findings are emitted as a list of records, each with:

    {
      "rule_id": "HH001" | "HH002" | "HH003" | "HH004",
      "severity": "warn",
      "event": "SessionStart" | "PreToolUse" | ...,
      "matcher": str | null,
      "command": str (truncated to ~200 chars),
      "script_path": str | null,   # resolved referenced script, when rule reads it
      "message": str,
      "evidence": {"binary": str | null, "line": int | null, "snippet": str | null}
    }

Rules (all severity=warn):

    HH001 — bare binary not in minimal-PATH-safe set
            Safe set: {python3, bash, sh, git, jq, env, ...}. Bare invocation
            of anything outside that set (e.g. ``node``, ``rally``, ``rg``,
            ``npx``, ``terminal-notifier``) must be absolute-pathed,
            ``command -v``-guarded, or wrapped in ``[ -x ... ]``.
    HH002 — set -e + unguarded external binary in command substitution
            Read referenced shell scripts; flag the combination of strict mode
            (``set -e`` / ``set -euo pipefail``) with a bare risky binary inside
            ``$(...)`` or backticks AFTER the strict-mode line.
    HH003 — advisory hook without explicit fail-open path
            Inline commands with a risky bare token and no ``exit 0`` / ``|| true``
            tail. Pure ``bash ${CLAUDE_PLUGIN_ROOT}/…``-absolute invocations of
            in-repo scripts do not trigger (no risky token).
    HH004 — advisory hook emits permissionDecision=deny or decision=block
            Without an explicit opt-in/safety marker (``BL_SAFETY_GATE`` env
            check, or a comment naming "safety gate" / "security gate" /
            "integrity gate").
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Binaries / shell keywords reliably present under /usr/bin:/bin OR built into
# every POSIX shell. Adding to this set lowers HH001 coverage; do so only with
# evidence that the binary is universally present.
MINIMAL_PATH_SAFE = frozenset({
    # Real binaries always present under /usr/bin:/bin on macOS + Linux.
    "python3", "bash", "sh", "git", "jq", "env",
    "printf", "echo", "cat", "test", "rm", "mv", "cp", "ln", "mkdir",
    "grep", "sed", "awk", "cut", "tr", "sort", "head", "tail", "wc",
    "ls", "pwd", "date", "sleep", "kill", "nohup", "xargs", "tee",
    "id", "whoami", "hostname",
    # Shell builtins / keywords — captured by BARE_TOKEN_RE but not bins.
    "true", "false", "exit", "cd", "set", "if", "then", "fi", "else",
    "elif", "for", "do", "done", "while", "case", "esac", "return",
    "export", "local", "readonly", "unset", "shift", "eval", "exec",
    "trap", "source", "wait", "read",
})

# Risky binaries we explicitly flag when seen bare. Not exhaustive — anything
# not in MINIMAL_PATH_SAFE is treated as risky — but this list lets HH001's
# message name the offender precisely when the offender is a known one.
KNOWN_RISKY = frozenset({
    "node", "rally", "terminal-notifier", "rg", "npx",
    "deno", "bun", "pnpm", "yarn", "claude", "codex",
    "ollama", "uv", "pip", "pipx", "poetry", "go", "cargo",
    "brew", "docker", "kubectl",
})

# Tokens that exempt a deny/block emission from HH004 — the operator opted in.
SAFETY_GATE_MARKERS = (
    "BL_SAFETY_GATE",
    "safety gate", "safety-gate",
    "security gate", "security-gate",
    "integrity gate", "integrity-gate",
    "explicit safety", "explicit-safety",
    "explicit security", "explicit-security",
    "opt-in safety", "opt-in-safety",
)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

# A leading token that looks like a bare command (alphanumeric + dash +
# underscore, no slash, no ${...}, no $-var). Sample at the start of the
# string AND after common command separators (;, &&, ||, |, newline) AND inside
# command substitutions ($(...) and `...`).
BARE_TOKEN_RE = re.compile(
    r"""
    (?:^|[;&|\n]|\$\(|`)     # start, separator, or command-substitution opener
    \s*                       # optional whitespace
    ([a-zA-Z_][a-zA-Z0-9_-]*) # bare token (NO slash, NO ${, NO $)
    \b
    """,
    re.VERBOSE,
)

# Strip these constructs BEFORE running BARE_TOKEN_RE — they reference a binary
# by name without invoking it, so they shouldn't count as bare invocations.
GUARD_PATTERNS = [
    re.compile(r"command\s+-v\s+\S+"),
    re.compile(r"hash\s+\S+"),
    re.compile(r"type\s+\S+"),
    re.compile(r"\[\s*-[xfre]\s+[^\]]+\]"),
    re.compile(r"which\s+\S+"),
]

# Extract guarded binary names so we can skip them in the bare-token scan.
GUARDED_NAME_RES = [
    re.compile(r"command\s+-v\s+([a-zA-Z_][a-zA-Z0-9_-]*)"),
    re.compile(r"\[\s*-[xfre]\s+[^\]]*?([a-zA-Z_][a-zA-Z0-9_.-]*?)\s*\]"),
    re.compile(r"hash\s+([a-zA-Z_][a-zA-Z0-9_-]*)"),
    re.compile(r"type\s+([a-zA-Z_][a-zA-Z0-9_-]*)"),
    re.compile(r"which\s+([a-zA-Z_][a-zA-Z0-9_-]*)"),
]

# A command-substitution `$(...)` or backtick — extracted whole so HH002 can
# look at WHAT'S INSIDE. The inner group accepts one level of nested parens
# (e.g. `node -e 'process.stdout.write("y")'`) before bailing — bash supports
# arbitrary nesting but two levels covers ~all hook scripts in practice.
CMD_SUBST_RE = re.compile(
    r"\$\(((?:[^()]|\([^()]*\))*)\)|`([^`]*)`"
)

# Detect strict mode.
SET_E_RE = re.compile(r"\bset\s+-[a-z]*e[a-z]*o?\b")

# Detect denial / block emissions.
DENY_RE = re.compile(
    r"""permissionDecision\s*['\"]?\s*:\s*['\"]?\s*deny""",
    re.IGNORECASE,
)
BLOCK_RE = re.compile(
    r"""\bdecision\s*['\"]?\s*:\s*['\"]?\s*block""",
    re.IGNORECASE,
)
EXIT_2_RE = re.compile(r"\bexit\s+2\b")

# Detect an explicit fail-open tail anywhere in the command string.
FAILOPEN_TAIL_RE = re.compile(
    r"""(?:;\s*exit\s+0\b
       | \|\|\s*true\b
       | \|\|\s*exit\s+0\b
       | (?:^|\n)\s*exit\s+0\s*$
       | printf\s+['"]\{\}['"]
       )""",
    re.VERBOSE,
)


def _truncate(s: str, n: int = 200) -> str:
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 1] + "…"


def _collect_guarded_names(cmd: str) -> set[str]:
    out: set[str] = set()
    for pat in GUARDED_NAME_RES:
        for m in pat.finditer(cmd):
            token = m.group(1)
            if token:
                # `[ -x "$root/foo" ]` → captures "foo".
                out.add(token.lstrip("$").rstrip("/").split("/")[-1])
    return out


# Strip single-quoted and double-quoted string literals — tokens inside a
# string literal are data, not invocations. Use non-greedy and handle escapes.
QUOTED_SQ_RE = re.compile(r"'(?:\\.|[^'\\])*'")
QUOTED_DQ_RE = re.compile(r'"(?:\\.|[^"\\])*"')


def _strip_quoted_literals(cmd: str) -> str:
    """Replace single- and double-quoted string literals with whitespace of the
    same length. Preserves offsets and keeps separators between tokens, so
    regex matches outside the quotes still work. Command substitutions `$(...)`
    and backticks survive because they're shell constructs, not literals."""
    def _blank(m: re.Match) -> str:
        return " " * len(m.group(0))
    out = QUOTED_SQ_RE.sub(_blank, cmd)
    out = QUOTED_DQ_RE.sub(_blank, out)
    return out


def _strip_shell_comment(line: str) -> str:
    """Blank an unquoted ``#`` comment (to end of line) with same-length spaces.

    A ``#`` that begins a word (start of line or after whitespace) starts a
    shell comment; everything after it is documentation, not live code. We
    blank quoted literals first so a ``#`` inside a string (e.g. a regex or a
    URL fragment) is not mistaken for a comment. Offsets are preserved so the
    caller can keep using line/column positions.

    Without this, a hook that *documents* avoiding ``set -e`` in a comment
    (``# no bare `set -e```) trips HH002's strict-mode detector — a false
    positive against the very hooks that follow the rule.
    """
    scrubbed = _strip_quoted_literals(line)
    # First unquoted `#` at start-of-line or following whitespace.
    m = re.search(r"(?:^|\s)#", scrubbed)
    if m is None:
        return line
    cut = m.start() + (0 if m.group(0).startswith("#") else 1)
    return line[:cut] + " " * (len(line) - cut)


def _strip_guards(cmd: str) -> str:
    # Strip quoted literals FIRST so guard regexes don't match inside strings.
    out = _strip_quoted_literals(cmd)
    for pat in GUARD_PATTERNS:
        out = pat.sub("  ", out)
    return out


def _find_bare_risky(cmd: str) -> list[str]:
    """Return risky bare-binary tokens found in `cmd`, order-preserving.

    Risky = (in KNOWN_RISKY) OR (not in MINIMAL_PATH_SAFE and not a path/var).
    """
    guarded = _collect_guarded_names(cmd)
    scrubbed = _strip_guards(cmd)
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in BARE_TOKEN_RE.finditer(scrubbed):
        token = m.group(1)
        if not token or token in seen_set:
            continue
        if token in MINIMAL_PATH_SAFE:
            continue
        if token in guarded:
            continue
        # Skip env-var assignments: `FOO=bar somecmd` — the BARE_TOKEN_RE
        # captures FOO; rule out by checking the next char.
        end = m.end()
        if end < len(scrubbed) and scrubbed[end : end + 1] == "=":
            continue
        if token in KNOWN_RISKY:
            seen.append(token)
            seen_set.add(token)
            continue
        # Conservative: skip ALL-CAPS tokens — almost always an env var
        # reference (e.g. `PATH`, `HOME`).
        if token.isupper() and len(token) > 1:
            continue
        seen.append(token)
        seen_set.add(token)
    return seen


def _has_failopen_tail(cmd: str) -> bool:
    return FAILOPEN_TAIL_RE.search(cmd) is not None


def _has_safety_marker(cmd: str) -> bool:
    low = cmd.lower()
    return any(marker.lower() in low for marker in SAFETY_GATE_MARKERS)


def _has_safe_anchor(cmd: str) -> bool:
    """A command is 'safe-anchored' iff it has zero risky bare tokens —
    everything is in the safe set, absolute-pathed, ${VAR}-prefixed, or
    explicitly guarded."""
    return _find_bare_risky(cmd) == []


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def rule_hh001(cmd: str, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Bare binary not in minimal-PATH-safe set."""
    out: list[dict[str, Any]] = []
    for bin_name in _find_bare_risky(cmd):
        out.append({
            "rule_id": "HH001",
            "severity": "warn",
            "event": ctx["event"],
            "matcher": ctx.get("matcher"),
            "command": _truncate(cmd),
            "script_path": None,
            "message": (
                f"Bare invocation of `{bin_name}` — hooks run under a minimal "
                f"PATH (/usr/bin:/bin) and binaries outside it exit 127. "
                f"Use an absolute path, `${{CLAUDE_PLUGIN_ROOT}}/…`, or guard "
                f"with `command -v {bin_name} >/dev/null 2>&1 || exit 0`."
            ),
            "evidence": {"binary": bin_name, "line": None, "snippet": None},
        })
    return out


def rule_hh002(cmd: str, ctx: dict[str, Any],
                script_path: Path | None) -> list[dict[str, Any]]:
    """set -e + unguarded external binary in command substitution.

    Inline + referenced-script. Finding fires per substitution; one risky
    binary per substitution is sufficient.
    """
    findings: list[dict[str, Any]] = []

    def _check(text: str, source_path: str | None) -> None:
        # Reason over comment-stripped lines: a `set -e` / risky binary that
        # appears only inside a `#` comment is documentation, not live code,
        # and must not trip the strict-mode detector.
        lines = [_strip_shell_comment(ln) for ln in text.splitlines()]
        if not any(SET_E_RE.search(ln) for ln in lines):
            return
        set_e_line: int | None = None
        for i, line in enumerate(lines, start=1):
            if SET_E_RE.search(line):
                set_e_line = i
                break
        if set_e_line is None:
            return
        # Pre-compute the set of binaries guarded earlier in the script (by a
        # `command -v BIN` / `[ -x ... BIN ]` / `hash BIN` / `type BIN` earlier
        # in source order). This catches the idiomatic guarded form
        # `if command -v node; then meta=$(node ...); fi` where the strict
        # guard is on a different line than the substitution — the §17 lesson's
        # "guard BEFORE the substitution" rule is satisfied.
        for i, line in enumerate(lines, start=1):
            if i < set_e_line:
                continue
            guarded_so_far: set[str] = set()
            for prev in lines[:i]:
                guarded_so_far.update(_collect_guarded_names(prev))
            for m in CMD_SUBST_RE.finditer(line):
                inner = m.group(1) or m.group(2) or ""
                risky = _find_bare_risky(inner)
                if not risky:
                    continue
                # Skip if every risky token is already guarded earlier.
                if all(r in guarded_so_far for r in risky):
                    continue
                findings.append({
                    "rule_id": "HH002",
                    "severity": "warn",
                    "event": ctx["event"],
                    "matcher": ctx.get("matcher"),
                    "command": _truncate(text),
                    "script_path": source_path,
                    "message": (
                        f"`set -e`/`set -euo pipefail` combined with unguarded "
                        f"`{risky[0]}` in a command substitution — when the "
                        f"binary is missing from the hook's minimal PATH, the "
                        f"substitution returns 127 and `set -e` aborts the "
                        f"whole script before any later `|| true` runs. Guard "
                        f"the binary (`command -v {risky[0]} >/dev/null || "
                        f"exit 0`) BEFORE the substitution."
                    ),
                    "evidence": {
                        "binary": risky[0],
                        "line": i,
                        "snippet": line.strip()[:200],
                    },
                })

    _check(cmd, None)
    if script_path and script_path.is_file():
        try:
            text = script_path.read_text(encoding="utf-8", errors="replace")
            _check(text, str(script_path))
        except OSError:
            pass
    return findings


def rule_hh003(cmd: str, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Advisory hook command with no `exit 0` / `|| true` fail-open path.

    Only fires when the command has at least one risky bare token (otherwise
    the command is already safe-anchored or pure-stdlib). Safety markers
    exempt — they're explicit enforcers.
    """
    if _has_safety_marker(cmd):
        return []
    if _has_safe_anchor(cmd):
        return []
    if _has_failopen_tail(cmd):
        return []
    return [{
        "rule_id": "HH003",
        "severity": "warn",
        "event": ctx["event"],
        "matcher": ctx.get("matcher"),
        "command": _truncate(cmd),
        "script_path": None,
        "message": (
            "Hook command references an external binary without a fail-open "
            "tail — append `; exit 0` or `|| true` (or wrap in "
            "`[ -x BIN ] && BIN args; exit 0`) so the hook never enforces by "
            "accident when the binary is missing or slow."
        ),
        "evidence": {"binary": None, "line": None, "snippet": None},
    }]


def rule_hh004(cmd: str, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Advisory hook emits deny / block / exit 2 without a safety marker."""
    enforces = (
        DENY_RE.search(cmd) is not None
        or BLOCK_RE.search(cmd) is not None
        or EXIT_2_RE.search(cmd) is not None
    )
    if not enforces:
        return []
    if _has_safety_marker(cmd):
        return []
    return [{
        "rule_id": "HH004",
        "severity": "warn",
        "event": ctx["event"],
        "matcher": ctx.get("matcher"),
        "command": _truncate(cmd),
        "script_path": None,
        "message": (
            "Advisory hook emits `permissionDecision:\"deny\"` / "
            "`decision:\"block\"` / `exit 2` without an explicit safety-gate "
            "marker — advisory/coordination hooks should never enforce by "
            "default. If enforcement is intentional, mark it with a "
            "`safety gate` / `security gate` / `BL_SAFETY_GATE=1` opt-in."
        ),
        "evidence": {"binary": None, "line": None, "snippet": None},
    }]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _walk_hooks(payload: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    """Walk a hooks.json / settings.json payload; yield (event, matcher, cmd)."""
    out: list[tuple[str, str | None, str]] = []
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            for h in group.get("hooks", []) or []:
                if not isinstance(h, dict):
                    continue
                cmd = h.get("command")
                if isinstance(cmd, str) and cmd.strip():
                    out.append((event, matcher, cmd))
    return out


# Detect a referenced bash script. Used by HH002 to read the script and check
# the strict-mode + unguarded-subst pattern there.
SCRIPT_REF_RE = re.compile(
    r"""bash\s+["']?
        (?:\$\{CLAUDE_PLUGIN_ROOT(?::-\$CLAUDE_PROJECT_DIR)?\})?
        (/?[\w./@-]+\.sh)
        ["']?""",
    re.VERBOSE,
)


def _referenced_script(cmd: str, hooks_dir: Path) -> Path | None:
    """Best-effort: resolve a `bash "${CLAUDE_PLUGIN_ROOT}/foo/bar.sh"` reference
    to a real path under `hooks_dir`'s repo root. Returns None when nothing
    plausible is found."""
    m = SCRIPT_REF_RE.search(cmd)
    if not m:
        return None
    raw = m.group(1)
    # If the captured path is preceded by `${CLAUDE_PLUGIN_ROOT}` (or a similar
    # `${VAR}/...` prefix the regex strips), it's repo-relative — strip the
    # leading slash and join with the repo root. Otherwise treat as absolute.
    pre = cmd[: m.start(1)]
    repo_root = hooks_dir.parent if hooks_dir.name == "hooks" else hooks_dir
    if "${" in pre and pre.rstrip().endswith("}"):
        candidate = (repo_root / raw.lstrip("/")).resolve()
    elif raw.startswith("/"):
        candidate = Path(raw).resolve()
    else:
        candidate = (repo_root / raw).resolve()
    if candidate.is_file():
        return candidate
    return None


def lint_payload(payload: dict[str, Any],
                  hooks_dir: Path | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for event, matcher, cmd in _walk_hooks(payload):
        ctx = {"event": event, "matcher": matcher}
        script_path = _referenced_script(cmd, hooks_dir) if hooks_dir else None
        findings.extend(rule_hh001(cmd, ctx))
        findings.extend(rule_hh002(cmd, ctx, script_path))
        findings.extend(rule_hh003(cmd, ctx))
        findings.extend(rule_hh004(cmd, ctx))
    return findings


def summarize(findings: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": len(findings)}
    for f in findings:
        rid = f["rule_id"]
        summary[rid] = summary.get(rid, 0) + 1
    return summary


def determine_exit(findings: list[dict[str, Any]]) -> int:
    return 1 if findings else 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

SELF_TEST_BAD = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": "node --version"},
                    {"type": "command",
                     "command": 'printf \'{"permissionDecision":"deny"}\''},
                ],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [
                    {"type": "command", "command": "rally announce stop"},
                ],
            }
        ],
    }
}

SELF_TEST_GOOD = {
    "hooks": {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {"type": "command",
                     "command": ('bash "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}'
                                 '/hooks/session-start.sh" </dev/null '
                                 '>/dev/null 2>&1; exit 0')},
                ],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [
                    {"type": "command",
                     "command": ('root="${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}"; '
                                 'hook="$root/scripts/hooks/stop.sh"; '
                                 'if [ -x "$hook" ]; then "$hook"; '
                                 'else printf \'{}\'; fi')},
                ],
            }
        ],
    }
}


def run_self_test() -> int:
    failures: list[str] = []

    findings = lint_payload(SELF_TEST_BAD)
    by_rule = {f["rule_id"] for f in findings}
    for required in ("HH001", "HH003", "HH004"):
        if required not in by_rule:
            failures.append(
                f"SELF_TEST_BAD: expected {required} in findings, "
                f"got {sorted(by_rule)}"
            )

    findings = lint_payload(SELF_TEST_GOOD)
    if findings:
        failures.append(
            f"SELF_TEST_GOOD: expected zero findings, got "
            f"{[f['rule_id'] for f in findings]}"
        )

    if failures:
        print("hook_hygiene_lint self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("hook_hygiene_lint self-test PASS")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Lint a hooks.json (or settings.json) for minimal-PATH / fail-open "
            "/ advisory-only violations. WARN-level — exit 1 on any finding."
        ),
    )
    p.add_argument("--hooks", help="Path to hooks.json or settings.json.")
    p.add_argument("--json", "--quiet", dest="quiet", action="store_true",
                   help="Emit JSON only; suppress human summary on stdout.")
    p.add_argument("--self-test", action="store_true",
                   help="Run the inline self-test and exit.")
    args = p.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.hooks:
        p.error("--hooks is required (or use --self-test)")
        return 2

    hooks_path = Path(args.hooks).expanduser()
    try:
        payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"hook-hygiene-lint: failed to load {hooks_path}: {e}",
              file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("hook-hygiene-lint: input must be a JSON object", file=sys.stderr)
        return 2

    try:
        findings = lint_payload(payload, hooks_dir=hooks_path.parent)
    except Exception as e:  # noqa: BLE001 — runner error -> exit 2
        print(f"hook-hygiene-lint: error: {e}", file=sys.stderr)
        return 2

    summary = summarize(findings)
    exit_code = determine_exit(findings)

    output = {
        "hooks": str(hooks_path),
        "summary": summary,
        "findings": findings,
        "exit_code": exit_code,
    }
    print(json.dumps(output, indent=2))

    if not args.quiet and exit_code != 0:
        parts = ", ".join(f"{k}={v}" for k, v in summary.items() if k != "total")
        print(f"hook-hygiene-lint: {summary['total']} finding(s) — {parts}",
              file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
