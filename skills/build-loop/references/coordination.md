<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Coordination — How build-loop consumes agent-rally

Build-loop is a **consumer** of the agent-rally architecture. It does not own the channel, the schema, the watcher, or the discovery layer — those live in [`agent-rally-point`](https://github.com/tyroneross/agent-rally-point) (substrate) and [`agent-rally-watcher`](https://github.com/tyroneross/agent-rally-watcher) (daemon). This document describes how build-loop **uses** those layers; cross-reference the upstream architecture docs before changing how build-loop reads or writes events.

> **Binding constitution** (verdict-gating, MECE briefs, Phase D closeout, peer-no-mutate, release-surface verification) lives in [`references/coordination-rules.md`](../../../references/coordination-rules.md). This file documents the **integration shape**, not the rules of engagement.

## Build-loop's position in the three-layer model

```
Layer 3 — CONSUMERS (build-loop, codex, claude_code, ...)
              ▲
              │  build-loop is one of many
              ▼
Layer 2 — agent-rally-watcher (optional; for push-based subscribers)
              ▲
              │  build-loop CAN subscribe, OR poll Layer 1 directly
              ▼
Layer 1 — agent-rally-point (substrate; channel, schema, post API)
```

Build-loop **always** uses Layer 1 (it posts events and calls `checkpoint_read`). It uses Layer 2 **opportunistically** — when a peer session is detected via Rally Point presence, the coordination polling gate spins up a `coordination_watch.py` loop or installs a watcher subscription so chunk-close, commit, and feedback events surface within seconds rather than at the next phase boundary.

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

The status reports: active peers (live sessions in the channel's `sessions/`), unread inbox messages (`inbox/<my-tool>.jsonl`, `inbox/all.jsonl`), and any active coordination file. If `active_peers > 0` OR `inbox_unread_count > 0`, the orchestrator MUST drain the inbox before dispatching any chunk; otherwise the peer's last verdict/handoff is invisible to the new run.

### Coordination polling gate — watcher install when a peer is present

When the Phase 1 status returns peers, build-loop installs a cheap continuous watcher:

```bash
python3 scripts/coordination_watch.py --workdir "$PWD" --session-id "$SESSION_ID" --tool claude_code --interval 5 --jsonl --baseline-current
```

The watcher reports revision changes + inbox deltas as line-delimited JSON. The orchestrator polls it before commits, before final responses, and after any 30-second work interval. When a peer posts (e.g. a verifier returns a `feedback` verdict), the orchestrator routes the response into the active coordination file's "Codex feedback log" section rather than asking the user to paste it.

The watcher process is OPTIONAL — if the daemon process can't start (no FS-watch support, sandboxed environment), the orchestrator falls back to per-checkpoint `coordination_status.py` polls. The same flow works; latency is higher.

### Chunk-close — checkpoint read for downstream chunks

After each chunk's commit step closes, the orchestrator runs `checkpoint_read` against this session's cursor to surface any peer events that landed during the chunk. Reactions (`reinstall`, `re-baseline`, `soft-claim`) are documented in [`agent-rally-point/docs/SCHEMA.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/SCHEMA.md). Reactions are **awareness only** — `soft-claim` warns when a peer touched files this session also plans to touch; the orchestrator decides whether to wait, rebase, or proceed.

## Peer-no-mutate rule

When a peer presence record indicates active work AND that work isn't already merged (`branch_merge_status: unmerged` AND files differ from `origin/main`), build-loop's orchestrator MUST NOT modify the same files. The rule and the merge-status pre-check are in [`references/coordination-rules.md`](../../../references/coordination-rules.md) §"Peer-no-mutate" and `feedback_verify_peer_merged_before_blocking`.

Read-only operations (Phase 1 architecture baseline, plan drafting, fact-check) are always safe and do not invoke the rule.

## Discovery integration (planned)

Today build-loop's orchestrator and `coordination_status.py` hardcode `~/.build-loop/apps/<slug>/` as the channel root. As of `agent-rally-point` v0.2.0, the canonical channel root is `~/.agent-rally-point/apps/<slug>/` and the discovery layer (see [`agent-rally-point/docs/DISCOVERY.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/DISCOVERY.md)) provides:

```python
from agent_rally_point.discover import discover
info = discover()  # returns dict with channel_dir, channel_layout, active_revision, active_peers, ...
channel_dir = info["channel_dir"]   # canonical OR legacy-fallback path
```

The orchestrator SHOULD call `discover()` on session start instead of hardcoding the legacy path. Implementation steps (tracked separately from this doc):

1. Replace hardcoded `Path("~/.build-loop/apps").expanduser() / slug` lookups with the `discover()` result in `coordination_status.py`, `coordination_watch.py`, and `scripts/rally_point/post.py`'s default-channel resolution.
2. Honor the discovery result's `channel_layout` field — when it returns `"legacy"`, build-loop should still operate normally (the legacy fallback chain handles backward compatibility transparently); when it returns `"canonical"`, all new event writes land under the canonical root.
3. Emit a one-shot warning when `channel_layout == "legacy"` so the operator knows a migration is available.

The migration is **read-only** for now — `discover()` resolves the path; it does not move legacy channels. Hard-coded path removal is a separate PR (tracked by the build-loop roadmap, not this document).

## Cross-references

- **Substrate (channel format, post API, presence, lifecycle)** — [`agent-rally-point/ARCHITECTURE.md`](https://github.com/tyroneross/agent-rally-point/blob/main/ARCHITECTURE.md)
- **Record schema (all 6 kinds + payloads)** — [`agent-rally-point/docs/SCHEMA.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/SCHEMA.md)
- **Discovery protocol (manifest, CLI, `discover()`)** — [`agent-rally-point/docs/DISCOVERY.md`](https://github.com/tyroneross/agent-rally-point/blob/main/docs/DISCOVERY.md)
- **Push-based daemon (consumers.toml, sinks)** — [`agent-rally-watcher/ARCHITECTURE.md`](https://github.com/tyroneross/agent-rally-watcher/blob/main/ARCHITECTURE.md)
- **Binding constitution (verdicts gating, MECE briefs, Phase D closeout)** — [`references/coordination-rules.md`](../../../references/coordination-rules.md)
- **Per-run coordination file template** — [`references/coordination-file-template.md`](../../../references/coordination-file-template.md)

## Why a separate doc

`coordination-rules.md` is the binding *constitution* — operating rule, verdict gating, MECE field list, closeout sequence. This file is the *integration map* — where build-loop sits in the three-layer architecture, what it posts, what it reads, where the path-hardcoding lives, and what the discovery migration looks like. Conceptually distinct audiences: the rules file is for any participant; this file is for build-loop's orchestrator and anyone modifying how build-loop talks to rally-point.
