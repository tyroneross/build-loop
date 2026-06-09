<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Auto-invoke coordination — three trigger-point protocol

Extracted from `agents/build-orchestrator.md` §"Auto-invoke coordination". The agent body keeps a one-line summary + a pointer here. This file is the canonical source for the trigger-point branching pseudocode, idempotency rules, and token-budget rationale. See also `references/multi-session-coordination.md`, `references/rally-point-protocol.md`, and `references/coordination-rules.md`.

## Summary

Coordination is auto-invoked at three trigger points — Phase 1 Assess preamble, Phase 3 chunk-close, and Phase 4 Review-A — using one ~100-token `coordination_status.py` poll per trigger. Solo runs incur the poll cost and nothing else (no coord file written, no presence-handoff post). Peer runs auto-bootstrap a coord file from `references/coordination-file-template.md`, write own presence, post `kind=handoff`, and flip the orchestrator's internal `mode=coordinated`. The user-facing `/agent-rally-point` slash command exposes the same primitives manually (`status` / `init` / `docs`).

## `channel_dir` vs `coord_file`

They are different artifacts.

- **`channel_dir`** is **GLOBAL** — lives under the channel returned by `scripts/rally_point/discovery_bridge.resolve(...)`, defaulting to `~/.agent-rally-point/apps/<repo-id-or-slug>/`. It is auto-derived from cwd, worktree- and clone-independent. Every Rally Point session for a given repo joins the same `channel_dir`.
- **`coord_file`** is **PER-TOPIC and OPTIONAL** — lives repo-local at `.build-loop/coordination/<topic>.md`, scoped to one cross-session conversation, created on demand by `coordination_bootstrap.py`.

The canonical discovery command is `python3 scripts/agent_rally.py where` (bare-path or `--json`); the plain-text `coordination_status.py` output also leads with `channel: <channel_dir>` as its first line so a fresh agent sees the answer without needing to know the resolver. When `agent-rally-point` is installed, both surfaces **delegate** channel resolution to its `discover()` (the protocol-of-record — canonical→legacy fallback chain — see `agent-rally-point/docs/DISCOVERY.md`); the JSON envelopes carry `resolved_via: "agent-rally-point" | "build-loop-internal"` so callers can tell which path produced the answer.

## Trigger points

All three follow the same branching pseudocode:

1. **Phase 1 Assess preamble** — after presence is written, before architecture baseline dispatches.
2. **Phase 3 chunk-close** — after the per-chunk commit step closes and before the next chunk dispatches.
3. **Phase 4 Review-A** — before independent-auditor dispatches at build scope.

## Branching pseudocode (executed at each trigger point)

```python
# Cheap poll (always)
status = run_cli(
    "python3", "scripts/coordination_status.py",
    "--workdir", ".",
    "--session-id", session_id,
    "--coordination-file", active_coord_file_or_none,
    "--json",
)
peers = status["active_peers"]

if not peers and not status.get("coordination_file"):
    mode = "solo"  # no further action; downstream phases run normal solo path
else:
    coord_path = status.get("coordination_file")
    if coord_path is None:
        # Peers detected, no active coord file -> bootstrap
        run_cli(
            "python3", "scripts/coordination_bootstrap.py",
            "--workdir", ".",
            "--topic", f"{run_slug}-{date}",
            "--scope", scope_one_liner,
            "--session-id", session_id,
            "--json",
        )
        # bootstrap writes own presence + posts kind=handoff internally
    else:
        # Existing coord file -> join (write presence + post joined-existing-coord)
        from scripts.rally_point.presence import write_presence
        from scripts.rally_point.post import post
        write_presence(channel_dir, session_id=session_id, ...)
        post(channel_dir=channel_dir, kind="phase",
             payload={"phase": "joined-existing-coord", "coord_file": coord_path, ...})
    mode = "coordinated"

# Coordinated mode: subsequent dispatches honor verdict-gating per coordination-rules.md
```

## Token budget

Solo mode is `status.json` poll only (~100 tokens × 3 triggers = ~300 tokens/run). Coordinated mode adds the bootstrap call (~200 tokens once per run, idempotent) + per-handoff post (~50 tokens). Net negligible vs. the cost of an unsurfaced peer collision.

## Idempotency

`coordination_bootstrap.py` is idempotent — if the coord file already exists, it writes presence + posts a `phase=joined-existing-coord` record instead of overwriting. Two orchestrators bootstrapping at the same moment converge on one coord file; the second posts a join record.

## Path-cutover note

This protocol uses build-loop's current convention `.build-loop/coordination/<topic>.md`. The standalone `agent-rally-point` CLI (sprint 3 cutover, v1.0) will rename this to `.agent-rally-point/coordination.md`. When that ships, the bootstrap helper switches to the rally-point CLI; the trigger-point branching logic is unchanged.

## User-facing manual invocation

`/agent-rally-point status` runs the same poll; `/agent-rally-point init` runs the same bootstrap. The slash command is documented at `commands/rally-point.md`.

## Phase-1 trigger detail

After presence is written and before architecture baseline dispatches, run the branching pseudocode above. Solo mode → continue normally. Peer-detected mode → bootstrap or join coord file, set `mode=coordinated`, downstream dispatches honor verdict gating.

## Phase-3 trigger detail

After the commit step closes and before the next chunk dispatches, run the branching pseudocode above. When `mode=coordinated`, poll `coordination_status.py --coordination-file <active>` for new peer verdicts; pause dispatch on `status: blocked` until unresolved verdicts clear. When still `mode=solo`, re-check active peers (a peer session may have joined mid-run); on transition to coordinated, bootstrap or join per the same pseudocode.

## Phase-4 trigger detail

Run before independent-auditor dispatches at build scope. On `mode=coordinated`, ensure all per-chunk verdicts in the active coord file are PASS or resolved-VARIANCE before proceeding (a `verification-pending` chunk blocks build-scope critique).
