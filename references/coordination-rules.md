<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

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
from scripts.rally_point.discovery_bridge import resolve
from pathlib import Path
envelope = resolve(Path.cwd())
channel = Path(envelope.channel_dir)
post(
    channel_dir=channel,
    kind="feedback",        # or "phase", "commit", "dep-change", "handoff", "arch-scan-complete"
    tool="codex",           # or "claude_code", "gemini_cli", etc.
    model="gpt-5",
    run_id="<run-id>",
    app_slug=envelope.app_slug,
    payload={"step": "<id>", "verdict": "PASS", "evidence": {...}, "impact": "...", "requested_action": "..."},
)
```

`post()` bumps the revision FIRST, then appends the record. That ordering guarantees readers who see the new revision can always find the corresponding record (no race where revision is ahead of the log).

**Channel scope (worktree- and clone-independent):** resolve the channel through `scripts/rally_point/discovery_bridge.resolve(workdir)`. Native `agent-rally-point` discovery returns the canonical shared channel (currently `~/.agent-rally-point/apps/<repo-id>/`). The embedded build-loop fallback also defaults to `~/.agent-rally-point/apps/<slug>/`, where `slug` comes from `git rev-parse --git-common-dir` via `scripts/rally_point/channel_paths.app_slug(cwd)`. The main checkout, every worktree, and every clone of the same canonical repo share ONE channel. Different canonical repos get different channel directories (cross-repo isolation).

**Anti-pattern (silent no-op):**

```python
# Never do this — readers' checkpoint_read returns changed: false
from scripts.rally_point.changes import append_change
append_change(channel_dir, record)  # forgot bump_revision; record invisible
```

Memory citation: `feedback_post_helper_prevents_revision_bump_bug`.

---

## Trust model (unauthenticated channel; advisory leadership lease)

**The coordination channel is unauthenticated and trusted-local-peers-only.** `changes.jsonl`, `presence/`, `rally/lead.json`, and the coordination markdown all live under the channel returned by `discovery_bridge.resolve(workdir)` with ordinary user-account file permissions. Any process running as the same local user can append a change record, write a presence file, or claim/transfer the leadership lease. There is no signing, no authentication, and no identity verification — and there should not be: build-loop is a local single-user developer tool, so a cryptographic trust layer would be disproportionate to the threat.

What this means in practice:

- **Change-record payloads are untrusted free text.** A buggy or hostile channel writer can put arbitrary text — including prompt-injection content — into a `payload` field. Records flow into orchestrator LLM context via `checkpoint_read` `new_changes[]` and `coordination_status` `new_changes` / `open_escalations`. **Mitigation (SEC-002):** the consume boundary sanitizes every record before surfacing — `scripts/rally_point/checkpoint.sanitize_change_for_surface()` keeps only known structured metadata keys and length-caps every free-text string. The raw `changes.jsonl` log stays immutable and untouched; only the *surfaced projection* is sanitized. Reactions (`dep-change`, `arch-scan-complete`, `soft-claim`) are derived from raw records first, because they read only the structured `kind` field.

- **The leadership lease is advisory coordination, not access control (SEC-003).** Every mutating call in `scripts/rally_point/leadership.py` (`claim_lead`, `renew_lease`, `transfer_lead`, `relinquish_lead`) trusts a caller-supplied `session_id`. `claim_lead` succeeds for anyone whenever the lease is absent or expired; `renew`/`transfer`/`relinquish` "authorize" only by string-matching `session_id` against the world-readable `lead.json`. Any local process that reads `lead.json` learns the incumbent's `session_id` and can forge a renew, transfer, or relinquish. **The orchestrator MUST NOT gate an irreversible action on a lead claim** — a lead claim answers "who is coordinating" for cooperating peers, not "who is authorized". The proportionate control is observability: every `claim_lead` / `transfer_lead` / `relinquish_lead` emits a stderr audit line (`[rally-point audit] ...`) recording the requesting tool and `run_id`, in addition to the durable `lead-*` record in `changes.jsonl`, so an unexpected lease mutation is visible after the fact.

Threats this model does NOT cover (out of scope by design): a hostile process running as the same user, a compromised local account, or a multi-tenant host. Those are the operating system's responsibility, not the coordination channel's.

---

## Evidence boundary (Rally is not a verifier)

Rally records are peer-authored coordination metadata. They can tell an agent
what another agent claimed, handed off, reviewed, blocked, or released, and they
can point to artifacts worth inspecting. They do not prove the artifact, code,
package, tag, release, or remote state is correct.

Before making a factual claim about repo or release state, check the authoritative
surface directly:

- Code and docs: working tree, `git diff`, file contents, and tests.
- Package/version surface: manifests, package tests, dry-run pack/publish output,
  and release-surface verifier scripts.
- Remote release state: GitHub/npm/GitHub Packages API or public pages, not a
  Rally `release` or `artifact` record.

A Rally `artifact`, `release`, `resolve`, `review_artifact`, or `next` record is
a routing signal. It may identify what to inspect; it is never the inspection.

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
8. Declaring a self-recursive runtime-changing stage ready for the next stage
   after a dogfood reload checkpoint.

Between triggers, no polling is needed only when there is no active peer, no
active coord file, and no tool inbox message. When a host is waiting on an
async peer response, has an active peer, or has an inbox message, keep a cheap
watcher running:

```bash
python3 scripts/coordination_watch.py --workdir "$PWD" --session-id "$SESSION_ID" --tool "$TOOL_NAME" --interval 5 --jsonl --baseline-current
```

For long-running task ownership, write a task heartbeat at task start and at
least every 10 minutes:

```bash
python3 scripts/agent_rally.py heartbeat --workdir "$PWD" --session-id "$SESSION_ID" --tool "$TOOL_NAME" --task-ref "$TASK_REF" --progress "still on task" --json
```

Then pass `--task-ref "$TASK_REF"` to `status` or `watch`. Presence only says
the session is live; task heartbeat says whether it is still on the claimed
task and when the next check-in is due.

For self-recursive runtime-changing stages, use
`scripts/dogfood_reload_checkpoint.py` and
`references/dogfood-reload-checkpoint.md`. A Rally handoff or inject is not reload proof.
Each participating terminal must ACK runtime root + commit, or
the live agent must record a fallback (`reassign`, `defer`, or
`continue_solo`) before continuing.

Use stable tool ids (`claude_code`, `codex`, `cursor`, etc.) so targeted
`inbox/<tool>.jsonl` messages route cleanly. Broadcast messages live in
`inbox/all.jsonl`; every tool's read path includes that file in addition to
its direct inbox. Unread counts are session-ack aware: after reading and acting
on current inbox payloads, run `agent_rally.py ack-inbox --session-id <id>
--tool <tool>` so resolved notes stop appearing as new doorbells. Status
`clear` → proceed; status `warn` → review peer
overlap + dirty files; status `blocked` → resolve unresolved verdicts before
any of the above. Memory citation:
`feedback_poll_channel_at_step_boundaries`, `feedback_script_first_coordination_checks`.

## Peer liveness & orphaned lanes (never wait on an idle peer)

**An interactive CLI peer (Codex, Cursor, a peer Claude terminal) is NOT a daemon.** It acts only within a turn its user prompts, then idles awaiting the next input — it does **not** autonomously poll this channel and resume. A handoff to such a peer therefore executes only when its user next drives that terminal; it may sit unread indefinitely. Do not model a CLI peer as a continuously-running worker.

**Liveness rule:** treat a peer with no channel activity for **>10 minutes while it owns an open handoff lane** as *idle* (silent ≠ dead, but ≠ progressing). Detect via the peer's last `recorded_at` in `changes.jsonl` vs now; a clean `stop`/`relinquish` also means idle.

**Orphaned-lane absorption:** when a lane assigned to an idle peer is **local and reversible** (commits, doc/agent edits, dead-code or dead-key trims, version bumps, test updates), the live agent **absorbs it** — does the work itself, then records in the report `absorbed <peer>'s idle lane: <what> [<evidence>]`. Do **not** block a release, a finish, or "done" on an idle peer's local lane — that is the same manufactured wait as a turn-length stop (see `skills/build-loop/SKILL.md` §"Keep going until done"). Only surface/hold a lane that is genuinely **peer-exclusive**: needs the other vendor's model (true cross-vendor review), the peer's environment/credentials, or an irreversible action only that peer is authorized to take. Coordination is cooperative, not a dependency that can deadlock the live agent.

