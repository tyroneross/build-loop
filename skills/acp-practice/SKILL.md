---
name: build-loop:acp-practice
description: "Route, build, and verify Agent Client Protocol (ACP) clients and adapters that drive coding agents over JSON-RPC stdio. Use when choosing ACP vs a PTY for an agent, or a diff touches ACP code (paths or identifiers matching acp, agent-client-protocol, session/prompt). NOT for MCP servers (use mcp-builder) or driving a native TUI in a terminal. Covers Claude, Codex, Cursor, Gemini and OpenCode routing, startup speed, cancel/teardown reliability, session/update rendering, and the Review checklist."
version: 0.1.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# ACP Practice

ACP v1 (`protocolVersion: 1`) is the shipping protocol; v2 is a breaking draft
(`v2.0.0-alpha.9`, 2026-10-16). Facts below were checked against the spec, the
official schema `schema-v1.26.0`, and the live registry on 2026-10-21. Each
reference file names its sources; recheck anything versioned before relying on it.

## ACP or PTY

Use **ACP** when the output must be structured or rendered by us: guided or
headless modes, multi-agent fan-out, permission gating, resumable sessions, diff
review. Use a **PTY** when the user must see the agent's own TUI, or the agent
has no ACP endpoint. Do not run both for one session: the transcripts diverge.

## Routing

| Agent | Protocol | Launch |
|---|---|---|
| Claude | ACP | `npx @agentclientprotocol/claude-agent-acp@<pin>` |
| Codex | **app-server primary** (`codex app-server`); ACP option `@agentclientprotocol/codex-acp`, which itself spawns app-server | see `references/agents.md` |
| Cursor | ACP | `cursor-agent acp`; must answer its blocking `cursor/*` requests |
| Gemini | ACP | `gemini --acp` |
| OpenCode | ACP | `opencode acp` |
| Muse | its own protocol, MSP (`muse serve`) | not ACP; do not wrap it |
| Antigravity | ❓ unverified: registry ships a separate `agy_acp_server.par`, no `--acp` flag found | test before routing |

Full table, versions, and refresh command: `references/agents.md`.

## Speed

- Time `initialize`, `session/new`, and first `agent_message_chunk` as three
  separate numbers. MCP server connection time dominates `session/new`; a slow
  MCP server has blocked it for 20–35 s.
- Prewarm in two stages: (1) spawn + `initialize`, safe to pool; (2) `session/new`,
  only when cwd and MCP set are predictable.
- Key the warm pool by binary path **and version**, args, env hash, cwd, and MCP
  set, so a version bump invalidates spares.
- Prefer `session/resume` over `session/load` when the client already holds the
  history; resend `additionalDirectories` and `mcpServers` on resume.

## Reliability

- **Cancel:** send `session/cancel`, answer every pending
  `session/request_permission` with `outcome: cancelled`, then expect
  `stopReason: "cancelled"` (not an error). Keep accepting late updates: a
  deferred `available_commands_update` can arrive after the cancel response.
- **Unknown requests:** reply to any agent→client request you do not implement
  (`_*`, `cursor/ask_question`, `cursor/create_plan`) with JSON-RPC `-32601`
  immediately. An unanswered blocking request hangs the agent. Ignore unknown
  notifications.
- **Stdout is JSON-RPC only:** drop and count non-JSON lines, cap line length,
  keep a ring buffer for diagnostics, never fail the session on one bad line.
- **Teardown:** escalate `session/close` (if advertised) → close stdin → SIGTERM →
  SIGKILL, and kill the **whole process group**: adapters leave child processes
  behind.
- **Versions:** pin exact adapter versions and turn self-update off via env.
  Record `agentInfo.version` from `initialize` and compare it with the pin and
  the registry latest.
- **v2 readiness:** v2 changes turn completion, upserts, and file-change shapes.
  Flag a hand-rolled JSON-RPC client (no official SDK) as migration debt in the
  Review report; gate any v2 path behind version negotiation.

## Verification

- **Agents:** `uv run acp-tck --protocol-version 1 --report-json r.json -- <agent cmd>`
  (`agentclientprotocol/acp-tck`, experimental). It tests agents, not clients.
- **Installed agents:** `rosslabs-lsp acp doctor`, if installed (ships separately):
  versions, capabilities, `initialize` and `session/new` timings, drift against
  the registry. Not installed → record `⚠️ untested — rosslabs-lsp acp doctor absent`.
- **Clients:** record a real session transcript (NDJSON, timestamp + direction)
  and replay it through the client in tests; no official client kit exists.

## On a diff that touches ACP code

Phase 4 Review loads this skill and runs `references/review-checklist.md`. Each
item names the command or evidence that proves it; an item without evidence is
reported `⚠️ untested` with the reason, never as passed.

## References

| File | Contents |
|---|---|
| `references/review-checklist.md` | The seven Review checks and the report line |
| `references/rendering.md` | Rules for readable, customizable session output, plus theming hooks |
| `references/agents.md` | Launch command per agent, generated from `registry.json` on 2026-10-21 |
