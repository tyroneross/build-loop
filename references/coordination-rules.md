# Coordination Rules (Binding Constitution)

**Audience:** Any agent participating in a multi-session build-loop run — Claude Code orchestrator, Codex verifier, peer Claude session, CI, headless host.

**Status:** Binding. Every rule below was codified from a concrete prior-run failure; cross-reference cited next to each rule.

This file is the durable source of truth for **how peers coordinate**. It replaces tone-suggesting "should" framings with operational rules that have automated enforcement where possible. New coordination files (`.build-loop/coordination/<topic>.md`) start from `references/coordination-file-template.md` and inherit this constitution by reference; per-run files MUST NOT contradict it.

---

## Operating Rule (verdicts are gating, not advisory)

**Claude does not proceed past a step marked `verification-pending` until the latest verifier feedback entry for that step is one of:**

- `PASS` — acceptance criteria verified end-to-end.
- `VARIANCE` that has been resolved (Claude fixed the variance, documented non-acceptance with rationale, OR escalated to the user with explicit decision).
- Explicit user override (recorded in the coord file under "Codex feedback log" or in `state.json.userOverrides[]`).

A `VARIANCE` left unresolved blocks the next step. A `BLOCKED` entry (verifier could not verify because evidence is missing) requires the producing peer to supply the missing evidence before the next step dispatches.

**Why binding (not advisory):** the 2026-05-20 audit-execution run found that even when a fresh verifier session reads the coord file, the default reading of "verifier" leans advisory. The operating rule must be stated up-front in the coord file and re-stated in the brief sent to the verifier. Memory citation: `feedback_codex_pass_is_gate_not_comment`.

