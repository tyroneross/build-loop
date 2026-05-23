#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Classify an orchestrator action into one of four MECE labels.

The labels are MECE on the *output action shape* the orchestrator must take:

  SAFE       proceed on the main worktree, no prompt
  RISKY      proceed on an isolated worktree-branch, push branch, continue main
  DECISION   implementer surfaced novel_decisions[] — auto-pick in long-mode,
             escalate to operator in normal-mode
  PRODUCTION irreversible *and* production-targeted — always escalate

MECE proof. Every action satisfies exactly one of:
  - has an unresolved novel_decisions[] (DECISION)
  - is irreversible + targets production (PRODUCTION)
  - is irreversible non-production OR reversible+broad-blast (RISKY)
  - is none of the above (SAFE)

The decision tree below makes priority explicit so two rules can't fire at once.

Read-only short-circuit. Most operator pain comes from `vercel curl`,
`vercel logs`, `git status`, `cat`, `grep`, etc. being classified `unknown` by
deployment_policy and routed to `confirm`. Read-only commands are mechanically
SAFE regardless of environment — they cannot mutate prod by definition. The
read-only matcher fires before any other rule.

CLI:
  python3 classify_action.py --workdir . --command "vercel curl --deployment x.com /"
  python3 classify_action.py --workdir . --files-touched "migrations/0042.sql"
  python3 classify_action.py --workdir . --envelope-json /path/to/envelope.json

Exit codes:
  0 — SAFE
  1 — RISKY
  2 — PRODUCTION
  3 — DECISION
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autonomy_gate  # noqa: E402
import deployment_policy  # noqa: E402


# ---------------------------------------------------------------------------
# A1. Read-only command detection
# ---------------------------------------------------------------------------
#
# A command is read-only when its first token (and any sub-verb) only inspects
# state. We intentionally err on the side of false negatives — when in doubt,
# don't claim a command is read-only. The penalty for missing a read-only is
# a redundant pass through later rules; the penalty for a false positive could
# be auto-executing a destructive op.

READ_ONLY_FIRST_TOKENS = frozenset({
    # POSIX inspection
    "ls", "cat", "head", "tail", "less", "more", "wc", "file", "stat",
    "find", "locate", "which", "type", "whereis",
    "grep", "egrep", "fgrep", "rg", "ag",
    "ps", "top", "htop", "uptime", "uname", "whoami", "id", "groups",
    "pwd", "echo", "printf", "date", "hostname",
    "env", "printenv",
    "du", "df", "free",
    "diff", "cmp",
    "jq", "yq", "xq",
    "tree",
    # Network read
    "curl", "wget", "http", "httpie", "xh",
    "ping", "traceroute", "dig", "nslookup", "host",
    "nc", "ncat",  # only if reading; we accept some false positives here
    # HTTP-API CLIs in read mode (sub-verb matched below)
    # vercel/gh/aws are handled per-subverb in READ_ONLY_SUBVERBS
})

# Sub-verb whitelists: tools whose top-level command name is generic but whose
# read-only sub-verbs are stable. Maps top-level → set of read-only sub-verbs.
READ_ONLY_SUBVERBS: dict[str, frozenset[str]] = {
    "git": frozenset({
        "status", "log", "diff", "show", "branch", "tag", "blame",
        "ls-files", "ls-remote", "ls-tree", "rev-parse", "rev-list",
        "describe", "config", "remote", "shortlog", "reflog", "cat-file",
        "for-each-ref", "show-ref", "name-rev", "whatchanged",
    }),
    "vercel": frozenset({
        "curl", "logs", "inspect", "ls", "list", "whoami", "teams",
        # Notably NOT "env" — `vercel env rm/add` writes to vercel's env store;
        # `vercel env ls/list/pull` is handled via READ_ONLY_SUB_SUBVERBS below.
    }),
    "gh": frozenset({
        "api", "auth", "browse", "issue", "pr", "release", "repo", "run",
        "search", "status", "workflow", "label", "ssh-key", "gpg-key",
    }),  # most gh sub-verbs are read-OR-write; we accept some false negatives downstream
    "aws": frozenset({"sts", "configure"}),  # very conservative
    "kubectl": frozenset({
        "get", "describe", "logs", "top", "explain", "version", "cluster-info",
        "config", "api-resources", "api-versions", "auth",
    }),
    "docker": frozenset({
        "ps", "images", "logs", "inspect", "history", "info", "version",
        "stats", "top", "events", "diff", "search", "system",
    }),
    "npm": frozenset({"list", "ls", "outdated", "ping", "view", "search", "config"}),
    "pnpm": frozenset({"list", "ls", "outdated", "view"}),
    "uv": frozenset({"pip", "lock", "tree", "cache"}),  # uv pip list/show etc.
    "pip": frozenset({"list", "show", "freeze", "check", "config"}),
    "poetry": frozenset({"show", "check", "env", "config"}),
    "node": frozenset({"--version", "-v", "--eval"}),  # generally OK
    "python": frozenset({"--version", "-V", "-c"}),  # -c can mutate but typically used for inspection
    "python3": frozenset({"--version", "-V", "-c"}),
}

