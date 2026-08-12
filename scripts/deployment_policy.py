#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Classify deployment commands against a repo-local build-loop policy.

Default policy:
  preview -> auto
  testflight -> auto
  production -> confirm
  unknown -> auto  (do-unless-clearly-risky philosophy)

Repo override:
  .build-loop/config.json
  {
    "deploymentPolicy": {
      "preview": "auto",
      "testflight": "auto",
      "production": "confirm",
      "unknown": "auto",
      "protectedBranches": ["main", "master", "release"]
    }
  }

`protectedBranches` overrides which branch names route `git push` as
`production` (default: main / master / production / prod / release /
stable / trunk / live). Empty list = no branches are protected → any
git push routes as preview unless the command shape is itself
production-flavored (npm publish, gh release, etc.). Casing is
ignored; names are compared lowercased. `production` always stays
`confirm` unless the repo explicitly opts in via target-action
mapping — branch declassification only changes the routing path, not
the user-permission posture for true production-shaped commands.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_POLICY = {
    "preview": "auto",
    "testflight": "auto",
    "production": "confirm",
    "unknown": "auto",  # "do unless clearly risky" — unrecognized commands are not risky by default
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
PROTECTED_BRANCHES_KEYS = {"protectedBranches", "protected-branches", "protected_branches"}


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
        if raw_target in PROTECTED_BRANCHES_KEYS:
            # Honored by load_protected_branches; not a target-action mapping.
            continue
        target = _normalize_target(str(raw_target))
        action = _normalize_action(raw_action)
        policy[target] = action
    return policy, str(config_path)


def load_protected_branches(workdir: Path) -> frozenset[str]:
    """Return the effective protected-branch set for this repo.

    Default = `PRODUCTION_BRANCHES`. Override via
    `deploymentPolicy.protectedBranches` (or `protected-branches` /
    `protected_branches`) in `.build-loop/config.json`. Must be a list of
    strings; empty list = no protected branches.
    """
    config_path = workdir / ".build-loop" / "config.json"
    if not config_path.exists():
        return frozenset(PRODUCTION_BRANCHES)
    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{config_path} is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise PolicyError(f"{config_path} must contain a JSON object")
    raw_policy = config.get("deploymentPolicy", {}) or {}
    if not isinstance(raw_policy, dict):
        raise PolicyError("deploymentPolicy must be a JSON object")
    raw_value: Any = None
    for key in PROTECTED_BRANCHES_KEYS:
        if key in raw_policy:
            raw_value = raw_policy[key]
            break
    if raw_value is None:
        return frozenset(PRODUCTION_BRANCHES)
    return _normalize_protected_branches(raw_value)


def _normalize_protected_branches(value: Any) -> frozenset[str]:
    if not isinstance(value, list):
        raise PolicyError("deploymentPolicy.protectedBranches must be a list of strings")
    normalized: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise PolicyError(
                f"deploymentPolicy.protectedBranches entries must be strings, got {type(item).__name__}"
            )
        name = item.strip().lower()
        if name:
            normalized.add(name)
    return frozenset(normalized)


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


def classify_command(
    raw_command: str,
    protected_branches: frozenset[str] | set[str] | None = None,
) -> tuple[str, str]:
    command = extract_command(raw_command)
    if not command.strip():
        return "unknown", "empty command"

    tokens = _split(command)
    lower_tokens = [token.lower() for token in tokens]
    lower_text = command.lower()
    effective_protected = (
        PRODUCTION_BRANCHES if protected_branches is None else protected_branches
    )

    if _is_testflight_command(lower_text, lower_tokens):
        return "testflight", "Apple TestFlight/App Store Connect upload or export"

    if _is_production_command(lower_text, lower_tokens):
        return "production", "production deploy or release command"

    if _is_git_push(lower_tokens):
        branch = _git_push_target_branch(tokens)
        if branch is None:
            return "unknown", "git push without an explicit target branch"
        if branch.lower() in effective_protected:
            return "production", f"git push targets protected branch {branch}"
        return "preview", f"git push targets non-production branch {branch}"

    if _is_preview_command(lower_text, lower_tokens):
        return "preview", "preview or non-production deploy command"

    return "unknown", "command is not recognized as a supported deployment target"


_QUOTED_REGION = re.compile(r"""'[^']*'|"[^"]*\"""", re.VERBOSE)


def _split(command: str) -> list[str]:
    """Argv tokens for the command. Quoted DATA never becomes tokens.

    Why the fallback strips quotes (2026-07-29): `shlex.split` raises on an
    unbalanced quote — which a prose argument containing an apostrophe reliably
    produces. The old fallback was a bare `command.split()`, so every word of
    that prose became a "token", and the substring classifiers below then read
    argument data as command structure. A `rally say handoff --summary "...
    preview ... deploy ..."` message classified as a preview deploy and was
    hard-blocked, three times across two sessions, including a read-only
    investigation *into this defect*.

    Blast radius is why it matters: on any repo with one standing HIGH finding,
    the pre-deploy gate turns that misclassification into a hard block, so any
    command whose PROSE mentions deployment becomes unrunnable. That
    specifically punishes coordination messages, incident write-ups, and
    investigations about deploys.

    On parse failure, drop quoted regions before splitting: a real deploy
    carries its tool and verb as bare argv tokens, never inside quoted data,
    so nothing genuine is lost.
    """
    try:
        return shlex.split(command)
    except ValueError:
        return _QUOTED_REGION.sub(" ", command).split()


def _structural_tokens(lower_tokens: list[str]) -> list[str]:
    """Tokens that can carry command MEANING, i.e. flags, paths, and bare words.

    A token containing whitespace is a quoted argument — prose, a commit
    message, a summary — and its contents describe something rather than doing
    it. `shlex.split` preserves such an argument as ONE token, so a plain
    substring scan over tokens still reads `git commit -m "explain the
    testflight upload flow"` as a TestFlight upload. Flags (`--testflight-only`)
    and paths (`/usr/bin/testflight`) never contain a space, so dropping
    whitespace-bearing tokens costs no genuine command shape.
    """
    return [token for token in lower_tokens if not any(ch.isspace() for ch in token)]


def _is_testflight_command(lower_text: str, lower_tokens: list[str]) -> bool:
    # Token-scoped, not text-scoped — same reason as _is_preview_command below.
    # Substring-within-token is kept for genuine command shapes
    # (`--testflight-only`, a path ending in /testflight); quoted prose is
    # excluded by _structural_tokens.
    structural = _structural_tokens(lower_tokens)
    if any("testflight" in token for token in structural):
        return True
    if "xcrun" in lower_tokens and "altool" in lower_tokens and "--upload-app" in lower_tokens:
        return True
    if "xcodebuild" in lower_tokens and "-exportarchive" in lower_tokens:
        return True
    if any(
        "app-store-connect" in token or "appstoreconnect" in token for token in structural
    ) and any("upload" in token for token in structural):
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


# Commands that ship code somewhere other people can reach it. Deliberately
# WIDER than classify_command's production/preview sets, and kept separate from
# them, because the two answer different questions with opposite cost profiles:
#
#   classify_command  → "should a human confirm this?"  A false positive here
#                       interrupts the user, so it stays conservative.
#   is_deploy_like    → "should the security scanner run first?"  A false
#                       positive costs one cheap local scan; a false negative
#                       ships a secret. So this one errs toward yes.
#
# Both live in this file so deploy-command vocabulary has a single home.
_DEPLOY_VERBS = {"deploy", "publish", "release", "up", "submit", "push", "apply", "sync"}

_DEPLOY_TOOLS = {
    # edge / serverless platforms
    "vercel", "netlify", "wrangler", "flyctl", "fly", "railway", "render",
    "deno", "cloudflare", "firebase", "amplify", "serverless", "sls",
    # cloud CLIs
    "gcloud", "aws", "sam", "cdk", "az", "eb", "heroku",
    # container / orchestration
    "kubectl", "helm", "skaffold", "docker", "nerdctl",
    # backend platforms
    "supabase", "convex", "planetscale", "neonctl", "fauna",
    # mobile
    "eas", "fastlane", "expo",
    # package registries
    "npm", "pnpm", "yarn", "twine", "cargo", "gem", "poetry", "uv",
}


def is_deploy_like(raw_command: str) -> bool:
    """True when the command plausibly publishes code or config to a live target.

    Used by the pre-deploy security gate, not by the confirmation policy. Errs
    toward True: the cost of an unnecessary scan is a second of CPU, the cost of
    a missed one is a shipped credential.
    """
    command = extract_command(raw_command)
    if not command.strip():
        return False

    for segment in re.split(r"&&|\|\||[;|&]", command):
        tokens = [t.lower() for t in _split(segment)]
        if not tokens:
            continue
        text = " ".join(tokens)

        # Anything classify_command already recognizes is deploy-like by
        # definition — no need to restate its rules here.
        target, _reason = classify_command(segment)
        if target in {"production", "preview", "testflight"}:
            return True

        # A known deploy tool paired with a shipping verb.
        if any(t in _DEPLOY_TOOLS for t in tokens) and any(
            v in _DEPLOY_VERBS for v in tokens
        ):
            # `npm run deploy` / `pnpm deploy` count; `npm install` does not,
            # and neither does a local-only `docker build`.
            if "install" in tokens or "add" in tokens or "build" in tokens:
                if not any(v in _DEPLOY_VERBS for v in tokens if v != "build"):
                    continue
            return True

        # Registry publishes and workflow triggers that carry no tool/verb pair.
        if re.search(
            r"\b(?:gh\s+workflow\s+run|gh\s+release\s+create|"
            r"git\s+push\s+.*--tags|make\s+deploy|make\s+release|"
            r"terraform\s+apply|pulumi\s+up|ansible-playbook)\b",
            text,
        ):
            return True

    return False


# Git global options that consume the NEXT token as their value. Anything
# else starting with `-` is a flag we can skip on its own.
_GIT_GLOBAL_OPTS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--config-env",
}


