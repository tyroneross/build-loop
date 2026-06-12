# Build Loop

Orchestrated 5-phase development loop with a mandatory Phase 6 Learn, for significant multi-step code changes. Use this methodology when changes span multiple files, require planning, and benefit from structured validation.

**Skip this loop for:** single-file edits, config changes, quick fixes under ~20 lines.

## Autonomy gate — the three things that confirm (governing rule, highest priority)

Once a plan is accepted, the loop does not stop to ask. Exactly three actions require human confirmation; everything else auto-executes:

1. **Production push** — a deploy/publish/migration that reaches live users (production deploy, `npm publish`, `gh release`, prod DB migration). Preview/testflight/staging deploys are auto.
2. **Destructive delete** — archive is auto (reversible); an irreversible purge/drop requires confirmation. Prefer archiving over deleting. Build artifacts (node_modules, dist, caches) are regenerable and delete freely.
3. **Major user-impacting decision** — a product/platform-direction choice the plan marks `user_impact: major`. Implementation-tradeoff DECISIONs do NOT surface.

Action classification: SAFE → execute on main; RISKY → isolate to branch + log + continue main; DECISION → auto-pick `recommended_default` and log (unless `user_impact: major`); PRODUCTION → confirm (gate 1). Catastrophic-never commands (`rm -rf /`, `rm -rf ~`, force-push to a protected branch) are auto-refused — the loop logs and continues, does not ask.

There is NO gate for code size or complexity. Genuine inability to proceed (missing credential, external blocker) is logged, worked around with available tasks, and reported in the readback — not a stop-and-ask. Oversight is the end-of-run readback, not a mid-run halt.

## Session-start preflight

Run this once at the start of every session, **before any other action**, to learn the coordination state of this repo (active peers, pending ACKs addressed to you, north-star paths, memory locations, guardrails) and to write a presence record so other tools can see you. Output tells you whether to `proceed_solo` or `join_active` (handle pending ACKs first).

```bash
rally codex --human
```

The command is **host-neutral** — the same binary works for every coding tool. Substitute the `--tool` value for your host: `codex`, `cursor`, `gemini`, `claude_code`, or `other`. The JSON form (omit `--human`) is the machine-readable envelope.

When you know your intent or files at session start, include them so peers can see the work immediately:

```bash
rally start codex --intent "<what you are doing>" --path "<file-or-dir>" --json
```

When you finish or hand off, close the loop:

```bash
rally stop codex --session-id "<session-id>" --reason "done" --json
```

`rally stop` removes live presence, marks the agent stopped, and releases active file claims unless `--keep-claims` is passed.
Peers read active work from `active_peers[]` and last-known active/stopped session state from `peer_states[]`.

**Fallback:** if `rally` is not on PATH, proceed without preflight — do **not** crash, do **not** block on it. The Phase 1 Rally Point presence write below covers the minimum coordination contract.

## Output style

Concise output — say only what the user needs to decide or act; cut narration, restated context, filler; no jargon.

Lead each point with the finding. Progressive disclosure: headline first, files/detail below. Number points as standalone **bold-number** paragraphs with a blank line between (plain `1.` list syntax renders compressed). Never use the contrastive-pivot construction ("not X — it's Y", "isn't X, it's Y", "not just X but Y"); state the point directly. Style only, never a gate.

## Phases

| # | Phase | Purpose | Output |
|---|-------|---------|--------|
| 1 | **Assess** | Understand state (project type, architecture, tools, prior state) AND define goal + 3-5 scoring criteria with pass/fail conditions | State summary + `.build-loop/goal.md` |
| 2 | **Plan** | Break work into tasks with dependency order, identify parallel-safe groups | Plan with dependency graph |
| 3 | **Execute** | Build it — dispatch parallel work for independent file groups | Working implementation |
| 4 | **Review** | Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report — seven ordered sub-steps, single exit point | Scorecard + evidence; routes to Iterate on failure |
| 5 | **Iterate** | Fix Review failures, loop back to Review (max 5x) | Updated scorecard |
| 6 | **Learn** (mandatory) | Always runs after Review-G; emits a `## Learn` outcome line (accruing / deferred / full). Detects recurring patterns across runs (incl. retro enforce-candidates as a second signal source), auto-drafts experimental skills/agents with A/B tracking; promotion to `active/` still requires explicit `/build-loop:promote-experiment` | Experimental artifacts + synthesis |

## Run Modes

These run parameters apply on any host — pass them on the invocation (`--flag`) or honor the equivalent intent when the user states it in prose. Default is autonomous, 2h budget.

| Mode / flag | Effect |
|---|---|
| default | Autonomous queue-drain loop: Phase 5 Iterate self-replenishes from `.build-loop/{ux-queue,issues,followup}/`, alignment-checks each item against intent, executes the aligned subset, batches commits. 2h wall-clock budget. |
| `--long` (or goal keywords `overnight`, `long-running`, `large-scale`, `multi-day`) | Same loop, 8h budget. |
| `--budget 30m \| 4h \| 30s` | Custom wall-clock budget; overrides `--long`. `budget_check.py` routes `continue \| checkin \| finalize_and_stop` at each iterate entry, commit, and phase boundary. |
| `--autonomous=false` | Classic single pass — run Phases 1–6 once; queue items become `followup/` instead of being drained. |
| `--resume <run-id \| latest>` | Re-enter a crashed/killed build mid-flight. Reuses the original `deadline_at` (a 2h budget that died at 1h59m does NOT get a fresh 2h — `resume_resolver.py` owns this). Reads existing `intent.md`/`plan.md`; jumps to remaining chunks. On a normal dispatch with an incomplete prior run detected, offer resume vs fresh before Phase 1. |

**Iteration caps:** classic = 5 per build; autonomous = 25 per build (3 same-verdict per item). Stop conditions: cap reached, budget exhausted, a drained item classifying `confirm`/`block`, 5 consecutive same-criterion failures, or an item whose intent anchor no longer resolves.

**Per-commit mode** (self-recursive builds — when the working dir IS the runtime, e.g. editing build-loop itself): plan once, then run one orchestrator pass per commit so each commit reviews and lands cleanly before the next starts. Auto-on for self-recursive; force with `--per-commit` / `--no-per-commit`.

**Budget-aware behavior is host-neutral** — without programmatic budget tracking, treat the budget as a soft wall-clock target: check in at ~50%, finalize before the deadline rather than starting new chunks.

## Core Principles

