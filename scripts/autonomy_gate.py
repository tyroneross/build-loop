#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Classify arbitrary build-loop actions against a generalized autonomy policy.

Precedence (highest to lowest):
  1. deployment_policy.py  — push/deploy/release commands route there first
  2. Repo blockFor         — hard-block patterns from .build-loop/config.json
  3. Repo confirmFor       — require-confirm patterns from .build-loop/config.json
  4. Repo warnFor          — warn-only patterns from .build-loop/config.json (NEW)
  5. Default confirmFor    — 7 built-in confirm patterns (see DEFAULT_CONFIRM_FOR)
  6. Default warnFor       — empty by default (NEW)
  7. Default blockFor      — empty by default
  8. auto                  — no pattern matched; safe to execute

Exit codes (mirror deployment_policy.py exit semantics via --require-auto):
  0 — auto    (safe to execute)
  0 — warn    (safe to execute, but flagged for match-rate tracking) [NEW]
  1 — confirm (operator must approve)
  2 — block   (do not execute under any circumstance)

Repo override schema (.build-loop/config.json):
  {
    "autonomy": {
      "autoFixGuidance": true,
      "autoExecuteOpenRecs": true,
      "confirmFor": ["<glob>", ...],
      "warnFor": ["<glob>", ...],
      "blockFor": ["<glob>", ...]
    }
  }

  confirmFor, warnFor, and blockFor REPLACE defaults, not extend.
  Copy the 7 defaults into your config if you want to extend.
  When a command matches both confirmFor and warnFor, confirmFor wins (stricter
  verdict on tie).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default patterns
# ---------------------------------------------------------------------------

DEFAULT_CONFIRM_FOR: list[str] = [
    "npm publish*",
    "git push --force*",
    "git push * main",
    "git push * master",
    "production deploy*",
    "DROP TABLE*",
    "rm -rf /*",
]

DEFAULT_WARN_FOR: list[str] = []  # empty by default; operators add patterns to observe match-rate before promoting to confirmFor

DEFAULT_BLOCK_FOR: list[str] = []

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class AutonomyConfigError(ValueError):
    """Raised when .build-loop/config.json autonomy section is malformed."""


def load_autonomy_config(workdir: Path) -> tuple[dict[str, Any], str]:
    """Return (autonomy_config_dict, source_label).

    source_label is 'default' when no config file exists or the file has no
    'autonomy' key.  'config' otherwise.
    """
    config_path = workdir / ".build-loop" / "config.json"
    if not config_path.exists():
        return {}, "default"

    try:
        raw = config_path.read_text()
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Graceful fallback: warn to stderr, use defaults.
        print(
            f"WARNING: {config_path} is not valid JSON ({exc}); falling back to defaults.",
            file=sys.stderr,
        )
        return {}, "default"

    if not isinstance(config, dict):
        print(
            f"WARNING: {config_path} root must be a JSON object; falling back to defaults.",
            file=sys.stderr,
        )
        return {}, "default"

    autonomy = config.get("autonomy")
    if autonomy is None:
        return {}, "default"

    if not isinstance(autonomy, dict):
        print(
            "WARNING: config.json 'autonomy' must be a JSON object; falling back to defaults.",
            file=sys.stderr,
        )
        return {}, "default"

    return autonomy, "config"


def _get_pattern_list(autonomy: dict[str, Any], key: str) -> list[str] | None:
    """Return the list for key, or None if key is absent (meaning: use defaults)."""
    if key not in autonomy:
        return None
    value = autonomy[key]
    if not isinstance(value, list):
        print(
            f"WARNING: autonomy.{key} must be a JSON array; ignoring.",
            file=sys.stderr,
        )
        return None
    return [str(p) for p in value]


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


def _matches_any(command: str, patterns: list[str]) -> str | None:
    """Return the first matching glob pattern, or None."""
    normalized = command.strip()
    for pattern in patterns:
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(
            normalized.lower(), pattern.lower()
        ):
            return pattern
    return None


