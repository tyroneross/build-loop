<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Privileged-request broker — naming, coalescing, and recording admin prompts

Load this when a build touches a command that asks macOS for an administrator
password, when a user reports unexplained password dialogs, or when changing
`scripts/privileged_commands.json`.

Scope: RUNTIME privileged requests across concurrent agent tasks. The separate
background-item identity work — giving persistent login items recognizable names
— is a different problem and is untouched here.

---

## The incident this exists for

**2026-08-20, 01:29:11 and 01:29:25 PDT.** A Codex task ran `sfltool dumpbtm`
twice. macOS showed two administrator-password dialogs naming only `sfltool` —
no app, no repository, no reason. Parent chain: ChatGPT/Codex → zsh → sfltool.

Evidence, from `~/.codex/sessions/2026/08/20/`:

| Session | UTC | Command |
|---|---|---|
| `01a01e31` | 08:04:07 | `sfltool dumpbtm` (bare, inside a 4-way `Promise.all`) |
| `01a01e46` | 08:29:11 | `sfltool dumpbtm 2>/dev/null \| rg -n -C 2 '…' \| sed -n '1,260p'` |
| `01a01e46` | 08:29:25 | `set -o pipefail`⏎`sfltool dumpbtm \| sed -n '1,120p'`⏎`rc=$?` |
| `01a01e4b` | 08:30:59 | probing where `sfltool` lives (no `dumpbtm`) |

The 08:29 pair share a session AND a `turn_id`. Both returned **empty output**
after 10.6 s and 30.6 s of wall clock — the time the dialog sat on screen.

### Three separable faults

**1 — Anonymity.** `sfltool dumpbtm` requires root and is **undocumented**:
`sfltool(1)` describes only `sfltool archive`. macOS names the leaf binary in the
dialog and nothing else, so the user could not tell which of several concurrent
agent tasks was asking, or why.

**2 — A failure shaped like a result.** A refused privileged read returns empty
stdout, which is indistinguishable from "the grep matched nothing". The agent
could not tell denial from no-results, so it retried 14 seconds later with a
different wrapper (`set -o pipefail`, `rc=$?`) purely to get diagnostics. **The
retry was rational.** The second dialog was caused by the first one's outcome
being unreadable, not by carelessness.

**3 — No shared broker.** Three sessions reached for the same host fact inside
27 minutes. Nothing coalesced them, named them, or recorded that a prompt
occurred.

### Baseline, measured

`python3 scripts/privileged_audit.py report` over 4,169 transcript files:

| metric | before | projected |
|---|---:|---:|
| privileged invocations | 13 | 13 |
| OS prompts | 10 | 7 |
| coalesced | 0 | 3 |
| retries | 1 | 0 |
| unattributed | 13 | 0 |
| distinct requests | 6 | 6 |

`projected` replays the same observed trace through the coalescing rules. It is a
counterfactual on real input, not a measurement; the measured column fills in from
the broker's own ledger as traffic routes through it.

---

## What was built

| Piece | File | Job |
|---|---|---|
| Registry | `scripts/privileged_commands.json` | Which commands are privileged, their scope, mutability, TTL. Data, not code. |
| Coordinator | `scripts/privileged_broker.py` | Attribution, single-flight, TTL cache, state machine, hash-chained ledger. |
| Forensics | `scripts/privileged_audit.py` | Read-only. Reconstructs the baseline from transcripts; before/after counts. |
| Enforcement | `scripts/hooks/pre_bash_privileged.py` | PreToolUse:Bash gate. Redirects a raw privileged command to the broker. |

---

## Using it

```bash
# What in this command needs root?
python3 scripts/privileged_broker.py classify --command "sfltool dumpbtm | head" --json

# Run it through the coordinator. --purpose is MANDATORY.
python3 scripts/privileged_broker.py request \
  --purpose "enumerate background-task items to name unlabeled login items" \
  --task-id "$RUN_ID" --repo "$PWD" --initiating-app "Claude Code" \
  --argv sfltool dumpbtm

# What is in flight, what is cached, is the ledger intact?
python3 scripts/privileged_broker.py status --json
python3 scripts/privileged_broker.py verify-ledger --json

# Cancelled a dialog and now want to allow it? Drop the cached answer.
# This can only ever CAUSE a prompt, never skip one. An in-flight key is left alone.
python3 scripts/privileged_broker.py forget --key <key-or-prefix> --json

# Before/after counts.
python3 scripts/privileged_audit.py report --window 300
```

Exit codes for `request`: `0` completed · `1` denied/cancelled/timeout/failed ·
`2` refused by the broker (bad request, password-capture shape, attempt cap).

### What the user sees before the dialog

```
┌ ADMIN PASSWORD REQUEST ─────────────────────────────────────
│ macOS is about to ask for your admin password for: sfltool
│ Who    Codex · task d71397a3 · thread 01a01e46
│ Where  build-loop (worktree run-864834) · branch main
│ What   /usr/bin/sfltool dumpbtm
│ Why    enumerate background-task items to name unlabeled login items
│ Scope  btm:read · read-only · trust=local-admin
│ Shared yes — identical read-only requests reuse this for 900s, 2 task(s) waiting
│ When   2026-08-20T08:29:11Z · request 4f2a…
└─────────────────────────────────────────────────────────────
```

---

