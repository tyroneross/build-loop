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

## Top-Level Agents

These files live under `agents/`. Descriptions and models come from the current
frontmatter and are the source of truth for Claude Code agent routing.

| Agent | Description | Model |
|---|---|---|
| `advisor` | Frontier planning advisor for hard decisions, approach tradeoffs, and scope framing. | `fable` |
| `alignment-checker` | Checks whether planned or completed work still matches the stated goal and intent. | `sonnet` |
| `api-assessor` | Assesses API, route, auth, rate-limit, CORS, and request/response failures. | `sonnet` |
| `architecture-scout` | Read-only architecture analyst for baseline, impact, rules, iterate subgraph, and learn-sync tasks. | `sonnet` |
| `assessment-orchestrator` | Coordinates multi-domain debugging assessment across database, frontend, API, and performance lanes. | `opus` |
| `build-orchestrator` | Drives the full build-loop workflow and dispatches specialists. | `opus` |
| `database-assessor` | Assesses query, migration, schema, connection, vector index, and data integrity failures. | `sonnet` |
| `design-contract-specialist` | Writes and checks UI input/output contracts before UI implementation. | `sonnet` |
| `fact-checker` | Traces claims, metrics, and rendered facts to their real data sources. | `fable` |
| `fix-critique` | Pressure-tests whether a proposed fix addresses root cause and avoids regressions. | `fable` |
| `frontend-assessor` | Assesses React, rendering, hydration, state, component, and client performance issues. | `sonnet` |
| `implementer` | Applies one bounded Phase 5 fix plan or criterion-targeted implementation packet. | `sonnet` |
| `independent-auditor` | Independent adversarial review for chunk and build-scope completion claims. | `fable` |
| `mock-scanner` | Scans production paths for placeholder, fake, fixture, and mock data. | `haiku` |
| `optimize-runner` | Runs metric-driven optimization loops with measurement and regression handling. | `sonnet` |
| `overfitting-reviewer` | Reviews optimization results for test gaming, Goodhart effects, and overfitting. | `fable` |
| `performance-assessor` | Assesses latency, memory, CPU, timeout, and bottleneck symptoms. | `sonnet` |
| `plan-critic` | Reviews plans for missing dependencies, scope drift, weak validation, and unclear ownership. | `fable` |
| `promotion-reviewer` | Reviews proposed skill, agent, or enforcement promotions before activation. | `fable` |
| `recurring-pattern-detector` | Detects repeated run patterns and Learn candidates from run history and retro signals. | `haiku` |
| `retrospective-synthesizer` | Writes the nine-section post-build retrospective and enforce-candidate summary. | `sonnet` |
| `root-cause-investigator` | Builds causal trees for persistent or ambiguous failures and identifies research boundaries. | `inherit` |
| `scope-auditor` | Checks Plan-to-Execute boundaries and public signature caller coverage. | `fable` |
| `security-reviewer` | Reviews auth, secrets, trust boundaries, injection, and other security-sensitive changes. | `fable` |
| `self-improvement-architect` | Drafts experimental skills, agents, and workflow improvements from recurring lessons. | `sonnet` |
| `synthesis-critic` | Reviews synthesis-heavy outputs for coherence across multiple dimensions. | `sonnet` |
| `transcript-pattern-miner` | Mines transcripts for repeated patterns and candidate self-improvement signals. | `haiku` |
| `ui-validator` | Validates changed UI behavior, states, accessibility, and rendering evidence. | `sonnet` |

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