- **KISS + DRY — code and output (governing).** Before adding a rule, gate, schema, script, agent, or report section, first try to (a) delete something, (b) extend something that exists, or (c) do nothing. A new mechanism must earn its place against a *named, observed* failure in this repo — not a cited statistic. Prefer one rule covering many cases over many narrow rules; one source of truth over duplicated logic. For output: say it once in the fewest words that keep the evidence; omit empty sections; headline first. Fewer rules and fewer lines is the default; growth is the justified exception. When this tensions with the principles below, simplify. **Every issue is a systems issue:** when something doesn't work, debugging finishes only when the *system* is updated so the class can't recur (durable guidance/check/simplification/restructure addressing root cause + meta-point, never a surface patch). Default corrective move is to reduce complexity (fewer lines/deps/steps) or, when size is irreducible, better structure (split large files, progressive disclosure) to minimize cognitive load. Scalable means simple over compact — every node (rule, script, agent, step, dep) is a failure site.
- **Tools on demand.** Detect what's available, use what's needed. Don't assume any tool exists. Native debugging skills are bundled inside build-loop; `/build-loop:debug <symptom>` is always available, and the orchestrator auto-invokes `Skill("build-loop:debug-loop")` on Review-B Validate failures and Iterate attempts 2 and 3. Standalone Coding Debugger is optional for cross-project incident memory.
- **North star first.** Understand the app/repo purpose, primary users, core workflows, and update intent before planning. Every subtask should explain how it contributes to that purpose.
- **Beauty in the basics.** Core flows, real data, clear hierarchy, useful states, working controls, and accurate information matter more than extra surface area.
- **Modular by default, not by dogma.** Prefer high cohesion, loose coupling, stable interfaces, and scalable boundaries unless a simpler or integrated approach better serves the use case. Document `MODULARITY EXCEPTION: <reason>` when taking that path.
- **Two-lens recommendations.** Do not let current tech debt, prior architecture, existing dependencies, or earlier decisions silently define "best." For non-trivial architecture, workflow, dependency, or product decisions, assess the clean-sheet best approach for the use case first, then assess the best current-constraints approach given the repo's debt, tools, dependencies, migration cost, and risk. Prior decisions are evidence, not axioms.
- **MECE work ownership.** Partition files, agents, and task groups so ownership is mutually exclusive and collectively exhaustive: no overlapping file owners, no unowned responsibilities, and one clear grouping dimension per level.
- **Guidelines for creation, guardrails for output.** Be flexible during building. Be strict about what reaches users.
- **No false data.** No mock data in production. No hardcoded metrics pretending to be real. No unverified claims.
- **Name every UI input and output.** For UI work, every affected surface must have an input/output contract before component choices are locked: data taxonomy, CRUD/domain operation, component mapping, states, modality fallback, validation/security, and traceability.
- **Diagnose before fixing.** Root-cause analysis before code changes. Many errors sharing a pattern = one system problem.
- **C-RCA / root_cause_before_done — investigate every open issue to root cause before "done," verified by a second subagent.** Before any completion claim, investigate EVERY open issue — failed tests, loose ends, errors, warnings, minor issues — none left unaddressed. For each, reach the ROOT CAUSE, not a surface patch. Use debugging skills (debug-loop / root-cause-investigator / systematic-debugging) and/or a 5-whys / causal-tree analysis to find the true cause AND its full span (does the same root cause affect other sites? fix all of them). The fix MUST address the root cause — a surface/symptom patch is a violation. The root-cause identification, fix, and non-regression MUST be verified by an independent subagent before "done." Pairs with C-HEAL (reactive self-heal) and the verify-every-subagent rule.
- **Research persistent problems, don't just retry.** When a fix doesn't hold, the same Iterate criterion fails 2+ times, or behavior contradicts your model, stop guessing and do internet research from trusted sources (T1 official docs and issue trackers first) before another attempt. `root-cause-investigator` carries WebSearch for exactly this. A documented upstream bug or library/terminal behavior often explains an "impossible" intermittent failure faster than another local loop — and prevents shipping a layered workaround over a known root cause. Mark confidence on what you find (✅ T1 cited / ⚠️ inferred).
- **Hook policy.** Hooks are advisory and non-blocking by default. Stop hooks must emit valid JSON on stdout when they emit anything, must exit 0, and must not stop work for reminders such as dirty worktrees, coordination notes, missing optional telemetry, or cache maintenance. Blocking is reserved for explicit safety/security/integrity gates: destructive actions, secret exposure, protected push/deploy/publish/release gates, or the self-modification safety gate. If a Stop hook needs blocking behavior, it must be behind an explicit opt-in and explain the exact safety boundary.
- **Converge or escalate.** If iteration isn't improving scores, stop and surface the blocker. Don't burn cycles.
- **Keep going until done.** Once the user accepts the plan, every phase is authorized scope. Do not ask the user to confirm each phase. Issues found mid-build route to Iterate. Status updates are fine; permission requests are not. Completed, validated, authorized work commits automatically — asking "should I commit?" or "want me to commit this?" is a workflow violation. `git commit` is classified `auto` by the autonomy gate (exit 0); it is never a permission-gated action. The only commit-adjacent stops are autonomy-gate `confirm`/`block` verdicts on a *push or deploy* command. The only valid stops are: a destructive action not in the plan, a missing credential, externally-blocked work, an explicit hand-off point in the plan, a genuine scope branch the plan does not resolve, or 8 hours wall-clock without a Review pass / 5 consecutive Iterate failures on the same criterion. **NOT stops:** posting a coordination handoff to a peer is *fire-and-continue* (work your owned lane in parallel; only a verifier verdict gating the *next* step is a wait); output volume / turn length is never a stop; "continue or hold?" on authorized, lane-isolated, determinate remaining work is a manufactured fork — finish it. A multi-step prune/refactor/migration with a defined item list runs to completion across that list.
- **Commit authorship stays human.** When build-loop manages a commit or push, do not set the official git author, committer, GitHub author, push actor, release actor, or equivalent platform actor to `Claude Code`, `Codex`, or any agent identity. Use the repo's configured human or service identity for official authorship. Agent involvement may be recorded in commit-body notes, run notes, `.build-loop` context, judge decisions, or auxiliary metadata; it must not replace the official author or actor field.
- **Self-heal SAFE issues — reactive fix AND proactive self-simplification (C-HEAL / self_heal_safe_issues).** Self-heal has two arms, both active. **Reactive:** when build-loop encounters (a) an error or crash from its own tooling, a hook, a script, a Bash command, or a build/test/lint failure; OR (b) a quality or performance issue from any Review sub-step, self-review, fact-check, simplify, or efficiency scan — ROOT-CAUSE and FIX it, then continue. Classify via `scripts/classify_action.py`: SAFE → apply, verify, commit, continue — no surface, no ask; RISKY → isolate to worktree-branch + log + continue main + surface in report; DECISION/PRODUCTION → surface/escalate. **Proactive:** during deep self-review (and any self-recursive build), the self-review/self-heal loop ALSO proactively simplifies build-loop's own code — reducing complexity, splitting oversized files, removing dead/duplicated logic, adding missing tests — driven by `self_review.py`'s `self_simplification[]` findings. The loop MAY also author new skills and new scripts (new scripts require a colocated `test_<name>.py`). Any change to build-loop's own code MUST pass the SELF-MODIFICATION SAFETY GATE (`self_mod_verify.py --scope auto --auto-revert`, `verdict: pass` required) before commit; on fail the gate auto-reverts and the change is not committed. **Self-modifications execute — they do not stop the loop.** A self-modification that is part of the accepted plan (including edits to the gate, tests, or the self-improvement loop) executes behind the test-suite gate. Build-loop never halts a planned self-modification for human approval. Oversight is post-hoc: (a) self-modifying runs trigger an ADDITIONAL adversarial review (independent-auditor at build scope; the periodic deep self-review re-audits recent self-modifications) — non-blocking; (b) the end-of-run readback reports every self-modification and the additional-review findings. The loop stays on task and reports once, at the end. Structural/architectural self-modifications surface as DECISION, never auto-apply. Full gate in `skills/build-loop/references/self-review.md` §"Self-modification of the restricted repo". **Banned anti-pattern:** bypassing a fixable error — `--no-verify`, xfail-ing a test, commenting out failing code, `|| true` on a real failure — when a SAFE fix exists. Workarounds allowed only when the fix classifies RISKY/DECISION/PRODUCTION or is genuinely infeasible; record both.

