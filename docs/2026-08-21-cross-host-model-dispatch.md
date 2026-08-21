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

## CORRECTION 2026-08-21 — verified against codex session logs and the rally ledger

The "not verified" section below was resolved by reading the logs. One claim in the
original analysis was **wrong**.

### Rally carries no model. Verified.

`build-loop/.rally/facts.db`, 11,472 events: **zero** have any `*model*` JSON key. The
`agent-rally.fact.v1` schema is `created_at, event_id, evidence, from_session_id, kind,
ref, role, scope, subject, summary, target, thread_id, tool, uri`. 477 events mention a
model *in prose*; none carry it as a field. Rally cannot dispatch a model because the
fact schema has no place to put one. That part of the original analysis holds.

### Codex DOES run Claude models — by shelling out, not through rally. This corrects the original.

The original speculated Codex was "handing off to a Claude session that resolves Sonnet
locally". It is not. Codex invokes the Claude CLI directly with an explicit model.

Session `rollout-2026-08-20T16-15-54-01a02175-aa61-7883-b222-b52829ee38a1.jsonl`:

```
claude -p --model opus   --effort xhigh --safe-mode      x11
claude -p --model sonnet --effort high  --safe-mode       x7
claude -p --model sonnet --effort high  --permission...   x6
claude -p --model opus   --effort high  --permission...   x4
sonnet:PASS x3   opus:PASS x3
```

The same session authored rally fact `seq=11377` with evidence
`['self_mod_verify:pass 9/9', 'artifact regression:18/18', 'sonnet:PASS', 'opus:PASS']`.
Rally recorded the *result*; the CLI did the dispatch.

Codex also authors the routing config itself — `seq=6368` claims
`references/model-taxonomy.json` and `model-tier-mapping.md` to "Wire GPT-5.6
Sol/Terra/Luna model routing"; `seq=6387` claims five `agents/*.md` files to "Route five
medium-risk reviewers to Claude Opus while retaining GPT-5.6 Sol". Those are file
claims, not runtime dispatch.

### The asymmetry, restated

Codex reaches Claude models through `claude -p --model <id> --effort <level>`, a
first-class pattern in its sessions. Claude reaches Codex through `codex exec`, and
across recent Claude transcripts essentially every occurrence is **prose about the
auditor ladder** rather than an invocation. Exactly one real invocation with a model
appears: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=...`.

So the capability exists on both sides and only one side uses it habitually. The
resolver's provider partition explains why neither host can *resolve* cross-vendor; it
does not explain the dispatch gap. That gap is convention, not mechanism.

### What would close it

Give Claude the same habit Codex has: `codex exec -m <id>` selected by tier, rather than
`codex exec` bare on a stuck-detector. The flag already works — it appears once in the
transcripts and `scripts/exec_state.py:60` accepts `--model`.
