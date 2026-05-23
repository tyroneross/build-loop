<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Independent Commit Auditor

A boundary-gated audit that fires on every `git commit`, regardless of who initiates the commit. Distinguished from the existing `commit-auditor` agent in three ways:

1. **Boundary-gated, not orchestrator-dispatched.** A PreToolUse Bash hook in `hooks/hooks.json` invokes `scripts/audit_before_commit.py` whenever the Bash tool runs a command matching `git commit`. The orchestrator cannot skip it. Manual user commits, Codex commits, IDE commits, and build-loop commits all pass through it.
2. **Self-contextualizing.** The script gathers its own context from on-disk `.build-loop/intent.md`, `.build-loop/goal.md`, repo `CLAUDE.md` + `README.md`, the first PRD found (`docs/PRD.md` → `docs/prd.md` → `docs/prd/*.md` → `.build-loop/prd.md`), `~/.build-loop/memory/constitution.md`, and the last 5 commit subjects. No upstream packet needed.
3. **Verdict rendered in conversation.** The hook itself is a deterministic packet-builder — no LLM call from inside the hook (which would be slow and blocked). The script emits a structured packet to stderr; the running Claude session reads it and renders one of four verdicts in conversation.

## Four-verdict taxonomy

- **yay (approve)** — packet aligns with intent + constitution; ship.
- **nay (reject)** — packet contradicts intent or trips a constitution rule; do not land.
- **suggest correction** — partial alignment; name file:line edits the implementer should make before re-committing.
- **look again** — context insufficient to judge; name the missing artifact (PRD section, prior decision, test result) and gather it.

## When the hook fires

Every Bash tool invocation whose command matches `\bgit\s+commit\b`. The hook timeout is 5000ms; the script is fast (no LLM call, no network). It runs at the Bash boundary, so it fires before the commit lands — the running session can short-circuit by not actually committing if the packet shows a contradiction.

## Exit codes

| Code | Meaning | Caller behavior |
|---|---|---|
| 0 | Packet emitted, no deterministic block | Commit proceeds; running session renders a verdict in chat |
| 2 | Deterministic block (secrets file staged, merge-conflict markers in diff) | Caller / hook framework signals the commit should not proceed |
| 1 | Reserved | — |

Deterministic blocks are zero-judgment hard fails (filenames matching `*.pem` / `id_rsa*` / `id_ed25519*`, `.env*` files with credential-shaped content, or `<<<<<<< HEAD` / `=======` / `>>>>>>>` markers in the staged diff).

## Bypass

Emergency commits can bypass the audit by setting `BUILDLOOP_AUDIT_BYPASS=1` in the environment:

```bash
BUILDLOOP_AUDIT_BYPASS=1 git commit -m "emergency hotfix"
```

Each bypass is logged to `~/.build-loop/audit-bypass.log` with timestamp + cwd + reason. Bypasses are valid but visible — Phase 6 Learn can mine the log for chronic-bypass patterns.

## Escalation agent

For richer LLM-grade judgment on a specific commit or commit range, dispatch `Agent(subagent_type="build-loop:independent-auditor", ...)`. The agent uses the same context-gathering procedure as the script but renders a structured JSON envelope (with explicit `context_seen` flags and `missing_artifacts[]`) that the orchestrator can route the same way it routes `commit-auditor` verdicts. The agent is Sonnet-tier (cheap); use it for cross-chunk reviews or pre-squash gates, not for every commit.

## How the running session should interpret a packet

When a Bash `git commit` returns with the packet appended to stderr:

1. **Read the packet sections** (Intent, Goal, PRD reference, Trajectory, etc.).
2. **Pick a verdict** explicitly in your next assistant message — the user (and any audit-trail tooling) needs to see the verdict named, not implied.
3. **If `yay`**, no further action — the commit is in.
4. **If `nay` or `suggest correction`**, do not push. State which finding triggered the verdict and either revert (`git reset HEAD~1`) or make the suggested edits and amend.
5. **If `look again`**, gather the missing artifact (read the PRD section, dispatch the escalation agent, etc.) and re-render the verdict.

The verdict belongs in the running session's transcript so future readers (and Phase 6 Learn) can see what the auditor saw and what the operator did with it.