## Phase Details

### Phase 1: Assess

Combines situational awareness with goal definition so Plan has everything it needs.

**Understand state:**
- Detect project type and tooling (language, framework, test runner, linter, build system)
- Read deployment policy from `.build-loop/config.json.deploymentPolicy` when present. Default: `preview: auto`, `testflight: auto`, `production: confirm`, `unknown: confirm`.
- **Credential preflight** (fail-soft, names only — no values): scan referenced env keys against `.env`/`env` and surface each gap as `[CREDENTIAL REQUIRED] <name>` in the Assess summary and end-of-run readback. A missing credential is logged, worked around with available tasks, and reported — never a stop-and-ask.
- **Stale-context triage** (fail-soft): check handoff/orchestration/continuation docs against git history. Flag each drifted doc as `[STALE CONTEXT] <path>` so the agent does not plan from stale state.
- **Memory-staleness triage** (fail-soft): compare the project's milestone log against HEAD. When stale, surface `[MEMORY STALE] <slug> N commits behind HEAD` and continue — do not stop.
- Capture app/repo north star and update intent in `.build-loop/intent.md`: purpose, primary users, core jobs, user value, and non-goals.
- Capture modular structure in `.build-loop/state.json.structure`: current module boundaries, stable interfaces, coupling risks, likely MECE work partitions, and any justified modularity exception.
- Capture approach lenses in `.build-loop/state.json.approachLenses` for non-trivial recommendations: clean-sheet best approach, current-constraints best approach, constraints/debt that change the answer, and the bridge/backcast path from current state toward the clean-sheet target.
- Map relevant architecture (only what the goal touches)
- Check for prior state (`.build-loop/state.json` from interrupted builds)
- If goal involves external frameworks or APIs: research current docs before planning
- **Capture web/doc findings as references (default-on, any phase/mode).** Whenever the run fetched external info (WebSearch/WebFetch/Context7/api-registry/official docs) AND used it in a decision, persist the EXTRACTED findings (not raw HTML) via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/reference_capture.py capture --workdir "$PWD" --run-id "<run_id>" --topic "<topic>" --findings "<distilled>" --source "<url>|<T1..T4>" --decision "<what it informed>" --json`. This routes through the canonical memory writer into the project `research` lane with `retrieved_at` and a per-content-class `refresh_after` horizon (api-docs/pricing age in days, surveys in months, specs in ~half a year). The store is uncommitted by default. On the next run, `context_bootstrap.py` flags any reference past its horizon as `stale-needs-refresh` in the brief (`packet.reference_freshness`) — advisory only, never a blocking ask. Do not ask whether to capture; it is a default behavior. Full policy: `references/research-trigger-policy.md` §"Reference Capture".
- If web/mobile UI: capture current visual state for before/after comparison, then load `skills/build-loop/references/ui-io-contract.md` and inventory the affected user inputs and system outputs before planning
- **Supply-chain dependency cooldown**: if a JS project (`package.json`), run `scripts/inject_dependency_cooldown.py --workdir <repo>` to idempotently write the 7-day publish-age config using each PM's native key: npm ≥ 11.10.0 → `.npmrc` `min-release-age` (DAYS); pnpm → `pnpm-workspace.yaml` `minimumReleaseAge` (MINUTES) + `.npmrc` `minimum-release-age` for 10.x; yarn ≥ 4.10 → `.yarnrc.yml` `npmMinimalAgeGate` (numeric MINUTES). npm has no native exclude (npm/cli#8994), so on npm the user-authored allowlist (`.build-loop/config.json` → `dependencyCooldown.allowlist`, default `["@tyroneross/*"]`) is enforced by the PreToolUse hook (`scripts/hooks/pre_bash_dependency_cooldown.sh`), which stays engaged even with native config; pnpm/yarn carry the exclude natively so the hook stands down once enforced. `--check` verifies the PM actually recognizes the key (no false `enforced:true`). Constitution rule: `C-SUPPLY/dependency_cooldown`. Older npm (< 11.10.0) falls back to the hook's `--before=<7d ago>` date-pin. pip/cargo not covered in v1.

**Memory bootstrap + queue surfacing (session start — before planning):**

Run once at the Phase 1 preamble, immediately after `run_id` is known and before the architecture baseline or any planning:

1. **Identify the repo**: `project_resolver.resolve_project(Path("$PWD"))` → project slug (git remote or dir name).
2. **Run bootstrap**:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/context_bootstrap.py \
     --workdir "$PWD" --query "<goal-keywords>" \
     --output "$PWD/.build-loop/context-bootstrap.json" --json
   ```
   The packet covers: canonical `build-loop-memory` root/project `MEMORY.md` + `constitution.md`, indexed recall via `memory_facade.py`, repo-local `.build-loop/{feedback,state,goal,intent,plan}` files, Codex memory registry `~/.codex/memories/MEMORY.md` plus linked rollout summaries, best-effort Rally/coordination state, **queue counts + top items** (`queues.{issues,backlog,ux-queue,followup,proposals}.{count,top[]}`), **progressive lessons** (`lessons_progressive[]` — SQLite FTS5, scoped to current work, zero external deps; degrades gracefully when DB absent), and `session_prefs`.
3. **Surface + ask once** (immediately after reading the packet):
   - Read `packet.agent_brief` for the one-liner summary, then check each queue: if any `queues.*.count > 0`, emit `#issues=N #backlog=M …` plus the top item titles from `queues.*.top[0].title`.
   - Surface `lessons_progressive[].name` (up to 3) as ambient context so planning reflects recent learnings.
   - Check `session_prefs.continue_from_queues`:
     - `"always"` → include queue work in the plan without asking.
     - `"never"` → skip queue work without asking.
     - `"ask"` (default) AND first Assess of this run AND any queue count > 0 → ask the user ONCE: *"Tackle queue items now / after current task / not this session?"* Persist their answer: call `write_session_prefs(workdir, value, source="asked")` from `scripts/context_bootstrap.py` — this writes `state.json.session_prefs.{continue_from_queues, set_at, source}`.
     - When `session_prefs.source == "config"` (set in `.build-loop/config.json` → `sessionPrefs.continueFromQueues`), do NOT ask — the repo has a standing preference.
   - User task instructions always take priority over queue work.

**End-of-run continuation gate (after followup drain, before closing):**

After the main build's followup drain completes (or `.build-loop/followup/` was empty), run:

```python
from scripts.context_bootstrap import should_continue_into_queues, pending_queue_items
should_continue = should_continue_into_queues(workdir)   # True iff session_prefs == "always"
pending        = pending_queue_items(workdir)             # {"issues": N, "backlog": M}
```

