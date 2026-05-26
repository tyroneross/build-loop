---
name: agent-rally-point
description: "Use when coordinating build-loop with peer coding agents, checking Rally Point presence/inbox state, posting handoffs or feedback, validating the embedded Rally Point boundary, or changing the future agent-rally-point spin-out surface."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Agent Rally Point

Build-loop ships a native embedded `agent-rally-point` capability while the
standalone repo matures. Treat it as a mini-plugin inside build-loop: keep the
substrate logic, docs, tests, and thin build-loop adapters grouped so spin-out
is mechanical later.

## Native Surface

| Purpose | Path |
|---|---|
| Substrate package | `${CLAUDE_PLUGIN_ROOT}/scripts/rally_point/` |
| Host-neutral CLI | `${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py` |
| Slash command | `${CLAUDE_PLUGIN_ROOT}/commands/agent-rally-point.md` |
| Hook adapters | `${CLAUDE_PLUGIN_ROOT}/hooks/session-start-rally-point.sh`, `${CLAUDE_PLUGIN_ROOT}/hooks/pre-edit-rally-point.sh` |
| Build-loop adapters | `${CLAUDE_PLUGIN_ROOT}/scripts/coordination_status.py`, `${CLAUDE_PLUGIN_ROOT}/scripts/coordination_rally.py`, `${CLAUDE_PLUGIN_ROOT}/scripts/coordination_bootstrap.py` |
| Boundary manifest | `${CLAUDE_PLUGIN_ROOT}/scripts/rally_point/plugin_boundary.json` |

## Required Orchestrator Contract

At the Phase 1 preamble, before the first Rally Point write, the orchestrator
must generate or resume the durable run identity:

```python
from scripts.rally_point.build_loop_id import generate_or_resume

execution = generate_or_resume(
    workdir=Path.cwd(),
    tool="<tool-id>",
    session_id="<session-id>",
)
```

`session_id` is the ephemeral host session. `run_id` is caller-chosen legacy
provenance. `build_loop_id` is the durable human-visible run identity and is
surfaced on Rally Point records as top-level `build_loop_id` and
`build_loop_run_label`.

## Operating Rules

- Resolve the channel through `scripts/rally_point/discovery_bridge.py` or
  `scripts/agent_rally.py where`; do not hardcode legacy channel roots.
- Write cross-session events through `scripts/rally_point/post.py::post()`;
  do not append directly to `changes.jsonl`.
- Keep `build_loop_id` separate from `producer_metadata`.
- Keep substrate files independent from build-loop memory/orchestration
  internals. If a build-loop-specific integration is needed, put it in a thin
  adapter named in `plugin_boundary.json`.
- Use stable tool ids: `claude_code`, `codex`, `cursor`, `gemini`, or another
  lowercase id agreed by the adapter.

## Commands

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py status --workdir "$PWD" --session-id "$SESSION_ID" --json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py boundary --repo "${CLAUDE_PLUGIN_ROOT}" --check --json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rally_point/boundary.py --repo "${CLAUDE_PLUGIN_ROOT}" --check --json
```

Use `/agent-rally-point status` for the same sensor from Claude Code.

## Validation

```bash
uv run pytest scripts/test_build_loop_id.py scripts/rally_point/test_boundary.py scripts/rally_point/test_orchestrator_contract.py
python3 scripts/agent_rally.py boundary --repo "$PWD" --check --json
```

## Spin-Out Rule

When extracting to the standalone plugin, copy the namespaced substrate,
skill, command, hooks, tests, and docs named in `plugin_boundary.json`.
Build-loop should then keep only the thin adapters that call the standalone
package.