# ---------------------------------------------------------------------------
# Deployment-policy delegation
# ---------------------------------------------------------------------------

_DEPLOYMENT_KEYWORDS = (
    "deploy",
    "push",
    "release",
    "publish",
    "testflight",
    "vercel",
    "netlify",
    "firebase",
    "heroku",
    "railway",
    "xcrun",
    "xcodebuild",
    "twine",
    "gh release",
    "npm publish",
)


_HTTP_CLIENTS = frozenset({"curl", "wget", "http", "httpie", "xh"})


def _looks_like_deployment_command(command: str) -> bool:
    """Quick heuristic: does this command smell like push/deploy/release?"""
    cmd = command.strip()
    if not cmd:
        return False
    # HTTP test clients can never deploy regardless of URL contents.
    # Without this guard, "curl https://app.vercel.app/..." substring-matches
    # "vercel" and gets routed to deployment_policy → unknown → confirm.
    first = cmd.split(None, 1)[0].lstrip("(").rstrip(";")
    if first in _HTTP_CLIENTS:
        return False
    lower = cmd.lower()
    return any(kw in lower for kw in _DEPLOYMENT_KEYWORDS)


def _invoke_deployment_policy(workdir: Path, command: str) -> dict[str, Any] | None:
    """Shell out to deployment_policy.py.  Returns a result dict, or None on error."""
    script = Path(__file__).resolve().parent / "deployment_policy.py"
    if not script.exists():
        return None

    try:
        result = subprocess.run(
            [sys.executable, str(script), "--workdir", str(workdir), "--command", command, "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    return data


_DP_ACTION_TO_AUTONOMY = {
    "auto": "auto",
    "confirm": "confirm",
    "block": "block",
}


def _deployment_policy_envelope(
    dp_data: dict[str, Any], action_label: str, command: str
) -> dict[str, Any]:
    dp_action = dp_data.get("action", "confirm")
    mapped = _DP_ACTION_TO_AUTONOMY.get(dp_action, "confirm")
    return {
        "action": mapped,
        "matched_rule": dp_data.get("target"),
        "list_source": "deployment_policy",
        "reason": dp_data.get("reason", "routed to deployment_policy"),
        "label": action_label,
        "command": command,
    }


# ---------------------------------------------------------------------------
# Core classification
# ---------------------------------------------------------------------------


def classify(
    workdir: Path,
    action_label: str,
    command: str,
) -> dict[str, Any]:
    """Return the autonomy envelope for (action_label, command).

    Does NOT invoke deployment_policy for commands that don't look like
    deploy/push operations (avoids the subprocess overhead for the common case).
    """
    # Step 0: read-only short-circuit.
    # Most user-pain comes from `sed -n ... deployment_policy.py`, `vercel curl`,
    # `vercel logs`, `git status`, etc. being routed to deployment_policy → unknown
    # → confirm. Read-only inspection cannot mutate prod by definition; skip the
    # whole routing chain. classify_action.py owns the read-only matcher.
    try:
        import classify_action  # local import to avoid circular imports at module load
        if classify_action._is_read_only(command):
            autonomy, _ = load_autonomy_config(workdir)
            return _envelope(
                "auto",
                "read_only",
                "default",
                "read-only command; safe to execute",
                action_label,
                command,
                _extract_flags(autonomy),
            )
    except Exception:
        # classify_action not yet installed in older deployments — fall through.
        pass

    # Step 1: deployment_policy delegation
    # When deployment_policy returns an "unknown" target, fall through to
    # autonomy_gate's own pattern matching — DEFAULT_CONFIRM_FOR still catches
    # `production deploy*`, `npm publish*`, etc. via glob, so we don't lose
    # coverage by deferring. Only return early for recognized targets
    # (preview/testflight/production).
    if _looks_like_deployment_command(command):
        dp_data = _invoke_deployment_policy(workdir, command)
        if dp_data is not None and dp_data.get("target") != "unknown":
            env = _deployment_policy_envelope(dp_data, action_label, command)
            # Merge flags from autonomy config even when routed through DP
            autonomy, _ = load_autonomy_config(workdir)
            env["flags"] = _extract_flags(autonomy)
            return env

    # Steps 2-7: pattern matching
    autonomy, config_source = load_autonomy_config(workdir)
    flags = _extract_flags(autonomy)

    repo_block = _get_pattern_list(autonomy, "blockFor")
    repo_confirm = _get_pattern_list(autonomy, "confirmFor")
    repo_warn = _get_pattern_list(autonomy, "warnFor")

    effective_block = repo_block if repo_block is not None else DEFAULT_BLOCK_FOR
    effective_confirm = repo_confirm if repo_confirm is not None else DEFAULT_CONFIRM_FOR

    # Step 2: repo blockFor
    matched = _matches_any(command, effective_block)
    if matched and repo_block is not None:
        return _envelope("block", matched, "config", "matched repo blockFor pattern", action_label, command, flags)

    # Step 3: repo confirmFor (only when config exists and has confirmFor)
    # confirmFor wins over warnFor on a tie — check confirmFor first.
    if repo_confirm is not None:
        matched = _matches_any(command, repo_confirm)
        if matched:
            return _envelope("confirm", matched, "config", "matched repo confirmFor pattern", action_label, command, flags)

    # Step 4: repo warnFor (only when config exists and has warnFor)
    if repo_warn is not None:
        matched = _matches_any(command, repo_warn)
        if matched:
            return _envelope("warn", matched, "config", "matched repo warnFor pattern", action_label, command, flags)

    # Step 5: default confirmFor (only when repo has NOT overridden confirmFor)
    if repo_confirm is None:
        matched = _matches_any(command, DEFAULT_CONFIRM_FOR)
        if matched:
            return _envelope("confirm", matched, "default", "matched default confirmFor pattern", action_label, command, flags)

    # Step 6: default warnFor (only when repo has NOT overridden warnFor; empty by default — no-op)
    if repo_warn is None:
        matched = _matches_any(command, DEFAULT_WARN_FOR)
        if matched:
            return _envelope("warn", matched, "default", "matched default warnFor pattern", action_label, command, flags)

    # Step 7: default blockFor (only when repo has NOT overridden blockFor)
    if repo_block is None:
        matched = _matches_any(command, DEFAULT_BLOCK_FOR)
        if matched:
            return _envelope("block", matched, "default", "matched default blockFor pattern", action_label, command, flags)

    # Step 8: auto
    source = config_source if config_source == "config" else "default"
    return _envelope("auto", None, source, "no pattern matched; safe to execute", action_label, command, flags)


def _envelope(
    action: str,
    matched_rule: str | None,
    list_source: str,
    reason: str,
    label: str,
    command: str,
    flags: dict[str, Any],
) -> dict[str, Any]:
    return {
        "action": action,
        "matched_rule": matched_rule,
        "list_source": list_source,
        "reason": reason,
        "label": label,
        "command": command,
        "flags": flags,
    }


def _extract_flags(autonomy: dict[str, Any]) -> dict[str, Any]:
    return {
        "autoFixGuidance": bool(autonomy.get("autoFixGuidance", True)),
        "autoExecuteOpenRecs": bool(autonomy.get("autoExecuteOpenRecs", True)),
    }


# ---------------------------------------------------------------------------
# Exit code helpers
# ---------------------------------------------------------------------------

ACTION_EXIT_CODES = {
    "auto": 0,
    "warn": 0,      # does not block; flags the action for match-rate tracking
    "confirm": 1,
    "block": 2,
}


# ---------------------------------------------------------------------------
# Inline self-test suite
# ---------------------------------------------------------------------------


def _run_self_tests() -> bool:
    """Run a quick inline sanity suite.  Returns True if all pass."""
    import tempfile

    failures: list[str] = []
    passed = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed
        if condition:
            passed += 1
        else:
            failures.append(f"FAIL [{name}]: {detail}")

    tmp = Path(tempfile.mkdtemp())

    # --- Default confirmFor patterns ---
    for pattern, cmd in [
        ("npm publish*", "npm publish"),
        ("git push --force*", "git push --force origin main"),
        ("git push * main", "git push origin main"),
        ("git push * master", "git push origin master"),
        ("production deploy*", "production deploy v1.0"),
        ("DROP TABLE*", "DROP TABLE users"),
        ("rm -rf /*", "rm -rf /"),
    ]:
        env = classify(tmp, "test", cmd)
        check(
            f"default confirmFor:{cmd}",
            env["action"] == "confirm",
            f"expected confirm, got {env['action']}",
        )

    # --- Auto for benign commands ---
    env = classify(tmp, "edit", "edit scripts/foo.py")
    check("auto benign", env["action"] == "auto", f"got {env['action']}")

    # --- Repo override: custom confirmFor replaces defaults ---
    config_path = tmp / ".build-loop" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"autonomy": {"confirmFor": ["custom danger*"]}}))

    env = classify(tmp, "custom", "custom danger op")
    check("repo custom confirm", env["action"] == "confirm", f"got {env['action']}")

    # Default confirmFor should now be DISABLED (replaced).
    # Use "DROP TABLE users" — in the defaults but not deployment-flavored,
    # so it won't be routed to deployment_policy.
    env = classify(tmp, "db", "DROP TABLE users")
    check("defaults disabled by repo override", env["action"] == "auto", f"got {env['action']}")

    # --- Repo warnFor ---
    config_path.write_text(json.dumps({"autonomy": {"warnFor": ["touch-prod-config*"]}}))
    env = classify(tmp, "ops", "touch-prod-config /etc/foo")
    check("repo warnFor exits 0", env["action"] == "warn", f"got {env['action']}")

    # --- Repo blockFor ---
    config_path.write_text(json.dumps({"autonomy": {"blockFor": ["rm -rf *"]}}))
    env = classify(tmp, "delete", "rm -rf /home")
    check("repo blockFor", env["action"] == "block", f"got {env['action']}")

    # --- Malformed JSON falls back gracefully ---
    config_path.write_text("{ not json }")
    env = classify(tmp, "test", "edit scripts/foo.py")
    check("malformed json fallback", env["action"] == "auto", f"got {env['action']}")

    config_path.unlink()

    # --- Summary ---
    total = passed + len(failures)
    print(f"self-test: {passed}/{total} passed")
    for f in failures:
        print(f)
    return len(failures) == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify a build-loop action against the repo autonomy policy."
    )
    parser.add_argument(
        "--workdir",
        default=".",
        help="Repo root. Defaults to current directory. Reads .build-loop/config.json if present.",
    )
    parser.add_argument(
        "--action",
        required=False,
        default="",
        help="Short label for the action (e.g. 'cache resync', 'guidance fix').",
    )
    parser.add_argument(
        "--command",
        required=False,
        default="",
        help="Shell command or pseudo-command to classify.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="emit_json",
        help="Emit a single-line JSON envelope (default: human-readable one-liner).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the inline test suite and exit.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        ok = _run_self_tests()
        return 0 if ok else 1

    if not args.command:
        parser.error("--command is required (unless --self-test is used)")

    workdir = Path(args.workdir).resolve()
    result = classify(workdir, args.action, args.command)

    if args.emit_json:
        print(json.dumps(result))
    else:
        action = result["action"]
        label = result["label"] or result["command"]
        reason = result["reason"]
        print(f"{action}: {label} — {reason}")

    return ACTION_EXIT_CODES.get(result["action"], 1)


if __name__ == "__main__":
    sys.exit(main())