Proceed only when BOTH `should_continue is True` AND `pending["issues"] + pending["backlog"] > 0`. When both are true, enter one additional Phase 5 iterate cycle targeting `.build-loop/issues/` then `.build-loop/backlog/` (issues first). Use the same iterate machinery: alignment-checker per item, scope-auditor, independent-auditor post-fix; same iterate-cap and stop conditions. Items classified `PRODUCTION` or `DECISION` → surface in report, do not auto-execute. When either condition is false, the run ends — do NOT ask again (the preference was already captured at session start).

**Multi-session presence (Rally Point — cross-host: Claude Code, Codex, Gemini CLI, others):**

Multiple build-loop sessions can run concurrently against the same project across terminals and coding hosts. Rally Point presence is the single concurrent-presence source of truth — an awareness layer (never a lock), host-neutral, invoked from any host. (The legacy `session_registry.py` / `~/.build-loop/sessions/` collision mechanism was documented-dead and removed 2026-05-18 — see `KNOWN-ISSUES.md` §M4.)

1. **Write presence and intent at the Phase 1 preamble** (immediately after `run_id` is known), and refresh it at each phase-start. Preferred Rust path:
   ```bash
   rally start codex --session-id "<sid>" --intent "phase=assess" --json
   ```
   When files are owned, include one `--path` per file or directory so Rally creates explicit file claims:
   ```bash
   rally start codex --session-id "<sid>" --intent "phase=execute" --path "src/app.ts" --json
   ```
   Embedded fallback path when the Rust `rally` binary is unavailable:
   ```python
   from pathlib import Path
   from scripts.rally_point import presence
   from scripts.rally_point.discovery_bridge import resolve
   envelope = resolve(Path("$PWD"))
   slug = envelope.app_slug
   channel = Path(envelope.channel_dir)
   presence.write_presence(channel, session_id="<sid>", tool="codex",
       model="<model>", run_id="$RUN_ID", app_slug=slug,
       phase="assess", files_in_flight=[])
   ```
   `tool` values: `claude_code | codex | gemini | other`. Resolve the channel through `discovery_bridge.resolve(...)` before every direct write. Rust-backed channels use `rally start` / `rally stop`; embedded fallback writes one file per live session at `<resolved-channel>/sessions/<session-id>.json` (session_id, tool, model, run_id, app_slug, phase, files_in_flight, heartbeat_ts, read cursor). Fire-and-forget — never raises, never blocks.
