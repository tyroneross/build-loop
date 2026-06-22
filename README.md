<!-- build-loop@tyroneross:canary:build-loop -->
<!-- canary-end -->
# build-loop

Build-loop is an agent workflow package for multi-step code changes. It gives
Claude Code, Codex, and other AGENTS.md-aware tools the same operating loop:
assess, plan, execute, review, iterate, then Learn.

The repo ships three agent surfaces:

- Claude Code plugin metadata, commands, hooks, and `agents/*.md`.
- Codex plugin metadata plus a slim public skill entrypoint.
- Host-neutral `AGENTS.md` instructions for other coding agents.

## Mac Install

Use the package installer when you want the shortest local setup path on macOS.

```bash
npm install -g @tyroneross/build-loop@0.36.0
build-loop-install --host all
```

For GitHub Packages, authenticate first and use the GitHub registry for the
`@tyroneross` scope:

```bash
npm config set @tyroneross:registry https://npm.pkg.github.com
npm login --scope=@tyroneross --registry=https://npm.pkg.github.com
npm install -g @tyroneross/build-loop@0.36.0
build-loop-install --host all
```

`build-loop-install` runs the existing package helpers from the installed npm
package:

- Syncs Claude cache from the package root.
- Syncs Codex cache from `plugin-artifacts/codex`, the slim Codex install
  artifact.
- Bootstraps the build-loop memory root with public templates.
- Leaves publishing, GitHub releases, and production deploys to explicit release
  commands.

Installer options:

| Option | Use |
|---|---|
| `--host claude` | Sync only the Claude Code cache. |
| `--host codex` | Sync only the Codex cache. |
| `--host all` | Sync both caches. This is the default. |
| `--project <slug>` | Ensure `projects/<slug>/raw/` exists in build-loop memory. Repeatable. |
| `--memory-dest <path>` | Override the memory root. |
| `--skip-memory` | Sync plugin caches only. |
| `--dry-run` | Show cache sync actions without writing. |
| `--json` | Emit one machine-readable result. |

Local development install:

```bash
git clone https://github.com/tyroneross/build-loop.git
cd build-loop
npm install
npm run build
python3 scripts/sync_plugin_cache.py --source . --host claude
npm run codex:sync-cache
python3 scripts/install_memory.py --ensure-project build-loop
```

## Agent Start Protocol

Start every build-loop repo session by checking Rally. The historical
`rally codex --human` form may be unavailable on newer Rally binaries; use the
current command surface when needed.

```bash
rally next --tool codex --json
rally room --tool codex --json
```

Before editing, check every path you will write:

```bash
rally check before-write --tool codex --path README.md --strict --json
```

When a handoff is addressed to you, resolve it before editing unrelated files:

```bash
rally say resolve --tool codex --ref <event-id> --subject "consumed handoff" --json
```

For substantial work, use the build-loop skill:

```text
Assess -> Plan -> Execute -> Review -> Iterate -> Learn
```

Skip the loop only for single-file edits, config-only changes, or very small
fixes. Release, publish, production deploy, destructive delete, and major
user-impacting decisions are the only human-confirmation gates after a plan is
accepted.

## Agent Commands

Normal coding work:

```text
/build-loop:run add billing settings with tests
```

Debugging:

```text
/build-loop:debug tests pass locally but fail in CI
```

Direct advanced modes:

```text
/build-loop:optimize-run reduce API latency
/build-loop:research-run compare queue providers
/build-loop:test --strict
```

Codex-specific delegation is opt-in. Build-loop planning language such as
"parallel-safe groups" does not by itself authorize Codex subagents. Spawn Codex
workers only when the user explicitly asks for parallel delegation or passes a
parallel flag.

## Agent Roles

This is a role index, not a list of autonomous commands. Build-loop routes work
through a lead orchestrator, invokes bounded subagents with scoped context, and
accepts output only after verification. Core authority follows
`references/agent-role-taxonomy.md`: **core means a pipeline step is contingent
on the verdict**, not that the agent is top-level or expensive. Model tier
follows role; it does not define authority.

Deterministic judge surfaces also exist outside `agents/`, including
`scripts/plan_verify.py`, `scripts/judgment_gate.py`, and release/package
verifiers. Agent roles below are the LLM-side surfaces.

### Lead / Workflow Agents

| Agent | Description | Model |
|---|---|---|
| `build-orchestrator` | Lead workflow owner for Assess -> Plan -> Execute -> Review -> Iterate -> Learn; owns dispatch, phase transitions, commits, and report. | `opus` |
| `assessment-orchestrator` | Multi-domain debugging coordinator for unclear symptoms across database, frontend, API, and performance lanes. | `opus` |
| `optimize-runner` | Optimization-loop coordinator for metric-driven experiments, measurement, and regression handling. | `sonnet` |

### Judgment / Review Agents