## The rules

### Coalescing is narrow

Two requests share one authorization only when **all** of these are identical:
resolved `argv`, `scope`, `trust_domain`, `mutating`, uid, and registry entry.
Anything different is a different key and inherits nothing.

The identity is the **argv**, not the shell string. That is what makes it work on
the real incident: `sfltool dumpbtm 2>/dev/null | rg …` and `set -o pipefail`⏎
`sfltool dumpbtm | sed …` are different strings and the same request.

### Mutating never coalesces

A mutating request gets a private key directory, never reads the cache, never
writes one. One request, one prompt, no inheritance — in either direction.

### A negative is remembered; a negative is never upgraded

Denial, cancellation, and timeout are cached for `negative_ttl_seconds`
(default 300 s, 600 s for `sfltool dumpbtm`). During that window an identical
request is refused **from cache, without a dialog**. This is the control that
kills the observed retry. A cached terminal state replays verbatim; no branch
turns a `denied` into a `completed`.

The negative cache must not become a trap, so `forget --key` drops a cached
answer on demand. It removes an answer; the next request has to earn a new one.

### A cap is a rate limit, never a lockout

When a result ages out and nobody owns the key, the TTL window rolls: the cached
result and the prompt-attempt counter are cleared **together**. They have to move
as a unit — a counter that outlived its window would leave a key that once hit
the cap permanently `denied_exhausted`.

### The password is never touched

macOS performs the authentication. The broker decides who triggers it and shares
the resulting *output*. It refuses `sudo -S`, `--stdin`, `-A`, `--askpass`, a set
`SUDO_ASKPASS`, and any `--password=` argument, and never gives the child a piped
stdin. Nothing password-shaped is ever written to the store.

### A crashed owner cannot strand or storm

The owner heartbeats while the command runs. A waiter whose owner has a stale
heartbeat **and** a dead pid takes the lease over — once. `max_prompt_attempts`
(default 2) bounds how many dialogs one key may open in a TTL window; past it the
key goes terminal as `denied_exhausted` and every waiter gets that answer.

### Ambient observes; Ambient never decides

Durable visibility is `ledger.jsonl` — append-only and hash-chained, so a deleted
or edited record is detectable (`verify-ledger`). Live visibility is an optional
`ambient.notify_command` that receives each event on stdin. **The return value is
discarded and never inspected**, so no Ambient state can approve, deny, terminate,
or widen a request. A hostile sink is a covered test case.

To surface the event in RossLabs Ambient Agent, run its local daemon and configure
the broker with its `ambientctl` binary and state directory. The receiver accepts
only the redacted event on standard input; it records the purpose, requesting app,
risk class, and broker event id in Ambient activity. It never receives command
arguments, command output, or a password.

```json
{
  "ambient": {
    "mode": "live",
    "notify_command": ["/absolute/path/to/ambientctl", "--state-dir", "/absolute/path/to/ambient-state", "systemAccessRequest"],
    "notify_timeout_seconds": 3
  }
}
```

### Unavailability is never approval

| Risk class | Coordinator unavailable | Ambient unavailable |
|---|---|---|
| read-only | proceed **uncoalesced**, attribution still printed, gap receipt written | proceed; gap receipt |
| mutating | **refuse** — never run a privileged mutation with no record | proceed; gap receipt |
| unknown | proceed uncoalesced, never coalesce; gap receipt | proceed; gap receipt |

Every receipt carries `unattributed_possible: true`. An empty ledger means *no
brokered traffic*, never *no privileged request*. `privileged_audit.py` counts
gaps as `unattributed` for exactly this reason.

---

## Adding a command

Edit `scripts/privileged_commands.json`. Never special-case a command in the
broker.

```json
{
  "id": "sfltool-dumpbtm",
  "executable": "sfltool",
  "argv_prefix": ["dumpbtm"],
  "scope": "btm:read",
  "mutating": false,
  "cacheable": true,
  "ttl_seconds": 900,
  "negative_ttl_seconds": 600,
  "confidence": "observed",
  "notes": "…"
}
```

Longest matching `argv_prefix` wins, so `csrutil status` (`"privileged": false`)
beats the `csrutil` catch-all. Set `"prompts": false` for a privileged command
that cannot open a dialog (`sudo -n`) — it is still attributed and recorded, just
never counted as a prompt. `confidence` is one of `observed` (seen prompting here,
evidence on file), `documented` (vendor/man page says root), `inferred`
(behaviour follows from the command class; unverified here).

When in doubt, leave `mutating: true`. A read wrongly marked mutating costs one
extra dialog; a mutation wrongly marked read-only inherits an approval it should
never have had.

## Known limits

- `prompt_opened` is a **proxy**: the broker counts the times it invoked a
  privileged command, because it cannot observe SecurityAgent directly.
- `sudo` keeps its own sudoers timestamp cache, independent of this broker.
  Consecutive `sudo` calls inside that window may show fewer real dialogs than
  the baseline counts. The SecurityAgent commands have no such cache.
- The PreToolUse gate covers **Claude Code Bash calls**. Codex sessions are
  covered by the `AGENTS.md` protocol, which is instruction-level, not enforced.
- `ambient.mode` defaults to `ledger-only`; live push stays unconfigured until a
  `notify_command` is set, and that state is reported once as a coverage gap
  rather than silently.