2. **Read active peers** at the preamble and each phase-start:
   ```python
   peers = presence.read_active_presence(channel, exclude_session="<sid>")
   ```
   This reaps stale presence (heartbeat older than the channel's `heartbeat_minutes`, default 15) as a side effect — no daemon, no cleanup step.
3. **Peer routing** (awareness only — never a hard block, D4; identical across hosts):
   - No peers / no `files_in_flight` overlap → log one line per peer (tool, run_id, phase); proceed.
   - Overlap with a peer's `files_in_flight` → surface a `soft-claim` **WARNING** (peer, overlapping files, peer phase); proceed with awareness. Interactive hosts MAY additionally ask the user to coordinate; headless hosts (Codex / cron) log + proceed. There is no SAFE-STOP sentinel and no non-zero exit.
4. **Refresh presence** at every phase-start and whenever the phase's owned files change — re-call `write_presence` with the new `phase` + `files_in_flight` (the per-session read cursor is preserved across refreshes).
5. **Stop explicitly.** On Rust-backed channels, run `rally stop <tool> --session-id <sid> --reason "<done|handoff|blocked>" --json` when the session ends or file ownership changes materially. This clears presence, marks the session stopped in `peer_states[]`, and releases active claims. Embedded fallback still self-heals with `reap_stale` when a host exits without unregistering.
6. **Memory writes (M5 — separate concern)** — use `scripts/memory_writer.py write` instead of writing memory files directly. The writer adds provenance frontmatter (source_repo, source_workdir, source_run_id, source_host, cross_repo_validated, applied_in_repos, created_at, last_updated_at), then atomically appends a row to `INDEX.jsonl` for sibling discovery:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py write \
     --file "<rel-path>" --name "<slug>" --description "<one-line>" \
     --type feedback --run-id "$RUN_ID" --workdir "$PWD" --host codex \
     --body-file /tmp/memory-body.md
   ```
7. **Memory reads (cross-session discovery)** — between phases, tail the index for new peer learnings:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_index.py tail \
     --since "$LAST_INDEX_CHECK_TS" --exclude-run-id "$RUN_ID" --json
   ```
   For each returned row, read the memory file. Tag with `[CROSS-REPO — requires scrutiny]` when `source_workdir` ≠ current `$PWD` AND `source_repo` ≠ this repo's git remote. Tag with `[VALIDATED — applied in N repos]` when `cross_repo_validated: true` AND `len(applied_in_repos) >= 2`.
8. **Memory mark-applied** — when a cross-repo memory is successfully applied here, record the application:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py mark-applied \
     --file "<rel-path>" --applying-repo "$THIS_REPO_REMOTE" \
     --applying-workdir "$PWD" --applying-run-id "$RUN_ID"
   ```
   Flips `cross_repo_validated: true` once a different repo confirms the lesson.
10. **One-time migration** — on the first build after installing this version, run:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py migrate \
     --run-id "$RUN_ID" --workdir "$PWD" --host codex
   ```
   Idempotent backfill of provenance frontmatter onto pre-existing memory files. Safe to re-run.

All three scripts are stdlib-only Python 3.11+ with atomic writes (`tmpfile + os.replace`) and `fcntl.flock` on append. They work identically in Claude Code and Codex; the only difference is which `--host` value the session passes.


**Define goal + criteria:**
- State the goal in one concrete sentence — what will be true when this succeeds?
- Design 3-5 scoring criteria. Each criterion must have:
  - A clear pass condition
  - A grading method: code-based (preferred) or LLM-as-judge (for nuance)
- Write goal to `.build-loop/goal.md`

**Synthesis-density routing (4-priority resolution):** if a plan exists at this point, count its `synthesis_dimensions:` entries (see Phase 2) and resolve the implementer tier in this priority order:

1. **Explicit override** — if the host config sets a `thinking-tier` override OR the plan/chunk frontmatter declares `tier: thinking` → route to thinking-tier.
2. **Auto-escalate on density** — `count > 5` (6 or more entries) → `tier: thinking` (synthesis-dense at the commit level; fan-out loses cross-dimension coherence here).
3. **Default — fan-out for speed** — count 0–5 → fan out to code-tier implementers; backstops in Phase 4.5 catch the residual recall gap.
4. **Per-chunk override** — individual chunks may declare `tier: thinking` even when the plan-level decision was fan-out (mixed-density plans).

Write the verdict to `.build-loop/state.json.synthesisDensity` as `{count, escalated, reason}`. Routing target is `tier: thinking` (provider-agnostic), never a hardcoded model name.

The `> 5` threshold matches the empirical inflection point measured in the synthesis-decision A/B experiment (2026-05-07): below that, code-tier implementer recall is poor but non-zero and the Phase 4.5 backstops materially help; at 6+ dimensions, depth dominates.

**Dynamic tier assignment (guide, not a fixed rule):** the orchestrator judges each subtask's complexity at dispatch time and assigns the tier that fits. Both escalation directions are active on every dispatch decision.

**Priority order: accuracy > speed > cost.** Pick the tier that does the work CORRECTLY first — never trade accuracy for a cheaper or faster model. Among accuracy-equivalent options, prefer the faster path (spawn Thinking-tier subagents to accelerate complex work; fan out in parallel where the host supports it). Optimize cost only after accuracy and speed are both satisfied.

**Prefer the Code tier** (the workhorse: Sonnet on Claude, GPT-5 Codex on Codex, Gemini 2.5 Flash on Gemini) for the bulk of work. Down-tier to **Pattern** (Haiku / GPT-5 Mini / Flash Lite) only for genuinely trivial mechanical/recognition tasks — pure pattern-match, no judgment, no gradient. When in doubt, use Code tier.

**Thinking-tier subagents are allowed to accelerate complex subtasks** — cross-file reasoning, novel design, ambiguous spec, hard refactor. Thinking tier is not reserved for the top-level orchestrator. On Codex this maps to GPT-5 Thinking workers; spawn them only under the existing Codex permission gate (explicit `--parallel` / delegation authorization).

**Verify every subagent's output before accepting it.** The cheaper the tier, the stronger the check. This per-subagent verification is the safety net that makes dynamic (and occasionally cheaper) assignment safe. Verification ties to build-loop's existing surfaces: verify-scope / verify-landed (Phase 3 commit step), independent-auditor (Phase 4 Review-A), and the implementer return envelope (`status: blocked | partial` routes to Iterate before output is accepted). No subagent output is trusted unchecked.

Full provider substitution table (Thinking / Code / Pattern → each host's models): `references/model-tier-mapping.md` §"Dynamic tier assignment".

**Eval methodology:**
- Binary pass/fail only. No Likert scales, no partial credit.
- One evaluator per dimension. No multi-dimension "God Evaluator."
- Code-based graders first (test pass/fail, lint clean, build succeeds, type check passes).
- LLM-as-judge only for criteria code can't evaluate (UX quality, naming clarity, etc.).

### Phase 2: Plan

- Break work into tasks with exact file paths
- Identify dependency order — what must complete before what?
- Flag parallel-safe groups: files that don't import each other can be written simultaneously
- Partition files and agents MECE: every changed file has exactly one owner, every required responsibility has an owner, and each group declares `owns`, `does not own`, `interface contract`, and `integration checkpoint`
- Define checkpoints where work should be verified before continuing
- Optimize: remove unnecessary steps, combine related changes, eliminate redundant work
- **Two-lens approach gate**: for non-trivial architecture, workflow, dependency, UI/product, or long-lived interface decisions, add a `## Approach Lenses` section before implementation tasks. It must include:
  - **Clean-sheet best approach:** what would be recommended if prior repo decisions, tech debt, and current implementation constraints did not exist.
  - **Current-constraints approach:** what is best given existing code, debt, dependencies, tools, team/runtime constraints, migration risk, and delivery horizon.
  - **Bridge/backcast:** the smallest credible path from the current-constraints approach toward the clean-sheet target, including debt retired, dependencies added/removed, and decision points.
  - **Recommendation:** which path to execute now and why. If the recommendation follows current constraints instead of the clean-sheet approach, name the constraint that justifies the compromise.
- **UI input/output contract gate**: if `uiTarget != null`, add a `## UI Input/Output Contract` section before mockups or implementation. It must name every changed surface's inputs, outputs, data taxonomy, operation/domain verb, component mapping, state matrix, modality fallback, validation/security layer, and schema/API/design-system traceability.
- **Recent design structures gate**: if `uiTarget != null`, pass `skills/build-loop/references/recent-design-structures.md` to `design-contract-specialist` after the UI input/output contract exists. The specialist chooses the structure based on product/workflow/data/platform fit; recent structures are options, not requirements.
- **Enumerate synthesis dimensions** for any commit that involves design judgment (UI placement, copy tone, CTA tier, schema shape, dispatch contracts, etc.). Add a `synthesis_dimensions:` block to the plan listing each named decision with a concrete claimed value:
  ```yaml
  synthesis_dimensions:
    placement_NewsBanner: "after `<NewsCard>` in app/components/Feed.tsx"
    cta_tier_save_button: "primary"
    copy_tone_settings: "second person, calm-precision, no exclamation marks"
    empty_state_feed: "icon + one-line explanation + primary CTA"
  ```
  Vague values (`"appropriate"`, `"as needed"`, `"sensible"`) fail the deterministic plan-verify rule — every entry must name a specific choice or write `n/a` with a reason. The block is the contract the implementer attests to applying; Phase 4.5 lints diff-vs-claim.
- **Mockup-first gate for major UI work**: if the plan introduces a new page/screen or makes a major redesign (changes navigation graph, primary user flow, or replaces ≥40% of an existing screen), pause and use a mockup-drafting tool to produce black-and-white mockups before any UI is written. Wait for user feedback; carry the selected mockup into Execute as a reference. Skip for cosmetic tweaks, copy edits, or single-component swaps. This is the documented exception to the "actions/functions only, no plugin UI surfaces" bridging policy — mockup drafting IS the action.
- **Pay-it-forward architectural gate**: when a chunk touches a typed protocol / interface boundary / schema / multi-surface-capable behavior, the plan MUST include a `Path A vs Path B` comparison per `skills/build-loop/references/pay-it-forward-arch.md`. Default recommendation is **Path B** (typed-contract extension). Gates that justify Path A: time-budget >2×, missing dep/infra, missing design decision, or empty foreclosed-future-capability list. Named-future-capability list must cite the roadmap / PRD / `intent.md` — flexibility-for-its-own-sake (plugin systems, abstract factories with no current second consumer) is the explicit anti-pattern. Skip when the chunk fires none of the signals. Path A/B section template:
  ```markdown
  ### Path A vs Path B — <chunk name>
  **Path A (minimum-viable):** <what / where / time / foreclosed>
  **Path B (typed-contract extension):** <what / where / time delta / NAMED capabilities unlocked>
  **Gates check:** time-budget? missing dep? missing decision? empty foreclosed-future-list?
  **Recommendation:** Path B (default) / Path A (because <named gate>)
  ```

**Plan acceptance gate** — required before Phase 3 begins:

1. **`plan-verify` (deterministic, Python stdlib)** — run grep-checkable rules over the plan:
   ```bash
   python3 <build-loop>/scripts/plan_verify.py <plan-file> --repo "$PWD" --json
   ```
   Catches: deletes/orphans contradicted by repo grep, internal numeric drift, route changes without evidence, package-state contradictions, missing markers, scope-split breadth.
   - Exit 0 → continue to step 2.
   - Exit 1 → revise the plan to clear each BLOCKER (or document an explicit override with rationale).
   - Exit 2 → verifier error; log and continue with step 2 only.
2. **`plan-critic` (non-deterministic)** — invoke the equivalent reviewer in your tool of choice with the plan + the JSON from step 1. Looks for: less-invasive alternatives considered, MECE quality of phase splits, marker adequacy across long passages, headline drift across sections. Findings cap at WARN — surface but do not auto-block.
3. **Gaps readback (mandatory)** — run both steps above BEFORE presenting any plan. Prepend `✓ Plan gaps-checked: none` when both pass clean, or `⚠ Plan gaps: <N> — <each finding, marked resolved/surfaced>` when findings exist. The host is never asked "anything missing?" — the gaps-check result is always shown first.

Wire all three surfaces (`skills/build-loop/SKILL.md`, `agents/build-orchestrator.md`, this file) together when the gate evolves — phase-asymmetric updates have caused silent skips before.

### Phase 3: Execute

- Dispatch parallel work for independent file groups
- Each worker gets minimal context + integration contract (what interfaces to implement) + an intent packet explaining how the subtask fits the north star + a MECE ownership packet defining owned files, non-owned files, interface contracts, and integration checkpoints
- If the host supports typed subagents, map read-only codebase questions to explorer-style agents and disjoint implementation slices to worker-style agents. If the host requires explicit user authorization for subagents, identify parallel-safe groups but execute locally unless the user asked for delegation, parallelization, workers, or a `--parallel` mode.
- **Single-entry routing (host-neutral):** every coding task enters through one build-loop invocation; the runtime auto-classifies intent (build / optimize / research / debug / test) and routes accordingly. The host does not pick a mode — classification is internal. This applies equally across coding hosts (Claude Code, Codex, Cursor, Gemini CLI, others).
- **Subagent scaling (host-neutral):** when the host supports parallel delegation (e.g. Codex with `--parallel` authorization), dispatch up to `scripts/parallelism.py effective_max_implementers()` workers — machine-aware cap, default 8, ceiling 12 — decomposing work into the maximum number of independent MECE chunks. The permission gate still applies: workers only on explicit `--parallel` / delegation authorization. When parallel delegation is unavailable, execute sequentially without asking.
- Do not delegate ambiguous product decisions, final integration, destructive git operations, push/deploy confirmation, or tasks whose result blocks the immediate next lead-session step.
- For UI work: follow established design system or sensible defaults (44px touch targets, 4.5:1 contrast). Every visible element must have meaning, working behavior, a clear user purpose, and a matching entry in the UI input/output contract.
- Surface pre-existing issues separately from new work. If an issue impacts users and is local to the current build, plan and fix it automatically; if too large/risky, log user impact and defer.
- Checkpoint after major integration points

**Implementer return contract (envelope):** every implementer subagent returns a structured envelope, not freeform prose. Required fields:

```json
{
  "status": "completed" | "blocked" | "failed",
  "files_modified": ["path/to/file.ts", "..."],
  "synthesis_attestation": {
    "<dimension_name>": "applied" | "deviated" | "n/a",
    "<dimension_name>": {"status": "applied", "claim": "<concrete value>"}
  },
  "novel_decisions": [
    {"decision": "<one line>", "reasoning": "<why and what alternative>"}
  ],
  "commit_sha": "<sha or null>"
}
```

The `synthesis_attestation` map MUST have one entry per dimension named in the plan's `synthesis_dimensions` block. `novel_decisions` is the recall-test field — synthesis-class decisions the implementer faced that weren't enumerated in the plan. Be honest; silent decisions defeat the purpose.

**Halt-and-ask backstop (`status: blocked`):** if an implementer encounters a synthesis-class decision NOT in the plan's `synthesis_dimensions` block, it returns `status: blocked` with the decision in `novel_decisions[]` instead of committing. The orchestrator routes each blocked decision to a thinking-tier resolver, stores resolutions in `state.json.novelDecisionResolutions[]`, and re-dispatches the implementer with resolutions appended to the brief. Hard-fail counter: 3 attempts. No new dependency required — this is a status-branch addition, not a state-machine framework.

### Phase 4: Review

Seven ordered sub-steps; intermediate failures route to Iterate, final pass writes Report artifacts.

**Sub-step A — Critic (adversarial read-only)**: perform a read-only adversarial review of the diff — inline in the lead session, or via an authorized explorer when delegation is permitted. This is the independent-auditor pass: render one of four verdicts (`yay` approve / `nay` reject / `suggest_correction` / `look_again`) gathering context from intent, goal, PRD, constitution. Catch scope drift, missed edge cases, rubric violations before spending tokens on full validation. **When Assess flagged a risk-surface change** (new auth, network, persistence, secrets, or external-input surface), also run a security review in this sub-step against OWASP LLM/Agentic/Web top-10 — its critical/high findings feed the no-critical/high exit gate and are blocking. Strong-checkpoint findings route back to Execute (no iteration burn); guidance findings are logged.

**Sub-step A.5 — Synthesis-decision backstops (post-implementer-commit, runs before B):**

Two checkpoints fire automatically after every implementer commit on plans that declared a `synthesis_dimensions` block:

- **Phase 4.5a — `attestation_lint`** (deterministic): compares the implementer's `synthesis_attestation` envelope against the actual git diff for verifiable dimensions (placement, cta_tier, visual_weight). Catches silent drift between claim and code. Exit 1 escalates to user; exit 2 logs warning and proceeds; exit 0 silent. Subjective dimensions (copy_tone, empty_state) return `unverifiable` and route to 4.5b. Reference implementation: `scripts/attestation_lint.py` (Python stdlib, accepts strict α-style and permissive β-style claim shapes; `--strict-mode` reverts to α-only).
- **Phase 4.5b — `synthesis-critic`** (subjective, read-only critic): a code-tier critic agent reviews the diff against the plan's subjective synthesis dimensions (copy_tone, empty_state). Severity capped at WARN — never blocks. Output: `{verdict: "pass" | "flag", flagged: [{dimension, claimed, observed, reasoning}], notes}`. Skips when the diff touches no UI files (`*.tsx`/`*.jsx`/`*.vue`/`*.svelte`).

Both backstops are first-class on the code-tier (fan-out) implementer path where they catch some of the recall gap, and defense-in-depth on the thinking-tier path where they rarely fire.

**Sub-step B — Validate**: when the build touches UI, run build-loop's `ui-validator` first, then check the UI input/output contract for changed surfaces, code-based graders (test, lint, type, build), design-rule scanner, visual evidence capture, and LLM-as-judge for nuanced criteria. Every pass/fail has evidence. Use only headless/programmatic surfaces — never auto-open a viewer/dashboard. IBR's interactive viewer / persistent browser sessions stay explicit-only; **UI visual-verification** (BL-3) routes through `build-loop:ibr-bridge` as the primary verifier when the IBR plugin is installed (headless scan is a programmatic action), with `native-ax-driver` / `ui-validator` as fallback. Symbol-only checks (`nm`, `strings`, `otool`) are never a substitute for visual/AX verification on a UI chunk. Scorecard format:

| # | Criterion | Method | Result | Evidence |
|---|-----------|--------|--------|----------|
| 1 | Tests pass | code | PASS | exit 0, 47/47 |
| 2 | No lint errors | code | FAIL | 3 errors in auth.ts |

**Sub-step C — Optimize (opt-in)**: runs only when a mechanical metric exists and the user hasn't disabled it. 3-5 iterations polish. Uses autoresearch pattern: constrained scope + metric + atomic changes + commit-or-revert.

**Sub-step D — Fact-Check, Mock Scan, UX Triage, Coverage**: gates run in parallel.

- *Fact Check*: trace every rendered metric (%, $, score, count) to source. For UI work, walk the full rendered surface, not just changed files. Flag "always", "never", "100%", "guaranteed" — replace unless genuinely absolute.
- *UI Input/Output Contract Scan*: for UI work, trace every changed user input and system output to the plan contract. New gaps in component mapping, states, modality fallback, validation/security, or source traceability are blocking.
- *Mock Data Scan*: production paths only. Detect lorem ipsum, faker, hardcoded fake values, `Math.random()` in display, placeholder text. Classify blocking (renders to user) vs warning.
- *Architectural Violations* (if available): `navgator rules --json`. Blocking: circular-dependency, layer-violation, database-isolation, frontend-direct-db. Warning: hotspot, high-fan-out, orphan.
- *Plugin Cache Sync* (plugin work): resync the local cache when diverged. Defer version bumps until the feature batch is declared complete (see Version Advisor).
- *Version Advisor* (plugin work): `scripts/version_advisor.py` reads plugin manifest and last-bump SHA, counts commits since via Conventional Commits to propose semver. Default state is `hold` — a one-line note in Report. State `suggest` only when the user creates `.build-loop/release-pending.md`. Never auto-bumps; never blocks.
- *UX Triage* (UI work): `scripts/ux_triage.py` static-scans interactability, performance, data-accuracy, and usability across the full project. Each blocker/major finding becomes a queue entry in `.build-loop/ux-queue/<id>.md` with a complete fix plan. Agent-driven augmentation (performance, fact-check on broader surface) merges into the same queue.
- *Coverage Gap* (UI work): for each changed critical surface lacking render/interaction coverage, add a repo-native test-coverage queue entry with a proposed test plan. Do not auto-draft `.ibr-test.json` files.

Blocking gates route to Iterate. Queue entries flow into Phase 5's prioritized work list. Warnings land in Report.

**Sub-step E — Simplify**: trim the diff — inline single-use helpers, delete dead branches, remove validation for upstream-guaranteed invariants. Preserve public API, tests, observability, and modular boundaries that protect user value, scalability, accuracy, security, testability, or stable interfaces. If an integrated simplification is better, document `MODULARITY EXCEPTION`.

The default Simplify pass covers both dead-code removal and logic/architecture simplification: flatten deep nesting, apply DRY, eliminate accidental O(n²) patterns, remove redundant multi-pass sequences, and cut needless indirection — wherever the result is a clear behavior-preserving win. `scripts/complexity_detector.py` is a Python-specific AST accelerator that emits ranked hotspots (high complexity, deep nesting, accidental O(n²), redundant multi-pass, needless single-call-site indirection); the running agent reasons over the diff language-agnostically and applies the same logic to any file type without it. APPLY a simplification only when all three hold: it is a clear win, the existing test subset for the touched files still passes, and public signatures + observable behavior are unchanged (reuse the existing Validate + independent-auditor gates — no new safety machinery, no perf gate, no benchmark). Ambiguous or uncertain rewrites are emitted as advisory variances, never applied.

**Sub-step F — Auto-Resolve**: before writing the report, drain non-destructive open items instead of listing them as questions. Classify each remaining item with `scripts/autonomy_gate.py` (or the action classifier) and route by verdict: `auto` → fix now and continue; `warn` → apply and note (exit 0, never blocks); `confirm` → surface once to the user (production/irreversible only); `block` → do not run. Same-shape, same-intent follow-ups go to `.build-loop/followup/` and re-enter Iterate, not into the report as "should I keep going?" prose. The report lists only genuinely held (`confirm`/`block`) items.

**Sub-step G — Report** (only on final Review pass):
- **Scorecard** with final pass/fail per criterion + evidence
- **Verified** (working with evidence), **Unknown** (untested), **Unfixed** (post-cap)
- **Discovered issues**: pre-existing problems from assessment
- **Fact check results**: warnings from sub-step D
- **`## Self-modifications (readback)`** (self-recursive runs only, omit otherwise): one row per self-modification attempted this run — file, what/why, gate verdict, additional-review finding. This is how the human stays informed without the loop stopping. Full spec in `agents/build-orchestrator.md` §G.

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`. Append run entry to `.build-loop/state.json.runs[]` with `run_id`, phase statuses, files touched, diagnostic commands, manual interventions, active experimental artifacts.

**Milestone append (every run, append-only)**: immediately after the run entry is written, append a milestone record to `build-loop-memory/projects/<slug>/milestones.jsonl` — one line: what shipped + commit. This log is never rewritten; it is the permanent progress record. Phase 1's memory-staleness triage compares the latest milestone against HEAD and flags gaps before planning begins. Principle: append-only log + a pointer, never full-state rewritten in place.

**Steering-decision capture (mandatory bridge)**: immediately after any host equivalent of a steering confirmation (architecture direction, library/dependency choice, platform, product direction, license, or any `user_impact: major` decision), append the answer as a DECISION record to `build-loop-memory/projects/<slug>/decisions/`. Root cause closed: steering answers that live only in context are lost when the session ends; every such answer is a durable decision and must be persisted immediately.

Before any push/deploy, classify the exact command with `scripts/deployment_policy.py` when available. Follow the returned action: `auto` may run after Review passes; `confirm` requires explicit user confirmation in chat; `block` must not run. Defaults allow preview deploys and Xcode/App Store Connect/TestFlight upload/export flows, while production deploys, releases, publishes, protected-branch pushes, and unknown targets require confirmation.

### Phase 5: Iterate

Build a prioritized work list per pass: (1) blocking Validate failures, (2) blocker UX queue entries with `architecture_impact: false`, (3) major UX queue entries with `architecture_impact: false`, (4) optimization findings, (5) UI coverage-gap queue entries. Entries with `architecture_impact: true` are deferred to Report for explicit user confirmation, NOT picked up here. Do not defer based on patch size — the only deferral signal is architecture impact.

Partition the list by disjoint `files_touched` and dispatch up to 4 parallel implementer subagents per pass (the standard cap). Sequential groups process after the parallel batch.

When the build touches UI files, after each implementer reports back AND before re-entering Sub-step B, run the build-loop UI re-validate hook against the affected route or screen. Catches new visual/interaction regressions cheaply.

For each fix:
1. Diagnose root cause (not just symptoms)
2. Use the queue entry's `proposed_fix` plan as the prompt (or, for Validate failures, create a targeted fix plan)
3. Execute fix
4. Loop back to Review sub-step B (Validate). Sub-step A usually skipped unless the fix touched new files.

**Followup overflow**: when iteration cap is reached and queue entries remain, write them to `.build-loop/followup/<topic>.md` for a subsequent build invocation. The followup build skips its own Plan phase for these entries (plans are already complete).

**Convergence rules:**
- If a criterion fails 3 times with the same root cause: escalate to user
- If fixing one criterion breaks another: stop, reassess approach
- If score doesn't improve after 2 consecutive iterations: change strategy, don't repeat
- **Hard stop at 5 iterations.** Proceed to Review sub-step G (Report) with remaining ❓ Unfixed.

Log iteration state to `.build-loop/state.json`.

### Phase 6: Learn (mandatory; always runs and always reports)

Runs after Review sub-step G (Report) on **every** build. Cheap detector + `consolidate_memory.py` + `procedural_governance.py --mode detect-patterns` always fire. A `## Learn` outcome line is always emitted. Three outcome states:

- **Accruing** (`runs[] < 3`): cheap detector + consolidation only; report `Learn: accruing (N/3 runs)`.
- **Deferred** (debug-only `closeout: false` in dispatch envelope OR budget-exhausted `budget_check.py` envelope `action == "finalize_and_stop"` at Phase 6 entry): cheap detector + consolidation; write `.build-loop/proposals/learn-deferred-<run-id>.md` marker with `{reason, runs_count, budget_action}`; skip Sonnet draft + Opus signoff so Learn never blows the budget ceiling. Report `Learn: deferred — <reason>`.
- **Full** (`runs[] >= 3` AND pattern crossing threshold AND not-deferred): full flow below.

Full-flow steps:

- **Detect**: pattern detector scans (a) `state.json.runs[]` for recurring `phase_failure` + `manual_intervention` + `security_finding` signals, (b) `.build-loop/proposals/enforce-from-retro/*.md` for `enforce_recurrence` patterns (the same normalized candidate signature across ≥ 2 distinct run-ids — wires the post-push retrospective's enforce-candidates into Learn's cross-run detector).
- **Draft**: for each kept pattern, architect agent writes experimental SKILL.md with A/B Experiment section (sample target 8 non-confounded runs).
- **Signoff**: Opus reviews each draft; APPROVE / REVISE (1 retry) / DISCARD.
- **Sample sweep**: for existing experimental artifacts with sample complete, eligible for promotion (only when `autoPromote: true` config is set AND effective non-confounded sample ≥ 8 AND non-regression). Promotion to `active/` still requires explicit `/build-loop:promote-experiment <name>` user confirmation. Regressions and inconclusive results write proposals, never auto-delete.

User controls: `rm -rf .build-loop/skills/experimental/<name>/`, `.build-loop/skills/.demoted` blocklist. The prior `autoSelfImprove: false` opt-out is deprecated to a migration no-op — old configs do not error, but the value is logged as a one-line `state.json.warnings[]` entry and ignored.

## Project Data

Build loop stores state in `.build-loop/` within the project directory:

```
.build-loop/
├── goal.md              # Current build goal
├── intent.md            # North star, update intent, user value, non-goals
├── config.json          # Optional repo flags, including deploymentPolicy
├── state.json           # Iteration state, phase progress, structure summary
├── feedback.md          # Post-build lessons (one line per build)
├── release-pending.md   # User-created marker: "feature batch complete, advise version bump"
├── ux-queue/            # UX-impacting findings with full fix plans (drained by Iterate)
│   └── <id>.md
├── followup/            # Overflow when iteration cap hit; input to subsequent build
│   └── <topic>.md
├── backlog/             # Deferred-but-wanted work (drained by end-of-run continuation)
│   └── <id>.md          # frontmatter: title, created, classify, effort, status
├── evals/               # Scorecard archives
│   └── YYYY-MM-DD-*.md
└── issues/              # Discovered issues (drained by end-of-run continuation)
```

This directory is created on first use. Add `.build-loop/` to your project's `.gitignore`.

### Phase D: Closeout

Runs by default at the end of every run (after Phase 6 Learn if it ran, otherwise immediately after Review sub-step G Report). Automated, not operator-discipline-dependent — skipping it leaves ghost-peer signals and locked worktrees that mislead the next run.

**Mandatory sequence:**

1. Reap this session's presence: `scripts/rally_point/lifecycle.reap_my_sessions(channel_dir, my_session_id)`.
2. Stop coordination watchers: SIGTERM any `coordination_watch.py --interval N` processes started during this run (PIDs tracked in `state.json.runs[N].watcherPids[]`).
3. **Collapse branches and worktrees (merge winner first, then collapse):** for solo-on-main runs the work is already on `main` — nothing to merge. For multi-worktree runs, merge the winning/validated line(s) to `main` via the normal single-writer commit flow before calling collapse. Then run:
   ```bash
   python3 ${RUNTIME_PLUGIN_ROOT}/scripts/collapse_run.py --workdir "$PWD" --run-id latest --json
   ```
   The script normalizes `dispatchedWorktrees[]` + `riskyBranches[]` + `createdRefs[]` into one ref list, creates a `git bundle ... --all` under `.build-loop/bundles/` (reversibility), then per ref: MERGED → delete branch + remove worktree folder; UNMERGED+`review_hold` → keep branch ref, remove worktree folder (→ `kept_for_review`); UNMERGED+no-hold → keep branch ref, remove worktree folder (→ `surfaced_unmerged`). Output: `{run_id, bundle_path, deleted[], kept_for_review[], surfaced_unmerged[], errors[], dry_run}`. Fail-soft — errors logged, closeout continues.
4. Archive the coordination file: move `.build-loop/coordination/<this-coord-file>.md` to `.build-loop/coordination/archived/`.
5. Optional `changes.jsonl` rotation: `scripts/rally_point/lifecycle.rotate_changes_log(channel_dir, max_mb=1, max_entries=500)`.
6. Final post: `post(kind="phase", payload={"phase": "run-closeout", ...})` signals to the channel that this run is done.
7. Write `state.json.runs[N].closeout_status`.

**`## Branch hygiene` report block** — every run's final report includes:
```
## Branch hygiene
created N · merged-to-main M (deleted) · kept-for-review R: [<branch-name>, ...]
· surfaced-unmerged U: [<branch-name>, ...] (ask keep/discard) · bundle: <path>
```
When a run created zero refs: `Branch hygiene: clean — no run-created branches/worktrees; on main.`

**Structural run-close (Stop hook).** Phase D above is the orchestrator path. An INLINE run (skill-as-methodology, no orchestrator dispatch) never reaches it, so a host `Stop` hook fires the minimum structural closeout with no human prompt — `hooks/closeout.sh stop` → `scripts/stop_closeout.py`:

1. **Record + surface.** Records the run via `append_run.py` (so Phase 6 Learn's `runs[]` sees it) and runs `judgment_gate.py --agent-tool-available false`, surfacing a WARN `systemMessage` when a stakes-gated run skipped the Frontier judgment layer. A Stop hook cannot dispatch agents, so it auto-records + auto-surfaces the gap — it does not run the retrospective-synthesizer or memory closeout; it leaves `.build-loop/closeout-pending/<run-id>.md` for the next SessionStart (`hooks/closeout.sh session-start`) to surface once.

2. **Contract.** Advisory + fail-open (always exit 0, never `decision: block`), self-gated on `.build-loop/` presence + this-session match (`current_session_id`, heartbeat-freshness fallback when the host passes no session id), minimal-PATH safe, idempotent with Phase D — the marker is the inline-path sentinel and `runs[]` membership is the Phase-D sentinel, so neither double-records the other. Tests: `scripts/test_stop_closeout.py` + `hooks/test_closeout.sh`.

3. **Codex wiring.** Both hosts ship in-repo: Claude via `hooks/hooks.json`, Codex via the tracked `.codex/hooks.json` — `Stop` and `SessionStart` entries call the same shim (`root="$(git rev-parse --show-toplevel)"; bash "$root/hooks/closeout.sh" stop`).

## Post-Build

After every build, if something surprising happened, append one line to `.build-loop/feedback.md`:

```
YYYY-MM-DD | what happened | what to do differently
```

These entries are loaded during Phase 1 (Assess) of future builds to prevent repeating mistakes.