| Agent | Description | Model |
|---|---|---|
| `advisor` | Frontier planning author or re-planner when Phase 2 needs deeper synthesis. | `fable` |
| `plan-critic` | Plan critique for dependencies, scope drift, validation, ownership, alternatives, and MECE quality. | `fable` |
| `scope-auditor` | Plan-to-Execute boundary check and public-signature caller coverage. | `fable` |
| `independent-auditor` | Independent adversarial review for chunk and build-scope completion claims. | `fable` |
| `fix-critique` | Root-cause and regression pressure-test after a proposed fix. | `fable` |
| `fact-checker` | Claim, metric, and rendered-data provenance checks before completion. | `fable` |
| `security-reviewer` | Security review for auth, secrets, trust boundaries, injection, and adjacent risks. | `fable` |
| `overfitting-reviewer` | Optimization review for test gaming, Goodhart effects, and overfitting. | `fable` |
| `promotion-reviewer` | Review of proposed skill, agent, or enforcement promotions before activation. | `fable` |
| `synthesis-critic` | Advisory coherence review for synthesis-heavy outputs across multiple dimensions. | `sonnet` |
| `alignment-checker` | Advisory queue-item alignment check against current intent, goal, and non-goals. | `sonnet` |

### Worker / Specialist Agents

| Agent | Description | Model |
|---|---|---|
| `implementer` | Bounded coding worker for one Phase 5 fix plan or criterion-targeted implementation packet. | `sonnet` |
| `api-assessor` | API, route, auth, rate-limit, CORS, and request/response failure assessment. | `sonnet` |
| `database-assessor` | Query, migration, schema, connection, vector index, and data integrity failure assessment. | `sonnet` |
| `frontend-assessor` | React, rendering, hydration, state, component, and client performance assessment. | `sonnet` |
| `performance-assessor` | Latency, memory, CPU, timeout, and bottleneck assessment. | `sonnet` |
| `architecture-scout` | Read-only architecture baseline, impact, rules, iterate subgraph, and learn-sync tasks. | `sonnet` |
| `design-contract-specialist` | UI/data input-output contracts, design direction, and traceability artifacts. | `sonnet` |
| `ui-validator` | UI behavior, state, accessibility, layout, console, and rendering evidence validation. | `sonnet` |
| `root-cause-investigator` | Causal-tree investigation for persistent or ambiguous failures. | `inherit` |
| `mock-scanner` | Production-path scan for placeholder, fake, fixture, and mock data. | `haiku` |

### Learning Agents

| Agent | Description | Model |
|---|---|---|
| `recurring-pattern-detector` | Repeated run-pattern and Learn-candidate detection from run history and retro signals. | `haiku` |
| `retrospective-synthesizer` | Background post-build retrospective and enforce-candidate summary. | `sonnet` |
| `self-improvement-architect` | Experimental skill, agent, and workflow drafts from recurring lessons. | `sonnet` |
| `transcript-pattern-miner` | Transcript mining for repeated patterns and self-improvement candidates. | `haiku` |

## Phase Summary

| Phase | Agent obligation |
|---|---|
| Assess | Read live repo state, tooling, memory, Rally, current docs, and external docs when needed. Define the goal and pass/fail criteria. |
| Plan | Produce a dependency-ordered plan with MECE file ownership, validation gates, and approach tradeoffs. |
| Execute | Implement the accepted plan. Keep edits scoped to owned files. |
| Review | Run critic, validation, fact-check, simplify, auto-resolve, and report steps. |
| Iterate | Fix review failures until pass or a real blocker is reached. |
| Learn | Always emit the Learn outcome and capture durable lessons when warranted. |

## Release Checklist

For a plugin/package release, keep these version surfaces in lockstep:

- `package.json`
- `package-lock.json`
- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `.codex-plugin/plugin.json`
- `.agents/plugins/marketplace.json`
- `plugin-artifacts/codex/.codex-plugin/plugin.json`

Build and verify:

```bash
npm run build
python3 scripts/test_plugin_manifest.py
python3 scripts/test_agent_surface_policy.py
npm run codex:build-artifact
npm pack --dry-run --json
```

Release verification after tag/push:

```bash
python3 scripts/verify_release_surface.py --version v0.36.0 --branch main --remote origin --json
```

Publishing to GitHub Packages, npmjs, or GitHub Releases is a release action.
Run it only when explicitly requested by the human owner.

## Runtime Data

Consumer projects store run state under `.build-loop/`:

```text
.build-loop/
  goal.md
  intent.md
  config.json
  state.json
  feedback.md
  evals/
  issues/
  backlog/
```

Add `.build-loop/` to consumer project `.gitignore` unless a repo intentionally
tracks selected backlog or plan files.

Build-loop memory defaults to `~/.build-loop-memory` on a fresh machine, or an
existing `~/dev/git-folder/build-loop-memory` when present. Bootstrap or inspect
it with:

```bash
python3 scripts/install_memory.py
python3 scripts/install_memory.py --check
```

## Codex Surface

The Codex package exposes one public entrypoint skill through the slim artifact:

```text
plugin-artifacts/codex/
  .codex-plugin/plugin.json
  skills/build-loop/SKILL.md
```

The full `skills/` tree still ships for Claude Code and for internal references.
Codex should load helper instructions only when the public build-loop skill asks
for them.

Check installed cache sync:

```bash
python3 scripts/check_cache_sync.py --host codex --source plugin-artifacts/codex
python3 scripts/check_cache_sync.py --host claude --source .
```

Prune stale cache versions:

```bash
python3 scripts/prune_plugin_cache.py --source . --host all --apply
```

## License

Apache-2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE), and
[CONTRIBUTING.md](CONTRIBUTING.md).