## Recency decay & size-scaled lead/ownership auto-reclaim

A single coordination policy governs message aging and stale-claim reclaim,
mirrored from the canonical Rust implementation (agent-rally-point). Tunables
live under `coordinationPolicy` in `.build-loop/config.json` (defaults shown):

```json
{
  "coordinationPolicy": {
    "half_life_hours": 48,
    "archive_floor_weight": 0.05,
    "reclaim_small_minutes": 30,
    "reclaim_large_minutes": 120
  }
}
```

- **Recency decay (listing order + archive).** Every coordination change gets a
  weight `0.5 ** (age_hours / half_life_hours)` (default half-life 48h). The
  status / recent-changes listing orders fresh-first by weight and EXCLUDES any
  change whose weight has fallen below the archive floor (default `0.05`, ≈14d).
  Archived changes are losslessly retrievable with `--include-archived`
  (`coordination_status.py --include-archived`), which also folds back any
  physically-rotated `changes.jsonl.<date>` logs. Decay applies only to the
  historical change stream — never to the live direct-message inbox or to active
  state. Fails OPEN: a change with a malformed `ts` is treated as fresh.
- **Size-scaled lead/ownership auto-reclaim.** A lead lease (`rally/lead.json`)
  whose `lease_until` has passed is auto-reclaimable by the next `claim_lead`.
  The lease WINDOW scales with the claimed work size: a small (single-file /
  effort XS·S) claim expires after `reclaim_small_minutes` (default 30m); a
  large (multi-file / coarse / effort M·L·XL) claim after `reclaim_large_minutes`
  (default 2h). Pass `work_size`/`effort`/`owns` to `claim_lead`; with NO size
  signal the lease window stays the historical `renew_every_minutes` cadence
  (backward-compatible). An auto-reclaim posts a durable `lead-reclaim` record
  naming who reclaimed, the prior owner, and the reason (`stale-by-timeout`).
