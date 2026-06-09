<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Coordination — How build-loop consumes agent-rally

Build-loop is a **consumer** of the agent-rally architecture, but it currently
vendors native embedded copies of the substrate and watcher while the standalone
repos mature. Treat `agent-rally-point` and `agent-rally-watcher` as
mini-plugins inside build-loop: grouped scripts, skills, docs, tests, and thin
build-loop adapters first; full spin-out later. Cross-reference the upstream
architecture docs before changing how build-loop reads or writes events, but do
not make build-loop depend on those external repos at runtime yet.

> **Binding constitution** (verdict-gating, MECE briefs, Phase D closeout, peer-no-mutate, release-surface verification) lives in [`references/coordination-rules.md`](../../../references/coordination-rules.md). This file documents the **integration shape**, not the rules of engagement.

## Build-loop's position in the three-layer model

```
Layer 3 — CONSUMERS (build-loop, codex, claude_code, ...)
              ▲
              │  build-loop is one of many
              ▼
Layer 2 — agent-rally-watcher (embedded watcher namespace; optional listener)
              ▲
              │  build-loop CAN subscribe, OR poll Layer 1 directly
              ▼
Layer 1 — agent-rally-point (embedded substrate; channel, schema, post API)
```

Build-loop **always** uses Layer 1 (it posts events and calls `checkpoint_read`). It uses Layer 2 **opportunistically** — when a peer session is detected via Rally Point presence, the coordination polling gate spins up a `coordination_watch.py` loop or installs a watcher subscription so chunk-close, commit, and feedback events surface within seconds rather than at the next phase boundary.

Native skill entrypoints:

- `skills/agent-rally-point/SKILL.md` — substrate workflow, CLI, boundary,
  run identity, and spin-out rules.
- `skills/agent-rally-watcher/SKILL.md` — watcher workflow, JSONL sensor
  behavior, closeout, and spin-out rules.

The machine-readable boundary is `scripts/rally_point/plugin_boundary.json`.

## What build-loop posts

