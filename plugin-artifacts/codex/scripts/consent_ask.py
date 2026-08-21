#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Host-specific ask adapter for the CLI dispatch consent gate.

Contract: references/cli-dispatch-consent-contract.md — read it before changing
anything here, especially the "Ask surface" section. This module turns "we must
ask" into a request the HOST renders, so the agent can never author the
operator's answer.

WHAT THIS IS NOT: a place that records consent. It only reads
`cli_dispatch_consent.check()` / `request_text()` and formats the result for the
current host. Recording a decision belongs to `cli_dispatch_consent` alone (its
`record` function), driven by the operator's actual answer coming back through
the host's ask surface (AskUserQuestion, Codex's approval prompt, …) — never by
this module.

Per the contract's "Ask surface" table:

    | Host           | Surface           | Failure mode                        |
    |----------------|--------------------|--------------------------------------|
    | Claude Code    | AskUserQuestion    | -                                    |
    | Codex          | its approval prompt| -                                    |
    | Cursor headless| none               | fail closed: no ask primitive means  |
    |                |                    | no grant, only a pre-recorded policy |
    | unknown        | none               | fail closed                          |

    python3 scripts/consent_ask.py --product build-loop --vendor codex --command "codex exec ..."
    python3 scripts/consent_ask.py --product build-loop --vendor cursor --command "agent -p ..." --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_dispatch_consent  # noqa: E402
import host_capabilities  # noqa: E402

EXIT_ALLOWED = cli_dispatch_consent.EXIT_ALLOWED
EXIT_MUST_ASK = cli_dispatch_consent.EXIT_MUST_ASK
EXIT_DENIED = cli_dispatch_consent.EXIT_DENIED
EXIT_CHAIN_BROKEN = cli_dispatch_consent.EXIT_CHAIN_BROKEN

# Hosts that DO have an interactive ask primitive this module knows how to
# address. Everything else (cursor headless, gemini, opencode, unknown, ...)
# fails closed per the contract — no ask primitive means no grant, only a
# pre-recorded standing policy the operator set some other way.
_ASKABLE_HOSTS = ("claude_code", "codex")


def _fail_closed_reason(host: str) -> str:
    if host == "cursor":
        return (
            "fail-closed: Cursor headless `agent -p` has no interactive ask "
            "primitive (it takes --force or nothing), so no ask means NO grant — "
            "only a pre-recorded standing policy (an existing `auto` entry) can "
            "authorize this dispatch"
        )
    return (
        f"fail-closed: host {host!r} is unrecognized and has no known ask "
        "primitive, so no ask means NO grant, only a pre-recorded standing policy"
    )


def ask_plan(
    product: str,
    vendor: str,
    command: str,
    *,
    host: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the host-specific request to ask the operator.

    Never authors the answer — `request_text` comes verbatim from
    `cli_dispatch_consent.request_text()`. This function only decides WHERE and
    HOW that text gets in front of the operator for the resolved host.
    """
    resolved_host = host if host is not None else host_capabilities.detect_host(env=env)
    text = cli_dispatch_consent.request_text(product, vendor, command)

    if resolved_host == "claude_code":
        envelope = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": text,
            }
        }
        return {
            "host": resolved_host,
            "can_ask": True,
            "surface": "AskUserQuestion",
            "request_text": text,
            "envelope": envelope,
            "reason": "Claude Code renders the request via AskUserQuestion; the "
            "operator's selection, not this module, is the answer.",
        }

    if resolved_host == "codex":
        return {
            "host": resolved_host,
            "can_ask": True,
            "surface": "codex_approval",
            "request_text": text,
            "envelope": None,
            "reason": "Codex renders the request via its own approval prompt; the "
            "operator's response there is the answer, not anything this module writes.",
        }

    # Every other host (cursor headless, gemini, opencode, unknown, ...) fails
    # closed per the contract's Ask surface table.
    return {
        "host": resolved_host,
        "can_ask": False,
        "surface": "none",
        "request_text": text,
        "envelope": None,
        "reason": _fail_closed_reason(resolved_host),
    }


def resolve(
    product: str,
    vendor: str,
    command: str,
    *,
    host: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The single entry point: check first, only build an ask_plan if needed.

    - already allowed (`auto`)      -> short-circuit, no ask
    - denied, or depth exceeded     -> short-circuit, no ask
    - chain broken, or needs_prompt -> build and attach an ask_plan
    """
    check = cli_dispatch_consent.check(product, vendor, env=env)
    out: dict[str, Any] = {"check": check, "ask_plan": None}

    if not check["needs_prompt"]:
        # Either already granted (`auto`) or a terminal refusal (`denied`,
        # depth-exceeded). Neither case asks — asking a second time after a
        # standing `denied` or a hard depth cap would just be pestering the
        # operator for an answer that cannot change the outcome.
        return out

    out["ask_plan"] = ask_plan(product, vendor, command, host=host, env=env)
    return out


def _cmd(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", choices=cli_dispatch_consent.PRODUCTS, required=True)
    ap.add_argument("--vendor", choices=cli_dispatch_consent.VENDORS, required=True)
    ap.add_argument("--command", required=True, help="the vendor CLI command about to be dispatched")
    ap.add_argument("--host", default=None, help="override host detection (e.g. claude_code, codex, cursor)")
    ap.add_argument("--json", action="store_true", dest="emit_json")
    a = ap.parse_args(argv)

    result = resolve(a.product, a.vendor, a.command, host=a.host)
    check = result["check"]

    if a.emit_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{check['key']}: {check['reason']}")
        if result["ask_plan"] is not None:
            plan = result["ask_plan"]
            print(f"host={plan['host']} can_ask={plan['can_ask']} surface={plan['surface']}")
            print(plan["reason"])

    return int(check["exit"])


def main(argv: list[str] | None = None) -> int:
    return _cmd(argv)


if __name__ == "__main__":
    sys.exit(main())
