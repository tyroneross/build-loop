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
