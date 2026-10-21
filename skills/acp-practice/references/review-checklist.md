<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# ACP Review checklist

Run in Phase 4 Review when the build diff touches ACP code (trigger in
`skills/build-loop/references/phase-4-review.md` Sub-step A, step 1c). The
orchestrator runs the commands; reviewers without Bash receive the outputs.

**Evidence rule.** Each item passes only on the named evidence: command output,
a test name with its result, or a transcript path. Agent self-report and "the
code looks right" do not count. When the evidence cannot be produced, mark the
item `⚠️ untested — <reason>` and carry it to the Review-G report.

`rosslabs-lsp acp …` commands ship separately. If `command -v rosslabs-lsp`
fails, or `rosslabs-lsp acp --help` has no `doctor`, use the fallback column.

| # | Check | Passes when | Primary evidence | Fallback evidence |
|---|---|---|---|---|
| 1 | `initialize` result matches the baseline | `protocolVersion` still `1`; any capability, `authMethods`, or `agentInfo.version` change is listed in the report and intended | `rosslabs-lsp acp doctor --json` diffed against the last recorded output | Pipe one `initialize` request into the agent command; diff the JSON result against the previous build's capture |
| 2 | Cancel mid-tool ends the turn cleanly | Response carries `stopReason: "cancelled"` within the grace period (2 s per stage, the TCK default), and every pending `session/request_permission` was answered `cancelled` | A client test that cancels during a tool call, with its name and pass result | `uv run acp-tck --protocol-version 1 --report-json r.json -- <agent cmd>`: cancel tests green in `r.json` (agent side only) |
| 3 | Resume after a kill restores the session | After SIGKILL of the process group, respawn → `initialize` → `session/resume` (or `session/load`) returns the same `sessionId`, and history is not duplicated | A test that kills and resumes, with its result | Recorded transcript of the kill/resume sequence, attached by path |
| 4 | Non-JSON stdout does not kill the session | A session fed a stray log line on stdout keeps running; the drop counter increments | A test injecting a non-JSON line, with its result | Replay a recorded transcript with one line replaced by plain text; the session survives |
| 5 | Unknown requests get an error reply | An unknown `_foo` request and `cursor/ask_question` each receive a JSON-RPC error (`-32601`) in the transcript, and the turn continues | A test sending both, with its result | `git grep -n "32601"` in the client, plus the code path that sends it for any unmatched method |
| 6 | Adapter version is pinned, self-update off | Launch command names an exact version (`pkg@x.y.z` or a versioned binary); the agent's auto-update env var is set when the registry lists one | `rosslabs-lsp acp doctor --json`: no drift against the pin | `git grep -nE "claude-agent-acp\|codex-acp\|--acp\| acp\b"` in the launch config; compare against `references/agents.md` |
| 7 | A recorded transcript replays cleanly | Replaying a real session through the changed client renders without errors and produces the same tool, plan, and diff entries as the original run | Replay test name, transcript path, and result | ❓ no fallback: mark `⚠️ untested — no transcript recorded` and open a follow-up to record one |

Add these when they apply:

- **v2 debt.** If the diff adds or edits hand-rolled JSON-RPC framing instead of an
  official SDK (`agent-client-protocol` crate, `@agentclientprotocol/sdk`,
  Python `agent-client-protocol`), report it as v2 migration debt.
- **Capability honesty.** If the client changes advertised `fs`, `terminal`, or
  `elicitation` capabilities, confirm each advertised method is implemented.

## Report line

One line per item in Review-G:

```
ACP-<n> <check> — ✅ <evidence path or test name> | ⚠️ untested — <reason> | ❌ <observed failure>
```

Example: `ACP-2 cancel mid-tool — ✅ tests/acp_cancel.rs::cancel_during_tool passed (stopReason=cancelled, 1 pending permission answered)`.

A `❌` routes to Iterate like any failed grader. `⚠️ untested` does not block,
but Review-G must list it.

Sources: research packet 2026-10-21, build-loop recommendation 2; cancel and
stop-reason rules from the [prompt-turn spec](https://agentclientprotocol.com/protocol/prompt-turn);
TCK tiers and 2 s grace from [acp-tck](https://github.com/agentclientprotocol/acp-tck).
