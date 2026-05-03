# Known Issues

## Skill-runtime collision: `Skill(skill="build-loop:build-loop")` returns slash-command template

**Symptom.** Calling `Skill(skill="build-loop:build-loop", args="...")` from Claude Code's Skill tool returns the unrendered/rendered slash-command body (`commands/build-loop.md`) as a user message instead of loading and executing the skill body at `skills/build-loop/SKILL.md`. The runtime emits `Launching skill: build-loop:build-loop`, then sends the slash-command template through as if a slash command had been invoked.

**Reproduction.** Any session that does:

```python
Skill(skill="build-loop:build-loop", args="any goal")
```

The first observed fallout in the wild was 2026-05-01, FlowDoro session, where the workaround was dispatching the `build-loop:build-orchestrator` agent directly. Same collision affects `claude-code-debugger:debug-loop`.

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

**Discovered:** 2026-05-01 by FlowDoro session investigation; root-cause analysis at `~/dev/git-folder/FlowDoro/.bookmark/` (2026-05-01 SNAP entries).

**Resolved:** 2026-05-01 — applied Option 2 (rename slash-command to `commands/run.md`). User surface is now `/build-loop:run [goal]`. Skill name unchanged at `skills/build-loop/SKILL.md`, so all `Skill("build-loop:build-loop")` callers (build-orchestrator agent, downstream plugins) continue to resolve correctly. README + CLAUDE.md updated.

**Sibling colliders — latent risk accepted, 2026-05-01.** Three sibling command/skill pairs share the same namesake-collision shape but were intentionally NOT renamed. UX cost of a suffix was deemed not worth the unverified risk; none have been observed misbehaving in practice. If `Skill("build-loop:<name>")` ever returns the slash-command template for one of these, apply the same Option 2 rename pattern used for `build-loop:build-loop`.

- `commands/optimize.md` ↔ `skills/optimize/SKILL.md` → `build-loop:optimize`
- `commands/research.md` ↔ `skills/research/SKILL.md` → `build-loop:research`
- `commands/plan-verify.md` ↔ `skills/plan-verify/SKILL.md` → `build-loop:plan-verify`

`commands/promote-experiment.md` has no namesake skill and was not touched. `skills/self-improve/SKILL.md` has no namesake command and was not touched.

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
- Inline-implementer fallback path (the load-bearing alternative to item 1) — drained 3 atomize-ai queue entries with 14 file modifications, all verifications green.
- `plugin-dev:plugin-validator` static review of all build-loop agents including the new implementer.

**Workaround for item 1**: write a queue entry to `.build-loop/ux-queue/<id>.md` and let the orchestrator's inline-implementer fallback handle it. Same protocol, same quality bar, no parallelism.
