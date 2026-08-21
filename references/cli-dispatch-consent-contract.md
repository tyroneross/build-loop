<!--
SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
SPDX-License-Identifier: Apache-2.0
-->
# CLI dispatch consent — shared contract

Build Loop and Rally Point each shell out to another vendor's LLM CLI. Each owns
its own implementation of this gate (different languages, different chokepoints),
and neither takes a runtime dependency on the other. This file is the contract both
implement, and the conformance suite grades both against it.

Modeled on `plugin_boundary.json`: one tracked spec, validated by a checker, rather
than a shared runtime that would invert Rally Point's standalone boundary.

## What this gate is, and is not

It is **tamper-evident**, not tamper-proof. The gated process runs as the operator,
with the operator's read and write access. An agent that decides to forge consent
can write the store. What it cannot do is write the store *without the next
`--verify-chain` failing* and *without the head hash the operator was last shown
changing*.

Anyone who describes this as preventing a determined local process is wrong. It
detects. Prevention requires a separate principal (a broker under another UID),
which is deliberately out of scope here.

## Enforcement points

| Product | Chokepoint | Mechanism |
|---|---|---|
| Build Loop | `scripts/hooks/pre_bash_dispatch.sh` | PreToolUse:Bash hook; the dispatch is a model-typed shell command, not a function call |
| Rally Point | `crates/cockpitd/src/supervisor.rs` `launch_session` / `launch_session_async` | in-process check before `adapter.start` |

Rally Point MUST also gate `Adapter::send` (`adapter/codex.rs` `exec resume`). That
path sets `Stdio::null()` on all three streams, so an ungated spawn there produces
no observable output at all.

## Key granularity

The consent key is `"<product>:<vendor>"`.

- products: `build-loop`, `rally-point`
- vendors: `claude`, `codex`, `cursor`, `ollama`

Keying on the vendor, never the model id. Model names drift (`fable`, `sol`,
`opus-5`), and a key that re-prompts on every rename trains the operator to answer
`auto` to everything to make the prompting stop. Bounded at products x vendors = 8
lifetime prompts.

A grant for one key NEVER extends to another. `build-loop:codex` says nothing about
`rally-point:codex` or `build-loop:claude`.

## Modes

| Mode | Grants forward? | Meaning |
|---|---|---|
| `once` | no | approved that one invocation; recorded because "has this operator been asked" is itself worth knowing |
| `ask` | no | a standing choice to be asked every time |
| `auto` | **yes** | the ONLY granting mode |
| `denied` | no | refuse, and keep refusing |

**Never-relax rule.** A recorded decision may only ever turn a `confirm` into an
`auto`, and only for a key it explicitly covers. It can never produce `auto` for: a
key with no record, a record whose chain does not verify, a record whose mode is not
literally `auto`, or a dispatch whose depth exceeds the cap. Each is a separate
branch, not a documentation claim.

Absence of a decision is never consent. An unreadable, missing, or malformed store
reads as NO consent.

## Store

Path is fixed: `~/.build-loop/cli-dispatch-consent.json`. Per-operator, not
per-repo — consent is a standing decision about the operator's own credentials and
spend, and should not silently reset because work moved to a new checkout. Every
entry records the repo it was decided in, so a future narrowing to per-repo scope
has the data.

There is **no environment override of the store path** outside a test process. An
override is a one-line bypass of everything above it: point it at an attacker-written
file and every check passes. Implementations MAY honor an override only when the
process is demonstrably a test runner (`PYTEST_CURRENT_TEST` set, or an explicit
in-process argument).

## Wire format

```json
{
  "version": 2,
  "log": [
    {
      "seq": 0,
      "key": "build-loop:codex",
      "mode": "auto",
      "decided_at": "2026-08-21T18:04:11Z",
      "decided_by": "user",
      "decided_via": "claude_code_ask",
      "decided_in_repo": "/Users/x/dev/git-folder/build-loop",
      "prev_sha256": null,
      "entry_sha256": "9f2b…"
    }
  ]
}
```

`consent` state is DERIVED by replaying `log` in `seq` order; the last entry for a
key wins. There is no separate materialized map to disagree with the log.

### Hashing

`entry_sha256` = SHA-256 over the UTF-8 canonical JSON of the entry with
`entry_sha256` removed, keys sorted, separators `(",", ":")`, no trailing newline.
`prev_sha256` is the previous entry's `entry_sha256`, or `null` at `seq` 0.

The chain alone is worth nothing — an agent can recompute the whole thing. It works
only against an anchor outside the agent's reach. The anchor here is the operator:
the head hash is printed at session start and after every grant, so a silent rewrite
becomes visible because the operator holds the previous value.

## Depth guard

Env `BUILD_LOOP_DISPATCH_DEPTH`, an integer, incremented by each dispatching process
before it spawns a vendor CLI. Above `2`, refuse regardless of recorded consent.

One `auto` grant otherwise authorizes unbounded recursion: claude -> build-loop ->
codex -> claude. Depth is the only thing that caps the cascade, and it is not
covered by any consent answer the operator can give.

Unset reads as `0`. A non-integer value reads as **exceeded**, not as `0` — a
garbage value is the shape a bypass attempt takes. A **negative** value likewise
reads as exceeded: no legitimate caller counts backwards, and `-1` would otherwise
buy four levels of recursion instead of two.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | allowed without asking |
| 1 | must ask (no record, or `once`/`ask`) |
| 2 | denied |
| 3 | chain does not verify — treated as not-allowed, and said out loud |

Mirrors the autonomy-gate convention. Codes 1, 2 and 3 all mean "do not dispatch
silently"; they differ in what the operator is told.

## Ask surface

The gate NEVER authors its own approval. It emits a request; the host renders it and
returns the operator's actual selection.

| Host | Surface | Failure mode |
|---|---|---|
| Claude Code | `AskUserQuestion`, rendered by the harness | — |
| Codex | its approval prompt | — |
| Cursor headless | none — `agent -p` takes `--force` or nothing | **fail closed**: no ask primitive means no grant, only a pre-recorded standing policy |
| unknown | none | fail closed |

A prompt-injected agent can ask to be approved. It cannot fabricate the operator's
click. That asymmetry is the entire security value of this design; a shared secret
both sides can read provides none of it.

## Kill switch

`BUILD_LOOP_HOOKS=off` disables the Build Loop hook dispatcher wholesale, and stays
that way. It is not removed: the hook's own history records that misfiring gates
teach operators to reach for it permanently, and a gate that gets disabled forever is
worse than one with a logged escape hatch.

Every dispatch that proceeds with the switch off MUST append a `kill_switch_used`
entry to the chain. Visible, not prevented — consistent with the rest of this file.

## Rollout

Ship warn-only. Emit `ask`, never `deny`, and count what would have been blocked.
Arm it only after the measured fire rate is read. A noisy gate is worse than no gate.