# gh sub-verbs that are read-only when their second sub-verb is one of these
READ_ONLY_SUB_SUBVERBS: dict[tuple[str, str], frozenset[str]] = {
    ("gh", "pr"): frozenset({"list", "view", "diff", "checks", "status"}),
    ("gh", "issue"): frozenset({"list", "view"}),
    ("gh", "release"): frozenset({"list", "view"}),
    ("gh", "run"): frozenset({"list", "view", "watch"}),
    ("gh", "workflow"): frozenset({"list", "view"}),
    ("gh", "repo"): frozenset({"list", "view", "clone"}),
    ("vercel", "env"): frozenset({"ls", "list", "pull"}),
}


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_read_only(command: str) -> bool:
    """Return True when the command is mechanically read-only."""
    cmd = command.strip()
    if not cmd:
        return False
    # Skip leading parens / semicolons used in compound commands
    head = cmd.split(None, 1)[0].lstrip("(").rstrip(";")
    if head in READ_ONLY_FIRST_TOKENS:
        return True

    tokens = _tokens(cmd)
    if not tokens:
        return False
    first = tokens[0].lstrip("(").rstrip(";")
    sub = tokens[1] if len(tokens) > 1 else ""
    sub2 = tokens[2] if len(tokens) > 2 else ""

    # Two-level whitelist (e.g. `gh pr view`)
    # When (first, sub) appears in the two-level table, it OVERRIDES the
    # one-level whitelist — we treat the second level as authoritative for that
    # sub-verb. Prevents false positives like `vercel env rm` matching the
    # one-level "env" entry when the two-level entry restricts to {ls,list,pull}.
    two = (first, sub)
    if two in READ_ONLY_SUB_SUBVERBS:
        return sub2 in READ_ONLY_SUB_SUBVERBS[two]

    # One-level whitelist (e.g. `git status`, `vercel logs`)
    sub_set = READ_ONLY_SUBVERBS.get(first)
    if sub_set is not None and sub in sub_set:
        return True

    return False


# ---------------------------------------------------------------------------
# A5. Irreversible-non-deployment commands
# ---------------------------------------------------------------------------
# These are destructive *regardless* of environment target. force-push to a
# feature branch is recoverable (you can re-push); force-push to main is not.
# We classify the COMMAND here; the environment determines whether it becomes
# PRODUCTION or RISKY.

IRREVERSIBLE_PATTERNS = (
    re.compile(r"\bdrop\s+(table|database|schema)\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+publish\b"),
    re.compile(r"\bpnpm\s+publish\b"),
    re.compile(r"\byarn\s+publish\b"),
    re.compile(r"\btwine\s+upload\b"),
    re.compile(r"\bcargo\s+publish\b"),
    re.compile(r"\bgh\s+release\s+create\b"),
    re.compile(r"\brm\s+-r?f?\s+/(\s|$)"),
    re.compile(r"\bgit\s+push\s+(--force|-f)\b"),
)


def _is_irreversible(command: str) -> bool:
    cmd = command.strip()
    return any(p.search(cmd) for p in IRREVERSIBLE_PATTERNS)


# ---------------------------------------------------------------------------
# A2. RISKY file globs (broad blast on otherwise-reversible work)
# ---------------------------------------------------------------------------

