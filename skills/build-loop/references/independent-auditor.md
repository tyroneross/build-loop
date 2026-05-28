<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Independent Commit Auditor

Single source of truth for build-loop commit/build-scope adversarial review. **Consolidated 2026-05-23** — replaces both the retired `commit-auditor` agent (chunk + build scope) and the earlier retired `sonnet-critic`. Operates on two surfaces sharing one context-gathering procedure and one verdict taxonomy:

1. **Boundary-gated hook (every commit).** A PreToolUse Bash hook in `hooks/hooks.json` invokes `scripts/audit_before_commit.py` whenever the Bash tool runs a command matching `git commit`. The orchestrator cannot skip it. Manual user commits, Codex commits, IDE commits, and build-loop commits all pass through it.
2. **Self-contextualizing.** The script gathers its own context from on-disk `.build-loop/intent.md`, `.build-loop/goal.md`, repo `CLAUDE.md` + `README.md`, the first PRD found (`docs/PRD.md` -> `docs/prd.md` -> `docs/prd/*.md` -> `.build-loop/prd.md`), canonical build-loop-memory constitution context, and the last 5 commit subjects. No upstream packet needed.
3. **LLM-grade dispatched agent.** The `independent-auditor` agent (`agents/independent-auditor.md`) is dispatched at Phase 3 chunk-close (chunk advisory) and Phase 4 Review-A (build scope). Same context procedure as the hook, plus diff range — emits a structured JSON envelope. Verdict rendered in conversation by the running Claude session.

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

## Dispatched-agent surface

For LLM-grade judgment on a specific commit or commit range, dispatch `Agent(subagent_type="build-loop:independent-auditor", ...)`. The agent uses the same context-gathering procedure as the script and renders a structured JSON envelope (with explicit `context_seen` flags and `missing_artifacts[]`). The agent is Sonnet-tier; use it for per-chunk advisory, cross-chunk reviews, and Phase 4 Review-A build-scope critique.

## How the running session should interpret a packet

When a Bash `git commit` returns with the packet appended to stderr:

1. **Read the packet sections** (Intent, Goal, PRD reference, Trajectory, etc.).
2. **Pick a verdict** explicitly in your next assistant message — the user (and any audit-trail tooling) needs to see the verdict named, not implied.
3. **If `yay`**, no further action — the commit is in.
4. **If `nay` or `suggest correction`**, do not push. State which finding triggered the verdict and either revert (`git reset HEAD~1`) or make the suggested edits and amend.
5. **If `look again`**, gather the missing artifact (read the PRD section, dispatch the escalation agent, etc.) and re-render the verdict.

The verdict belongs in the running session's transcript so future readers (and Phase 6 Learn) can see what the auditor saw and what the operator did with it.
