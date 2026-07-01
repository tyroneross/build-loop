# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# route_guard.py — keep build/fix/refactor prompts routed to build-loop instead
# of being hijacked by another plugin's coercive SessionStart injection
# (notably superpowers:brainstorming, whose using-superpowers skill injects a
# "brainstorming first, you MUST" block every session and out-ranks build-loop
# in the model's skill routing).
#
# Two modes, one classifier (single source of truth for the build-signal regex):
#
#   prompt-submit  (UserPromptSubmit) — classify the submitted prompt, persist a
#                  verdict to .build-loop/routing/last-verdict.json, and — only
#                  when superpowers is installed and the prompt is build-shaped —
#                  inject a short authoritative routing note. Fires AFTER all
#                  SessionStart injections already sit in context, so it wins on
#                  salience without a SessionStart-vs-SessionStart ordering race.
#
#   pre-skill      (PreToolUse, matcher "Skill") — if the model is about to invoke
#                  superpowers:brainstorming while the current turn's verdict is
#                  "build", DENY with a reason that redirects to build-loop. This
#                  is the deterministic layer: it does not depend on the model
#                  routing correctly; it catches the miss at the tool boundary.
#
# Design notes:
#   * Fail-open ALWAYS. Any error -> emit nothing meaningful, exit 0. A routing
#     guard must never break a session. See memory reference_hooks_minimal_path_failopen.
#   * Advisory-by-design for legitimate ideation: a prompt that explicitly asks
#     to brainstorm/ideate classifies as passthrough, so an intentional
#     brainstorming request is never blocked ("named override").
#   * Opt out with BUILD_LOOP_ROUTE_GUARD=off (mirrors RALLY_HOOKS=off).

import glob
import json
import os
import re
import sys
import time

VERDICT_TTL_SECONDS = 2 * 60 * 60  # a verdict older than this can never block
GUARDED_SKILLS = {"superpowers:brainstorming"}

# Build / fix / refactor intent — mirrors build-loop's own SKILL trigger vocab.
_BUILD_RE = re.compile(
    r"\b(build|implement|creat(e|ing)|add(ing)?|ship|wire\s*up|wire|integrat(e|ing)|"
    r"refactor|migrat(e|ing)|rewrit(e|ing)|replac(e|ing)|scaffold|set\s*up|"
    r"fix(ing)?|broken|debug|doesn'?t\s+work|isn'?t\s+(work|load|display)|"
    r"not\s+(work|load|display|render)|should\s+(show|display|be)|needs?\s+to|make\s+it)\b",
    re.IGNORECASE,
)

# Explicit ideation intent — user WANTS open-ended thinking. Suppresses routing
# even if build verbs are also present ("named override").
_IDEATION_RE = re.compile(
    r"\b(brainstorm(ing)?|ideat(e|ion)|explore\s+ideas?|think\s+through|"
    r"weigh\s+(the\s+)?options|options\s+for|what\s+should|help\s+me\s+decide|"
    r"pros\s+and\s+cons|design\s+options|figure\s+out\s+what)\b",
    re.IGNORECASE,
)

ROUTING_NOTE = (
    "[build-loop routing] This looks like a build/fix/refactor task. Enter via "
    "/build-loop:build-loop, which runs its own assess->plan phases. Do NOT "
    "auto-invoke superpowers:brainstorming (or other superpowers process skills) "
    "for this unless the user names it explicitly."
)

DENY_REASON = (
    "build-loop owns build/fix/refactor routing in this repo, and the current "
    "prompt was classified as a build task (not an explicit ideation request). "
    "Route via /build-loop:build-loop instead of superpowers:brainstorming. If "
    "the user actually asked to brainstorm, re-read the prompt and proceed; if "
    "this guard is unwanted, set BUILD_LOOP_ROUTE_GUARD=off."
)


def _read_stdin():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _is_build(prompt: str) -> bool:
    if not prompt:
        return False
    if _IDEATION_RE.search(prompt):
        return False
    return bool(_BUILD_RE.search(prompt))


def _project_dir(payload) -> str:
    return (
        os.environ.get("CLAUDE_PROJECT_DIR")
        or payload.get("cwd")
        or os.getcwd()
    )


def _verdict_path(payload):
    proj = _project_dir(payload)
    if not proj:
        return None
    return os.path.join(proj, ".build-loop", "routing", "last-verdict.json")


def _superpowers_installed() -> bool:
    home = os.path.expanduser("~")
    patterns = [
        os.path.join(home, ".claude", "plugins", "cache", "*", "superpowers"),
        os.path.join(home, ".claude", "plugins", "*", "superpowers"),
        os.path.join(home, ".claude", "plugins", "*superpowers*"),
    ]
    for pat in patterns:
        if glob.glob(pat):
            return True
    return False


def _emit(obj):
    try:
        sys.stdout.write(json.dumps(obj))
    except Exception:
        sys.stdout.write("{}")


def mode_prompt_submit(payload):
    prompt = payload.get("prompt", "") or ""
    is_build = _is_build(prompt)

    # Persist verdict for the pre-skill guard (best-effort).
    path = _verdict_path(payload)
    if path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump(
                    {
                        "verdict": "build" if is_build else "passthrough",
                        "ts": int(time.time()),
                        "prompt": prompt[:200],
                    },
                    fh,
                )
        except Exception:
            pass

    # Proactively bias routing only when there's a real conflict to resolve.
    if is_build and _superpowers_installed():
        _emit(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": ROUTING_NOTE,
                }
            }
        )
    else:
        sys.stdout.write("{}")


def mode_pre_skill(payload):
    tool = payload.get("tool_name") or payload.get("tool") or ""
    if tool != "Skill":
        sys.stdout.write("{}")
        return

    tool_input = payload.get("tool_input") or {}
    skill = (tool_input.get("skill") or tool_input.get("name") or "").strip()
    if skill not in GUARDED_SKILLS:
        sys.stdout.write("{}")
        return

    # Only deny when the current turn was classified as a build task.
    path = _verdict_path(payload)
    verdict = None
    if path and os.path.exists(path):
        try:
            with open(path) as fh:
                data = json.load(fh)
            if int(time.time()) - int(data.get("ts", 0)) <= VERDICT_TTL_SECONDS:
                verdict = data.get("verdict")
        except Exception:
            verdict = None

    if verdict == "build":
        _emit(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": DENY_REASON,
                }
            }
        )
    else:
        sys.stdout.write("{}")


def main():
    # Escape hatch + hard fail-open wrapper.
    if os.environ.get("BUILD_LOOP_ROUTE_GUARD", "").lower() == "off":
        sys.stdout.write("{}")
        return
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else ""
        payload = _read_stdin()
        if mode == "prompt-submit":
            mode_prompt_submit(payload)
        elif mode == "pre-skill":
            mode_pre_skill(payload)
        else:
            sys.stdout.write("{}")
    except Exception:
        # Never break a session over a routing hint.
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