- **Preserved invariants.** Reclaim stays race-safe (the `rally/lead.lock`
  fcntl lock is untouched) and FAIL-CLOSED: a present incumbent lease whose
  `lease_until` is unparseable is NEVER auto-reclaimed (we refuse rather than
  reclaim on a timestamp we cannot trust). An empty seat is still freely
  claimable.
- **Separation invariant (compatible, but separate).** build-loop computes its
  decay/reclaim ENTIRELY in Python (`decay.py`) over its own change-log store; it
  never delegates the recency-decay listing to the Rust `rally` binary. So an
  installed `rally` that predates the decay feature CANNOT un-decay build-loop's
  output — build-loop's listing decays regardless of binary version. The Rust
  `rally recent/room --include-archived` surface is agent-rally-point's OWN
  equivalent for its `.rally` ledger, kept curve-compatible via the shared golden
  fixture (`scripts/rally_point/decay_vectors.json` ≡ the Rust fixture, tracked in
  `_provenance.json`). Pinned by `scripts/test_coordination_decay_invariant.py`:
  if a future change routes build-loop's decay listing through any binary, that
  test fails. (To get decayed output from the Rust CLI directly, install a
  `rally` built from this feature or later — an older on-PATH binary won't decay.)

This complements (does not replace) the >10-minute idle absorption rule above:
idle-absorption handles local reversible lanes a quiet peer left open; the lease
timeout governs the formal lead/ownership role handover.

## In-room stale-state reaper (actuator) & codex parity

The sections above define WHEN records become stale. This section describes the
ACTUATOR that physically removes them.

**Canonical actuator (Rust):** `rally doctor --reap-stale [--apply]` — bundled with
agent-rally-point >= 0.5. Dry-run by default; `--apply` physically removes
over-TTL presence, claims, and leads.

**Fallback actuator (Python):** `scripts/rally_point/reaper.py` — runs when the
Rust binary is absent. Called fire-and-forget at every session-start via
`hooks/session-start-rally-point.sh` Step 3. Also available as a standalone CLI:
`python3 scripts/rally_point/reaper.py --workdir <path> [--apply] [--json]`.

**FAIL-CLOSED invariant.** The reaper NEVER removes a record whose ownership
timestamp it cannot unambiguously prove is over-TTL. Missing, unparseable, or
future timestamps are ALWAYS preserved. Specifically:
- Presence: `heartbeat_ts` must be a positive float AND older than
  `heartbeat_minutes * 60` (default 900 s). A zero or unreadable value → kept.
- Claims: `lease_expires_at` must be a valid RFC3339 timestamp AND `<= now`. A
  missing, malformed, or future value → kept.
- Lead: `_reclaimable(doc, now)` from `leadership.py` must return `True` (requires
  a present, parseable `lease_until` that is at/after expiry). A malformed lease →
  kept (the same FAIL-CLOSED invariant as `claim_lead`).

**Rust-vs-Python claim store rule.** When the discovery bridge resolves via
`repo-local-rally-cli`, the Rust binary owns the `claim-index.json` projection;
the Python reaper reaps PRESENCE + Python lead.json only and reports expired claim
count as `claims_deferred_to_rust` rather than physically rewriting the file (which
would fight the Rust projection). Only when the Rust binary is absent does the
Python reaper rewrite `claim-index.json`.

**Codex parity.** A codex session emits the same presence record claude does, via
the `.codex/hooks.json` `SessionStart` hook that calls `session_probe.py --tool
codex`. Its presence record therefore ages and decays identically to claude's —
heartbeat parity is proven by `scripts/rally_point/heartbeat_parity_vectors.json`
(byte-identical to the Rust fixture `crates/rally-cli/tests/fixtures/
heartbeat_parity_vectors.json`). The parity test in `test_reaper.py` asserts
`decay.recency_weight(age, half_life_secs)` matches each vector's `expected_weight`
within 1e-4, and the `stale_at_15m` staleness verdict, for every case.

**Session-end self-release.** Both tool hooks emit `rally stop <tool>` at turn
completion (`Stop` event) so peers immediately see the agent's absence rather than
waiting for heartbeat TTL expiry. This contains accretion at the source and
reduces the reaper's steady-state work.

## Idle-agent self-selection (rally facilitates, the agent decides)

**Rally is a facilitator, not an orchestrator or verifier.** It exposes room state (`rally room` / `rally next`), file-level deconfliction (`rally check before-write --path P`), and claims/handoffs. It does **not** assign work, pick work, or verify code/release truth. A waiting agent runs this decision tree itself and chooses — the agent's LLM reasons over Rally's surfaced coordination records. This keeps coordination decentralized: no single point that hands out tasks (which would be a failure site and a bottleneck).

When an agent is idle and `rally next` returns no actionable item, walk the tree top-down, stop at the first match:

1. **Pending handoff/inject addressed to me** (by session, name, or tool) → handle it, record the response, then run `ack-inbox`.
2. **An open blocker I can resolve** → resolve it; post the resolution.
3. **A no-regret item is free** → pick from the project's no-regret backlog (`.build-loop/followup/`, deferred-but-safe items, the run's recorded follow-ups). For its files, run `rally check before-write --path <each>`; if clear, `claim` them, `say` what you're starting, then do it. Reversible + behavior-preserving + tests-pass only.
4. **All coding candidates are claimed or conflicted** → do read-only research or assessment that helps and has zero file conflict (simplification scans of untouched areas, duplication/test-gap audits, docs the room needs).
5. **Nothing fits, or the only work left is risky/deferred/peer-exclusive** → stay idle and say so; do not start risky/deferred work, do not touch another session's claimed paths.