def _git_subcommand_index(tokens: list[str]) -> int | None:
    """Index of git's SUBCOMMAND token, or None if this isn't a `git ...` call.

    `push` is only a push when it is the subcommand. Matching "git appears
    before push" anywhere in the token list misreads local-only commands —
    `git stash push -m msg` is a working-tree save, not a remote publish, and
    classifying it as a deploy wedged an authorized local merge behind the
    pre-deploy security gate (observed 2026-07-27, atomize-ai).
    """
    lower = [t.lower() for t in tokens]
    try:
        i = lower.index("git") + 1
    except ValueError:
        return None
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("-"):
            return i
        # `--git-dir=/path` carries its value inline; `--git-dir /path` does not.
        if token in _GIT_GLOBAL_OPTS_WITH_VALUE:
            i += 2
        else:
            i += 1
    return None


def _is_git_push(lower_tokens: list[str]) -> bool:
    index = _git_subcommand_index(lower_tokens)
    return index is not None and lower_tokens[index] == "push"


def _git_push_target_branch(tokens: list[str]) -> str | None:
    push_index = _git_subcommand_index(tokens)
    if push_index is None or tokens[push_index].lower() != "push":
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
    # Both words must be argv TOKENS, not substrings of the command text.
    # `lower_text` includes quoted argument data, so a prose summary mentioning
    # a preview deploy used to classify as one. Sibling of the `_is_testflight`
    # fix above and of the earlier `git stash push` defect in this file: the
    # recurring shape is argument data read as command structure.
    return "preview" in lower_tokens and "deploy" in lower_tokens