**Detection:** `python3 scripts/coordination_status.py --workdir . --session-id <id> --coordination-file <path> --json` — `unresolved: []` means safe to advance; non-empty array means hold and resolve. See [Cheap detection](#cheap-detection-at-step-boundaries) below.

---

## Channel & Rally Point

**Every cross-session signal goes through Rally Point using the canonical `scripts/rally_point/post.py` `post()` helper.** Raw `append_change(...)` without a subsequent `bump_revision(...)` is a silent-no-op for consumers — the record lands on disk but no peer's `checkpoint_read(...)` ever surfaces it because their cursor still matches the unchanged revision.

```python
from scripts.rally_point.post import post
from pathlib import Path
channel = Path("~/.build-loop/apps/build-loop").expanduser()
post(
    channel_dir=channel,
    kind="feedback",        # or "phase", "commit", "dep-change", "handoff", "arch-scan-complete"
    tool="codex",           # or "claude_code", "gemini_cli", etc.
    model="gpt-5",
    run_id="<run-id>",
    app_slug="build-loop",
    payload={"step": "<id>", "verdict": "PASS", "evidence": {...}, "impact": "...", "requested_action": "..."},
)
```

`post()` bumps the revision FIRST, then appends the record. That ordering guarantees readers who see the new revision can always find the corresponding record (no race where revision is ahead of the log).

**Channel scope (worktree- and clone-independent):** `~/.build-loop/apps/<slug>/` where `slug` is derived from `git rev-parse --git-common-dir` via `scripts/rally_point/channel_paths.app_slug(cwd)`. The main checkout, every worktree, and every clone of the same canonical repo share ONE channel. Different canonical repos get different channel directories (cross-repo isolation). Slug collisions across two different repos with the same basename are mitigated by `_safe_project_tag` but basename collision remains possible — accept it; the alternative scoping (per-coord-file channel) loses cross-run pattern memory.

**Anti-pattern (silent no-op):**

```python
# Never do this — readers' checkpoint_read returns changed: false
from scripts.rally_point.changes import append_change
append_change(channel_dir, record)  # forgot bump_revision; record invisible
```

Memory citation: `feedback_post_helper_prevents_revision_bump_bug`.

---

## Trust model (unauthenticated channel; advisory leadership lease)

**The coordination channel is unauthenticated and trusted-local-peers-only.** `changes.jsonl`, `presence/`, `rally/lead.json`, and the coordination markdown all live under `~/.build-loop/apps/<slug>/` with ordinary user-account file permissions. Any process running as the same local user can append a change record, write a presence file, or claim/transfer the leadership lease. There is no signing, no authentication, and no identity verification — and there should not be: build-loop is a local single-user developer tool, so a cryptographic trust layer would be disproportionate to the threat.

What this means in practice:

- **Change-record payloads are untrusted free text.** A buggy or hostile channel writer can put arbitrary text — including prompt-injection content — into a `payload` field. Records flow into orchestrator LLM context via `checkpoint_read` `new_changes[]` and `coordination_status` `new_changes` / `open_escalations`. **Mitigation (SEC-002):** the consume boundary sanitizes every record before surfacing — `scripts/rally_point/checkpoint.sanitize_change_for_surface()` keeps only known structured metadata keys and length-caps every free-text string. The raw `changes.jsonl` log stays immutable and untouched; only the *surfaced projection* is sanitized. Reactions (`dep-change`, `arch-scan-complete`, `soft-claim`) are derived from raw records first, because they read only the structured `kind` field.

- **The leadership lease is advisory coordination, not access control (SEC-003).** Every mutating call in `scripts/rally_point/leadership.py` (`claim_lead`, `renew_lease`, `transfer_lead`, `relinquish_lead`) trusts a caller-supplied `session_id`. `claim_lead` succeeds for anyone whenever the lease is absent or expired; `renew`/`transfer`/`relinquish` "authorize" only by string-matching `session_id` against the world-readable `lead.json`. Any local process that reads `lead.json` learns the incumbent's `session_id` and can forge a renew, transfer, or relinquish. **The orchestrator MUST NOT gate an irreversible action on a lead claim** — a lead claim answers "who is coordinating" for cooperating peers, not "who is authorized". The proportionate control is observability: every `claim_lead` / `transfer_lead` / `relinquish_lead` emits a stderr audit line (`[rally-point audit] ...`) recording the requesting tool and `run_id`, in addition to the durable `lead-*` record in `changes.jsonl`, so an unexpected lease mutation is visible after the fact.

Threats this model does NOT cover (out of scope by design): a hostile process running as the same user, a compromised local account, or a multi-tenant host. Those are the operating system's responsibility, not the coordination channel's.

---

## Cheap detection at step boundaries

**Poll `coordination_status.py` BEFORE any step-boundary decision.** Costs ~100 tokens; prevents stale-state recommendations that cost full plan rewrites (~5K tokens).

```bash
python3 scripts/coordination_status.py \
  --workdir . \
  --session-id <my-session-id> \
  --owned-file <path>... \
  --coordination-file .build-loop/coordination/<active-coord-file>.md \
  --json
```

**Always pass `--coordination-file` explicitly.** The default-pick heuristic resolves to safe candidates (active.json pointer → oldest `audit-execution-*.md` → oldest direct markdown), but explicit beats implicit. Fresh handoff stubs are often newer than the run ledger they point at; relying on default-pick has misfired before.

**Step-boundary triggers (poll BEFORE each):**

1. Recommending next steps to the user.
2. Dispatching a subagent.
3. Committing (any commit).
4. Bumping plugin version.
5. Archiving / deleting files.
6. Editing a shared / no-touch-zone file.
7. Transitioning a step from `verification-pending` to `done`.

Between triggers, no polling is needed only when there is no active peer, no
active coord file, and no tool inbox message. When a host is waiting on an
async peer response, has an active peer, or has an inbox message, keep a cheap
watcher running:

```bash
python3 scripts/coordination_watch.py --workdir "$PWD" --session-id "$SESSION_ID" --tool "$TOOL_NAME" --interval 5 --jsonl --baseline-current
```

Use stable tool ids (`claude_code`, `codex`, `cursor`, etc.) so targeted
`inbox/<tool>.jsonl` messages route cleanly. Broadcast messages live in
`inbox/all.jsonl`; every tool's read path includes that file in addition to
its direct inbox. Status `clear` → proceed; status `warn` → review peer
overlap + dirty files; status `blocked` → resolve unresolved verdicts before
any of the above. Memory citation:
`feedback_poll_channel_at_step_boundaries`, `feedback_script_first_coordination_checks`.

---

## MECE Packets (every write-handoff requires all four)

**Every implementation handoff to a peer MUST spell out four elements: `owns / does-not-own / interface-contract / integration-checkpoint`.** Anything less is "informational handoff" — produces drift, two writers on the same file, ambiguous "done" definitions.

| Element | What it answers | Example |
|---|---|---|
| **Owns** | Which files/scopes may the peer write? | `scripts/coordination_status.py`, `scripts/test_coordination_status.py` |
| **Does not own** | Which files/scopes must the peer NOT touch? | any agent body; any coord-file content |
| **Interface contract** | What shape does the deliverable take? (schema, format, exit code, location) | CLI `--json` returns `{status, latest_verdicts, ...}`; exit 0 clear / 1 warn / 2 blocked |
| **Integration checkpoint** | How does Claude verify the handoff landed and how does it plug back in? | regression test passes; orchestrator parses returned JSON; entry appears in coord file |

**Enforcement:** `python3 scripts/brief_mece_validator.py --brief-file <path> --json`. Exit 0 → all four present. Exit 1 → at least one missing; orchestrator surfaces a `[warn]` and may still dispatch (C-FLOW pattern — non-blocking lint). The orchestrator wires this lint into every `Agent(subagent_type=..., ...)` dispatch site for peer-handoff briefs.

**Carve-out:** pure-read handoffs ("go look at this and tell me what you find") skip MECE. All write-handoffs need all four. Memory citation: `feedback_handoffs_require_mece_packets`.

---

## Verification of release surface

**Verifying a release means checking the release surface end-to-end, not just local files.** Manifest edited locally + test passing locally proves nothing about what shipped. The release surface includes seven checks:

1. **Manifests show target version** — every file the manifest test enforces (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.claude-plugin/marketplace.json` `metadata.version` + `plugins[name=<plugin>].version` for RossLabs-ecosystem plugins). See [Three-file lockstep](#three-file-lockstep-plugin-manifest) below.
2. **Manifest test exits 0** — `python3 scripts/test_plugin_manifest.py` (or the plugin's equivalent).
3. **Local commit log matches expected pattern** — `git log --oneline -1` shows the expected commit message shape and SHA.
4. **Local tag exists** — `git tag --list <tag>` returns the tag.
5. **Branch HEAD SHA matches commit SHA** — `git rev-parse <branch>` == commit SHA.
6. **Remote refs at same SHA** (load-bearing) — `git ls-remote origin <branch> <tag>` shows BOTH refs at the same SHA. Without this, a passing local verification can ship nothing (silent push failure, wrong remote, branch protection block).
7. **(Optional) Fresh-session load test** — `claude plugin refresh` + cache diff vs canonical returns empty.

**Enforcement:** `python3 scripts/verify_release_surface.py --version <vN.N.N> --branch <name> --remote origin --json`. Returns structured JSON with per-check pass/fail + evidence; exit 0 if all pass, 1 if any fail. Verifier (Codex, CI, second Claude session) calls this instead of running the seven commands manually. Memory citation: `feedback_verification_checks_release_surface`.

### Three-file lockstep (plugin manifest)

Plugin version bumps in the RossLabs ecosystem update **three** files in lockstep, not two:

1. `.claude-plugin/plugin.json` — `version` field.
2. `.codex-plugin/plugin.json` — `version` field.
3. `.claude-plugin/marketplace.json` — BOTH `metadata.version` AND `plugins[name=<plugin>].version`.

`scripts/test_plugin_manifest.py` enforces all three via `VersionShapeTests.test_codex_manifest_matches_plugin_name_and_version` and `test_marketplace_versions_match_plugin`. Two-file bumps fail the marketplace test.

**The test is the source of truth, not the prose.** Before drafting ANY version-bump brief, run `python3 scripts/test_plugin_manifest.py` first. Read failure messages to enumerate every enforced manifest. Build the file list from the test output, not from a docs paragraph that may undercount. Memory citation: `feedback_three_file_lockstep_plugin_manifest`. See also `skills/plugin-builder/SKILL.md` §"Dual-Host: Shipping to Claude Code AND Codex".

---

## Closeout hygiene

**A coordination run is not complete until all live processes, presence records, worktrees, and active coord files are explicitly cleaned up.** Stale heartbeats in `~/.build-loop/apps/<slug>/sessions/` and locked worktrees in `.claude/worktrees/` mislead the next run's peer-detection — Rally Point may report "active peer" for a dead process; `git worktree list` may show locked entries that block branch operations.

**Phase D closeout protocol (orchestrator runs by default at end of every run):**

1. **Reap this run's session presence:** `scripts/rally_point/lifecycle.reap_my_sessions(channel_dir, my_session_id)`.
2. **Stop watchers:** SIGTERM any `coordination_watch.py --interval N` processes started during the run.
3. **Force-remove dispatch worktrees:** `git worktree remove -f -f <path>` + `git branch -D worktree-agent-<id>` for any `Agent(isolation="worktree", ...)` dispatch. The double `-f` is required if the worktree was locked by the agent process.
4. **Archive the coord file:** `mv .build-loop/coordination/<this-coord-file>.md .build-loop/coordination/archived/`. Not deletion — preserves the durable record while clearing the active queue.
5. **Optional changes.jsonl rotation:** `scripts/rally_point/lifecycle.rotate_changes_log(channel_dir, max_mb=1, max_entries=500)` rotates when either threshold is exceeded.
6. **Final post:** `post(kind="phase", payload={"phase": "run-closeout", ...})` signals to channel that this run is done; future readers know to skip its presence/changes when scoping.
7. **Track in state:** `state.json.runs[N].closeout_status`.

The protocol is automated, not operator-discipline-dependent. Memory citation: `feedback_close_out_stops_the_watcher`.

---

## Quick-reference cross-index

| Rule | Canonical implementation |
|---|---|
| Operating rule (verdicts gating) | `scripts/coordination_status.py` `BLOCKING_VERDICTS` constant; coord file Operating Rule section |
| `post()` mandatory | `scripts/rally_point/post.py` (the helper itself) |
| Cheap detection at step boundaries | `scripts/coordination_status.py` + `scripts/coordination_watch.py` |
| MECE packets enforcement | `scripts/brief_mece_validator.py` + `agents/build-orchestrator.md` dispatch wrappers |
| Release-surface verification | `scripts/verify_release_surface.py` |
| Three-file lockstep enforcement | `scripts/test_plugin_manifest.py` `VersionShapeTests` |
| Closeout hygiene | `scripts/rally_point/lifecycle.py` + `agents/build-orchestrator.md` Phase D |
| Coord-file shape | `references/coordination-file-template.md` |

---

## When to update this file

This is a constitution, not a changelog. Edit only when a coordination rule itself changes — new operating rule, new enforcement script, retired pattern. Single-run details and step status belong in the per-run coordination file under `.build-loop/coordination/<topic>.md`, not here.