The tree is the guideline; Rally supplies coordination records (claims, collisions, pending items) each branch needs. Two same-tool agents running it independently land on different work because claim-first + `check before-write` makes the first claimant win and the second re-select — no central referee required.

## Coordination reliability (verify the room before trusting it)

Room resolution can shift under you — a binary update, a repo-keying change, or a worktree path can move which channel you resolve to. Before concluding "no peers" or "empty room", verify it:

- **Check which channel you actually resolved.** If `rally enter` / `rally room` returns a null or empty channel, your *read* is suspect, not the room. A peer you "can't see" is often in a **different room** (different repo slug, or pre/post a keying change), not absent. Confirm the channel's `repo_root` (in `rally.channel.json`) matches the repo you mean.
- **One repo = one room** (keyed off the canonical repo root, shared by all worktrees); **different repos = different rooms**, correctly. Before declaring a peer missing, confirm you are both keyed to the same repo root — two agents in sibling repos (e.g. `build-loop` vs the spun-out `agent-rally-point`) are *supposed* to be in separate rooms.
- **A lead that posted then went quiet is idle** (same as any interactive CLI peer — see §Peer liveness). Do not block on it; absorb local lanes, leave a relay for its return.
- **Never hand-append the hash-chain channel files** (`changes.jsonl`) to "reach" a peer — corruption risk. If the CLI cannot post, relay out-of-band; do not edit the chain.

