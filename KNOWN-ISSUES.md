# Known Issues

## Coordination recency decay + size-scaled lead/ownership auto-reclaim (NEW 2026-06-22, v0.13.0)

Additive feature mirroring the canonical agent-rally-point (Rust) implementation; build-loop's rally fallback now applies the SAME policy. A single shared policy module `scripts/rally_point/decay.py` (Python mirror of `crates/rally-cli/src/decay.rs`) defines the decay weight, archive-floor predicate, and size→timeout map; the constant 48h half-life lives in exactly one place per language. Cross-repo parity is guaranteed by a shared golden-vector fixture (`scripts/rally_point/decay_vectors.json`, byte-identical to `agent-rally-point/crates/rally-cli/tests/fixtures/decay_vectors.json`) that BOTH test suites assert against.

**Recency decay (Feature A):** every coordination change gets a weight `0.5 ** (age_hours / half_life)` (default half-life 48h). `coordination_status.py` orders recent changes fresh-first by weight and excludes any change below the archive floor (default `0.05`, ≈14d); `--include-archived` re-includes them and folds back physically-rotated `changes.jsonl.<date>` logs via the new `changes.read_archived_changes()`. Decay applies only to the historical change stream — never the live inbox or active state. Fails OPEN on a malformed `ts`.

**Size-scaled auto-reclaim (Feature B):** `leadership.claim_lead` now scales the lease window by claimed-work size — small (single-file / effort XS·S) = `reclaim_small_minutes` (default 30m), large (multi-file / coarse / effort M·L·XL) = `reclaim_large_minutes` (default 2h). Size-scaling is opt-in (pass `work_size`/`effort`/`owns`); with no size signal the lease window stays the historical `renew_every_minutes` cadence — no behavior change for existing lead claims. An auto-reclaim posts a durable `lead-reclaim` record (new `KNOWN_KINDS` entry) naming who reclaimed, the prior owner, and `reclaim_reason: stale-by-timeout`. Preserves the `rally/lead.lock` fcntl lock and is FAIL-CLOSED: a present incumbent lease with an unparseable `lease_until` is never reclaimed (new `_reclaimable` helper, distinct from the permissive `_lease_expired`).

Tunables under `coordinationPolicy` in `.build-loop/config.json` (loader `scripts/rally_point/coordination_policy.py`, following the `deployment_policy.load_policy` pattern; out-of-range values are ignored, never a hard failure). Docs: `references/coordination-rules.md` §"Recency decay & size-scaled lead/ownership auto-reclaim". Verified: `uv run pytest scripts/rally_point/ scripts/test_coordination_status.py` (258 passed) + the Rust parity test. Self-mod gate (`self_mod_verify.py`) passed before commit.

## Web deploy verification (Vercel) added (NEW 2026-05-16, v0.12.0)

Additive feature, no behavior change to existing flows. `scripts/verify_deploy.py` (+ `scripts/test_verify_deploy.py`) is a sibling of `runtime_smoke.py`: a Phase 4 Review-B gate that fires after a deploy actually ran and the consumer project is Vercel-linked (`.vercel/project.json` or `vercel.json`). It resolves the latest production deployment, polls `vercel inspect` to a terminal state, then probes the prod root + each `--changed-route`. Classification: `Ready` + root `200` + every changed route in `{200, 3xx, 401, 403}` → `pass`; `Error`/`Canceled`/build-failure or any changed route `5xx` → `fail` (routes to Iterate on `findings`); missing/unauthed CLI or any transient infra → `skipped` (never hard-fails the build). Key encoded heuristic: an auth-gated `401`/`403` on a protected route is **healthy** (function deployed and running), not a failure. MCP is documented only as an optional preferred-tier upgrade (`mcp.vercel.com`, user-added to `.mcp.json`); no MCP server is bundled. Wired into `capability-routing.md`, `fallbacks.md#web-deploy-verify`, `phase-4-review.md`, and `agents/build-orchestrator.md`.

Version sync side effect: `.codex-plugin/plugin.json` and `.claude-plugin/marketplace.json` (metadata + plugin entry) were stale at `0.10.0` against `.claude-plugin/plugin.json` `0.11.1` on `main` (`scripts/test_plugin_manifest.py` was failing on `main` for this reason). The 0.12.0 bump realigns all four version fields; the manifest test now passes.

