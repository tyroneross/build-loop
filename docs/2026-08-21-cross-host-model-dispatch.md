<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Cross-host model dispatch: a tier lane collapses to the host's own vendor

Measured 2026-08-21 from a live Claude Code session. Backlog:
`BUIL-MODEL-RESOLUTION-m0jahjgzemfd0byhvz2qt` (decision bucket — needs a routing-policy
call, not an implementation).

The question this answers: why does Codex appear to deploy Sonnet through rally while
Claude never invokes Terra or Sol?

## The resolver cannot return a cross-vendor model

`detect_host_providers()` reads `CLAUDECODE` from the environment and returns
`{'anthropic'}`. `resolve()` then folds every non-anthropic registry model into
`unavailable` **before** any tier walk:

```
resolve(code) -> sonnet
unavailable_considered: gpt-5.4, gpt-5.5, gpt-5.6-luna, gpt-5.6-sol,
                        gpt-5.6-terra, qwen2.5-coder-32b, ...
```

An explicit override does not reach past it either — `modelOverrides.frontier =
gpt-5.6-sol` records `skipped: unavailable` in `resolution_path`.

**This is symmetric by design.** A Codex session detects `{'openai'}` and has opus,
sonnet, haiku, and fable filtered the same way. Codex cannot resolve Sonnet through
the resolver either.

## Rally announces a model; it does not assign one

`--model` is a field on the `presence` command, beside `--tool` and `--task`. It
records *"I am claude_code running opus"* so peers can see who is in the room. No
packet field assigns a model to a peer.

So "Codex deploys Sonnet via rally" is most likely Codex **handing off to a Claude
session**, which then resolves Sonnet inside its own provider. Codex is not choosing
Sonnet; it is delegating to a host whose only option is Sonnet.

## The real asymmetry is in dispatch, not resolution

Claude does have a Codex path — `codex exec`, documented in the auditor ladder and the
host-adapter table. Three things keep it from ever selecting a Codex model:

1. **No call site passes `--model`.** `codex exec` runs bare, so Codex picks its own
   default. Claude never names Terra or Sol even when it delegates.
2. **The trigger is difficulty, not tier.** `codex-rescue` fires when Claude is stuck
   or wants a second opinion. Nothing routes on "this tier resolves to a Codex model".
3. **The host-adapter table says the opposite.** Claude's row reads "use the existing
   Claude orchestrator and agent definitions"; the worker-template row belongs to Codex.

The capability is already present and unwired: `scripts/exec_state.py:60` accepts
`--model` with help text *"Explicit model id (skips tier resolution)"*, and nothing
passes a cross-host model into it.

## Consequence

`CLAUDE.md` treats Sonnet and Terra as execution-lane peers, and Opus and Sol as
orchestration-lane peers. The provider-partitioned resolver cannot express that, so a
lane always collapses to the host's own vendor. **Model-tier routing is, in practice,
vendor routing.**

## Not verified

No Codex session was observed actually deploying Sonnet. Everything here is read from
code and measured on the Claude side only. A rally transcript showing Codex naming
Sonnet would contradict this reading of `presence` and should be examined before
anyone acts on this document.

## Possible fix, deliberately not built

Wire `codex exec --model` to a tier decision so a lane can resolve cross-vendor,
instead of gating cross-host dispatch on a stuck-detector. That changes which model
executes work, which is a routing-policy decision rather than a defect fix.