## Room-policy reconciliation (mission / envelopes vs dispatch brief)

Named failure (2026-06-09, agent-rally-point): two orchestrator runs hit the same in-room mission guardrail ("No push to origin without Tyrone go") and split — one pushed past it without addressing it; the other held a finished build at push time even though the line was stale (superseded in practice by five operator-approved pushes). The rule below makes the reconciliation explicit and early.

- **Read room policy at entry.** After `rally enter`/`ack`, read the room mission and this agent's autonomy envelope (`rally mission --json`) and reconcile them against the dispatch brief's authorizations for gated actions (push, deploy, destructive).
- **Surface conflicts at Phase 1, not at push time.** A mission/brief conflict on a gated action is posted on-channel as a decision-needed fact AND returned to the dispatcher immediately — never first discovered after the work is done.
- **Precedence when reconciling:** newer ledger decision facts supersede older mission text; a per-agent autonomy envelope `may` grant covers its named action; an operator-attributed decision fact satisfies a "without <operator> go" guardrail. Operator-attributed means posted by the operator or from an operator-present interactive session — a subagent cannot mint its own go signal by posting a decision fact mid-run.
- **Genuine conflict after checking all three → hold the gated action and surface.** Holding is the correct terminal behavior; the failure mode this rule removes is holding late.

---

## MECE Packets (briefs require all seven; rally packets six + optional 7th)

**Every implementation handoff to a peer MUST spell out seven elements: `owns / does-not-own / interface-contract / integration-checkpoint / allowed-tools / denied-tools / acceptance-criteria`. The hard seven-field lint applies to dispatch BRIEFS (`brief_mece_validator.py`); rally `kind=handoff` ownership packets require the six structural fields (`mece_gate.py`) and validate `acceptance_criteria` when present — bootstrap/presence posts are not delegations and may omit it.** Anything less is "informational handoff" — produces drift, two writers on the same file, ambiguous "done" definitions.

| Element | What it answers | Example |
|---|---|---|
| **Owns** | Which files/scopes may the peer write? | `scripts/coordination_status.py`, `scripts/test_coordination_status.py` |
| **Does not own** | Which files/scopes must the peer NOT touch? | any agent body; any coord-file content |
| **Interface contract** | What shape does the deliverable take? (schema, format, exit code, location) | CLI `--json` returns `{status, latest_verdicts, ...}`; exit 0 clear / 1 warn / 2 blocked |
| **Integration checkpoint** | How does Claude verify the handoff landed and how does it plug back in? | regression test passes; orchestrator parses returned JSON; entry appears in coord file |
| **Allowed tools** | Which tools may the peer use? (empty list = no restriction) | `["Bash", "Read", "Edit"]` or `[]` |
| **Denied tools** | Which tools must the peer NOT use? (empty list = no restriction) | `["WebSearch"]` or `[]` |

