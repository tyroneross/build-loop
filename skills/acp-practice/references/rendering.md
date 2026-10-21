<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Rendering ACP sessions

Rules for turning a `session/update` stream into output a person can read and
follow. Each rule names the update kind it consumes and the client it was learned
from. The synthesis is inferred from those clients (❓ TAG:INFERRED); the update
kinds are spec facts from the v1 schema, checked 2026-10-21.

## Rules

1. **Fold finished tool calls; keep failures open.** Render each `tool_call` as
   one header: `[status icon] kind-verb title (location)`. Fold the body when
   `status` becomes `completed`; keep it open while `in_progress` or `failed`.
   Folding hides the body only, so the header and status icon stay visible.
   Truncate long `execute` titles and show the full command in the body.
   ([agentic.nvim](https://github.com/carlos-algms/agentic.nvim))
2. **Pin the plan as a checklist.** A `plan` update replaces the whole plan
   ([spec](https://agentclientprotocol.com/protocol/agent-plan)): discard the old
   one, render `entries[]` as a pinned checklist with a progress fraction (3/7),
   not as a chat message. Plan operations (`plan_update`) are still a draft.
3. **Show thoughts dimmed and collapsed.** `agent_thought_chunk` text renders in a
   dimmed style, collapses after the turn, and keeps a "Thought for Ns" header.
   ([agentic.nvim](https://github.com/carlos-algms/agentic.nvim),
   [agent-shell](https://github.com/xenodium/agent-shell))
4. **Inline diff stats; review on demand.** Show `+12 −3 path` inline for each
   `diff` content block; expand to unified or split view with syntax highlighting.
   Collect a turn's edits into an edited-files strip with keep/reject per file or
   hunk. Prefer the AIR `diffPatch` when the agent sends one.
   ([Zed agent panel](https://zed.dev/docs/ai/agent-panel))
5. **One-key permissions.** `session/request_permission` blocks the turn. Show the
   four option kinds (`allow_once`, `allow_always`, `reject_once`,
   `reject_always`) with distinct icons on number keys 1–4, plus what the call
   touches (`locations`, the command). Cancel answers every pending request
   `cancelled`. ([agentic.nvim](https://github.com/carlos-algms/agentic.nvim))
6. **Status line, always visible.** Agent and model, mode, busy spinner with
   elapsed time, context % and cost from `usage_update`, and session title from
   `session_info_update`. Render config options (`config_option_update`) as menus.
   ([agent-shell](https://github.com/xenodium/agent-shell),
   [Zed](https://zed.dev/docs/ai/agent-panel))
7. **Coalesce chunks per message.** Buffer `agent_message_chunk` by `messageId`
   and flush on a frame tick (~150 ms). Parse Markdown incrementally and redraw
   only the changed region; never re-render the transcript per chunk.
   ([Toad](https://github.com/batrachianai/toad); flicker claim ⚠️ via
   [Simon Willison](https://simonwillison.net/2025/Jul/23/announcing-toad/))
8. **Navigate between tool calls and edits.** Keys jump to the next or previous
   tool call, failed call, and edit (agentic.nvim uses `]t` / `[t`); copy a turn
   as Markdown. ([agentic.nvim](https://github.com/carlos-algms/agentic.nvim),
   [Zed](https://zed.dev/docs/ai/agent-panel))

Also from the same clients: embed `terminal` content live (tail N lines, exit
status in the header), and show errors as durable entries with a recovery action
rather than toasts.

## Theming hooks

A renderer should expose these so output can be customized without forking it:

- **Fold defaults per kind:** tool call, thought, user message, plan; plus
  toggle-all. ([agent-shell](https://github.com/xenodium/agent-shell))
- **Icon set:** per tool `kind`, per `status`, per permission option kind.
- **Style tokens, not hard-coded colors:** dimmed (thoughts), success, failure,
  diff add/remove, muted metadata, each resolvable for light and dark themes.
- **Density:** full, compact, or prompt-only.
  ([Toad](https://github.com/batrachianai/toad))
- **Status-line segments:** which segments show and in what order.
- **Event callbacks:** `on_request_permission`, `on_file_edit`,
  `on_session_update`. ([agentic.nvim](https://github.com/carlos-algms/agentic.nvim))
- **Per-project overrides** read from a theme file beside the project config.
