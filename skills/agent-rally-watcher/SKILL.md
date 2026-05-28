---
name: agent-rally-watcher
description: "Use when listening for Rally Point changes, wiring coordination watchers, debugging watch-loop behavior, or changing the future agent-rally-watcher spin-out surface."
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Agent Rally Watcher

Build-loop ships a native embedded `agent-rally-watcher` capability while the
standalone watcher repo matures. Treat it as a mini-plugin inside build-loop:
watcher behavior lives in one namespace, and build-loop keeps compatibility
entrypoints thin.

## Native Surface

| Purpose | Path |
|---|---|
| Watcher package | `${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally_watcher/` |
| Compatibility CLI | `${CLAUDE_PLUGIN_ROOT}/scripts/coordination_watch.py` |
| Status dependency | `${CLAUDE_PLUGIN_ROOT}/scripts/coordination_status.py` |
| Manual command | `${CLAUDE_PLUGIN_ROOT}/commands/agent-rally-point.md` `watch` subcommand |
| Boundary manifest | `${CLAUDE_PLUGIN_ROOT}/scripts/rally_point/plugin_boundary.json` |

## Operating Rules

- Keep watch-loop logic under `scripts/agent_rally_watcher/`.
- Keep `scripts/coordination_watch.py` as a compatibility wrapper.
- Watchers emit compact JSONL transition events; they do not make decisions,
  mutate code, stage files, commit, or resolve verdicts.
- Prefer short interactive watchers for active work. Durable always-on
  watchers belong in launchd/systemd packaging, not ad hoc shell backgrounding.
- Stop watchers started by the current run before final closeout.

## Commands

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/coordination_watch.py \
  --workdir "$PWD" \
  --session-id "$SESSION_ID" \
  --tool "$TOOL_ID" \
  --interval 5 \
  --jsonl \
  --baseline-current
```

For one-shot validation:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/coordination_watch.py \
  --workdir "$PWD" \
  --session-id watcher-smoke \
  --tool codex \
  --iterations 1 \
  --jsonl
```

## Validation

```bash
uv run pytest scripts/test_coordination_status.py scripts/rally_point/test_session_probe.py
python3 scripts/coordination_watch.py --workdir "$PWD" --session-id watcher-smoke --tool codex --iterations 1 --jsonl
python3 scripts/agent_rally.py boundary --repo "$PWD" --check --json
```

## Spin-Out Rule

When extracting to the standalone watcher plugin, copy
`scripts/agent_rally_watcher/`, this skill, watcher tests, and the
compatibility contract named in `plugin_boundary.json`. Build-loop should then
keep `coordination_watch.py` as a thin adapter to the standalone package.