## Dependency cooldown: per-PM native config keys + npm has no native exclude (REVISED 2026-05-16, v0.11.1)

The supply-chain dependency cooldown (`scripts/inject_dependency_cooldown.py`) writes a different native key per package manager (verified empirically on npm 11.14.1, 2026-05-16; source: mcollina gist + npm `config ls -l` + Socket.dev):

| PM | key | unit | file | exclude/allowlist |
|---|---|---|---|---|
| **npm ≥ 11.10** | `min-release-age` | **DAYS** (`min-release-age=7`) | `.npmrc` | **NONE** — npm has no native exclude (open issue [npm/cli#8994](https://github.com/npm/cli/issues/8994)) |
| **pnpm ≥ 11** | `minimumReleaseAge` | **MINUTES** (`10080`) | `pnpm-workspace.yaml` | `minimumReleaseAgeExclude:` (YAML list) |
| **pnpm 10.x** | `minimum-release-age` | **MINUTES** | `.npmrc` | (exclude carried in workspace yaml) |
| **yarn ≥ 4.10** | `npmMinimalAgeGate` | **MINUTES** (numeric — the `7d` string form is bugged, [yarnpkg/berry#6991](https://github.com/yarnpkg/berry/issues/6991)) | `.yarnrc.yml` | `npmPreapprovedPackages:` |

**The v0.11.0 bug (fixed in 0.11.1):** the injector wrote npm's `.npmrc` with pnpm's camelCase `minimumReleaseAge`. npm 11.14.1 rejects it (`npm warn Unknown project config "minimumReleaseAge"`) and installs ungated packages anyway, while `--check` still reported `enforced:true` (a false positive — it only verified a file was written, never that npm honored the key). On npm that false `enforced:true` also made the PreToolUse hook stand down, leaving **no gate at all**. `--check` now runs `npm config get min-release-age` and reports `enforced:true` **only** when npm recognizes the key (no "Unknown project config" stderr AND the value matches); a written-but-unrecognized key reports `enforced:false` with a reason.

**npm has no native exclude mechanism.** Native `min-release-age` covers transitive dependencies (the gap the native layer closes) but cannot exempt user-authored packages. So on npm the allowlist is enforced by the **PreToolUse backstop hook, which stays engaged even when native config is active** (`allowlist_mechanism: "hook"` in the injector envelope). Hook behavior on npm with native config: if every explicit package in the install command is allowlisted → rewrite to append `--min-release-age=0` (cooldown bypassed for that command only); otherwise silent no-op (native `min-release-age` already gates the third-party packages). The hook **never** adds `--before` when npm native config is active — npm hard-errors when both are present (`--before cannot be provided when using --min-release-age`). On pnpm/yarn the native config carries the exclude list (`allowlist_mechanism: "native"`) so the hook stands down entirely once enforced.

On genuinely old npm (< 11.10.0) the injector reports `status: fallback-hook` and does **not** write an inert key; the hook's `--before=<7d ago>` date-pin is the active (coarser) gate. The hook cannot rewrite lockfile-driven `npm ci` or pnpm — those are **denied** with an actionable message. pip/cargo have no native cooldown primitive and are **not covered in v1** (`[FOLLOW-UP] pip/cargo cooldown`).

Operator note: `npm view <pkg> --before <date>` **ignores** `--before` for the metadata read (it only constrains the install resolver). To inspect what a cooldown would resolve, use `npm install <pkg> --dry-run` with the `.npmrc` in place, not `npm view`.

Resolved (2026-05-16): `scripts/hooks/test_hooks.sh` Cases 1 & 3 (which test `pre_bash_autonomy.sh`, unrelated to the cooldown feature) previously failed on `main` because they sent `cwd:/tmp` and expected `permissionDecision=allow`. That expectation predated the autonomy hook's scope-guard hardening (the hook intentionally returns silent `{}` for any cwd lacking a `.build-loop/` marker — deliberate false-positive prevention, mirroring the cooldown hook). The hook was correct; the tests were stale. Cases 1 & 3 now run the benign command in a build-loop-marked temp cwd and still assert `allow` (coverage preserved), and a new Case 3b asserts the scope-guard contract directly (benign command with `cwd:/tmp`, no marker → silent `{}`). All `test_hooks.sh` cases pass; test+docs only, no behavior change.

## M4 session_registry.py doesn't fire — RESOLVED / SUPERSEDED by Rally Point presence (2026-05-18)

**Original symptom (2026-05-12).** During a private app session on 2026-05-11, two concurrent writers (main Claude session + background build-loop orchestrator) collided on the same worktree. The M4 `scripts/session_registry.py` was supposed to register presence files at `~/.build-loop/sessions/<run_id>.json` and detect this exact collision tier, but the directory was never created and no presence file was ever written — the mechanism never fired.

**Resolved (2026-05-18).** The dead M4 collision mechanism was retired and `scripts/session_registry.py` + `scripts/test_session_registry.py` were deleted. **Rally Point presence is now the single concurrent-presence source of truth**: `scripts/rally_point/presence.py` (`write_presence` / `read_active_presence` / `reap_stale` + per-session read cursor) writing one file per live session at `<resolved-channel>/sessions/<session-id>.json`, where `<resolved-channel>` comes from `scripts/rally_point/discovery_bridge.resolve(...)` and defaults to `~/.agent-rally-point/apps/<repo-id-or-slug>/`. The slug remains worktree- and clone-independent (D1), so the main checkout and every `git worktree` of the same repo share one channel. It is a checkpoint-poll awareness layer (D3, no daemon); peer file-overlap surfaces as a `soft-claim` **WARNING, never a block** (D4). The orchestrator wires it at the Phase 1 preamble and each phase-start per `references/rally-point-protocol.md` and `references/multi-session-coordination.md`.

The ambiguity that hid the original collision — two notional presence mechanisms, one of them dead — no longer exists: there is exactly one, and `~/.build-loop/sessions/<run_id>.json` is no longer written or read by any code path.

**Resume protocol unaffected.** The `--resume` crash-recovery path (`scripts/resume_resolver.py`, heartbeat-staleness on `state.json.execution`) shared **zero** code with the deleted collision mechanism (verified: no import, no reference in the resume tests). It was a separate concern reusing an overloaded "M4" label; that label was disambiguated to "crash-resume staleness signal" in `references/resume-protocol.md`. Resume behavior is byte-for-byte unchanged.

**Regression guard.** `tests/test_no_session_registry.py` fails loudly if the dead parallel mechanism (module, test, import, or a live CLI invocation in a tracked file) is ever re-introduced.

**Historical workaround (no longer required as the sole mitigation).** `isolation: "worktree"` on Agent dispatches remains good practice for HEAD/index isolation, but it is no longer compensating for an absent presence mechanism.

---

## Skill-runtime collision: `Skill(skill="build-loop:build-loop")` returns slash-command template

**Symptom.** Calling `Skill(skill="build-loop:build-loop", args="...")` from Claude Code's Skill tool returns the unrendered/rendered slash-command body (`commands/build-loop.md`) as a user message instead of loading and executing the skill body at `skills/build-loop/SKILL.md`. The runtime emits `Launching skill: build-loop:build-loop`, then sends the slash-command template through as if a slash command had been invoked.

**Reproduction.** Any session that does:

```python
Skill(skill="build-loop:build-loop", args="any goal")
```

The first observed fallout in the wild was a 2026-05-01 private app session, where the workaround was dispatching the `build-loop:build-orchestrator` agent directly. Same collision affects `claude-code-debugger:debug-loop`.

**Root cause (suspected, not fully verified).** Slash commands and skills sharing the same qualified name (`<plugin>:<name>`) — the slash command is at `commands/build-loop.md` (filename-derived name), the skill is at `skills/build-loop/SKILL.md` with `name: build-loop` in frontmatter. The Skill tool's resolver appears to pick the slash-command file. Sibling skills like `build-loop:research` share the same shape (matching command + skill names) but were not directly verified to be working — they may have the same latent bug.

**Why it isn't a 5-minute fix.**
- Renaming the slash command (e.g. `commands/build-loop.md` → `commands/run.md`) breaks the `/build-loop:build-loop` user surface that the README and tutorials reference.
- Renaming the skill (e.g. `skills/build-loop/SKILL.md` → `skills/orchestrator/SKILL.md`, name: orchestrator) means every `Skill(skill="build-loop:build-loop")` call across the world's plugins/agents needs to update.
- Merging command body + skill body into a single file would duplicate content and make `Skill(skill=...)` calls return unparsed Handlebars.

**Recommended fix path.** Pick one:

1. **Drop the slash-command, keep the skill.** Users invoke via `/build-loop` (no subcommand) or via Skill tool. Simplest, but loses the slash-command argument-hint tooltip in some IDEs.
2. **Rename slash-command file to `commands/run.md`.** Users type `/build-loop:run "goal"`. README + AGENTS.md updated. Skill name unchanged.
3. **Rename skill directory to `skills/orchestrator/`** with `name: orchestrator`. Users invoke via `Skill(skill="build-loop:orchestrator")`. README + AGENTS.md updated. Slash-command unchanged.

Option 2 is least disruptive to skill callers (which include the build-orchestrator agent and downstream plugin authors).

**Workaround until fixed.** Dispatch the `build-loop:build-orchestrator` agent directly with the same prompt that would have gone into the skill:

```
Agent(
  subagent_type="build-loop:build-orchestrator",
  prompt="<full self-contained brief, including model-tiering, parallelism cap, etc>"
)
```

This bypasses the resolver entirely and produces the same outcome.

**Discovered:** 2026-05-01 by private app session investigation; root-cause analysis at `<private-app>/.bookmark/` (2026-05-01 SNAP entries).

**Resolved:** 2026-05-01 — applied Option 2 (rename slash-command to `commands/run.md`). User surface is now `/build-loop:run [goal]`. Skill name unchanged at `skills/build-loop/SKILL.md`, so all `Skill("build-loop:build-loop")` callers (build-orchestrator agent, downstream plugins) continue to resolve correctly. README + CLAUDE.md updated.

**Sibling colliders — RESOLVED 2026-06-09 (Option 2 rename).** Three sibling command/skill pairs shared the same namesake-collision shape. Rather than carry the latent risk further, the proven Option-2 rename (slash-command file renamed, skill name unchanged) was applied to all three so `/build-loop:<x>` can never shadow `Skill("build-loop:<x>")`:

- `commands/optimize.md` → `commands/optimize-run.md` (`/build-loop:optimize-run`); `skills/optimize/SKILL.md` unchanged → `Skill("build-loop:optimize")` resolves to the skill. *(Historical: the `optimize-run` command was retired 2026-07-02 in the one-command move — optimize is now reached via `/build-loop:run` + "optimize" language.)*
- `commands/research.md` → `commands/research-run.md` (`/build-loop:research-run`); `skills/research/SKILL.md` unchanged → `Skill("build-loop:research")` resolves to the skill.
- `commands/plan-verify.md` → `commands/verify-plan.md` (`/build-loop:verify-plan`); `skills/plan-verify/SKILL.md` unchanged → `Skill("build-loop:plan-verify")` resolves to the skill.

`/build-loop:run` continues to auto-route to all three modes — the renamed commands are advanced overrides, so the slash-name change has minimal UX cost. Skill callers (orchestrator, README, downstream plugins) reference `Skill("build-loop:<name>")` and keep resolving unchanged. `commands/promote-experiment.md` has no namesake skill and was not touched. `skills/self-improve/SKILL.md` has no namesake command and was not touched.

**Final three siblings — RESOLVED 2026-06-09 (WP-D, Option 2 rename).** A second set of three command/skill pairs carried the same collision shape (the first batch above renamed `optimize`/`research`/`plan-verify`; these three were the remaining latent pairs). All renamed via Option-2 so the namespace can never shadow:

- `commands/agent-rally-point.md` → `commands/rally-point.md` (`/build-loop:rally-point`); `skills/agent-rally-point/SKILL.md` unchanged → `Skill("build-loop:agent-rally-point")` resolves to the skill.
- `commands/handoff.md` → `commands/compose-handoff.md` (`/build-loop:compose-handoff`); `skills/handoff/SKILL.md` unchanged → `Skill("build-loop:handoff")` resolves to the skill. Self-referential slash mentions in the command + skill bodies were updated to the new name.
- `commands/knowledge-review.md` → `commands/review-knowledge.md` (`/build-loop:review-knowledge`); `skills/knowledge-review/SKILL.md` unchanged → `Skill("build-loop:knowledge-review")` resolves to the skill.
  - **Follow-up (2026-07, pool-consolidation Inc 3):** `skills/knowledge-review/` was folded into `knowledge` as a read-only *review mode* (`skills/knowledge/references/review-mode.md`; review-intent triggers appended to `knowledge`'s frontmatter description). `Skill("build-loop:knowledge-review")` no longer resolves — reach the review surface via `knowledge` (review mode) or `/knowledge:review`.

With this batch, build-loop has **zero** namesake collisions and `scripts/test_skill_resolution.py` enforces it (`ACCEPTED_SIBLINGS` is now empty; `collision_scan.py --strict` exits 0). Any new sibling pair fails CI. The guard suite (`collision_scan.py` + `test_skill_resolution.py` + `test_plugin_manifest.py`) runs in CI via the existing `pytest scripts/` invocation in `.github/workflows/pytest.yml`.

---

## Plugin merge — 2026-05-02

**What happened.** `claude-code-debugger` (cccd, v1.8.2) was merged into build-loop (0.5.0 → 0.6.0). Build-loop is now the single home for the debugger; cccd remains as a standalone plugin and repository for backward-compat callers but will be deprecated in a follow-up.

**What moved into build-loop:**

| Source (cccd) | Destination (build-loop) |
|---|---|
| `agents/{api,database,frontend,performance}-assessor.md`, `assessment-orchestrator.md`, `fix-critique.md`, `root-cause-investigator.md` | `agents/` (7 new agents) |
| `commands/{assess,debugger,debugger-detail,debugger-scan,debugger-status}.md` | `commands/` (5 new commands, kept names) |
| `commands/debugger-agent.md` | renamed to `commands/debug.md` (user-facing slash is `/build-loop:debug` to avoid namesake collision with `skills/debug-loop/`) |
| `commands/{claude-code-debugger,feedback,update}.md` | NOT copied (build-loop has its own front door at `commands/run.md`) |
| `skills/{debug-loop,debugging-memory,logging-tracer}/` | `skills/` (3 new skills) |
| `src/`, `cli/`, `dist/`, `package.json`, `package-lock.json`, `tsconfig.json`, `.mcp.json` | top-level (TypeScript MCP server + CLI) |
| `hooks/hooks.json` Stop entry | merged into `hooks/hooks.json` alongside existing PostToolUse |

**Namespace rewrite.** All `claude-code-debugger:*` qualified-skill references inside build-loop were rewritten to `build-loop:*`. The bridge skills (`debugger-bridge`, `logging-tracer-bridge`) kept their orchestration logic but were retitled as internal coordinators (they no longer indirect to an external plugin). **v0.7.0 (2026-05-02) dissolved the bridges**: when-to-fire policy moved into `agents/build-orchestrator.md`; verdict / direct-apply gate moved into `skills/debugging-memory/SKILL.md`; parallel-assess escalation moved into `skills/debug-loop/SKILL.md`; ephemeral-by-default + Mechanism A/B + placement rules moved into `skills/logging-tracer/SKILL.md`. **v0.7.1 (2026-05-02) restored the bridges as thin extended-capability escalation hops** — target skills call them when bundled capability isn't enough; bridges pre-flight `availablePlugins.claudeCodeDebugger` and delegate to the standalone supporting plugin (cross-project memory, additional assessors, coordination), no-op gracefully if standalone isn't installed. Orchestrator continues to call target skills directly (when-to-fire stays there); target skills own procedural detail; bridges add an optional outbound delegation lever.

**Persistent state preserved.** `.claude-code-debugger/` filesystem paths (incident memory, config, log readers) were intentionally NOT renamed. Existing user incident memory created when cccd was a standalone plugin keeps working without migration.

**Backward-compat.** `Skill("claude-code-debugger:*")` callers in other plugins continue to resolve as long as the standalone cccd plugin is installed. New build-loop builds prefer the internal `Skill("build-loop:*")` names. cccd will be deprecated separately once the ecosystem migrates.

**Auto-invocation.** Build-orchestrator runs the always-on memory-first gate at Review-B (calling `Skill("build-loop:debugging-memory")`), invokes `Skill("build-loop:logging-tracer")` reactively on `evidence_gap`, and escalates to `claude-code-debugger:assess` (parallel domain assessors) at 2 same-root-cause failures and `Skill("build-loop:debug-loop")` (causal-tree) at 3 same-criterion failures. The full cascade lives in `agents/build-orchestrator.md` §Phase 5.

**Why merge instead of cherry-pick bridge.** The debugger is invoked from inside the build loop on every Review-B / Iterate failure — multiple times per build. Loose coupling didn't buy anything except an indirection layer. Other companions (NavGator, IBR) remain external because they're invoked at most a few times per build and have their own use cases outside build-loop.

---

## Bundled MCP server name collision — resolved 2026-05-01 (v0.8.2)

**Symptom.** Both build-loop's bundled debugger and the standalone `claude-code-debugger` plugin previously registered an MCP server named `debugger`. When both plugins were installed, only one server "won" at runtime — the other was silently shadowed. Tools surfaced under `mcp__plugin_<name>__*` qualified names sometimes hit the wrong implementation depending on plugin load order.

**Fix (v0.8.2).** Renamed the bundled server in `.mcp.json` from `debugger` to `build-loop-debugger`. The standalone `claude-code-debugger` plugin's server remains `debugger` (unchanged). Now:

- Bundled (always present in build-loop): `mcp__plugin_build-loop-debugger__{search,store,outcome,read_logs,list}`
- Standalone (only when `claude-code-debugger` is also installed): `mcp__plugin_claude_code_debugger__*`

Both can be installed; neither shadows the other. The `debugger-bridge` skill explicitly delegates to the standalone `mcp__plugin_claude_code_debugger__*` surface for cross-project memory and additional assessor coverage when escalating beyond bundled capability.

**Impact on callers.** Internal build-loop callers (orchestrator Assess priming, Review-B `read_logs`, Review-F `store`/`outcome`, debugging-memory skill) were updated to the new qualified names. External callers using `mcp__plugin_claude_code_debugger__*` continue to work as long as the standalone plugin is installed — they target the standalone deliberately.

**Verification.** `python3 scripts/test_mcp_registration.py` now passes all 5 checks without skipping `ServerNamingHygiene` (previously skipped with a hint).

## Phase 5 fan-out is mode-dependent — surfaced 2026-05-03

**Symptom.** When the `build-orchestrator` agent is dispatched as a subagent (via `Agent(subagent_type="build-loop:build-orchestrator", ...)` from any parent session), it cannot spawn implementer subagents during Phase 5 — the no-sub-sub-agents rule from `~/.claude/CLAUDE.md` §Sub-Agents applies. First observed during the live test of v0.9.0's parallel UX-fix fan-out: orchestrator halted before any implementer dispatch, reporting "no `Task`/`Agent` tool in orchestrator tool surface."

**Root cause.** The orchestrator's frontmatter declares `tools: [..., "Agent", ...]`, but the runtime strips `Agent` from any agent that itself runs as a subagent (preventing the cascade documented in user CLAUDE.md). This is a hard constraint, not a bug.

**Resolved:** 2026-05-03 — orchestrator now degrades gracefully to **inline-implementer mode** when `Agent` is unavailable. Same protocol (scope to `files_touched`, refuse `architecture_impact: true`, verify locally before declaring fixed) applied serially by the orchestrator itself instead of dispatched in parallel to subagents. Quality bar unchanged; parallelism lost. The Review-F report now surfaces a `⚠️ Phase 5 ran in subagent mode — no parallel fan-out` note when degradation kicks in.

**For full parallelism**, invoke `/build-loop:run` directly in a top-level session (not via Agent dispatch from a parent). Top-level mode dispatches up to 4 implementer subagents in parallel per partition group. Subagent mode is the safe fallback, not the intended primary path.

## Items requiring fresh session to test — surfaced 2026-05-03

The following build-loop 0.9.0+ behaviors cannot be exercised inside a session that already loaded the plugin before the behavior was added:

1. **`Agent(subagent_type="build-loop:implementer", ...)` direct dispatch**: Claude Code's agent registry loads at session start; agents added mid-session are not hot-reloaded. The implementer agent (shipped in `f3eac63`) is structurally validated by `plugin-dev:plugin-validator` and statically reachable in the symlinked cache, but cannot be live-dispatched in the session that added it. Test path: start a fresh session, then `Agent(subagent_type="build-loop:implementer", prompt=...)`.

2. **Top-level Phase 5 parallel fan-out**: requires the orchestrator to run at top-level (user's session) with the `Agent` tool available. Subagent context strips `Agent` (no-sub-sub-agents enforcement) and falls through to inline-implementer mode. Test path: start a fresh session, run `/build-loop:run "drain UX queue"` directly (not via `Agent(subagent_type="build-loop:build-orchestrator")`).

3. **Mockup-gallery Phase 2 hook**: only triggers when Phase 2 detects a "new page/screen or ≥40% redesign." Requires a real plan that meets the trigger. Synthetic exercise possible but low-value; first real major-UI build will exercise it.

4. **Orchestrator runtime routing of new statuses** (`plan_malformed`, `needs_dependency`, `failed` escalation, `concurrent_modification_detected`): documented in build-orchestrator.md and SKILL.md as of `41b4203`, but only invoked when an implementer dispatch returns one of these statuses. Synthetic exercise would require dispatching the implementer with deliberately broken plans, which itself blocks on item 1.

**What IS testable mid-session and was verified**:
- Script-level: `version_advisor.py` (Gate 6), `ibr_quickpass.py` (Gate 8), `ux_triage.py` (Gate 7) — all edge cases including parallel pool, comment-strip, stable IDs, multi-line tag matching.
- Inline-implementer fallback path (the load-bearing alternative to item 1) — drained 3 example-app queue entries with 14 file modifications, all verifications green.
- `plugin-dev:plugin-validator` static review of all build-loop agents including the new implementer.

**Workaround for item 1**: write a queue entry to `.build-loop/ux-queue/<id>.md` and let the orchestrator's inline-implementer fallback handle it. Same protocol, same quality bar, no parallelism.

---

## Marketplace `autoUpdate: true` does not actually re-install drifted plugins — 2026-05-03

**Symptom.** A user marketplace declared in `~/.claude/settings.json.extraKnownMarketplaces` with `autoUpdate: true` keeps the marketplace's local checkout (`~/.claude/plugins/marketplaces/<name>/`) refreshed against the catalog source, but Claude Code does not re-install the per-plugin files into each entry's `installPath` (`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`). Result: the marketplace catalog declares e.g. `navgator: 0.8.2`, but `installed_plugins.json` still points its installPath at `…/navgator/0.8.1/`, and `Skill("navgator:…")` calls load the old code.

**Reproduction.** Confirmed 2026-05-03 with `rosslabs-ai-toolkit`. Catalog declared `navgator 0.8.2` + `claude-code-debugger 1.8.2`; registry pointed at `0.8.1` and `1.8.1` respectively. Same shape applies to any user marketplace with `autoUpdate: true` plus catalog `source: { source: "github", repo: "<owner>/<repo>" }` entries.

**Root cause (suspected, not authoritatively confirmed against Claude Code internals).** The `autoUpdate` flag appears wired only to the marketplace-checkout refresh path, not to a per-entry diff-and-reinstall pass. Filing a Claude Code bug is the right long-term fix; in the meantime see workaround.

**Workaround (deployed 2026-05-03).** SessionStart hook script `~/.claude/scripts/hooks/marketplace-autoupdate.py` reads the registry, compares each entry's effective version (on-disk `plugin.json` first, falling back to the registry's `version` field) against the catalog's declared version, and on drift: clones the catalog source into a fresh `cache/<mkt>/<plugin>/<catalog-version>/` directory, verifies the cloned `plugin.json` `version` matches the catalog (rejects on mismatch — likely upstream packaging error), then atomically rewrites `installed_plugins.json` to point at the new dir. Old version dirs are retained for rollback. Lock file at `~/.claude/plugins/.marketplace-autoupdate.lock` prevents concurrent runs. Kill switch: `touch ~/.claude/settings.json.disable-marketplace-autoupdate`. Log: `~/.claude/logs/marketplace-autoupdate.log`. 30-second budget; over-budget plugins defer to the next session. Always exits 0 — never blocks session start.

**Caveats and known holes.**
- The official `claude-plugins-official` marketplace uses a different catalog shape (`source: "./relative-path"` strings, no per-plugin `version` fields); the script logs them as unsupported and skips. Those plugins are bundled with the marketplace checkout itself and update implicitly when `autoUpdate: true` refreshes the marketplace tree.
- `@local` marketplace entries (e.g. local-dev symlinks under `installPath`) are skipped — local-dev iteration is not a drift target.
- Plugins whose GitHub repo lacks `.claude-plugin/plugin.json` at the repo root (verified 2026-05-03 for `agent-builder`, `agent-astronomer`, `stratagem`) cannot be auto-updated by this script — the verify-before-swap step rejects them. Those repos need root-level `.claude-plugin/plugin.json` before the hook can update them.

**Version-controlled 2026-06-09 (WP3c).** The canonical implementation now lives in this repo at `scripts/marketplace_autoupdate.py` (with `scripts/test_marketplace_autoupdate.py`). The host install at `~/.claude/scripts/hooks/marketplace-autoupdate.py` is now a thin pure-exec shim that execs the repo copy (per the hooks-hygiene lesson: a loose copy at a global path desyncs silently from its source). The shim resolves the canonical file via `$BUILD_LOOP_MARKETPLACE_AUTOUPDATE` → `~/dev/git-folder/build-loop/scripts/marketplace_autoupdate.py` → newest plugin-cache version dir, and exits 0 if none resolve. Edit the repo copy; the shim picks it up with no re-copy.

**Resolution criteria.** Remove this entry once Claude Code's `autoUpdate: true` actually re-installs catalog drift into `installPath`. Then delete the repo script + test, replace the shim with `rm`, and remove the matching SessionStart entry from `~/.claude/settings.json`.

---

## Inline-vs-agent activation gap — verification seats/telemetry skipped on inline runs — 2026-07-02

**Symptom.** An inline `/build-loop:build-loop` run (skill-as-methodology in the host, no `Agent(subagent_type=…)` dispatch) reached neither the Fable-pinned verification seats nor the rich Review-G run record. Effect: Fable-seat routing worked on the agent path but ~0 on the inline path; LLM judges rarely fired; `state.json.runs[]` under-populated (starving `recurring-pattern-detector` + Phase 6 `self-improve`); the human became the de-facto orchestrator. Independently surfaced by Codex ("parent-must-dispatch unresolved", Rally seq 3934).

**Assess correction.** The enforcement mostly already existed: `stop_closeout.py` records inline runs, `judgment_gate.py` stakes-gates the Frontier auditor/advisor dispatch (FAIL top-level / WARN nested), `append_run.build_record` already sets `host` + typed `manualInterventions`. The residual gaps were narrow — fixed by P0 (`docs/plans/2026-07-02-P0-inline-vs-agent-activation-closure-plan.md`):
- **C1** `judgment_gate --require-seats` (opt-in): attests `plan-critic`/`scope-auditor` (synthesisDensity>5) + `security-reviewer` (riskSurfaceChange) via the agent-ledger; reports `missing_seats[]`.
- **C2** `stop_closeout` writes `followup/judgment-owed-<run-id>.md` on a stakes-gated WARN/FAIL → Phase 5 Iterate drains it, closing the "parent owes it" sidestep loophole. The inline gate call keeps `require_seats=False` (the seat ledger channel is not yet populated — see residual); the followup is driven by the auditor/advisor floor and is **deleted once the debt clears** (a later passing Stop), so Phase 5 never drains phantom debt. Independent review (Fable + Codex GPT-5.5) hardened C1/C2: wrong-tier ledger rows no longer count a seat present; a missing `run_id` attests nothing.
- **C3** `write_run_entry` now records `host` (parity with `append_run`) — fixes null-host orchestrator runs.

**Residual (why this stays listed).**
- C1 seat-attestation is **opt-in (`--require-seats`, default off)** until telemetry confirms every seat reliably writes an agent-ledger row for its run; promote to always-on (and wire into Review-G + stop_closeout by default) once confirmed.
- Legacy pre-schema `runs[]` records (null `host`, string `manualInterventions`) are left as historical; only new records are normalized.
- The CLAUDE.md parity-contract note lands at P0 merge.

**Resolution criteria.** Remove once `--require-seats` is promoted to default-on and the CLAUDE.md contract documents inline-vs-agent parity as enforced.