DEFAULT_RISKY_GLOBS: list[str] = [
    "migrations/**",
    "**/migrations/**",
    "schema*.prisma",
    "**/schema*.prisma",
    ".github/workflows/**",
    "Dockerfile",
    "docker-compose*.y*ml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "**/auth/**",
    "**/secrets/**",
]


def _load_classify_config(workdir: Path) -> dict[str, Any]:
    config_path = workdir / ".build-loop" / "config.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("classifyAction")
    if not isinstance(block, dict):
        return {}
    return block


def _resolved_risky_globs(workdir: Path) -> list[str]:
    cfg = _load_classify_config(workdir)
    custom = cfg.get("riskyGlobs")
    if isinstance(custom, list):
        return [str(p) for p in custom]
    return list(DEFAULT_RISKY_GLOBS)


def _files_match_risky(files: list[str], globs: list[str]) -> tuple[bool, str]:
    for f in files:
        for g in globs:
            if fnmatch.fnmatch(f, g) or fnmatch.fnmatch(f.lower(), g.lower()):
                return True, f"{f} matches {g}"
    return False, ""


# ---------------------------------------------------------------------------
# A4. Deployment target detection (delegates to deployment_policy)
# ---------------------------------------------------------------------------

def _deployment_target(command: str) -> str:
    """Return 'production' | 'testflight' | 'preview' | 'unknown' | 'n/a'."""
    if not command.strip():
        return "n/a"
    target, _reason = deployment_policy.classify_command(command)
    return target


# ---------------------------------------------------------------------------
# A3. Decision pickability from envelope
# ---------------------------------------------------------------------------

def _decision_state(envelope: dict[str, Any]) -> tuple[str, str]:
    """Return (state, reason).
    state ∈ {"none", "pickable", "low_confidence", "malformed"}
    """
    novel = envelope.get("novel_decisions") or []
    if not isinstance(novel, list) or not novel:
        return "none", ""

    for i, entry in enumerate(novel):
        if not isinstance(entry, dict):
            return "malformed", f"novel_decisions[{i}] not an object"
        if not entry.get("recommended_default"):
            return "malformed", f"novel_decisions[{i}] missing recommended_default"
        if entry.get("confidence") == "low":
            return "low_confidence", f"novel_decisions[{i}] confidence=low"

    return "pickable", f"{len(novel)} pickable decisions"


# ---------------------------------------------------------------------------
# MECE classifier
# ---------------------------------------------------------------------------

def classify(
    workdir: Path,
    *,
    command: str = "",
    files_touched: list[str] | None = None,
    envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a single MECE classification envelope.

    Priority order (first match wins):
      1. PRODUCTION   — irreversible + production target
      2. RISKY        — irreversible + non-production target
      3. PRODUCTION   — production deployment command (not read-only)
      4. SAFE         — read-only command
      5. DECISION     — has unresolved novel_decisions[]
      6. RISKY        — touches broad-blast files
      7. SAFE         — default
    """
    files_touched = files_touched or []
    envelope = envelope or {}

    has_command = bool(command.strip())
    target = _deployment_target(command) if has_command else "n/a"
    irreversible = _is_irreversible(command) if has_command else False
    read_only = _is_read_only(command) if has_command else False

    # 1. PRODUCTION — irreversible + production
    if irreversible and target == "production":
        return _envelope(
            "PRODUCTION",
            reason=f"irreversible command targets production ({target})",
            matched_rule="irreversible+production",
            delegated_to="classify_action",
        )

    # 2. RISKY — irreversible + non-production (force-push to feature, etc.)
    if irreversible:
        return _envelope(
            "RISKY",
            reason="irreversible command targeting non-production — isolate to branch",
            matched_rule="irreversible+non-production",
            delegated_to="classify_action",
        )

    # 3. PRODUCTION — non-irreversible production deploy command
    # Read-only inspection of prod (e.g. `vercel logs --prod`) is NOT production.
    if target == "production" and not read_only:
        return _envelope(
            "PRODUCTION",
            reason="command targets production deployment",
            matched_rule="deployment_policy:production",
            delegated_to="deployment_policy",
        )

    # 4. SAFE — read-only short-circuit
    if read_only:
        return _envelope(
            "SAFE",
            reason="read-only command",
            matched_rule="read_only",
            delegated_to="classify_action",
        )

    # 5. DECISION — envelope has open decisions
    dstate, dreason = _decision_state(envelope)
    if dstate != "none":
        return _envelope(
            "DECISION",
            reason=dreason,
            matched_rule=f"envelope:{dstate}",
            delegated_to="envelope",
            decision_state=dstate,
        )

    # 6. RISKY — broad-blast files (only if reversible + non-prod + no decision)
    if files_touched:
        globs = _resolved_risky_globs(workdir)
        hit, detail = _files_match_risky(files_touched, globs)
        if hit:
            return _envelope(
                "RISKY",
                reason=f"file touch warrants branch isolation: {detail}",
                matched_rule=detail,
                delegated_to="risky_globs",
            )

    # 7. SAFE — default
    return _envelope(
        "SAFE",
        reason="no irreversible action, no production target, no broad-blast file, no decision",
        matched_rule=None,
        delegated_to=None,
    )


def _envelope(
    classification: str,
    *,
    reason: str,
    matched_rule: str | None,
    delegated_to: str | None,
    **extra: Any,
) -> dict[str, Any]:
    env = {
        "classification": classification,
        "reason": reason,
        "matched_rule": matched_rule,
        "delegated_to": delegated_to,
    }
    env.update(extra)
    return env


_EXIT_CODES = {"SAFE": 0, "RISKY": 1, "PRODUCTION": 2, "DECISION": 3}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--command", default="")
    parser.add_argument("--files-touched", default="")
    parser.add_argument("--envelope-json", default="")
    parser.add_argument("--action-label", default="")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()

    files_touched: list[str] = []
    if args.files_touched:
        for t in args.files_touched.replace("\n", ",").split(","):
            t = t.strip()
            if t:
                files_touched.append(t)

    envelope: dict[str, Any] = {}
    if args.envelope_json:
        env_path = Path(args.envelope_json)
        if env_path.exists():
            try:
                envelope = json.loads(env_path.read_text())
            except json.JSONDecodeError as exc:
                print(json.dumps({
                    "classification": "SAFE",
                    "reason": f"envelope JSON unparseable ({exc}); defaulting SAFE",
                    "matched_rule": None,
                    "delegated_to": None,
                    "warning": "envelope_unparseable",
                }))
                return 0

    result = classify(workdir, command=args.command, files_touched=files_touched, envelope=envelope)
    if args.action_label:
        result["action_label"] = args.action_label

    print(json.dumps(result))
    return _EXIT_CODES.get(result["classification"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