def _has_option_value(tokens: list[str], option: str, value: str) -> bool:
    if f"{option}={value}" in tokens:
        return True
    for index, token in enumerate(tokens[:-1]):
        if token == option and tokens[index + 1] == value:
            return True
    return False


def analyze(workdir: Path, command: str) -> dict[str, str | bool]:
    policy, source = load_policy(workdir)
    protected = load_protected_branches(workdir)
    target, reason = classify_command(command, protected_branches=protected)
    action = policy.get(target, DEFAULT_POLICY[target])
    if target == "production":
        hold = initiative_production_hold(workdir)
        if hold:
            action = "block"
            reason = f"initiative production prohibited by {hold}"
    return {
        "target": target,
        "action": action,
        "requiresConfirmation": action == "confirm",
        "policySource": source,
        "reason": reason,
    }


def initiative_production_hold(workdir: Path) -> str | None:
    """Return the matching initiative queue receipt for the active branch."""
    try:
        branch = subprocess.run(
            ["git", "-C", str(workdir), "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except OSError:
        return None
    if not branch:
        return None
    queue = workdir / ".build-loop" / "queue"
    try:
        queue.resolve().relative_to(workdir.resolve())
    except (OSError, ValueError):
        return None
    try:
        receipts = sorted(queue.glob("*.md")) if queue.is_dir() else []
    except OSError:
        return None
    for path in receipts:
        try:
            path.resolve().relative_to(queue.resolve())
        except (OSError, ValueError):
            continue
        try:
            head = path.read_text(encoding="utf-8").split("---", 2)[1]
        except (OSError, IndexError):
            continue
        fields: dict[str, str] = {}
        for line in head.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"').strip("'")
        if (
            fields.get("bucket") == "initiative"
            and fields.get("production_policy") == "prohibited"
            and fields.get("target_branch") == branch
        ):
            return str(path)
    return None


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