Both `allowed-tools` and `denied-tools` MUST be present on every `kind=handoff` post; either MAY be an empty list. An empty `allowed_tools` is a valid explicit "no lateral limits" declaration — only a missing or non-list field is rejected by `mece_gate.validate_handoff`. These fields are the G2 lateral-limits feature (`feat(rally): tool-level lateral limits on handoff packets`, 2026-05-22).

**Enforcement:** `python3 scripts/brief_mece_validator.py --brief-file <path> --json`. Exit 0 → all seven present (briefs). Exit 1 → at least one missing; orchestrator surfaces a `[warn]` and may still dispatch (C-FLOW pattern — non-blocking lint). The orchestrator wires this lint into every `Agent(subagent_type=..., ...)` dispatch site for peer-handoff briefs.

**Carve-out:** pure-read handoffs ("go look at this and tell me what you find") skip MECE. All write-handoffs need all seven. Memory citation: `feedback_handoffs_require_mece_packets`.

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

**A coordination run is not complete until all live processes, presence records, worktrees, and active coord files are explicitly cleaned up.** Stale heartbeats in the resolved Rally Point channel's `sessions/` directory and locked worktrees under `.build-loop/worktrees/` mislead the next run's peer-detection — Rally Point may report "active peer" for a dead process; `git worktree list` may show locked entries that block branch operations.

**Phase D closeout protocol (orchestrator runs by default at end of every run):**

1. **Reap this run's session presence:** `scripts/rally_point/lifecycle.reap_my_sessions(channel_dir, my_session_id)`.
2. **Stop watchers:** SIGTERM any `coordination_watch.py --interval N` processes started during the run.
3. **Collapse branches and worktrees:** merge the winning/validated line(s) to `main` first (solo-on-main runs skip this — work is already on main), then call `scripts/collapse_run.py` as described in `agents/build-orchestrator.md` §"Phase D: Closeout" step 4. That step is the single source of truth for the collapse invocation, ordering, JSON-to-report wiring, and `createdRefs[]` lifecycle status updates.
4. **Archive the coord file:** `mv .build-loop/coordination/<this-coord-file>.md .build-loop/coordination/archived/`. Not deletion — preserves the durable record while clearing the active queue.
5. **Optional changes.jsonl rotation:** `scripts/rally_point/lifecycle.rotate_changes_log(channel_dir, max_mb=1, max_entries=500)` rotates when either threshold is exceeded.
6. **Final post:** `post(kind="phase", payload={"phase": "run-closeout", ...})` signals to channel that this run is done; future readers know to skip its presence/changes when scoping.
7. **Track in state:** `state.json.runs[N].closeout_status`.

The protocol is automated, not operator-discipline-dependent. Memory citation: `feedback_close_out_stops_the_watcher`.

---

## C-FLOW rules

**C-FLOW/no_ask_to_commit** — Completed, validated, build-loop-authorized work commits automatically; pausing to ask the user whether to commit is a workflow violation. Only push/deploy verdicts of `confirm` or `block` from `autonomy_gate.py` / `deployment_policy.py` stop the loop; routine commit advancement does not require confirmation.

**C-FLOW/no_ask_at_chunk_boundary** — The phrasing "want me to keep going?" / "should I continue with X next?" at a chunk boundary is a workflow violation when the items are same-shape and same-intent. Referenced from `skills/build-loop/SKILL.md`.

