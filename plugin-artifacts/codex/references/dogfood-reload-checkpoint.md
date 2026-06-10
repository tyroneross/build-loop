<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Dogfood Reload Checkpoint

Use a reload checkpoint whenever build-loop changes the runtime that active
agents are using. The checkpoint makes the run stop at a safe boundary, reload
or restart onto the validated build, prove runtime identity, then continue from
Rally.

## Trigger

Run the checkpoint after a validated stage touches any runtime surface:

- `skills/*/SKILL.md`, `agents/*.md`, `commands/*.md`, hooks, plugin manifests,
  MCP config, or plugin install/cache/version behavior.
- Rally Point integration, watcher/status/heartbeat code, leadership, inbox, or
  room resolution behavior.
- Memory ingestion/recall/bootstrap/research-trigger paths.
- Self-recursive detector, per-commit mode, or self-modification safety gates.

Do not force a reload checkpoint for ordinary app code or docs that do not
change the running build-loop runtime.

Detect the surface first:

```bash
python3 scripts/dogfood_reload_checkpoint.py detect \
  --changed-file skills/build-loop/SKILL.md \
  --changed-file scripts/rally_point/task_heartbeat.py
```

## Protocol

1. Finish and validate the current stage.
2. Create the checkpoint:

   ```bash
   python3 scripts/dogfood_reload_checkpoint.py create \
     --workdir "$PWD" \
     --checkpoint-id "reload-<commit>" \
     --commit "<commit>" \
     --branch "$(git rev-parse --abbrev-ref HEAD)" \
     --changed-file "<runtime-file>" \
     --expect-tool claude_code \
     --expect-tool codex \
     --instructions "Claude Code: restart with claude --plugin-dir <path-to>/build-loop; Codex: use source scripts from this checkout; run rally next/status after reload."
   ```

3. Post the checkpoint path and instructions to Rally using
   `scripts/agent_rally.py handoff` or the native Rally CLI.
4. Every participating terminal stops at the boundary, reloads/restarts, and
   ACKs:

   ```bash
   python3 scripts/dogfood_reload_checkpoint.py ack \
     --workdir "$PWD" \
     --checkpoint-id "reload-<commit>" \
     --tool codex \
     --session-id "$SESSION_ID" \
     --runtime-root "$PWD" \
     --runtime-commit "$(git rev-parse --short HEAD)" \
     --reload-method "source-checkout" \
     --rally-next-status "proceed_solo"
   ```

5. Continue only when `status.ready == true`:

   ```bash
   python3 scripts/dogfood_reload_checkpoint.py status \
     --workdir "$PWD" \
     --checkpoint-id "reload-<commit>"
   ```

If a terminal is unmanaged, stale, or cannot ACK, record the fallback instead
of waiting silently:

```bash
python3 scripts/dogfood_reload_checkpoint.py fallback \
  --workdir "$PWD" \
  --checkpoint-id "reload-<commit>" \
  --tool claude_code \
  --decision continue_solo \
  --reason "lead lease expired; no active peer in Rally status"
```

Fallback decisions are limited to `reassign`, `defer`, or `continue_solo`.

## Host Reload Boundaries

Claude Code:

- Working-tree dogfood path: `claude --plugin-dir <path-to>/build-loop`.
- `SKILL.md` edits may hot-reload with `/reload-plugins`.
- Hook, agent, command, MCP, and manifest changes require `/reload-plugins` or a
  fresh session. Prefer a fresh session for self-recursive stage boundaries.

Codex / Rally:

- Use scripts from the source checkout when validating source-built behavior.
- Do not assume a globally installed `rally` or plugin cache is current.
- ACK with the source path, commit hash, and the result of the current
  `agent_rally.py status` or `rally next --tool <tool> --json` check.

## Report Requirement

Every self-recursive stage that changes runtime behavior states one of:

- `reload_checkpoint: ready` with checkpoint id, ACKed tools, runtime roots, and
  commit.
- `reload_checkpoint: fallback` with the missing tool and fallback decision.
- `reload_checkpoint: not_required` with the detector output proving no runtime
  surface changed.
