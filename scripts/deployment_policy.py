#!/usr/bin/env python3
"""Classify deployment commands against a repo-local build-loop policy.

Default policy:
  preview -> auto
  testflight -> auto
  production -> confirm
  unknown -> confirm

Repo override:
  .build-loop/config.json
  {
    "deploymentPolicy": {
      "preview": "auto",
      "testflight": "auto",
      "production": "confirm",
      "unknown": "confirm"
    }
  }
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

DEFAULT_POLICY = {
    "preview": "auto",
    "testflight": "auto",
    "production": "confirm",
    "unknown": "confirm",
}

VALID_ACTIONS = {"auto", "confirm", "block"}
TARGET_ALIASES = {
    "preview": "preview",
    "nonprod": "preview",
    "non-prod": "preview",
    "nonproduction": "preview",
    "staging": "preview",
    "development": "preview",
    "dev": "preview",
    "testflight": "testflight",
    "test-flight": "testflight",
    "xcode": "testflight",
    "appconnect": "testflight",
    "app-store-connect-testflight": "testflight",
    "production": "production",
    "prod": "production",
    "live": "production",
    "unknown": "unknown",
}
ACTION_ALIASES = {
    "auto": "auto",
    "automatic": "auto",
    "true": "auto",
    "confirm": "confirm",
    "ask": "confirm",
    "manual": "confirm",
    "false": "confirm",
    "block": "block",
    "deny": "block",
}
PRODUCTION_BRANCHES = {"main", "master", "production", "prod", "release", "stable", "trunk", "live"}


class PolicyError(ValueError):
    """Raised when repo deployment policy config is malformed."""


def extract_command(raw: str) -> str:
    """Return a shell-like command from raw text or a hook JSON payload."""
    text = raw.strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        for key in ("command", "cmd", "script"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return " ".join(_string_values(payload))
    if isinstance(payload, str):
        return payload
    return text


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []


def load_policy(workdir: Path) -> tuple[dict[str, str], str]:
    config_path = workdir / ".build-loop" / "config.json"
    if not config_path.exists():
        return dict(DEFAULT_POLICY), "default"
    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{config_path} is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise PolicyError(f"{config_path} must contain a JSON object")

    raw_policy = config.get("deploymentPolicy", {})
    if raw_policy is None:
        raw_policy = {}
    if not isinstance(raw_policy, dict):
        raise PolicyError("deploymentPolicy must be a JSON object")

    policy = dict(DEFAULT_POLICY)
    for raw_target, raw_action in raw_policy.items():
        target = _normalize_target(str(raw_target))
        action = _normalize_action(raw_action)
        policy[target] = action
    return policy, str(config_path)


def _normalize_target(value: str) -> str:
    key = value.strip().lower().replace("_", "-").replace(" ", "-")
    if key not in TARGET_ALIASES:
        raise PolicyError(f"unknown deploymentPolicy target {value!r}")
    return TARGET_ALIASES[key]


def _normalize_action(value: Any) -> str:
    key = str(value).strip().lower().replace("_", "-").replace(" ", "-")
    action = ACTION_ALIASES.get(key)
    if action not in VALID_ACTIONS:
        raise PolicyError(f"deploymentPolicy action must be one of {sorted(VALID_ACTIONS)}")
    return action


def classify_command(raw_command: str) -> tuple[str, str]:
    command = extract_command(raw_command)
    if not command.strip():
        return "unknown", "empty command"

    tokens = _split(command)
    lower_tokens = [token.lower() for token in tokens]
    lower_text = command.lower()

    if _is_testflight_command(lower_text, lower_tokens):
        return "testflight", "Apple TestFlight/App Store Connect upload or export"

    if _is_production_command(lower_text, lower_tokens):
        return "production", "production deploy or release command"

    if _is_git_push(lower_tokens):
        branch = _git_push_target_branch(tokens)
        if branch is None:
            return "unknown", "git push without an explicit target branch"
        if branch.lower() in PRODUCTION_BRANCHES:
            return "production", f"git push targets protected branch {branch}"
        return "preview", f"git push targets non-production branch {branch}"

    if _is_preview_command(lower_text, lower_tokens):
        return "preview", "preview or non-production deploy command"

    return "unknown", "command is not recognized as a supported deployment target"


def _split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_testflight_command(lower_text: str, lower_tokens: list[str]) -> bool:
    if "testflight" in lower_text:
        return True
    if "xcrun" in lower_tokens and "altool" in lower_tokens and "--upload-app" in lower_tokens:
        return True
    if "xcodebuild" in lower_tokens and "-exportarchive" in lower_tokens:
        return True
    if ("app-store-connect" in lower_text or "appstoreconnect" in lower_text) and "upload" in lower_text:
        return True
    return False


def _is_production_command(lower_text: str, lower_tokens: list[str]) -> bool:
    command_text = " ".join(lower_tokens)
    if "npm publish" in command_text or "gh release" in command_text or "twine upload" in command_text:
        return True
    if "vercel" in lower_tokens and "deploy" in lower_tokens:
        return "--prod" in lower_tokens or _has_option_value(lower_tokens, "--target", "production")
    if "netlify" in lower_tokens and "deploy" in lower_tokens:
        return "--prod" in lower_tokens or _has_option_value(lower_tokens, "--context", "production")
    if "firebase" in lower_tokens and "deploy" in lower_tokens:
        return True
    if "app store" in lower_text and re.search(r"\b(release|submit|submission|production|prod)\b", lower_text):
        return True
    return False


def _is_git_push(lower_tokens: list[str]) -> bool:
    if "git" not in lower_tokens or "push" not in lower_tokens:
        return False
    return lower_tokens.index("git") < lower_tokens.index("push")


def _git_push_target_branch(tokens: list[str]) -> str | None:
    lower_tokens = [token.lower() for token in tokens]
    try:
        push_index = lower_tokens.index("push")
    except ValueError:
        return None
    refs = _git_push_positionals(tokens[push_index + 1 :])
    if not refs:
        return None
    if len(refs) == 1 and _looks_like_remote(refs[0]):
        return None

    candidate = refs[-1]
    if len(refs) >= 2 and _looks_like_remote(refs[0]):
        candidate = refs[1]
    return _branch_name_from_ref(candidate)


def _git_push_positionals(args: list[str]) -> list[str]:
    refs: list[str] = []
    skip_next = False
    options_with_value = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            continue
        if arg in options_with_value:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        refs.append(arg)
    return refs


def _looks_like_remote(value: str) -> bool:
    return value in {"origin", "upstream"} or value.startswith(("git@", "https://", "ssh://"))


def _branch_name_from_ref(ref: str) -> str | None:
    target = ref.split(":", 1)[1] if ":" in ref else ref
    target = target.removeprefix("refs/heads/")
    if "/" in target and target.split("/", 1)[0] in {"origin", "upstream"}:
        target = target.split("/", 1)[1]
    if target in {"", "HEAD", "head"}:
        return None
    return target


def _is_preview_command(lower_text: str, lower_tokens: list[str]) -> bool:
    if "vercel" in lower_tokens and "deploy" in lower_tokens:
        return "--prod" not in lower_tokens and not _has_option_value(lower_tokens, "--target", "production")
    if "netlify" in lower_tokens and "deploy" in lower_tokens:
        return "--prod" not in lower_tokens and not _has_option_value(lower_tokens, "--context", "production")
    return "preview" in lower_text and "deploy" in lower_text


def _has_option_value(tokens: list[str], option: str, value: str) -> bool:
    if f"{option}={value}" in tokens:
        return True
    for index, token in enumerate(tokens[:-1]):
        if token == option and tokens[index + 1] == value:
            return True
    return False


def analyze(workdir: Path, command: str) -> dict[str, str | bool]:
    policy, source = load_policy(workdir)
    target, reason = classify_command(command)
    action = policy.get(target, DEFAULT_POLICY[target])
    return {
        "target": target,
        "action": action,
        "requiresConfirmation": action == "confirm",
        "policySource": source,
        "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a push/deploy command against build-loop deployment policy.")
    parser.add_argument("--workdir", default=".", help="Repo root. Defaults to current directory.")
    parser.add_argument("--command", required=True, help="Shell command or hook payload to classify.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--require-auto", action="store_true", help="Exit nonzero unless the policy action is auto.")
    args = parser.parse_args(argv)

    try:
        result = analyze(Path(args.workdir), args.command)
    except PolicyError as exc:
        result = {
            "target": "unknown",
            "action": "confirm",
            "requiresConfirmation": True,
            "policySource": "error",
            "reason": f"policy error: {exc}",
        }
        _print_result(result, args.format)
        return 1

    _print_result(result, args.format)
    if args.require_auto:
        if result["action"] == "auto":
            return 0
        if result["action"] == "block":
            return 3
        return 2
    return 0


def _print_result(result: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result, sort_keys=True))
        return
    print(f"{result['target']} {result['action']}: {result['reason']}")


if __name__ == "__main__":
    sys.exit(main())