**C-HEAL/self_heal_safe_issues** — Self-heal is **both reactive and proactive**. **Reactive arm:** when build-loop encounters an error or crash from its own tooling, a hook, a script, a Bash command, or a build/test/lint failure; OR a quality or performance issue surfaced by any Review sub-step, self-review, fact-check, simplify, or efficiency scan — ROOT-CAUSE and FIX it, then continue. Classify via `scripts/classify_action.py`: SAFE → apply, verify (re-run failed action and relevant tests), commit, continue; RISKY → isolate to worktree-branch + log + continue main + surface in report; DECISION/PRODUCTION → surface/escalate. **Proactive arm:** during deep self-review (and any self-recursive build), the self-review/self-heal loop ALSO proactively simplifies build-loop's own code — reducing complexity, splitting oversized files, removing dead/duplicated logic, adding missing tests — driven by `self_review.py`'s `self_simplification[]` findings. The loop MAY author new skills and new scripts (new scripts require a colocated `test_<name>.py`). **MANDATORY SAFETY GATE for self-modifications:** any change to build-loop's own plugin repo or the `build-loop-memory` durable repo MUST pass `python3 scripts/self_mod_verify.py --scope auto --auto-revert` (`verdict: pass`) before commit; on `verdict: fail` the gate auto-reverts and the change is not committed. **Self-modifications execute — they do not stop the loop.** A self-modification that is part of the accepted plan (including edits to the gate, tests, or the self-improvement loop) executes behind the test-suite gate. Build-loop never halts a planned self-modification for human approval. Oversight is post-hoc: (a) self-modifying runs trigger an ADDITIONAL adversarial review (independent-auditor at build scope; the periodic deep self-review re-audits recent self-modifications) — non-blocking; (b) the end-of-run readback reports every self-modification and the additional-review findings. The loop stays on task and reports once, at the end. Structural/architectural self-modifications (new phase, changed contract, agent-role change) surface as DECISION, never auto-apply. Full gate protocol: `skills/build-loop/references/self-review.md` §"Self-modification of the restricted repo". Banned anti-pattern: bypassing a fixable error — `--no-verify`, xfail-ing a test, commenting out failing code, `|| true` on a real failure — when a SAFE root-cause fix exists. Workarounds allowed only when the fix classifies RISKY/DECISION/PRODUCTION or is genuinely infeasible; record both the workaround and the issue.

**C-RCA/root_cause_before_done** — Before any "done"/completion claim, investigate EVERY open issue — failed tests, loose ends, errors, warnings, minor issues — to ROOT CAUSE; none are left unaddressed. A surface/symptom patch is a violation. Use the debugging skills (`build-loop:debug-loop` / `root-cause-investigator` / `systematic-debugging`) and/or a **5-whys / causal-tree** analysis to determine the true cause AND how far it spans (same root cause affecting other sites → fix all of them). The fix MUST be **verified by another, independent subagent** (confirms root cause correctly identified, fix resolves it, no regression introduced) before "done." Both the investigation-before-done and the second-subagent verification are mandatory; the specific technique is the operator's choice. The second-subagent check reuses existing surfaces (`independent-auditor`, `fix-critique`, or a dispatched verifier) — no new agent. C-RCA pairs with C-HEAL: C-HEAL governs what to do when a SAFE error surfaces (reactive fix + proactive simplification); C-RCA mandates that the root cause is understood, the fix is durable, and a peer has confirmed both before the run closes. It also operationalizes the standing "attack over defense / always the durable fix / fix everything" preferences as a completion gate. The investigation-before-done and second-subagent verification are non-negotiable; the specific technique is the operator's choice. Referenced from `agents/build-orchestrator.md` §"Root cause before done" and `skills/build-loop/SKILL.md` §"Root cause before done".

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
| Closeout hygiene | `scripts/rally_point/lifecycle.py` + `scripts/collapse_run.py` + `agents/build-orchestrator.md` Phase D |
| Coord-file shape | `references/coordination-file-template.md` |

---

## When to update this file

This is a constitution, not a changelog. Edit only when a coordination rule itself changes — new operating rule, new enforcement script, retired pattern. Single-run details and step status belong in the per-run coordination file under `.build-loop/coordination/<topic>.md`, not here.
