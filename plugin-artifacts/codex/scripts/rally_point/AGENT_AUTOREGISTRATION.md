<!--
SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
SPDX-License-Identifier: Apache-2.0
-->
# Agent-tool auto-registration

Make subagents spawned via the **Agent tool** post their own rally
presence, so the roster shows them as live nested agents instead of a
bare aggregate count under the spawner.

## The gap this closes

`presence.write_presence` already models a parent/child tree (`parent` +
`spawned` fields) and `roster.build_roster` already nests children under
their spawner. What was missing: **nothing made a spawned subagent post
its own presence.** The spawner self-reports an aggregate
(`spawned: {coder: 2}`); the children themselves stay invisible — no
`session_id`, `task`, `cwd`, `branch`, or heartbeat of their own. For the
minutes a subagent runs real work, the roster cannot see it.

## Why a helper, not a hook

There is no Claude-Code hook that fires *inside* a spawned subagent's
context, and the Agent tool does not post rally presence. So
auto-registration is a **convention the spawner threads through and the
child executes as step 0**, made zero-friction by
[`agent_autoreg.py`](agent_autoreg.py). Three cooperating tiers, each
fire-and-forget:

1. **Identity inheritance** — the spawner sets env vars on each child
   (`spawn_env`) so the child self-registers with zero arguments.
2. **Prompt preamble** — the spawner prepends a one-line self-register
   directive to the child prompt (`preamble`); the child runs it first.
3. **Self-register call** — `register(...)` resolves parent / run-id /
   model / workdir from args → env → defaults, posts presence with
   `parent` linked, and returns the child session id.

Registration never raises into, or blocks, the subagent's real work;
`register` returns `""` on any failure.

## Orchestrator recipe

When dispatching subagents via the Agent tool:

```python
from rally_point import agent_autoreg

PARENT = "<my own session_id>"   # the spawner's session
RUN_ID = "<this run's id>"

for chunk in chunks:
    line = agent_autoreg.preamble(
        agent_type="coder",
        task=f"implement {chunk.name}",
        parent_session=PARENT,
        run_id=RUN_ID,
    )
    prompt = line + "\n\n" + chunk.prompt   # child self-registers as step 0
    # ... dispatch via the Agent tool with `prompt` ...
```

The embedded line is just the CLI:

```bash
python3 .../rally_point/agent_autoreg.py register \
  --agent-type coder --task "implement chunk-3" \
  --parent build-orchestrator-abc123 --run-id run-99 \
  >/dev/null 2>&1 || true
```

The child now appears nested under the spawner in
`agent_rally.py roster` with its own heartbeat, cwd, branch, and task.

### Cleanup

On child completion (or in the spawner's Phase D closeout), reap the
presence file so it doesn't linger as a "live peer":

```bash
python3 .../rally_point/agent_autoreg.py deregister --session-id "<child sid>"
```

If a child crashes without deregistering, the standard reaper
(`presence.reap_stale`, default 15 min) and the closeout sweep
(`lifecycle.reap_stale_sessions`) clean it up.

## API

| Function | Purpose |
|---|---|
| `register(*, agent_type, task=…, parent=…, run_id=…, model=…, workdir=…)` → `str` | Self-register; returns child session id (`""` on failure). |
| `deregister(session_id, *, workdir=…)` → `bool` | Reap the child's presence file. |
| `spawn_env(*, parent_session, run_id=…, model=…)` → `dict` | Identity env vars the spawner sets on children. |
| `preamble(*, agent_type, task, parent_session, run_id=…, workdir=…)` → `str` | One-line self-register directive for the child prompt. |

Identity env vars: `RALLY_PARENT_SESSION`, `RALLY_POINT_RUN_ID`
(falls back to `BUILD_LOOP_RUN_ID`), `RALLY_POINT_MODEL`.

## Notes & limits

- **Session-id scheme:** `agent:<type>-<csprng-hex>`. The `agent:` prefix
  distinguishes spawned subagents from top-level sessions in a raw
  channel listing; the CSPRNG suffix (SEC-007) avoids
  collision/forgery in the shared channel. `tool` is recorded as
  `agent:<type>` for at-a-glance origin.
- **Nesting requires a live parent.** `build_roster` nests a child only
  when its `parent` matches a live session in the same window; otherwise
  the child renders as a root. Pass the spawner's real `session_id`.
- **Self-registration over on-behalf.** The spawner *could* write child
  presence at dispatch time, but it can't supply the child's real
  pid/heartbeat, so those entries go stale immediately. Self-registration
  from inside the child gives a real, refreshing heartbeat.
- **Rust-CLI channels:** `register` writes the Python presence record
  directly (the rust `start` bridge has no `--parent`); the roster reads
  these files regardless of how the channel was resolved.