Every cross-session signal goes through `scripts/rally_point/post.py::post()` (the canonical writer that bumps revision *before* appending — see [`agent-rally-point/docs/SCHEMA.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/SCHEMA.md) for the field-by-field record format). Raw `append_change(...)` without a subsequent `bump_revision(...)` is a silent-no-op for peers (`feedback_codex_pass_is_gate_not_comment`).

| When                                  | `kind`       | `payload` shape                                                  | Triggered by                       |
|---------------------------------------|--------------|------------------------------------------------------------------|------------------------------------|
| Chunk closes and lands on the branch  | `phase`      | `{phase: "chunk-close", chunk_id, commit_sha, files_changed}`    | Phase 3 commit step                |
| Phase boundary (Phase 1, 4, 5)        | `phase`      | `{phase: "<phase-name>", summary, ...}`                          | Orchestrator phase entry           |
| Build run closes (Phase D)            | `phase`      | `{phase: "run-closeout", session_id, coord_file, outcomes}`      | Phase D closeout sequence          |
| Peer hands off work to a verifier     | `handoff`    | `{from_tool, to_tool, work_item, deadline_ts}`                   | Sub-step F when a peer is present  |
| Verifier returns a verdict            | `feedback`   | `{step, verdict: PASS|VARIANCE|BLOCKED, rationale, evidence}`    | After verifier runs on a step      |
| Architecture scan completes           | `arch-scan-complete` | `{digest_path, files_scanned}`                            | Phase 1 architecture baseline      |
| Dependency manifest changed           | `dep-change` | `{manifest, added, removed, session_id}`                         | Phase 3 commit step on manifest    |
| Coordination announcement / take-over | `phase`      | `{phase: "leadership", role, scope}`                             | Multi-orchestrator handoff         |

All writes are fire-and-forget — a coordination failure must never crash the build.

## What build-loop reads

### Phase 1 Assess — inbox pickup + presence check

At Phase 1 entry, the orchestrator (see `agents/build-orchestrator.md` §"Phase 1: Assess") runs:

```bash
python3 scripts/coordination_status.py --workdir "$PWD" --session-id "$SESSION_ID" --json
```

The status reports: active peers (live sessions in the channel's `sessions/`), session-ack-aware unread inbox messages (`inbox/<my-tool>.jsonl`, `inbox/all.jsonl`), task-heartbeat health when `--task-ref` is set, and any active coordination file. If `active_peers > 0` OR `inbox_unread_count > 0`, the orchestrator MUST drain the inbox before dispatching any chunk; otherwise the peer's last verdict/handoff is invisible to the new run. After acting on the inbox payload, run `python3 scripts/agent_rally.py ack-inbox --workdir "$PWD" --session-id "$SESSION_ID" --tool "$TOOL_ID" --json` so stale notes stop ringing the doorbell.

### Coordination polling gate — watcher install when a peer is present

When the Phase 1 status returns peers, build-loop installs a cheap continuous watcher:

```bash
python3 scripts/coordination_watch.py --workdir "$PWD" --session-id "$SESSION_ID" --tool claude_code --task-ref "$TASK_REF" --interval 5 --jsonl --baseline-current
```

The watcher reports revision changes, inbox deltas, and task-heartbeat health as line-delimited JSON. The orchestrator polls it before commits, before final responses, and after any 30-second work interval. When a peer posts (e.g. a verifier returns a `feedback` verdict), the orchestrator routes the response into the active coordination file's "Codex feedback log" section rather than asking the user to paste it.

For long-running work, write `agent_rally.py heartbeat --task-ref "$TASK_REF"`
at task start and at least every 10 minutes. Presence says the session is live;
task heartbeat says it is still on the claimed task.

The watcher process is OPTIONAL — if the daemon process can't start (no FS-watch support, sandboxed environment), the orchestrator falls back to per-checkpoint `coordination_status.py` polls. The same flow works; latency is higher.

### Chunk-close — checkpoint read for downstream chunks

After each chunk's commit step closes, the orchestrator runs `checkpoint_read` against this session's cursor to surface any peer events that landed during the chunk. Reactions (`reinstall`, `re-baseline`, `soft-claim`) are documented in [`agent-rally-point/docs/SCHEMA.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/SCHEMA.md). Reactions are **awareness only** — `soft-claim` warns when a peer touched files this session also plans to touch; the orchestrator decides whether to wait, rebase, or proceed.

## Peer-no-mutate rule

When a peer presence record indicates active work AND that work isn't already merged (`branch_merge_status: unmerged` AND files differ from `origin/main`), build-loop's orchestrator MUST NOT modify the same files. The rule and the merge-status pre-check are in [`references/coordination-rules.md`](../../../references/coordination-rules.md) §"Peer-no-mutate" and `feedback_verify_peer_merged_before_blocking`.

Read-only operations (Phase 1 architecture baseline, plan drafting, fact-check) are always safe and do not invoke the rule.

## Embedded mini-plugin boundary

For now, the Rally Point substrate and watcher still ship inside the
build-loop plugin, but they are treated as embedded mini-plugins with
explicit extraction edges:

| Future plugin | Embedded namespace | Build-loop compatibility entrypoints |
|---|---|---|
| `agent-rally-point` | `scripts/rally_point/**` | `scripts/agent_rally.py`, `commands/rally-point.md`, `hooks/*rally-point.sh`, `scripts/coordination_status.py`, `scripts/coordination_rally.py`, `scripts/coordination_bootstrap.py` |
| `agent-rally-watcher` | `scripts/agent_rally_watcher/**` | `scripts/coordination_watch.py` |

The machine-readable contract lives at
`scripts/rally_point/plugin_boundary.json` and is validated by:

```bash
python3 scripts/rally_point/boundary.py --repo "$PWD" --check --json
python3 scripts/agent_rally.py boundary --workdir "$PWD" --check --json
```

Boundary rule: put substrate behavior in `scripts/rally_point/**`, watcher
behavior in `scripts/agent_rally_watcher/**`, and leave build-loop files as
thin adapters. Do not import build-loop memory-store or orchestration internals
from those namespaces unless the dependency is explicitly isolated as an
adapter in the boundary manifest.

## Discovery integration (current)

Build-loop uses `scripts/rally_point/discovery_bridge.py` as the shared
channel resolver. It prefers the standalone `agent-rally-point` discovery
surface when installed and falls back to build-loop's embedded resolver only
when needed. Both native discovery and the embedded fallback default to
`~/.agent-rally-point/apps/...`; the fallback uses the local worktree-aware
`<slug>` when no native `<repo-id>` is available.

The discovery layer (see
[`agent-rally-point/docs/DISCOVERY.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/DISCOVERY.md))
provides:

```python
from agent_rally_point.discover import discover
info = discover()  # returns dict with channel_dir, channel_layout, active_revision, active_peers, ...
channel_dir = info["channel_dir"]   # canonical OR legacy-fallback path
```

Current build-loop callers route through the bridge:

1. `scripts/agent_rally.py` resolves `where`, `presence`, `handoff`, `lead`,
   and `boundary` through the shared namespace.
2. `scripts/coordination_status.py` reads status from the resolved channel
   instead of deriving a parallel channel path.
3. `scripts/rally_point/session_probe.py` and `scripts/rally_point/hooks.py`
   use the resolved channel for session-start and pre-edit hook behavior.
4. `scripts/coordination_watch.py` remains a compatibility wrapper; watcher
   behavior lives under `scripts/agent_rally_watcher/`.

## Cross-references

- **Substrate (channel format, post API, presence, lifecycle)** — [`agent-rally-point/ARCHITECTURE.md`](https://github.com/tyroneross/agent-rally-point/blob/main/ARCHITECTURE.md)
- **Record schema (all 6 kinds + payloads)** — [`agent-rally-point/docs/SCHEMA.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/SCHEMA.md)
- **Discovery protocol (manifest, CLI, `discover()`)** — [`agent-rally-point/docs/DISCOVERY.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/DISCOVERY.md)
- **Push-based daemon (consumers.toml, sinks)** — [`agent-rally-watcher/ARCHITECTURE.md`](https://github.com/tyroneross/agent-rally-watcher/blob/main/ARCHITECTURE.md)
- **Binding constitution (verdicts gating, MECE briefs, Phase D closeout)** — [`references/coordination-rules.md`](../../../references/coordination-rules.md)
- **Per-run coordination file template** — [`references/coordination-file-template.md`](../../../references/coordination-file-template.md)

## Why a separate doc

`coordination-rules.md` is the binding *constitution* — operating rule, verdict gating, MECE field list, closeout sequence. This file is the *integration map* — where build-loop sits in the three-layer architecture, what it posts, what it reads, where the path-hardcoding lives, and what the discovery migration looks like. Conceptually distinct audiences: the rules file is for any participant; this file is for build-loop's orchestrator and anyone modifying how build-loop talks to rally-point.
